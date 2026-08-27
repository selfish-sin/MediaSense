import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'

const apiMocks = vi.hoisted(() => ({ batchStatus: vi.fn() }))

// 可控的 socket mock：支持注册/解绑 handler、手动触发事件、切换连接状态
const harness = vi.hoisted(() => {
  const listeners = {}
  return {
    listeners,
    socket: {
      connected: false,
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
  onBatchProgress: (cb) => {
    harness.socket.on('batch_progress', cb)
    return () => harness.socket.off('batch_progress', cb)
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

import { useBatchProgress } from '../composables/useBatchProgress'

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

function trigger(event, data) {
  harness.socket.emit(event, data)
}

describe('useBatchProgress', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    Object.keys(harness.listeners).forEach(k => delete harness.listeners[k])
    harness.socket.connected = false
    apiMocks.batchStatus.mockResolvedValue({ batch_id: 'b1', total: 2, completed: 0 })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('batch_progress 推送：当前批次更新，其他批次拒绝（防串台）', async () => {
    const { result, wrapper } = withSetup(useBatchProgress)
    result.batchResult.value = { batch_id: 'b1', count: 2 }

    trigger('batch_progress', { batch_id: 'other', total: 9 })
    expect(result.batchResult.value.summary).toBeUndefined()

    trigger('batch_progress', { batch_id: 'b1', total: 2, completed: 1, all_done: false })
    expect(result.batchResult.value.summary).toEqual({ batch_id: 'b1', total: 2, completed: 1, all_done: false })
    wrapper.unmount()
  })

  it('socket 断开时降级 10s 轮询，重连后停止轮询并补拉一次', async () => {
    const { result, wrapper } = withSetup(useBatchProgress)
    result.batchResult.value = { batch_id: 'b1', count: 2 }

    // 断开（connected=false）→ 启动降级轮询
    result.syncWithSocket(false)
    const before = apiMocks.batchStatus.mock.calls.length
    vi.advanceTimersByTime(10000)
    expect(apiMocks.batchStatus.mock.calls.length).toBeGreaterThan(before)
    expect(apiMocks.batchStatus).toHaveBeenLastCalledWith('b1')

    // 重连 → 停止轮询 + 立即补拉一次
    harness.socket.connected = true
    result.syncWithSocket(true)
    const afterReconnect = apiMocks.batchStatus.mock.calls.length
    expect(afterReconnect).toBeGreaterThan(0)
    vi.advanceTimersByTime(30000)
    expect(apiMocks.batchStatus.mock.calls.length).toBe(afterReconnect)
    wrapper.unmount()
  })

  it('批次全部完成（all_done）：不再轮询也不再补拉', async () => {
    const { result, wrapper } = withSetup(useBatchProgress)
    result.batchResult.value = { batch_id: 'b1', count: 2, summary: { all_done: true } }

    const before = apiMocks.batchStatus.mock.calls.length
    result.syncWithSocket(false)
    vi.advanceTimersByTime(30000)
    expect(apiMocks.batchStatus.mock.calls.length).toBe(before)
    wrapper.unmount()
  })

  it('组件卸载：socket 监听全部解绑、轮询清理', async () => {
    const { result, wrapper } = withSetup(useBatchProgress)
    result.batchResult.value = { batch_id: 'b1', count: 2 }
    result.syncWithSocket(false)
    wrapper.unmount()

    // 卸载后事件不再生效
    trigger('batch_progress', { batch_id: 'b1', total: 9 })
    expect(result.batchResult.value.summary).toBeUndefined()
    // 定时器已清理
    vi.advanceTimersByTime(30000)
    expect(apiMocks.batchStatus.mock.calls.filter(c => c[0] === 'b1').length).toBeLessThanOrEqual(1)
  })
})
