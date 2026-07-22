<script setup>
import { ref, onMounted, onUnmounted, onActivated, onDeactivated, computed, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { UploadFilled, Search, Edit, Check, Close, CopyDocument, QuestionFilled, Loading, ArrowDown } from '@element-plus/icons-vue'
import { useJobStore } from '../stores/jobStore'
import { useSettingsStore } from '../stores/settingsStore'
import { useModelStore } from '../stores/modelStore'
import { jobsApi, exportApi, translateApi, tokenApi, resultApi, chatApi, summaryApi } from '../utils/api'
import { ACTIVE_JOB_STATUSES } from '../utils/jobActions'
import {
  onProgress, onLog, onAiToken, onAiDone, onPreview, onAiThinking,
  continueSummary, cancelPreview, cancelJob,
} from '../utils/socket'
import { renderMarkdown } from '../utils/markdown'
import ResummarizeDialog from '../components/ResummarizeDialog.vue'
import SplitPromptPanel from '../components/SplitPromptPanel.vue'
import ChatPanel from '../components/ChatPanel.vue'

// 思维导图渲染
import { Transformer } from 'markmap-lib'
import { Markmap } from 'markmap-view'

let transformer = null

const jobStore = useJobStore()
const router = useRouter()
const route = useRoute()
const settings = useSettingsStore()
const modelStore = useModelStore()

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
  } catch (e) { /* ignore */ }
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
  } catch (e) { /* ignore */ }
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
watch(() => settings.selectedDimensions.word_count, (wordCount) => {
  if (!modelTouched.value) {
    settings.selectedModel = settings.recommendModel(wordCount)
  }
}, { immediate: true })

// ★ Socket 监听解绑函数集合（onUnmounted 时逐个调用，只移除本组件注册的 handler）
let socketUnsubs = []

// ── 系统日志 ──
const backendLogs = ref([])
const autoScroll = ref(true)
let logTimer = null
let lastTotal = 0

async function fetchLogs() {
  try {
    const res = await jobsApi.getLogs(200)
    if (res.total < lastTotal) {
      backendLogs.value = []
      lastTotal = 0
    }
    const delta = res.total - lastTotal
    if (delta > 0 && delta <= 200) {
      backendLogs.value = backendLogs.value.concat(res.lines.slice(-delta))
      lastTotal = res.total
      if (backendLogs.value.length > 1000) backendLogs.value = backendLogs.value.slice(-1000)
    }
    if (autoScroll.value) nextTick(() => {
      const el = document.getElementById('log-panel')
      if (el) el.scrollTop = el.scrollHeight
    })
  } catch {}
}

async function loadAllHistory() {
  try {
    const res = await jobsApi.getLogs(2000)
    backendLogs.value = res.lines
    lastTotal = res.total
    nextTick(() => {
      const el = document.getElementById('log-panel')
      if (el) el.scrollTop = el.scrollHeight
    })
  } catch {}
}

function clearLogs() { backendLogs.value = []; lastTotal = 0 }
function scrollLogTop() {
  document.getElementById('log-panel')?.scrollTo({ top: 0, behavior: 'smooth' })
}
function refreshLogs() { fetchLogs() }

// ★ 批量任务结果与进度
const batchResult = ref(null)  // {batch_id, count, skipped, summary}

async function refreshBatchStatus() {
  if (!batchResult.value?.batch_id) return
  try {
    const s = await jobsApi.batchStatus(batchResult.value.batch_id)
    batchResult.value.summary = s
  } catch (err) {
    console.error('查询批量进度失败:', err)
  }
}

// 批量进度自动刷新定时器
let batchTimer = null
function startBatchPolling() {
  stopBatchPolling()
  batchTimer = setInterval(() => {
    refreshBatchStatus()
    // 全部完成停止轮询
    if (batchResult.value?.summary?.all_done) stopBatchPolling()
  }, 3000)
}
function stopBatchPolling() {
  if (batchTimer) { clearInterval(batchTimer); batchTimer = null }
}

// 任务状态 → 标签类型/文案
function jobStatusLabel(s) {
  return {
    pending: '排队', downloading: '下载中', transcribing: '转录中',
    preview: '待确认', summarizing: 'AI处理中',
    completed: '完成', failed: '失败', cancelled: '已取消',
  }[s] || s
}
function jobStatusTagType(s) {
  return {
    pending: 'info', downloading: 'warning', transcribing: 'warning',
    preview: 'warning', summarizing: 'warning',
    completed: 'success', failed: 'danger', cancelled: 'info',
  }[s] || 'info'
}

// 跳转到任务详情页（批量任务的子任务点击）
function viewBatchJob(jobId) {
  router.push(`/result/${jobId}`)
}

// ============ 旧总结类型筛选状态（已迁移到 DimensionSelector，保留空变量避免模板引用报错） ============
const typeSearchQuery = ref('')
const typeFilterCategory = ref('all')
const categoryTabs = []
const categoryNames = {}
const filteredSummaryTypes = computed(() => [])

// ============ 路由参数处理（rerun / restore，供 onMounted 和 onActivated 复用） ============
let _routeQueryHandled = false

