/** 任务状态：全站统一文案与标签色 */

export const JOB_STATUS_LABELS = Object.freeze({
  idle: '空闲',
  pending: '排队中',
  // ★ 纯前端展示态：任务已占用处理槽位，但 DB status 仍停在 pending
  //   （字幕/下载阶段不更新 status）——队列面板用它区分「排队中」与「处理中」
  processing: '处理中',
  downloading: '下载中',
  transcribing: '转录中',
  preview: '待确认',
  summarizing: 'AI处理中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
})

/** Element Plus el-tag type */
export const JOB_STATUS_TAG_TYPES = Object.freeze({
  idle: 'info',
  pending: 'info',
  processing: 'warning',
  downloading: 'warning',
  transcribing: 'warning',
  preview: 'warning',
  summarizing: 'warning',
  completed: 'success',
  failed: 'danger',
  cancelled: 'info',
})

export function jobStatusLabel(status) {
  if (!status) return ''
  return JOB_STATUS_LABELS[status] || status
}

export function jobStatusTagType(status) {
  if (!status) return 'info'
  return JOB_STATUS_TAG_TYPES[status] || 'info'
}

/** 是否处于「进行中」（含排队、预览） */
export function isActiveJobStatus(status) {
  return ['pending', 'downloading', 'transcribing', 'preview', 'summarizing'].includes(status)
}
