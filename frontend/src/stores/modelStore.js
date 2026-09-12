import { defineStore } from 'pinia'
import { ref } from 'vue'
import { modelsApi } from '../utils/api'

export const useModelStore = defineStore('model', () => {
  const models = ref([])
  const defaultModel = ref('glm-4.5-air')
  // AI 校正默认模型（校正模型选择器留空时后端使用；空串 = 回退总结主模型）
  const correctDefault = ref('')
  const loading = ref(false)

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
    loadModels,
    addModel,
    updateModel,
    deleteModel,
    testModel,
    setDefaultModel,
  }
})