function applyJobConfig(job) {
  const options = job?.options && typeof job.options === 'object' ? job.options : {}
  const sourceType = options.input_type || (job.input_url ? 'url' : job.input_text ? 'text' : 'file')
  inputType.value = sourceType
  inputUrl.value = job.input_url || ''
  inputText.value = job.input_text || ''
  uploadedFilePath.value = job.input_file || ''
  uploadedFileName.value = uploadedFilePath.value.split(/[\\/]/).pop() || ''

  if (job.model) settings.selectedModel = job.model
  if (job.summary_type) settings.selectedSummaryType = job.summary_type

  const dimensions = options.dimensions && typeof options.dimensions === 'object'
    ? { ...settings.selectedDimensions, ...options.dimensions }
    : { ...settings.selectedDimensions }
  settings.selectedDimensions = dimensions

  const restoredOptions = { ...options, dimensions }
  delete restoredOptions._retry
  delete restoredOptions.input_type
  delete restoredOptions.do_summary
  delete restoredOptions.do_translate
  delete restoredOptions.do_subtitle
  delete restoredOptions.preview_before_summary
  delete restoredOptions.output_options
  settings.advancedOptions = { ...settings.advancedOptions, ...restoredOptions, dimensions }

  settings.doSummary = options.do_summary ?? true
  settings.doTranslate = options.do_translate ?? false
  settings.doSubtitle = options.do_subtitle ?? false
  settings.previewBeforeSummary = options.preview_before_summary ?? true
  outputOptions.value = Array.isArray(options.output_options)
    ? [...options.output_options]
    : ['result_md', 'srt', 'txt']

  customPrompt.value = options.custom_prompt || ''
  settings.fullOverride = options.full_override || ''
  settings.useFullOverride = Boolean(options.full_override)
  promptOverride.value = options.edited_prompt || ''
  usePromptOverride.value = Boolean(options.edited_prompt)
}

async function handleRouteQuery() {
  const rerunJobId = route.query.rerun && route.query.rerun !== '1'
    ? route.query.rerun
    : route.query.rerun === '1' ? route.query.restore : null
  let restoreJobId = null

  if (rerunJobId) {
    try {
      const sourceJob = await jobsApi.get(rerunJobId)
      jobStore.reset()
      applyJobConfig(sourceJob)
      rerunSourceJobId.value = rerunJobId
      ElMessage.info('已载入原任务配置，可修改后重新处理')
    } catch (err) {
      rerunSourceJobId.value = null
      ElMessage.error(`加载原任务配置失败: ${err.message}`)
    }
  } else if (route.query.restore) {
    restoreJobId = route.query.restore
  }
  else if (!_routeQueryHandled && jobStore.tryRestore()) {
    restoreJobId = jobStore.currentJobId
  }

  if (restoreJobId) {
    try {
      const loadedJob = await jobStore.loadJob(restoreJobId)
      applyJobConfig(loadedJob)
      rerunSourceJobId.value = null

      const freshStatus = jobStore.jobStatus
      if (ACTIVE_JOB_STATUSES.includes(freshStatus)) {
        jobStore.isProcessing = true
        jobStore.addLog(`📋 已恢复任务 ${restoreJobId}（状态: ${freshStatus}）`)
        if (freshStatus === 'preview' && jobStore.previewText) {
          jobStore.addLog('⏸ 转录预览待确认，请在下方检查并继续')
        }
      } else {
        jobStore.isProcessing = false
      }
    } catch (err) {
      jobStore.reset()
    }
  }

  // 恢复完成后再清除 query 参数（避免 router.replace 与加载并发导致状态错乱）
  if (route.query.restore || route.query.rerun) {
    await router.replace({ path: '/', query: {} }).catch(() => {})
  }

  _routeQueryHandled = true
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

  // 恢复输入内容（sessionStorage）
  loadInputState()

  // ★ 路由参数处理（rerun / restore 检测 + 任务恢复）
  await handleRouteQuery()

  // ============ Socket 事件监听 ============
  // ★ 关键改动：preview 事件不再仅限 currentJobId，任何任务进入 preview 都自动切换跟踪
  socketUnsubs.push(onProgress((data) => {
    if (data.job_id === jobStore.currentJobId) {
      jobStore.updateProgress(data)
    }
    // 批量子任务进度变化时也刷新批量面板
    if (batchResult.value?.batch_id) {
      refreshBatchStatus()
    }
  }))
  socketUnsubs.push(onLog((data) => {
    if (data.job_id === jobStore.currentJobId) {
      jobStore.addLog(data.msg)
    }
  }))
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

  // ── 启动日志轮询 ──
  fetchLogs()
  logTimer = setInterval(fetchLogs, 3000)
})

// keep-alive: 切出页面时停止轮询，切回时恢复
onDeactivated(() => {
  stopBatchPolling()
})

onActivated(async () => {
  // keep-alive 切回时处理路由参数（rerun / restore）
  await handleRouteQuery()

  // 恢复批量进度轮询
  if (batchResult.value?.summary && !batchResult.value.summary.all_done) {
    startBatchPolling()
  }
  // 检查任务是否已完成（切页期间可能跑完了）
  if (jobStore.currentJobId && jobStore.isProcessing) {
    jobsApi.get(jobStore.currentJobId).then(job => {
      if (job.status === 'completed' && jobStore.jobStatus !== 'completed') {
        jobStore.jobStatus = 'completed'
        jobStore.isProcessing = false
        jobStore.resultText = job.result_text || jobStore.resultText
        jobStore.addLog('任务已完成')
        loadTokenUsage()
        loadTokenHistory()
      }
    }).catch(() => {})
  }
})

// 组件销毁时清理（keep-alive 不会触发，但路由从缓存中移除时会触发）
onUnmounted(() => {
  // ★ 只移除本组件注册的 socket 监听（不用全局 removeAllListeners，避免误清 keep-alive 其他组件）
  socketUnsubs.forEach(off => off())
  socketUnsubs = []
  stopBatchPolling()
  clearInterval(logTimer)
  // ★ 清理 rAF 渲染回调，避免组件销毁后更新已卸载的 ref
  if (_rafId !== null) {
    cancelAnimationFrame(_rafId)
    _rafId = null
  }
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

  // 提示词覆盖
  if (usePromptOverride.value && promptOverride.value) {
    settings.advancedOptions.edited_prompt = promptOverride.value
  }

  // ★ 完全自定义提示词库：TemplatesView 启用时，full_override 覆盖一切维度拼装
  if (settings.useFullOverride && settings.fullOverride) {
    settings.advancedOptions.full_override = settings.fullOverride
  } else {
    delete settings.advancedOptions.full_override
  }

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
      // ★ 完全自定义提示词库（同单任务处理）
      const batchOptions = { ...settings.advancedOptions, dimensions: { ...settings.selectedDimensions } }
      if (settings.useFullOverride && settings.fullOverride) {
        batchOptions.full_override = settings.fullOverride
      } else {
        delete batchOptions.full_override
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
        preview_before_summary: settings.previewBeforeSummary,
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
      // 立即拉一次进度 + 启动轮询
      refreshBatchStatus()
      startBatchPolling()
      return
    } catch (err) {
      ElMessage.error(err.message); return
    }
  }

  // 重置结果区状态
  editMode.value = false
  translationText.value = ''
  bilingualMode.value = 'parallel'
  resultTab.value = 'markdown'

  try {
    const res = await jobStore.submitJob(jobData)
    ElMessage.success(rerunSourceJobId.value ? '已创建新的重新处理任务' : '任务已提交')
    rerunSourceJobId.value = null
  } catch (err) {
    ElMessage.error(err.message)
  }
}

