/**
 * 失败原因展示（纯函数，无 Vue 依赖）
 *
 * 背景（2026-10-08）：列表接口早就返回 `error`（db/job_repo.py 的 list_columns /
 * _QUEUE_COLUMNS 都含它），但历史表格与队列行都没渲染——失败任务必须点进详情
 * 才知道为什么失败。这里给两处共用一个格式化入口。
 */

/** 没有 error 时的回退文案（区别于「有原因但为空」） */
export const NO_FAILURE_DETAIL = '失败（无错误详情）'

/** 多余空白压平（DB 里 error 常带换行/缩进，直接塞进表格会把行撑高） */
function normalize(text) {
  return String(text ?? '')
    .replace(/\s+/g, ' ')
    .trim()
}

/**
 * 取失败任务的一行原因
 * @param {object} job 任务对象（列表行/队列行，含 status 与 error）
 * @param {number} maxLen 展示最大长度（超出截断并加省略号）
 * @returns {string} 空串表示「该任务不是失败态，无需展示」
 */
export function failureLabel(job, maxLen = 60) {
  if (!job || job.status !== 'failed') return ''
  const text = normalize(job.error)
  const limit = Number(maxLen) > 0 ? Number(maxLen) : 60
  if (!text) return NO_FAILURE_DETAIL
  if (text.length <= limit) return text
  return `${text.slice(0, limit)}…`
}

/** title 悬停用全文（截断前），无原因时给空串（避免 tooltip 显示空气泡） */
export function failureTitle(job) {
  if (!job || job.status !== 'failed') return ''
  return normalize(job.error)
}