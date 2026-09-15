import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { summaryApi, asrApi } from '../utils/api'

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
    try { localStorage.setItem(USER_RECIPES_KEY, JSON.stringify(userRecipes.value)) } catch { /* 忽略：隐私模式/quota 下降级为不持久化 */ }
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
    language: '简体中文',      // ★ 第 9 维度：输出语言（外文素材默认翻译成中文呈现）
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
    // ★ 转录引擎：cloud_free=云端 XingChen（免费，后端推荐优先）/ cloud=云端 MiMo-ASR（付费）/
    //   firered=本地 FireRedASR2（中英最准）/ local=本地 Whisper；
    //   ★ 占位值是**免费档**（不是付费 cloud）：loadCloudAsrStatus 在用户未触碰过引擎时按
    //   /api/asr/status 的 recommended_engine 替换（免费优先）；付费档只能由用户显式选择
    //   （历史泄漏：占位 'cloud' 让"还没加载完就提交"落到付费档）。运行时失败自动回退本地。
    transcribe_engine: 'cloud_free',
    // ★ 转录设备：auto=自动探测（有GPU用GPU）/ cuda=强制CUDA(不可用回退CPU) / cpu=强制CPU（仅本地引擎）
    whisper_device: 'auto',
    // ★ 默认中文：Whisper自动识别会把中文误判成英文，强制中文避免误识别
    transcribe_language: 'zh',
    force_transcribe: false,
    ai_correct: false,
    ai_correct_model: '',
    // ★ 校正领域提示词模板：default=通用 / tcm=中医（保护经典引文与用量观点不被改写）
    ai_correct_domain: 'default',
  })
  // ★ 用户是否手动动过「转录引擎」下拉：动过之后 loadCloudAsrStatus 不再用推荐值覆盖它。
  //   用显式标记而非"当前值 === 占位值"判断——后者无法把"用户主动选了付费档"与"仍是占位"分开。
  const engineTouched = ref(false)
  function touchEngine() { engineTouched.value = true }

  // 云端 ASR 可用性（/api/asr/status 同步；未配置时云端选项禁用并回落 local）
  const cloudAsrAvailable = ref(false)
  const cloudAsrModel = ref('')
  // 免费云端（engine=cloud_free，XingChen Ultra）；本地 FireRedASR 模型就绪状态
  const freeCloudAvailable = ref(false)
  const fireredReady = ref(false)

  // 功能开关
  const doSummary = ref(true)
  const previewBeforeSummary = ref(false)  // 默认不做转录预览确认（批量固定忽略此开关）

  // ★ 任务目标（仅生成字幕模式，2026-09-13）：summary=完整处理 / subtitle_only=只出字幕不总结。
  //   后端 do_summary=false 本就「拿完文本即收尾」，这里只做语义包装：jobGoal↔doSummary、
  //   subtitleSource↔advancedOptions.force_transcribe 单点映射，组件不各自翻译布尔。
  //   来源语义（用户定稿）：auto=优先平台字幕，拿不到自动回退转录；transcribe=跳过平台字幕直接转录。
  const jobGoal = computed({
    get: () => (doSummary.value ? 'summary' : 'subtitle_only'),
    set: (v) => { doSummary.value = v === 'summary' },
  })
  const subtitleSource = computed({
    get: () => (advancedOptions.value.force_transcribe ? 'transcribe' : 'auto'),
    set: (v) => { advancedOptions.value.force_transcribe = v === 'transcribe' },
  })

  // ★ 输出素材（2026-09-13 v4 起为真实生效项）：结果页「下载素材」按勾选过滤展示；
  //   video 勾选在提交时映射为既有 options.download_video（链接任务真实下载视频原件）。
  //   默认含核心产物 md/srt/txt；「仅生成字幕」模式强制包含 srt（HomeAdvancedOptions watcher）
  const outputOptions = ref(['result_md', 'srt', 'txt'])

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
    await Promise.all([loadDeepConfig(), loadCloudAsrStatus()])
  }

  // 拉取云端 ASR 配置状态：默认引擎消费后端推荐（免费优先），云端选项可用性同步
  async function loadCloudAsrStatus() {
    try {
      const res = await asrApi.status()
      cloudAsrAvailable.value = !!res.cloud_configured
      cloudAsrModel.value = res.model || ''
      freeCloudAvailable.value = !!res.free_configured
      fireredReady.value = !!res.firered_ready
      // ★ 默认引擎策略单点在后端（routers/asr.py recommended_engine）：
      //   前端只消费者——用户未手动动过引擎时才消费推荐值（免费优先），手选不被覆盖
      const recommended = res.recommended_engine
      if (recommended && !engineTouched.value) {
        advancedOptions.value.transcribe_engine = recommended
      }
    } catch (err) {
      console.error('加载云端 ASR 状态失败:', err)
      cloudAsrAvailable.value = false
      // 状态不可用（后端未起/网络异常）：保守落 local，不猜云端（更不猜付费档）
      if (!engineTouched.value) {
        advancedOptions.value.transcribe_engine = 'local'
      }
    }
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
      // ★ merge 而非整体替换：配方未定义的维度保留当前值；内置配方已含 language=简体中文（应用即回默认简中）
      selectedDimensions.value = { ...selectedDimensions.value, ...recipe.dimensions }
      advancedOptions.value.dimensions = { ...selectedDimensions.value }
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
    engineTouched,
    touchEngine,
    cloudAsrAvailable,
    cloudAsrModel,
    freeCloudAvailable,
    fireredReady,
    doSummary,
    jobGoal,
    subtitleSource,
    previewBeforeSummary,
    outputOptions,
    // actions
    loadDeepConfig,
    loadCloudAsrStatus,
    init,
    loadSummaryTypes,
    loadSummaryOptions,
  }
})
