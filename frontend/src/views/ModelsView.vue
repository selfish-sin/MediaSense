<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading, RefreshRight, View, Hide } from '@element-plus/icons-vue'
import { useModelStore } from '../stores/modelStore'
import { modelsApi } from '../utils/api'

const modelStore = useModelStore()

const dialogVisible = ref(false)
const dialogMode = ref('add')  // add / edit
const editingKey = ref('')
const showApiKey = ref(false)
const testing = ref(false)
const testResult = ref(null)
const fetchingModels = ref(false)
const fetchingModelKey = ref('')
const fetchDialogVisible = ref(false)
const fetchedModels = ref([])
const fetchError = ref('')

const form = reactive({
  key: '',
  name: '',
  api_key: '',
  endpoint: '',
  model_id: '',
  auth_type: 'bearer',
  temperature: 0.3,
  max_tokens: null,
})

function openAddDialog() {
  dialogMode.value = 'add'
  showApiKey.value = false
  Object.assign(form, {
    key: '', name: '', api_key: '', endpoint: '',
    model_id: '', auth_type: 'bearer', temperature: 0.3, max_tokens: null,
  })
  dialogVisible.value = true
}

function openEditDialog(model) {
  dialogMode.value = 'edit'
  editingKey.value = model.key
  showApiKey.value = false
  Object.assign(form, {
    key: model.key,
    name: model.name,
    api_key: model.api_key || '',
    endpoint: model.endpoint,
    model_id: model.model_id,
    auth_type: model.auth_type,
    temperature: model.temperature,
    max_tokens: model.max_tokens,
  })
  dialogVisible.value = true
}

async function handleSubmit() {
  if (dialogMode.value === 'add') {
    await handleAdd()
  } else {
    await handleUpdate()
  }
}

