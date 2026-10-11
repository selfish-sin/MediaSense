<script setup>
import { ref, onMounted, onUnmounted, onActivated, computed, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useJobStore } from '../stores/jobStore'
import { useSettingsStore } from '../stores/settingsStore'
import { useModelStore } from '../stores/modelStore'
import { jobsApi } from '../utils/api'
import socket, {
  onAiToken, onAiDone, onPreview, onAiThinking,
  continueSummary, cancelPreview, cancelJob,
} from '../utils/socket'
import { useBatchProgress } from '../composables/useBatchProgress'
import { isTerminalStatus } from '../utils/batchProgress'
import { useJobRestore } from '../composables/useJobRestore'
import { createFailureNotifier } from '../utils/errorNotice'
import { isSubmitShortcut } from '../utils/shortcut'
import BackendLogPanel from '../components/BackendLogPanel.vue'
import HomeAdvancedOptions from '../components/HomeAdvancedOptions.vue'
import HomeInputPanel from '../components/HomeInputPanel.vue'
import HomeResultPanel from '../components/HomeResultPanel.vue'
import HomeTokenPanel from '../components/HomeTokenPanel.vue'
import JobProgressPanel from '../components/JobProgressPanel.vue'
import SplitPromptPanel from '../components/SplitPromptPanel.vue'
import ChatPanel from '../components/ChatPanel.vue'

const jobStore = useJobStore()
const router = useRouter()
const route = useRoute()
const settings = useSettingsStore()
const modelStore = useModelStore()

// 后台静默拉取失败提示（连续 3 次才提示一次，成功即重置，避免刷屏）
const statusCheckNotifier = createFailureNotifier('任务状态检查失败，请确认后端服务正常')

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
// 重新处理的来源任务，仅用于提示和配置回填，不代表当前运行任务
const rerunSourceJobId = ref(null)

// 输入内容变化时持久化
watch([inputType, inputUrl, inputText, batchUrls], () => saveInputState(), { deep: true })

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

/** 批量提交后滚到批量进度面板（大进度条 + 当前子任务实时进度都在那里） */
function scrollToBatchProgress() {
  nextTick(() => {
    document.getElementById('batch-progress-section')?.scrollIntoView({
      behavior: 'smooth',
      block: 'start',
    })
  })
}

// 批量任务进行期间，进度统一由 BatchResultPanel（大进度条 + 当前子任务详情）承载，
// 单任务进度卡隐藏，避免同一时刻两处「任务进度」互相干扰
const showSingleProgress = computed(() => !(inputType.value === 'batch' && batchResult.value))

