/**
 * 表格列布局档位：按「表格容器实际宽度」判定，**不是** window.innerWidth。
 *
 * 为什么不用窗口宽度：侧栏（200px）+ `.page-container` 内边距 + 卡片内边距会吃掉
 * 200~300px，「窗口看着还行、表格容器已经很窄」是常态。Element Plus 在列宽总和
 * 超过容器时不会横向滚动，而是让 `fixed` 操作列 sticky 覆盖相邻列，表现为文字叠字
 * （历史页/模型页都踩过）。
 *
 * 三档语义：
 * - full    全列（含次要列）
 * - compact 隐藏次要列，仅留关键列
 * - narrow  次要信息并入主列第二行、操作列不再吸边（列宽总和最小，基本不需要横向滚动）
 */
export const HISTORY_COLUMN_BREAKPOINTS = { full: 890, compact: 540 }
export const MODELS_COLUMN_BREAKPOINTS = { full: 780, compact: 590 }

/**
 * @param {number} width 表格容器宽度（px）
 * @param {{full: number, compact: number}} breakpoints
 * @returns {'full'|'compact'|'narrow'}
 */
export function resolveTableLayout(width, breakpoints) {
  const w = Number(width)
  // 宽度未知（首帧、环境不支持 ResizeObserver）时按 compact 处理：宁可少显示次要列，也不叠字
  if (!Number.isFinite(w) || w <= 0) return 'compact'
  if (w >= breakpoints.full) return 'full'
  if (w >= breakpoints.compact) return 'compact'
  return 'narrow'
}