// ============ 文件上传 ============
const uploadedFilePath = ref('')
const uploadedFileName = ref('')

async function handleFileUpload(file) {
  uploading.value = true
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
    ElMessage.error(err.message)
  } finally {
    uploading.value = false
  }
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

// ============ 放弃当前任务（不取消后端，只清前端状态） ============
// 用于刷新恢复后用户不想继续盯这个任务的情况
async function abandonCurrentJob() {
  try {
    await ElMessageBox.confirm(
      '放弃当前任务的前端跟踪？后端任务会继续运行，你可以在历史记录中查看结果。',
      '提示',
      { type: 'warning' }
    )
    jobStore.reset()
    ElMessage.info('已放弃跟踪，任务仍在后端运行')
  } catch (err) {
    // 用户取消
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
// ★ rAF 节流渲染：每帧最多渲染一次 Markdown，避免高频 token 推送导致 O(n) 重渲染堆积
const _renderedHtml = ref('')
let _rafId = null
function _scheduleRender() {
  if (_rafId !== null) return  // 已有等待中的渲染
  _rafId = requestAnimationFrame(() => {
    _rafId = null
    _renderedHtml.value = renderMarkdown(jobStore.resultText)
  })
}
// 监听 resultText 变化，触发 rAF 节流渲染
watch(() => jobStore.resultText, () => {
  _scheduleRender()
}, { immediate: true })
// renderedResult 改为读取 rAF 渲染的 HTML
const renderedResult = computed(() => _renderedHtml.value)
const progressPct = computed(() => jobStore.progress.pct)
const isPreviewState = computed(() => jobStore.jobStatus === 'preview')
const isProcessing = computed(() => jobStore.isProcessing)

// 输出内容选择（默认全选 AI总结+字幕+转录文本）
const outputOptions = ref(['result_md', 'srt', 'txt'])
// 旧字段保留空实现（新系统用 DimensionSelector）
const selectedTypeInfo = computed(() => null)

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
  } catch { _jobFiles.value = [] }
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

// 是否有翻译内容
const translationText = ref('')
const hasTranslation = computed(() => !!translationText.value)

// ============ 截图提取 ============
const screenshotFrames = ref([])
const screenshotLoading = ref(false)
const screenshotVisible = ref(false)

async function extractScreenshots() {
  if (!jobStore.currentJobId) return
  screenshotLoading.value = true
  try {
    const res = await jobsApi.screenshots(jobStore.currentJobId, 10)
    screenshotFrames.value = res.frames || []
    screenshotVisible.value = true
    if (screenshotFrames.value.length > 0) {
      ElMessage.success(`提取了 ${screenshotFrames.value.length} 张截图`)
    } else {
      ElMessage.warning('未提取到截图，请确认视频文件存在')
    }
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    screenshotLoading.value = false
  }
}

// ============ 思维导图渲染 ============
const mindmapRef = ref(null)
let markmapInstance = null

function renderMindmap() {
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
watch(hasTranslation, (val) => {
  if (!val && resultTab.value === 'bilingual') resultTab.value = 'markdown'
})

// ============ 双语对照翻译 ============
const bilingualMode = ref('parallel')   // parallel / interleaved / translation-only
const translating = ref(false)
const targetLang = ref('zh')
const translateLangs = ref([])

// ============ 自定义选项输入（保留兼容，新系统由 DimensionSelector 管理） ============
const customPrompt = ref('')

// 同步自定义值到 advancedOptions（保留兼容入口）
function syncCustomToOptions() {
  settings.advancedOptions.custom_prompt = customPrompt.value
}

// ============ 提示词预览（新系统由 DimensionSelector 内部加载，此处保留兼容） ============
const showPromptPreview = ref(false)
const promptPreviewText = ref('')
const promptPreviewLoading = ref(false)
const promptOverride = ref('')
const usePromptOverride = ref(false)

async function fetchPromptPreview() {
  promptPreviewLoading.value = true
  try {
    // ★ 完全自定义提示词库启用时，直接展示该 prompt（不参与维度拼装）
    if (settings.useFullOverride && settings.fullOverride) {
      promptPreviewText.value = settings.fullOverride
      if (!usePromptOverride.value) {
        promptOverride.value = settings.fullOverride
      }
      promptPreviewLoading.value = false
      return
    }
    const res = await summaryApi.promptPreview({
      dimensions: { ...settings.selectedDimensions },
    })
    promptPreviewText.value = res.prompt || ''
    if (!usePromptOverride.value) {
      promptOverride.value = promptPreviewText.value
    }
  } catch {
    promptPreviewText.value = ''
  } finally {
    promptPreviewLoading.value = false
  }
}

watch(() => settings.selectedDimensions, () => {
  if (showPromptPreview.value) fetchPromptPreview()
})

// ★ 完全自定义切换时也刷新预览
watch(() => [settings.useFullOverride, settings.fullOverride], () => {
  if (showPromptPreview.value) fetchPromptPreview()
})

// 交错显示：原文段落+译文段落交替
const interleavedContent = computed(() => {
  if (!jobStore.resultText || !translationText.value) return []
  const origParagraphs = jobStore.resultText.split(/\n\n+/).filter(p => p.trim())
  const transParagraphs = translationText.value.split(/\n\n+/).filter(p => p.trim())
  const result = []
  for (let i = 0; i < Math.max(origParagraphs.length, transParagraphs.length); i++) {
    result.push({
      orig: origParagraphs[i] || '',
      trans: transParagraphs[i] || '',
    })
  }
  return result
})

async function handleTranslate() {
  if (!jobStore.resultText) { ElMessage.warning('没有可翻译的内容'); return }
  translating.value = true
  try {
    const res = await translateApi.bilingual(
      jobStore.resultText,
      targetLang.value,
      settings.selectedModel,
      jobStore.currentJobId || undefined
    )
    translationText.value = res.translation
    resultTab.value = 'bilingual'
    ElMessage.success('翻译完成')
  } catch (err) {
    ElMessage.error(`翻译失败: ${err.message}`)
  } finally {
    translating.value = false
  }
}

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
  } catch (err) {
    // 即使后端保存失败，也更新前端状态
    jobStore.resultText = editText.value
    editMode.value = false
    ElMessage.warning('前端已更新，但后端保存失败')
  } finally {
    savingEdit.value = false
  }
}

