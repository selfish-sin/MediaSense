<script setup>
/**
 * 任务进度卡片（HomeView 拆分 slice-4，2026-09-12）
 * - 自持：socket onProgress/onLog 监听 + 展开状态；进度与日志数据来自 jobStore
 * - 进度详情（进度条/阶段/日志）渲染交给 JobProgressDetail（与批量子任务共用）
 * - expose reveal()：父组件提交任务后调用（展开本卡并滚动到视口，工作台第一眼）
 * - 生命周期注意：本组件在 v-if 下挂载/卸载（无任务时卸载 → 监听随之注销），
 *   与 HomeView 原 socketUnsubs（onUnmounted 清理）语义一致；keep-alive 切页不卸载。
 */
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useJobStore } from '../stores/jobStore'
import { onProgress, onLog } from '../utils/socket'
import { jobStatusLabel, jobStatusTagType } from '../utils/jobStatus'
import { jobsApi } from '../utils/api'
import { isDialogCancel } from '../utils/errorNotice'
import { useJobQueue } from '../composables/useJobQueue'
import JobProgressDetail from './JobProgressDetail.vue'
import JobQueueList from './JobQueueList.vue'

const jobStore = useJobStore()

// 处理队列（串行：running + pending），数据源 GET /api/jobs/queue
const { queue, refreshQueue } = useJobQueue()

/** 取消排队中的任务（pending 也支持取消：process_job 顶部会检查取消标记） */
async function handleQueueCancel(jobId) {
  try {
    await ElMessageBox.confirm(
      '取消该任务？正在排队中的任务会直接跳过，不占用后续顺序。',
      '取消排队任务',
      { type: 'warning', confirmButtonText: '确认取消', cancelButtonText: '继续等待' }
    )
  } catch (err) {
    if (!isDialogCancel(err)) ElMessage.error(err?.message || err)
    return
  }
  try {
    await jobsApi.cancel(jobId)
    ElMessage.success('已取消')
    refreshQueue()
  } catch (err) {
    ElMessage.error(err?.message || err)
  }
}

/** 切换到队列中另一个任务的进度视图（用户点击队列项） */
async function handleQueueSelect(jobId) {
  if (!jobId || jobId === jobStore.currentJobId) return
  manualSwitchLock.value = true  // 用户手动选择优先，暂停自动接力
  try {
    await jobStore.loadJob(jobId)
  } catch {
    ElMessage.error('切换任务失败，请稍后重试')
  }
}

// ── 任务进度日志（默认展开，当作进度条使用） ──
const showJobProgress = ref(true)
const cancelling = ref(false)

const isProcessing = computed(() => jobStore.isProcessing)

/** 比队列快照「处理中」更细的阶段态（由 progress 事件 step 推出） */
const FINE_GRAINED_STATUSES = ['downloading', 'transcribing', 'summarizing']
/** 终态：当前跟踪任务已跑到终态且队列里还有别的任务在跑时，自动接力跟踪 */
const TERMINAL_STATUSES = ['completed', 'failed', 'cancelled', 'idle']

/**
 * 日志/进度接力：串行队列下前一个任务终态后，自动切到队列里正在处理的任务。
 * ★ 否则会出现「队列显示 A 在跑、日志面板停在已完成的 B」的错位观感。
 * 用户手动点选队列项后加锁，不再自动抢切（manualSwitchLock）。
 */
const manualSwitchLock = ref(false)
watch(
  () => [queue.value?.running?.[0]?.id, jobStore.currentJobId, jobStore.jobStatus, jobStore.isProcessing],
  async ([runningId, currentId, status, processing]) => {
    const targetId = runningId || ''
    if (manualSwitchLock.value || !targetId || targetId === currentId) return
    if (processing || !TERMINAL_STATUSES.includes(status || 'idle')) return
    try {
      await jobStore.loadJob(targetId)
    } catch {
      // 切换失败不影响主流程（用户可手动点选队列项）
    }
  }
)
// 新任务被跟踪（提交后 pending）→ 解除手动锁，恢复自动接力
watch(
  () => [jobStore.currentJobId, jobStore.jobStatus],
  ([, status]) => { if (status === 'pending') manualSwitchLock.value = false }
)

/**
 * 当前任务的**真实**状态：优先取队列快照（DB 权威），拿不到再回落 jobStore。
 * jobStore.jobStatus 在提交后停在 'pending'（socket 进度事件不带 status），
 * 会出现「顶部标签：排队中 / 队列面板：AI处理中」的自相矛盾。
 */
const liveJobStatus = computed(() => {
  const id = jobStore.currentJobId
  if (id && queue.value) {
    const inRunning = (queue.value.running || []).find(j => j.id === id)
    // 已占用槽位但 DB status 还停在 pending（字幕/下载阶段）→ 展示「处理中」
    if (inRunning) {
      const snapshotStatus = inRunning.status === 'pending' ? 'processing' : inRunning.status
      // 队列快照只给得出「处理中」这种模糊态时，回落到 jobStore 的细分阶段
      // （下载/转录/AI），否则顶部标签会退化成笼统的「处理中」
      if (snapshotStatus === 'processing' && FINE_GRAINED_STATUSES.includes(jobStore.jobStatus)) {
        return jobStore.jobStatus
      }
      return snapshotStatus
    }
    const inPending = (queue.value.pending || []).find(j => j.id === id)
    if (inPending?.status) return inPending.status
  }
  return jobStore.jobStatus
})

