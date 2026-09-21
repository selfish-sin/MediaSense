import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'

const apiMocks = vi.hoisted(() => ({ queue: vi.fn() }))

// 可控 socket mock：注册/解绑 handler、手动触发事件、切换连接状态
const harness = vi.hoisted(() => {
  const listeners = {}
  return {
    listeners,
    socket: {
      connected: true,
      on(event, handler) { (listeners[event] = listeners[event] || []).push(handler) },
      off(event, handler) {
        listeners[event] = (listeners[event] || []).filter(h => h !== handler)
      },
      emit(event, data) { (listeners[event] || []).forEach(h => h(data)) },
    },
  }
})

vi.mock('../utils/api', () => ({ jobsApi: apiMocks }))

vi.mock('../utils/socket', () => ({
  default: harness.socket,
  onProgress: (cb) => {
    harness.socket.on('progress', cb)
    return () => harness.socket.off('progress', cb)
  },
  onLog: (cb) => {
    harness.socket.on('log', cb)
    return () => harness.socket.off('log', cb)
  },
  onSocketConnect: (cb) => {
    harness.socket.on('connect', cb)
    return () => harness.socket.off('connect', cb)
  },
  onSocketDisconnect: (cb) => {
    harness.socket.on('disconnect', cb)
    return () => harness.socket.off('disconnect', cb)
  },
}))

import { useJobQueue } from '../composables/useJobQueue'

function withSetup(composable) {
  let result
  const wrapper = mount(defineComponent({
    setup() {
      result = composable()
      return () => null
    },
  }))
  return { result, wrapper }
}

describe('useJobQueue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    Object.keys(harness.listeners).forEach(k => delete harness.listeners[k])
    harness.socket.connected = true
    apiMocks.queue.mockResolvedValue({ concurrency: 1, running: [], pending: [], total: 0 })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('挂载即拉取一次队列快照', () => {
    const { wrapper } = withSetup(useJobQueue)

    expect(apiMocks.queue).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('refreshQueue 写入快照（running + pending）', async () => {
    const snapshot = {
      concurrency: 1,
      running: [{ id: 'r1', status: 'downloading' }],
      pending: [{ id: 'p1', status: 'pending' }],
      total: 2,
    }
    apiMocks.queue.mockResolvedValue(snapshot)
    const { result, wrapper } = withSetup(useJobQueue)

    await result.refreshQueue()

    expect(result.queue.value).toEqual(snapshot)
    wrapper.unmount()
  })

  it('progress/log 高频事件 1s debounce 合并为一次刷新', async () => {
    const { wrapper } = withSetup(useJobQueue)
    apiMocks.queue.mockClear()

    harness.socket.emit('progress', { job_id: 'j1', pct: 1 })
    harness.socket.emit('progress', { job_id: 'j1', pct: 2 })
    harness.socket.emit('log', { job_id: 'j1', msg: 'x' })
    expect(apiMocks.queue).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1000)
    expect(apiMocks.queue).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('socket 断开时降级 10s 轮询，重连后停表并补拉一次', async () => {
    const { result, wrapper } = withSetup(useJobQueue)
    apiMocks.queue.mockClear()

    result.syncWithSocket(false)
    await vi.advanceTimersByTimeAsync(10000)
    expect(apiMocks.queue.mock.calls.length).toBeGreaterThan(0)

    result.syncWithSocket(true)
    const afterReconnect = apiMocks.queue.mock.calls.length
    expect(afterReconnect).toBeGreaterThan(0)
    await vi.advanceTimersByTimeAsync(30000)
    expect(apiMocks.queue.mock.calls.length).toBe(afterReconnect)
    wrapper.unmount()
  })

  it('组件卸载：socket 监听解绑、轮询与 debounce 清理', async () => {
    const { wrapper } = withSetup(useJobQueue)
    wrapper.unmount()
    apiMocks.queue.mockClear()

    harness.socket.emit('progress', { job_id: 'j1' })
    await vi.advanceTimersByTimeAsync(30000)

    expect(apiMocks.queue).not.toHaveBeenCalled()
  })
})
