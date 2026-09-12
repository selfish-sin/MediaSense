<script setup>
/**
 * 批量任务进度面板（HomeView 拆分 slice-3，2026-09-12）
 * 受控组件：数据由父组件 batchResult（useBatchProgress）传入，操作以事件上抛，
 * 父组件保留刷新/跳转/子任务预览确认的实际逻辑。
 */
import { jobStatusLabel, jobStatusTagType } from '../utils/jobStatus'

defineProps({
  batchResult: { type: Object, required: true },
})
const emit = defineEmits(['refresh', 'view-job', 'confirm-preview', 'cancel-preview'])
</script>

<template>
  <div class="batch-panel">
    <div class="batch-panel-head">
      <span class="batch-panel-title">
        批量任务
        <code class="mono-tag">#{{ batchResult.batch_id }}</code>
        <span v-if="batchResult.summary" class="batch-panel-meta">共 {{ batchResult.summary.total }} 个</span>
      </span>
      <el-button size="small" text @click="emit('refresh')">刷新</el-button>
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
      <div class="batch-job-list">
        <div
          v-for="j in batchResult.summary.jobs"
          :key="j.id"
          class="batch-job-row"
          :class="{ preview: j.status === 'preview', clickable: j.status !== 'preview' }"
          @click="j.status !== 'preview' && emit('view-job', j.id)"
        >
          <span class="batch-job-name">{{ j.title || j.input_url || j.id }}</span>
          <div class="batch-job-actions">
            <el-tag size="small" :type="jobStatusTagType(j.status)">{{ jobStatusLabel(j.status) }}</el-tag>
            <el-button v-if="j.status === 'preview'" size="small" type="primary" @click.stop="emit('confirm-preview', j.id)">确认</el-button>
            <el-button v-if="j.status === 'preview'" size="small" @click.stop="emit('cancel-preview', j.id)">取消</el-button>
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
