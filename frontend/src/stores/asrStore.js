import { defineStore } from 'pinia'
import { ref } from 'vue'
import { asrApi } from '../utils/api'

/**
 * ASR（转录）配置状态：模型管理页「ASR 模型」域使用。
 *
 * - 只读总览走 loadOverview（失败落到 error 供页面显示失败态，不抛错）
 * - 写操作（增删改/测试/设置/本地模型）全部抛错，由调用方 ElMessage 提示——
 *   禁止静默失败（规范：错误必须可见可行动）
 */
export const useAsrStore = defineStore('asr', () => {
  const providers = ref([])
  // local.engines 是本地引擎列表（单一事实源在后端注册表）：每项含
  // key/name/fee/languages/note/size_hint/ready/busy/bytes/parts/manual_hint/download
  //   —— 新增本地引擎前端零改动；Whisper 的档位明细单独在 local.whisper
  const local = ref({
    engines: [],
    sherpa_available: false,
    whisper: { models: [], default_model: '', device: 'auto' },
  })
  const settings = ref({ default_engine: '', whisper_model: '', whisper_device: 'auto' })
  const recommendedEngine = ref('')
  const loading = ref(false)
  const error = ref('')

  // 引擎值 → 显示名（与后端 transcriber.engine_label 文案一致；前端只做展示）
  // 收费标识不在这里推断：云端 provider 由后端给 fee，本地引擎一律「免费」
  const ENGINE_LABELS = {
    cloud: '云端（收费）',
    cloud_free: '云端（免费）',
    firered: '本地 FireRedASR',
    local: '本地 Whisper',
  }

  async function loadOverview() {
    loading.value = true
    error.value = ''
    try {
      const res = await asrApi.overview()
      providers.value = res.providers || []
      local.value = res.local || local.value
      settings.value = res.settings || settings.value
      recommendedEngine.value = res.recommended_engine || ''
    } catch (err) {
      error.value = err.message
      // 总览失败时清空列表，避免页面继续展示上一次的陈旧数据（假就绪）
      providers.value = []
    } finally {
      loading.value = false
    }
  }

  async function addProvider(payload) {
    const res = await asrApi.addProvider(payload)
    await loadOverview()
    return res
  }

  async function updateProvider(key, payload) {
    const res = await asrApi.updateProvider(key, payload)
    await loadOverview()
    return res
  }

  async function deleteProvider(key) {
    const res = await asrApi.deleteProvider(key)
    await loadOverview()
    return res
  }

  async function testProvider(key) {
    return await asrApi.testProvider(key)
  }

  async function saveSettings(patch) {
    const res = await asrApi.saveSettings(patch)
    settings.value = res.settings || settings.value
    recommendedEngine.value = res.recommended_engine || ''
    return res
  }

  async function startEngineDownload(engine) {
    const res = await asrApi.startEngineDownload(engine)
    await refreshDownloadState(engine)
    return res
  }

  async function cancelEngineDownload(engine) {
    const res = await asrApi.cancelEngineDownload(engine)
    await refreshDownloadState(engine)
    return res
  }

  /** 轻量轮询：只取该引擎的下载状态，不触发磁盘遍历（总览较重，别用来轮询） */
  async function refreshDownloadState(engine) {
    const res = await asrApi.engineDownloadState(engine)
    // 合并语义：只更新目标引擎字段，其余引擎与 whisper 段保持不动
    local.value = {
      ...local.value,
      engines: (local.value.engines || []).map((e) =>
        e.key === engine ? { ...e, ...res, download: res.download } : e),
    }
    return res
  }

  async function removeEngine(engine) {
    const res = await asrApi.removeEngine(engine)
    await loadOverview()
    return res
  }

  async function removeWhisper(size) {
    const res = await asrApi.removeWhisper(size)
    await loadOverview()
    return res
  }

  return {
    providers,
    local,
    settings,
    recommendedEngine,
    loading,
    error,
    ENGINE_LABELS,
    loadOverview,
    addProvider,
    updateProvider,
    deleteProvider,
    testProvider,
    saveSettings,
    startEngineDownload,
    cancelEngineDownload,
    refreshDownloadState,
    removeEngine,
    removeWhisper,
  }
})
