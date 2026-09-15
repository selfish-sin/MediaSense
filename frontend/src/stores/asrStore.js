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
  const local = ref({
    whisper: { models: [], default_model: '', device: 'auto' },
    firered: { ready: false, files: [], bytes: 0, busy: false, download: { status: 'idle', percent: 0, message: '', error: '' } },
  })
  const settings = ref({ default_engine: '', whisper_model: '', whisper_device: 'auto' })
  const recommendedEngine = ref('')
  const loading = ref(false)
  const error = ref('')

  // 引擎值 → 显示名（与后端 transcriber.engine_label 文案一致；前端只做展示）
  const ENGINE_LABELS = {
    cloud: '云端付费档',
    cloud_free: '云端免费档',
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

  async function startFireredDownload() {
    const res = await asrApi.startFireredDownload()
    await refreshDownloadState()
    return res
  }

  async function cancelFireredDownload() {
    const res = await asrApi.cancelFireredDownload()
    await refreshDownloadState()
    return res
  }

  /** 轻量轮询：只取下载状态，不触发磁盘遍历（总览较重，别用来轮询） */
  async function refreshDownloadState() {
    const res = await asrApi.fireredDownloadState()
    local.value = {
      ...local.value,
      firered: { ...(local.value.firered || {}), ...res, download: res.download },
    }
    return res
  }

  async function removeFirered() {
    const res = await asrApi.removeFirered()
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
    startFireredDownload,
    cancelFireredDownload,
    refreshDownloadState,
    removeFirered,
    removeWhisper,
  }
})
