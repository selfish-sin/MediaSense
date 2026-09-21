<script setup>
/**
 * 处理队列列表（挂在「任务进度」卡下方）：正在处理 + 排队中。
 *
 * 受控组件：队列数据由父组件（JobProgressPanel 内的 useJobQueue）提供，
 * 本组件只渲染 + 上抛事件（cancel / select），不发请求。
 */
import { computed } from 'vue'
import { jobStatusLabel, jobStatusTagType } from '../utils/jobStatus'

const props = defineProps({
  queue: { type: Object, default: null },
  currentJobId: { type: String, default: '' },
})
const emit = defineEmits(['cancel', 'select'])

const running = computed(() => props.queue?.running || [])
const pending = computed(() => props.queue?.pending || [])
const total = computed(() => props.queue?.total || 0)

/**
 * 「正在处理」项的展示状态：DB status 在字幕/下载阶段仍停在 pending，
 * 但它们已占用处理槽位 → 展示「处理中」而不是「排队中」。
 */
function runningStatus(item) {
  return item.status === 'pending' ? 'processing' : item.status
}

/** 显示名：标题 → URL 短写（排队中的任务还没拿到 title，只有 URL） */
function itemLabel(item) {
  const title = (item.title || '').trim()
  if (title) return title
  const url = item.input_url || ''
  if (!url) return item.id
  try {
    const parsed = new URL(url)
    return `${parsed.hostname.replace(/^www\./, '')}${parsed.pathname}`
  } catch {
    return url.slice(0, 60)
  }
}
</script>

<template>
  <div v-if="total > 0" class="queue-panel">
    <div class="queue-head">
      <span class="queue-head-title">处理队列</span>
      <el-tag v-if="running.length" size="small" type="success" effect="dark">
        正在处理 {{ running.length }}
      </el-tag>
      <el-tag v-if="pending.length" size="small" type="info" effect="plain">
        排队中 {{ pending.length }}
      </el-tag>
      <span v-if="queue?.concurrency" class="queue-hint">同一时刻最多 {{ queue.concurrency }} 个</span>
    </div>

    <ul class="queue-list">
      <li
        v-for="item in running"
        :key="item.id"
        class="queue-item"
        :class="{ 'is-current': item.id === currentJobId }"
      >
        <span class="queue-dot" />
        <span class="queue-name" :title="itemLabel(item)" @click="emit('select', item.id)">
          {{ itemLabel(item) }}
        </span>
        <el-tag size="small" :type="jobStatusTagType(runningStatus(item))" effect="dark">
          {{ jobStatusLabel(runningStatus(item)) }}
        </el-tag>
        <el-button size="small" text type="danger" @click="emit('cancel', item.id)">取消</el-button>
      </li>
      <li
        v-for="(item, idx) in pending"
        :key="item.id"
        class="queue-item"
        :class="{ 'is-current': item.id === currentJobId }"
      >
        <span class="queue-index">{{ idx + 1 }}</span>
        <span class="queue-name" :title="itemLabel(item)" @click="emit('select', item.id)">
          {{ itemLabel(item) }}
        </span>
        <el-tag size="small" type="info" effect="plain">排队中</el-tag>
        <el-button size="small" text type="danger" @click="emit('cancel', item.id)">取消</el-button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.queue-panel {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--app-border);
}
.queue-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.queue-head-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-text-secondary);
}
.queue-hint {
  font-size: 12px;
  color: var(--app-text-secondary);
}
.queue-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 220px;
  overflow-y: auto;
}
.queue-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border-radius: 6px;
  font-size: 13px;
}
.queue-item:hover {
  background: rgba(var(--app-accent-1-rgb), 0.06);
}
.queue-item.is-current {
  background: rgba(var(--app-accent-1-rgb), 0.1);
}
.queue-index {
  width: 20px;
  text-align: center;
  font-size: 12px;
  color: var(--app-text-secondary);
}
.queue-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #22c55e;
  flex: none;
}
.queue-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}
</style>