// ============ 组件挂载 ============
onMounted(async () => {
    await Promise.all([
    settings.init(),
    modelStore.loadModels(),
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

  // 全局快捷键：Ctrl/Cmd + Enter 直接提交（不用滚到底部提交按钮）
  window.addEventListener('keydown', handleShortcutKeydown)

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
      // ★ 来源信息（transcript_meta/ai_call_chain）只在任务详情里有：终态后补拉一次，
      //   流式任务不走 loadJob（不扰动结果区状态），只刷新这两个展示字段
      jobStore.refreshMeta(data.job_id)
      if (data.success) {
        _realTimeComplete = true       // ★ 标记实时完成
        jobStore.jobStatus = 'completed'
        jobStore.isProcessing = false
        // ★ 仅生成字幕等无流式输出的任务没有 ai_token 渐进填充，done 时补拉任务
        //   （result_text=转录文本，结果区/下载素材才有内容；总结任务已有流式文本则跳过）
        if (!jobStore.resultText) {
          jobStore.loadJob(data.job_id)
        }
        jobStore.addLog('AI处理完成')
        tokenPanelRef.value?.refresh()  // 刷新Token统计
      } else {
        // ★ 失败必须"可见"：写日志 + 弹窗 + 落到失败态（此前只有日志行，
        //   结果区又命中"暂无结果"空态，用户表现为"只转圈、没提示"）
        jobStore.addLog(`处理失败: ${data.error || '未知原因'}`)
        jobStore.setFailed(data.error)
        ElMessage.error(`任务失败: ${data.error || '未知原因'}`, { duration: 8000 })
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
  // ★ 必须同时对账 failed：此前只认 completed，任务在切走期间失败时
  //   isProcessing 永远停在 true → 永久转圈且无任何提示（静默失败的一种形态）
  if (jobStore.currentJobId && jobStore.isProcessing) {
    jobsApi.get(jobStore.currentJobId).then(job => {
      statusCheckNotifier.ok()
      if (job.status === 'completed' && jobStore.jobStatus !== 'completed') {
        jobStore.jobStatus = 'completed'
        jobStore.isProcessing = false
        jobStore.resultText = job.result_text || jobStore.resultText
        jobStore.addLog('任务已完成')
        tokenPanelRef.value?.refresh()
      } else if (job.status === 'failed' && jobStore.jobStatus !== 'failed') {
        jobStore.addLog(`任务失败: ${job.error || '未知原因'}`)
        jobStore.setFailed(job.error)
        ElMessage.error(`任务失败: ${job.error || '未知原因'}`, { duration: 8000 })
      }
    }).catch(() => statusCheckNotifier.fail())
  }
})

// 组件销毁时清理（keep-alive 不会触发，但路由从缓存中移除时会触发）
onUnmounted(() => {
  // ★ 只移除本组件注册的 socket 监听（不用全局 removeAllListeners，避免误清 keep-alive 其他组件）
  socketUnsubs.forEach(off => off())
  socketUnsubs = []
  window.removeEventListener('keydown', handleShortcutKeydown)
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
// 提交请求进行中（仅用于按钮 loading；不再用 isProcessing 禁用按钮——
// 任务进行中也要能继续提交，入队后逐个处理）
const submitting = ref(false)

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
      output_options: [...settings.outputOptions],
      // ★ 输出素材「视频」接通既有后端选项：勾选才真实下载视频原件（默认仅音频）
      download_video: settings.outputOptions.includes('video'),
      ...(mergedCustom ? { custom_prompt: mergedCustom } : {}),
    },
    do_summary: settings.doSummary,
    force_new: inputType.value === 'url',
    preview_before_summary: settings.previewBeforeSummary,
    output_options: [...settings.outputOptions],  // 输出内容选择（结果页过滤，restore 回填用）
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
      // ★ 输出素材与单任务对齐：结果过滤字段 + 视频→download_video（既有后端选项）
      batchOptions.output_options = [...settings.outputOptions]
      batchOptions.download_video = settings.outputOptions.includes('video')
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
        // ★ 批量固定不做转录预览确认（后端 batch_submit 同样强制忽略），流程统一直通总结
      })
      batchResult.value = {
        batch_id: res.batch_id,
        count: res.count,
        skipped: res.skipped || 0, // 语义 = 无效行数（批量永不去重跳过）
        invalidLines: res.invalid_lines || [],
        summary: null,
      }
      // 非 URL 行不静默吞：行号原样告警（后端只清洗不去重，提交几行跑几行）
      if (res.invalid_lines && res.invalid_lines.length > 0) {
        const badInfo = res.invalid_lines.map(i => `第${i.line}行「${String(i.text).slice(0, 30)}」`).join('、')
        ElMessage.warning({
          message: `${res.invalid_lines.length} 行不是有效链接，未提交: ${badInfo}`,
          duration: 5000,
        })
      }
      ElMessage.success(`已提交 ${res.count} 个批量任务，将按顺序逐个执行`)
      // 立即拉一次进度；后续由 batch_progress 推送实时更新（断线时自动降级轮询）
      refreshBatchStatus()
      syncWithSocket(socket.connected)
      scrollToBatchProgress()
      return
    } catch (err) {
      ElMessage.error(err.message); return
    }
  }

  // ★ 串行队列：任务进行中再提交 = 入队（后端按提交顺序逐个处理）
  //   入队不能重置当前任务的跟踪状态与结果区，否则用户正在看的进度/结果被冲掉
  const queueMode = isProcessing.value && !!jobStore.currentJobId

  // 重置结果区状态（仅空闲提交时；editMode/resultTab 由结果组件自持，经 expose reset 复位）
  if (!queueMode) resultPanelRef.value?.reset()

  submitting.value = true
  try {
    await jobStore.submitJob(jobData, { queueMode })
    ElMessage.success(
      queueMode
        ? '已加入队列，将按顺序依次处理'
        : (rerunSourceJobId.value ? '已创建新的重新处理任务' : '任务已提交')
    )
    rerunSourceJobId.value = null
    progressPanelRef.value?.refreshQueue()
    if (!queueMode) scrollToJobProgress()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    submitting.value = false
  }
}