// ============ 重新总结 ============
const resummarizeDialog = ref(false)

function openResummarize() {
  resummarizeDialog.value = true
}

function onResummarizeSuccess(data) {
  // 弹窗已关闭，重置流式输出状态
  jobStore.aiTokens = []
  jobStore.resultText = ''
  jobStore.jobStatus = 'summarizing'
  jobStore.isProcessing = true
  // 滚动到结果栏
  nextTick(() => {
    document.getElementById('result-section')?.scrollIntoView({ behavior: 'smooth' })
  })

  // ★ 修复：实际调用后端重新总结 API，否则 socket 永远不会推送 token
  const targetJobId = jobStore.currentJobId
  if (!targetJobId) {
    ElMessage.error('没有任务ID，无法重新总结')
    jobStore.jobStatus = 'failed'
    jobStore.isProcessing = false
    return
  }
  jobsApi.summarize(targetJobId, data).then(() => {
    // 结果通过 socket 流式推送，无需在此处理 result_text
    console.log('[重新总结] API 调用成功，等待 socket 流式输出')
  }).catch(err => {
    ElMessage.error(`重新总结失败: ${err?.message || err}`)
    jobStore.jobStatus = 'failed'
    jobStore.isProcessing = false
  })
}

function onResummarizeCompleted(res) {
  // ★ 后端通过 socket 流式推送 + done 信号 + emit_ai_token(done=True) 已经处理了完成状态
  // 这里仅刷新 token 统计（HTTP 请求成功返回后调用）
  loadTokenUsage()
  loadTokenHistory()
}

// ============ 结果内搜索 ============
const searchQuery = ref('')
const searchCount = ref(0)
const searchCurrent = ref(0)
const resultBodyRef = ref(null)

function doSearch() {
  if (!resultBodyRef.value || !searchQuery.value.trim()) {
    searchCount.value = 0
    return
  }
  const container = resultBodyRef.value
  // 移除旧高亮
  container.querySelectorAll('mark.search-hl').forEach(m => {
    m.replaceWith(document.createTextNode(m.textContent))
  })
  container.normalize()

  const q = searchQuery.value.trim()
  if (!q) { searchCount.value = 0; return }

  // 遍历文本节点并高亮
  let count = 0
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT)
  const nodes = []
  while (walker.nextNode()) nodes.push(walker.currentNode)

  nodes.forEach(node => {
    const text = node.textContent
    const idx = text.toLowerCase().indexOf(q.toLowerCase())
    if (idx === -1) return
    count++
    const before = document.createTextNode(text.slice(0, idx))
    const mark = document.createElement('mark')
    mark.className = 'search-hl'
    mark.textContent = text.slice(idx, idx + q.length)
    const after = document.createTextNode(text.slice(idx + q.length))
    node.parentNode.replaceChild(after, node)
    node.parentNode.insertBefore(mark, after)
    node.parentNode.insertBefore(before, mark)
  })
  searchCount.value = count
  searchCurrent.value = count > 0 ? 1 : 0
  if (count > 0) {
    const first = container.querySelector('mark.search-hl')
    if (first) first.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
}

function searchNav(dir) {
  if (!resultBodyRef.value || searchCount.value === 0) return
  const marks = resultBodyRef.value.querySelectorAll('mark.search-hl')
  marks.forEach(m => m.classList.remove('search-hl--active'))
  searchCurrent.value = ((searchCurrent.value - 1 + dir + searchCount.value) % searchCount.value) + 1
  const target = marks[searchCurrent.value - 1]
  if (target) {
    target.classList.add('search-hl--active')
    target.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
}

function clearSearch() {
  searchQuery.value = ''
  searchCount.value = 0
  if (resultBodyRef.value) {
    resultBodyRef.value.querySelectorAll('mark.search-hl').forEach(m => {
      m.replaceWith(document.createTextNode(m.textContent))
    })
    resultBodyRef.value.normalize()
  }
}

// 复制结果
async function copyResult() {
  const text = jobStore.resultText
  if (!text) { ElMessage.warning('没有可复制的内容'); return }
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(`已复制 ${text.length} 字符到剪贴板`)
  } catch {
    // fallback 兼容旧浏览器/非 HTTPS
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'; ta.style.left = '-9999px'
    document.body.appendChild(ta)
    ta.select()
    try { document.execCommand('copy'); ElMessage.success(`已复制 ${text.length} 字符到剪贴板`) }
    catch { ElMessage.error('复制失败，请手动选择文本') }
    finally { document.body.removeChild(ta) }
  }
}

// ============ Token 统计 ============
const tokenUsage = ref(null)
const tokenHistory = ref([])
const tokenPanelOpen = ref(false)

async function loadTokenUsage() {
  try {
    const res = await tokenApi.usage()
    tokenUsage.value = res
  } catch {
    // 静默失败
  }
}

