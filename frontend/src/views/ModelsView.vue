<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { RefreshCcw, Eye, EyeOff } from 'lucide-vue-next'
import { useModelStore } from '../stores/modelStore'
import { modelsApi } from '../utils/api'
import { modelDisplayName } from '../utils/modelDisplay'

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
  engine: '',
  name: '',
  api_key: '',
  endpoint: '',
  model_id: '',
  auth_type: 'bearer',
  temperature: 0.3,
  max_tokens: null,
  skip_ssl_verify: false,
})

// ★ 常见引擎名（可下拉选，也可直接输入任意自定义引擎名）
//   默认选中的引擎名随「快捷模板」自动带出，无需手填
const ENGINE_OPTIONS = [
  '阿里云', 'DeepSeek', 'OpenAI', '智谱', '硅基流动',
  '本地', '月之暗面', '字节豆包', '百度千帆', '腾讯混元',
]

// ★ 常见服务商快捷模板：点击预填引擎/端点/认证/模型ID，减少手填
const PROVIDER_PRESETS = [
  { label: 'DeepSeek', engine: 'DeepSeek', endpoint: 'https://api.deepseek.com/v1/chat/completions', modelHint: 'deepseek-chat', auth: 'bearer' },
  { label: '阿里云百炼', engine: '阿里云', endpoint: 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions', modelHint: 'qwen-plus', auth: 'bearer' },
  { label: 'OpenAI', engine: 'OpenAI', endpoint: 'https://api.openai.com/v1/chat/completions', modelHint: 'gpt-4o', auth: 'bearer' },
  { label: '智谱 GLM', engine: '智谱', endpoint: 'https://open.bigmodel.cn/api/paas/v4/chat/completions', modelHint: 'glm-4.5-air', auth: 'bearer' },
  { label: '硅基流动', engine: '硅基流动', endpoint: 'https://api.siliconflow.cn/v1/chat/completions', modelHint: 'deepseek-ai/DeepSeek-V3', auth: 'bearer' },
  { label: '本地 Ollama', engine: '本地', endpoint: 'http://localhost:11434/v1/chat/completions', modelHint: 'llama3.1', auth: 'none' },
]

function applyPreset(p) {
  form.engine = p.engine
  form.endpoint = p.endpoint
  form.auth_type = p.auth
  form.model_id = p.modelHint
  if (!form.key) form.key = deriveKey(p.modelHint)
  if (!form.name) form.name = p.modelHint
}

function deriveKey(id) {
  return String(id || '').replace(/[^a-zA-Z0-9_-]/g, '-').toLowerCase()
}

function normalizeEndpoint(url) {
  let u = (url || '').trim().replace(/\/+$/, '')
  if (!u) return u
  if (u.includes('/chat/completions')) {
    // 已是 /v1/chat/completions 原样；否则自动补 /v1（用户输入的格式一般不带 v1）
    return u.includes('/v1/chat/completions')
      ? u
      : u.replace(/\/chat\/completions$/, '/v1/chat/completions')
  }
  // 末尾恰是 /v1 先去掉，统一补 /v1/chat/completions
  u = u.replace(/\/v1$/, '')
  return `${u}/v1/chat/completions`
}

// ★ 端点自动补全预览：输入框下方实时显示保存后的完整格式
const endpointPreview = computed(() => {
  const raw = (form.endpoint || '').trim().replace(/\/+$/, '')
  if (!raw) return ''
  return normalizeEndpoint(raw)
})

function isDefaultModel(row) {
  return modelStore.defaultModel && row.key === modelStore.defaultModel
}

function openAddDialog() {
  dialogMode.value = 'add'
  showApiKey.value = false
  Object.assign(form, {
    key: '', engine: '', name: '', api_key: '', endpoint: '',
    model_id: '', auth_type: 'bearer', temperature: 0.3, max_tokens: null,
    skip_ssl_verify: false,
  })
  dialogVisible.value = true
}

function openEditDialog(model) {
  dialogMode.value = 'edit'
  editingKey.value = model.key
  showApiKey.value = false
  Object.assign(form, {
    key: model.key,
    engine: model.engine || '',
    name: model.name,
    api_key: '',  // ★ 编辑态不预填密钥：留空 = 不修改（防止脱敏星号回写覆盖真 key）
    endpoint: model.endpoint,
    model_id: model.model_id,
    auth_type: model.auth_type,
    temperature: model.temperature,
    max_tokens: model.max_tokens,
    skip_ssl_verify: !!(model.skip_ssl_verify || model.skip_ssl_verify === 1),
  })
  if (model.key_broken) {
    ElMessage.warning(`模型「${model.name}」的密钥已失效（被星号污染），请粘贴真实密钥后保存`)
  }
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
  // 自动派生/补全：key ← model_id/name，name ← model_id，endpoint 补 /chat/completions
  const derivedKey = form.key.trim() || deriveKey(form.model_id || form.name)
  const derivedName = form.name.trim() || form.model_id || derivedKey
  const endpoint = normalizeEndpoint(form.endpoint)
  if (!derivedKey || !derivedName || !endpoint) {
    ElMessage.warning('请填写必填字段：Key、名称、端点（或点击上方服务商模板）')
    return
  }
  try {
    await modelStore.addModel({ ...form, key: derivedKey, name: derivedName, endpoint })
    ElMessage.success('模型已添加')
    dialogVisible.value = false
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function handleUpdate() {
  const name = form.name.trim() || form.model_id || form.key
  const endpoint = normalizeEndpoint(form.endpoint)
  if (!name || !endpoint) {
    ElMessage.warning('请填写必填字段：名称、端点')
    return
  }
  try {
    await modelStore.updateModel(editingKey.value, { ...form, name, endpoint })
    ElMessage.success('模型已更新')
    dialogVisible.value = false
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function handleSetDefault(key) {
  try {
    await modelStore.setDefaultModel(key)
    ElMessage.success(`已设为默认模型：${key}`)
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
    const res = await modelsApi.fetch({
      endpoint: form.endpoint,
      api_key: form.api_key,
      skip_ssl_verify: form.skip_ssl_verify,
    })
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

async function handleQuickAdd(modelId) {
  // ★ 一步保存：复用弹窗里已填的 endpoint/api_key，直接添加，不再弹回表单二次提交
  if (!form.endpoint) {
    ElMessage.warning('请先填写端点地址')
    return
  }
  const key = deriveKey(modelId)
  const payload = {
    ...form,
    key: form.key.trim() || key,
    name: form.name.trim() || modelId,
    model_id: modelId,
    endpoint: normalizeEndpoint(form.endpoint),
  }
  try {
    await modelStore.addModel(payload)
    ElMessage.success(`模型 ${modelId} 已添加`)
    fetchDialogVisible.value = false
    // 添加模式下直接关掉表单弹窗；编辑模式下保留，避免打断正在进行的编辑
    if (dialogMode.value === 'add') dialogVisible.value = false
  } catch (err) {
    ElMessage.error(err.message)
  }
}

onMounted(() => {
  modelStore.loadModels()
})
</script>

<template>
  <div style="max-width: 1200px; margin: 0 auto; padding: 24px;">
    <el-card class="app-card" shadow="never" style="margin-bottom: 16px;">
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <h2 style="margin: 0;">AI模型管理</h2>
        <el-button type="primary" @click="openAddDialog">添加模型</el-button>
      </div>
    </el-card>

    <el-card class="app-card" shadow="never">
      <el-table :data="modelStore.models" v-loading="modelStore.loading" stripe style="width: 100%;">
      <el-table-column label="名称" width="260" show-overflow-tooltip>
        <template #default="{ row }">
          <div style="display: flex; align-items: center; gap: 6px;">
            <span>{{ modelDisplayName(row) }}</span>
            <el-tag v-if="row.key_broken" type="danger" size="small" effect="plain">密钥无效</el-tag>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="endpoint" label="端点" min-width="260" show-overflow-tooltip />
      <el-table-column prop="model_id" label="模型ID" width="160" show-overflow-tooltip />
      <el-table-column prop="auth_type" label="认证" width="76" show-overflow-tooltip />
      <el-table-column label="默认" width="70" align="center">
        <template #default="{ row }">
          <el-tag v-if="isDefaultModel(row)" type="success" size="small">默认</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button size="small" link type="primary" @click="openEditDialog(row)">编辑</el-button>
          <el-button size="small" link @click="handleTest(row.key)" :loading="testing">测试</el-button>
          <el-button
            v-if="!isDefaultModel(row)"
            size="small"
            link
            type="warning"
            @click="handleSetDefault(row.key)"
          >设为默认</el-button>
          <el-button size="small" type="danger" link @click="handleDelete(row.key)" :disabled="row.is_builtin">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    </el-card>

    <!-- 添加/编辑模型弹窗 -->
    <el-dialog v-model="dialogVisible" :title="dialogMode === 'add' ? '添加模型' : '编辑模型'" width="500px">
      <el-form :model="form" label-width="80px">
        <el-form-item v-if="dialogMode === 'add'" label="快捷模板">
          <div style="display: flex; flex-wrap: wrap; gap: 6px;">
            <el-tag
              v-for="p in PROVIDER_PRESETS"
              :key="p.label"
              size="small"
              effect="plain"
              class="preset-tag"
              style="cursor: pointer;"
              @click="applyPreset(p)"
            >{{ p.label }}</el-tag>
          </div>
        </el-form-item>
        <el-form-item label="Key" required>
          <el-input v-model="form.key" :disabled="dialogMode === 'edit'" placeholder="唯一标识，如 my-gpt4" />
        </el-form-item>
        <el-form-item label="引擎">
          <el-select
            v-model="form.engine"
            filterable
            allow-create
            default-first-option
            clearable
            placeholder="选择常见引擎或直接输入，如 阿里云"
            style="width: 100%;"
          >
            <el-option v-for="e in ENGINE_OPTIONS" :key="e" :label="e" :value="e" />
          </el-select>
          <div style="font-size: 11px; color: #909399; line-height: 1.5; margin-top: 4px;">
            填写后显示为「{{ form.engine || '引擎' }}-{{ form.name || '模型名' }}」，如 阿里云-qwen-plus；留空则直接显示名称
          </div>
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="显示名称" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="form.api_key"
            :type="showApiKey ? 'text' : 'password'"
            :placeholder="dialogMode === 'edit' ? '留空 = 保持原密钥' : 'API密钥'"
          >
            <template #suffix>
              <el-icon v-if="!showApiKey" style="cursor: pointer;" @click="showApiKey = true"><Eye /></el-icon>
              <el-icon v-else style="cursor: pointer;" @click="showApiKey = false"><EyeOff /></el-icon>
            </template>
          </el-input>
          <div v-if="dialogMode === 'edit'" style="font-size: 11px; color: #909399; line-height: 1.5; margin-top: 4px;">
            编辑时留空表示不修改原密钥；含星号的密钥无效，需重新粘贴真实密钥
          </div>
        </el-form-item>
        <el-form-item label="端点" required>
          <el-input v-model="form.endpoint" placeholder="https://api.openai.com/chat/completions" />
          <div v-if="endpointPreview" style="font-size: 11px; color: #909399; line-height: 1.5; margin-top: 4px;">
            保存为：{{ endpointPreview }}<template v-if="endpointPreview !== (form.endpoint || '').trim()">
              （已自动补全，统一带 /v1）
            </template>
          </div>
        </el-form-item>
        <el-form-item label="证书校验">
          <el-checkbox v-model="form.skip_ssl_verify" style="font-weight: normal;">
            跳过 SSL 证书校验（自签名证书/IP 直连的中转端点时勾选）
          </el-checkbox>
          <div style="font-size: 11px; color: #909399; line-height: 1.5; margin-top: 4px;">
            勾选后获取模型列表、测试连接与 AI 调用对该端点都跳过证书校验
          </div>
        </el-form-item>
        <el-form-item label="模型ID">
          <div style="display: flex; gap: 6px; align-items: center;">
            <el-input v-model="form.model_id" placeholder="API调用时的模型名" style="flex: 1;" />
            <el-tooltip content="从端点获取可用模型列表" placement="top">
              <el-button
                :icon="RefreshCcw"
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
            <el-button size="small" type="primary" link @click="handleQuickAdd(row.id)">添加</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="fetchedModels.length > 0" style="font-size: 11px; color: #909399; margin-top: 8px;">
        点击「添加」直接用已填写的端点/API Key 保存该模型
      </div>
      <div v-else-if="!fetchError" style="text-align: center; padding: 40px; color: #909399;">
        未获取到模型或接口不支持
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.preset-tag:hover {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
}
</style>