async function handleAdd() {
  if (!form.key || !form.name || !form.endpoint) {
    ElMessage.warning('请填写必填字段：Key、名称、端点')
    return
  }
  try {
    await modelStore.addModel({ ...form })
    ElMessage.success('模型已添加')
    dialogVisible.value = false
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function handleUpdate() {
  if (!form.name || !form.endpoint) {
    ElMessage.warning('请填写必填字段：名称、端点')
    return
  }
  try {
    await modelStore.updateModel(editingKey.value, { ...form })
    ElMessage.success('模型已更新')
    dialogVisible.value = false
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function handleDelete(key) {
  try {
    await ElMessageBox.confirm(`确认删除模型 ${key}？`, '提示', { type: 'warning' })
    await modelStore.deleteModel(key)
    ElMessage.success('已删除')
  } catch (err) {
    if (err !== 'cancel') ElMessage.error(err.message)
  }
}

async function handleTest(key) {
  testing.value = true
  testResult.value = null
  try {
    const res = await modelStore.testModel(key)
    testResult.value = res
    if (res.success) {
      ElMessage.success(`连接成功 (${res.latency_ms}ms)`)
    } else {
      ElMessage.error(`连接失败: ${res.error}`)
    }
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    testing.value = false
  }
}

async function handleFetchModels(key) {
  fetchingModels.value = true
  fetchingModelKey.value = key
  fetchedModels.value = []
  fetchError.value = ''
  fetchDialogVisible.value = true
  try {
    const res = await modelsApi.fetch(key)
    handleFetchResult(res)
  } catch (err) {
    fetchError.value = err.message
    ElMessage.error(err.message)
  } finally {
    fetchingModels.value = false
  }
}

async function fetchModelsFromForm() {
  // 从添加/编辑表单中直接调用（端点尚未保存时）
  if (!form.endpoint) {
    ElMessage.warning('请先填写端点地址')
    return
  }
  fetchingModels.value = true
  fetchingModelKey.value = '__form__'
  fetchedModels.value = []
  fetchError.value = ''
  fetchDialogVisible.value = true
  try {
    const res = await modelsApi.fetch({ endpoint: form.endpoint, api_key: form.api_key })
    handleFetchResult(res)
  } catch (err) {
    fetchError.value = err.message
    ElMessage.error(err.message)
  } finally {
    fetchingModels.value = false
  }
}

function handleFetchResult(res) {
  if (res.error) {
    fetchError.value = res.error
    ElMessage.warning(res.error)
  } else {
    fetchedModels.value = res.models || []
    if (fetchedModels.value.length > 0) {
      ElMessage.success(`获取到 ${fetchedModels.value.length} 个模型`)
    } else {
      ElMessage.warning('未获取到模型')
    }
  }
}

function handleQuickAdd(modelId) {
  // 关闭获取列表弹窗，打开添加模型弹窗，预填 model_id
  form.model_id = modelId
  form.key = modelId.replace(/[^a-zA-Z0-9_-]/g, '-').toLowerCase()
  form.name = modelId
}

onMounted(() => {
  modelStore.loadModels()
})
</script>

<template>
  <div style="max-width: 1200px; margin: 0 auto; padding: 24px;">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
      <h2 style="margin: 0;">AI模型管理</h2>
      <el-button type="primary" @click="openAddDialog">添加模型</el-button>
    </div>

    <el-table :data="modelStore.models" v-loading="modelStore.loading" stripe style="width: 100%;">
      <el-table-column prop="key" label="Key" width="150" />
      <el-table-column prop="name" label="名称" width="200" />
      <el-table-column prop="endpoint" label="端点" min-width="300" show-overflow-tooltip />
      <el-table-column prop="model_id" label="模型ID" width="150" />
      <el-table-column prop="auth_type" label="认证" width="80" />
      <el-table-column label="内置" width="60">
        <template #default="{ row }">
          <el-tag v-if="row.is_builtin" type="info" size="small">内置</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="240" fixed="right">
        <template #default="{ row }">
          <el-button size="small" link type="primary" @click="openEditDialog(row)">编辑</el-button>
          <el-button size="small" link @click="handleTest(row.key)" :loading="testing">测试</el-button>
          <el-button size="small" type="danger" link @click="handleDelete(row.key)" :disabled="row.is_builtin">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 添加/编辑模型弹窗 -->
    <el-dialog v-model="dialogVisible" :title="dialogMode === 'add' ? '添加模型' : '编辑模型'" width="500px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="Key" required>
          <el-input v-model="form.key" :disabled="dialogMode === 'edit'" placeholder="唯一标识，如 my-gpt4" />
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="显示名称" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="form.api_key" :type="showApiKey ? 'text' : 'password'" placeholder="API密钥">
            <template #suffix>
              <el-icon v-if="!showApiKey" style="cursor: pointer;" @click="showApiKey = true"><View /></el-icon>
              <el-icon v-else style="cursor: pointer;" @click="showApiKey = false"><Hide /></el-icon>
            </template>
          </el-input>
        </el-form-item>
        <el-form-item label="端点" required>
          <el-input v-model="form.endpoint" placeholder="https://api.openai.com/v1/chat/completions" />
        </el-form-item>
        <el-form-item label="模型ID">
          <div style="display: flex; gap: 6px; align-items: center;">
            <el-input v-model="form.model_id" placeholder="API调用时的模型名" style="flex: 1;" />
            <el-tooltip content="从端点获取可用模型列表" placement="top">
              <el-button
                :icon="RefreshRight"
                circle
                size="small"
                @click="fetchModelsFromForm"
                :loading="fetchingModels && fetchingModelKey === '__form__'"
              />
            </el-tooltip>
          </div>
        </el-form-item>
        <el-form-item label="认证类型">
          <el-select v-model="form.auth_type">
            <el-option label="Bearer Token" value="bearer" />
            <el-option label="Direct Key" value="direct" />
            <el-option label="无认证" value="none" />
          </el-select>
        </el-form-item>
        <el-form-item label="温度">
          <el-slider v-model="form.temperature" :min="0" :max="2" :step="0.1" show-input />
        </el-form-item>
        <el-form-item label="最大Tokens">
          <el-input-number v-model="form.max_tokens" :min="0" :max="200000" :step="256" placeholder="留空=模型自动" controls-position="right" />
          <span style="font-size: 11px; color: #909399; margin-left: 6px;">留空 = 模型自动决定</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSubmit">{{ dialogMode === 'add' ? '添加' : '保存' }}</el-button>
      </template>
    </el-dialog>

    <!-- 获取模型列表结果 -->
    <el-dialog v-model="fetchDialogVisible" title="获取模型列表" width="500px" :show-close="true">
      <div v-if="fetchError" style="color: #f56c6c; margin-bottom: 12px;">{{ fetchError }}</div>
      <el-table v-if="fetchedModels.length > 0" :data="fetchedModels" max-height="400" size="small" stripe>
        <el-table-column prop="id" label="模型ID" min-width="200" show-overflow-tooltip />
        <el-table-column label="操作" width="80" align="center">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="handleQuickAdd(row.id); fetchDialogVisible = false; dialogVisible = true">添加</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-else-if="!fetchError" style="text-align: center; padding: 40px; color: #909399;">
        未获取到模型或接口不支持
      </div>
    </el-dialog>
  </div>
</template>
