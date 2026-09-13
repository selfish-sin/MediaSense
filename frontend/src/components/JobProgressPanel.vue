<script setup>
/**
 * 任务进度卡片（HomeView 拆分 slice-4，2026-09-12）
 * - 自持：socket onProgress/onLog 监听 + 展开状态；进度与日志数据来自 jobStore
 * - 进度详情（进度条/阶段/日志）渲染交给 JobProgressDetail（与批量子任务共用）
 * - expose reveal()：父组件提交任务后调用（展开本卡并滚动到视口，工作台第一眼）
 * - 生命周期注意：本组件在 v-if 下挂载/卸载（无任务时卸载 → 监听随之注销），
 *   与 HomeView 原 socketUnsubs（onUnmounted 清理）语义一致；keep-alive 切页不卸载。
 */
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useJobStore } from '../stores/jobStore'
import { onProgress, onLog } from '../utils/socket'
import { jobStatusLabel, jobStatusTagType } from '../utils/jobStatus'
import JobProgressDetail from './JobProgressDetail.vue'

const jobStore = useJobStore()

// ── 任务进度日志（默认展开，当作进度条使用） ──
const showJobProgress = ref(true)

const isProcessing = computed(() => jobStore.isProcessing)

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
defineExpose({ reveal })
</script>

<template>
  <el-card
    id="job-progress-section"
    v-if="isProcessing || jobStore.logs.length > 0 || jobStore.currentJobId"
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
          <el-tag v-if="jobStore.jobStatus && jobStore.jobStatus !== 'idle'" size="small" :type="jobStatusTagType(jobStore.jobStatus)" effect="dark">
            {{ jobStatusLabel(jobStore.jobStatus) }}
          </el-tag>
          <span v-if="jobStore.jobTitle" class="progress-job-title">
            {{ jobStore.jobTitle }}
          </span>
        </div>
        <div class="progress-header-right">
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
