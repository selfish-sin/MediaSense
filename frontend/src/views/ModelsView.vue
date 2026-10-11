<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { RefreshCcw, Eye, EyeOff, ArrowDown } from 'lucide-vue-next'
import { useModelStore } from '../stores/modelStore'
import { modelsApi } from '../utils/api'
import { modelDisplayName } from '../utils/modelDisplay'
import { isDialogCancel } from '../utils/errorNotice'
import { useContainerWidth } from '../composables/useContainerWidth'
import { MODELS_COLUMN_BREAKPOINTS, resolveTableLayout } from '../utils/tableLayout'
import AsrPanel from '../components/models/AsrPanel.vue'
import FallbackChainPanel from '../components/models/FallbackChainPanel.vue'

const modelStore = useModelStore()

// 一级分域：llm = 文本模型（原页面内容）/ asr = 转录引擎（云端 + 本地）
const activeDomain = ref('llm')

const dialogVisible = ref(false)
const dialogMode = ref('add')  // add / edit
const editingKey = ref('')
const showApiKey = ref(false)
const testing = ref(false)
const testResult = ref(null)
const healthChecking = ref(false)
// key -> 体检结果；空表示本次没测过（表头不显示状态，不假装已体检）
const healthMap = ref({})
const healthSummary = ref(null)
// 单模型测试失败的常驻告警（title=现象，hint=修法）；成功/未测时为 null
const testAlert = ref(null)
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
  // auth_type 不在 UI 暴露（2026-09-13：移除表格「认证」列与表单「认证类型」项），
  // 但提交时必须回传：后端 ModelConfigCreate.auth_type 默认 bearer，PUT 不带它
  // 会把已有的 direct/none 模型悄悄改成 bearer。新建走默认 bearer，编辑回填原值。
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
// ⚠ 端点里的版本段（/v1、/v3、/v4）由后端 _normalize_endpoint 识别并原样保留，
//   不会瞎插 /v1 —— 火山方舟的 /api/v3 与智谱的 /api/paas/v4 都依赖这条规则。
const PROVIDER_PRESETS = [
  { label: 'DeepSeek', engine: 'DeepSeek', endpoint: 'https://api.deepseek.com/v1/chat/completions', modelHint: 'deepseek-chat', auth: 'bearer' },
  { label: '阿里云百炼', engine: '阿里云', endpoint: 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions', modelHint: 'qwen3.8-flash', auth: 'bearer' },
  { label: '火山方舟', engine: '字节豆包', endpoint: 'https://ark.cn-beijing.volces.com/api/v3/chat/completions', modelHint: 'doubao-seed-2-1-turbo-260628', auth: 'bearer' },
  // 方舟 Agent Plan 是独立套餐，凭「Agent Plan-开发配置」的专属 key 调用，
  // 端点带 /api/plan 段。它和普通方舟 key 不是一回事：普通 key 调 plan 端点会报
  // 403 "invalid plan type"，Agent Plan 专属 key 调 /api/v3 也会被拒。
  { label: '方舟 Agent Plan', engine: '火山方舟', endpoint: 'https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions', modelHint: 'deepseek-v4-flash', auth: 'bearer' },
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
    if (!isDialogCancel(err)) ElMessage.error(err?.message || err)
  }
}

// 行内「更多」下拉：command 路由到既有处理函数（测试/设为默认/删除）
function handleRowCommand(cmd, row) {
  if (cmd === 'test') { handleTest(row.key); return }
  if (cmd === 'setDefault') { handleSetDefault(row.key); return }
  if (cmd === 'delete') { handleDelete(row.key); return }
}

