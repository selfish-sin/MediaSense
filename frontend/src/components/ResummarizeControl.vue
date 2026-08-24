<script setup>
/**
 * 重新总结内联控件（替代旧的 ResummarizeDialog 弹出页）
 * 只负责「选 AI 模型 + 触发重新总结」；相同提示词（沿用任务原始 dimensions/options）由父组件
 * 在 @submit(model) 回调里组装 payload 并调用 /jobs/{id}/summarize。
 */
import { ref, watch } from 'vue'
import { useModelStore } from '../stores/modelStore'

const props = defineProps({
  // 初始模型（通常是任务原模型 key，传空则落到默认模型）
  model: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  buttonType: { type: String, default: 'warning' },
  plain: { type: Boolean, default: false },
})

const emit = defineEmits(['submit'])

const modelStore = useModelStore()
const selected = ref(props.model)

// 任务切换（ResultView 换 jobId）时跟随初始模型
watch(() => props.model, (v) => {
  if (v) selected.value = v
})

// 模型列表加载完成且尚未选中时，落到任务原模型（若在列表中）→ 默认模型 → 第一个模型
watch(() => modelStore.models, (models) => {
  if (!selected.value && models.length) {
    selected.value = (props.model && models.some((m) => m.key === props.model))
      ? props.model
      : (modelStore.defaultModel || models[0].key || '')
  }
}, { immediate: true })

function submit() {
  if (!selected.value) return
  emit('submit', selected.value)
}
</script>

<template>
  <div class="resummarize-control">
    <el-select
      v-model="selected"
      size="small"
      style="width: 170px;"
      :disabled="disabled"
      placeholder="选择 AI 模型"
    >
      <el-option v-for="m in modelStore.models" :key="m.key" :label="m.name" :value="m.key" />
    </el-select>
    <el-button
      :type="buttonType"
      :plain="plain"
      size="small"
      :disabled="disabled || !selected"
      @click="submit"
    >
      重新总结
    </el-button>
  </div>
</template>

<style scoped>
.resummarize-control {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
</style>
