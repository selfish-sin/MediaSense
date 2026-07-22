export const ACTIVE_JOB_STATUSES = Object.freeze([
  'pending',
  'downloading',
  'transcribing',
  'preview',
  'summarizing',
])

export function getJobActionModel(status) {
  if (status === 'preview') {
    return { primary: 'restore', primaryLabel: '继续确认', canCancel: true }
  }
  if (ACTIVE_JOB_STATUSES.includes(status)) {
    return { primary: 'restore', primaryLabel: '查看进度', canCancel: true }
  }
  if (status === 'completed') {
    return { primary: 'result', primaryLabel: '查看结果', secondary: 'rerun', secondaryLabel: '重新处理' }
  }
  if (status === 'failed' || status === 'cancelled') {
    return { primary: 'resume', primaryLabel: '断点续传', secondary: 'forceRetry', secondaryLabel: '从头重试' }
  }
  return { primary: 'detail', primaryLabel: '查看详情' }
}

export function buildRestoreRoute(jobId) {
  return { path: '/', query: { restore: jobId } }
}

export function buildRerunRoute(jobId) {
  return { path: '/', query: { rerun: jobId } }
}
