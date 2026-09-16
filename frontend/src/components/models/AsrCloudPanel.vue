<script setup>
/**
 * 云端 ASR 引擎管理（模型管理 → ASR 模型 → 云端 ASR）
 *
 * 视觉与交互沿用 AI 模型页既有约定：app-card / card-head-* / row-more-btn 全局类，
 * el-table + 「主操作 + 更多下拉」，窄窗口隐藏次要列（避免 Element Plus 裁切导致 fixed 列覆盖）。
 */
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { RefreshCcw, ArrowDown, Eye, EyeOff } from 'lucide-vue-next'
import { useAsrStore } from '../../stores/asrStore'
import { isDialogCancel } from '../../utils/errorNotice'

const asrStore = useAsrStore()

const dialogVisible = ref(false)
const dialogMode = ref('add')          // add / edit
const editingKey = ref('')
const showApiKey = ref(false)
const saving = ref(false)
const savingSettings = ref(false)
const testing = ref(false)
const testDialogVisible = ref(false)
const testResult = ref(null)

// 默认引擎下拉：'' = 未手动指定（后端按可用性自动推荐，免费优先）
const engineChoice = ref('')

// 本地档选项来自后端引擎列表（registry 驱动：新增本地引擎无需改这里）；
// Whisper 恒定排在链尾，与后端回退链语义一致
const ENGINE_OPTIONS = computed(() => [
  { label: '自动（免费优先）', value: '' },
  { label: '云端（免费）', value: 'cloud_free' },
  { label: '云端（收费）', value: 'cloud' },
  ...(asrStore.local.engines || [])
    .slice()
    .sort((a, b) => (a.key === 'local' ? 1 : 0) - (b.key === 'local' ? 1 : 0))
    .map((e) => ({ label: `${e.name}（${e.key}）${e.ready ? '' : ' · 未就绪'}`, value: e.key })),
])

// 云端的两个槽位（费用标识由后端给：cloud=收费 / cloud_free=免费）
const ENGINE_SLOTS = [
  { label: '收费槽位（cloud）', value: 'cloud' },
  { label: '免费槽位（cloud_free）', value: 'cloud_free' },
]

const API_POSTURES = [
  { label: 'chat（chat/completions 音频多模态）', value: 'chat' },
  { label: 'multipart（/audio/transcriptions）', value: 'multipart' },
]

// 快捷模板：只放"用现有两种请求姿势真的能跑通"的服务商，避免给用户假配置
const PROVIDER_PRESETS = [
  {
    label: '小米 MiMo（付费）', name: '小米 MiMo-ASR', engine: 'cloud', api: 'chat',
    endpoint: 'https://api.xiaomimimo.com/v1/chat/completions', model: 'mimo-v2.5-asr',
    remark: '¥0.5/小时，术语最准；密钥：https://platform.xiaomimimo.com/',
  },
  {
    label: '硅基流动 XingChen（免费）', name: '硅基流动 XingChen（免费）', engine: 'cloud_free', api: 'multipart',
    endpoint: 'https://api.siliconflow.cn/v1/audio/transcriptions',
    model: 'XingChenAGI/XingChenASR-V3.2-Ultra',
    remark: '限时免费，简单视频够用；密钥：https://cloud.siliconflow.cn/account/ak',
  },
  {
    label: 'OpenAI Whisper', name: 'OpenAI Whisper', engine: 'cloud', api: 'multipart',
    endpoint: 'https://api.openai.com/v1/audio/transcriptions', model: 'whisper-1',
    remark: '$0.006/分钟；密钥：https://platform.openai.com/api-keys',
  },
]

const form = reactive({
  key: '',
  name: '',
  engine: 'cloud',
  api: 'chat',
  endpoint: '',
  api_key: '',
  model: '',
  enabled: true,
  sort_order: 100,
  remark: '',
})

const engineStatusText = computed(() => {
  const key = asrStore.recommendedEngine
  return asrStore.ENGINE_LABELS[key] || key || '未知'
})

function applyPreset(p) {
  form.name = p.name
  form.engine = p.engine
  form.api = p.api
  form.endpoint = p.endpoint
  form.model = p.model
  form.remark = p.remark
  if (!form.key) form.key = deriveKey(p.model)
}

function deriveKey(text) {
  return String(text || 'asr-engine')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 32) || 'asr-engine'
}

function openAddDialog() {
  dialogMode.value = 'add'
  editingKey.value = ''
  showApiKey.value = false
  Object.assign(form, {
    key: '', name: '', engine: 'cloud', api: 'chat', endpoint: '',
    api_key: '', model: '', enabled: true, sort_order: 100, remark: '',
  })
  dialogVisible.value = true
}

