<script setup>
import { ref, onMounted, onUnmounted, onActivated, computed, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { UploadCloud, Pencil, Check, X, Copy, Loader2, ArrowDown } from 'lucide-vue-next'
import { useJobStore } from '../stores/jobStore'
import { useSettingsStore } from '../stores/settingsStore'
import { useModelStore } from '../stores/modelStore'
import { jobsApi, exportApi, tokenApi, resultApi } from '../utils/api'
import { buildSamePromptPayload } from '../utils/jobActions'
import socket, {
  onAiToken, onAiDone, onPreview, onAiThinking,
  continueSummary, cancelPreview, cancelJob,
} from '../utils/socket'
import { useBatchProgress } from '../composables/useBatchProgress'
import { useJobRestore } from '../composables/useJobRestore'
import { useRafMarkdown } from '../composables/useMarkdownRender'
import { copyText } from '../utils/clipboard'
import { createFailureNotifier } from '../utils/errorNotice'
import { uploadRejectReason } from '../utils/uploadRules'
import BackendLogPanel from '../components/BackendLogPanel.vue'
import BatchResultPanel from '../components/BatchResultPanel.vue'
import HomeAdvancedOptions from '../components/HomeAdvancedOptions.vue'
import JobProgressPanel from '../components/JobProgressPanel.vue'
import ResummarizeControl from '../components/ResummarizeControl.vue'
import SplitPromptPanel from '../components/SplitPromptPanel.vue'
import ChatPanel from '../components/ChatPanel.vue'

// 思维导图渲染：markmap 体积较大，切到思维导图 Tab 时再动态加载
let transformer = null

const jobStore = useJobStore()
const router = useRouter()
const route = useRoute()
const settings = useSettingsStore()
const modelStore = useModelStore()

// 后台静默拉取失败提示（连续 3 次才提示一次，成功即重置，避免刷屏）
const statusCheckNotifier = createFailureNotifier('任务状态检查失败，请确认后端服务正常')
const jobFilesNotifier = createFailureNotifier('任务文件列表加载失败')
const tokenNotifier = createFailureNotifier('Token 统计拉取失败')

// ============ 输入区域状态 ============
// ★ 输入内容持久化到 sessionStorage（F5刷新不丢，关标签页清空）
const INPUT_STORAGE_KEY = 'yt_tool_input_state'

function saveInputState() {
  try {
    sessionStorage.setItem(INPUT_STORAGE_KEY, JSON.stringify({
      inputType: inputType.value,
      inputUrl: inputUrl.value,
      inputText: inputText.value,
      batchUrls: batchUrls.value,
      batch_id: batchResult.value?.batch_id || null,
    }))
  } catch { /* sessionStorage 不可用时静默降级 */ }
}

function loadInputState() {
  try {
    const raw = sessionStorage.getItem(INPUT_STORAGE_KEY)
    if (raw) {
      const s = JSON.parse(raw)
      if (s.inputType) inputType.value = s.inputType
      if (s.inputUrl) inputUrl.value = s.inputUrl
      if (s.inputText) inputText.value = s.inputText
      if (s.batchUrls) batchUrls.value = s.batchUrls
      if (s.batch_id) {
        batchResult.value = { batch_id: s.batch_id, count: 0, skipped: 0, summary: null }
        refreshBatchStatus()
      }
    }
  } catch { /* sessionStorage 不可用时静默降级 */ }
}

const inputType = ref('url')  // url / file / text / batch
const inputUrl = ref('')
const inputText = ref('')
const batchUrls = ref('')
const uploading = ref(false)
// 重新处理的来源任务，仅用于提示和配置回填，不代表当前运行任务
const rerunSourceJobId = ref(null)

// 输入内容变化时持久化
watch([inputType, inputUrl, inputText, batchUrls], () => saveInputState(), { deep: true })

// 总结思考过程显隐（★ 必须在 script setup 顶层声明，模板才能访问）
const showThinking = ref(true)
// 模型是否被用户手动改过（决定是否按字数自动推荐）
const modelTouched = ref(false)
// ★ 旧内置默认 key：仅当选择还停留在这些值（用户没设过默认/没手选）时才自动推荐
const LEGACY_MODEL_KEYS = new Set(['', 'glm-4.5-air', 'deepseek-v4-flash'])
function isLegacyModelSelection(key) {
  return LEGACY_MODEL_KEYS.has(key)
}

// ── 转录引擎提示文案（engineHint/speakerDiarizationDesc/transcribeLanguageHint）
//    随高级选项卡迁至 components/HomeAdvancedOptions.vue（2026-09-12 slice-2） ──

watch(() => settings.selectedDimensions.word_count, (wordCount) => {
  if (!modelTouched.value && isLegacyModelSelection(settings.selectedModel)) {
    settings.selectedModel = settings.recommendModel(wordCount)
  }
}, { immediate: true })

// ★ Socket 监听解绑函数集合（onUnmounted 时逐个调用，只移除本组件注册的 handler）
let socketUnsubs = []

// ── 任务进度：已拆为 components/JobProgressPanel.vue（socket 监听/展开与滚动状态组件内自持） ──

// ── 系统日志：已拆为 components/BackendLogPanel.vue（useBackendLogs 组件内自持） ──

// ★ 批量任务结果与进度：Socket.IO batch_progress 实时推送为主；
//   socket 断开时降级为低频（10s）HTTP 轮询，重连后补拉一次对齐状态
// 逻辑抽离到 composables/useBatchProgress（socket 绑定+降级轮询自管理）
const { batchResult, refreshBatchStatus, syncWithSocket } = useBatchProgress()

// 跳转到任务详情页（批量任务的子任务点击）
function viewBatchJob(jobId) {
  router.push(`/result/${jobId}`)
}

// 进度卡模板引用：滚动/展开由 JobProgressPanel.reveal() 负责
const progressPanelRef = ref(null)

/** 提交后滚到任务进度（工作台第一眼）——等 DOM 更新（组件挂载）后再调用 */
function scrollToJobProgress() {
  nextTick(() => progressPanelRef.value?.reveal())
}

// ============ 组件挂载 ============
onMounted(async () => {
    await Promise.all([
    settings.init(),
    modelStore.loadModels(),
    loadTokenUsage(),
    loadTokenHistory(),
  ])
  // 追问模型列表由 ChatPanel 自行加载

  // ★ 默认模型：用户在模型管理页设置了默认模型且当前未手动选过时，工作台默认选中它
  if (!modelTouched.value && isLegacyModelSelection(settings.selectedModel) && modelStore.defaultModel) {
    settings.selectedModel = modelStore.defaultModel
  }

  // 恢复输入内容（sessionStorage）
  loadInputState()

  // ★ 路由参数处理（rerun / restore 检测 + 任务恢复）
  await handleRouteQuery()

  // ============ Socket 事件监听 ============
  // ★ 关键改动：preview 事件不再仅限 currentJobId，任何任务进入 preview 都自动切换跟踪
  // onProgress/onLog 监听已随进度卡迁至 components/JobProgressPanel.vue（2026-09-12 slice-4）
  socketUnsubs.push(onAiToken((data) => {
    if (data.job_id === jobStore.currentJobId) {
      jobStore.appendAiToken(data)
    }
  }))
  socketUnsubs.push(onAiThinking((data) => {
    if (data.job_id === jobStore.currentJobId) {
      jobStore.appendAiThinking(data)
    }
  }))

  socketUnsubs.push(onAiDone((data) => {
    if (data.job_id === jobStore.currentJobId) {
      if (data.success) {
        _realTimeComplete = true       // ★ 标记实时完成
        jobStore.jobStatus = 'completed'
        jobStore.isProcessing = false
        jobStore.addLog('AI处理完成')
        loadTokenUsage()  // 刷新Token统计
        loadTokenHistory() // 刷新历史
      } else {
        jobStore.addLog(`处理失败: ${data.error}`)
        jobStore.jobStatus = 'failed'
        jobStore.isProcessing = false
      }
    }
    // 批量子任务完成时刷新批量进度
    if (batchResult.value?.batch_id) {
      refreshBatchStatus()
    }
  }))
  socketUnsubs.push(onPreview((data) => {
    // ★ 核心：preview 事件自动切换跟踪到该任务
    // 不管是不是当前 currentJobId，都切换过来让用户确认
    if (data.job_id !== jobStore.currentJobId) {
      console.log('[Preview自动切换] 从', jobStore.currentJobId, '切换到', data.job_id)
    }
    jobStore.currentJobId = data.job_id
    jobStore.setPreview(data)
    jobStore.isProcessing = true
    jobStore.saveState()
    // 滚动到预览区域
    nextTick(() => {
      document.getElementById('preview-section')?.scrollIntoView({ behavior: 'smooth' })
    })
  }))
  // 批量进度 socket 订阅与日志轮询由 useBatchProgress / useBackendLogs 自管理
})

// keep-alive: 批量进度与系统日志的轮询启停由各自 composable 的
// onActivated/onDeactivated 钩子处理，这里只保留任务相关的恢复逻辑
onActivated(async () => {
  // keep-alive 切回时处理路由参数（rerun / restore）
  await handleRouteQuery()

  // 检查任务是否已完成（切页期间可能跑完了）
  if (jobStore.currentJobId && jobStore.isProcessing) {
    jobsApi.get(jobStore.currentJobId).then(job => {
      statusCheckNotifier.ok()
      if (job.status === 'completed' && jobStore.jobStatus !== 'completed') {
        jobStore.jobStatus = 'completed'
        jobStore.isProcessing = false
        jobStore.resultText = job.result_text || jobStore.resultText
        jobStore.addLog('任务已完成')
        loadTokenUsage()
        loadTokenHistory()
      }
    }).catch(() => statusCheckNotifier.fail())
  }
})

// 组件销毁时清理（keep-alive 不会触发，但路由从缓存中移除时会触发）
onUnmounted(() => {
  // ★ 只移除本组件注册的 socket 监听（不用全局 removeAllListeners，避免误清 keep-alive 其他组件）
  socketUnsubs.forEach(off => off())
  socketUnsubs = []
})

// ============ 桌面通知 ============
let lastNotifiedJobId = null
let _realTimeComplete = false  // ★ 标记任务是否实时完成（区分历史加载和实时完成）

watch(() => jobStore.jobStatus, (newStatus) => {
  if (newStatus === 'completed' && _realTimeComplete && jobStore.currentJobId && jobStore.currentJobId !== lastNotifiedJobId) {
    _realTimeComplete = false  // 重置，防止重复通知
    lastNotifiedJobId = jobStore.currentJobId
    if ('Notification' in window && Notification.permission === 'granted') {
      const title = jobStore.resultText.slice(0, 50).replace(/\n/g, ' ') + '...'
      new Notification('任务处理完成', {
        body: title || '点击查看结果',
        icon: '/favicon.ico',
      })
    } else if ('Notification' in window && Notification.permission !== 'denied') {
      Notification.requestPermission().then(perm => {
        if (perm === 'granted') {
          const title = jobStore.resultText.slice(0, 50).replace(/\n/g, ' ') + '...'
          new Notification('任务处理完成', {
            body: title || '点击查看结果',
            icon: '/favicon.ico',
          })
        }
      })
    }
  }
  if (newStatus === 'failed') {
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification('任务处理失败', { body: '请查看日志了解详情', icon: '/favicon.ico' })
    }
  }
})

