import { defineStore } from 'pinia'
import { ref } from 'vue'
import { modelsApi } from '../utils/api'

export const useModelStore = defineStore('model', () => {
  const models = ref([])
  const defaultModel = ref('glm-4.5-air')
  const loading = ref(false)

  async function loadModels() {
    loading.value = true
    try {
      const res = await modelsApi.list()
      models.value = res.models || []
      defaultModel.value = res.default || 'glm-4.5-air'
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
    loading,
    loadModels,
    addModel,
    updateModel,
    deleteModel,
    testModel,
    setDefaultModel,
  }
})
