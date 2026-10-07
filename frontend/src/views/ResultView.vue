<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Search, Loader2, ExternalLink, FileText, Cpu, RefreshCcw } from 'lucide-vue-next'
import { jobsApi, exportApi } from '../utils/api'
import { useModelStore } from '../stores/modelStore'
import { useRafMarkdown } from '../composables/useMarkdownRender'
import { copyText } from '../utils/clipboard'
import { createFailureNotifier, isDialogCancel } from '../utils/errorNotice'
import { buildRerunRoute, buildRestoreRoute, getJobActionModel, buildSamePromptPayload } from '../utils/jobActions'
import { jobStatusLabel, jobStatusTagType } from '../utils/jobStatus'
import ResummarizeControl from '../components/ResummarizeControl.vue'
import AiCallChain from '../components/AiCallChain.vue'
import TranscriptProvenance from '../components/TranscriptProvenance.vue'
import ChatPanel from '../components/ChatPanel.vue'
import {
  onAiToken, onAiDone, onAiThinking,
} from '../utils/socket'

const route = useRoute()
const router = useRouter()
const modelStore = useModelStore()

// 版本历史拉取失败提示（连续 3 次才提示一次）
const summaryHistoryNotifier = createFailureNotifier('总结版本历史加载失败')

const job = ref(null)
const loading = ref(false)
const searchText = ref('')
const searchMatches = ref([])
const currentMatch = ref(0)
// 结果区容器引用（★ 模板 ref="resultSectionRef" 必须有同名顶层声明）
const resultSectionRef = ref(null)
// ★ Socket 监听解绑函数集合（卸载时逐个调用，只移除本组件注册的 handler）
let socketUnsubs = []

// 流式输出状态
const isStreaming = ref(false)
const streamingText = ref('')
const streamingThinking = ref('')
const showThinking = ref(true)
// ★ 总结历史版本（从后端 API 加载，持久化）
const summaryHistory = ref([])
const activeVersion = ref(0)  // 0 = 最新（job.result_text），其他 = 历史版本号
// 当前激活版本的结果文本（非当前版本时从 history 加载）
const activeVersionResult = ref(null)

// 追问聊天逻辑已抽离到 ChatPanel 组件（SSE 流式 / 思考过程 / 引用源 / 编辑重发）

// 思维导图
const mindmapLoading = ref(false)
const mindmapReady = ref(false)
const mindmapContainer = ref(null)

// ============ 转录文本 tab ============
// ★ 校正后文本(txt)与原文(txt_raw)独立展示，不再混入 result_text
//   - transcriptTab: 'transcript' 时显示转录区
//   - transcriptView: 'corrected'(校正后) / 'raw'(校正前原文) / 'clean'(纯净阅读版)
//   - hasCorrection: 是否做过 AI 校正（有 txt_raw 备份）
const transcriptText = ref('')        // 校正后转录文本
const transcriptRawText = ref('')     // 校正前原文（仅校正过才有）
const transcriptCleanText = ref('')   // 纯净阅读版（无时间戳/说话人标记）
const transcriptLoading = ref(false)
const transcriptLoaded = ref(false)
const transcriptView = ref('corrected')  // 默认看校正后

// 主 tab：summary / mindmap / transcript
const resultTab = ref('summary')

// 是否做过 AI 校正：有 txt_raw 备份文件即视为校正过
const hasCorrection = computed(() => !!(job.value?.files?.txt_raw))
// 是否有纯净文本（txt_clean，所有转录任务都有；校对开启时基于校正后文本）
const hasClean = computed(() => !!(job.value?.files?.txt_clean))
// 校正统计（后端持久化，仅校正过的任务有）
const correctionStats = computed(() => job.value?.correction_stats || null)
// AI 调用链（兜底环）：只在真的换过模型时才有内容，否则会套出一个空卡片
const showCallChain = computed(
  () => Array.isArray(job.value?.ai_call_chain) && job.value.ai_call_chain.length > 1
)
const correctionNeedReview = computed(() => {
  const s = correctionStats.value
  if (!s) return false
  return !!(s.failed_chunks || s.integrity_warnings || s.length_warnings)
})

// ★ 当前显示的结果（流式输出时显示 streamingText，否则根据 activeVersion 显示）
const displayResult = computed(() => {
  if (isStreaming.value) return streamingText.value
  if (activeVersion.value > 0 && activeVersionResult.value) {
    return activeVersionResult.value.result_text || ''
  }
  return job.value?.result_text || ''
})

// ★ 当前激活版本的总结类型（用于信息栏显示）
const activeSummaryType = computed(() => {
  if (activeVersion.value > 0 && activeVersionResult.value) {
    return activeVersionResult.value.summary_type || '-'
  }
  return job.value?.summary_type || '-'
})

const activeModel = computed(() => {
  if (activeVersion.value > 0 && activeVersionResult.value) {
    return activeVersionResult.value.model || '-'
  }
  return job.value?.model || '-'
})

// rAF 节流渲染 Markdown（重新总结流式输出逐 token 追加，避免每次追加全量重渲染）
const { html: renderedResult } = useRafMarkdown(() => displayResult.value)

