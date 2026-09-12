import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const apiMocks = vi.hoisted(() => ({ status: vi.fn() }))

vi.mock('../utils/api', () => ({
  summaryApi: { deepConfig: vi.fn() },
  asrApi: { status: apiMocks.status },
}))

import { useSettingsStore } from '../stores/settingsStore'

describe('settingsStore 默认转录引擎（消费后端 recommended_engine）', () => {
  let consoleSpy

  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
    consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    consoleSpy.mockRestore()
  })

  it('免费优先：免费档可用时默认引擎落 cloud_free（不再强制 local）', async () => {
    apiMocks.status.mockResolvedValue({
      cloud_configured: true, free_configured: true, firered_ready: false,
      recommended_engine: 'cloud_free', model: 'mimo',
    })
    const store = useSettingsStore()
    await store.loadCloudAsrStatus()
    expect(store.advancedOptions.transcribe_engine).toBe('cloud_free')
    expect(store.freeCloudAvailable).toBe(true)
  })

  it('仅配置免费档：默认 cloud_free', async () => {
    apiMocks.status.mockResolvedValue({
      cloud_configured: false, free_configured: true, firered_ready: false,
      recommended_engine: 'cloud_free',
    })
    const store = useSettingsStore()
    await store.loadCloudAsrStatus()
    expect(store.advancedOptions.transcribe_engine).toBe('cloud_free')
  })

  it('云端全不可用：按推荐落 local', async () => {
    apiMocks.status.mockResolvedValue({
      cloud_configured: false, free_configured: false, firered_ready: false,
      recommended_engine: 'local',
    })
    const store = useSettingsStore()
    await store.loadCloudAsrStatus()
    expect(store.advancedOptions.transcribe_engine).toBe('local')
  })

  it('用户已手选引擎（firered）：不被推荐覆盖', async () => {
    const store = useSettingsStore()
    store.advancedOptions.transcribe_engine = 'firered'
    apiMocks.status.mockResolvedValue({
      cloud_configured: true, free_configured: true, firered_ready: true,
      recommended_engine: 'cloud_free',
    })
    await store.loadCloudAsrStatus()
    expect(store.advancedOptions.transcribe_engine).toBe('firered')
  })

  it('旧后端缺 recommended_engine 字段：保持默认 cloud（不误降 local）', async () => {
    apiMocks.status.mockResolvedValue({
      cloud_configured: true, free_configured: false, firered_ready: false,
    })
    const store = useSettingsStore()
    await store.loadCloudAsrStatus()
    expect(store.advancedOptions.transcribe_engine).toBe('cloud')
  })

  it('status 请求失败：保守落 local 兜底', async () => {
    apiMocks.status.mockRejectedValue(new Error('Network Error'))
    const store = useSettingsStore()
    await store.loadCloudAsrStatus()
    expect(store.advancedOptions.transcribe_engine).toBe('local')
  })
})
