<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadCloud, Download, CircleCheck, CircleX, RefreshCcw, Info, FileText, Video, Music, Image, HelpCircle, ArrowLeftRight, X, Crosshair, Send, CheckCircle2, XCircle, TriangleAlert } from 'lucide-vue-next'

// ============ 引擎状态 ============
const engines = ref({ pandoc: false, ffmpeg: false, libreoffice: false, pillow: false })
const engineChecked = ref(false)

const engineLabels = {
  pandoc:    { name: 'Pandoc',     desc: '文档/标记语言转换' },
  ffmpeg:    { name: 'FFmpeg',     desc: '视频/音频转换' },
  libreoffice: { name: 'LibreOffice', desc: 'Office/PDF高保真转换' },
  pillow:    { name: 'Pillow',     desc: '图片格式转换' },
}

async function loadEngines() {
  try {
    const resp = await fetch('/api/converter/engines')
    const data = await resp.json()
    engines.value = data.engines
    engineChecked.value = true
  } catch {
    ElMessage.error('获取引擎状态失败')
  }
}

const anyEngineMissing = computed(() => {
  return Object.values(engines.value).some(v => !v)
})

// ============ 文件上传 ============
const uploadedFile = ref(null)      // { name, size, type, raw }
const isDragOver = ref(false)

function handleFileChange(file) {
  if (file.raw) {
    const type = detectFileType(file.name)
    if (type.group === 'unknown') {
      ElMessage.warning(`不支持的文件格式：${file.name}`)
      return
    }
    uploadedFile.value = {
      name: file.name,
      size: file.size,
      type,
      raw: file.raw,
    }
    // 清空之前的选择
    selectedTarget.value = ''
    result.value = null
    convertError.value = ''
  }
}

function detectFileType(filename) {
  const ext = '.' + filename.split('.').pop().toLowerCase()
  const docExts = ['.md','.docx','.doc','.xlsx','.xls','.pptx','.ppt','.pdf','.epub','.html','.htm','.txt','.csv','.tex']
  const vidExts = ['.mp4','.mkv','.webm','.avi','.mov','.flv','.gif','.ts','.m4v','.3gp']
  const audExts = ['.mp3','.wav','.aac','.ogg','.flac','.m4a','.opus','.wma']
  const imgExts = ['.png','.jpg','.jpeg','.webp','.bmp','.tiff']
  if (docExts.includes(ext)) return { label: '文档', group: 'document', icon: 'document' }
  if (vidExts.includes(ext)) return { label: '视频', group: 'video', icon: 'video' }
  if (audExts.includes(ext)) return { label: '音频', group: 'audio', icon: 'audio' }
  if (imgExts.includes(ext)) return { label: '图片', group: 'image', icon: 'image' }
  return { label: '未知', group: 'unknown', icon: 'unknown' }
}

function clearFile() {
  uploadedFile.value = null
  selectedTarget.value = ''
  result.value = null
  convertError.value = ''
}

// ============ 格式选择 ============
const selectedTarget = ref('')
const quality = ref('medium')

