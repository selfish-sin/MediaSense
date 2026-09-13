<script setup>
/**
 * 输入区卡片（HomeView 拆分二阶段 slice-A，2026-09-12）
 * - 输入类型/内容状态由父组件持有（handleSubmit/useJobRestore/sessionStorage 依赖），
 *   经 6 个 v-model 双向绑定（inputType/inputUrl/inputText/batchUrls/uploadedFilePath/uploadedFileName）
 * - 上传交互（进行中状态/文件名校验/请求）内聚本组件，成功/移除经 v-model 写回父组件
 * - 批量面板 BatchResultPanel 事件原样转发：refresh / view-job / confirm-preview / cancel-preview
 */
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadCloud, Check, X } from 'lucide-vue-next'
import { uploadRejectReason } from '../utils/uploadRules'
import BatchResultPanel from './BatchResultPanel.vue'

const props = defineProps({
  inputType: { type: String, required: true },
  inputUrl: { type: String, required: true },
  inputText: { type: String, required: true },
  batchUrls: { type: String, required: true },
  uploadedFilePath: { type: String, required: true },
  uploadedFileName: { type: String, required: true },
  batchResult: { type: Object, default: null },
})

const emit = defineEmits([
  'update:inputType',
  'update:inputUrl',
  'update:inputText',
  'update:batchUrls',
  'update:uploadedFilePath',
  'update:uploadedFileName',
  'refresh',
  'view-job',
  'confirm-preview',
  'cancel-preview',
])

// ── 双向绑定代理（保持模板 v-model 书写形态与原 HomeView 一致） ──
const inputTypeModel = computed({
  get: () => props.inputType,
  set: (v) => emit('update:inputType', v),
})
const inputUrlModel = computed({
  get: () => props.inputUrl,
  set: (v) => emit('update:inputUrl', v),
})
const inputTextModel = computed({
  get: () => props.inputText,
  set: (v) => emit('update:inputText', v),
})
const batchUrlsModel = computed({
  get: () => props.batchUrls,
  set: (v) => emit('update:batchUrls', v),
})

// ── 文件上传 ──
// 上传区可见状态只由上传结果驱动：成功才显示「已上传」，失败/超限不留任何痕迹
const uploading = ref(false)
const uploadingFileName = ref('')
const uploadRef = ref(null)

async function handleFileUpload(file) {
  if (file.status !== 'ready') return
  // 内部列表始终清空：不展示未上传成功的文件，也保证同一文件可以重新选择
  uploadRef.value?.clearFiles()
  const reject = uploadRejectReason(file.name, file.raw?.size || 0)
  if (reject) {
    ElMessage.error({ message: reject, duration: 6000 })
    return
  }
  if (uploading.value) { ElMessage.warning('文件正在上传中，请稍候'); return }
  uploading.value = true
  uploadingFileName.value = file.name
  try {
    const formData = new FormData()
    formData.append('file', file.raw)
    const api = (await import('../utils/api.js')).default
    const res = await api.post('/jobs/upload', formData, {
      timeout: 120000,
    })
    if (res.success) {
      emit('update:uploadedFilePath', res.file_path)
      emit('update:uploadedFileName', res.file_name)
      ElMessage.success(`文件上传成功: ${res.file_name}`)
    } else {
      ElMessage.error(res.message || '上传失败')
    }
  } catch (err) {
    if (err.response) {
      console.error('[上传失败]', err.response.status, JSON.stringify(err.response.data, null, 2))
      console.error('[请求头]', err.config?.headers)
    }
    ElMessage.error(err.message)
  } finally {
    uploading.value = false
    uploadingFileName.value = ''
  }
}

function handleFileRemove() {
  emit('update:uploadedFilePath', '')
  emit('update:uploadedFileName', '')
}
</script>

