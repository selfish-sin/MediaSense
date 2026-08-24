<script setup>
/**
 * 重新总结对话框（模块化提示词版本）
 * 用户选择 7 维度 + AI 模型后，emit('success', payload)
 */
import { ref, watch, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { useSettingsStore } from '../stores/settingsStore'
import { useModelStore } from '../stores/modelStore'
import DimensionSelector from './DimensionSelector.vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  jobId: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue', 'success'])

const settings = useSettingsStore()
const modelStore = useModelStore()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const model = ref('glm-4.5-air')
const dimensions = ref({
  role: 'general',
  rules: 'standard',
  output: 'summary',
  content_type: 'general',
  depth: 'standard',
  tone: '学术严谨',
  word_count: '3000',
})

// ★ 旧兜底值：settings.selectedModel 初始为 'glm-4.5-air'，用户没显式选择时
//   应优先用数据库默认模型，而不是这个遗留占位值
const LEGACY_PLACEHOLDER_KEYS = new Set(['', 'glm-4.5-air'])

// 弹窗打开时初始化
watch(visible, (v) => {
  if (v) {
    // 同步当前 settings 的默认维度选择；模型优先用工作台选择，其次数据库默认模型
    dimensions.value = { ...settings.selectedDimensions }
    if (LEGACY_PLACEHOLDER_KEYS.has(settings.selectedModel) && modelStore.defaultModel) {
      model.value = modelStore.defaultModel
    } else {
      model.value = settings.selectedModel || 'glm-4.5-air'
    }
  }
})

function handleSubmit() {
  if (!props.jobId) {
    ElMessage.warning('没有任务ID')
    return
  }
  visible.value = false
  // emit success：携带 dimensions + model，由父组件调用后端 API
  emit('success', {
    dimensions: { ...dimensions.value },
    model: model.value,
    options: { dimensions: { ...dimensions.value } },
  })
  ElMessage.success('重新总结已提交，结果将流式输出到结果栏')
}
</script>

<template>
  <el-dialog v-model="visible" title="重新总结配置" width="70%" top="5vh" destroy-on-close :close-on-click-modal="false">
    <div style="max-height: 75vh; overflow-y: auto; padding-right: 8px;">
      <DimensionSelector v-model="dimensions" />

      <el-divider content-position="left" style="font-size: 12px;">AI 模型</el-divider>
      <el-form label-width="70px" size="small">
        <el-form-item label="AI 模型">
          <el-select v-model="model" style="width: 100%;">
            <el-option v-for="m in modelStore.models" :key="m.key" :label="m.name" :value="m.key" />
          </el-select>
        </el-form-item>
      </el-form>
    </div>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" @click="handleSubmit">开始重新总结</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
</style>
