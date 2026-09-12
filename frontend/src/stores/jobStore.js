import { defineStore } from 'pinia'
import { ref, reactive, watch } from 'vue'
import { jobsApi } from '../utils/api'
import { ACTIVE_JOB_STATUSES } from '../utils/jobActions'

// ============ localStorage 持久化 ============
const STORAGE_KEY = 'yt_tool_job_state'

function saveToStorage(state) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch { /* 忽略 quota 错误 */ }
}

function loadFromStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function clearStorage() {
  try { localStorage.removeItem(STORAGE_KEY) } catch { /* ignore */ }
}

// 日志按 jobId 分组持久化（避免日志太多撑爆 localStorage）
const LOG_STORAGE_KEY = 'yt_tool_job_logs'
function saveLogsToStorage(jobId, logs) {
  try {
    const all = loadLogsFromStorage()
    all[jobId] = logs.slice(-200)  // 每个 job 最多保留 200 条
    // 只保留最近 10 个 job 的日志
    const keys = Object.keys(all)
    if (keys.length > 10) {
      delete all[keys[0]]
    }
    localStorage.setItem(LOG_STORAGE_KEY, JSON.stringify(all))
  } catch { /* 忽略 */ }
}
function loadLogsFromStorage() {
  try {
    const raw = localStorage.getItem(LOG_STORAGE_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}
function loadLogsForJob(jobId) {
  const all = loadLogsFromStorage()
  return all[jobId] || []
}
function clearLogsForJob(jobId) {
  try {
    const all = loadLogsFromStorage()
    delete all[jobId]
    localStorage.setItem(LOG_STORAGE_KEY, JSON.stringify(all))
  } catch { /* ignore */ }
}

export const useJobStore = defineStore('job', () => {
  // ============ 状态 ============
  const currentJobId = ref(null)
  const jobStatus = ref('idle')  // idle/pending/downloading/transcribing/preview/summarizing/completed/failed
  const progress = reactive({
    step: '',
    pct: 0,
    msg: '',
  })
  const logs = ref([])
  const aiTokens = ref([])  // 保留用于debug，不再用于渲染
  const resultText = ref('')
  const streamingThinking = ref('')
  const _renderDirty = ref(false)  // rAF 渲染标记
  const previewText = ref('')
  const isProcessing = ref(false)
  // 任务标题（恢复时显示）
  const jobTitle = ref('')

  // 日志持久化节流：进度日志高频到达，合并为一次写（trailing debounce）
  let logsSaveTimer = null
  let logsSaveJobId = null

  function flushLogsNow() {
    if (logsSaveTimer !== null) {
      clearTimeout(logsSaveTimer)
      logsSaveTimer = null
    }
    if (currentJobId.value && logsSaveJobId === currentJobId.value) {
      saveLogsToStorage(currentJobId.value, logs.value)
    }
    logsSaveJobId = null
  }

  // ============ 持久化 currentJobId + jobStatus ============
  // 任务进入 active 状态时持久化，完成/失败/取消时清除
  watch([currentJobId, jobStatus], ([id, status]) => {
    if (id && ACTIVE_JOB_STATUSES.includes(status)) {
      saveToStorage({ currentJobId: id, jobStatus: status, jobTitle: jobTitle.value })
    } else if (['completed', 'failed', 'cancelled', 'idle'].includes(status)) {
      flushLogsNow()  // 终态落盘：把最后一批日志也持久化，避免刷新丢尾部
      clearStorage()
    }
  })

  // ============ 动作 ============
  function clearLogs() {
    flushLogsNow()
    // flushLogsNow 已取消待写定时器；清空内存与存储（先清存储避免定时器复活）
    logs.value = []
    if (currentJobId.value) clearLogsForJob(currentJobId.value)
  }

  function reset() {
    clearLogs()
    currentJobId.value = null
    jobStatus.value = 'idle'
    progress.step = ''
    progress.pct = 0
    progress.msg = ''
    logs.value = []
    aiTokens.value = []
    resultText.value = ''
    previewText.value = ''
    streamingThinking.value = ''
    isProcessing.value = false
    jobTitle.value = ''
    clearStorage()
  }

  function addLog(msg) {
    const time = new Date().toLocaleTimeString()
    logs.value.push({ time, msg })
    // 保留最近500条日志
    if (logs.value.length > 500) {
      logs.value = logs.value.slice(-500)
    }
    // 持久化日志（用于刷新恢复）——按帧合并写，避免每条日志一次 JSON.stringify
    if (currentJobId.value) {
      logsSaveJobId = currentJobId.value
      if (logsSaveTimer === null) {
        logsSaveTimer = setTimeout(() => {
          logsSaveTimer = null
          if (logsSaveJobId === currentJobId.value) {
            saveLogsToStorage(currentJobId.value, logs.value)
          }
        }, 1500)
      }
    }
  }

  function updateProgress(data) {
    progress.step = data.step || progress.step
    progress.pct = data.pct ?? progress.pct
    progress.msg = data.msg || ''
  }

  function appendAiToken(data) {
    if (data.done) {
      // done 信号附带完整内容，直接覆盖（避免流式片段拼接不完整）
      resultText.value = data.token
      aiTokens.value = [data.token]
      _renderDirty.value = true  // 标记需要渲染
    } else {
      // 兼容两种格式：单 token string（旧）和 batch array（新）
      const pieces = Array.isArray(data.tokens) ? data.tokens : (data.token ? [data.token] : [])
      if (pieces.length > 0) {
        // ★ 字符串直接拼接，O(1) 操作（替代旧 O(n²) 的 array join）
        resultText.value += pieces.join('')
        _renderDirty.value = true  // 标记需要渲染（由 rAF 决定何时实际渲染）
      }
    }
    if (data.done) {
      jobStatus.value = 'completed'
      isProcessing.value = false
    }
  }

  function appendAiThinking(data) {
    if (data && data.token) {
      streamingThinking.value += data.token
    }
  }

  function setPreview(data) {
    previewText.value = data.text || ''
    jobStatus.value = 'preview'
  }

  function setResult(text) {
    resultText.value = text
    aiTokens.value = [text]
  }

  async function submitJob(jobData) {
    reset()
    isProcessing.value = true
    jobStatus.value = 'pending'

    try {
      const res = await jobsApi.submit(jobData)
      currentJobId.value = res.job_id

      if (res.existing_job && jobData.force_new) {
        throw new Error('后端未创建新任务，请重启后端后重试')
      }

      // ★ URL 去重：如果已有同 URL 的任务，跳转到已有任务（force_new 模式除外）
      if (res.existing_job && !jobData.force_new) {
        addLog(`该URL已有处理记录（${res.existing_status}），跳转到已有任务`)
        jobStatus.value = res.existing_status || 'idle'
        jobTitle.value = res.existing_title || ''
        isProcessing.value = ACTIVE_JOB_STATUSES.includes(res.existing_status)
        return res  // 返回给调用方做跳转判断
      }

      addLog(`任务已提交: ${res.job_id}`)
      return res
    } catch (err) {
      jobStatus.value = 'failed'
      addLog(`提交失败: ${err.message}`)
      isProcessing.value = false
      throw err
    }
  }

  async function loadJob(jobId) {
    try {
      const job = await jobsApi.get(jobId)
      currentJobId.value = job.id
      jobStatus.value = job.status
      jobTitle.value = job.title || ''
      resultText.value = job.result_text || ''
      // 恢复预览文本（preview 状态）
      if (job.status === 'preview' && job.preview_text) {
        previewText.value = job.preview_text
      }
      // 恢复进度信息
      if (job.progress_msg) {
        progress.msg = job.progress_msg
      }
      // 恢复日志
      const savedLogs = loadLogsForJob(jobId)
      if (savedLogs && savedLogs.length > 0) {
        logs.value = savedLogs
      } else {
        logs.value = []
      }
      // active 状态 → 标记为处理中
      if (ACTIVE_JOB_STATUSES.includes(job.status)) {
        isProcessing.value = true
      } else {
        isProcessing.value = false
      }
      return job
    } catch (err) {
      addLog(`加载任务失败: ${err.message}`)
      throw err
    }
  }

  /**
   * 从 localStorage 恢复未完成的任务状态
   * 返回 true 表示有可恢复的任务
   */
  function tryRestore() {
    const saved = loadFromStorage()
    if (!saved || !saved.currentJobId) return false
    currentJobId.value = saved.currentJobId
    jobStatus.value = saved.jobStatus || 'pending'
    jobTitle.value = saved.jobTitle || ''
    return true
  }

  function saveState() {
    if (currentJobId.value && ACTIVE_JOB_STATUSES.includes(jobStatus.value)) {
      saveToStorage({ currentJobId: currentJobId.value, jobStatus: jobStatus.value, jobTitle: jobTitle.value })
    }
  }

  return {
    // 状态
    currentJobId,
    jobStatus,
    progress,
    logs,
    aiTokens,
    resultText,
    previewText,
    streamingThinking,
    isProcessing,
    jobTitle,
    // 动作
    reset,
    addLog,
    clearLogs,
    flushLogsNow,
    updateProgress,
    appendAiToken,
    appendAiThinking,
    setPreview,
    setResult,
    submitJob,
    loadJob,
    tryRestore,
    saveState,
  }
})
