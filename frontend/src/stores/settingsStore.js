import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { summaryApi } from '../utils/api'

export const useSettingsStore = defineStore('settings', () => {
  // ============ 模块化提示词系统（8 维度） ============
  const promptDimensions = ref({})      // 8 维度的模块定义（从后端加载，含 DB 覆盖）
  // ★ 预设配方 = 后端 RECIPES + 前端本地自定义配方（localStorage 持久化，不改后端）
  const backendRecipes = ref([])        // 后端返回的内置配方（只读）
  const USER_RECIPES_KEY = 'yt_tool_user_recipes'
  function loadUserRecipes() {
    try {
      const raw = localStorage.getItem(USER_RECIPES_KEY)
      return raw ? JSON.parse(raw) : []
    } catch { return [] }
  }
  function persistUserRecipes() {
    try { localStorage.setItem(USER_RECIPES_KEY, JSON.stringify(userRecipes.value)) } catch {}
  }
  const userRecipes = ref(loadUserRecipes())  // 前端自定义配方
  // 合并后的全部配方（供 DimensionSelector 应用条 / TemplatesView 编辑器使用）
  // 去重：避免内置配方被「另存为」前意外生成同 key 副本导致列表出现重复项
  const recipes = computed(() => {
    const seen = new Set()
    const out = []
    for (const r of [...backendRecipes.value, ...userRecipes.value]) {
      if (seen.has(r.key)) continue
      seen.add(r.key)
      out.push(r)
    }
    return out
  })
  // 当前选择的 8 个维度值（key → module_key）
  const selectedDimensions = ref({
    role: 'general',
    mode: 'general',          // ★ 第 8 维度：通用 / 社科深度特化
    rules: 'standard',
    output: 'summary',
    content_type: 'general',
    depth: 'standard',
    tone: '学术严谨',
    word_count: '3000',
  })
  // ★ 维度级自定义文本（dimKey → 自定义文本，作为追加指引）
  const customDimensions = ref({})
  // ★ 完全自定义提示词（非空时覆盖一切维度拼装）
  const fullOverride = ref('')
  const useFullOverride = ref(false)

  // ============ 兼容旧系统（保留某些字段供 HomeView 过渡期使用） ============
  const summaryTypes = ref([])          // 旧：15种类型（保留 ref 避免别处引用报错，但会留空）
  const summaryOptions = ref({})        // 旧：选项配置
  const selectedModel = ref('glm-4.5-air')
  function recommendModel(wordCount) {
    const n = parseInt(wordCount) || 0
    if (n >= 5000) return 'deepseek-v4-flash'
    return 'glm-4.5-air'
  }
  const selectedSummaryType = ref('summary')  // 兼容旧字段

  // 高级选项（保留转录相关 + 提示词覆盖；word_count/tone/format/purpose 改从 dimensions 派生）
  const advancedOptions = ref({
    // 模块化提示词维度直接嵌在 advancedOptions 中，便于 handleSubmit 一次性提交
    dimensions: { ...selectedDimensions.value },
    word_count: '3000',  // 兼容字段（从 dimensions.word_count 派生）
    tone: '学术严谨',     // 兼容字段
    format: 'Markdown',
    purpose: '学习笔记',
    speaker_diarization: false,
    model_size: 'medium',
    // ★ 默认中文：Whisper自动识别会把中文误判成英文，强制中文避免误识别
    transcribe_language: 'zh',
    force_transcribe: false,
    ai_correct: false,
    ai_correct_model: '',
  })

  // 功能开关
  const doSummary = ref(true)
  const doTranslate = ref(false)
  const doSubtitle = ref(false)
  const previewBeforeSummary = ref(true)

  // ============ Actions ============
  async function loadDeepConfig() {
    try {
      const res = await summaryApi.deepConfig()
      promptDimensions.value = res.dimensions || {}
      backendRecipes.value = res.recipes || []
      if (res.defaults) {
        selectedDimensions.value = { ...res.defaults }
        // 同步到 advancedOptions.dimensions
        advancedOptions.value.dimensions = { ...res.defaults }
        // 派生兼容字段
        advancedOptions.value.word_count = res.defaults.word_count || '3000'
        advancedOptions.value.tone = res.defaults.tone || '学术严谨'
      }
    } catch (err) {
      console.error('加载模块化提示词配置失败:', err)
    }
  }

  // 兼容旧的 init 函数（被 HomeView onMounted 调用）
  async function init() {
    await loadDeepConfig()
  }

  // 更新某个维度的选择（key 是维度名，value 是模块 key）
  function updateDimension(dimKey, moduleKey) {
    selectedDimensions.value[dimKey] = moduleKey
    // 同步到 advancedOptions
    advancedOptions.value.dimensions = { ...selectedDimensions.value }
    // 派生兼容字段
    if (dimKey === 'word_count') advancedOptions.value.word_count = moduleKey
    if (dimKey === 'tone') advancedOptions.value.tone = moduleKey
  }

  // 设置维度级自定义文本（作为追加指引）
  function setCustomDimension(dimKey, text) {
    if (text && text.trim()) {
      customDimensions.value[dimKey] = text.trim()
    } else {
      delete customDimensions.value[dimKey]
    }
  }

  // 设置完全自定义提示词
  function setFullOverride(text) {
    fullOverride.value = text || ''
  }

  // 切换完全自定义模式开关
  function toggleFullOverride(on) {
    useFullOverride.value = on
  }

  // 拼装提交给后端的 custom_prompt（维度级自定义的合并）
  function buildCustomPrompt() {
    const parts = []
    for (const [dimKey, text] of Object.entries(customDimensions.value)) {
      parts.push(`【${dimKey} 自定义】\n${text}`)
    }
    return parts.join('\n\n')
  }

  // 应用预设配方
  function applyRecipe(recipe) {
    if (recipe?.dimensions) {
      selectedDimensions.value = { ...recipe.dimensions }
      advancedOptions.value.dimensions = { ...recipe.dimensions }
      advancedOptions.value.word_count = recipe.dimensions.word_count || '3000'
      advancedOptions.value.tone = recipe.dimensions.tone || '学术严谨'
    }
  }

  // 重新加载维度（编辑器保存覆盖后调用，刷新 is_customized 标记）
  async function reloadDimensions() {
    try {
      const res = await summaryApi.dimensions()
      promptDimensions.value = res.dimensions || {}
    } catch (err) {
      console.error('重新加载维度失败:', err)
    }
  }

  // ============ 前端自定义配方（localStorage 持久化，无需后端） ============
  // 保存/更新一个自定义配方（recipe: {key, name, desc, dimensions}）
  function saveUserRecipe(recipe) {
    const idx = userRecipes.value.findIndex(r => r.key === recipe.key)
    if (idx >= 0) {
      userRecipes.value[idx] = recipe
    } else {
      userRecipes.value.push(recipe)
    }
    persistUserRecipes()
  }

  // 删除自定义配方（后端内置配方不可删）
  function deleteUserRecipe(key) {
    userRecipes.value = userRecipes.value.filter(r => r.key !== key)
    persistUserRecipes()
  }

  // 生成唯一配方 key
  function genRecipeKey(name) {
    const base = 'usr_' + (name || 'recipe').replace(/[^a-zA-Z0-9\u4e00-\u9fa5]+/g, '_')
    let key = base
    let i = 1
    const allKeys = new Set(recipes.value.map(r => r.key))
    while (allKeys.has(key)) { key = `${base}_${i++}` }
    return key
  }

  // 兼容旧 API（保留空实现，避免引用报错）
  async function loadSummaryTypes() {}
  async function loadSummaryOptions() {}

  return {
    // 新：模块化提示词
    promptDimensions,
    recipes,
    selectedDimensions,
    customDimensions,
    fullOverride,
    useFullOverride,
    updateDimension,
    setCustomDimension,
    setFullOverride,
    toggleFullOverride,
    buildCustomPrompt,
    applyRecipe,
    reloadDimensions,
    saveUserRecipe,
    deleteUserRecipe,
    genRecipeKey,
    // 兼容旧字段
    summaryTypes,
    summaryOptions,
    selectedModel,
    recommendModel,
    selectedSummaryType,
    advancedOptions,
    doSummary,
    doTranslate,
    doSubtitle,
    previewBeforeSummary,
    // actions
    loadDeepConfig,
    init,
    loadSummaryTypes,
    loadSummaryOptions,
  }
})
