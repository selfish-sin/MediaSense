import { io } from 'socket.io-client'

// 创建 Socket.IO 连接
// 开发时通过 Vite proxy 的 /ws 路径代理到后端
// 生产时同域直接连接
const socket = io({
  path: '/ws/socket.io',
  transports: ['websocket', 'polling'],
  autoConnect: true,
  reconnection: true,
  reconnectionDelay: 1000,
  reconnectionAttempts: 10,
})

// 连接状态
socket.on('connect', () => {
  console.log('[Socket.IO] 已连接:', socket.id)
})

socket.on('disconnect', () => {
  console.log('[Socket.IO] 已断开')
})

socket.on('connect_error', (err) => {
  console.error('[Socket.IO] 连接错误:', err.message)
})

// ============ 事件监听辅助函数 ============
// ★ 每个 onXxx 返回一个解绑函数，调用方在卸载时只移除自己注册的 handler，
//    避免使用全局 removeAllListeners 误清其他组件（如 keep-alive 的 HomeView）的监听。

export function onAiDone(callback) {
  const handler = (data) => callback(data)
  socket.on('done', handler)
  return () => socket.off('done', handler)
}

export function onProgress(callback) {
  const handler = (data) => callback(data)
  socket.on('progress', handler)
  return () => socket.off('progress', handler)
}

export function onLog(callback) {
  const handler = (data) => callback(data)
  socket.on('log', handler)
  return () => socket.off('log', handler)
}

export function onAiToken(callback) {
  const handler = (data) => callback(data)
  socket.on('ai_token', handler)
  return () => socket.off('ai_token', handler)
}

export function onAiThinking(callback) {
  const handler = (data) => callback(data)
  socket.on('ai_thinking', handler)
  return () => socket.off('ai_thinking', handler)
}

export function onPreview(callback) {
  const handler = (data) => callback(data)
  socket.on('preview', handler)
  return () => socket.off('preview', handler)
}

// ============ 事件发送辅助函数 ============

export function continueSummary(jobId, editedText = '') {
  socket.emit('continue_summary', { job_id: jobId, edited_text: editedText })
}

export function cancelPreview(jobId) {
  socket.emit('cancel_preview', { job_id: jobId })
}

export function cancelJob(jobId) {
  socket.emit('cancel_job', { job_id: jobId })
}

export function editTranscript(jobId, text) {
  socket.emit('edit_transcript', { job_id: jobId, text })
}

export function sendChatMessage(message, jobId = null) {
  socket.emit('chat_message', { message, job_id: jobId })
}

// ============ 移除监听 ============
// ⚠ 已废弃：会清掉该事件的所有 handler（包括其他组件的），跨组件会误清。
//    请改用各 onXxx 返回的解绑函数。保留仅为向后兼容。
export function removeAllListeners() {
  socket.removeAllListeners('progress')
  socket.removeAllListeners('log')
  socket.removeAllListeners('ai_token')
  socket.removeAllListeners('done')
  socket.removeAllListeners('preview')
}

export default socket
