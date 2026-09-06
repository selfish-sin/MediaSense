import { ref, watch, nextTick, onScopeDispose, onActivated, onDeactivated } from 'vue'
import { ElMessage } from 'element-plus'
import { jobsApi } from '../utils/api'

/**
 * 后端系统日志面板：增量拉取 + 超界全量回退 + 展开时定时轮询。
 * 在组件 setup 中调用即自动接管生命周期：
 * - watch 展开/收起启停轮询
 * - keep-alive 失活暂停、激活恢复
 * - 组件销毁清理定时器
 */
export function useBackendLogs() {
  // 默认收起：系统日志是排障用的次要面板，展开时才轮询后端文件
  const showBackendLogs = ref(false)
  const backendLogs = ref([])
  const autoScroll = ref(true)
  let logTimer = null
  let lastTotal = 0
  // 连续拉取失败计数：≥3 次只提示一次，避免定时器刷屏；成功即重置
  let logFetchFailures = 0

  function scrollToBottom() {
    nextTick(() => {
      const el = document.getElementById('log-panel')
      if (el) el.scrollTop = el.scrollHeight
    })
  }

  async function fetchLogs() {
    try {
      const res = await jobsApi.getLogs(200)
      logFetchFailures = 0
      if (res.total < lastTotal) {
        backendLogs.value = []
        lastTotal = 0
      }
      const delta = res.total - lastTotal
      if (delta > 200) {
        // 增量超出单次拉取上限（日志爆发增长）：回退全量重载，禁止静默丢更新
        await loadAllHistory()
        return
      }
      if (delta > 0) {
        backendLogs.value = backendLogs.value.concat(res.lines.slice(-delta))
        lastTotal = res.total
        if (backendLogs.value.length > 1000) backendLogs.value = backendLogs.value.slice(-1000)
      }
      if (autoScroll.value) scrollToBottom()
    } catch {
      logFetchFailures++
      if (logFetchFailures === 3) {
        ElMessage.warning(`系统日志拉取失败（已连续 ${logFetchFailures} 次），稍后自动重试`)
      }
    }
  }

  async function loadAllHistory() {
    try {
      const res = await jobsApi.getLogs(2000)
      logFetchFailures = 0
      backendLogs.value = res.lines
      lastTotal = res.total
      scrollToBottom()
    } catch {
      logFetchFailures++
      if (logFetchFailures === 3) {
        ElMessage.warning('系统日志加载失败，请检查后端服务是否正常')
      }
    }
  }

  function clearLogs() { backendLogs.value = []; lastTotal = 0 }
  function scrollLogTop() {
    document.getElementById('log-panel')?.scrollTo({ top: 0, behavior: 'smooth' })
  }
  function refreshLogs() { fetchLogs() }

  function startPolling() {
    fetchLogs()
    if (!logTimer) logTimer = setInterval(fetchLogs, 3000)
  }
  function stopPolling() {
    if (logTimer) { clearInterval(logTimer); logTimer = null }
  }

  // 展开时轮询；收起即停
  watch(showBackendLogs, (on) => {
    if (on) startPolling()
    else stopPolling()
  }, { immediate: true })

  onDeactivated(stopPolling)
  onActivated(() => { if (showBackendLogs.value) startPolling() })
  onScopeDispose(stopPolling)

  return {
    showBackendLogs, backendLogs, autoScroll,
    fetchLogs, loadAllHistory, clearLogs, scrollLogTop, refreshLogs,
    startPolling, stopPolling,
  }
}