// ★ 失败类别文案：与后端 _classify_failure 的 kind 一一对应（改一处要同步另一处）。
//   存在的意义：401/403/404/429/网络不通原本一律叫「连接失败」，用户无从判断该改密钥、
//   找客服开权限、改模型 ID 还是关代理——分类后每类都带明确修法。
const HEALTH_KIND = {
  ok: { label: '正常', type: 'success' },
  no_key: { label: '未设密钥', type: 'info' },
  network: { label: '网络/代理不通', type: 'danger' },
  tls: { label: '证书校验失败', type: 'warning' },
  auth: { label: '密钥无效', type: 'danger' },
  forbidden: { label: '账号无权限', type: 'danger' },
  forbidden_realname: { label: '账号未实名', type: 'warning' },
  model: { label: '模型ID不存在', type: 'danger' },
  endpoint: { label: '端点路径不对', type: 'danger' },
  rate_limit: { label: '被限流', type: 'warning' },
  quota: { label: '额度/余额不足', type: 'warning' },
  server: { label: '服务端错误', type: 'warning' },
  bad_request: { label: '请求被拒绝', type: 'danger' },
  unknown: { label: '未知错误', type: 'info' },
}
function healthKindMeta(kind) {
  return HEALTH_KIND[kind] || { label: kind || '未知错误', type: 'info' }
}
const healthFailed = computed(() => Object.values(healthMap.value).filter((r) => !r.ok))
function healthOf(key) {
  return healthMap.value[key] || null
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
      const meta = healthKindMeta(res.kind)
      ElMessage.error(`连接失败（${meta.label}）: ${res.error}`)
      // 修法不塞进 toast（会被截断），单测失败也在表格上方常驻告警里写清楚怎么修
      testAlert.value = res.hint
        ? { type: 'error', title: `${key} 连接失败（${meta.label}）：${res.error}`, hint: res.hint }
        : { type: 'error', title: `${key} 连接失败：${res.error}`, hint: '' }
    }
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    testing.value = false
  }
}

/** 一键体检：真实发一次最小请求探测全部模型（后端 4 路并发，单个 20s 超时）。 */
async function runHealthCheck() {
  healthChecking.value = true
  try {
    const res = await modelStore.healthCheck()
    const map = {}
    for (const r of res.results || []) map[r.key] = r
    healthMap.value = map
    healthSummary.value = res.summary || null
    const s = healthSummary.value
    if (s && s.failed) {
      const ring = s.failed_in_ring ? `，其中 ${s.failed_in_ring} 个还在兜底环里` : ''
      ElMessage.warning(`体检完成：${s.ok}/${s.total} 正常，${s.failed} 个异常${ring}`)
    } else {
      ElMessage.success(`体检完成：${s?.total ?? 0} 个模型全部正常`)
    }
  } catch (err) {
    ElMessage.error(err?.message || String(err))
  } finally {
    healthChecking.value = false
  }
}

/**
 * 把体检判定的"硬失败"模型移出兜底环。
 * 硬失败 = 密钥/权限/实名/模型不存在/端点错/未设密钥——这些重试永远不会成功，
 * 留在环里只会让每次换模型都白撞一次（用户体感就是"又连不上"）。
 * 限流/服务端错误/网络不通**不算**硬失败：它们会自愈，摘掉等于少一个真备胎。
 */
const HARD_FAIL_KINDS = ['auth', 'forbidden', 'forbidden_realname', 'model', 'endpoint', 'no_key', 'quota']
const hardFailed = computed(() => healthFailed.value.filter((r) => HARD_FAIL_KINDS.includes(r.kind)))

async function removeHardFailedFromRing() {
  if (!hardFailed.value.length) return
  const cfg = await modelStore.loadFallbackChain()
  const bad = new Set(hardFailed.value.map((r) => r.key))
  const next = (cfg.chain || []).filter((k) => !bad.has(k))
  const removed = (cfg.chain || []).length - next.length
  if (!removed) {
    ElMessage.info('这些异常模型本来就不在兜底环里')
    return
  }
  try {
    await ElMessageBox.confirm(
      `将移出兜底环（模型本身不会被删除，随时可加回）：\n${[...bad].join('、')}`,
      '确认清理兜底环',
      { type: 'warning' },
    )
  } catch (err) {
    if (isDialogCancel(err)) return
    throw err
  }
  const saved = await modelStore.saveFallbackChain(next, cfg.enabled)
  ElMessage.success(`已移出 ${removed} 个失效模型，兜底环剩 ${(saved.chain || []).length} 个候选`)
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
      // ★ 编辑态带上 key：编辑表单不回填密钥（防污染），后端在表单密钥留空时回退 DB 原密钥，
      //   否则空密钥直发会被服务端 401 Invalid token
      ...(dialogMode.value === 'edit' && editingKey.value ? { key: editingKey.value } : {}),
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
    // ★ 自签名证书自动放行：后端已跳过校验并（按key拉取时）写回 DB——这里直接勾上「跳过SSL校验」，
    //   添加流程保存时标记随之入库；编辑已存模型时后端已自愈，无需额外操作
    if (res.ssl_bypassed) {
      form.skip_ssl_verify = true
      ElMessage.info('服务器证书为自签名证书，已自动跳过证书校验')
    }
    fetchedModels.value = res.models || []
    if (fetchedModels.value.length > 0) {
      ElMessage.success(`获取到 ${fetchedModels.value.length} 个模型`)
    } else {
      ElMessage.warning('未获取到模型')
    }
  }
}