// ============ 快捷键：Ctrl/Cmd + Enter 直接提交 ============
// 目的：在顶部输入区粘完链接即可开始，不必滚到底部提交按钮（与按钮同一入口 handleSubmit）。
// 两道守卫：
//  - 路由守卫：HomeView 被 keep-alive 缓存（App.vue include=['HomeView']），window 监听切页后仍存活，
//    只在 home 路由响应，避免历史/模型页误提交；
//  - 追问框守卫：ChatPanel 输入区内 Ctrl+Enter 语义属于聊天，不劫持以免误提交新任务。
function handleShortcutKeydown(e) {
  if (route.name !== 'home') return
  if (!isSubmitShortcut(e)) return
  if (e.target instanceof HTMLElement && e.target.closest('.chat-input-area')) return
  if (submitting.value) return
  e.preventDefault()
  handleSubmit()
}

// ============ 文件上传 ============
// 交互逻辑（进行中状态/校验/请求）已随输入区迁至 components/HomeInputPanel.vue（2026-09-12 slice-A）；
// 父组件仅保留路径/文件名状态（输入区经 v-model 写回，handleSubmit/useJobRestore 读取）
const uploadedFilePath = ref('')
const uploadedFileName = ref('')

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

// ★ 批量子任务取消（2026-09-13 用户反馈：批量面板缺取消按钮）。
//   走后端 POST /jobs/{id}/cancel：request_cancel 打断运行中任务 + 终态保护防覆盖；
//   未启动的 pending 任务标记取消后，编排器跑到它时 process_job 顶部的取消检查直接跳过。
async function cancelBatchJob(jobId) {
  try {
    await jobsApi.cancel(jobId)
    ElMessage.info('已取消该任务')
  } catch (err) {
    ElMessage.error('取消失败: ' + (err?.message || err))
  }
  setTimeout(() => refreshBatchStatus(), 800)
}

// 取消剩余：批量内全部未终态子任务（含排队中与运行中）一次性取消
async function cancelBatchRemaining() {
  const targets = (batchResult.value?.summary?.jobs || [])
    .filter(j => !isTerminalStatus(j.status))
    .map(j => j.id)
  if (!targets.length) return
  try {
    await ElMessageBox.confirm(
      `确定取消剩余 ${targets.length} 个任务？正在运行的任务也会中断。`,
      '取消批量任务',
      { type: 'warning', confirmButtonText: '取消这些任务', cancelButtonText: '再想想' }
    )
  } catch {
    return
  }
  try {
    const res = await jobsApi.batchCancel(targets)
    ElMessage.success(res.message || `已取消 ${res.cancelled_count} 个任务`)
  } catch (err) {
    ElMessage.error('批量取消失败: ' + (err?.message || err))
  }
  setTimeout(() => refreshBatchStatus(), 800)
}

// ============ 取消任务 ============
function handleCancelJob() {
  if (jobStore.currentJobId) {
    cancelJob(jobStore.currentJobId)
    jobStore.reset()
    ElMessage.info('任务已取消')
  }
}

// ============ 计算属性 ============
const isPreviewState = computed(() => jobStore.jobStatus === 'preview')
const isProcessing = computed(() => jobStore.isProcessing)

// 输出素材选择已收进 settingsStore.outputOptions（v4：真实过滤结果页下载素材 + video→download_video）

// 渲染节流（useRafMarkdown）/文件列表/结果 Tab/思维导图/编辑/导出/重总结/复制：
// 已随结果区迁至 components/HomeResultPanel.vue（2026-09-12 slice-B）

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
  customPrompt, promptOverride, usePromptOverride,
  rerunSourceJobId,
  settings, jobStore, route, router,
  scrollToJobProgress,
})

// ============ 结果在线编辑/重新总结/复制：已随结果区迁至 components/HomeResultPanel.vue（2026-09-12 slice-B） ============

// 结果卡模板引用：提交新任务时复位其内部 editMode/resultTab（组件 expose reset）
const resultPanelRef = ref(null)

