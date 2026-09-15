<script setup>
/**
 * 本地 ASR 管理（模型管理 → ASR 模型 → 本地 ASR）
 *
 * 两块：
 * 1) 本地 Whisper 四档（tiny/base/small/medium）：就绪状态、磁盘占用、默认档位与转录设备
 * 2) 本地引擎列表：**由后端注册表驱动**（overview.local.engines），每条列出
 *    名称 / 免费标识 / 语言 / 说明 / 体积 / 就绪 / 占用 / 文件明细 / 下载 / 删除
 *    —— 后端新增本地引擎时本页零改动（filter 掉 key==='local' 的 Whisper 条目，它由上面那块展示）
 *
 * 视觉沿用 app-card / card-head-* / row-more-btn 全局类；不引入新风格。
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { RefreshCcw, ArrowDown } from 'lucide-vue-next'
import { useAsrStore } from '../../stores/asrStore'
import { isDialogCancel } from '../../utils/errorNotice'

const asrStore = useAsrStore()

const savingSettings = ref(false)
const downloadingEngine = ref('')
const removingEngine = ref('')

const WHISPER_SIZES = [
  { value: 'tiny', label: 'tiny（最快，精度最低）' },
  { value: 'base', label: 'base（默认）' },
  { value: 'small', label: 'small' },
  { value: 'medium', label: 'medium（最准，最慢）' },
]

const DEVICES = [
  { value: 'auto', label: '自动（有 GPU 用 GPU，否则 CPU）' },
  { value: 'cuda', label: 'CUDA（不可用时自动回退 CPU）' },
  { value: 'cpu', label: 'CPU（强制）' },
]

const whisper = computed(() => asrStore.local.whisper || { models: [], default_model: '', device: 'auto' })
const sherpaAvailable = computed(() => asrStore.local.sherpa_available === true)
// 引擎列表去掉 Whisper（key='local'）：它有自己的档位块，避免同一引擎出现两次
const engines = computed(() => (asrStore.local.engines || []).filter((e) => e.key !== 'local'))

const IDLE_DOWNLOAD = { status: 'idle', percent: 0, message: '', error: '' }
function engineDownload(eng) {
  return eng.download || IDLE_DOWNLOAD
}
function isDownloading(eng) {
  return engineDownload(eng).status === 'downloading'
}

// 可写 computed：显示值来自后端已保存状态，写入即保存——
// 保存失败时显示值自动回到后端真值（不会出现"UI 已改、后端没改"的假成功）
const whisperModelChoice = computed({
  get: () => whisper.value.default_model || '',
  set: (v) => handleSaveSettings({ whisper_model: v }),
})
const deviceChoice = computed({
  get: () => whisper.value.device || 'auto',
  set: (v) => handleSaveSettings({ whisper_device: v }),
})

function humanBytes(n) {
  const size = Number(n || 0)
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  return `${(size / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function whisperStatus(row) {
  if (row.ready) return { type: 'success', text: '已就绪' }
  if (row.partial) return { type: 'warning', text: '不完整' }
  return { type: 'info', text: '未安装' }
}

async function handleSaveSettings(patch) {
  savingSettings.value = true
  try {
    await asrStore.saveSettings(patch)
    ElMessage.success('设置已保存')
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    savingSettings.value = false
  }
}

async function handleRemoveWhisper(row) {
  try {
    await ElMessageBox.confirm(
      `确认删除本地 Whisper（${row.size}）模型？将释放 ${humanBytes(row.bytes)}，删除后该档位需重新放置模型文件。`,
      '提示', { type: 'warning' },
    )
    removingEngine.value = 'local'
    const res = await asrStore.removeWhisper(row.size)
    ElMessage.success(`已删除，释放 ${humanBytes(res.freed_bytes)}`)
  } catch (err) {
    if (!isDialogCancel(err)) ElMessage.error(err?.message || err)
  } finally {
    removingEngine.value = ''
  }
}

// ── 下载进度轮询（仅下载中启动；离开页面/完成即停止，不留残留定时器）──
let pollTimer = null
let pollingEngine = ''
function startPolling(engine) {
  if (pollTimer) return
  pollingEngine = engine
  pollTimer = setInterval(async () => {
    try {
      const res = await asrStore.refreshDownloadState(pollingEngine)
      if (res.download?.status !== 'downloading') {
        stopPolling()
        if (res.download?.status === 'done') {
          ElMessage.success('模型下载完成')
          await asrStore.loadOverview()
        }
      }
    } catch (err) {
      // 轮询失败不打断：连续失败由用户手动刷新兜底，这里只在控制台留痕避免刷屏
      console.warn('查询下载状态失败:', err.message)
    }
  }, 2000)
}
function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  pollingEngine = ''
}

async function handleStartDownload(eng) {
  downloadingEngine.value = eng.key
  try {
    const res = await asrStore.startEngineDownload(eng.key)
    if (res.started) {
      startPolling(eng.key)
      ElMessage.success(res.message)
    } else {
      ElMessage.info(res.message)
    }
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    downloadingEngine.value = ''
  }
}

async function handleCancelDownload(eng) {
  try {
    await asrStore.cancelEngineDownload(eng.key)
    ElMessage.info('已请求取消下载')
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function handleRemoveEngine(eng) {
  try {
    await ElMessageBox.confirm(
      `确认删除${eng.name} 模型？将释放 ${humanBytes(eng.bytes)}，删除后可再次下载。`,
      '提示', { type: 'warning' },
    )
    removingEngine.value = eng.key
    const res = await asrStore.removeEngine(eng.key)
    ElMessage.success(res.message)
  } catch (err) {
    if (!isDialogCancel(err)) ElMessage.error(err?.message || err)
  } finally {
    removingEngine.value = ''
  }
}

async function reload() {
  await asrStore.loadOverview()
  if (asrStore.error) ElMessage.error(`加载失败：${asrStore.error}`)
  // 刷新后若仍有引擎在下载，恢复轮询
  const active = engines.value.find((e) => isDownloading(e))
  if (active) startPolling(active.key)
}

function handleWhisperCommand(cmd, row) {
  if (cmd === 'remove') { handleRemoveWhisper(row); return }
}

onMounted(async () => {
  await asrStore.loadOverview()
  const active = engines.value.find((e) => isDownloading(e))
  if (active) startPolling(active.key)
})
onUnmounted(stopPolling)
</script>

<template>
  <div>
    <!-- 本地 Whisper -->
    <el-card class="app-card" shadow="never" style="margin-bottom: 16px;">
      <div class="card-head-row">
        <span class="wb-card-title">本地 Whisper</span>
        <el-tag type="success" size="small" effect="plain">免费</el-tag>
        <span class="card-head-hint">末位兜底引擎，任何云端/本地引擎失败都可回退到它</span>
        <div class="card-head-actions">
          <el-button size="small" :icon="RefreshCcw" :loading="asrStore.loading" @click="reload">刷新</el-button>
        </div>
      </div>

      <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 12px;">
        <span class="card-head-hint">默认档位</span>
        <el-select v-model="whisperModelChoice" style="width: 240px;" :loading="savingSettings">
          <el-option v-for="o in WHISPER_SIZES" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
        <span class="card-head-hint">转录设备</span>
        <el-select v-model="deviceChoice" style="width: 280px;" :loading="savingSettings">
          <el-option v-for="o in DEVICES" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
      </div>

      <el-table :data="whisper.models" v-loading="asrStore.loading" stripe style="width: 100%; margin-top: 12px;">
        <el-table-column prop="size" label="档位" min-width="110">
          <template #default="{ row }">
            <span>{{ row.size }}</span>
            <el-tag
              v-if="whisper.default_model === row.size"
              type="success" size="small" effect="plain" style="margin-left: 6px;"
            >默认</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" min-width="100">
          <template #default="{ row }">
            <el-tag :type="whisperStatus(row).type" size="small" effect="plain">{{ whisperStatus(row).text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="占用" min-width="100">
          <template #default="{ row }">{{ humanBytes(row.bytes) }}</template>
        </el-table-column>
        <el-table-column prop="path" label="目录" min-width="260" show-overflow-tooltip />
        <el-table-column label="操作" min-width="130" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small" link type="primary"
              :disabled="whisper.default_model === row.size"
              @click="handleSaveSettings({ whisper_model: row.size })"
            >设为默认</el-button>
            <el-dropdown trigger="click" @command="(cmd) => handleWhisperCommand(cmd, row)">
              <el-button size="small" link class="row-more-btn">
                更多<el-icon class="row-more-arrow"><ArrowDown /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="remove" :disabled="!row.bytes">删除模型文件</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
      </el-table>

      <p class="adv-field-hint" style="margin-top: 8px;">
        模型需自行放置到上表目录（需含 <span class="mono-tag">model.bin</span> 与
        <span class="mono-tag">tokenizer.json</span>）；本工具不提供 Whisper 在线下载。
        「不完整」表示目录存在但缺少关键文件，该档位会被自动跳过并降级。
      </p>
    </el-card>

    <!-- 本地引擎列表（注册表驱动：后端加引擎，本页自动出现） -->
    <el-card
      v-for="eng in engines"
      :key="eng.key"
      class="app-card" shadow="never" style="margin-bottom: 16px;"
    >
      <div class="card-head-row">
        <span class="wb-card-title">{{ eng.name }}</span>
        <el-tag :type="eng.fee === '免费' ? 'success' : 'warning'" size="small" effect="plain">
          {{ eng.fee }}
        </el-tag>
        <span class="card-head-hint">{{ eng.languages }}</span>
        <div class="card-head-actions">
          <el-tag v-if="eng.ready" type="success" size="small" effect="plain">已就绪</el-tag>
          <el-tag v-else type="info" size="small" effect="plain">未就绪</el-tag>
          <el-tag v-if="eng.busy" type="warning" size="small" effect="plain">转录中</el-tag>
        </div>
      </div>

      <p class="adv-field-hint" style="margin-top: 6px;">{{ eng.note }}</p>

      <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 8px;">
        <span class="card-head-hint">占用磁盘：{{ humanBytes(eng.bytes) }}</span>
        <span class="card-head-hint" v-if="eng.size_hint">模型体积：{{ eng.size_hint }}</span>
        <span class="card-head-hint">
          sherpa-onnx 依赖：{{ sherpaAvailable ? '已安装' : '未安装（pip install sherpa-onnx）' }}
        </span>
        <el-button
          v-if="!eng.ready && !isDownloading(eng)"
          size="small" type="primary" :loading="downloadingEngine === eng.key"
          @click="handleStartDownload(eng)"
        >下载模型{{ eng.size_hint ? `（${eng.size_hint}）` : '' }}</el-button>
        <el-button v-if="isDownloading(eng)" size="small" @click="handleCancelDownload(eng)">取消下载</el-button>
        <el-button
          v-if="eng.ready && !isDownloading(eng)"
          size="small" type="danger" plain :loading="removingEngine === eng.key"
          @click="handleRemoveEngine(eng)"
        >删除模型</el-button>
      </div>

      <div v-if="isDownloading(eng) || engineDownload(eng).status === 'error'" style="margin-top: 12px;">
        <el-progress
          :percentage="engineDownload(eng).percent"
          :status="engineDownload(eng).status === 'error' ? 'exception' : undefined"
        />
        <p class="adv-field-hint" style="margin-top: 6px;">
          {{ engineDownload(eng).status === 'error'
            ? `下载失败：${engineDownload(eng).error}`
            : engineDownload(eng).message }}
        </p>
      </div>

      <el-table :data="eng.parts" v-loading="asrStore.loading" size="small" style="width: 100%; margin-top: 12px;">
        <el-table-column prop="label" label="模型文件" min-width="150" show-overflow-tooltip />
        <el-table-column label="状态" min-width="90">
          <template #default="{ row }">
            <el-tag :type="row.ready ? 'success' : 'info'" size="small" effect="plain">
              {{ row.ready ? '已就位' : '缺失' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="大小" min-width="90">
          <template #default="{ row }">{{ humanBytes(row.bytes) }}</template>
        </el-table-column>
        <el-table-column prop="path" label="路径" min-width="300" show-overflow-tooltip />
      </el-table>

      <p class="adv-field-hint" style="margin-top: 8px;">
        {{ eng.manual_hint }}；下载失败时可按上表路径手动放置模型后点「刷新」。
        删除模型前请确认没有正在进行的转录（转录中会拒绝删除）。
      </p>
    </el-card>
  </div>
</template>
