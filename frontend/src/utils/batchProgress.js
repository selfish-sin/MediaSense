// 批量任务进度辅助：从批次子任务列表里挑选"当前进行中"的任务。
// 批量严格串行（backend `_run_batch_sequential`），任何时刻至多一个子任务在跑，
// 因此"第一个未终态任务"即当前任务（未启动的排在后面，状态为 pending）。

export const TERMINAL_JOB_STATUSES = Object.freeze(['completed', 'failed', 'cancelled'])

export function isTerminalStatus(status) {
  return TERMINAL_JOB_STATUSES.includes(status)
}

/**
 * 选出应当在进度详情里展示的子任务 id。
 * - 当前 id 仍在批次内且未终态 → 保持不变（避免高频刷新时闪烁）
 * - 否则取第一个未终态任务（串行下即正在跑/下一个待跑的任务）
 * - 全部终态 → 保留当前 id，让最后一个任务的进度与日志留在界面上
 * - 批次内找不到当前 id 且全部终态 → null（交由调用方隐藏详情块）
 */
export function pickActiveJobId(jobs, currentId) {
  const list = Array.isArray(jobs) ? jobs : []
  const cur = list.find(j => j.id === currentId)
  if (cur && !isTerminalStatus(cur.status)) return cur.id
  const next = list.find(j => !isTerminalStatus(j.status))
  if (next) return next.id
  return cur ? cur.id : null
}
