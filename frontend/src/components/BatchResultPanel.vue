<script setup>
/**
 * 批量任务进度面板（HomeView 拆分 slice-3，2026-09-12）
 * 受控组件：数据由父组件 batchResult（useBatchProgress）传入，操作以事件上抛，
 * 父组件保留刷新/跳转/子任务预览确认的实际逻辑。
 *
 * 2026-09-13：大进度条下方内嵌「当前任务」实时进度详情（进度条+阶段+日志），
 * 复用 JobProgressDetail（与单任务同一套视觉）。批量严格串行，任何时刻至多一个
 * 子任务在跑，故监听该子任务的 progress/log 推送并在其完成后自动切到下一个。
 */
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { jobStatusLabel, jobStatusTagType } from '../utils/jobStatus'
import { isTerminalStatus, pickActiveJobId } from '../utils/batchProgress'
import { onProgress, onLog } from '../utils/socket'
import JobProgressDetail from './JobProgressDetail.vue'

const props = defineProps({
  batchResult: { type: Object, required: true },
})
const emit = defineEmits(['refresh', 'view-job', 'confirm-preview', 'cancel-preview', 'cancel-job', 'cancel-batch'])

// ── 当前子任务（批量串行 → 至多一个未终态任务） ──
const jobs = computed(() => props.batchResult.summary?.jobs || [])
const batchJobIds = computed(() => new Set(jobs.value.map(j => j.id)))
const activeJobId = ref(null)
const activeProgress = ref({ step: '', pct: 0, msg: '' })
const activeLogs = ref([])
const listRef = ref(null)

const activeJob = computed(() => jobs.value.find(j => j.id === activeJobId.value) || null)
const activeJobDone = computed(() => isTerminalStatus(activeJob.value?.status))
const activeJobIndex = computed(() => jobs.value.findIndex(j => j.id === activeJobId.value) + 1)
// 未终态子任务（pending/running/preview…）可取消；有才显示「取消剩余」
const cancellableCount = computed(() => jobs.value.filter(j => !isTerminalStatus(j.status)).length)

function switchTo(jobId) {
  if (jobId === activeJobId.value) return
  activeJobId.value = jobId
  // 切任务即清屏：日志/进度是"当前任务"的，不能与上一个任务混在一起
  activeProgress.value = { step: '', pct: 0, msg: '' }
  activeLogs.value = []
}

// summary 更新（batch_progress 推送 / 轮询）后对齐当前任务：
// 已完成则顺延到下一个未终态任务；全部完成则保留最后一个（终态留屏）
watch(jobs, (list) => {
  const next = pickActiveJobId(list, activeJobId.value)
  if (next !== activeJobId.value) switchTo(next)
}, { immediate: true })

// 活动行滚进可视区（子任务多时列表会滚动）
watch(activeJobId, () => {
  nextTick(() => {
    listRef.value?.querySelector('.batch-job-row.active')?.scrollIntoView({ block: 'nearest' })
  })
})

/** 判断该子任务的事件是否该落到详情区；必要时把跟踪目标切过去 */
function shouldTrack(jobId) {
  if (!batchJobIds.value.has(jobId)) return false
  if (jobId === activeJobId.value) return true
  const job = jobs.value.find(j => j.id === jobId)
  if (!job || isTerminalStatus(job.status)) return false
  switchTo(jobId)
  return true
}

let socketUnsubs = []
onMounted(() => {
  socketUnsubs.push(onProgress((data) => {
    if (!shouldTrack(data.job_id)) return
    activeProgress.value = {
      step: data.step || '',
      pct: data.pct ?? 0,
      msg: data.msg || '',
    }
  }))
  socketUnsubs.push(onLog((data) => {
    if (!shouldTrack(data.job_id)) return
    activeLogs.value.push({ time: new Date().toLocaleTimeString(), msg: data.msg })
    if (activeLogs.value.length > 500) activeLogs.value = activeLogs.value.slice(-500)
  }))
})
onUnmounted(() => {
  socketUnsubs.forEach(off => off())
  socketUnsubs = []
})
</script>