const outputGroups = computed(() => {
  if (!uploadedFile.value) return []
  const g = uploadedFile.value.type.group
  const list = []

  if (g === 'document') {
    const docs = [
      { fmt: 'md',     name: 'Markdown',      avail: engines.value.pandoc, desc: '轻量标记语言' },
      { fmt: 'docx',   name: 'Word (.docx)',   avail: engines.value.pandoc || engines.value.libreoffice, desc: 'Microsoft Word' },
      { fmt: 'html',   name: 'HTML',           avail: engines.value.pandoc, desc: '网页格式' },
      { fmt: 'epub',   name: 'EPUB (电子书)',   avail: engines.value.pandoc, desc: '电子书阅读器' },
      { fmt: 'txt',    name: '纯文本 (.txt)',   avail: engines.value.pandoc, desc: '无格式文本' },
      { fmt: 'pptx',   name: 'PPT (.pptx)',    avail: engines.value.pandoc, desc: 'PowerPoint 演示' },
      { fmt: 'csv',    name: 'CSV',            avail: engines.value.pandoc, desc: '逗号分隔表格' },
      { fmt: 'pdf',    name: 'PDF',            avail: engines.value.pandoc || engines.value.libreoffice, desc: '便携文档' },
      { fmt: 'xlsx',   name: 'Excel (.xlsx)',  avail: engines.value.libreoffice, desc: 'Microsoft Excel' },
    ].filter(f => f.avail)
    if (docs.length) list.push({ label: '文档格式', icon: 'document', formats: docs })
  }

  if (g === 'video' || g === 'audio') {
    if (g === 'video') {
      const vids = [
        { fmt: 'mp4',  name: 'MP4 (H.264)',   avail: engines.value.ffmpeg, desc: '通用，兼容性最好' },
        { fmt: 'mkv',  name: 'MKV',            avail: engines.value.ffmpeg, desc: '高质量存档' },
        { fmt: 'webm', name: 'WebM',           avail: engines.value.ffmpeg, desc: '网页嵌入' },
        { fmt: 'avi',  name: 'AVI',            avail: engines.value.ffmpeg, desc: '老旧设备兼容' },
        { fmt: 'mov',  name: 'MOV',            avail: engines.value.ffmpeg, desc: 'Apple 生态' },
        { fmt: 'gif',  name: 'GIF 动图',       avail: engines.value.ffmpeg, desc: '表情包/短动图' },
      ].filter(f => f.avail)
      if (vids.length) {
        list.push({ label: '视频格式', icon: 'video', formats: vids, hint: '同格式转换=压缩视频' })
      }
    }
    const auds = [
      { fmt: 'mp3',  name: 'MP3 (320kbps)',  avail: engines.value.ffmpeg, desc: '通用，兼容性最好' },
      { fmt: 'wav',  name: 'WAV (无损)',      avail: engines.value.ffmpeg, desc: '原始 PCM 音频' },
      { fmt: 'aac',  name: 'AAC',            avail: engines.value.ffmpeg, desc: '移动端推荐' },
      { fmt: 'ogg',  name: 'OGG Vorbis',     avail: engines.value.ffmpeg, desc: '开源替代' },
      { fmt: 'flac', name: 'FLAC (无损压缩)', avail: engines.value.ffmpeg, desc: '高保真存档' },
      { fmt: 'm4a',  name: 'M4A',            avail: engines.value.ffmpeg, desc: 'Apple 生态' },
      { fmt: 'opus', name: 'Opus',           avail: engines.value.ffmpeg, desc: '低码率高音质' },
    ].filter(f => f.avail)
    if (auds.length) {
      const hint = g === 'video' ? '视频→音频=提取音频轨' : '同格式转换=改码率'
      list.push({ label: '音频格式', icon: 'audio', formats: auds, hint })
    }
  }

  if (g === 'image') {
    const imgs = [
      { fmt: 'png',  name: 'PNG (无损)',   avail: engines.value.pillow, desc: '截图、UI 素材' },
      { fmt: 'jpg',  name: 'JPG',          avail: engines.value.pillow, desc: '照片、网络分享' },
      { fmt: 'webp', name: 'WebP',         avail: engines.value.pillow, desc: '网页优化，体积小' },
      { fmt: 'bmp',  name: 'BMP',          avail: engines.value.pillow, desc: 'Windows 兼容' },
    ].filter(f => f.avail)
    if (imgs.length) list.push({ label: '图片格式', icon: 'image', formats: imgs, hint: '同格式转换=压缩图片' })
  }

  return list
})

// ============ 同格式转换提示 ============
const isSameFormat = computed(() => {
  if (!uploadedFile.value || !selectedTarget.value) return false
  const ext = uploadedFile.value.name.split('.').pop().toLowerCase()
  return ext === selectedTarget.value.toLowerCase()
})

const sameFormatHint = computed(() => {
  if (!isSameFormat.value) return ''
  const group = uploadedFile.value.type.group
  if (group === 'document') return null  // 文档同格式不允许
  if (group === 'video') return '同格式转换将重新编码压缩视频'
  if (group === 'audio') return '同格式转换将重新编码调整码率'
  if (group === 'image') return '同格式转换将重新压缩图片'
  return ''
})