function handlePickFetched(row) {
  // ★ 选取即回填表单：模型ID 直接填入，名称空缺时自动带上；
  //   用户确认端点/密钥后点「添加/保存」落库（编辑已存模型 = 把模型ID换到当前模型上）
  form.model_id = row.id
  if (!form.name.trim()) form.name = row.id
  fetchDialogVisible.value = false
  ElMessage.success(`已选用模型 ${row.id}，确认后点「${dialogMode.value === 'edit' ? '保存' : '添加'}」`)
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

// ★ 列布局按「表格容器实际宽度」分档（同历史页：窗口宽度会被侧栏/内边距误导）
//   full 全列 / compact 隐藏模型ID·默认 / narrow 端点并入名称单元格、操作列不吸边
const tableWrapRef = ref(null)
const tableWidth = useContainerWidth(tableWrapRef, window.innerWidth)
const layout = computed(() => resolveTableLayout(tableWidth.value, MODELS_COLUMN_BREAKPOINTS))

onMounted(() => {
  modelStore.loadModels()
})
</script>

<template>
  <div class="page-container">
    <el-card class="app-card" shadow="never">
      <div class="page-header">
        <div class="page-header-text">
          <h2 class="page-title">模型管理</h2>
          <p class="page-subtitle">LLM 文本模型与 ASR 语音模型分域管理，配置与默认值互不影响</p>
        </div>
        <div class="page-header-actions">
          <el-button v-if="activeDomain === 'llm'" type="primary" @click="openAddDialog">添加模型</el-button>
        </div>
      </div>
    </el-card>

    <!-- 一级分域：LLM 文本模型 / ASR 语音模型。两者配置、密钥、默认值完全独立。
         约定：el-tabs 本身不套外壳卡片，卡片只出现在各 pane 内容里（全站统一）。 -->
    <el-tabs v-model="activeDomain">
      <el-tab-pane name="llm">
        <template #label>LLM 模型</template>
        <el-card class="app-card" shadow="never">
          <div class="card-head-row" style="margin-bottom: 12px;">
            <span class="wb-card-title">LLM 文本模型</span>
            <span class="card-head-hint">用于总结、AI 校正、打标签、思维导图与追问</span>
            <div style="margin-left: auto; display: flex; gap: 8px; align-items: center;">
              <el-tooltip content="真实发一次最小请求，逐个探测全部模型；失败会列出真实原因与修法（会消耗极少量额度）" placement="top">
                <el-button :loading="healthChecking" @click="runHealthCheck">
                  <el-icon><RefreshCcw /></el-icon>
                  <span style="margin-left: 4px;">一键体检</span>
                </el-button>
              </el-tooltip>
              <el-tooltip v-if="hardFailed.length" content="把密钥/权限/实名/模型不存在这类永远不会成功的模型移出兜底环（模型本身不删除）" placement="top">
                <el-button type="warning" plain @click="removeHardFailedFromRing">
                  移出兜底环（{{ hardFailed.length }}）
                </el-button>
              </el-tooltip>
            </div>
          </div>

          <!-- 体检结论常驻展示：失败原因 + 该怎么修。修法写在告警里而不是 toast，
               因为 toast 会被长文案截断，而"限流"和"没权限"用户要做的完全不同。 -->
          <el-alert
            v-if="healthFailed.length"
            type="warning" show-icon :closable="false" style="margin-bottom: 12px;"
          >
            <template #title>体检发现 {{ healthFailed.length }} 个模型连不上（共 {{ healthSummary?.total ?? 0 }} 个，{{ healthSummary?.ok ?? 0 }} 个正常）</template>
            <div style="font-size: 12px; line-height: 1.8;">
              <div v-for="r in healthFailed" :key="r.key">
                <el-tag size="small" :type="healthKindMeta(r.kind).type" style="margin-right: 6px;">{{ healthKindMeta(r.kind).label }}</el-tag>
                <b>{{ r.key }}</b>
                <span v-if="r.in_fallback_ring" style="color: var(--el-color-warning);">（在兜底环中，换模型时会被反复尝试）</span>
                <div>{{ r.hint }}</div>
                <div style="font-size: 11px; color: var(--el-text-color-secondary);">服务端原话：{{ r.reason }}</div>
              </div>
            </div>
          </el-alert>

          <el-alert
            v-if="testAlert"
            :type="testAlert.type" show-icon closable style="margin-bottom: 12px;"
            @close="testAlert = null"
          >
            <template #title>{{ testAlert.title }}</template>
            <div v-if="testAlert.hint" style="font-size: 12px;">{{ testAlert.hint }}</div>
          </el-alert>

          <div ref="tableWrapRef" class="table-wrap">
          <el-table :data="modelStore.models" v-loading="modelStore.loading" stripe style="width: 100%;">
      <!-- 列宽一律 min-width：容器变窄时由 Element Plus 触发横向滚动（固定 width 会被裁切）；
           narrow 档位把端点并入「名称」单元格第二行，少一列就不必横向滚动 -->
      <el-table-column label="名称" min-width="200" :show-overflow-tooltip="layout !== 'narrow'">
        <template #default="{ row }">
          <div class="cell-stack">
            <span class="cell-stack__row">
              <span class="cell-stack__text">{{ modelDisplayName(row) }}</span>
              <el-tag v-if="row.key_broken" type="danger" size="small" effect="plain">密钥无效</el-tag>
            </span>
            <span v-if="layout === 'narrow'" class="cell-stack__sub" :title="row.endpoint">{{ row.endpoint }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column v-if="layout !== 'narrow'" prop="endpoint" label="端点" min-width="240" show-overflow-tooltip />
      <!-- 状态列只在体检跑过后出现：没测过就显示"未体检"，不拿空白冒充已检测 -->
      <el-table-column v-if="healthSummary && layout !== 'narrow'" label="体检状态" min-width="130" align="center">
        <template #default="{ row }">
          <el-tag v-if="healthOf(row.key)" size="small"
                  :type="healthOf(row.key).ok ? 'success' : healthKindMeta(healthOf(row.key).kind).type">
            {{ healthOf(row.key).ok ? `${healthOf(row.key).latency_ms}ms` : healthKindMeta(healthOf(row.key).kind).label }}
          </el-tag>
          <span v-else style="font-size: 11px; color: var(--el-text-color-placeholder);">未体检</span>
        </template>
      </el-table-column>
      <el-table-column v-if="layout === 'full'" prop="model_id" label="模型ID" min-width="130" show-overflow-tooltip />
      <el-table-column v-if="layout === 'full'" label="默认" min-width="60" align="center">
        <template #default="{ row }">
          <el-tag v-if="isDefaultModel(row)" type="success" size="small">默认</el-tag>
        </template>
      </el-table-column>
      <!-- 操作列：编辑常显，「更多」下拉收纳测试/设默认/删除；narrow 档位不吸边（靠横向滚动而非覆盖相邻列） -->
      <el-table-column label="操作" min-width="130" :fixed="layout === 'narrow' ? false : 'right'">
        <template #default="{ row }">
          <el-button size="small" link type="primary" @click="openEditDialog(row)">编辑</el-button>
          <el-dropdown class="row-more-dropdown" trigger="click" @command="(cmd) => handleRowCommand(cmd, row)">
            <el-button size="small" link class="row-more-btn">
              更多<el-icon class="row-more-arrow"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="test">测试连接</el-dropdown-item>
                <el-dropdown-item v-if="!isDefaultModel(row)" command="setDefault">设为默认</el-dropdown-item>
                <el-dropdown-item divided command="delete" :disabled="row.is_builtin">删除</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
      </el-table-column>
          </el-table>
          </div>
        </el-card>

        <!-- 兜底链配置：同页跟在模型表下方（用户配完模型立刻能排链，不必跳页） -->
        <FallbackChainPanel />
      </el-tab-pane>

      <!-- ASR 域内部再小分「云端 / 本地」（AsrPanel 内二级分段）；
           lazy：未点开 ASR 页签前不挂载，避免无谓请求与本地磁盘遍历 -->
      <el-tab-pane label="ASR 模型" name="asr" lazy>
        <AsrPanel />
      </el-tab-pane>
    </el-tabs>

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
          <el-table
            v-if="fetchedModels.length > 0"
            :data="fetchedModels"
            max-height="400"
            size="small"
            stripe
            highlight-current-row
            @row-click="handlePickFetched"
          >
            <el-table-column prop="id" label="模型ID" min-width="200" show-overflow-tooltip />
            <el-table-column label="操作" width="110" align="center">
              <template #default="{ row }">
                <el-button size="small" type="primary" link @click.stop="handlePickFetched(row)">选用</el-button>
                <el-button size="small" link @click.stop="handleQuickAdd(row.id)">添加</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div v-if="fetchedModels.length > 0" style="font-size: 11px; color: #909399; margin-top: 8px;">
            点击「选用」或行本身：把该模型填入表单，确认后点「添加/保存」；「添加」直接用已填端点/密钥存为新模型
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
