import axios from 'axios'

// 创建 axios 实例
const api = axios.create({
  baseURL: '/api',  // 开发时通过 Vite proxy 代理到后端
  timeout: 300000,  // 5分钟超时（下载/转录可能很久）
  // 不设默认 Content-Type：axios 按 payload 自动识别
  //   对象 → application/json，FormData → multipart/form-data + boundary
})

// 请求拦截器
api.interceptors.request.use(
  (config) => {
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器
api.interceptors.response.use(
  (response) => {
    // blob 响应返回完整 response（导出功能需要 headers 和 data）
    if (response.config?.responseType === 'blob') {
      return response
    }
    return response.data
  },
  (error) => {
    console.error('API Error:', error)
    const detail = error.response?.data?.detail
    // FastAPI 422 校验错误 detail 是数组，逐条提取 msg
    const msg = Array.isArray(detail)
      ? detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
      : (detail || error.message || '请求失败')
    return Promise.reject(new Error(msg))
  }
)

// ============ 任务相关 API ============
export const jobsApi = {
  submit: (data) => api.post('/jobs', data),
  list: (params = {}) => api.get('/jobs', { params }),
  get: (jobId) => api.get(`/jobs/${jobId}`),
  delete: (jobId, deleteFiles = true) => api.delete(`/jobs/${jobId}`, { params: { delete_files: deleteFiles } }),
  clear: () => api.post('/jobs/clear'),
  clearByStatus: (status) => api.post(`/jobs/clear/${status}`),
  cancel: (jobId) => api.post(`/jobs/${jobId}/cancel`),
  batchCancel: (jobIds) => api.post('/jobs/batch-cancel', { job_ids: jobIds }),
  batchDelete: (jobIds, dryRun = false, deleteFiles = true) =>
    api.post('/jobs/batch-delete', { job_ids: jobIds, dry_run: dryRun, delete_files: deleteFiles }),
  batchDiskInfo: (jobIds) => api.post('/jobs/batch-disk-info', { job_ids: jobIds }),
  retry: (jobId, force = false) => api.post(`/jobs/${jobId}/retry`, null, { params: { force } }),
  getLogs: (lines = 300) => api.get('/logs', { params: { lines } }),
  running: () => api.get('/jobs/running'),
  batch: (data) => api.post('/jobs/batch', data),  // data: {urls, options, model, summary_type, do_summary, ...}
  batchStatus: (batchId) => api.get(`/jobs/batch/${batchId}`),
  searchTranscript: (q, limit = 50, maxMatchesPerJob = 5) =>
    api.get('/jobs/search/transcript', { params: { q, limit, max_matches_per_job: maxMatchesPerJob } }),
  summarize: (jobId, data) => api.post(`/jobs/${jobId}/summarize`, data),
  screenshots: (jobId, frameCount = 10) => api.get(`/jobs/${jobId}/screenshots`, { params: { frame_count: frameCount } }),
  getSummaryHistory: (jobId) => api.get(`/jobs/${jobId}/summary-history`),
  getSummaryVersion: (jobId, version) => api.get(`/jobs/${jobId}/summary-history/${version}`),
  generateTags: (jobId, model) => api.post(`/jobs/${jobId}/generate-tags`, model ? { model } : {}),
  updateTags: (jobId, tags) => api.put(`/jobs/${jobId}/tags`, { tags }),
  generateMindmap: (jobId, model) => api.post(`/jobs/${jobId}/mindmap`, model ? { model } : {}),
}

// ============ 存储清理 API（仅 localhost 可用） ============
export const storageCleanupApi = {
  status: () => api.get('/storage-cleanup/status'),
  preview: (policy) => api.post('/storage-cleanup/preview', policy),
  apply: (planId, confirm) => api.post('/storage-cleanup/apply', {
    plan_id: planId,
    confirm,
  }),
  audit: (limit = 100) => api.get('/storage-cleanup/audit', { params: { limit } }),
}

// ============ 云端 ASR API ============
export const asrApi = {
  status: () => api.get('/asr/status'),
}

// ============ 模型管理 API ============
export const modelsApi = {
  list: () => api.get('/models'),
  add: (data) => api.post('/models', data),
  update: (key, data) => api.put(`/models/${key}`, data),
  delete: (key) => api.delete(`/models/${key}`),
  test: (data) => api.post('/models/test', data),
  setDefault: (key) => api.post('/models/default', { key }),
  fetch: (keyOrParams) => {
    // 支持两种调用方式：传 key（字符串）或传 {endpoint, api_key}（对象）
    if (typeof keyOrParams === 'string') {
      return api.post('/models/fetch', null, { params: { key: keyOrParams } })
    }
    return api.post('/models/fetch', null, { params: keyOrParams })
  },
}

// ============ 总结 API（模块化提示词系统） ============
export const summaryApi = {
  // 模块化提示词：维度/配方/默认/预览
  dimensions: () => api.get('/summary/dimensions'),
  recipes: () => api.get('/summary/recipes'),
  defaults: () => api.get('/summary/defaults'),
  // 拼装预览：{ dimensions: {...}, custom_prompt?: "...", full_override?: "..." }
  promptPreview: (data) => api.post('/summary/prompt-preview', data),
  // 一次性加载全部配置（dimensions + recipes + defaults）
  deepConfig: () => api.get('/summary/deep-config'),
  // 模块覆盖 CRUD（供 TemplatesView 编辑器）
  listOverrides: () => api.get('/summary/overrides'),
  getOverride: (dimKey, modKey) => api.get(`/summary/overrides/${dimKey}/${modKey}`),
  upsertOverride: (data) => api.put('/summary/overrides', data),
  deleteOverride: (dimKey, modKey) => api.delete(`/summary/overrides/${dimKey}/${modKey}`),
  resetAllOverrides: () => api.delete('/summary/overrides'),
  // 兼容旧 API（保留空实现）
  types: () => Promise.resolve({ types: [] }),
  options: () => Promise.resolve({ options: {} }),
  prompt: () => Promise.resolve({ prompt_template: '' }),
  scenarios: () => Promise.resolve({ scenarios: [] }),
}

// ============ 导出 API（返回文件流，触发浏览器下载） ============
export const exportApi = {
  docx: (jobId) => _downloadExport('/export/docx', jobId, 'docx'),
  pdf: (jobId) => _downloadExport('/export/pdf', jobId, 'pdf'),
  html: (jobId) => _downloadExport('/export/html', jobId, 'html'),
  txt: (jobId) => _downloadExport('/export/txt', jobId, 'txt'),
  // 批量导出：多个任务的结果打包为 ZIP
  batch: (jobIds) => _downloadBlob('/export/batch', { job_ids: jobIds }),
}

// 导出专用：POST返回blob文件流，触发浏览器下载（文件名取自 content-disposition）
async function _downloadBlob(endpoint, body) {
  const resp = await api.post(endpoint, body, {
    responseType: 'blob',
    timeout: 120000,
  })
  // 从response header提取文件名
  const contentDisposition = resp.headers?.['content-disposition']
  let filename = 'result.bin'
  if (contentDisposition) {
    const match = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/)
    if (match && match[1]) {
      filename = match[1].replace(/['"]/g, '')
      // 处理URL编码的中文文件名
      try { filename = decodeURIComponent(filename) } catch { /* 非法编码保持原样 */ }
    }
  }
  // 创建下载链接
  const blob = new Blob([resp.data])
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  window.URL.revokeObjectURL(url)
  return { success: true, filename }
}

async function _downloadExport(endpoint, jobId, format) {
  return _downloadBlob(endpoint, { job_id: jobId, format })
}

// ============ 翻译 API ============
export const translateApi = {
  langs: () => api.get('/translate/langs'),
  bilingual: (text, targetLang, model, jobId) =>
    api.post('/translate/bilingual', {
      text,
      target_lang: targetLang,
      model: model || undefined,
      job_id: jobId || undefined,
    }),
}

// ============ Token 统计 API ============
export const tokenApi = {
  usage: () => api.get('/token-usage'),
  history: (limit = 30) => api.get('/token-usage/history', { params: { limit } }),
}

// ============ 结果编辑 API ============
export const resultApi = {
  editResult: (jobId, resultText) =>
    api.post(`/jobs/${jobId}/edit-result`, { result_text: resultText }),
}

// ============ B站 API ============
export const bilibiliApi = {
  qr: () => api.get('/bilibili/qr'),
  qrCheck: (qrcodeKey) => api.get('/bilibili/qr/check', { params: { qrcode_key: qrcodeKey } }),
  status: () => api.get('/bilibili/status'),
  logout: () => api.post('/bilibili/logout'),
}

// ============ 下载 API ============
export const downloadApi = {
  file: (path) => api.get('/download/file', { params: { path } }),
  view: (path) => api.get('/download/view', { params: { path } }),
}

// ============ 模板管理 API ============
export const templatesApi = {
  list: () => api.get('/templates'),
  create: (data) => api.post('/templates', data),
  update: (id, data) => api.put(`/templates/${id}`, data),
  delete: (id) => api.delete(`/templates/${id}`),
}

// ============ 追问聊天 API（多会话） ============
// sessionId 必填：每个视频下可有多条独立对话线程
export const chatApi = {
  // 会话列表（含消息数）
  sessions: (jobId) => api.get(`/jobs/${jobId}/chat/sessions`),
  // 新建会话（body: { title? }）
  createSession: (jobId, data) => api.post(`/jobs/${jobId}/chat/sessions`, data),
  // 重命名会话（body: { title }）
  renameSession: (jobId, sessionId, data) => api.put(`/jobs/${jobId}/chat/sessions/${sessionId}`, data),
  // 删除整个会话（含消息）
  deleteSession: (jobId, sessionId) => api.delete(`/jobs/${jobId}/chat/sessions/${sessionId}`),
  // 清空会话内的消息（保留会话本身）
  clearSessionMessages: (jobId, sessionId) => api.delete(`/jobs/${jobId}/chat/sessions/${sessionId}/messages`),
  // 某会话的消息历史
  sessionHistory: (jobId, sessionId) => api.get(`/jobs/${jobId}/chat/sessions/${sessionId}/history`),
  // 删除单条（user/assistant）——按 msg_id 全局唯一
  deleteMessage: (jobId, msgId) => api.delete(`/jobs/${jobId}/chat/${msgId}`),
  // 删除从该条（含）之后的所有消息
  deleteFrom: (jobId, msgId) => api.delete(`/jobs/${jobId}/chat/from/${msgId}`),
  // SSE 流式追问：返回 Promise；通过 onEvent/onEnd/onError 回调驱动 UI
  // handlers: { signal, onEvent(ev), onEnd(aborted?), onError(err) }
  streamChat: (jobId, sessionId, { content, model, enableSearch }, handlers) => {
    const { signal, onEvent, onEnd, onError } = handlers
    return fetch(`/api/jobs/${jobId}/chat/sessions/${sessionId}/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, model: model || undefined, enable_search: !!enableSearch }),
      signal,
    }).then(async (resp) => {
      if (!resp.ok) {
        let msg = `请求失败 (${resp.status})`
        try { const j = await resp.json(); msg = j.detail || msg } catch { /* 响应非 JSON 时沿用默认错误文案 */ }
        throw new Error(msg)
      }
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        let idx
        while ((idx = buf.indexOf('\n\n')) !== -1) {
          const chunk = buf.slice(0, idx)
          buf = buf.slice(idx + 2)
          const dataLine = chunk.split('\n').find((l) => l.startsWith('data:'))
          if (!dataLine) continue
          const payload = dataLine.slice(5).trim()
          if (!payload) continue
          try { onEvent && onEvent(JSON.parse(payload)) } catch { /* 跳过无法解析的事件帧 */ }
        }
      }
      onEnd && onEnd(false)
    }).catch((e) => {
      if (e && e.name === 'AbortError') { onEnd && onEnd(true); return }
      onError && onError(e)
    })
  },
}

// ============ 网页搜索 API ============
export const searchApi = {
  search: (q, maxResults = 5, engine = 'pro') =>
    api.get('/search', { params: { q, max_results: maxResults, engine } }),
}

export default api