function openEditDialog(row) {
  dialogMode.value = 'edit'
  editingKey.value = row.key
  showApiKey.value = false
  Object.assign(form, {
    key: row.key,
    name: row.name,
    engine: row.engine,
    api: row.api,
    endpoint: row.endpoint,
    api_key: '',   // ★ 不回填密钥：留空 = 不修改（防脱敏串回写覆盖真密钥）
    model: row.model || '',
    enabled: !!row.enabled,
    sort_order: row.sort_order ?? 100,
    remark: row.remark || '',
  })
  if (row.key_broken) {
    ElMessage.warning(`「${row.name}」的密钥已失效（被脱敏串污染），请重新粘贴真实密钥后保存`)
  }
  dialogVisible.value = true
}

async function handleSubmit() {
  if (!form.name.trim()) { ElMessage.warning('请填写名称'); return }
  if (!form.endpoint.trim()) { ElMessage.warning('请填写端点地址'); return }
  saving.value = true
  try {
    if (dialogMode.value === 'add') {
      if (!form.key.trim()) { form.key = deriveKey(form.model || form.name) }
      await asrStore.addProvider({ ...form })
      ElMessage.success('云端 ASR 引擎已添加')
    } else {
      await asrStore.updateProvider(editingKey.value, { ...form })
      ElMessage.success('云端 ASR 引擎已更新')
    }
    dialogVisible.value = false
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    saving.value = false
  }
}

function rowToPayload(row, overrides = {}) {
  return {
    key: row.key,
    name: row.name,
    engine: row.engine,
    api: row.api,
    endpoint: row.endpoint,
    api_key: '',   // 留空 = 后端保留原密钥
    model: row.model || '',
    enabled: !!row.enabled,
    sort_order: row.sort_order ?? 100,
    remark: row.remark || '',
    ...overrides,
  }
}

async function handleToggleEnabled(row, value) {
  try {
    await asrStore.updateProvider(row.key, rowToPayload(row, { enabled: value }))
    ElMessage.success(value ? `已启用「${row.name}」` : `已停用「${row.name}」`)
  } catch (err) {
    ElMessage.error(err.message)
    // 失败回滚开关显示（否则 UI 会与后端状态不一致，形成"假启用"）
    row.enabled = !value
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(
      `确认删除云端 ASR 引擎「${row.name}」？删除后该槽位将改用下一个可用引擎。`,
      '提示', { type: 'warning' },
    )
    await asrStore.deleteProvider(row.key)
    ElMessage.success('已删除')
  } catch (err) {
    if (!isDialogCancel(err)) ElMessage.error(err?.message || err)
  }
}

function handleRowCommand(cmd, row) {
  if (cmd === 'test') { handleTest(row); return }
  if (cmd === 'delete') { handleDelete(row); return }
}

async function handleTest(row) {
  testing.value = true
  testResult.value = null
  testDialogVisible.value = true
  try {
    const res = await asrStore.testProvider(row.key)
    testResult.value = { ...res, name: row.name }
    if (res.success) {
      ElMessage.success(`「${row.name}」链路正常 (${res.latency_ms}ms)`)
    } else {
      ElMessage.error(`「${row.name}」测试失败`)
    }
  } catch (err) {
    testResult.value = { success: false, error: err.message, name: row.name }
    ElMessage.error(err.message)
  } finally {
    testing.value = false
  }
}

async function handleEngineChange(value) {
  savingSettings.value = true
  try {
    await asrStore.saveSettings({ default_engine: value })
    ElMessage.success(value ? '默认转录引擎已保存' : '已恢复自动推荐')
  } catch (err) {
    engineChoice.value = asrStore.settings.default_engine || ''
    ElMessage.error(err.message)
  } finally {
    savingSettings.value = false
  }
}

async function reload() {
  await asrStore.loadOverview()
  if (asrStore.error) ElMessage.error(`加载失败：${asrStore.error}`)
}

// 窄窗口（< 1240px）隐藏次要列：全列宽约 990px，低于该宽度时 Element Plus 会裁切
// 超出部分并让 fixed 操作列覆盖相邻列（视觉重叠）
const COMPACT_WIDTH = 1240
const isCompact = ref(window.innerWidth < COMPACT_WIDTH)
function updateCompact() {
  isCompact.value = window.innerWidth < COMPACT_WIDTH
}

onMounted(async () => {
  window.addEventListener('resize', updateCompact)
  await asrStore.loadOverview()
  engineChoice.value = asrStore.settings.default_engine || ''
})
onUnmounted(() => window.removeEventListener('resize', updateCompact))
</script>

