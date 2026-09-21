import { ref, onScopeDispose, onActivated, onDeactivated } from 'vue'
import { jobsApi } from '../utils/api'
import socket, { onProgress, onLog, onSocketConnect, onSocketDisconnect } from '../utils/socket'

/**
 * 处理队列（串行）：`GET /api/jobs/queue` → `{concurrency, running[], pending[], total}`
 *
 * 数据源是后端 DB 的任务状态，不另造内存队列 —— 刷新/切页后队列仍在。
 * 刷新时机（与 `useBatchProgress` 同构，见 .trellis/spec/frontend/index.md）：
 *   - 提交任务后由父组件主动调 `refreshQueue()`
 *   - socket `progress`/`log` 事件触发（1s debounce，任务开始/结束都会推进队列）
 *   - socket 断开时降级 10s 轮询，重连补拉一次
 */
export function useJobQueue() {
  const queue = ref(null)
  let pollTimer = null
  let debounceTimer = null
  const socketUnsubs = []

  async function refreshQueue() {
    try {
      queue.value = await jobsApi.queue()
    } catch (err) {
      console.error('查询处理队列失败:', err)
    }
  }

  // 高频 progress/log 事件合并为一次查询（否则一个任务能触发上千次）
  function scheduleRefresh() {
    if (debounceTimer !== null) return
    debounceTimer = setTimeout(() => {
      debounceTimer = null
      refreshQueue()
    }, 1000)
  }

  function startPolling() {
    stopPolling()
    pollTimer = setInterval(refreshQueue, 10000)
  }
  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  }
  function stopDebounce() {
    if (debounceTimer !== null) { clearTimeout(debounceTimer); debounceTimer = null }
  }

  function syncWithSocket(connected) {
    if (connected) {
      stopPolling()
      refreshQueue()  // 重连补拉，弥补断线期间错过的状态变化
    } else {
      startPolling()
    }
  }

  socketUnsubs.push(onProgress(scheduleRefresh))
  socketUnsubs.push(onLog(scheduleRefresh))
  socketUnsubs.push(onSocketConnect(() => syncWithSocket(true)))
  socketUnsubs.push(onSocketDisconnect(() => syncWithSocket(false)))
  onDeactivated(stopPolling)
  onActivated(() => syncWithSocket(socket.connected))
  onScopeDispose(() => {
    socketUnsubs.forEach(off => off())
    socketUnsubs.length = 0
    stopPolling()
    stopDebounce()
  })

  refreshQueue()

  // syncWithSocket 一并暴露：与 useBatchProgress 一致，便于测试直接驱动连接状态分支
  return { queue, refreshQueue, syncWithSocket }
}
