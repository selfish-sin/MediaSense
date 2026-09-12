<script setup>
/**
 * 任务进度卡片（HomeView 拆分 slice-4，2026-09-12）
 * - 自持：socket onProgress/onLog 监听 + 展开/自动滚动状态；进度与日志数据来自 jobStore
 * - expose reveal()：父组件提交任务后调用（展开本卡并滚动到视口，工作台第一眼）
 * - 生命周期注意：本组件在 v-if 下挂载/卸载（无任务时卸载 → 监听随之注销），
 *   与 HomeView 原 socketUnsubs（onUnmounted 清理）语义一致；keep-alive 切页不卸载。
 */
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useJobStore } from '../stores/jobStore'
import { onProgress, onLog } from '../utils/socket'
import { jobStatusLabel, jobStatusTagType } from '../utils/jobStatus'

const jobStore = useJobStore()

// ── 任务进度日志（默认展开，当作进度条使用） ──
const showJobProgress = ref(true)
const jobLogAutoScroll = ref(true)

const isProcessing = computed(() => jobStore.isProcessing)
const progressPct = computed(() => jobStore.progress.pct)

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
      // 新进度日志到来时保持面板展开并滚到底
      if (!showJobProgress.value) showJobProgress.value = true
      if (jobLogAutoScroll.value) {
        nextTick(() => {
          const el = document.getElementById('job-log-panel')
          if (el) el.scrollTop = el.scrollHeight
        })
      }
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
          <el-switch v-model="jobLogAutoScroll" active-text="自动滚动" size="small" />
          <el-button size="small" text @click="jobStore.clearLogs">清空</el-button>
          <el-button size="small" text @click="showJobProgress = !showJobProgress">
            {{ showJobProgress ? '收起' : '展开' }}
          </el-button>
        </div>
      </div>
    </template>

    <div v-show="showJobProgress">
      <div class="progress-meter">
        <div class="progress-meter-row">
          <span class="progress-msg">
            {{ jobStore.progress.msg || (isProcessing ? '排队/准备中...' : '暂无进度') }}
          </span>
          <span v-if="isProcessing || progressPct > 0" class="progress-pct">
            {{ Math.min(100, Math.max(0, progressPct || 0)) }}%
          </span>
        </div>
        <el-progress
          v-if="isProcessing || progressPct > 0"
          class="progress-bar-glow"
          :percentage="Math.min(100, Math.max(0, progressPct || 0))"
          :stroke-width="16"
          :show-text="false"
          :status="jobStore.jobStatus === 'completed' ? 'success' : (jobStore.jobStatus === 'failed' ? 'exception' : undefined)"
        />
        <div v-if="jobStore.progress.step" class="progress-step">
          阶段 · {{ jobStore.progress.step }}
        </div>
      </div>

      <div id="job-log-panel" class="log-panel job-progress-log">
        <div v-if="!jobStore.logs.length" class="log-empty">
          提交任务后，这里会实时显示下载、转录、AI 各阶段进度（含速度与预计剩余时间）。
        </div>
        <div v-for="(log, i) in jobStore.logs" :key="i" class="job-log-line">
          <span class="log-time">{{ log.time }}</span>
          <span class="log-msg">{{ log.msg }}</span>
        </div>
      </div>
    </div>
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
.progress-meter {
  margin-bottom: 12px;
  padding: 12px 14px;
  border-radius: 10px;
  background: var(--app-progress-meter-bg);
  border: 1px solid var(--app-border);
}
.progress-meter-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 8px;
  gap: 12px;
}
.progress-msg {
  font-size: 13px;
  color: var(--app-text);
  flex: 1;
  word-break: break-all;
  line-height: 1.5;
}
.progress-pct {
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  background: linear-gradient(135deg, var(--app-accent-1), var(--app-accent-2));
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  flex-shrink: 0;
}
.progress-step {
  margin-top: 6px;
  font-size: 11px;
  color: var(--app-text-secondary);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.progress-bar-glow :deep(.el-progress-bar__outer) {
  background: var(--app-progress-track) !important;
  border-radius: 999px;
  overflow: hidden;
}
.progress-bar-glow :deep(.el-progress-bar__inner) {
  border-radius: 999px;
  background: linear-gradient(90deg, var(--app-accent-1), var(--app-accent-2)) !important;
  box-shadow: 0 0 12px rgba(var(--app-accent-1-rgb), 0.45);
}
.job-progress-log {
  max-height: 340px;
  margin-top: 0;
}
.job-log-line {
  display: flex;
  gap: 6px;
  padding: 3px 0;
  border-bottom: 1px solid var(--app-log-line-border);
  word-break: break-all;
}
.job-log-line:last-child { border-bottom: none; }
.job-log-line .log-msg {
  color: var(--app-log-text);
  flex: 1;
}
</style>