<template>
  <div>
    <!-- 默认转录引擎（工作台默认值来源） -->
    <el-card class="app-card" shadow="never" style="margin-bottom: 16px;">
      <div class="card-head-row">
        <span class="wb-card-title">默认转录引擎</span>
        <span class="card-head-hint">工作台提交任务时的默认引擎；此处设置优先于"免费优先"自动推荐</span>
      </div>
      <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 12px;">
        <el-select
          v-model="engineChoice"
          style="width: 280px;"
          :loading="savingSettings"
          @change="handleEngineChange"
        >
          <el-option v-for="o in ENGINE_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
        <el-tag type="info" effect="plain">当前生效：{{ engineStatusText }}</el-tag>
        <el-tag v-if="asrStore.settings.default_engine" type="success" effect="plain">已手动指定</el-tag>
        <span v-else class="card-head-hint">未手动指定，按可用性自动推荐</span>
      </div>
      <p class="adv-field-hint" style="margin-top: 8px;">
        任一引擎失败会按回退链自动降级（云端收费 → 云端免费 → 本地 FireRedASR → 本地 Whisper），
        成本只降不升：免费引擎失败绝不会自动转收费引擎。
      </p>
    </el-card>

    <!-- 云端引擎列表 -->
    <el-card class="app-card" shadow="never">
      <div class="card-head-row">
        <span class="wb-card-title">云端 ASR 引擎</span>
        <span class="card-head-hint">同一槽位可挂多个引擎，运行时取优先级最小且已启用者</span>
        <div class="card-head-actions">
          <el-button size="small" :icon="RefreshCcw" :loading="asrStore.loading" @click="reload">刷新</el-button>
          <el-button size="small" type="primary" @click="openAddDialog">添加引擎</el-button>
        </div>
      </div>

      <el-alert
        v-if="asrStore.error"
        type="error"
        show-icon
        :closable="false"
        style="margin-top: 12px;"
        :title="`加载失败：${asrStore.error}`"
      />
      <el-table
        v-else
        :data="asrStore.providers"
        v-loading="asrStore.loading"
        stripe
        style="width: 100%; margin-top: 12px;"
      >
        <el-table-column label="名称" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
              <span>{{ row.name }}</span>
              <el-tag v-if="row.runtime" type="success" size="small" effect="plain">正在使用</el-tag>
              <el-tag v-if="row.is_builtin" type="info" size="small" effect="plain">内置</el-tag>
              <el-tag v-if="row.key_broken" type="danger" size="small" effect="plain">密钥无效</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="槽位" min-width="110">
          <template #default="{ row }">
            <!-- 收费标识单点在后端（provider.fee）：前端只显示，不按 engine 名推断 -->
            <el-tag :type="row.fee === '免费' ? 'success' : 'warning'" size="small" effect="plain">
              {{ row.fee === '免费' ? '免费槽位' : '收费槽位' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="endpoint" label="端点" min-width="220" show-overflow-tooltip />
        <el-table-column v-if="!isCompact" prop="model" label="模型" min-width="150" show-overflow-tooltip />
        <el-table-column v-if="!isCompact" label="请求姿势" min-width="110">
          <template #default="{ row }">
            <span class="mono-tag">{{ row.api }}</span>
          </template>
        </el-table-column>
        <el-table-column v-if="!isCompact" label="密钥" min-width="100">
          <template #default="{ row }">
            <el-tag v-if="row.key_broken" type="danger" size="small" effect="plain">需重填</el-tag>
            <el-tag v-else-if="row.has_key" type="success" size="small" effect="plain">已配置</el-tag>
            <el-tag v-else type="danger" size="small" effect="plain">未配置</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="80" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="row.enabled"
              size="small"
              @update:model-value="(v) => handleToggleEnabled(row, v)"
            />
          </template>
        </el-table-column>
        <!-- 操作列：编辑常显，「更多」下拉收纳测试连接/删除，避免窄窗口下覆盖相邻列 -->
        <el-table-column label="操作" min-width="130" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="openEditDialog(row)">编辑</el-button>
            <el-dropdown class="row-more-dropdown" trigger="click" @command="(cmd) => handleRowCommand(cmd, row)">
              <el-button size="small" link class="row-more-btn">
                更多<el-icon class="row-more-arrow"><ArrowDown /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="test">测试连接</el-dropdown-item>
                  <el-dropdown-item divided command="delete" :disabled="row.is_builtin">删除</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有云端 ASR 引擎">
            <el-button type="primary" @click="openAddDialog">添加云端 ASR 引擎</el-button>
          </el-empty>
        </template>
      </el-table>
    </el-card>

    <!-- 添加/编辑引擎弹窗 -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogMode === 'add' ? '添加云端 ASR 引擎' : '编辑云端 ASR 引擎'"
      width="560px"
    >
      <el-form :model="form" label-width="92px">
        <el-form-item v-if="dialogMode === 'add'" label="快捷模板">
          <div style="display: flex; flex-wrap: wrap; gap: 6px;">
            <el-tag
              v-for="p in PROVIDER_PRESETS"
              :key="p.label"
              size="small"
              effect="plain"
              style="cursor: pointer;"
              @click="applyPreset(p)"
            >{{ p.label }}</el-tag>
          </div>
        </el-form-item>
        <el-form-item label="标识" required>
          <el-input v-model="form.key" :disabled="dialogMode === 'edit'" placeholder="唯一标识，如 my-relay（小写字母/数字/-/_）" />
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="显示名称，如 小米 MiMo-ASR" />
        </el-form-item>
        <el-form-item label="引擎槽位">
          <el-select v-model="form.engine" style="width: 100%;">
            <el-option v-for="o in ENGINE_SLOTS" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <p class="adv-field-hint">决定它在回退链中的位置：收费槽位（cloud）失败可降免费槽位；免费槽位绝不升级收费。</p>
        </el-form-item>
        <el-form-item label="请求姿势">
          <el-select v-model="form.api" style="width: 100%;">
            <el-option v-for="o in API_POSTURES" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <p class="adv-field-hint">
            音频模型走 chat/completions 选 chat；OpenAI 兼容转写接口选 multipart。
            厂商原生协议（如百炼 DashScope 原生格式）暂不支持，需其 OpenAI 兼容端点。
          </p>
        </el-form-item>
        <el-form-item label="端点" required>
          <el-input v-model="form.endpoint" placeholder="https://api.example.com/v1/chat/completions" />
        </el-form-item>
        <el-form-item label="模型名">
          <el-input v-model="form.model" placeholder="如 mimo-v2.5-asr / whisper-1" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="form.api_key"
            :type="showApiKey ? 'text' : 'password'"
            :placeholder="dialogMode === 'edit' ? '留空 = 保持原密钥' : 'API 密钥'"
          >
            <template #suffix>
              <el-icon v-if="!showApiKey" style="cursor: pointer;" @click="showApiKey = true"><Eye /></el-icon>
              <el-icon v-else style="cursor: pointer;" @click="showApiKey = false"><EyeOff /></el-icon>
            </template>
          </el-input>
          <p v-if="dialogMode === 'edit'" class="adv-field-hint">
            编辑时留空表示不修改原密钥；含星号的密钥无效，需重新粘贴真实密钥
          </p>
        </el-form-item>
        <el-form-item label="优先级">
          <el-input-number v-model="form.sort_order" :min="0" :max="9999" controls-position="right" />
          <span style="font-size: 11px; color: #909399; margin-left: 6px;">同槽位内数字越小越先用</span>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" placeholder="如密钥获取地址、计费说明" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
          <span style="font-size: 11px; color: #909399; margin-left: 8px;">停用后该引擎不参与路由</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSubmit">
          {{ dialogMode === 'add' ? '添加' : '保存' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 测试连接结果 -->
    <el-dialog v-model="testDialogVisible" title="测试连接" width="520px">
      <div v-if="testing" style="text-align: center; padding: 24px;">
        <el-icon class="is-loading" style="font-size: 20px;"><RefreshCcw /></el-icon>
        <p class="adv-field-hint" style="margin-top: 8px;">正在用 2 秒探测音频真实调用一次…</p>
      </div>
      <template v-else-if="testResult">
        <el-alert
          :type="testResult.success ? 'success' : 'error'"
          show-icon
          :closable="false"
          :title="testResult.success ? `链路正常（${testResult.latency_ms}ms）` : `测试失败（${testResult.latency_ms}ms）`"
        />
        <div v-if="testResult.success" style="margin-top: 12px;">
          <p class="adv-field-hint">{{ testResult.note }}</p>
          <p class="adv-field-hint">端点：{{ testResult.endpoint }}</p>
          <p class="adv-field-hint">模型：{{ testResult.model || '（未填写）' }} · 请求姿势：{{ testResult.api }}</p>
          <p v-if="testResult.text" style="margin-top: 8px;">返回文本：{{ testResult.text }}</p>
        </div>
        <div v-else style="margin-top: 12px;">
          <p style="color: var(--el-color-danger); word-break: break-all;">{{ testResult.error }}</p>
        </div>
      </template>
    </el-dialog>
  </div>
</template>