/** 取消当前任务：终止后端处理步骤，并把本地状态同步为已取消 */
async function handleCancel() {
  if (!jobStore.currentJobId) return
  try {
    await ElMessageBox.confirm(
      '取消将终止后端所有处理步骤（下载/转录/AI 加工），任务状态变为「已取消」。确认？',
      '取消任务',
      { type: 'warning', confirmButtonText: '确认取消', cancelButtonText: '继续等待' }
    )
  } catch (err) {
    if (!isDialogCancel(err)) ElMessage.error(err?.message || err)
    return
  }
  cancelling.value = true
  try {
    await jobsApi.cancel(jobStore.currentJobId)
    jobStore.jobStatus = 'cancelled'
    jobStore.isProcessing = false
    jobStore.progress.msg = '任务已取消'
    ElMessage.success('任务已取消')
  } catch (err) {
    ElMessage.error(err?.message || err)
  } finally {
    cancelling.value = false
  }
}

let socketUnsubs = []

onMounted(() => {
  socketUnsubs.push(onProgress((data) => {
    if (data.job_id === jobStore.currentJobId) {
      jobStore.updateProgress(data)
    }
    // 批量面板进度由 batch_progress 推送驱动（终态触发），不再随 progress 高频刷新
  }))
  socketUnsubs.push(onLog((data) => {
    if (data.job_id === jobStore.currentJobId) {
      jobStore.addLog(data.msg)
      // 新进度日志到来时保持面板展开（滚动由 JobProgressDetail 负责）
      if (!showJobProgress.value) showJobProgress.value = true
    }
  }))
})

onUnmounted(() => {
  socketUnsubs.forEach(off => off())
  socketUnsubs = []
})

/** 展开并滚动到本卡（父组件提交任务后调用） */
function reveal() {
  showJobProgress.value = true
  nextTick(() => {
    document.getElementById('job-progress-section')?.scrollIntoView({
      behavior: 'smooth',
      block: 'start',
    })
  })
}
defineExpose({ reveal, refreshQueue })
</script>

<template>
  <el-card
    id="job-progress-section"
    v-if="isProcessing || jobStore.logs.length > 0 || jobStore.currentJobId || (queue && queue.total > 0)"
    class="wb-card progress-card"
    :class="{ 'progress-card--live': isProcessing }"
    shadow="never"
  >
    <template #header>
      <div class="progress-header">
        <div class="progress-header-left">
          <span class="wb-card-title progress-title">
            <span v-if="isProcessing" class="pulse-dot" />
            任务进度
          </span>
          <el-tag v-if="jobStore.currentJobId" size="small" type="info" effect="plain" class="mono-tag">
            {{ jobStore.currentJobId }}
          </el-tag>
          <el-tag v-if="liveJobStatus && liveJobStatus !== 'idle'" size="small" :type="jobStatusTagType(liveJobStatus)" effect="dark">
            {{ jobStatusLabel(liveJobStatus) }}
          </el-tag>
          <span v-if="jobStore.jobTitle" class="progress-job-title">
            {{ jobStore.jobTitle }}
          </span>
        </div>
        <div class="progress-header-right">
          <el-button
            v-if="isProcessing"
            size="small"
            type="danger"
            plain
            :loading="cancelling"
            @click="handleCancel"
          >
            取消任务
          </el-button>
          <el-button size="small" text @click="showJobProgress = !showJobProgress">
            {{ showJobProgress ? '收起' : '展开' }}
          </el-button>
        </div>
      </div>
    </template>

    <JobProgressDetail
      v-show="showJobProgress"
      :progress="jobStore.progress"
      :logs="jobStore.logs"
      :active="isProcessing"
      :job-status="jobStore.jobStatus"
      @clear="jobStore.clearLogs"
    />

    <!-- 串行队列：正在处理 + 排队中（受控，数据来自 useJobQueue） -->
    <JobQueueList
      :queue="queue"
      :current-job-id="jobStore.currentJobId"
      @cancel="handleQueueCancel"
      @select="handleQueueSelect"
    />
  </el-card>
</template>

<style scoped>
.progress-card {
  border: 1px solid var(--app-border) !important;
  background:
    linear-gradient(135deg, var(--app-progress-card-bg-a), var(--app-progress-card-bg-b)) !important;
}
.progress-card--live {
  border-color: rgba(var(--app-accent-1-rgb), 0.45) !important;
  box-shadow: 0 0 0 1px rgba(var(--app-accent-1-rgb), 0.15), 0 8px 28px rgba(var(--app-accent-1-rgb), 0.12);
}
.progress-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.progress-header-left,
.progress-header-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.progress-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.pulse-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #22c55e;
  box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.6);
  animation: pulse-ring 1.4s ease-out infinite;
}
@keyframes pulse-ring {
  0% { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.55); }
  70% { box-shadow: 0 0 0 8px rgba(34, 197, 94, 0); }
  100% { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0); }
}
.progress-job-title {
  font-size: 12px;
  color: var(--app-text-secondary);
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
