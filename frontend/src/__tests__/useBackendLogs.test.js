import { describe, expect, it, vi, beforeEach } from 'vitest'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'

const apiMocks = vi.hoisted(() => ({ getLogs: vi.fn() }))
const messageMocks = vi.hoisted(() => ({ warning: vi.fn() }))

vi.mock('element-plus', () => ({
  ElMessage: { warning: messageMocks.warning },
}))

vi.mock('../utils/api', () => ({
  jobsApi: apiMocks,
}))

import { useBackendLogs } from '../composables/useBackendLogs'

/** 在真实组件 setup 中执行 composable，激活生命周期钩子 */
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

describe('useBackendLogs', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('正常增量：以旧末行为锚点拼接新行，不触发全量重载', async () => {
    apiMocks.getLogs.mockResolvedValueOnce({ lines: ['l1', 'l2', 'l3', 'l4', 'l5'] })
    const { result, wrapper } = withSetup(useBackendLogs)
    // 默认收起：不轮询；展开后 watch 触发首次 fetchLogs
    result.showBackendLogs.value = true
    await vi.waitFor(() => expect(result.backendLogs.value).toEqual(['l1', 'l2', 'l3', 'l4', 'l5']))

    apiMocks.getLogs.mockResolvedValueOnce({ lines: ['l3', 'l4', 'l5', 'l6', 'l7'] })
    await result.fetchLogs()
    expect(result.backendLogs.value).toEqual(['l1', 'l2', 'l3', 'l4', 'l5', 'l6', 'l7'])
    expect(apiMocks.getLogs).not.toHaveBeenCalledWith(2000)
    wrapper.unmount()
  })

  it('无新增：旧末行仍是窗口末行，保持不动', async () => {
    apiMocks.getLogs.mockResolvedValueOnce({ lines: ['a', 'b', 'c'] })
    const { result, wrapper } = withSetup(useBackendLogs)
    result.showBackendLogs.value = true
    await vi.waitFor(() => expect(result.backendLogs.value).toEqual(['a', 'b', 'c']))

    apiMocks.getLogs.mockResolvedValueOnce({ lines: ['a', 'b', 'c'] })
    await result.fetchLogs()
    expect(result.backendLogs.value).toEqual(['a', 'b', 'c'])
    wrapper.unmount()
  })

  it('日志爆发（旧末行滚出窗口）或文件被清：回退全量重载，不静默丢更新', async () => {
    apiMocks.getLogs.mockResolvedValueOnce({ lines: ['l1', 'l2', 'l3'] })
    const { result, wrapper } = withSetup(useBackendLogs)
    result.showBackendLogs.value = true
    await vi.waitFor(() => expect(result.backendLogs.value).toEqual(['l1', 'l2', 'l3']))

    const full = Array.from({ length: 500 }, (_, i) => `h${i}`)
    // 爆发后 200 行窗口里已没有旧末行 l3 → 触发 loadAllHistory（getLogs(2000)）
    apiMocks.getLogs.mockResolvedValueOnce({ lines: full.slice(300) })
    apiMocks.getLogs.mockResolvedValueOnce({ lines: full })
    await result.fetchLogs()

    expect(apiMocks.getLogs).toHaveBeenLastCalledWith(2000)
    expect(result.backendLogs.value.length).toBe(500)
    wrapper.unmount()
  })

  it('连续 3 次拉取失败：提示一次（不刷屏），成功后重置计数', async () => {
    apiMocks.getLogs.mockRejectedValue(new Error('network down'))
    const { result, wrapper } = withSetup(useBackendLogs)
    result.showBackendLogs.value = true
    await vi.waitFor(() => expect(apiMocks.getLogs).toHaveBeenCalled())

    await result.fetchLogs()
    await result.fetchLogs()
    expect(messageMocks.warning).toHaveBeenCalledTimes(1)

    // 恢复成功 → 计数重置，再失败 2 次不提示
    apiMocks.getLogs.mockResolvedValueOnce({ lines: ['ok'] })
    await result.fetchLogs()
    apiMocks.getLogs.mockRejectedValue(new Error('down again'))
    await result.fetchLogs()
    await result.fetchLogs()
    expect(messageMocks.warning).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('默认收起不轮询；收起停止轮询，重新展开恢复', async () => {
    apiMocks.getLogs.mockResolvedValue({ lines: ['x'] })
    const { result, wrapper } = withSetup(useBackendLogs)
    // 默认收起：不发起任何拉取
    expect(apiMocks.getLogs).not.toHaveBeenCalled()

    result.showBackendLogs.value = true
    await vi.waitFor(() => expect(apiMocks.getLogs).toHaveBeenCalled())

    const callsBefore = apiMocks.getLogs.mock.calls.length
    result.showBackendLogs.value = false
    expect(result.stopPolling)
    // 再手动拉取确认面板收起不影响主动刷新能力
    await result.refreshLogs()
    expect(apiMocks.getLogs.mock.calls.length).toBeGreaterThan(callsBefore)

    result.showBackendLogs.value = true
    await vi.waitFor(() => expect(apiMocks.getLogs.mock.calls.length).toBeGreaterThan(2))
    wrapper.unmount()
  })
})
