<script setup>
/**
 * 结果卡片（HomeView 拆分二阶段 slice-B，2026-09-12）
 * - 自持结果区全部交互：图文/思维导图 Tab、在线编辑、导出、下载素材、重新总结、复制、思考过程
 * - 数据源为 jobStore（resultText/aiTokens/streamingThinking/currentJobId）与 settingsStore（summaryType）
 * - expose reset()：父组件提交新任务时复位（editMode=false + resultTab='markdown'，与原 handleSubmit 语义一致）
 */
import { ref, computed, watch, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { Pencil, Check, X, Copy, Loader2, ArrowDown } from 'lucide-vue-next'
import { useJobStore } from '../stores/jobStore'
import { useSettingsStore } from '../stores/settingsStore'
import { jobsApi, exportApi, resultApi } from '../utils/api'
import { buildSamePromptPayload } from '../utils/jobActions'
import { copyText } from '../utils/clipboard'
import { useRafMarkdown } from '../composables/useMarkdownRender'
import { createFailureNotifier } from '../utils/errorNotice'
import ResummarizeControl from './ResummarizeControl.vue'

const jobStore = useJobStore()
const settings = useSettingsStore()

// 思维导图渲染：markmap 体积较大，切到思维导图 Tab 时再动态加载
let transformer = null

const resultCardRef = ref(null)
const isProcessing = computed(() => jobStore.isProcessing)

// ★ rAF 节流渲染 Markdown：每帧最多渲染一次，避免高频 token 推送导致 O(n) 重渲染堆积
// （逻辑抽离到 composables/useMarkdownRender，组件卸载时自清 rAF 回调）
const { html: renderedResult } = useRafMarkdown(() => jobStore.resultText)

// ============ 结果区 Tab 管理 ============
// markdown / mindmap
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

// ============ 任务关联文件列表（下载素材：字幕/音频/视频等） ============
// 后台静默拉取失败提示（连续 3 次才提示一次，成功即重置，避免刷屏）
const jobFilesNotifier = createFailureNotifier('任务文件列表加载失败')
const _jobFiles = ref([])

const jobFiles = computed(() => {
  if (!jobStore.currentJobId) return []
  // ★ 输出素材过滤（v4 起真实生效）：高级选项勾选 = 这里展示哪些产物。
  //   txt 组覆盖其衍生文件（纯净版/校正原文备份）；未登记键（历史遗留等）不过滤始终展示
  const FILE_GROUP_KEYS = {
    result_md: ['result_md'],
    srt: ['srt'],
    txt: ['txt', 'txt_clean', 'txt_raw'],
    audio: ['audio'],
    video: ['video'],
  }
  const keyToGroup = new Map(
    Object.entries(FILE_GROUP_KEYS).flatMap(([group, keys]) => keys.map((k) => [k, group])),
  )
  return _jobFiles.value.filter((f) => {
    const group = keyToGroup.get(f.key)
    return group ? settings.outputOptions.includes(group) : true
  })
})

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

function downloadJobFile(file) {
  if (!file?.path) return
  // ★ 传 job_id + label：后端用视频名生成可读下载名（{视频名}_{素材类型}.{ext}），
  //   不再直接暴露磁盘名（audio.m4a / bilibili_subtitle.srt）
  const params = new URLSearchParams({ path: file.path })
  if (jobStore.currentJobId) params.set('job_id', jobStore.currentJobId)
  if (file.label) params.set('label', file.label)
  window.open(`/api/download/file?${params.toString()}`, '_blank')
}

// 任务完成时加载文件列表
watch(() => jobStore.jobStatus, (val) => {
  if (val === 'completed' && jobStore.currentJobId) {
    loadJobFiles(jobStore.currentJobId)
  }
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
    resultCardRef.value?.$el?.scrollIntoView({ behavior: 'smooth' })
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

// 复制结果
async function copyResult() {
  return copyText(jobStore.resultText)
}

// ============ 思考过程显隐 ============
const showThinking = ref(true)

// ============ 父组件接口 ============
/** 提交新任务时复位结果区（等价原 handleSubmit 的 editMode=false + resultTab='markdown'） */
function reset() {
  editMode.value = false
  resultTab.value = 'markdown'
}
defineExpose({ reset })
</script>

<template>
  <el-card id="result-section" ref="resultCardRef" class="wb-card result-card" shadow="never">
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
              <el-dropdown-item v-for="f in jobFiles" :key="f.key" :command="f">
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
</template>

<style scoped>
/* 结果卡样式（自 HomeView 迁入，2026-09-12 slice-B） */
.result-card {
  min-height: 200px;
}

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
</style>