const canConvert = computed(() => {
  if (!uploadedFile.value || !selectedTarget.value) return false
  if (uploadedFile.value.type.group === 'unknown') return false
  // 文档同格式不允许，其他允许
  if (isSameFormat.value && uploadedFile.value.type.group === 'document') return false
  return true
})

// ============ 选中的目标格式详情 ============
const selectedFormatInfo = computed(() => {
  if (!selectedTarget.value) return null
  for (const g of outputGroups.value) {
    for (const f of g.formats) {
      if (f.fmt === selectedTarget.value) return f
    }
  }
  return null
})

// ============ 转换 ============
const result = ref(null)      // { output_name, output_size, engine, elapsed, download_id }
const converting = ref(false)
const convertError = ref('')

async function startConvert() {
  if (!canConvert.value) return
  converting.value = true
  convertError.value = ''
  result.value = null

  try {
    const file = uploadedFile.value?.raw
    if (!file) {
      ElMessage.warning('请先选择文件')
      converting.value = false
      return
    }

    const formData = new FormData()
    formData.append('file', file)
    formData.append('target', selectedTarget.value)
    formData.append('quality', quality.value)

    const resp = await fetch('/api/converter/convert', {
      method: 'POST',
      body: formData,
    })
    const data = await resp.json()
    if (!resp.ok) {
      throw new Error(data.detail || `转换失败 (${resp.status})`)
    }
    result.value = data
    ElMessage.success(`转换完成！耗时 ${data.elapsed} 秒`)
  } catch (err) {
    convertError.value = err.message
    ElMessage.error(err.message)
  } finally {
    converting.value = false
  }
}

function formatSize(bytes) {
  if (!bytes) return '?'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
  return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB'
}

function downloadResult() {
  if (!result.value?.download_id) return
  window.open(`/api/converter/download/${result.value.download_id}`, '_blank')
}

// ============ 初始化 ============
onMounted(() => {
  loadEngines()
})
</script>

