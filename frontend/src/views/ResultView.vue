<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Search, RefreshCw, Loader2, Plus, FileText, Cpu, RefreshCcw } from 'lucide-vue-next'
import { jobsApi, exportApi, chatApi } from '../utils/api'
import { renderMarkdown } from '../utils/markdown'
import { useModelStore } from '../stores/modelStore'
import { buildRerunRoute, buildRestoreRoute, getJobActionModel } from '../utils/jobActions'
import { jobStatusLabel, jobStatusTagType } from '../utils/jobStatus'
import ResummarizeDialog from '../components/ResummarizeDialog.vue'
import ChatPanel from '../components/ChatPanel.vue'
import {
  onProgress, onLog, onAiToken, onAiDone, onAiThinking,
} from '../utils/socket'
import { Transformer } from 'markmap-lib'

const route = useRoute()
const router = useRouter()
const modelStore = useModelStore()

const job = ref(null)
const loading = ref(false)
const searchText = ref('')
const searchMatches = ref([])
const currentMatch = ref(0)
const resummarizeDialog = ref(false)
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
const mindmapTab = ref('summary')
const mindmapContainer = ref(null)

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

const renderedResult = computed(() => renderMarkdown(displayResult.value))

async function loadJob() {
  const jobId = route.params.jobId
  if (!jobId) return
  loading.value = true
  try {
    job.value = await jobsApi.get(jobId)
    await loadSummaryHistory()
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
  } catch {
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
function openResummarize() {
  resummarizeDialog.value = true
}

function onResummarizeSuccess(data) {
  // 弹窗已关闭，准备流式输出
  activeVersion.value = 0
  streamingThinking.value = ''
  activeVersionResult.value = null
  isStreaming.value = true
  streamingText.value = ''
  
  // 滚动到结果区
  nextTick(() => {
    resultSectionRef.value?.scrollIntoView({ behavior: 'smooth' })
  })
  
  // 异步提交重新总结请求（整包透传 dialog 的 dimensions/model/options）
  const jobId = route.params.jobId
  jobsApi.summarize(jobId, data).then(res => {
    // HTTP 返回后，结果已通过 socket 流式推送完毕
    if (res?.result_text) {
      job.value.result_text = res.result_text
      if (data.summary_type) job.value.summary_type = data.summary_type
      if (data.dimensions) job.value.dimensions = data.dimensions
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
    mindmapTab.value = 'summary'
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
  const text = displayResult.value
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

// ============ 标签管理 ============
const tagGenerating = ref(false)
const tagEditing = ref(false)
const tagInputValue = ref('')

async function handleGenerateTags() {
  if (!job.value) return
  tagGenerating.value = true
  try {
    const res = await jobsApi.generateTags(job.value.id, job.value.model)
    job.value.tags = res.data?.tags || res.tags || []
    ElMessage.success(`AI 生成了 ${job.value.tags.length} 个标签`)
  } catch (err) {
    ElMessage.error(`标签生成失败: ${err.message}`)
  } finally {
    tagGenerating.value = false
  }
}

function startEditTags() {
  tagEditing.value = true
}

async function addTag() {
  const val = tagInputValue.value.trim()
  if (!val) return
  const tags = [...(job.value.tags || []), val]
  try {
    await jobsApi.updateTags(job.value.id, tags)
    job.value.tags = tags
    tagInputValue.value = ''
  } catch (err) {
    ElMessage.error(`添加失败: ${err.message}`)
  }
}

async function removeTag(tag) {
  const tags = (job.value.tags || []).filter(t => t !== tag)
  try {
    await jobsApi.updateTags(job.value.id, tags)
    job.value.tags = tags
  } catch (err) {
    ElMessage.error(`删除失败: ${err.message}`)
  }
}

async function saveTags() {
  tagEditing.value = false
  const tags = job.value.tags || []
  try {
    await jobsApi.updateTags(job.value.id, tags)
  } catch (err) {
    ElMessage.error(`保存失败: ${err.message}`)
  }
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
      if (err !== 'cancel') ElMessage.error(err.message)
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
      if (err !== 'cancel') ElMessage.error(err.message)
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

// ============ 思维导图 ============

async function generateMindmap() {
  if (mindmapLoading.value) return
  await nextTick()  // ★ 先等 DOM，再设 loading
  const container = mindmapContainer.value
  if (!container) { ElMessage.error('容器未就绪，请刷新页面'); return }
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
  mindmapTab.value = 'mindmap'
  if (!mindmapReady.value && !mindmapLoading.value) {
    generateMindmap()
  }
}
</script>

<template>
  <div style="max-width: 1000px; margin: 0 auto; padding: 24px;" v-loading="loading">
    <!-- 顶部导航 -->
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px;">
      <el-button :icon="ArrowLeft" @click="goBack" size="small">返回</el-button>
      <h2 style="margin: 0; flex: 1; font-size: 18px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
        {{ job?.title || '任务详情' }}
      </h2>
      <el-tag v-if="job" :type="jobStatusTagType(job.status)" size="small" effect="dark">
        {{ jobStatusLabel(job.status) }}
      </el-tag>
      <el-button
        v-if="taskAction.primary === 'restore' || taskAction.primary === 'resume'"
        type="primary"
        size="small"
        @click="handleTaskAction(taskAction.primary)"
      >
        {{ taskAction.primaryLabel }}
      </el-button>
      <el-button
        v-if="taskAction.secondary === 'rerun' || taskAction.secondary === 'forceRetry'"
        type="warning"
        size="small"
        @click="handleTaskAction(taskAction.secondary)"
      >
        {{ taskAction.secondaryLabel }}
      </el-button>
      <el-button
        v-if="taskAction.canCancel"
        type="danger"
        size="small"
        @click="handleTaskAction('cancel')"
      >
        取消任务
      </el-button>
    </div>

    <!-- 任务信息 -->
    <el-descriptions v-if="job" :column="3" border size="small" style="margin-bottom: 20px;">
      <el-descriptions-item label="平台">{{ job.platform || '-' }}</el-descriptions-item>
      <el-descriptions-item label="总结类型">
        {{ activeSummaryType }}
        <el-tag v-if="activeVersion > 0" size="small" type="warning" style="margin-left: 4px;">v{{ activeVersion }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="模型">{{ activeModel }}</el-descriptions-item>
      <el-descriptions-item label="创建时间">{{ formatTime(job.created_at) }}</el-descriptions-item>
      <el-descriptions-item label="标签" :span="2">
        <template v-if="tagEditing">
          <div style="display: flex; flex-wrap: wrap; gap: 4px; align-items: center;">
            <el-tag
              v-for="t in (job.tags || [])"
              :key="t"
              size="small"
              closable
              @close="removeTag(t)"
              style="margin: 2px;"
            >{{ t }}</el-tag>
            <el-input
              v-model="tagInputValue"
              size="small"
              placeholder="输入新标签"
              style="width: 100px;"
              @keyup.enter="addTag"
            />
            <el-button size="small" @click="addTag" :icon="Plus">添加</el-button>
            <el-button size="small" type="primary" @click="saveTags">完成</el-button>
          </div>
        </template>
        <template v-else>
          <el-tag v-for="t in (job.tags || [])" :key="t" size="small" style="margin-right: 4px;">{{ t }}</el-tag>
          <span v-if="!job.tags?.length" style="color: #909399;">无标签</span>
          <el-button size="small" text type="primary" @click="startEditTags" style="margin-left: 4px;">编辑</el-button>
          <el-button
            size="small"
            text
            type="warning"
            @click="handleGenerateTags"
            :loading="tagGenerating"
            :disabled="job?.status !== 'completed'"
            style="margin-left: 4px;"
          >AI 生成</el-button>
        </template>
      </el-descriptions-item>
    </el-descriptions>

    <!-- ★ 历史版本面板（从后端加载） -->
    <div v-if="summaryHistory.length > 0" style="margin-bottom: 16px;">
      <div style="font-size: 13px; color: #909399; margin-bottom: 8px;">
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
          <span style="font-size: 11px; color: #909399;">{{ item.summary_type }}</span>
          <span style="font-size: 10px; color: #606266; margin-left: 4px;">{{ formatTime(item.created_at) }}</span>
          <span style="font-size: 10px; color: #909399; margin-left: 4px;">{{ item.char_count }}字</span>
        </div>
      </div>
    </div>

    <!-- 结果展示区（固定栏，常驻显示） -->
    <div ref="resultSectionRef">

      <!-- ★ 标签切换：总结 / 思维导图 -->
      <div v-if="displayResult || isStreaming" style="display: flex; align-items: center; gap: 0; margin-bottom: 12px; border-bottom: 2px solid #e4e7ed;">
        <div
          @click="mindmapTab = 'summary'"
          :style="{ padding: '8px 16px', cursor: 'pointer', fontWeight: mindmapTab === 'summary' ? 600 : 400, color: mindmapTab === 'summary' ? '#409eff' : '#909399', borderBottom: mindmapTab === 'summary' ? '2px solid #409eff' : '2px solid transparent', marginBottom: '-2px' }"
        ><el-icon style="vertical-align: -2px; margin-right: 4px;"><FileText /></el-icon>AI 总结</div>
        <div
          @click="switchToMindmapTab"
          :style="{ padding: '8px 16px', cursor: 'pointer', fontWeight: mindmapTab === 'mindmap' ? 600 : 400, color: mindmapTab === 'mindmap' ? '#409eff' : '#909399', borderBottom: mindmapTab === 'mindmap' ? '2px solid #409eff' : '2px solid transparent', marginBottom: '-2px' }"
        ><el-icon style="vertical-align: -2px; margin-right: 4px;"><Cpu /></el-icon>思维导图</div>
        <div style="flex: 1;" />
        <el-button v-if="mindmapTab === 'mindmap' && !mindmapLoading && mindmapReady" size="small" type="primary" text @click="generateMindmap" style="margin-right: 8px;"><el-icon style="vertical-align: -2px; margin-right: 4px;"><RefreshCcw /></el-icon>重新生成</el-button>
      </div>

      <!-- 工具栏 -->
      <div v-if="displayResult || isStreaming" style="display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap;">
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
        <span v-if="searchMatches.length" style="font-size: 12px; color: #909399;">
          {{ currentMatch }}/{{ searchMatches.length }}
        </span>
        <div style="flex: 1;" />
        <!-- 流式输出指示 -->
        <el-tag v-if="isStreaming" type="warning" size="small" effect="plain">
          <el-icon class="is-loading" style="margin-right: 2px;"><Loader2 /></el-icon>
          AI 生成中...
        </el-tag>
        <el-button size="small" type="warning" :icon="RefreshCw" @click="openResummarize" :disabled="isStreaming || job?.status !== 'completed'">
          重新总结
        </el-button>
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
      <el-card v-if="displayResult || isStreaming || mindmapReady" shadow="never">
        <!-- 流式输出中但还没收到内容 -->
        <div v-if="isStreaming && !streamingText" style="text-align: center; padding: 40px; color: #909399;">
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

        <div
          v-if="mindmapTab === 'summary'"
          class="markdown-body"
          v-html="renderedResult"
          style="line-height: 1.8; font-size: 14px;"
        />
        <!-- 思维导图 -->
        <div v-else-if="mindmapTab === 'mindmap'" style="min-height: 400px; position: relative;">
          <div v-if="mindmapLoading" style="position: absolute; inset: 0; z-index: 2; display: flex; align-items: center; justify-content: center; background: rgba(255,255,255,0.85);">
            <div style="text-align: center; color: #909399;">
              <el-icon class="is-loading" style="font-size: 24px;"><Loader2 /></el-icon>
              <div style="margin-top: 8px;">正在生成思维导图...</div>
            </div>
          </div>
          <div ref="mindmapContainer" class="mindmap-container" style="width: 100%; height: 560px; overflow: auto; background: #fff; border-radius: 4px;">
            <div v-if="!mindmapReady && !mindmapLoading" style="text-align: center; padding: 40px; color: #909399;">
              点击上方 <strong><el-icon style="vertical-align: -2px;"><Cpu /></el-icon> 思维导图</strong> 标签生成
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
      style="margin-top: 16px;"
    />

    <!-- 重新总结弹窗 -->
    <ResummarizeDialog
      v-model="resummarizeDialog"
      :job-id="job?.id || ''"
      @success="onResummarizeSuccess"
    />

    <!-- 追问聊天（ChatPanel：SSE 流式 + 思考过程 + 引用源 + 编辑/重发） -->
    <ChatPanel
      v-if="job?.status === 'completed' && !isStreaming"
      :job-id="job?.id"
    />
  </div>
</template>

<style scoped>
.chat-bubble-user {
  background: #409eff;
  color: #fff;
}
.chat-bubble-ai {
  background: rgba(255, 255, 255, 0.08);
  color: #e0e0e0;
}
.version-chip {
  display: inline-flex;
  align-items: center;
  padding: 6px 10px;
  border: 1.5px solid #444;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
  background: transparent;
  font-size: 12px;
  gap: 2px;
}
.version-chip:hover {
  border-color: #409eff;
  background: rgba(64, 158, 255, 0.08);
}
.version-chip.active {
  border-color: #409eff;
  background: rgba(64, 158, 255, 0.15);
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

/* 思维导图容器 */
.mindmap-container {
  width: 100%;
  min-height: 400px;
  background: #fafafa;
  border-radius: 4px;
}
.mindmap-container :deep(svg) {
  width: 100% !important;
  height: auto !important;
  min-height: 400px;
}
</style>