<template>
  <el-card class="wb-card" shadow="never">
    <template #header>
      <div class="card-head-row">
        <span class="wb-card-title">输入</span>
      </div>
    </template>

    <el-radio-group v-model="inputTypeModel" class="input-type-tabs" size="default">
      <el-radio-button value="url">URL</el-radio-button>
      <el-radio-button value="batch">批量 URL</el-radio-button>
      <el-radio-button value="file">文件上传</el-radio-button>
      <el-radio-button value="text">文本粘贴</el-radio-button>
    </el-radio-group>

    <!-- URL 输入 -->
    <el-input
      v-if="inputType === 'url'"
      v-model="inputUrlModel"
      placeholder="粘贴视频 URL"
      size="large"
      clearable
      class="input-main"
    >
      <template #prepend>URL</template>
    </el-input>

    <!-- 文件上传：内部列表隐藏，状态区只展示真实上传结果（成功才有「已上传」） -->
    <el-upload
      v-else-if="inputType === 'file'"
      ref="uploadRef"
      class="input-upload"
      drag
      :auto-upload="false"
      :show-file-list="false"
      :on-change="handleFileUpload"
      accept="audio/*,video/*,.srt,.vtt,.txt,.md"
    >
      <el-icon class="upload-icon"><UploadCloud /></el-icon>
      <div class="upload-title">拖拽文件到此处，或点击上传</div>
      <template #tip>
        <div class="upload-tip">
          支持音频、视频、字幕、TXT、Markdown，单个文件不超过 2GB
          <span v-if="uploading && uploadingFileName" class="upload-busy">上传中: {{ uploadingFileName }}…</span>
          <span v-else-if="uploadedFileName" class="upload-ok"><el-icon :size="12" style="vertical-align: -1px;"><Check /></el-icon> 已上传: {{ uploadedFileName }}<el-icon class="upload-remove" :size="12" title="移除" @click.stop="handleFileRemove"><X /></el-icon></span>
        </div>
      </template>
    </el-upload>

    <!-- 文本粘贴 -->
    <el-input
      v-else-if="inputType === 'text'"
      v-model="inputTextModel"
      type="textarea"
      :rows="6"
      resize="vertical"
      placeholder="粘贴需要总结的文本内容"
      class="input-main"
    />

    <!-- 批量URL -->
    <el-input
      v-else-if="inputType === 'batch'"
      v-model="batchUrlsModel"
      type="textarea"
      :rows="6"
      resize="vertical"
      placeholder="每行一个 URL"
      class="input-main"
    />

    <!-- 批量任务进度面板 -->
    <BatchResultPanel
      v-if="inputType === 'batch' && batchResult"
      :batch-result="batchResult"
      @refresh="emit('refresh')"
      @view-job="(id) => emit('view-job', id)"
      @confirm-preview="(id) => emit('confirm-preview', id)"
      @cancel-preview="(id) => emit('cancel-preview', id)"
    />
  </el-card>
</template>

<style scoped>
/* 输入区（自 HomeView 迁入，2026-09-12 slice-A；.input-main 已提升 App.vue 全局） */
.input-type-tabs {
  margin-bottom: 14px;
  display: flex;
  flex-wrap: wrap;
}
/* 选中态改用全站渐变主色：Element Plus 默认纯蓝实心与主题配色不一致 */
.input-type-tabs :deep(.el-radio-button.is-active .el-radio-button__inner) {
  background: linear-gradient(135deg, var(--app-accent-1), var(--app-accent-2)) !important;
  border-color: transparent !important;
  color: #fff !important;
  box-shadow: 0 4px 12px rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.3);
}
.input-type-tabs :deep(.el-radio-button__inner:hover) {
  color: var(--app-accent-1, #38bdf8);
}
.input-type-tabs :deep(.el-radio-button.is-active .el-radio-button__inner:hover) {
  color: #fff;
}
.input-upload :deep(.el-upload-dragger) {
  border-radius: 12px;
  border-color: var(--app-border);
  background: var(--app-progress-meter-bg, transparent);
  padding: 28px 16px;
}
.upload-icon {
  font-size: 40px;
  color: var(--app-text-secondary);
}
.upload-title {
  margin-top: 8px;
  color: var(--app-text);
  font-size: 13px;
}
.upload-tip {
  font-size: 12px;
  color: var(--app-text-secondary);
  margin-top: 6px;
}
.upload-ok {
  color: #67c23a;
  margin-left: 8px;
  font-weight: 600;
}
.upload-busy {
  color: var(--el-color-primary, #409eff);
  margin-left: 8px;
  font-weight: 600;
}
.upload-remove {
  margin-left: 6px;
  cursor: pointer;
  vertical-align: -1px;
  color: var(--app-text-secondary);
}
.upload-remove:hover {
  color: var(--el-color-danger, #f56c6c);
}
</style>