<template>
  <div id="batch-progress-section" class="batch-panel">
    <div class="batch-panel-head">
      <span class="batch-panel-title">
        批量任务
        <code class="mono-tag">#{{ batchResult.batch_id }}</code>
        <span v-if="batchResult.summary" class="batch-panel-meta">共 {{ batchResult.summary.total }} 个</span>
      </span>
      <el-button size="small" text @click="emit('refresh')">刷新</el-button>
      <el-button
        v-if="cancellableCount > 0"
        size="small"
        text
        type="danger"
        @click="emit('cancel-batch')"
      >取消剩余（{{ cancellableCount }}）</el-button>
    </div>
    <template v-if="batchResult.summary">
      <el-progress
        :percentage="Math.round((batchResult.summary.completed + batchResult.summary.failed + batchResult.summary.cancelled) / batchResult.summary.total * 100)"
        :status="batchResult.summary.all_done ? 'success' : ''"
        class="batch-progress"
      />
      <div class="batch-stats">
        <el-tag size="small" type="success">完成 {{ batchResult.summary.completed }}</el-tag>
        <el-tag size="small" type="warning">进行中 {{ batchResult.summary.running }}</el-tag>
        <el-tag size="small" type="danger">失败 {{ batchResult.summary.failed }}</el-tag>
        <el-tag size="small" type="info" v-if="batchResult.summary.cancelled">取消 {{ batchResult.summary.cancelled }}</el-tag>
      </div>

      <!-- 当前任务：单任务那套进度界面（进度条 + 阶段 + 实时日志），随子任务完成自动切换 -->
      <div v-if="activeJob" class="batch-active-job">
        <div class="batch-active-head">
          <span class="batch-active-label">
            当前任务
            <span class="batch-active-index">{{ activeJobIndex }}/{{ batchResult.summary.total }}</span>
          </span>
          <span class="batch-active-name">{{ activeJob.title || activeJob.input_url || activeJob.id }}</span>
          <el-tag v-if="activeJobDone" size="small" :type="jobStatusTagType(activeJob.status)">
            {{ jobStatusLabel(activeJob.status) }}
          </el-tag>
          <el-tag v-else size="small" type="warning">进行中</el-tag>
        </div>
        <JobProgressDetail
          :progress="activeProgress"
          :logs="activeLogs"
          :active="!activeJobDone"
          :job-status="activeJob.status"
          :max-height="240"
          :empty-hint="`等待「${activeJob.title || activeJob.input_url || activeJob.id}」输出…`"
          @clear="activeLogs = []"
        />
      </div>

      <div ref="listRef" class="batch-job-list">
        <div
          v-for="j in batchResult.summary.jobs"
          :key="j.id"
          class="batch-job-row"
          :class="{ preview: j.status === 'preview', clickable: j.status !== 'preview', active: j.id === activeJobId }"
          @click="j.status !== 'preview' && emit('view-job', j.id)"
        >
          <span class="batch-job-name">{{ j.title || j.input_url || j.id }}</span>
          <div class="batch-job-actions">
            <el-tag size="small" :type="jobStatusTagType(j.status)">{{ jobStatusLabel(j.status) }}</el-tag>
            <el-button v-if="j.status === 'preview'" size="small" type="primary" @click.stop="emit('confirm-preview', j.id)">确认</el-button>
            <el-button v-if="j.status === 'preview'" size="small" @click.stop="emit('cancel-preview', j.id)">取消</el-button>
            <!-- 未终态子任务可单独取消（preview 走上面专用的取消确认按钮） -->
            <el-button
              v-if="!isTerminalStatus(j.status) && j.status !== 'preview'"
              size="small"
              text
              type="danger"
              @click.stop="emit('cancel-job', j.id)"
            >取消</el-button>
          </div>
        </div>
      </div>
      <p class="adv-field-hint">点击子任务查看详情 · 待确认可直接操作</p>
    </template>
    <div v-else class="adv-field-hint">加载进度中...</div>
  </div>
</template>

<style scoped>
.batch-panel {
  margin-top: 14px;
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid var(--app-border);
  background: var(--app-progress-meter-bg, var(--app-hover-bg));
}
.batch-panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
  gap: 8px;
}
.batch-panel-title {
  font-weight: 650;
  font-size: 13px;
  color: var(--app-text);
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.batch-panel-meta {
  font-weight: 400;
  color: var(--app-text-secondary);
  font-size: 12px;
}
.batch-progress {
  margin-bottom: 10px;
}
.batch-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}
/* 当前任务详情块：与下方任务列表用间距+分隔线区分开 */
.batch-active-job {
  margin: 4px 0 10px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.28);
  background: var(--app-card-bg);
}
.batch-active-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.batch-active-label {
  font-size: 12px;
  font-weight: 650;
  color: var(--app-text);
  flex-shrink: 0;
}
.batch-active-index {
  font-weight: 400;
  color: var(--app-text-secondary);
  font-variant-numeric: tabular-nums;
}
.batch-active-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--app-text-secondary);
}
.batch-job-list {
  max-height: 250px;
  overflow-y: auto;
  font-size: 12px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: var(--app-card-bg);
}
.batch-job-row {
  padding: 8px 10px;
  border-bottom: 1px solid var(--app-border);
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.batch-job-row:last-child {
  border-bottom: none;
}
.batch-job-row.preview {
  background: var(--app-override-bar-bg, rgba(253, 246, 236, 0.5));
}
.batch-job-row.active {
  box-shadow: inset 3px 0 0 rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.9);
  background: rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.08);
}
.batch-job-row.clickable {
  cursor: pointer;
}
.batch-job-row.clickable:hover {
  background: var(--app-hover-bg);
}
.batch-job-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--app-text);
}
.batch-job-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}
</style>