// ============ 提交任务 ============
async function handleSubmit() {
  // 同步自定义选项值
  syncCustomToOptions()

  // 提示词覆盖（关闭时清除，避免粘滞覆盖维度配置）
  if (usePromptOverride.value && promptOverride.value) {
    settings.advancedOptions.edited_prompt = promptOverride.value
  } else {
    delete settings.advancedOptions.edited_prompt
  }

  // ★ 完全自定义提示词库：TemplatesView 启用时，full_override 覆盖一切维度拼装
  if (settings.useFullOverride && settings.fullOverride) {
    settings.advancedOptions.full_override = settings.fullOverride
  } else {
    delete settings.advancedOptions.full_override
  }

  // ★ 维度「+ 自定义」指引：预览用 buildCustomPrompt，提交也必须带上
  const dimCustom = settings.buildCustomPrompt()
  const baseCustom = (settings.advancedOptions.custom_prompt || '').trim()
  const mergedCustom = [dimCustom, baseCustom].filter(Boolean).join('\n\n')

  const jobData = {
    input_type: inputType.value,
    model: settings.selectedModel,
    // ★ 模块化提示词：优先传 dimensions；summary_type 作为兼容字段保留
    summary_type: settings.selectedSummaryType,
    dimensions: { ...settings.selectedDimensions },
    options: {
      ...settings.advancedOptions,
      dimensions: { ...settings.selectedDimensions },
      output_options: [...outputOptions.value],
      ...(mergedCustom ? { custom_prompt: mergedCustom } : {}),
    },
    do_summary: settings.doSummary,
    force_new: inputType.value === 'url',
    do_translate: settings.doTranslate,
    do_subtitle: settings.doSubtitle,
    preview_before_summary: settings.previewBeforeSummary,
    output_options: [...outputOptions.value],  // 输出内容选择
  }
  if (inputType.value === 'url') {
    if (!inputUrl.value.trim()) { ElMessage.warning('请输入视频URL'); return }
    jobData.input_url = inputUrl.value.trim()
  } else if (inputType.value === 'file') {
    if (!uploadedFilePath.value) { ElMessage.warning('请先上传文件'); return }
    jobData.input_file_path = uploadedFilePath.value
  } else if (inputType.value === 'text') {
    if (!inputText.value.trim()) { ElMessage.warning('请输入文本内容'); return }
    jobData.input_text = inputText.value.trim()
  } else if (inputType.value === 'batch') {
    const urls = batchUrls.value.split('\n').map(u => u.trim()).filter(u => u)
    if (urls.length === 0) { ElMessage.warning('请输入至少一个URL'); return }
    // ★ 传完整参数（和单任务一致），后端负责去重+batch_id聚合
    try {
      // ★ 完全自定义提示词库 + 维度自定义指引（同单任务处理）
      const batchOptions = { ...settings.advancedOptions, dimensions: { ...settings.selectedDimensions } }
      if (settings.useFullOverride && settings.fullOverride) {
        batchOptions.full_override = settings.fullOverride
      } else {
        delete batchOptions.full_override
      }
      if (mergedCustom) {
        batchOptions.custom_prompt = mergedCustom
      }
      if (!(usePromptOverride.value && promptOverride.value)) {
        delete batchOptions.edited_prompt
      }
      const res = await jobsApi.batch({
        urls,
        options: batchOptions,
        model: settings.selectedModel,
        summary_type: settings.selectedSummaryType,
        dimensions: { ...settings.selectedDimensions },
        do_summary: settings.doSummary,
        do_translate: settings.doTranslate,
        do_subtitle: settings.doSubtitle,
        // ★ 批量固定不做转录预览确认（后端 batch_submit 同样强制忽略），流程统一直通总结
        force_new: true,
      })
      batchResult.value = {
        batch_id: res.batch_id,
        count: res.count,
        skipped: res.skipped || 0,
        duplicates: res.duplicates || [],
        summary: null,
      }
      // 如果有跨历史重复的 URL，提示用户
      if (res.duplicates && res.duplicates.length > 0) {
        const dupInfo = res.duplicates.map(d =>
          `${d.existing_title || d.url}（${d.existing_status}）`
        ).join('、')
        ElMessage.warning({
          message: `${res.duplicates.length} 个URL已有处理记录，已跳过: ${dupInfo}`,
          duration: 5000,
        })
      }
      ElMessage.success(`已提交 ${res.count} 个批量任务${res.skipped ? `（跳过 ${res.skipped} 个无效/重复）` : ''}`)
      // 立即拉一次进度；后续由 batch_progress 推送实时更新（断线时自动降级轮询）
      refreshBatchStatus()
      syncWithSocket(socket.connected)
      scrollToJobProgress()
      return
    } catch (err) {
      ElMessage.error(err.message); return
    }
  }

  // 重置结果区状态
  editMode.value = false
  resultTab.value = 'markdown'

  try {
    await jobStore.submitJob(jobData)
    ElMessage.success(rerunSourceJobId.value ? '已创建新的重新处理任务' : '任务已提交')
    rerunSourceJobId.value = null
    scrollToJobProgress()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

// ============ 文件上传 ============
// 上传区可见状态只由上传结果驱动：成功才显示「已上传」，失败/超限不留任何痕迹
const uploadedFilePath = ref('')
const uploadedFileName = ref('')
const uploadingFileName = ref('')
const uploadRef = ref(null)

async function handleFileUpload(file) {
  if (file.status !== 'ready') return
  // 内部列表始终清空：不展示未上传成功的文件，也保证同一文件可以重新选择
  uploadRef.value?.clearFiles()
  const reject = uploadRejectReason(file.name, file.raw?.size || 0)
  if (reject) {
    ElMessage.error({ message: reject, duration: 6000 })
    return
  }
  if (uploading.value) { ElMessage.warning('文件正在上传中，请稍候'); return }
  uploading.value = true
  uploadingFileName.value = file.name
  try {
    const formData = new FormData()
    formData.append('file', file.raw)
    const api = (await import('../utils/api.js')).default
    const res = await api.post('/jobs/upload', formData, {
      timeout: 120000,
    })
    if (res.success) {
      uploadedFilePath.value = res.file_path
      uploadedFileName.value = res.file_name
      ElMessage.success(`文件上传成功: ${res.file_name}`)
    } else {
      ElMessage.error(res.message || '上传失败')
    }
  } catch (err) {
    if (err.response) {
      console.error('[上传失败]', err.response.status, JSON.stringify(err.response.data, null, 2))
      console.error('[请求头]', err.config?.headers)
    }
    ElMessage.error(err.message)
  } finally {
    uploading.value = false
    uploadingFileName.value = ''
  }
}

function handleFileRemove() {
  uploadedFilePath.value = ''
  uploadedFileName.value = ''
}

// ============ 转录预览确认/取消 ============
function handleConfirmPreview() {
  if (jobStore.currentJobId) {
    continueSummary(jobStore.currentJobId, jobStore.previewText)
    jobStore.jobStatus = 'summarizing'
    jobStore.addLog('用户确认继续AI总结...')
    // 批量任务确认后刷新进度
    if (batchResult.value?.batch_id) {
      setTimeout(() => refreshBatchStatus(), 1000)
    }
  }
}
function handleCancelPreview() {
  if (jobStore.currentJobId) {
    cancelPreview(jobStore.currentJobId)
    jobStore.reset()
    // 批量任务取消后刷新进度
    if (batchResult.value?.batch_id) {
      setTimeout(() => refreshBatchStatus(), 1000)
    }
  }
}

// ★ 批量子任务的预览确认/取消（从批量进度面板直接操作）
async function confirmBatchPreview(jobId) {
  // 先加载该任务的 preview_text
  try {
    const job = await jobsApi.get(jobId)
    if (job.status !== 'preview') {
      ElMessage.warning('该任务已不在预览状态')
      refreshBatchStatus()
      return
    }
    // 切换跟踪到该任务并发送确认
    jobStore.currentJobId = jobId
    jobStore.jobStatus = 'preview'
    jobStore.isProcessing = true
    jobStore.previewText = job.preview_text || ''
    jobStore.jobTitle = job.title || job.input_url || ''
    continueSummary(jobId, job.preview_text || '')
    jobStore.jobStatus = 'summarizing'
    jobStore.addLog('用户确认继续AI总结...')
    jobStore.saveState()
    ElMessage.success('已确认，AI总结进行中...')
    setTimeout(() => refreshBatchStatus(), 1500)
  } catch (err) {
    ElMessage.error('确认失败: ' + err.message)
  }
}
function cancelBatchPreview(jobId) {
  cancelPreview(jobId)
  ElMessage.info('已取消该任务')
  // 如果当前跟踪的就是这个任务，重置
  if (jobStore.currentJobId === jobId) {
    jobStore.reset()
  }
  setTimeout(() => refreshBatchStatus(), 1000)
}

// ============ 取消任务 ============
function handleCancelJob() {
  if (jobStore.currentJobId) {
    cancelJob(jobStore.currentJobId)
    jobStore.reset()
    ElMessage.info('任务已取消')
  }
}

// ============ 导出 ============
async function handleExport(format) {
  if (!jobStore.currentJobId) { ElMessage.warning('没有可导出的任务'); return }
  try {
    const res = await exportApi[format](jobStore.currentJobId)
    if (res.success) {
      ElMessage.success('导出成功')
    } else {
      ElMessage.info(res.message || '导出功能待实现')
    }
  } catch (err) {
    ElMessage.error(err.message)
  }
}

// ============ 计算属性 ============
// ★ rAF 节流渲染 Markdown：每帧最多渲染一次，避免高频 token 推送导致 O(n) 重渲染堆积
// （逻辑抽离到 composables/useMarkdownRender，组件卸载时自清 rAF 回调）
const { html: renderedResult } = useRafMarkdown(() => jobStore.resultText)
const isPreviewState = computed(() => jobStore.jobStatus === 'preview')
const isProcessing = computed(() => jobStore.isProcessing)

// 输出内容选择（默认全选 AI总结+字幕+转录文本）
const outputOptions = ref(['result_md'])

// 任务关联文件列表（字幕/音频/视频等素材）
const jobFiles = computed(() => {
  if (!jobStore.currentJobId) return []
  // 从 jobStore 获取 files，需要异步加载
  // 这里用一个 ref 来存储
  return _jobFiles.value
})
const _jobFiles = ref([])

async function loadJobFiles(jobId) {
  if (!jobId) { _jobFiles.value = []; return }
  try {
    const job = await jobsApi.get(jobId)
    const files = job.files || {}
    const labelMap = {
      result_md: 'AI总结', srt: '字幕文件', txt: '转录文本',
      audio: '纯音频', video: '视频文件', translated_srt: '翻译字幕', translation: '翻译文本',
    }
    const result = []
    for (const [key, path] of Object.entries(files)) {
      if (!path || typeof path !== 'string') continue
      const ext = path.split('.').pop()?.toUpperCase() || '?'
      result.push({ key, path, label: labelMap[key] || key, ext })
    }
    _jobFiles.value = result
    jobFilesNotifier.ok()
  } catch {
    jobFilesNotifier.fail()
    _jobFiles.value = []
  }
}

function downloadJobFile(path) {
  if (!path) return
  // 通过 download API 下载
  const url = `/api/download/file?path=${encodeURIComponent(path)}`
  window.open(url, '_blank')
}

// 任务完成时加载文件列表
watch(() => jobStore.jobStatus, (val) => {
  if (val === 'completed' && jobStore.currentJobId) {
    loadJobFiles(jobStore.currentJobId)
  }
})

// ============ 结果区 Tab 管理 ============
// markdown / mindmap / bilingual
const resultTab = ref('markdown')

// 是否有思维导图内容（只在明确选择 mindmap 类型时显示）
const hasMindmap = computed(() => {
  if (!jobStore.resultText) return false
  return settings.selectedSummaryType === 'mindmap'
})

// ============ 思维导图渲染 ============
const mindmapRef = ref(null)
let markmapInstance = null

async function renderMindmap() {
  if (!mindmapRef.value || !jobStore.resultText) return
  // markmap 动态加载：仅首次进入思维导图 Tab 时拉取，缩小首包体积
  const [{ Transformer }, { Markmap }] = await Promise.all([
    import('markmap-lib'),
    import('markmap-view'),
  ])
  // 动态加载期间组件可能已卸载/切换 Tab
  if (!mindmapRef.value || !jobStore.resultText) return
  if (!transformer) {
    try {
      transformer = new Transformer()
    } catch (e) {
      console.error('Failed to init Transformer:', e)
      resultTab.value = 'markdown'
      return
    }
  }
  const { root } = transformer.transform(jobStore.resultText)
  if (!markmapInstance) {
    markmapInstance = Markmap.create(mindmapRef.value, { autoFit: true }, root)
  } else {
    markmapInstance.setData(root)
    markmapInstance.fit()
  }
}

watch([resultTab, () => jobStore.resultText], () => {
  if (resultTab.value === 'mindmap' && hasMindmap.value) {
    nextTick(() => renderMindmap())
  }
})

// 切换 Tab 时如果思维导图不可用，回退到 markdown
watch(hasMindmap, (val) => {
  if (!val && resultTab.value === 'mindmap') resultTab.value = 'markdown'
})

// ============ 自定义选项输入（保留兼容，新系统由 DimensionSelector 管理） ============
const customPrompt = ref('')

// 同步自定义值到 advancedOptions（保留兼容入口）
function syncCustomToOptions() {
  settings.advancedOptions.custom_prompt = customPrompt.value
}

// 提示词覆盖（handleSubmit 组装与 useJobRestore 回填用；预览展示已由 DimensionSelector 承载）
const promptOverride = ref('')
const usePromptOverride = ref(false)

// ============ rerun/restore 路由参数处理与任务配置回填 ============
// 逻辑抽离到 composables/useJobRestore（依赖注入视图状态，供 onMounted/onActivated 复用）
const { handleRouteQuery } = useJobRestore({
  inputType, inputUrl, inputText,
  uploadedFilePath, uploadedFileName,
  outputOptions, customPrompt, promptOverride, usePromptOverride,
  rerunSourceJobId,
  settings, jobStore, route, router,
  scrollToJobProgress,
})

// ============ 结果在线编辑 ============
const editMode = ref(false)
const editText = ref('')
const savingEdit = ref(false)

function enterEditMode() {
  editText.value = jobStore.resultText
  editMode.value = true
  resultTab.value = 'markdown'
}
function cancelEdit() {
  editMode.value = false
  editText.value = ''
}
async function saveEdit() {
  if (!editText.value.trim()) { ElMessage.warning('内容不能为空'); return }
  savingEdit.value = true
  try {
    if (jobStore.currentJobId) {
      await resultApi.editResult(jobStore.currentJobId, editText.value)
    }
    jobStore.resultText = editText.value
    editMode.value = false
    ElMessage.success('已保存')
  } catch {
    // 即使后端保存失败，也更新前端状态
    jobStore.resultText = editText.value
    editMode.value = false
    ElMessage.warning('前端已更新，但后端保存失败')
  } finally {
    savingEdit.value = false
  }
}

// ============ 重新总结 ============
// 相同提示词（沿用任务原始 dimensions/options），仅更换 AI 模型；跳过下载/转录直接重跑 AI 步骤
async function handleResummarize(model) {
  // 重置流式输出状态
  jobStore.aiTokens = []
  jobStore.resultText = ''
  jobStore.jobStatus = 'summarizing'
  jobStore.isProcessing = true
  // 滚动到结果栏
  nextTick(() => {
    document.getElementById('result-section')?.scrollIntoView({ behavior: 'smooth' })
  })

  const targetJobId = jobStore.currentJobId
  if (!targetJobId) {
    ElMessage.error('没有任务ID，无法重新总结')
    jobStore.jobStatus = 'failed'
    jobStore.isProcessing = false
    return
  }

  // 相同提示词：取任务原始 dimensions/options 透传，只改 model；
  // 拿不到任务详情时仅传 model，后端自动回退原始配置
  const payload = { model }
  try {
    const job = await jobsApi.get(targetJobId)
    Object.assign(payload, buildSamePromptPayload(job, model))
  } catch { /* 忽略详情拉取失败，仅传 model */ }

  jobsApi.summarize(targetJobId, payload).then(() => {
    // 结果通过 socket 流式推送，无需在此处理 result_text
    console.log('[重新总结] API 调用成功，等待 socket 流式输出')
  }).catch(err => {
    ElMessage.error(`重新总结失败: ${err?.message || err}`)
    jobStore.jobStatus = 'failed'
    jobStore.isProcessing = false
  })
}

// 复制结果
async function copyResult() {
  return copyText(jobStore.resultText)
}

// ============ Token 统计 ============
const tokenUsage = ref(null)
const tokenHistory = ref([])
const tokenPanelOpen = ref(false)

async function loadTokenUsage() {
  try {
    const res = await tokenApi.usage()
    tokenUsage.value = res
    tokenNotifier.ok()
  } catch {
    tokenNotifier.fail()
  }
}

async function loadTokenHistory() {
  try {
    const res = await tokenApi.history(30)
    tokenHistory.value = res.records || []
    tokenNotifier.ok()
  } catch {
    tokenNotifier.fail()
  }
}

function formatTokenTime(t) {
  if (!t) return ''
  const d = new Date(t)
  const now = new Date()
  const diffMs = now - d
  if (diffMs < 60000) return '刚刚'
  if (diffMs < 3600000) return `${Math.floor(diffMs / 60000)}分钟前`
  if (diffMs < 86400000) return `${Math.floor(diffMs / 3600000)}小时前`
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

/* 追问聊天逻辑已抽离到 ChatPanel 组件，统一支持 SSE 流式/思考过程/引用源/编辑重发 */
</script>

<template>
  <div class="home-workbench">

    <!-- 输入区 -->
    <el-card class="wb-card" shadow="never">
      <template #header>
        <div class="card-head-row">
          <span class="wb-card-title">输入</span>
        </div>
      </template>

      <el-radio-group v-model="inputType" class="input-type-tabs" size="default">
        <el-radio-button value="url">URL</el-radio-button>
        <el-radio-button value="batch">批量 URL</el-radio-button>
        <el-radio-button value="file">文件上传</el-radio-button>
        <el-radio-button value="text">文本粘贴</el-radio-button>
      </el-radio-group>

      <!-- URL 输入 -->
      <el-input
        v-if="inputType === 'url'"
        v-model="inputUrl"
        placeholder="粘贴视频 URL"
        size="large"
        clearable
        class="input-main"
      >
        <template #prepend>URL</template>
      </el-input>

      <!-- 文件上传：内部列表隐藏，状态区只展示真实上传结果（成功才有「已上传」） -->
      <el-upload
        v-else-if="inputType === 'file'"
        ref="uploadRef"
        class="input-upload"
        drag
        :auto-upload="false"
        :show-file-list="false"
        :on-change="handleFileUpload"
        accept="audio/*,video/*,.srt,.vtt,.txt,.md"
      >
        <el-icon class="upload-icon"><UploadCloud /></el-icon>
        <div class="upload-title">拖拽文件到此处，或点击上传</div>
        <template #tip>
          <div class="upload-tip">
            支持音频、视频、字幕、TXT、Markdown，单个文件不超过 2GB
            <span v-if="uploading && uploadingFileName" class="upload-busy">上传中: {{ uploadingFileName }}…</span>
            <span v-else-if="uploadedFileName" class="upload-ok"><el-icon :size="12" style="vertical-align: -1px;"><Check /></el-icon> 已上传: {{ uploadedFileName }}<el-icon class="upload-remove" :size="12" title="移除" @click.stop="handleFileRemove"><X /></el-icon></span>
          </div>
        </template>
      </el-upload>

      <!-- 文本粘贴 -->
      <el-input
        v-else-if="inputType === 'text'"
        v-model="inputText"
        type="textarea"
        :rows="6"
        resize="vertical"
        placeholder="粘贴需要总结的文本内容"
        class="input-main"
      />

      <!-- 批量URL -->
      <el-input
        v-else-if="inputType === 'batch'"
        v-model="batchUrls"
        type="textarea"
        :rows="6"
        resize="vertical"
        placeholder="每行一个 URL"
        class="input-main"
      />

      <!-- 批量任务进度面板（已拆为 BatchResultPanel 组件） -->
      <BatchResultPanel
        v-if="inputType === 'batch' && batchResult"
        :batch-result="batchResult"
        @refresh="refreshBatchStatus"
        @view-job="viewBatchJob"
        @confirm-preview="confirmBatchPreview"
        @cancel-preview="cancelBatchPreview"
      />
    </el-card>

    <!-- 总结配置 -->
    <el-card class="wb-card" shadow="never">
      <template #header>
        <div class="card-head-row">
          <span class="wb-card-title">总结配置</span>
        </div>
      </template>
      <SplitPromptPanel />
    </el-card>

    <!-- 高级选项（已拆为 HomeAdvancedOptions 组件） -->
    <HomeAdvancedOptions v-model:output-options="outputOptions" @touch-model="modelTouched = true" />

    <!-- 提交按钮 -->
    <div class="submit-row">
      <el-button
        class="submit-btn"
        type="primary"
        size="large"
        :loading="isProcessing"
        :disabled="isProcessing"
        @click="handleSubmit"
      >{{ isProcessing ? '处理中...' : '开始处理' }}</el-button>
      <el-button v-if="isProcessing" type="danger" size="large" plain @click="handleCancelJob">取消</el-button>
    </div>

    <!-- 任务进度（已拆为 JobProgressPanel 组件） -->
    <JobProgressPanel ref="progressPanelRef" />

    <!-- 固定结果栏 -->
    <el-card id="result-section" class="wb-card result-card" shadow="never">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span class="wb-card-title">结果</span>
          <div style="display: flex; gap: 8px; align-items: center;">
            <!-- 流式输出状态指示 -->
            <el-tag v-if="isProcessing && jobStore.jobStatus === 'summarizing'" type="warning" size="small" effect="plain">
              AI 生成中...
            </el-tag>
            <!-- 重新总结内联控件：相同提示词，换模型直接重跑 AI 步骤 -->
            <ResummarizeControl
              v-if="jobStore.resultText && !editMode"
              :disabled="isProcessing"
              button-type="warning"
              @submit="handleResummarize"
            />
          </div>
        </div>
      </template>

      <!-- 无结果时的空状态 -->
      <el-empty v-if="!jobStore.resultText && !isProcessing" description="暂无结果，提交任务后在此展示" :image-size="60" />

      <!-- 处理中但还没开始AI输出 -->
      <div v-else-if="isProcessing && !jobStore.resultText && jobStore.aiTokens.length === 0" style="text-align: center; padding: 40px; color: #909399;">
        <el-icon class="is-loading" style="font-size: 24px;"><Loader2 /></el-icon>
        <div style="margin-top: 8px;">{{ jobStore.progress.msg || '处理中...' }}</div>
      </div>

      <!-- 流式输出 / 结果展示 -->
      <div v-else>
        <!-- 工具栏 -->
        <div v-if="jobStore.resultText && !editMode" style="display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap;">
          <el-radio-group v-model="resultTab" size="small">
            <el-radio-button value="markdown">图文</el-radio-button>
            <el-radio-button v-if="hasMindmap" value="mindmap">思维导图</el-radio-button>
          </el-radio-group>
          <div style="flex: 1;" />
          <!-- 素材导出：AI总结 / 字幕 / 音频 / 视频 -->
          <el-dropdown v-if="jobFiles.length" size="small" @command="downloadJobFile">
            <el-button size="small">
              下载素材<el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-for="f in jobFiles" :key="f.key" :command="f.path">
                  {{ f.label }} ({{ f.ext }})
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button-group>
            <el-button size="small" @click="handleExport('docx')">Word</el-button>
            <el-button size="small" @click="handleExport('pdf')">PDF</el-button>
            <el-button size="small" @click="handleExport('html')">HTML</el-button>
            <el-button size="small" @click="handleExport('txt')">TXT</el-button>
          </el-button-group>
          <el-button size="small" :icon="Pencil" @click="enterEditMode">编辑</el-button>
          <el-button size="small" :icon="Copy" @click="copyResult">复制</el-button>
        </div>

        <!-- 编辑模式 -->
        <!-- 思考过程（流式/总结时显示） -->
        <div v-if="jobStore.streamingThinking" class="thinking-panel">
          <div class="thinking-head" @click="showThinking = !showThinking">
            <span class="thinking-label">AI 思考过程</span>
            <el-button size="small" text>{{ showThinking ? '收起' : '展开' }}</el-button>
          </div>
          <pre v-if="showThinking" class="thinking-body">{{ jobStore.streamingThinking }}</pre>
        </div>

        <div v-if="editMode">
          <el-input v-model="editText" type="textarea" :rows="20" />
          <div style="text-align: center; margin-top: 12px;">
            <el-button type="primary" :icon="Check" :loading="savingEdit" @click="saveEdit">保存</el-button>
            <el-button :icon="X" @click="cancelEdit">取消</el-button>
          </div>
        </div>

        <!-- Markdown / 流式输出视图 -->
        <div
          v-else-if="resultTab === 'markdown'"
          class="markdown-body"
          v-html="renderedResult"
          style="line-height: 1.8; font-size: 14px;"
        />

        <!-- 思维导图视图 -->
        <div v-else-if="resultTab === 'mindmap'" style="width: 100%; height: 520px; position: relative; background: #1a1a1a; border-radius: 8px;">
          <svg ref="mindmapRef" style="width: 100%; height: 100%; border-radius: 8px;"></svg>
        </div>
      </div>
    </el-card>

    <!-- 系统日志（已拆为 BackendLogPanel 组件） -->
    <BackendLogPanel />

    <!-- 转录预览 -->
    <el-card v-if="isPreviewState" id="preview-section" class="wb-card" shadow="never">
      <template #header>
        <div class="card-head-row">
          <span class="wb-card-title">转录预览</span>
        </div>
      </template>
      <el-input
        v-model="jobStore.previewText"
        type="textarea"
        :rows="12"
        placeholder="转录文本"
        class="input-main"
      />
      <div class="submit-row" style="margin-bottom: 0;">
        <el-button type="primary" class="submit-btn" style="min-width: 180px !important;" @click="handleConfirmPreview">
          确认继续 AI 总结
        </el-button>
        <el-button size="large" @click="handleCancelPreview">取消</el-button>
      </div>
    </el-card>

    <!-- 旧的结果展示区已合并到上方固定结果栏 -->

    <!-- 追问聊天（ChatPanel：SSE 流式 + 思考过程 + 引用源 + 编辑/重发） -->
    <ChatPanel v-if="jobStore.currentJobId" :job-id="jobStore.currentJobId" />

    <!-- Token 统计面板 -->
    <el-card v-if="tokenUsage" class="wb-card" shadow="never">
      <template #header>
        <div class="card-head-row token-head" @click="tokenPanelOpen = !tokenPanelOpen">
          <span class="wb-card-title">Token 统计</span>
          <span class="card-head-hint">
            总计 {{ ((tokenUsage.total_input || 0) + (tokenUsage.total_output || 0)).toLocaleString() }} tokens
          </span>
          <el-icon class="token-chevron" :class="{ open: tokenPanelOpen }">
            <svg viewBox="0 0 1024 1024" width="14" height="14"><path d="M884 256h-75c-5.1 0-9.9 2.5-12.9 6.6L512 654.2 227.9 262.6c-3-4.1-7.8-6.6-12.9-6.6h-75c-6.5 0-10.3 7.4-6.5 12.7l352.6 486.1c12.8 17.6 39 17.6 51.7 0l352.6-486.1c3.9-5.3.1-12.7-6.4-12.7z" fill="currentColor"/></svg>
          </el-icon>
        </div>
      </template>

      <div v-show="tokenPanelOpen">
        <el-row :gutter="16" style="margin-bottom: 12px;">
          <el-col :span="8">
            <div class="token-stat-item">
              <div class="token-stat-label">输入 Token</div>
              <div class="token-stat-value">{{ (tokenUsage.total_input || 0).toLocaleString() }}</div>
            </div>
          </el-col>
          <el-col :span="8">
            <div class="token-stat-item">
              <div class="token-stat-label">输出 Token</div>
              <div class="token-stat-value">{{ (tokenUsage.total_output || 0).toLocaleString() }}</div>
            </div>
          </el-col>
          <el-col :span="8">
            <div class="token-stat-item">
              <div class="token-stat-label">总计 Token</div>
              <div class="token-stat-value token-stat-total">
                {{ ((tokenUsage.total_input || 0) + (tokenUsage.total_output || 0)).toLocaleString() }}
              </div>
            </div>
          </el-col>
        </el-row>

        <!-- 按模型分列 -->
        <div v-if="tokenUsage.by_model && Object.keys(tokenUsage.by_model).length > 0">
          <el-divider content-position="left" style="font-size: 12px;">按模型</el-divider>
          <el-table :data="Object.entries(tokenUsage.by_model).map(([k,v]) => ({model: k, ...v}))" size="small" stripe>
            <el-table-column prop="model" label="模型" min-width="160" />
            <el-table-column prop="input_tokens" label="输入" width="100">
              <template #default="{ row }">{{ (row.input_tokens || 0).toLocaleString() }}</template>
            </el-table-column>
            <el-table-column prop="output_tokens" label="输出" width="100">
              <template #default="{ row }">{{ (row.output_tokens || 0).toLocaleString() }}</template>
            </el-table-column>
            <el-table-column label="合计" width="100">
              <template #default="{ row }">{{ ((row.input_tokens || 0) + (row.output_tokens || 0)).toLocaleString() }}</template>
            </el-table-column>
          </el-table>
        </div>

        <!-- 调用历史 -->
        <div v-if="tokenHistory.length > 0">
          <el-divider content-position="left" style="font-size: 12px;">调用历史 (次数)</el-divider>
          <el-table :data="tokenHistory" size="small" stripe max-height="250">
            <el-table-column prop="created_at" label="时间" width="140">
              <template #default="{ row }">{{ formatTokenTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column prop="model" label="模型" min-width="140" />
            <el-table-column prop="title" label="来源" min-width="120" show-overflow-tooltip />
            <el-table-column label="输入" width="80" align="right">
              <template #default="{ row }">{{ (row.input_tokens || 0).toLocaleString() }}</template>
            </el-table-column>
            <el-table-column label="输出" width="80" align="right">
              <template #default="{ row }">{{ (row.output_tokens || 0).toLocaleString() }}</template>
            </el-table-column>
            <el-table-column label="合计" width="80" align="right">
              <template #default="{ row }">
                <span style="color: #409eff; font-weight: 500;">{{ (row.total_tokens || 0).toLocaleString() }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <div v-else-if="tokenPanelOpen" style="text-align: center; padding: 12px; color: #909399; font-size: 12px;">
          暂无调用记录
        </div>
      </div>
    </el-card>

  </div>
</template>

<style scoped>
.home-workbench {
  max-width: 1040px;
  margin: 0 auto;
  padding: 20px 24px 48px;
}

.wb-card {
  margin-bottom: 16px;
  /* 透明背景 + 毛玻璃 + 圆角 + 悬停 glow 已由 App.vue 全局 .wb-card 提供 */
}
/* 通用卡片头样式已提升至 App.vue 全局（.wb-card-title/.card-head-row/.card-head-hint/.card-head-actions），
   供 HomeView 拆出的子组件共用（2026-09-12 slice-0） */

/* 输入区 */
.input-type-tabs {
  margin-bottom: 14px;
  display: flex;
  flex-wrap: wrap;
}
.input-main {
  width: 100%;
}
.input-upload :deep(.el-upload-dragger) {
  border-radius: 12px;
  border-color: var(--app-border);
  background: var(--app-progress-meter-bg, transparent);
  padding: 28px 16px;
}
.upload-icon {
  font-size: 40px;
  color: var(--app-text-secondary);
}
.upload-title {
  margin-top: 8px;
  color: var(--app-text);
  font-size: 13px;
}
.upload-tip {
  font-size: 12px;
  color: var(--app-text-secondary);
  margin-top: 6px;
}
.upload-ok {
  color: #67c23a;
  margin-left: 8px;
  font-weight: 600;
}
.upload-busy {
  color: var(--el-color-primary, #409eff);
  margin-left: 8px;
  font-weight: 600;
}
.upload-remove {
  margin-left: 6px;
  cursor: pointer;
  vertical-align: -1px;
  color: var(--app-text-secondary);
}
.upload-remove:hover {
  color: var(--el-color-danger, #f56c6c);
}

/* 批量面板样式随组件迁至 components/BatchResultPanel.vue（2026-09-12 slice-3） */

/* 高级选项卡片样式随组件迁至 components/HomeAdvancedOptions.vue（2026-09-12 slice-2）；
   .adv-field-hint 提升至 App.vue 全局（批量面板复用） */

/* 系统日志面板样式随组件迁至 components/BackendLogPanel.vue（2026-09-12 slice-1） */

/* 进度卡样式随组件迁至 components/JobProgressPanel.vue（2026-09-12 slice-4） */
.result-card {
  min-height: 200px;
}

.submit-row {
  text-align: center;
  margin: 8px 0 20px;
  display: flex;
  justify-content: center;
  gap: 12px;
  flex-wrap: wrap;
}
.submit-btn {
  min-width: 220px !important;
  height: 46px !important;
  border: none !important;
  border-radius: 12px !important;
  font-weight: 700 !important;
  letter-spacing: 0.06em;
  background: linear-gradient(135deg, var(--app-accent-1), var(--app-accent-2)) !important;
  box-shadow: 0 8px 24px rgba(var(--app-accent-1-rgb), 0.35) !important;
}
.submit-btn:hover:not(:disabled) {
  filter: brightness(1.06);
  transform: translateY(-1px);
}
.submit-btn:disabled {
  opacity: 0.75;
}

/* 日志面板基础样式（.log-panel/.log-empty/.log-time）已提升至 App.vue 全局（2026-09-12 slice-0） */
/* 任务进度日志样式随组件迁至 components/JobProgressPanel.vue（2026-09-12 slice-4） */

/* Markdown — 跟随主题 */
.markdown-body {
  color: var(--app-text);
}
.markdown-body :deep(h1) {
  font-size: 1.5em; font-weight: 650; margin: 16px 0 8px;
  color: var(--app-text-heading);
  border-bottom: 1px solid var(--app-border);
  padding-bottom: 6px;
}
.markdown-body :deep(h2) { font-size: 1.3em; font-weight: 650; margin: 14px 0 6px; color: var(--app-text-heading); }
.markdown-body :deep(h3) { font-size: 1.1em; font-weight: 600; margin: 12px 0 4px; color: var(--app-text-heading); }
.markdown-body :deep(p) { margin: 8px 0; color: var(--app-text); }
.markdown-body :deep(ul), .markdown-body :deep(ol) { padding-left: 24px; margin: 8px 0; }
.markdown-body :deep(code) {
  background: var(--app-code-bg);
  color: var(--app-code-text);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.9em;
}
.markdown-body :deep(pre) {
  background: var(--app-log-bg);
  color: var(--app-log-text);
  padding: 12px 14px;
  border-radius: 8px;
  overflow-x: auto;
  border: 1px solid var(--app-border);
}
.markdown-body :deep(blockquote) {
  border-left: 4px solid var(--app-accent-1);
  padding-left: 16px;
  color: var(--app-text-secondary);
  margin: 8px 0;
}
.markdown-body :deep(table) { border-collapse: collapse; width: 100%; margin: 8px 0; }
.markdown-body :deep(th), .markdown-body :deep(td) {
  border: 1px solid var(--app-border);
  padding: 8px 12px;
  text-align: left;
  color: var(--app-text);
}
.markdown-body :deep(th) {
  background: var(--app-table-header-bg);
  font-weight: 600;
}

/* 思考过程 */
.thinking-panel {
  margin-bottom: 12px;
  border: 1px dashed rgba(var(--app-accent-1-rgb), 0.55);
  border-radius: 10px;
  background: var(--app-thinking-bg);
}
.thinking-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 10px;
  cursor: pointer;
}
.thinking-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-thinking-text);
}
.thinking-body {
  margin: 0;
  padding: 0 10px 10px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--app-thinking-text);
  max-height: 300px;
  overflow-y: auto;
}

/* Token 统计 */
.token-head {
  cursor: pointer;
  width: 100%;
}
.token-chevron {
  margin-left: auto;
  color: var(--app-text-secondary);
  transition: transform 0.2s;
}
.token-chevron.open {
  transform: rotate(180deg);
}
.token-stat-item {
  text-align: center;
  padding: 12px;
  background: var(--app-hover-bg);
  border: 1px solid var(--app-border);
  border-radius: 10px;
}
.token-stat-label {
  font-size: 12px;
  color: var(--app-text-secondary);
  margin-bottom: 6px;
}
.token-stat-value {
  font-size: 20px;
  font-weight: 600;
  color: var(--app-text-heading);
}
.token-stat-total {
  background: linear-gradient(135deg, var(--app-accent-1), var(--app-accent-2));
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
</style>
