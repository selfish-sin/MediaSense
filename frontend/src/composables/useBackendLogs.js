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
      const lines = res.lines || []
      const prev = backendLogs.value
      if (!prev.length) {
        // 首次拉取：直接展示当前窗口
        backendLogs.value = lines
      } else {
        // 尾部同步：以旧窗口末行为锚点，定位新窗口中其后缀新增。
        // （后端 /api/logs 是尾部回读，无法提供全文件行数，
        //  此前的 total 差量增量在 total 缺失后彻底失效）
        const lastPrev = prev[prev.length - 1]
        const idx = lines.lastIndexOf(lastPrev)
        if (idx === -1) {
          // 旧末行已滚出 200 行窗口（日志爆发）或日志文件被清：
          // 回退全量重载，禁止静默丢更新
          await loadAllHistory()
          return
        }
        if (idx < lines.length - 1) {
          backendLogs.value = prev.concat(lines.slice(idx + 1))
          if (backendLogs.value.length > 1000) backendLogs.value = backendLogs.value.slice(-1000)
        }
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
      backendLogs.value = res.lines || []
      scrollToBottom()
    } catch {
      logFetchFailures++
      if (logFetchFailures === 3) {
        ElMessage.warning('系统日志加载失败，请检查后端服务是否正常')
      }
    }
  }

  function clearLogs() { backendLogs.value = [] }
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
