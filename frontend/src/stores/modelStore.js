import { defineStore } from 'pinia'
import { ref } from 'vue'
import { modelsApi } from '../utils/api'

export const useModelStore = defineStore('model', () => {
  const models = ref([])
  const defaultModel = ref('glm-4.5-air')
  // AI 校正默认模型（校正模型选择器留空时后端使用；空串 = 回退总结主模型）
  const correctDefault = ref('')
  // AI 调用兜底环（2026-10-05）：enabled=是否启用；chain=有序候选 key，顺序即优先级。
  // 不设条数上限——环长由用户负责，失败耗时与"环里大半失效"直接相关。
  const fallbackChain = ref([])
  const fallbackEnabled = ref(false)
  const fallbackLoading = ref(false)
  const loading = ref(false)

  async function loadFallbackChain() {
    fallbackLoading.value = true
    try {
      const res = await modelsApi.getFallbackChain()
      fallbackChain.value = res.chain || []
      fallbackEnabled.value = !!res.enabled
      return res
    } finally {
      fallbackLoading.value = false
    }
  }

  /** 保存兜底链。服务端会剔除已删模型、去重、限量，故以后端回读结果为准。 */
  async function saveFallbackChain(chain, enabled) {
    const res = await modelsApi.setFallbackChain(chain, enabled)
    fallbackChain.value = res.chain || []
    fallbackEnabled.value = !!res.enabled
    return res
  }

  async function loadModels() {
    loading.value = true
    try {
      const res = await modelsApi.list()
      models.value = res.models || []
      defaultModel.value = res.default || 'glm-4.5-air'
      correctDefault.value = res.correct_default || ''
    } catch (err) {
      console.error('加载模型失败:', err)
    } finally {
      loading.value = false
    }
  }

  async function addModel(data) {
    await modelsApi.add(data)
    await loadModels()
  }

  async function updateModel(key, data) {
    await modelsApi.update(key, data)
    await loadModels()
  }

  async function deleteModel(key) {
    await modelsApi.delete(key)
    await loadModels()
  }

  async function testModel(key) {
    return await modelsApi.test({ key })
  }

  async function setDefaultModel(key) {
    await modelsApi.setDefault(key)
    await loadModels()
  }

  return {
    models,
    defaultModel,
    correctDefault,
    loading,
    fallbackChain,
    fallbackEnabled,
    fallbackLoading,
    loadModels,
    loadFallbackChain,
    saveFallbackChain,
    addModel,
    updateModel,
    deleteModel,
    testModel,
    setDefaultModel,
  }
})
