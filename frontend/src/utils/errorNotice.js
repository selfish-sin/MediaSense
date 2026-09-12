/**
 * 连续失败提示（防刷屏）：连续 N 次失败只提示一次，成功后计数重置。
 * 用于后台静默拉取（日志/Token 统计/状态检查等）——既不静默吞错，也不每次失败刷屏。
 */
import { ElMessage } from 'element-plus'

/**
 * Element Plus 确认框的关闭原因判断：'cancel'（点取消）/'close'（点 X 或 ESC）
 * 都是"用户主动放弃"，不是业务错误——catch 里用它决定是否需要给错误提示。
 */
export function isDialogCancel(err) {
  return err === 'cancel' || err === 'close'
}

export function createFailureNotifier(message, threshold = 3) {
  let failures = 0
  return {
    ok() { failures = 0 },
    fail() {
      failures++
      if (failures === threshold) {
        ElMessage.warning(message)
      }
    },
  }
}
