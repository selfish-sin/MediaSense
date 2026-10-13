import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import HistoryView from '../views/HistoryView.vue'

const apiMocks = vi.hoisted(() => ({
  list: vi.fn(),
  batchDelete: vi.fn(),
  batchCancel: vi.fn(),
  batchDiskInfo: vi.fn(),
  delete: vi.fn(),
}))
const messageMocks = vi.hoisted(() => ({
  error: vi.fn(), warning: vi.fn(), success: vi.fn(), info: vi.fn(),
}))

vi.mock('element-plus', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    ElMessage: {
      error: messageMocks.error, warning: messageMocks.warning,
      success: messageMocks.success, info: messageMocks.info,
    },
    ElMessageBox: { confirm: vi.fn().mockResolvedValue('confirm'), prompt: vi.fn() },
  }
})

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))
vi.mock('../utils/api', () => ({ jobsApi: apiMocks, exportApi: { batch: vi.fn() } }))

function job(id, title) {
  return { id, title, status: 'completed', platform: 'bili', model: 'm', created_at: '2026-10-13 10:00:00' }
}

const ALL = Array.from({ length: 500 }, (_, i) => job(i + 1, `任务 ${i + 1}`))

async function mountView() {
  apiMocks.list.mockImplementation(({ keyword } = {}) => {
    const rows = keyword ? ALL.filter(j => j.title.includes(keyword)) : ALL
    return Promise.resolve({ jobs: rows.slice(0, 20), total: rows.length })
  })
  const wrapper = shallowMount(HistoryView)
  await flushPromises()
  return wrapper
}

// 走「全选当前筛选」拿到 500 项选择集
async function selectAllFiltered(wrapper) {
  apiMocks.list.mockResolvedValue({ jobs: ALL, total: ALL.length })
  await wrapper.vm.handleSelectAllFiltered()
  await flushPromises()
}

describe('HistoryView 全选当前筛选（C2）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.batchDiskInfo.mockResolvedValue({ jobs: [], total_size_bytes: 0 })
    apiMocks.batchDelete.mockResolvedValue({ deleted_count: 1, message: 'ok' })
  })

  it('全选后随手勾一行 = 排除该行，绝不把 500 项塌成 1 项', async () => {
    const wrapper = await mountView()
    await selectAllFiltered(wrapper)
    expect(wrapper.vm.activeSelection).toHaveLength(500)

    // 用户点了其中一行的勾选框
    wrapper.vm.handleSelectionChange([ALL[0]])
    await flushPromises()

    expect(wrapper.vm.selectAllMode).toBe(true)
    expect(wrapper.vm.activeSelection).toHaveLength(499)
    expect(wrapper.vm.activeSelection.some(j => j.id === ALL[0].id)).toBe(false)
    // 必须有可见说明，不允许静默替换
    expect(messageMocks.info).toHaveBeenCalled()
  })

  it('排除到最后一项时退出全选模式并提示', async () => {
    const wrapper = await mountView()
    apiMocks.list.mockResolvedValue({ jobs: [job(1, 'a')], total: 1 })
    await wrapper.vm.handleSelectAllFiltered()
    await flushPromises()

    wrapper.vm.handleSelectionChange([job(1, 'a')])
    await flushPromises()

    expect(wrapper.vm.selectAllMode).toBe(false)
    expect(wrapper.vm.activeSelection).toHaveLength(0)
    expect(messageMocks.info).toHaveBeenCalled()
  })

  it('clearSelection 的空回声不退出全选模式', async () => {
    const wrapper = await mountView()
    await selectAllFiltered(wrapper)

    wrapper.vm.handleSelectionChange([])
    await flushPromises()

    expect(wrapper.vm.selectAllMode).toBe(true)
    expect(wrapper.vm.activeSelection).toHaveLength(500)
  })

  it('改关键词重新搜索后全选模式失效（工具条不再谎称"当前筛选全部"）', async () => {
    const wrapper = await mountView()
    await selectAllFiltered(wrapper)

    wrapper.vm.searchQuery = '任务 1'
    await wrapper.vm.doSearch()
    await flushPromises()

    expect(wrapper.vm.selectAllMode).toBe(false)
    expect(wrapper.vm.activeSelection).toHaveLength(0)
    expect(messageMocks.warning).toHaveBeenCalled()
  })

  it('同关键词重复搜索不该白白丢掉有效的全选集合', async () => {
    const wrapper = await mountView()
    wrapper.vm.searchQuery = '任务 1'
    await selectAllFiltered(wrapper)

    await wrapper.vm.doSearch()
    await flushPromises()

    expect(wrapper.vm.selectAllMode).toBe(true)
    expect(wrapper.vm.activeSelection).toHaveLength(500)
  })

  it('全选集合过期后批量删除被拦下，绝不按旧集合删', async () => {
    const wrapper = await mountView()
    await selectAllFiltered(wrapper)

    // 用户改了关键词但还没点搜索（表格内容已按新关键词，实际未刷新）
    wrapper.vm.searchQuery = '别的关键词'
    await wrapper.vm.handleBatchDelete()
    await flushPromises()

    expect(apiMocks.batchDiskInfo).not.toHaveBeenCalled()
    expect(apiMocks.batchDelete).not.toHaveBeenCalled()
    expect(wrapper.vm.selectAllMode).toBe(false)
    expect(messageMocks.warning).toHaveBeenCalledWith(expect.stringContaining('失效'))
  })

  it('未过期时批量删除照常按全选集合执行', async () => {
    const wrapper = await mountView()
    await selectAllFiltered(wrapper)

    apiMocks.batchDiskInfo.mockResolvedValue({
      jobs: ALL.map(j => ({ ...j, size_bytes: 0, exists: true })),
      total_size_bytes: 0,
    })
    await wrapper.vm.handleBatchDelete()
    await flushPromises()

    expect(apiMocks.batchDelete).toHaveBeenCalledWith(
      ALL.map(j => j.id), false, true,
    )
  })
})