<script setup>
/**
 * AI 调用链（兜底环可视化）
 *
 * 只在真的换过模型时出现：单次成功没有"兜底"可言，展示出来只是噪音。
 * 首页结果面板与结果详情页共用本组件，保证两处形态、措辞、样式完全一致。
 */
import { computed } from 'vue'
import { RefreshCcw } from 'lucide-vue-next'
import { useModelStore } from '../stores/modelStore'
import { modelDisplayName } from '../utils/modelDisplay'

const props = defineProps({
  // [{ model, ok, reason }, ...]
  chain: { type: Array, default: null },
})

const modelStore = useModelStore()

const visible = computed(() => Array.isArray(props.chain) && props.chain.length > 1)

/** 调用链里存的是模型 key；能查到配置就显示「引擎-名称」，查不到（模型已删）就退回 key */
function modelLabel(key) {
  const m = modelStore.models.find((x) => x.key === key)
  return m ? modelDisplayName(m) : key
}
</script>

<template>
  <div v-if="visible" class="call-chain">
    <div class="call-chain-head">
      <el-icon class="call-chain-icon"><RefreshCcw /></el-icon>
      <span>本次发生了兜底重试（{{ chain.length }} 次尝试）</span>
    </div>
    <div v-for="(step, i) in chain" :key="i" class="call-step">
      <span class="call-step-badge" :class="step.ok ? 'call-step-badge--ok' : 'call-step-badge--err'">
        {{ i + 1 }}
      </span>
      <div class="call-step-body">
        <span class="call-step-name">{{ modelLabel(step.model) }}</span>
        <span class="call-step-state">{{ step.ok ? '成功' : '失败' }}</span>
        <span v-if="!step.ok && step.reason" class="call-step-reason" :title="step.reason">
          {{ step.reason }}
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.call-chain {
  margin-bottom: 12px;
  padding: 10px 12px;
  border: 1px solid var(--app-border);
  border-radius: 4px;
  background: var(--app-hover-bg);
}

.call-chain-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--app-text-secondary);
  margin-bottom: 8px;
}

.call-chain-icon {
  color: var(--app-accent-1);
}

.call-step {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 4px 0;
}

.call-step-badge {
  flex: 0 0 auto;
  width: 18px;
  height: 18px;
  line-height: 16px;
  text-align: center;
  font-size: 11px;
  border-radius: 50%;
  border: 1px solid var(--app-border);
}

.call-step-badge--ok {
  background: var(--app-engine-ok-bg);
  border-color: var(--app-engine-ok-border);
  color: var(--app-engine-ok-text);
}

.call-step-badge--err {
  background: var(--app-engine-err-bg);
  border-color: var(--app-engine-err-border);
  color: var(--app-engine-err-text);
}

.call-step-body {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex: 1;
  min-width: 0;
  flex-wrap: wrap;
}

.call-step-name {
  font-size: 13px;
  color: var(--app-text);
}

.call-step-state {
  font-size: 12px;
  color: var(--app-text-secondary);
}

.call-step-reason {
  font-size: 12px;
  color: var(--app-engine-err-text);
  line-height: 1.5;
  word-break: break-word;
}
</style>
