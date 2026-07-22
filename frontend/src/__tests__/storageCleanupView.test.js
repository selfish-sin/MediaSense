import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import StorageCleanupView from '../views/StorageCleanupView.vue'

const apiMocks = vi.hoisted(() => ({
  status: vi.fn(),
  preview: vi.fn(),
  apply: vi.fn(),
  audit: vi.fn(),
}))
const messageMocks = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  prompt: vi.fn(),
}))

vi.mock('element-plus', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    ElMessage: { success: messageMocks.success, error: messageMocks.error },
    ElMessageBox: { prompt: messageMocks.prompt },
  }
})

vi.mock('../utils/api', () => ({
  storageCleanupApi: apiMocks,
}))

const readyState = {
  phase: 'ready',
  plan_id: 'abcdef0123456789',
  plan_expires_at: 1784743200,
  operation_id: null,
  started_at: '2026-07-23T01:00:00+00:00',
  finished_at: '2026-07-23T01:00:01+00:00',
  progress: { current: 0, total: 2, percent: 0, bytes_reclaimed: 0 },
  results: [],
  logs: [{ timestamp: '2026-07-23T01:00:01+00:00', level: 'info', message: 'Preview ready' }],
  audit: [],
  active_jobs: 0,
  error: null,
  policy: {
    staged_ttl_hours: 72,
    orphan_ttl_days: 14,
    export_ttl_days: 7,
    media_ttl_days: 7,
    quarantine_ttl_days: 30,
    prune_url_audio: false,
    max_items: 1000,
    max_bytes: 20 * 1024 ** 3,
  },
  plan: {
    candidate_count: 2,
    total_bytes: 4096,
    counts: { expired_export: 2 },
    bytes_by_category: { expired_export: 4096 },
    candidates: [
      { category: 'expired_export', action: 'delete', size: 4096, path: 'D:/data/old.pdf', reason: 'expired' },
      { category: 'expired_export', action: 'delete', size: 0, path: 'D:/data/empty.txt', reason: 'expired' },
    ],
    protected: [{ path: 'D:/data/keep.txt', reason: 'protected' }],
  },
}

describe('StorageCleanupView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.status.mockResolvedValue(structuredClone(readyState))
    apiMocks.preview.mockResolvedValue(structuredClone(readyState))
  })

  it('loads and renders the current preview state', async () => {
    const wrapper = shallowMount(StorageCleanupView)
    await flushPromises()

    expect(apiMocks.status).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('清理工作台')
    expect(wrapper.text()).toContain('候选项')
    expect(wrapper.text()).toContain('预计释放')
    expect(wrapper.text()).toContain('计划 abcdef0123')
  })

  it('runs a dry-run preview from the policy controls', async () => {
    const wrapper = shallowMount(StorageCleanupView)
    await flushPromises()
    const previewButton = wrapper.find('[type="primary"]')

    expect(previewButton.exists()).toBe(true)
    await previewButton.trigger('click')
    await flushPromises()

    expect(apiMocks.preview).toHaveBeenCalledOnce()
    expect(apiMocks.preview.mock.calls[0][0]).toMatchObject({
      staged_ttl_hours: 72,
      prune_url_audio: false,
      max_items: 1000,
    })
    expect(messageMocks.success).toHaveBeenCalledWith('预览已生成，未修改任何文件')
  })
})
