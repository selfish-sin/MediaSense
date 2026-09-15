import { describe, expect, it, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const api = vi.hoisted(() => ({
  overview: vi.fn(),
  addProvider: vi.fn(),
  updateProvider: vi.fn(),
  deleteProvider: vi.fn(),
  testProvider: vi.fn(),
  saveSettings: vi.fn(),
  engineDownloadState: vi.fn(),
  startEngineDownload: vi.fn(),
  cancelEngineDownload: vi.fn(),
  removeEngine: vi.fn(),
  removeWhisper: vi.fn(),
}))

vi.mock('../utils/api', () => ({ asrApi: api }))

import { useAsrStore } from '../stores/asrStore'

const IDLE = { status: 'idle', percent: 0, message: '', error: '' }

const OVERVIEW = {
  providers: [
    { key: 'mimo', name: '小米 MiMo', engine: 'cloud', api: 'chat', endpoint: 'https://m.example.com',
      model: 'mimo-v2.5-asr', api_key: 'sk-a****ijkl', has_key: true, key_broken: false,
      enabled: true, is_builtin: true, configured: true, runtime: true, sort_order: 10, fee: '收费' },
  ],
  local: {
    // 引擎列表由后端注册表驱动（含免费标识）；Whisper 也在其中（key='local'），档位明细单独一段
    engines: [
      { key: 'local', name: '本地 Whisper', fee: '免费', ready: true, busy: false,
        bytes: 150, parts: [], download: IDLE },
      { key: 'firered', name: '本地 FireRedASR', fee: '免费', ready: false, busy: false,
        bytes: 0, parts: [], download: IDLE },
    ],
    sherpa_available: true,
    whisper: {
      models: [{ size: 'base', path: 'X:/models/whisper_base', ready: true, partial: false, bytes: 150 }],
      default_model: 'base',
      device: 'auto',
    },
  },
  settings: { default_engine: '', whisper_model: 'base', whisper_device: 'auto' },
  recommended_engine: 'cloud_free',
}

describe('asrStore（ASR 模型管理）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    api.overview.mockResolvedValue(OVERVIEW)
  })

  it('loadOverview 填充 provider/本地引擎列表/设置/推荐引擎', async () => {
    const store = useAsrStore()
    await store.loadOverview()
    expect(store.providers).toHaveLength(1)
    expect(store.providers[0].runtime).toBe(true)
    expect(store.local.engines.map((e) => e.key)).toEqual(['local', 'firered'])
    expect(store.local.whisper.models[0].size).toBe('base')
    expect(store.local.sherpa_available).toBe(true)
    expect(store.recommendedEngine).toBe('cloud_free')
    expect(store.loading).toBe(false)
    expect(store.error).toBe('')
  })

  it('引擎显示名用「收费/免费」表述（界面不再出现「档」）', () => {
    const store = useAsrStore()
    expect(store.ENGINE_LABELS.cloud).toBe('云端（收费）')
    expect(store.ENGINE_LABELS.cloud_free).toBe('云端（免费）')
  })

  it('loadOverview 失败时记录错误并清空列表（不展示陈旧数据造成假就绪）', async () => {
    const store = useAsrStore()
    await store.loadOverview()
    expect(store.providers).toHaveLength(1)

    api.overview.mockRejectedValue(new Error('网络不可用'))
    await store.loadOverview()
    expect(store.error).toBe('网络不可用')
    expect(store.providers).toEqual([])
    expect(store.loading).toBe(false)
  })

  it('增删改 provider 后自动重载总览，失败向上抛给调用方', async () => {
    const store = useAsrStore()
    api.addProvider.mockResolvedValue({ success: true })
    api.updateProvider.mockResolvedValue({ success: true })
    api.deleteProvider.mockResolvedValue({ success: true })

    await store.addProvider({ key: 'x', name: 'X' })
    await store.updateProvider('x', { name: 'X2' })
    await store.deleteProvider('x')
    expect(api.addProvider).toHaveBeenCalledWith({ key: 'x', name: 'X' })
    expect(api.updateProvider).toHaveBeenCalledWith('x', { name: 'X2' })
    // 三次写操作各触发一次重载
    expect(api.overview).toHaveBeenCalledTimes(3)

    api.deleteProvider.mockRejectedValue(new Error('内置 provider 不可删除'))
    await expect(store.deleteProvider('mimo')).rejects.toThrow('内置 provider 不可删除')
  })

  it('saveSettings 同步 settings 与推荐引擎', async () => {
    const store = useAsrStore()
    api.saveSettings.mockResolvedValue({
      settings: { default_engine: 'local', whisper_model: 'small', whisper_device: 'cpu' },
      recommended_engine: 'local',
    })
    await store.saveSettings({ default_engine: 'local' })
    expect(api.saveSettings).toHaveBeenCalledWith({ default_engine: 'local' })
    expect(store.settings.default_engine).toBe('local')
    expect(store.recommendedEngine).toBe('local')
  })

  it('refreshDownloadState 只取该引擎的下载状态，不触发整页总览', async () => {
    const store = useAsrStore()
    await store.loadOverview()
    api.engineDownloadState.mockResolvedValue({
      engine: 'firered',
      download: { status: 'downloading', percent: 42, message: '下载中', error: '' },
      ready: false,
      busy: false,
    })
    await store.refreshDownloadState('firered')
    expect(api.engineDownloadState).toHaveBeenCalledWith('firered')
    expect(api.overview).toHaveBeenCalledTimes(1)  // 未再次拉总览
    const firered = store.local.engines.find((e) => e.key === 'firered')
    expect(firered.download.percent).toBe(42)
    // 合并语义：其它引擎与 whisper 段不被清空
    expect(store.local.engines.find((e) => e.key === 'local').bytes).toBe(150)
    expect(store.local.whisper.models).toHaveLength(1)
  })

  it('本地模型操作：删除后重载，下载状态查询失败向上抛', async () => {
    const store = useAsrStore()
    api.removeWhisper.mockResolvedValue({ success: true, freed_bytes: 100 })
    api.removeEngine.mockResolvedValue({ success: true, freed_bytes: 200 })
    await store.removeWhisper('base')
    await store.removeEngine('firered')
    expect(api.removeWhisper).toHaveBeenCalledWith('base')
    expect(api.removeEngine).toHaveBeenCalledWith('firered')
    expect(api.overview).toHaveBeenCalledTimes(2)   // 两次删除各重载一次

    api.engineDownloadState.mockRejectedValue(new Error('后端未就绪'))
    await expect(store.refreshDownloadState('firered')).rejects.toThrow('后端未就绪')
  })

  it('testProvider 原样透传后端结果（含失败原因）', async () => {
    const store = useAsrStore()
    api.testProvider.mockResolvedValue({ success: false, error: '云端 ASR 401: 密钥无效', latency_ms: 12 })
    const res = await store.testProvider('mimo')
    expect(api.testProvider).toHaveBeenCalledWith('mimo')
    expect(res.success).toBe(false)
    expect(res.error).toContain('401')
  })
})