async function loadJob() {
  const jobId = route.params.jobId
  if (!jobId) return
  loading.value = true
  try {
    job.value = await jobsApi.get(jobId)
    await loadSummaryHistory()
    // ★ do_summary=false 或无总结结果但有转录文本时，默认落到转录 tab
    //   避免结果页空荡荡，让用户能看到转录/校正内容
    const hasSummary = !!(job.value?.result_text)
    const hasTranscript = !!(job.value?.files?.txt)
    if (!hasSummary && hasTranscript) {
      resultTab.value = 'transcript'
      loadTranscript()
    }
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
}

async function loadSummaryHistory() {
  const jobId = route.params.jobId
  if (!jobId) return
  try {
    const res = await jobsApi.getSummaryHistory(jobId)
    summaryHistory.value = res.versions || []
    summaryHistoryNotifier.ok()
  } catch {
    summaryHistoryNotifier.fail()
    summaryHistory.value = []
  }
}

// 切换到指定版本
async function switchToVersion(version) {
  if (!version) {
    // 切回最新版（job.result_text）
    activeVersion.value = 0
    activeVersionResult.value = null
    return
  }
  if (version === job.value?.summary_version && job.value?.result_text) {
    // 就是当前最新版
    activeVersion.value = 0
    activeVersionResult.value = null
    return
  }
  activeVersion.value = version
  activeVersionResult.value = null  // 先清空，避免短暂显示旧版本数据
  // 从后端加载该版本的全文
  try {
    const res = await jobsApi.getSummaryVersion(route.params.jobId, version)
    if (res?.result_text) {
      activeVersionResult.value = res
    } else {
      // result_text 为空也表示加载失败（数据可能未保存成功）
      ElMessage.warning(`版本 v${version} 的结果为空，可能保存时出错`)
      activeVersion.value = 0
    }
  } catch (err) {
    ElMessage.error('加载版本失败: ' + err.message)
    activeVersion.value = 0
  }
}

function goBack() {
  router.back()
}

// ============ 重新总结 ============
// 相同提示词（沿用任务原始 dimensions/options），仅更换 AI 模型；跳过下载/转录直接重跑 AI 步骤
function handleResummarize(model) {
  const jobId = route.params.jobId
  if (!jobId || !job.value) return
  if (!model) {
    ElMessage.warning('请选择 AI 模型')
    return
  }
  // 重置流式输出状态
  activeVersion.value = 0
  streamingThinking.value = ''
  activeVersionResult.value = null
  isStreaming.value = true
  streamingText.value = ''

  // 滚动到结果区
  nextTick(() => {
    resultSectionRef.value?.scrollIntoView({ behavior: 'smooth' })
  })

  // 相同提示词：透传任务原始 dimensions/options，只改 model
  const payload = buildSamePromptPayload(job.value, model)

  // 异步提交重新总结请求（整包透传 model + 原始提示词配置）
  jobsApi.summarize(jobId, payload).then(res => {
    // HTTP 返回后，结果已通过 socket 流式推送完毕
    if (res?.result_text) {
      job.value.result_text = res.result_text
      if (res.summary_type) job.value.summary_type = res.summary_type
      if (res.dimensions) job.value.dimensions = res.dimensions
    }
    isStreaming.value = false
    streamingText.value = ''
    // ★ 重新加载历史版本列表
    loadSummaryHistory()
  }).catch(err => {
    const detail = err?.response?.data?.detail
    const msg = Array.isArray(detail)
      ? detail.map(d => d.msg || JSON.stringify(d)).join('; ')
      : (detail || err.message)
    ElMessage.error(`重新总结失败: ${msg}`)
    isStreaming.value = false
    streamingText.value = ''
  })
}

// ============ Socket.IO 流式输出监听 ============
onMounted(() => {
  loadJob()
  // ★ 模型列表/默认模型（重新总结弹窗、思维导图等需要）
  if (!modelStore.models.length) modelStore.loadModels()

  // 监听流式 token（done=true 时 token 是完整全文，必须覆盖而非追加）
  socketUnsubs.push(onAiToken((data) => {
    if (data.job_id === route.params.jobId && isStreaming.value) {
      if (data.done) {
        const full = data.token || (Array.isArray(data.tokens) ? data.tokens.join('') : '')
        if (full) streamingText.value = full
      } else {
        const pieces = Array.isArray(data.tokens)
          ? data.tokens
          : (data.token ? [data.token] : [])
        if (pieces.length) streamingText.value += pieces.join('')
      }
    }
  }))
  socketUnsubs.push(onAiThinking((data) => {
    if (data.job_id === route.params.jobId && isStreaming.value) {
      if (data.token) {
        streamingThinking.value += data.token
      }
    }
  }))

  socketUnsubs.push(onAiDone((data) => {
    if (data.job_id === route.params.jobId && isStreaming.value) {
      if (data.success) {
        ElMessage.success('重新总结完成')
      } else {
        ElMessage.error(`处理失败: ${data.error || ''}`)
      }
    }
  }))
})

// ★ 路由参数变化时（如从 /result/jobA 跳到 /result/jobB），重新加载数据
watch(() => route.params.jobId, (newId, oldId) => {
  if (newId && newId !== oldId) {
    activeVersion.value = 0
    activeVersionResult.value = null
    isStreaming.value = false
    streamingText.value = ''
    streamingThinking.value = ''
    mindmapReady.value = false
    resultTab.value = 'summary'
    // 重置转录状态
    transcriptText.value = ''
    transcriptRawText.value = ''
    transcriptCleanText.value = ''
    transcriptLoaded.value = false
    transcriptView.value = 'corrected'
    if (mindmapContainer.value) mindmapContainer.value.innerHTML = ''
    loadJob()
  }
})

onBeforeUnmount(() => {
  // ★ 只移除本组件注册的 socket 监听（不用全局 removeAllListeners，避免误清 keep-alive 的 HomeView 监听）
  socketUnsubs.forEach(off => off())
  socketUnsubs = []
})

// 追问聊天逻辑已迁移至 ChatPanel 组件

// ============ 导出 ============
async function handleExport(format) {
  if (!job.value) return
  try {
    const res = await exportApi[format](job.value.id)
    if (res.success !== false) {
      ElMessage.success('导出成功')
    }
  } catch (err) {
    ElMessage.error(err.message)
  }
}

// ============ 一键复制 ============
async function handleCopyResult() {
  return copyText(displayResult.value)
}

// ============ HTML 预览 ============
function handlePreviewHtml() {
  const text = displayResult.value
  if (!text) { ElMessage.warning('没有可预览的内容'); return }
  const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${job.value?.title || '摘要预览'}</title>
<style>
  body { max-width: 900px; margin: 40px auto; padding: 0 20px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; line-height: 1.8; font-size: 14px; color: #333; }
  h1 { font-size: 24px; border-bottom: 2px solid #409eff; padding-bottom: 8px; }
  h2 { font-size: 18px; margin-top: 24px; }
  h3 { font-size: 15px; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; }
  th, td { border: 1px solid #e4e7ed; padding: 8px 12px; text-align: left; }
  th { background: #f5f7fa; }
  code { background: #f0f0f0; padding: 2px 6px; border-radius: 3px; font-size: 13px; }
  pre { background: #f5f7fa; padding: 16px; border-radius: 6px; overflow-x: auto; }
  pre code { background: none; padding: 0; }
  blockquote { border-left: 3px solid #409eff; margin: 12px 0; padding: 4px 16px; color: #666; background: #f5f7fa; }
  a { color: #409eff; }
</style>
</head>
<body>
${renderedResult.value}
</body>
</html>`
  const blob = new Blob([html], { type: 'text/html;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  window.open(url, '_blank')
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// ============ 搜索 ============
function doSearch() {
  if (!searchText.value.trim() || !displayResult.value) {
    searchMatches.value = []
    currentMatch.value = 0
    return
  }
  const q = searchText.value.toLowerCase()
  const text = displayResult.value.toLowerCase()
  const matches = []
  let idx = 0
  while ((idx = text.indexOf(q, idx)) !== -1) {
    matches.push(idx)
    idx += q.length
  }
  searchMatches.value = matches
  currentMatch.value = matches.length > 0 ? 1 : 0
  if (matches.length === 0) {
    ElMessage.info('未找到匹配内容')
  } else {
    ElMessage.success(`找到 ${matches.length} 处匹配`)
  }
}

function formatTime(time) {
  if (!time) return ''
  return new Date(time).toLocaleString()
}

// ============ 任务状态动作 ============
const taskAction = computed(() => getJobActionModel(job.value?.status))

async function handleTaskAction(action) {
  if (!job.value) return
  if (action === 'cancel') {
    try {
      await ElMessageBox.confirm('确认取消该任务？后台处理将在安全检查点停止。', '取消任务', { type: 'warning' })
      await jobsApi.cancel(job.value.id)
      job.value.status = 'cancelled'
      ElMessage.success('任务已取消')
    } catch (err) {
      if (!isDialogCancel(err)) ElMessage.error(err?.message || err)
    }
    return
  }
  if (action === 'restore') {
    await router.push(buildRestoreRoute(job.value.id))
    return
  }
  if (action === 'rerun') {
    await router.push(buildRerunRoute(job.value.id))
    return
  }
  if (action === 'resume' || action === 'forceRetry') {
    const force = action === 'forceRetry'
    try {
      if (force) {
        await ElMessageBox.confirm('从头重试会删除该任务已有文件并完全重新处理。确认？', '从头重试', { type: 'warning' })
      }
      await jobsApi.retry(job.value.id, force)
      ElMessage.success(force ? '已从头重新处理' : '已断点续传')
      await router.push(buildRestoreRoute(job.value.id))
    } catch (err) {
      if (!isDialogCancel(err)) ElMessage.error(err?.message || err)
    }
  }
}

// 到工作台重做：携带 rerun 参数回工作台，回填原任务配置重新处理
const canContinueInWorkbench = computed(() =>
  !!(job.value && (job.value.input_url || job.value.input_text || job.value.input_file))
)
function goToWorkbench() {
  if (!job.value) return
  router.push(buildRerunRoute(job.value.id))
}

// ★ 可重新总结：已完成的任务，或 AI 阶段失败但保留转录文本的任务（仅重跑 AI 步骤）
const canResummarize = computed(() => {
  if (isStreaming.value) return false
  const s = job.value?.status
  return (s === 'completed' || s === 'failed') && !!(job.value?.files?.txt)
})

// ============ 思维导图 ============

async function generateMindmap() {
  if (mindmapLoading.value) return
  // ★ 无总结结果不发起请求（2026-10-06）：后端会 400，前端先给出可行动提示
  if (!job.value?.result_text) {
    ElMessage.warning('该任务没有总结结果，无法生成思维导图（可先重新总结或到工作台重做）')
    return
  }
  await nextTick()  // ★ 先等 DOM，再设 loading
  let container = mindmapContainer.value
  if (!container) {
    // 结果卡是 v-if 条件渲染，从其它 tab 切过来时可能需要再等一拍
    await nextTick()
    container = mindmapContainer.value
  }
  if (!container) { ElMessage.error('思维导图容器未能挂载，请刷新页面重试'); return }
  mindmapLoading.value = true
  mindmapReady.value = false
  try {
    const res = await jobsApi.generateMindmap(route.params.jobId)
    if (!res?.data?.mindmap_md) {
      ElMessage.warning('思维导图生成失败：返回内容为空')
      mindmapLoading.value = false
      return
    }
    // 直接用 markmap-lib 的 Transformer 解析节点树，手写 HTML 渲染
    const { Transformer } = await import('markmap-lib')
    const transformed = new Transformer().transform(res.data.mindmap_md)
    const root = transformed?.root
    if (!root || !root.content) {
      // Transformer 解析失败 → 直接显示原始 markmap markdown
      container.innerHTML = `<pre style="padding:16px;background:#1a1a1a;color:#0f0;overflow:auto;height:100%;">${res.data.mindmap_md.replace(/</g,'&lt;')}</pre>`
      mindmapReady.value = true
      return
    }
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark'
    const bg = isDark ? '#1a1a1a' : '#fff'
    const text = isDark ? '#e0e0e0' : '#333'
    const colors = ['#409eff', '#67c23a', '#e6a23c', '#f56c6c', '#909399']

    function renderNode(node, depth) {
      if (!node) return ''
      const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
      }[ch]))
      const indent = 20 * depth
      const color = colors[Math.min(depth, colors.length - 1)]
      let html = `<div style="margin-left:${indent}px;padding:4px 0;border-left:2px solid ${color};padding-left:8px;">`
      html += `<div style="font-weight:600;font-size:${Math.max(12, 16-depth)}px;color:${color};cursor:pointer;" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'':'none'">${escapeHtml(node.content || node.v || '●')}</div>`
      if (node.children && node.children.length) {
        html += `<div>${node.children.map(c => renderNode(c, depth + 1)).join('')}</div>`
      }
      html += '</div>'
      return html
    }

    const html = `<div style="padding:16px;font-family:sans-serif;font-size:14px;background:${bg};color:${text};min-height:400px;overflow:auto;">${renderNode(root, 0)}</div>`
    container.innerHTML = html
    mindmapReady.value = true
  } catch (err) {
    ElMessage.error('思维导图生成失败: ' + (err?.message || String(err)))
  } finally {
    mindmapLoading.value = false
  }
}

function switchToMindmapTab() {
  resultTab.value = 'mindmap'
  // ★ 无总结结果：容器内显示占位说明，不自动发起生成（2026-10-06）
  if (!job.value?.result_text) return
  if (!mindmapReady.value && !mindmapLoading.value) {
    generateMindmap()
  }
}

// ============ 加载转录文本 ============
async function loadTranscript() {
  if (transcriptLoaded.value || transcriptLoading.value) return
  const files = job.value?.files || {}
  if (!files.txt) {
    transcriptLoaded.value = true
    return
  }
  transcriptLoading.value = true
  try {
    // 校正后文本（txt_path，校正后已写回）
    const res = await fetch(`/api/download/view?path=${encodeURIComponent(files.txt)}`)
    if (res.ok) {
      transcriptText.value = await res.text()
    }
    // 校正前原文（txt_raw 备份，仅校正过才有）
    if (files.txt_raw) {
      try {
        const resRaw = await fetch(`/api/download/view?path=${encodeURIComponent(files.txt_raw)}`)
        if (resRaw.ok) transcriptRawText.value = await resRaw.text()
      } catch { /* 原文读取失败不阻塞 */ }
    }
    // 纯净阅读版（无时间戳/说话人标记）
    if (files.txt_clean) {
      try {
        const resClean = await fetch(`/api/download/view?path=${encodeURIComponent(files.txt_clean)}`)
        if (resClean.ok) transcriptCleanText.value = await resClean.text()
      } catch { /* 纯净版读取失败不阻塞 */ }
    }
    // 无校正但有纯净版时，默认展示纯净版
    if (!hasCorrection.value && hasClean.value) transcriptView.value = 'clean'
    transcriptLoaded.value = true
  } catch (err) {
    ElMessage.error('加载转录文本失败: ' + (err.message || err))
  } finally {
    transcriptLoading.value = false
  }
}

function switchToTranscriptTab() {
  resultTab.value = 'transcript'
  if (!transcriptLoaded.value) loadTranscript()
}

// 当前展示的转录文本（根据校正/原文/纯净切换）
const displayTranscript = computed(() => {
  if (transcriptView.value === 'raw' && transcriptRawText.value) {
    return transcriptRawText.value
  }
  if (transcriptView.value === 'clean' && transcriptCleanText.value) {
    return transcriptCleanText.value
  }
  return transcriptText.value
})

// 复制转录文本
async function copyTranscript() {
  return copyText(displayTranscript.value)
}
</script>

<template>
  <div class="page-container" v-loading="loading">
    <!-- 顶部导航 -->
    <el-card class="app-card" shadow="never">
      <div class="page-header">
        <el-button :icon="ArrowLeft" @click="goBack">返回</el-button>
        <div class="page-header-text">
          <h2 class="page-title result-title" :title="job?.title || '任务详情'">
            {{ job?.title || '任务详情' }}
          </h2>
        </div>
        <div class="page-header-actions">
          <el-tag v-if="job" :type="jobStatusTagType(job.status)" size="small" effect="plain" round>
            {{ jobStatusLabel(job.status) }}
          </el-tag>
          <el-button
            v-if="taskAction.primary === 'restore' || taskAction.primary === 'resume'"
            type="primary"
            @click="handleTaskAction(taskAction.primary)"
          >
            {{ taskAction.primaryLabel }}
          </el-button>
          <el-button
            v-if="taskAction.secondary === 'rerun' || taskAction.secondary === 'forceRetry'"
            type="warning"
            @click="handleTaskAction(taskAction.secondary)"
          >
            {{ taskAction.secondaryLabel }}
          </el-button>
          <el-button
            v-if="taskAction.canCancel"
            type="danger"
            @click="handleTaskAction('cancel')"
          >
            取消任务
          </el-button>
        </div>
      </div>
    </el-card>

    <!-- 任务信息 -->
    <el-card class="app-card" shadow="never">
      <el-descriptions v-if="job" :column="3" border size="small">
      <el-descriptions-item label="平台">{{ job.platform || '-' }}</el-descriptions-item>
      <el-descriptions-item label="总结类型">
        {{ activeSummaryType }}
        <el-tag v-if="activeVersion > 0" size="small" type="warning" style="margin-left: 4px;">v{{ activeVersion }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="模型">{{ activeModel }}</el-descriptions-item>
      <el-descriptions-item label="创建时间">{{ formatTime(job.created_at) }}</el-descriptions-item>
      <el-descriptions-item label="链接" :span="2">
        <el-link
          v-if="job.input_url"
          :href="job.input_url"
          type="primary"
          target="_blank"
          :underline="false"
          style="font-size: 13px;"
        >
          <span style="word-break: break-all;">{{ job.input_url }}</span>
          <el-icon style="margin-left: 4px; vertical-align: -2px;"><ExternalLink /></el-icon>
        </el-link>
        <span v-else style="color: var(--app-text-secondary);">本地文件导入，无网页链接</span>
      </el-descriptions-item>
    </el-descriptions>
    </el-card>

    <!-- ★ 转录来源说明（2026-10-07 结果可解释性）：与首页结果面板共用 TranscriptProvenance。
         独立成卡：失败任务没有结果卡片，但"这份结果怎么来的"失败时同样该被看见。 -->
    <el-card v-if="job?.transcript_meta" class="app-card" shadow="never">
      <TranscriptProvenance :meta="job.transcript_meta" />
    </el-card>

    <!-- ★ AI 调用链（兜底环）：与首页结果面板共用 AiCallChain，形态/措辞完全一致。
         独立成卡而不是塞进结果区：失败任务没有结果文本，下面的结果卡片整块不渲染，
         而"这次调了谁、为什么全挂了"恰恰是失败时最该被看见的东西。 -->
    <el-card v-if="showCallChain" class="app-card" shadow="never">
      <AiCallChain :chain="job.ai_call_chain" />
    </el-card>

    <!-- ★ 历史版本面板（从后端加载） -->
    <el-card v-if="summaryHistory.length > 0" class="app-card" shadow="never">
      <div style="font-size: 13px; color: var(--app-text-secondary); margin-bottom: 8px;">
        历史版本 ({{ summaryHistory.length }})
        <el-button v-if="activeVersion > 0" size="small" text type="primary" @click="switchToVersion(0)" style="margin-left: 8px;">
          返回最新版
        </el-button>
      </div>
      <div style="display: flex; flex-wrap: wrap; gap: 6px;">
        <div
          v-for="item in summaryHistory"
          :key="item.version"
          class="version-chip"
          :class="{ active: activeVersion === item.version || (activeVersion === 0 && item.version === job?.summary_version) }"
          @click="switchToVersion(item.version)"
        >
          <span style="font-weight: 500;">v{{ item.version }}</span>
          <el-tag v-if="item.version === 1" size="small" type="success" effect="plain" style="margin: 0 4px; font-size: 10px;">基础</el-tag>
          <span style="font-size: 11px; color: var(--app-text-secondary);">{{ item.summary_type }}</span>
          <span style="font-size: 10px; color: var(--app-text-placeholder); margin-left: 4px;">{{ formatTime(item.created_at) }}</span>
          <span style="font-size: 10px; color: var(--app-text-secondary); margin-left: 4px;">{{ item.char_count }}字</span>
        </div>
      </div>
    </el-card>

    <!-- 结果展示区（固定栏，常驻显示） -->
    <div ref="resultSectionRef">

      <!-- ★ 标签切换：总结 / 思维导图 / 转录 -->
      <div v-if="displayResult || isStreaming || (job?.files?.txt)" style="display: flex; align-items: center; gap: 0; margin-bottom: 12px; border-bottom: 2px solid var(--app-border);">
        <div
          @click="resultTab = 'summary'"
          :style="{ padding: '8px 16px', cursor: 'pointer', fontWeight: resultTab === 'summary' ? 600 : 400, color: resultTab === 'summary' ? 'var(--app-accent-1)' : 'var(--app-text-secondary)', borderBottom: resultTab === 'summary' ? '2px solid var(--app-accent-1)' : '2px solid transparent', marginBottom: '-2px' }"
        ><el-icon style="vertical-align: -2px; margin-right: 4px;"><FileText /></el-icon>AI 总结</div>
        <div
          @click="switchToMindmapTab"
          :style="{ padding: '8px 16px', cursor: 'pointer', fontWeight: resultTab === 'mindmap' ? 600 : 400, color: resultTab === 'mindmap' ? 'var(--app-accent-1)' : 'var(--app-text-secondary)', borderBottom: resultTab === 'mindmap' ? '2px solid var(--app-accent-1)' : '2px solid transparent', marginBottom: '-2px' }"
        ><el-icon style="vertical-align: -2px; margin-right: 4px;"><Cpu /></el-icon>思维导图</div>
        <div
          v-if="job?.files?.txt"
          @click="switchToTranscriptTab"
          :style="{ padding: '8px 16px', cursor: 'pointer', fontWeight: resultTab === 'transcript' ? 600 : 400, color: resultTab === 'transcript' ? 'var(--app-accent-1)' : 'var(--app-text-secondary)', borderBottom: resultTab === 'transcript' ? '2px solid var(--app-accent-1)' : '2px solid transparent', marginBottom: '-2px' }"
        ><el-icon style="vertical-align: -2px; margin-right: 4px;"><FileText /></el-icon>转录文本
          <el-tag v-if="hasCorrection" size="small" type="warning" effect="plain" style="margin-left: 4px; font-size: 10px;">已校正</el-tag>
        </div>
        <div style="flex: 1;" />
        <el-button v-if="resultTab === 'mindmap' && !mindmapLoading && mindmapReady" size="small" type="primary" text @click="generateMindmap" style="margin-right: 8px;"><el-icon style="vertical-align: -2px; margin-right: 4px;"><RefreshCcw /></el-icon>重新生成</el-button>
      </div>

      <!-- 工具栏（总结/思维导图 tab 用） -->
      <div v-if="(displayResult || isStreaming) && resultTab !== 'transcript'" style="display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap;">
        <el-input
          v-model="searchText"
          placeholder="在结果中搜索..."
          size="small"
          style="width: 200px;"
          clearable
          @keyup.enter="doSearch"
        >
          <template #append>
            <el-button :icon="Search" @click="doSearch" />
          </template>
        </el-input>
        <span v-if="searchMatches.length" style="font-size: 12px; color: var(--app-text-secondary);">
          {{ currentMatch }}/{{ searchMatches.length }}
        </span>
        <div style="flex: 1;" />
        <!-- 流式输出指示 -->
        <el-tag v-if="isStreaming" type="warning" size="small" effect="plain">
          <el-icon class="is-loading" style="margin-right: 2px;"><Loader2 /></el-icon>
          AI 生成中...
        </el-tag>
        <ResummarizeControl
          :model="job?.model || ''"
          :disabled="!canResummarize"
          button-type="warning"
          @submit="handleResummarize"
        />
        <el-button size="small" type="primary" plain @click="goToWorkbench" :disabled="!canContinueInWorkbench && job?.status !== 'completed'">
          到工作台重做
        </el-button>
        <el-button size="small" type="success" plain @click="handleCopyResult" :disabled="!displayResult">
          一键复制
        </el-button>
        <el-button-group v-if="!isStreaming">
          <el-button size="small" @click="handleExport('docx')">Word</el-button>
          <el-button size="small" @click="handleExport('pdf')">PDF</el-button>
          <el-button size="small" @click="handleExport('html')">HTML</el-button>
          <el-button size="small" @click="handleExport('txt')">TXT</el-button>
          <el-button size="small" type="primary" plain @click="handlePreviewHtml" :disabled="!displayResult">预览</el-button>
        </el-button-group>
      </div>

      <!-- 结果内容 -->
      <!-- ★ mindmap 必须在条件里（2026-10-06）：任务没有总结结果时 displayResult 为 false，
           此前点思维导图 tab 卡片不渲染、容器恒为 null，必弹「容器未就绪」 -->
      <el-card v-if="displayResult || isStreaming || mindmapReady || resultTab === 'mindmap' || resultTab === 'transcript'" class="app-card" shadow="never">
        <!-- ★ 失败任务横幅：可只重跑 AI 总结步骤，无需重新下载/转录 -->
        <el-alert v-if="job?.status === 'failed'" type="error" :closable="false" show-icon style="margin-bottom: 12px;">
          <template #title>
            AI 处理失败：{{ job.error || '未知错误' }}
          </template>
          <div style="margin-top: 8px;">
            重新总结沿用本次提示词（只重跑 AI 步骤，不重新下载/转录），可换用其他 AI 模型；也可到工作台重做，改提示词后完整重跑。
            <div style="display: flex; gap: 8px; margin-top: 10px; align-items: center; flex-wrap: wrap;">
              <ResummarizeControl
                :model="job?.model || ''"
                :disabled="isStreaming"
                button-type="danger"
                plain
                @submit="handleResummarize"
              />
              <el-button size="small" type="primary" plain @click="goToWorkbench">到工作台重做</el-button>
            </div>
          </div>
        </el-alert>

        <!-- 流式输出中但还没收到内容 -->
        <div v-if="isStreaming && !streamingText" style="text-align: center; padding: 40px; color: var(--app-text-secondary);">
          <el-icon class="is-loading" style="font-size: 24px;"><Loader2 /></el-icon>
          <div style="margin-top: 8px;">AI 正在生成...</div>
        </div>
        <!-- Markdown 渲染 -->
        <!-- 思考过程（重总结流式时显示） -->
        <div v-if="streamingThinking" class="streaming-thinking-box">
          <div class="streaming-thinking-head" @click="showThinking = !showThinking">
            <span class="streaming-thinking-label">AI 思考过程</span>
            <el-button size="small" text>{{ showThinking ? '收起' : '展开' }}</el-button>
          </div>
          <pre v-if="showThinking" class="streaming-thinking-text">{{ streamingThinking }}</pre>
        </div>

        <!-- ★ 转录文本 tab：原文/校正切换 + 校正统计 + 纯文本展示 -->
        <div v-if="resultTab === 'transcript'">
          <!-- 转录工具栏 -->
          <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap;">
            <el-radio-group v-if="hasCorrection || hasClean" v-model="transcriptView" size="small">
              <el-radio-button v-if="hasCorrection" value="corrected">校正后</el-radio-button>
              <el-radio-button v-if="hasCorrection" value="raw">校正前原文</el-radio-button>
              <el-radio-button v-if="hasClean" value="clean">纯净</el-radio-button>
            </el-radio-group>
            <span v-else style="font-size: 12px; color: var(--app-text-secondary);">无转录文本</span>
            <div style="flex: 1;" />
            <el-button size="small" type="success" plain @click="copyTranscript" :disabled="!displayTranscript">复制转录</el-button>
          </div>
          <!-- 校正统计告警 -->
          <el-alert
            v-if="hasCorrection && correctionStats && correctionNeedReview && transcriptView === 'corrected'"
            type="warning"
            :closable="false"
            style="margin-bottom: 12px;"
          >
            <template #title>
              AI 校正存在需人工复核的批次：
              失败 {{ correctionStats.failed_chunks }} /
              完整性告警 {{ correctionStats.integrity_warnings }} /
              长度告警 {{ correctionStats.length_warnings }}（共 {{ correctionStats.total_chunks }} 批）
            </template>
          </el-alert>
          <!-- 校正统计（正常） -->
          <div v-if="hasCorrection && correctionStats && !correctionNeedReview && transcriptView === 'corrected'"
               style="font-size: 12px; color: #67c23a; margin-bottom: 8px;">
            ✅ AI 校正完成，{{ correctionStats.total_chunks }} 批全部通过完整性校验，改动 {{ correctionStats.changed_count || 0 }} 处
          </div>
          <!-- 逐句改动明细：语义级替换机械防线拦不住，必须可见可复核 -->
          <el-collapse v-if="hasCorrection && correctionStats && (correctionStats.changed_count || 0) > 0 && transcriptView === 'corrected'"
                       style="margin-bottom: 8px;">
            <el-collapse-item :title="`改动明细（${correctionStats.changed_count} 处）`">
              <div v-for="(d, i) in (correctionStats.changed_lines || [])" :key="i" class="correction-diff-item">
                <span class="diff-ts">{{ d.ts }}</span>
                <span class="diff-before">{{ d.before }}</span>
                <span class="diff-arrow">→</span>
                <span class="diff-after">{{ d.after }}</span>
              </div>
              <div v-if="correctionStats.changed_truncated" class="correction-diff-item" style="color: var(--app-text-secondary);">
                改动较多，仅显示前 100 处
              </div>
            </el-collapse-item>
          </el-collapse>
          <!-- 转录文本内容 -->
          <div v-if="transcriptLoading" style="text-align: center; padding: 40px; color: var(--app-text-secondary);">
            <el-icon class="is-loading" style="font-size: 24px;"><Loader2 /></el-icon>
            <div style="margin-top: 8px;">加载转录文本...</div>
          </div>
          <pre v-else-if="displayTranscript" class="transcript-text">{{ displayTranscript }}</pre>
          <el-empty v-else description="无转录文本" />
        </div>

        <div
          v-else-if="resultTab === 'summary'"
          class="markdown-body"
          v-html="renderedResult"
          style="line-height: 1.8; font-size: 14px;"
        />
        <!-- 思维导图 -->
        <div v-else-if="resultTab === 'mindmap'" style="min-height: 400px; position: relative;">
          <div v-if="mindmapLoading" style="position: absolute; inset: 0; z-index: 2; display: flex; align-items: center; justify-content: center; background: rgba(255,255,255,0.85);">
            <div style="text-align: center; color: var(--app-text-secondary);">
              <el-icon class="is-loading" style="font-size: 24px;"><Loader2 /></el-icon>
              <div style="margin-top: 8px;">正在生成思维导图...</div>
            </div>
          </div>
          <div ref="mindmapContainer" class="mindmap-container" style="width: 100%; height: 560px; overflow: auto; background: #fff; border-radius: 4px;">
            <div v-if="!mindmapReady && !mindmapLoading && !job?.result_text" style="text-align: center; padding: 40px; color: var(--app-text-secondary);">
              该任务没有总结结果，无法生成思维导图（可先重新总结或到工作台重做）
            </div>
            <div v-else-if="!mindmapReady && !mindmapLoading" style="text-align: center; padding: 40px; color: var(--app-text-secondary);">
              思维导图未生成或上次生成失败，点击上方「思维导图」标签重试
            </div>
          </div>
        </div>
      </el-card>

      <!-- 空状态 -->
      <el-empty v-else-if="!loading" description="该任务没有结果文本">
        <el-button v-if="job?.status === 'failed'" type="primary" @click="$router.push('/')">去处理</el-button>
      </el-empty>
    </div>

    <!-- 错误信息 -->
    <el-alert
      v-if="job?.error"
      :title="job.error"
      type="error"
      :closable="false"
    />

    <!-- 追问聊天（ChatPanel：SSE 流式 + 思考过程 + 引用源 + 编辑/重发） -->
    <ChatPanel
      v-if="job?.status === 'completed' && !isStreaming"
      :job-id="job?.id"
    />
  </div>
</template>

<style scoped>
/* 结果页标题：占满剩余宽度、超长省略（字号/字重沿用全局 .page-title） */
.result-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-bubble-user {
  background: var(--app-accent-1);
  color: #fff;
}
.chat-bubble-ai {
  background: rgba(var(--app-dim-color, 11, 17, 32), 0.5);
  color: var(--app-text);
}
.version-chip {
  display: inline-flex;
  align-items: center;
  padding: 6px 10px;
  border: 1.5px solid var(--app-border);
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
  background: transparent;
  font-size: 12px;
  gap: 2px;
}
.version-chip:hover {
  border-color: var(--app-accent-1);
  background: rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.08);
}
.version-chip.active {
  border-color: var(--app-accent-1);
  background: rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.15);
}

/* 流式思考过程 */
.streaming-thinking-box {
  margin-bottom: 12px;
  border: 1px dashed var(--app-accent-1);
  border-radius: 8px;
  background: rgba(var(--app-accent-1-rgb), 0.06);
}
.streaming-thinking-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 10px;
  cursor: pointer;
}
.streaming-thinking-label {
  font-size: 13px;
  color: var(--app-accent-1);
}
.streaming-thinking-text {
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

/* 转录文本 */
.transcript-text {
  margin: 0;
  padding: 16px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13.5px;
  line-height: 1.7;
  background: var(--app-preview-bg);
  border-radius: 6px;
  max-height: 70vh;
  overflow-y: auto;
  font-family: 'Consolas', 'Monaco', 'Source Code Pro', monospace;
}

/* 校正改动明细 */
.correction-diff-item {
  font-size: 12.5px;
  line-height: 1.8;
  padding: 2px 0;
  word-break: break-word;
}
.correction-diff-item .diff-ts {
  color: var(--app-text-secondary);
  margin-right: 6px;
  font-family: 'Consolas', 'Monaco', 'Source Code Pro', monospace;
}
.correction-diff-item .diff-before {
  color: #e6a23c;
}
.correction-diff-item .diff-arrow {
  color: var(--app-text-secondary);
  margin: 0 6px;
}
.correction-diff-item .diff-after {
  color: #67c23a;
}

/* 思维导图容器 */
.mindmap-container {
  width: 100%;
  min-height: 400px;
  background: var(--app-preview-bg);
  border-radius: 4px;
}
.mindmap-container :deep(svg) {
  width: 100% !important;
  height: auto !important;
  min-height: 400px;
}
</style>
