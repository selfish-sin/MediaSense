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

  it('正常增量：拼接新行，不触发全量重载', async () => {
    apiMocks.getLogs.mockResolvedValueOnce({ total: 5, lines: ['l1', 'l2', 'l3', 'l4', 'l5'] })
    const { result, wrapper } = withSetup(useBackendLogs)
    // watch immediate → startPolling → 首次 fetchLogs
    await vi.waitFor(() => expect(result.backendLogs.value).toEqual(['l1', 'l2', 'l3', 'l4', 'l5']))

    apiMocks.getLogs.mockResolvedValueOnce({ total: 7, lines: ['l3', 'l4', 'l5', 'l6', 'l7'] })
    await result.fetchLogs()
    expect(result.backendLogs.value).toEqual(['l1', 'l2', 'l3', 'l4', 'l5', 'l6', 'l7'])
    expect(apiMocks.getLogs).not.toHaveBeenCalledWith(2000)
    wrapper.unmount()
  })

  it('增量超界（日志爆发增长）：回退全量重载，不静默丢更新', async () => {
    apiMocks.getLogs.mockResolvedValueOnce({ total: 3, lines: ['l1', 'l2', 'l3'] })
    const { result, wrapper } = withSetup(useBackendLogs)
    await vi.waitFor(() => expect(result.backendLogs.value).toEqual(['l1', 'l2', 'l3']))

    const full = Array.from({ length: 500 }, (_, i) => `h${i}`)
    // 第一次：delta = 497 > 200 → 触发 loadAllHistory（getLogs(2000)）
    apiMocks.getLogs.mockResolvedValueOnce({ total: 500, lines: full.slice(0, 200) })
    apiMocks.getLogs.mockResolvedValueOnce({ total: 500, lines: full })
    await result.fetchLogs()

    expect(apiMocks.getLogs).toHaveBeenLastCalledWith(2000)
    expect(result.backendLogs.value.length).toBe(500)
    wrapper.unmount()
  })

  it('连续 3 次拉取失败：提示一次（不刷屏），成功后重置计数', async () => {
    apiMocks.getLogs.mockRejectedValue(new Error('network down'))
    const { result, wrapper } = withSetup(useBackendLogs)
    await vi.waitFor(() => expect(apiMocks.getLogs).toHaveBeenCalled())

    await result.fetchLogs()
    await result.fetchLogs()
    expect(messageMocks.warning).toHaveBeenCalledTimes(1)

    // 恢复成功 → 计数重置，再失败 2 次不提示
    apiMocks.getLogs.mockResolvedValueOnce({ total: 1, lines: ['ok'] })
    await result.fetchLogs()
    apiMocks.getLogs.mockRejectedValue(new Error('down again'))
    await result.fetchLogs()
    await result.fetchLogs()
    expect(messageMocks.warning).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('收起日志面板即停止轮询，重新展开恢复', async () => {
    apiMocks.getLogs.mockResolvedValue({ total: 1, lines: ['x'] })
    const { result, wrapper } = withSetup(useBackendLogs)
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
