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
  // ★ 必须异步：responseType='blob' 时错误体也是 Blob，
  //   直接读 error.response.data.detail 恒为 undefined → 只能显示 axios 通用串
  //   「Request failed with status code 4xx」，后端给的 detail 文案整条丢掉。
  async (error) => {
    console.error('API Error:', error)
    let msg = ''
    try {
      msg = await resolveErrorMessage(error)
    } catch {
      // 错误体读不出来（blob 读取失败等）→ 回落 axios 通用文案，绝不吞错
      msg = error.message || '请求失败'
    }
    return Promise.reject(new Error(msg))
  }
)

// 后端 detail → 可读文案（FastAPI 422 的 detail 是数组，逐条取 msg）
function formatDetail(detail) {
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
  return detail || ''
}

// 错误体可能是 JSON 对象，也可能是包着 JSON 的 Blob（blob 响应），
// 再兜一层纯文本；都取不到才回落 axios 的通用文案（错误可见、不臆造）
async function resolveErrorMessage(error) {
  const data = error.response?.data
  let detail = data?.detail
  if (detail === undefined && typeof Blob !== 'undefined' && data instanceof Blob) {
    const text = await data.text()
    try {
      detail = JSON.parse(text)?.detail
    } catch {
      detail = text.slice(0, 200)
    }
  }
  return formatDetail(detail) || error.message || '请求失败'
}

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
  // 处理队列快照（running + pending，FIFO）：串行队列的单一数据源
  queue: () => api.get('/jobs/queue'),
  searchTranscript: (q, limit = 50, maxMatchesPerJob = 5) =>
    api.get('/jobs/search/transcript', { params: { q, limit, max_matches_per_job: maxMatchesPerJob } }),
  summarize: (jobId, data) => api.post(`/jobs/${jobId}/summarize`, data),
  screenshots: (jobId, frameCount = 10) => api.get(`/jobs/${jobId}/screenshots`, { params: { frame_count: frameCount } }),
  getSummaryHistory: (jobId) => api.get(`/jobs/${jobId}/summary-history`),
  getSummaryVersion: (jobId, version) => api.get(`/jobs/${jobId}/summary-history/${version}`),
  generateTags: (jobId, model) => api.post(`/jobs/${jobId}/generate-tags`, model ? { model } : {}),
  updateTags: (jobId, tags) => api.put(`/jobs/${jobId}/tags`, { tags }),
  // version 可选：指定按哪个总结版本生成（历史详情页查看 vN 时必须带上，否则出的是最新版）
  generateMindmap: (jobId, model, version) =>
    api.post(`/jobs/${jobId}/mindmap`, { ...(model ? { model } : {}), ...(version ? { version } : {}) }),
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

// ============ ASR（转录）API ============
// status：工作台消费的引擎可用性/默认值（轻量，不含本地磁盘信息）
// overview：模型管理「ASR 模型」页总览（provider 列表 + 本地模型 + 设置，一次拿全）
export const asrApi = {
  status: () => api.get('/asr/status'),
  overview: () => api.get('/asr/overview'),
  addProvider: (data) => api.post('/asr/providers', data),
  updateProvider: (key, data) => api.put(`/asr/providers/${encodeURIComponent(key)}`, data),
  deleteProvider: (key) => api.delete(`/asr/providers/${encodeURIComponent(key)}`),
  // 真实调用一次云端 ASR 验证端点/密钥/模型链路（HTTP 200 + success 字段）
  testProvider: (key) => api.post(`/asr/providers/${encodeURIComponent(key)}/test`),
  // 默认引擎（default_engine 传空串 = 清除用户设置，恢复自动推荐）/ Whisper 档位 / 设备
  saveSettings: (data) => api.post('/asr/settings', data),
  // 本地引擎生命周期：路径按引擎 key 通用（引擎清单来自 overview.local.engines）
  engineDownloadState: (engine) => api.get(`/asr/local/${encodeURIComponent(engine)}/download`),
  startEngineDownload: (engine) => api.post(`/asr/local/${encodeURIComponent(engine)}/download`),
  cancelEngineDownload: (engine) => api.post(`/asr/local/${encodeURIComponent(engine)}/cancel`),
  removeEngine: (engine) => api.delete(`/asr/local/${encodeURIComponent(engine)}`),
  removeWhisper: (size) => api.delete(`/asr/local/whisper/${encodeURIComponent(size)}`),
}