async function loadTokenHistory() {
  try {
    const res = await tokenApi.history(30)
    tokenHistory.value = res.records || []
  } catch {
    // 静默失败
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

// keep-alive: 切出时停止轮询
onDeactivated(() => {
  stopBatchPolling()
})
</script>

<template>
  <div style="max-width: 1000px; margin: 0 auto; padding: 24px;">

    <!-- 输入区 -->
    <el-card shadow="never" style="margin-bottom: 16px;">
      <template #header>
        <span style="font-weight: 600;">输入</span>
      </template>

      <el-radio-group v-model="inputType" style="margin-bottom: 16px;">
        <el-radio-button value="url">URL粘贴</el-radio-button>
        <el-radio-button value="batch">批量URL</el-radio-button>
        <el-radio-button value="file">文件上传</el-radio-button>
        <el-radio-button value="text">文本粘贴</el-radio-button>
      </el-radio-group>

      <!-- URL 输入 -->
      <el-input
        v-if="inputType === 'url'"
        v-model="inputUrl"
        placeholder="粘贴视频URL (YouTube/Bilibili/抖音等)"
        size="large"
        clearable
      >
        <template #prepend>URL</template>
      </el-input>

      <!-- 文件上传 -->
      <el-upload
        v-else-if="inputType === 'file'"
        drag
        :auto-upload="false"
        :on-change="handleFileUpload"
        :limit="1"
        accept="audio/*,video/*,.srt,.vtt,.txt,.md"
      >
        <el-icon style="font-size: 48px; color: #c0c4cc;"><UploadFilled /></el-icon>
        <div style="margin-top: 8px;">拖拽文件到此处，或点击上传</div>
        <template #tip>
          <div style="font-size: 12px; color: #909399;">
            支持音频、视频、字幕、TXT 和 Markdown 文件
            <span v-if="uploadedFileName" style="color: #67c23a; margin-left: 8px;">
              ✓ 已上传: {{ uploadedFileName }}
            </span>
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
      />

      <!-- 批量URL -->
      <el-input
        v-else-if="inputType === 'batch'"
        v-model="batchUrls"
        type="textarea"
        :rows="6"
        resize="vertical"
        placeholder="每行一个URL（自动去重去空行）"
      />

      <!-- 批量任务进度面板 -->
      <div v-if="inputType === 'batch' && batchResult" class="batch-panel" style="margin-top: 12px; padding: 12px; border-radius: 6px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
          <span style="font-weight: 600; font-size: 13px;">
            批量任务 #{{ batchResult.batch_id }}
            <span v-if="batchResult.summary" style="color: #909399; font-weight: normal; margin-left: 8px;">
              共 {{ batchResult.summary.total }} 个
            </span>
          </span>
          <el-button size="small" text @click="refreshBatchStatus">刷新</el-button>
        </div>
        <template v-if="batchResult.summary">
          <el-progress
            :percentage="Math.round((batchResult.summary.completed + batchResult.summary.failed + batchResult.summary.cancelled) / batchResult.summary.total * 100)"
            :status="batchResult.summary.all_done ? 'success' : ''"
            style="margin-bottom: 8px;"
          />
          <div style="font-size: 12px; color: #606266;">
            <el-tag size="small" type="success" style="margin-right: 6px;">完成 {{ batchResult.summary.completed }}</el-tag>
            <el-tag size="small" type="warning" style="margin-right: 6px;">进行中 {{ batchResult.summary.running }}</el-tag>
            <el-tag size="small" type="danger" style="margin-right: 6px;">失败 {{ batchResult.summary.failed }}</el-tag>
            <el-tag size="small" type="info" v-if="batchResult.summary.cancelled">取消 {{ batchResult.summary.cancelled }}</el-tag>
          </div>
          <div style="margin-top: 8px; max-height: 250px; overflow-y: auto; font-size: 12px;">
            <div
              v-for="j in batchResult.summary.jobs"
              :key="j.id"
              style="padding: 6px 8px; border-bottom: 1px dashed #ebeef5; display: flex; justify-content: space-between; align-items: center; border-radius: 4px;"
              :style="{ background: j.status === 'preview' ? '#fdf6ec' : '', cursor: j.status !== 'preview' ? 'pointer' : 'default' }"
              @click="j.status !== 'preview' && viewBatchJob(j.id)"
            >
              <span style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-right: 8px;">
                {{ j.title || j.input_url || j.id }}
              </span>
              <div style="display: flex; align-items: center; gap: 6px; flex-shrink: 0;">
                <el-tag size="small" :type="jobStatusTagType(j.status)">{{ jobStatusLabel(j.status) }}</el-tag>
                <!-- preview 状态：直接确认/取消按钮 -->
                <el-button v-if="j.status === 'preview'" size="small" type="primary" @click.stop="confirmBatchPreview(j.id)">确认</el-button>
                <el-button v-if="j.status === 'preview'" size="small" @click.stop="cancelBatchPreview(j.id)">取消</el-button>
              </div>
            </div>
          </div>
          <div style="margin-top: 8px; font-size: 11px; color: #c0c4cc;">点击子任务查看详情 | 待确认任务可直接操作</div>
        </template>
        <div v-else style="font-size: 12px; color: #909399;">加载进度中...</div>
      </div>
    </el-card>

    <!-- 总结类型选择 → 模块化提示词 8 维度选择器 + 完全自定义（左右分屏） -->
    <el-card shadow="never" style="margin-bottom: 16px;">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-weight: 600;">总结配置（模块化提示词）</span>
          <span style="color: #909399; font-size: 12px;">左：8 维度预设 · 右：完全自定义（鼠标悬停展开）</span>
        </div>
      </template>

      <SplitPromptPanel />
    </el-card>

    <!-- 高级选项 -->
    <el-card shadow="never" style="margin-bottom: 16px;">
      <template #header>
        <span style="font-weight: 600;">高级选项</span>
      </template>

      <el-form label-width="80px" size="default">
        <el-row :gutter="16">
          <el-col :span="24">
            <el-form-item label="AI模型">
              <el-select v-model="settings.selectedModel" style="width: 100%;" @change="modelTouched = true">
                <el-option
                  v-for="m in modelStore.models"
                  :key="m.key"
                  :label="m.name"
                  :value="m.key"
                />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="16" style="margin-top: 8px;">
          <el-col :span="4">
            <el-switch v-model="settings.doSummary" active-text="AI总结" />
          </el-col>
          <el-col :span="4">
            <el-switch v-model="settings.doTranslate" active-text="全文翻译" />
          </el-col>
          <el-col :span="4">
            <el-switch v-model="settings.doSubtitle" active-text="字幕翻译" />
          </el-col>
          <el-col :span="4">
            <el-switch v-model="settings.previewBeforeSummary" active-text="转录预览" />
          </el-col>
          <el-col :span="4">
            <el-switch v-model="settings.advancedOptions.speaker_diarization" active-text="说话人分离" />
          </el-col>
        </el-row>

        <el-row :gutter="16" style="margin-top: 8px;">
          <el-col :span="8">
            <el-form-item label="转录模型" label-width="80px">
              <el-select v-model="settings.advancedOptions.model_size" size="small" style="width: 100%;">
                <el-option label="tiny（最快·精度低）" value="tiny" />
                <el-option label="base（快速·中文尚可）" value="base" />
                <el-option label="small（平衡）" value="small" />
                <el-option label="medium（高质量·默认）" value="medium" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="转录语言" label-width="80px">
              <el-select v-model="settings.advancedOptions.transcribe_language" size="small" style="width: 100%;">
                <el-option label="中文（默认）" value="zh" />
                <el-option label="英语" value="en" />
                <el-option label="日语" value="ja" />
                <el-option label="韩语" value="ko" />
                <el-option label="法语" value="fr" />
                <el-option label="德语" value="de" />
                <el-option label="自动检测（不推荐，中文可能误判为英文）" :value="null" />
              </el-select>
              <div style="font-size: 11px; color: #909399; margin-top: 2px;">默认中文，英文/外文视频请手动切换</div>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="强制转录" label-width="80px">
              <el-switch v-model="settings.advancedOptions.force_transcribe" active-text="跳过B站字幕" />
              <div style="font-size: 11px; color: #909399; margin-top: 2px;">开启后用Whisper转录（准确度高，更慢），仅对B站视频生效</div>
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="16" style="margin-top: 8px;">
          <el-col :span="8">
            <el-form-item label="AI校正" label-width="80px">
              <el-switch v-model="settings.advancedOptions.ai_correct" active-text="校正转录文本" />
              <div style="font-size: 11px; color: #909399; margin-top: 2px;">转录后用云端模型校正同音错字/专有名词/标点（消耗额外token）</div>
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left">输出内容选择</el-divider>
        <el-checkbox-group v-model="outputOptions" size="small">
          <el-checkbox value="result_md">AI总结 (.md)</el-checkbox>
          <el-checkbox value="srt">字幕文件 (.srt)</el-checkbox>
          <el-checkbox value="txt">转录文本 (.txt)</el-checkbox>
          <el-checkbox value="audio">纯音频 (.m4a)</el-checkbox>
          <el-checkbox value="video">视频文件 (.mp4)</el-checkbox>
        </el-checkbox-group>
      </el-form>
    </el-card>

    <!-- 提示词预览 -->
    <el-card v-if="showPromptPreview" shadow="never" style="margin-bottom: 16px; border-color: #b3d8ff;">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-weight: 600;">
            <el-icon style="margin-right: 4px; vertical-align: middle;"><Edit /></el-icon>
            提示词预览
          </span>
          <div style="display: flex; align-items: center; gap: 8px;">
            <el-tooltip content="开启后可手动修改实际发送给AI的提示词" placement="top">
              <el-checkbox v-model="usePromptOverride" value="override" size="small">覆盖提示词</el-checkbox>
            </el-tooltip>
            <el-button size="small" text @click="showPromptPreview = false">收起</el-button>
          </div>
        </div>
      </template>
      <el-input
        :model-value="usePromptOverride ? promptOverride : promptPreviewText"
        @update:model-value="val => promptOverride = val"
        type="textarea"
        :rows="10"
        :placeholder="promptPreviewLoading ? '加载中...' : '点击下方「显示提示词」查看'"
        :readonly="!usePromptOverride"
        style="font-family: 'Consolas', monospace; font-size: 13px; line-height: 1.6;"
      />
      <div style="margin-top: 6px; font-size: 11px; color: #909399;">
        <template v-if="usePromptOverride">已开启覆盖模式：修改上方内容将直接影响AI收到的提示词</template>
        <template v-else>只读模式：此处展示组装后的完整提示词，勾选「覆盖提示词」后可编辑</template>
      </div>
    </el-card>

    <!-- 显示提示词按钮（统一放在总结类型板块最底部） -->
    <div style="text-align: center; margin-bottom: 16px;">
      <el-button v-if="!showPromptPreview" size="default" @click="showPromptPreview = true; fetchPromptPreview()">
        <el-icon style="margin-right: 4px;"><Edit /></el-icon>显示提示词
      </el-button>
    </div>

    <!-- 提交按钮 -->
    <div style="text-align: center; margin-bottom: 16px;">
      <el-button
        type="primary"
        size="large"
        :loading="isProcessing"
        :disabled="isProcessing"
        @click="handleSubmit"
        style="min-width: 200px;"
      >{{ isProcessing ? '处理中...' : '开始处理' }}</el-button>
      <el-button v-if="isProcessing" type="danger" size="large" @click="handleCancelJob">取消</el-button>
    </div>

    <!-- 固定结果栏（常驻显示，放在开始处理按钮与日志区域之间） -->
    <el-card id="result-section" shadow="never" style="margin-bottom: 16px; min-height: 200px;">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-weight: 600;">结果</span>
          <div style="display: flex; gap: 8px; align-items: center;">
            <!-- 流式输出状态指示 -->
            <el-tag v-if="isProcessing && jobStore.jobStatus === 'summarizing'" type="warning" size="small" effect="plain">
              AI 生成中...
            </el-tag>
            <!-- 重新总结按钮 -->
            <el-button v-if="jobStore.resultText && !editMode" size="small" type="warning" @click="openResummarize">
              重新总结
            </el-button>
          </div>
        </div>
      </template>

      <!-- 无结果时的空状态 -->
      <el-empty v-if="!jobStore.resultText && !isProcessing" description="暂无结果，提交任务后在此展示" :image-size="60" />

      <!-- 处理中但还没开始AI输出 -->
      <div v-else-if="isProcessing && !jobStore.resultText && jobStore.aiTokens.length === 0" style="text-align: center; padding: 40px; color: #909399;">
        <el-icon class="is-loading" style="font-size: 24px;"><Loading /></el-icon>
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
          <el-button size="small" :icon="Edit" @click="enterEditMode">编辑</el-button>
          <el-button size="small" :icon="CopyDocument" @click="copyResult">复制</el-button>
        </div>

        <!-- 编辑模式 -->
        <!-- 思考过程（流式/总结时显示） -->
        <div v-if="jobStore.streamingThinking" style="margin-bottom: 12px; border: 1px dashed #6366f1; border-radius: 8px; background: rgba(99,102,241,0.06);">
          <div style="display: flex; justify-content: space-between; align-items: center; padding: 6px 10px; cursor: pointer;" @click="showThinking = !showThinking">
            <span style="font-size: 13px; color: #a5b4fc;">AI 思考过程</span>
            <el-button size="small" text>{{ showThinking ? '收起' : '展开' }}</el-button>
          </div>
          <pre v-if="showThinking" style="margin: 0; padding: 0 10px 10px; white-space: pre-wrap; word-break: break-word; font-size: 12.5px; line-height: 1.55; color: #c7d2fe; max-height: 300px; overflow-y: auto;">{{ jobStore.streamingThinking }}</pre>
        </div>

        <div v-if="editMode">
          <el-input v-model="editText" type="textarea" :rows="20" />
          <div style="text-align: center; margin-top: 12px;">
            <el-button type="primary" :icon="Check" :loading="savingEdit" @click="saveEdit">保存</el-button>
            <el-button :icon="Close" @click="cancelEdit">取消</el-button>
          </div>
        </div>

        <!-- Markdown / 流式输出视图 -->
        <div
          v-else-if="resultTab === 'markdown'"
          ref="resultBodyRef"
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

    <!-- 系统日志（常驻显示） -->
    <el-card shadow="never" style="margin-bottom: 16px;">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-weight: 600;">系统日志</span>
          <div style="display: flex; gap: 10px; align-items: center;">
            <el-switch v-model="autoScroll" active-text="自动滚动" size="small" />
            <el-button size="small" text @click="loadAllHistory">全部</el-button>
            <el-button size="small" text @click="clearLogs">清空</el-button>
            <el-button size="small" text @click="refreshLogs">刷新</el-button>
          </div>
        </div>
      </template>
      <div id="log-panel" class="log-panel" style="max-height: 300px; overflow-y: auto; font-family: 'Consolas','Courier New',monospace; font-size: 12px; line-height: 1.6; white-space: pre-wrap; word-break: break-all;">
        <div v-for="(line, i) in backendLogs" :key="i" :style="{ color: line.includes('[ERROR]') ? '#f56c6c' : line.includes('[WARNING]') ? '#e6a23c' : '#606266', borderLeft: line.includes('[ERROR]') ? '3px solid #f56c6c' : line.includes('[WARNING]') ? '3px solid #e6a23c' : '3px solid transparent', paddingLeft: '6px' }">{{ line }}</div>
        <div style="text-align: right; margin-top: 4px;">
          <el-button size="small" text @click="scrollLogTop">返回顶部</el-button>
        </div>
      </div>
    </el-card>

    <!-- 转录预览 -->
    <el-card v-if="isPreviewState" id="preview-section" shadow="never" style="margin-bottom: 16px;">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-weight: 600;">转录预览</span>
          <span style="font-size: 12px; color: #909399;">请检查转录文本，可编辑后继续</span>
        </div>
      </template>
      <el-input
        v-model="jobStore.previewText"
        type="textarea"
        :rows="12"
        placeholder="转录文本"
      />
      <div style="text-align: center; margin-top: 12px;">
        <el-button type="primary" @click="handleConfirmPreview">确认继续AI总结</el-button>
        <el-button @click="handleCancelPreview">取消</el-button>
      </div>
    </el-card>

    <!-- 旧的结果展示区已合并到上方固定结果栏 -->

    <!-- 追问聊天（ChatPanel：SSE 流式 + 思考过程 + 引用源 + 编辑/重发） -->
    <ChatPanel v-if="jobStore.currentJobId" :job-id="jobStore.currentJobId" />

    <!-- 截图画廊 -->
    <el-card v-if="screenshotVisible && screenshotFrames.length > 0" shadow="never" style="margin-bottom: 16px;">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-weight: 600;">视频截图</span>
          <el-button size="small" text @click="screenshotVisible = false">收起</el-button>
        </div>
      </template>
      <div style="display: flex; flex-wrap: wrap; gap: 12px;">
        <div
          v-for="(frame, i) in screenshotFrames"
          :key="i"
          style="width: 200px; border: 1px solid #e4e7ed; border-radius: 6px; overflow: hidden; cursor: pointer; transition: transform 0.15s;"
          @click="window.open(frame.path, '_blank')"
          @mouseenter="($el) => $el.style.transform = 'scale(1.03)'"
          @mouseleave="($el) => $el.style.transform = 'scale(1)'"
        >
          <img
            :src="frame.path"
            :alt="`Frame ${i + 1}`"
            style="width: 100%; height: 120px; object-fit: cover; display: block;"
            loading="lazy"
            @error="($el) => { $el.style.display = 'none'; $el.nextSibling.style.display = 'flex'; }"
          />
          <div style="display: none; width: 100%; height: 120px; align-items: center; justify-content: center; color: #666; font-size: 12px; background: #1a1a1a;">
            无法加载
          </div>
          <div style="padding: 6px 8px; font-size: 12px; color: #999; background: #1a1a1a;">
            第 {{ i + 1 }} 帧 · {{ frame.timestamp }}
          </div>
        </div>
      </div>
    </el-card>

    <!-- Token 统计面板 -->
    <el-card v-if="tokenUsage" shadow="never" style="margin-bottom: 16px;">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center; cursor: pointer;" @click="tokenPanelOpen = !tokenPanelOpen">
          <span style="font-weight: 600;">Token 统计</span>
          <div style="display: flex; align-items: center; gap: 16px;">
            <span style="font-size: 13px; color: #aaa;">
              总计：{{ (tokenUsage.total_input + tokenUsage.total_output).toLocaleString() }} tokens
            </span>
            <el-icon style="transition: transform 0.2s;" :style="tokenPanelOpen ? 'transform: rotate(180deg)' : ''">
              <svg viewBox="0 0 1024 1024" width="14" height="14"><path d="M884 256h-75c-5.1 0-9.9 2.5-12.9 6.6L512 654.2 227.9 262.6c-3-4.1-7.8-6.6-12.9-6.6h-75c-6.5 0-10.3 7.4-6.5 12.7l352.6 486.1c12.8 17.6 39 17.6 51.7 0l352.6-486.1c3.9-5.3.1-12.7-6.4-12.7z" fill="currentColor"/></svg>
            </el-icon>
          </div>
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
              <div class="token-stat-value" style="color: #409eff;">
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

    <!-- 重新总结对话框（共享组件） -->
    <ResummarizeDialog
      v-model="resummarizeDialog"
      :job-id="jobStore.currentJobId || ''"
      @success="onResummarizeSuccess"
      @completed="onResummarizeCompleted"
    />

  </div>
</template>

<style scoped>
/* 总结类型卡片 */
.type-card {
  border: 2px solid #333;
  border-radius: 8px;
  padding: 10px 12px;
  cursor: pointer;
  transition: all 0.2s ease;
  background: #1e1e1e;
  min-height: 70px;
}
.type-card:hover {
  border-color: #6366f1;
  background: #252530;
  transform: translateY(-1px);
}
.type-card--selected {
  border-color: #6366f1;
  background: #1e1e30;
  box-shadow: 0 0 0 1px #6366f1 inset;
}

/* 日志面板 */
.log-panel {
  margin-top: 12px;
  max-height: 200px;
  overflow-y: auto;
  background: #111;
  color: #c0c0c0;
  padding: 12px;
  border-radius: 8px;
  font-family: 'Cascadia Code', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.6;
}
.log-time { color: #569cd6; }

/* Markdown渲染 - 暗黑适配 */
.markdown-body :deep(h1) { font-size: 1.5em; font-weight: 600; margin: 16px 0 8px; color: #e8e8e8; }
.markdown-body :deep(h2) { font-size: 1.3em; font-weight: 600; margin: 14px 0 6px; color: #ddd; }
.markdown-body :deep(h3) { font-size: 1.1em; font-weight: 600; margin: 12px 0 4px; color: #ccc; }
.markdown-body :deep(p) { margin: 8px 0; }
.markdown-body :deep(ul), .markdown-body :deep(ol) { padding-left: 24px; margin: 8px 0; }
.markdown-body :deep(code) { background: #2a2a2a; color: #e0e0e0; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }
.markdown-body :deep(pre) { background: #111; color: #c0c0c0; padding: 12px; border-radius: 8px; overflow-x: auto; }
.markdown-body :deep(blockquote) { border-left: 4px solid #444; padding-left: 16px; color: #999; margin: 8px 0; }
.markdown-body :deep(table) { border-collapse: collapse; width: 100%; margin: 8px 0; }
.markdown-body :deep(th), .markdown-body :deep(td) { border: 1px solid #333; padding: 8px 12px; text-align: left; color: #ccc; }
.markdown-body :deep(th) { background: #252525; font-weight: 600; }

/* 搜索高亮 */
.markdown-body :deep(mark.search-hl) {
  background: #6b5e00;
  color: #ffe066;
  border-radius: 2px;
  padding: 0 2px;
}
.markdown-body :deep(mark.search-hl--active) {
  background: #ff9900;
  color: #111;
}

/* 双语并排 */
.bilingual-parallel {
  display: flex;
  gap: 0;
  align-items: flex-start;
}
.bilingual-col {
  flex: 1;
  min-width: 0;
  padding: 0 16px;
}
.bilingual-col:first-child { padding-left: 0; }
.bilingual-col:last-child { padding-right: 0; }
.bilingual-col-header {
  font-weight: 600;
  font-size: 13px;
  color: #6366f1;
  margin-bottom: 8px;
  padding-bottom: 4px;
  border-bottom: 1px solid #333;
}
.bilingual-divider {
  width: 1px;
  background: #333;
  align-self: stretch;
  margin: 0 4px;
}

/* 交错模式 */
.interleaved-pair {
  margin-bottom: 16px;
  border-radius: 6px;
  overflow: hidden;
}
.interleaved-orig {
  background: #1e1e1e;
  padding: 10px 14px;
  font-size: 13px;
  line-height: 1.7;
  color: #ccc;
}
.interleaved-trans {
  background: #1a1a28;
  padding: 10px 14px;
  font-size: 13px;
  line-height: 1.7;
  color: #ddd;
  border-top: 1px dashed #333;
}

/* Token 统计 */
.token-stat-item {
  text-align: center;
  padding: 12px;
  background: #1e1e1e;
  border-radius: 8px;
}
.token-stat-label {
  font-size: 12px;
  color: #888;
  margin-bottom: 6px;
}
.token-stat-value {
  font-size: 20px;
  font-weight: 600;
  color: #e0e0e0;
}
</style>
