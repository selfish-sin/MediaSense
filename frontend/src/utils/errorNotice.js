/**
 * 连续失败提示（防刷屏）：连续 N 次失败只提示一次，成功后计数重置。
 * 用于后台静默拉取（日志/Token 统计/状态检查等）——既不静默吞错，也不每次失败刷屏。
 */
import { ElMessage } from 'element-plus'

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