// ============ 模型管理 API ============
export const modelsApi = {
  list: () => api.get('/models'),
  add: (data) => api.post('/models', data),
  update: (key, data) => api.put(`/models/${key}`, data),
  delete: (key) => api.delete(`/models/${key}`),
  test: (data) => api.post('/models/test', data),
  // 一键体检：并发探测全部 LLM 模型，返回每模型连通性 + 失败类别 + 修法
  healthCheck: () => api.post('/models/health-check'),
  setDefault: (key) => api.post('/models/default', { key }),
  // AI 调用兜底链：GET 读配置，POST 保存（顺序即优先级）
  // 用 POST 而非 PUT：后端存在 `PUT /models/{key}`，子路径会被吞
  getFallbackChain: () => api.get('/models/fallback-chain'),
  setFallbackChain: (chain, enabled) => api.post('/models/fallback-chain', { chain, enabled }),
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
  // 一键恢复备份：version = 'original'（最初版本）| 'previous'（上一版本）
  restoreOverride: (dimKey, modKey, version) =>
    api.post(`/summary/overrides/${dimKey}/${modKey}/restore`, { version }),
  // 兼容旧 API（保留空实现）
  types: () => Promise.resolve({ types: [] }),
  options: () => Promise.resolve({ options: {} }),
  prompt: () => Promise.resolve({ prompt_template: '' }),
  scenarios: () => Promise.resolve({ scenarios: [] }),
}

// ============ 导出 API（返回文件流，触发浏览器下载） ============
export const exportApi = {
  // version 可选：传了就导出该历史版本（历史详情页正在查看 vN 时用），
  // 不传导出当前最新版——两条路径后端都支持，行为一致
  docx: (jobId, version) => _downloadExport('/export/docx', jobId, 'docx', version),
  pdf: (jobId, version) => _downloadExport('/export/pdf', jobId, 'pdf', version),
  html: (jobId, version) => _downloadExport('/export/html', jobId, 'html', version),
  txt: (jobId, version) => _downloadExport('/export/txt', jobId, 'txt', version),
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

async function _downloadExport(endpoint, jobId, format, version) {
  return _downloadBlob(endpoint, { job_id: jobId, format, ...(version ? { version } : {}) })
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
  // 切换变体组内生效版本（重生成历史版本）
  activateVariant: (jobId, msgId) => api.post(`/jobs/${jobId}/chat/variants/${msgId}/activate`),
  // 删除单条（user/assistant）——按 msg_id 全局唯一
  deleteMessage: (jobId, msgId) => api.delete(`/jobs/${jobId}/chat/${msgId}`),
  // 多选批量删除（body: { ids: [] }）：提问行=整轮（不级联后续轮次）、回答行=该条全部版本
  deleteMessages: (jobId, ids) => api.post(`/jobs/${jobId}/chat/messages/delete`, { ids }),
  // 删除从该条（含）之后的所有消息
  deleteFrom: (jobId, msgId) => api.delete(`/jobs/${jobId}/chat/from/${msgId}`),
  // SSE 流式追问：返回 Promise；通过 onEvent/onEnd/onError 回调驱动 UI
  // handlers: { signal, onEvent(ev), onEnd(aborted?), onError(err) }
  // searchMode: 'off' | 'strict'（严格依据材料） | 'broad'（广泛收集），见 utils/chatExport SEARCH_MODES
  // regenerate: true = 重新生成（不插新提问，回答作为新变体入组）
  // userMessageId: 重生成的目标轮（提问行 id，任意一轮都可）；不传 = 最后一轮
  streamChat: (jobId, sessionId, { content, model, searchMode, regenerate, userMessageId }, handlers) => {
    const { signal, onEvent, onEnd, onError } = handlers
    return fetch(`/api/jobs/${jobId}/chat/sessions/${sessionId}/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content,
        model: model || undefined,
        search_mode: searchMode || 'off',
        regenerate: !!regenerate,
        user_message_id: userMessageId || undefined,
      }),
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

// ★ 2026-10-08 删除 searchApi 与 /api/search：全前端零调用方，且后端该端点
//   search_web() 失败时静默返回空列表（死按钮风险面）。联网检索现在只有一条路：
//   追问面板的联网三态 → POST /api/jobs/{id}/chat/sessions/{sid}/stream（见 utils/api.js chatApi）。

export default api
