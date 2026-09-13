<script setup>
/**
 * 任务进度详情（进度消息 + 进度条 + 阶段 + 实时日志）——纯展示组件，不持有数据源。
 * 单任务（JobProgressPanel，数据来自 jobStore）与批量子任务（BatchResultPanel，
 * 数据来自当前子任务的 socket 推送）共用同一套视觉与滚动行为。
 */
import { ref, watch, nextTick } from 'vue'

const props = defineProps({
  progress: { type: Object, default: () => ({ step: '', pct: 0, msg: '' }) },
  logs: { type: Array, default: () => [] },
  // 任务进行中：决定进度条/百分比是否显示（终态任务若已有进度也照常显示）
  active: { type: Boolean, default: false },
  jobStatus: { type: String, default: '' },
  emptyHint: {
    type: String,
    default: '提交任务后，这里会实时显示下载、转录、AI 各阶段进度（含速度与预计剩余时间）。',
  },
  maxHeight: { type: Number, default: 340 },
})

const emit = defineEmits(['clear'])

const autoScroll = ref(true)
const logPanelRef = ref(null)

function clampPct(v) {
  return Math.min(100, Math.max(0, v || 0))
}

// 新日志到达时滚到底（若开启了自动滚动）
watch(() => props.logs.length, () => {
  if (!autoScroll.value) return
  nextTick(() => {
    const el = logPanelRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
})
</script>

<template>
  <div class="progress-detail">
    <div class="progress-meter">
      <div class="progress-meter-row">
        <span class="progress-msg">
          {{ progress.msg || (active ? '排队/准备中...' : '暂无进度') }}
        </span>
        <span v-if="active || progress.pct > 0" class="progress-pct">
          {{ clampPct(progress.pct) }}%
        </span>
      </div>
      <el-progress
        v-if="active || progress.pct > 0"
        class="progress-bar-glow"
        :percentage="clampPct(progress.pct)"
        :stroke-width="16"
        :show-text="false"
        :status="jobStatus === 'completed' ? 'success' : (jobStatus === 'failed' ? 'exception' : undefined)"
      />
      <div v-if="progress.step" class="progress-step">
        阶段 · {{ progress.step }}
      </div>
    </div>

    <div class="progress-log-bar">
      <el-switch v-model="autoScroll" active-text="自动滚动" size="small" />
      <el-button size="small" text @click="emit('clear')">清空</el-button>
    </div>

    <div ref="logPanelRef" class="log-panel job-progress-log" :style="{ maxHeight: `${maxHeight}px` }">
      <div v-if="!logs.length" class="log-empty">{{ emptyHint }}</div>
      <div v-for="(log, i) in logs" :key="i" class="job-log-line">
        <span class="log-time">{{ log.time }}</span>
        <span class="log-msg">{{ log.msg }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 进度计（自 JobProgressPanel 迁入，2026-09-13：单任务/批量子任务共用） */
.progress-meter {
  margin-bottom: 10px;
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
.progress-log-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-bottom: 4px;
}
.job-progress-log {
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
