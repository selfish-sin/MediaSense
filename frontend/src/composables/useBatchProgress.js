import { ref, onScopeDispose, onActivated, onDeactivated } from 'vue'
import { jobsApi } from '../utils/api'
import socket, { onBatchProgress, onSocketConnect, onSocketDisconnect } from '../utils/socket'

/**
 * 批量任务进度：batch_progress socket 推送为主，socket 断开时降级 10s 轮询，
 * 重连后补拉一次对齐状态。调用即绑定 socket 生命周期（组件销毁自动解绑）。
 */
export function useBatchProgress() {
  const batchResult = ref(null)  // {batch_id, count, skipped, summary}
  let batchTimer = null
  const socketUnsubs = []

  function applyBatchSummary(summary) {
    // 只接受当前批次的推送，防止串台
    if (!batchResult.value || !summary || summary.batch_id !== batchResult.value.batch_id) return
    batchResult.value.summary = summary
  }

  async function refreshBatchStatus() {
    if (!batchResult.value?.batch_id) return
    try {
      const s = await jobsApi.batchStatus(batchResult.value.batch_id)
      applyBatchSummary(s)
    } catch (err) {
      console.error('查询批量进度失败:', err)
    }
  }

  // 降级轮询定时器：仅在 socket 断开期间兜底
  function startBatchPolling() {
    stopBatchPolling()
    batchTimer = setInterval(refreshBatchStatus, 10000)
  }
  function stopBatchPolling() {
    if (batchTimer) { clearInterval(batchTimer); batchTimer = null }
  }

  // socket 连接状态变化时切换数据源（推送↔轮询）
  function syncWithSocket(connected) {
    const active = !!batchResult.value && !batchResult.value.summary?.all_done
    if (!active) { stopBatchPolling(); return }
    if (connected) {
      stopBatchPolling()
      refreshBatchStatus()  // 重连补拉，弥补断线期间错过的推送
    } else {
      startBatchPolling()
    }
  }

  function bindSocket() {
    socketUnsubs.push(onBatchProgress(applyBatchSummary))
    socketUnsubs.push(onSocketConnect(() => syncWithSocket(true)))
    socketUnsubs.push(onSocketDisconnect(() => syncWithSocket(false)))
    syncWithSocket(socket.connected)
  }
  function unbindSocket() {
    socketUnsubs.forEach(off => off())
    socketUnsubs.length = 0
  }

  bindSocket()
  onDeactivated(stopBatchPolling)
  onActivated(() => { syncWithSocket(socket.connected) })
  onScopeDispose(() => { unbindSocket(); stopBatchPolling() })

  return {
    batchResult, applyBatchSummary, refreshBatchStatus,
    startBatchPolling, stopBatchPolling, syncWithSocket,
  }
}