// ============ Token 统计：已拆为 components/HomeTokenPanel.vue（数据加载组件内自持） ============
const tokenPanelRef = ref(null)

/* 追问聊天逻辑已抽离到 ChatPanel 组件，统一支持 SSE 流式/思考过程/引用源/编辑重发 */
</script>

<template>
  <div class="home-workbench">

    <!-- 输入区（已拆为 HomeInputPanel 组件，2026-09-12 slice-A） -->
    <HomeInputPanel
      v-model:input-type="inputType"
      v-model:input-url="inputUrl"
      v-model:input-text="inputText"
      v-model:batch-urls="batchUrls"
      v-model:uploaded-file-path="uploadedFilePath"
      v-model:uploaded-file-name="uploadedFileName"
      :batch-result="batchResult"
      @refresh="refreshBatchStatus"
      @view-job="viewBatchJob"
      @confirm-preview="confirmBatchPreview"
      @cancel-preview="cancelBatchPreview"
      @cancel-job="cancelBatchJob"
      @cancel-batch="cancelBatchRemaining"
      @submit="handleSubmit"
    />

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
    <HomeAdvancedOptions @touch-model="modelTouched = true" />

    <!-- 提交按钮 -->
    <div class="submit-row">
      <!-- 按钮文案随任务目标联动：仅生成字幕模式下明示"生成字幕"，按下前最后确认一次 -->
      <!-- 任务进行中不再禁用：继续提交 = 加入队列（串行逐个处理） -->
      <el-button
        class="submit-btn"
        type="primary"
        size="large"
        :loading="submitting"
        @click="handleSubmit"
      >{{ isProcessing ? '加入队列' : (settings.jobGoal === 'subtitle_only' ? '生成字幕' : '开始处理') }}</el-button>
      <el-button v-if="isProcessing" type="danger" size="large" plain @click="handleCancelJob">取消</el-button>
    </div>

    <!-- 任务进度（已拆为 JobProgressPanel 组件）；批量时由 BatchResultPanel 承载，避免重复 -->
    <JobProgressPanel v-if="showSingleProgress" ref="progressPanelRef" />

    <!-- 固定结果栏（已拆为 HomeResultPanel 组件，2026-09-12 slice-B） -->
    <HomeResultPanel ref="resultPanelRef" />

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

    <!-- Token 统计面板（已拆为 HomeTokenPanel 组件） -->
    <HomeTokenPanel ref="tokenPanelRef" />

  </div>
</template>

<style scoped>
/* 工作台刻意比列表页窄（表单两列更易读），但左右/上下内边距与 .page-container 对齐 */
.home-workbench {
  max-width: 1040px;
  margin: 0 auto;
  padding: 24px 24px 48px;
}

.wb-card {
  margin-bottom: 16px;
  /* 透明背景 + 毛玻璃 + 圆角 + 悬停 glow 已由 App.vue 全局 .wb-card 提供 */
}
/* 通用卡片头样式已提升至 App.vue 全局（.wb-card-title/.card-head-row/.card-head-hint/.card-head-actions），
   供 HomeView 拆出的子组件共用（2026-09-12 slice-0） */

/* 输入区样式随组件迁至 components/HomeInputPanel.vue（2026-09-12 slice-A）；
   .input-main 提升至 App.vue 全局（转录预览卡复用） */

/* 批量面板样式随组件迁至 components/BatchResultPanel.vue（2026-09-12 slice-3） */

/* 高级选项卡片样式随组件迁至 components/HomeAdvancedOptions.vue（2026-09-12 slice-2）；
   .adv-field-hint 提升至 App.vue 全局（批量面板复用） */

/* 系统日志面板样式随组件迁至 components/BackendLogPanel.vue（2026-09-12 slice-1） */

/* 进度卡样式随组件迁至 components/JobProgressPanel.vue（2026-09-12 slice-4） */
/* 结果卡样式（.result-card）随组件迁至 components/HomeResultPanel.vue（2026-09-12 slice-B） */

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

/* 结果卡样式（.markdown-body/.thinking-*）随组件迁至 components/HomeResultPanel.vue（2026-09-12 slice-B） */

/* Token 统计样式随组件迁至 components/HomeTokenPanel.vue（2026-09-12 slice-5） */
</style>