<template>
  <div style="max-width: 1000px; margin: 0 auto; padding: 24px;">
    <h2 style="margin-top: 0; color: var(--app-text-heading); display: flex; align-items: center; gap: 8px;"><el-icon><ArrowLeftRight /></el-icon> 万能格式转换器</h2>
    <p style="color: var(--app-text-secondary); margin-bottom: 20px;">本地引擎驱动：Pandoc · LibreOffice · FFmpeg · Pillow</p>

    <!-- 引擎状态 -->
    <div style="display: flex; gap: 8px; margin-bottom: 24px; flex-wrap: wrap; align-items: center;">
      <div
        v-for="(info, key) in engineLabels"
        :key="key"
        :style="{
          display: 'inline-flex', alignItems: 'center', gap: '4px',
          padding: '4px 10px', borderRadius: '6px', fontSize: '12px',
          background: engines[key] ? 'var(--app-engine-ok-bg)' : 'var(--app-engine-err-bg)',
          color: engines[key] ? 'var(--app-engine-ok-text)' : 'var(--app-engine-err-text)',
          border: '1px solid ' + (engines[key] ? 'var(--app-engine-ok-border)' : 'var(--app-engine-err-border)'),
        }"
      >
        <el-icon :size="14"><CircleCheck v-if="engines[key]" /><CircleX v-else /></el-icon>
        {{ info.name }}
        <span style="opacity: 0.5; font-size: 11px;">{{ info.desc }}</span>
      </div>
      <el-button text size="small" @click="loadEngines" style="margin-left: auto;">
        <el-icon><RefreshCcw /></el-icon> 刷新
      </el-button>
    </div>

    <!-- 缺少引擎提示 -->
    <el-alert
      v-if="anyEngineMissing && engineChecked"
      title="部分引擎未安装，对应格式转换不可用。请安装后刷新。"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 16px;"
    />

    <el-card class="app-card" shadow="never" style="margin-bottom: 20px;">
      <!-- 文件上传区 -->
      <div v-if="!uploadedFile" style="text-align: center; padding: 36px 0;">
        <el-upload
          class="converter-upload"
          drag
          :auto-upload="false"
          :show-file-list="false"
          :on-change="handleFileChange"
          accept="*"
        >
          <el-icon :size="48" color="var(--app-accent-1)"><UploadCloud /></el-icon>
          <div style="margin-top: 12px; color: var(--app-text); font-size: 15px;">
            拖拽文件到此处 或 <em style="color: var(--app-accent-1); font-style: normal; font-weight: 600;">点击上传</em>
          </div>
          <div style="margin-top: 6px; color: var(--app-text-secondary); font-size: 12px; line-height: 1.8;">
            文档 (.md .docx .pdf .epub …)｜视频 (.mp4 .mkv .webm …)<br>
            音频 (.mp3 .wav .flac …)｜图片 (.png .jpg .webp …)
          </div>
        </el-upload>
      </div>

      <!-- 已选文件信息 -->
      <div v-else>
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px;">
          <div style="display: flex; align-items: center; gap: 12px;">
            <el-icon :size="32" style="line-height: 1;" color="var(--app-accent-1)">
              <FileText v-if="uploadedFile.type.icon === 'document'" />
              <Video v-else-if="uploadedFile.type.icon === 'video'" />
              <Music v-else-if="uploadedFile.type.icon === 'audio'" />
              <Image v-else-if="uploadedFile.type.icon === 'image'" />
              <HelpCircle v-else />
            </el-icon>
            <div>
              <div style="font-weight: 600; color: var(--app-text-heading); font-size: 15px;">{{ uploadedFile.name }}</div>
              <div style="font-size: 12px; color: var(--app-text-secondary);">
                {{ formatSize(uploadedFile.size) }} · {{ uploadedFile.type.label }}
              </div>
            </div>
          </div>
          <el-button text type="danger" size="small" :icon="X" @click="clearFile">移除</el-button>
        </div>

        <!-- 目标格式选择 -->
        <div v-for="group in outputGroups" :key="group.label" style="margin-bottom: 16px;">
          <div style="display: flex; align-items: baseline; gap: 8px; margin-bottom: 8px;">
            <span style="font-size: 13px; color: var(--app-text-secondary); display: inline-flex; align-items: center; gap: 4px;">
              <el-icon :size="14"><FileText v-if="group.icon === 'document'" /><Video v-else-if="group.icon === 'video'" /><Music v-else-if="group.icon === 'audio'" /><Image v-else-if="group.icon === 'image'" /></el-icon>
              {{ group.label }}
            </span>
            <span v-if="group.hint" style="font-size: 11px; color: var(--app-text-placeholder);">— {{ group.hint }}</span>
          </div>
          <div style="display: flex; flex-wrap: wrap; gap: 6px;">
            <el-tooltip
              v-for="fmt in group.formats"
              :key="fmt.fmt"
              :content="fmt.desc || ''"
              placement="top"
              :show-after="400"
            >
              <el-button
                :type="selectedTarget === fmt.fmt ? 'primary' : 'default'"
                size="small"
                @click="selectedTarget = fmt.fmt"
                :disabled="!fmt.avail"
                style="font-size: 12px;"
              >
                {{ fmt.name }}
                <span style="opacity: 0.45; margin-left: 2px; font-size: 11px;">.{{ fmt.fmt }}</span>
              </el-button>
            </el-tooltip>
          </div>
        </div>

        <!-- 选中的格式说明 -->
        <div v-if="selectedFormatInfo" style="margin-bottom: 16px; padding: 8px 12px; background: var(--app-hover-bg); border-radius: 6px; display: flex; align-items: center; gap: 8px; font-size: 12px;">
          <el-icon color="var(--app-accent-1)" :size="14"><Info /></el-icon>
          <span style="color: var(--app-text-secondary);">目标格式：<b style="color: var(--app-text);">{{ selectedFormatInfo.name }}</b> — {{ selectedFormatInfo.desc }}</span>
        </div>

        <!-- 同格式提示 -->
        <div v-if="sameFormatHint" style="margin-bottom: 12px; padding: 6px 12px; background: var(--app-override-bar-bg); border-radius: 6px; color: #facc15; font-size: 12px; display: flex; align-items: center; gap: 6px;">
          <el-icon :size="14"><TriangleAlert /></el-icon>{{ sameFormatHint }}
        </div>

        <!-- 质量选择 -->
        <div v-if="selectedTarget" style="margin-top: 16px;">
          <span style="font-size: 13px; color: var(--app-text-secondary); margin-right: 12px;">质量：</span>
          <el-radio-group v-model="quality" size="small">
            <el-radio-button value="low">低</el-radio-button>
            <el-radio-button value="medium">中（推荐）</el-radio-button>
            <el-radio-button value="high">高</el-radio-button>
          </el-radio-group>
          <span style="margin-left: 12px; font-size: 12px; color: var(--app-text-secondary);">
            {{ quality === 'low' ? '体积小速度快' : quality === 'high' ? '画质/音质最优' : '平衡体积与质量' }}
          </span>
        </div>

        <!-- 转换按钮 -->
        <div style="margin-top: 20px; display: flex; gap: 12px; align-items: center;">
          <el-button
            type="primary"
            size="large"
            :disabled="!canConvert"
            :loading="converting"
            @click="startConvert"
          >
            <el-icon v-if="!converting"><Send /></el-icon> {{ converting ? '转换中，请稍候...' : '开始转换' }}
          </el-button>
          <span v-if="!selectedTarget" style="color: var(--app-text-secondary); font-size: 13px; display: inline-flex; align-items: center; gap: 4px;">
            <el-icon :size="14"><Crosshair /></el-icon> 请先选择目标格式
          </span>
        </div>
      </div>
    </el-card>

    <!-- 转换结果 -->
    <el-card v-if="result && result.success" class="app-card" shadow="never" style="border: 1px solid var(--app-engine-ok-border); margin-top: 16px;">
      <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px;">
        <div style="display: flex; align-items: center; gap: 12px;">
          <el-icon :size="24" color="var(--app-engine-ok-text)"><CheckCircle2 /></el-icon>
          <div>
            <div style="font-weight: 700; color: var(--app-engine-ok-text); font-size: 15px;">
              {{ result.input_name }} → {{ result.output_name }}
            </div>
            <div style="font-size: 12px; color: var(--app-text-secondary); margin-top: 2px;">
              引擎: <b style="color: var(--app-text);">{{ result.engine }}</b> ·
              输出大小: <b style="color: var(--app-text);">{{ formatSize(result.output_size) }}</b> ·
              耗时: <b style="color: var(--app-text);">{{ result.elapsed }}s</b>
            </div>
          </div>
        </div>
        <el-button type="success" size="large" @click="downloadResult">
          <el-icon><Download /></el-icon> 下载结果
        </el-button>
      </div>
    </el-card>

    <!-- 转换错误 -->
    <el-card v-if="convertError" class="app-card" shadow="never" style="border: 1px solid var(--app-engine-err-border); margin-top: 16px;">
      <div style="display: flex; align-items: flex-start; gap: 8px; color: var(--app-engine-err-text);">
        <el-icon :size="16" style="margin-top: 1px;"><XCircle /></el-icon>
        <div>
          <div style="font-weight: 600; margin-bottom: 4px;">转换失败</div>
          <div style="font-size: 13px; opacity: 0.85; white-space: pre-wrap; word-break: break-all;">{{ convertError }}</div>
        </div>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.converter-upload :deep(.el-upload) { width: 100%; }
.converter-upload :deep(.el-upload-dragger) {
  width: 100%;
  background: var(--app-input-bg);
  border: 2px dashed var(--app-input-border);
  border-radius: 12px;
  padding: 44px 20px;
  transition: all 0.2s;
}
.converter-upload :deep(.el-upload-dragger:hover) {
  border-color: var(--app-accent-1);
  background: var(--app-hover-bg);
}
</style>
