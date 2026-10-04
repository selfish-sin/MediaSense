<script setup>
/**
 * 桌面背景设置面板。
 * - 5 套内置 SVG 预设背景（零版权，随主色联动）
 * - 自定义上传图片 + 提取主色调色板（一键应用为主色）
 * - 遮罩浓度 / 模糊度调节
 * - 主色预设色板 + 自定义色
 */
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, X, Upload, Sparkles, RotateCcw, ImageIcon, Palette } from 'lucide-vue-next'
import { useThemeStore } from '../stores/themeStore'
import { BUILTIN_BACKGROUNDS } from '../utils/backgrounds'
import { extractPalette } from '../utils/color'
import { prepareBackgroundImage } from '../utils/image'

const theme = useThemeStore()

// 内置预设缩略图：用各预设的 render 在小尺寸下生成 dataURI
const builtins = BUILTIN_BACKGROUNDS.map((b) => ({
  id: b.id,
  name: b.name,
  thumb: b.render(b.accent),
}))

// 主色预设色板（青蓝系为主，兼顾几种好看的选择）
const accentSwatches = [
  '#38BDF8', // 天青（默认）
  '#22D3EE', // 青
  '#60A5FA', // 海蓝
  '#818CF8', // 冰蓝
  '#34D399', // 翡翠
  '#A78BFA', // 淡紫（保留一种紫，作为备选）
  '#F472B6', // 樱粉
  '#FBBF24', // 琥珀
]

// 上传相关
const uploading = ref(false)
const customPreview = ref(theme.background.type === 'custom' ? theme.background.url : '')
const palette = ref([]) // 从上传图提取的色板
const extracting = ref(false)

function selectBuiltin(id) {
  theme.setBackground({ type: 'builtin', id })
}

function selectNone() {
  theme.setBackground({ type: 'none' })
}

function onAccent(hex) {
  theme.setAccent(hex)
}

async function onFileChange(e) {
  const input = e.target
  const file = input.files?.[0]
  if (!file) return
  // ★ 清空 value：否则连续选同一个文件不会再触发 change（表现为"点了没反应"）
  input.value = ''
  if (!file.type.startsWith('image/')) {
    ElMessage.warning('请选择图片文件')
    return
  }
  if (file.size > 30 * 1024 * 1024) {
    ElMessage.warning('图片需小于 30MB')
    return
  }
  uploading.value = true
  try {
    // 先压到可持久化的体积：整张原图 base64 会撑爆 localStorage 配额（详见 utils/image.js）
    const { dataUrl, compressed } = await prepareBackgroundImage(file)
    customPreview.value = dataUrl
    theme.setBackground({ type: 'custom', url: dataUrl })
    uploading.value = false
    ElMessage.success(compressed ? '背景已应用（大图已压缩以减轻存储占用）' : '背景已应用')

    extracting.value = true
    const colors = await extractPalette(dataUrl, { count: 6, sampleSize: 64 })
    palette.value = colors
    extracting.value = false
    if (!colors.length) {
      ElMessage.info('未能提取配色（图片像素无法读取），可在下方手动选色')
    }
  } catch (err) {
    uploading.value = false
    extracting.value = false
    ElMessage.error(err?.message || '图片处理失败')
  }
}

function applyPaletteColor(hex) {
  theme.setAccent(hex)
  ElMessage.success(`主色已设为 ${hex}`)
}

// —— 皮肤 ——
/** 导入/导出结果提示（成功与失败都必须可见，禁止静默）。 */
const skinNotice = ref(null)

/** 皮肤卡片上的色块预览：palette 取代表色，tokens 模式取底色/边框/文字/主色。 */
function skinSwatches(s) {
  if (s.palette) {
    return [s.palette.base, s.palette.surface1, s.palette.text, s.palette.mauve || s.palette.blue]
  }
  const t = s.tokens || {}
  return [t['--app-bg'], t['--app-border'], t['--app-text'], t['--app-accent-1']].filter(Boolean)
}

function onSkinFile(e) {
  const input = e.target
  const file = input.files?.[0]
  if (!file) return
  // 清空 value：否则连续选同一个文件不会再触发 change
  input.value = ''
  const reader = new FileReader()
  reader.onload = () => {
    const res = theme.importSkin(String(reader.result || ''))
    if (res.ok) {
      theme.setSkin(res.skin.id)
      skinNotice.value = { kind: 'ok', text: `已导入并应用皮肤「${res.skin.name}」` }
    } else {
      skinNotice.value = { kind: 'err', text: `导入失败：${res.error}` }
    }
  }
  reader.onerror = () => {
    skinNotice.value = { kind: 'err', text: '文件读取失败，请重试' }
  }
  reader.readAsText(file)
}

function onExportSkin() {
  const json = theme.exportSkinJson(theme.skinId)
  const blob = new Blob([json], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${theme.skinId}.json`
  a.click()
  URL.revokeObjectURL(url)
  skinNotice.value = { kind: 'ok', text: `已导出 ${theme.skinId}.json` }
}

function onRemoveSkin() {
  const res = theme.removeUserSkin(theme.skinId)
  skinNotice.value = res.ok
    ? { kind: 'ok', text: '已删除该皮肤并回退默认皮肤' }
    : { kind: 'err', text: res.error }
}

function resetAll() {
  theme.resetBackground()
  theme.resetEffects()
  theme.setAccent('#38BDF8')
  customPreview.value = ''
  palette.value = []
}

// 调节滑块：效果参数独立于背景媒体（见 utils/effects.js）
function effectModel(key, fallback) {
  return computed({
    get: () => theme.effects[key] ?? fallback,
    set: (v) => theme.setEffect({ [key]: v }),
  })
}
const dim = effectModel('dim', 26)
const vignette = effectModel('vignette', 55)
const top = effectModel('top', 12)
const bottom = effectModel('bottom', 22)
const cardOpacity = effectModel('cardOpacity', 72)
const glassBlur = effectModel('glassBlur', 14)
const bgBlur = effectModel('bgBlur', 0)
</script>

<template>
  <div class="bg-panel">
    <!-- 皮肤：换整套配色（含皮肤自带主色），取代"只能换单色"的旧模型 -->
    <section class="bp-section">
      <div class="bp-section-head">
        <Palette :size="15" />
        <span>皮肤</span>
      </div>
      <div class="bp-skin-grid">
        <button
          v-for="s in theme.availableSkins"
          :key="s.id"
          class="bp-skin-card"
          :class="{ active: theme.skinId === s.id }"
          :title="s.name"
          @click="theme.setSkin(s.id)"
        >
          <span class="bp-skin-swatches">
            <i v-for="(c, i) in skinSwatches(s)" :key="i" :style="{ background: c }" />
          </span>
          <span class="bp-skin-name">{{ s.name }}</span>
          <span v-if="theme.skinId === s.id" class="bp-skin-check"><Check :size="13" /></span>
        </button>
      </div>
      <div class="bp-skin-actions">
        <label class="bp-skin-btn">
          <input type="file" accept="application/json,.json" class="bp-file-input" @change="onSkinFile" />
          <Upload :size="13" /> 导入皮肤
        </label>
        <button class="bp-skin-btn" @click="onExportSkin">
          <Sparkles :size="13" /> 导出当前
        </button>
        <button
          v-if="theme.userSkins.some((s) => s.id === theme.skinId)"
          class="bp-skin-btn bp-skin-danger"
          @click="onRemoveSkin"
        >
          <X :size="13" /> 删除该皮肤
        </button>
      </div>
      <div v-if="skinNotice" class="bp-skin-notice" :class="skinNotice.kind">{{ skinNotice.text }}</div>
    </section>

    <!-- 内置预设 -->
    <section class="bp-section">
      <div class="bp-section-head">
        <Palette :size="15" />
        <span>内置氛围背景</span>
      </div>
      <div class="bp-builtin-grid">
        <button
          v-for="b in builtins"
          :key="b.id"
          class="bp-thumb"
          :class="{ active: theme.background.type === 'builtin' && theme.background.id === b.id }"
          :style="{ backgroundImage: b.thumb }"
          :title="b.name"
          @click="selectBuiltin(b.id)"
        >
          <span class="bp-thumb-name">{{ b.name }}</span>
          <span v-if="theme.background.type === 'builtin' && theme.background.id === b.id" class="bp-thumb-check">
            <Check :size="14" />
          </span>
        </button>
        <button
          class="bp-thumb bp-thumb-none"
          :class="{ active: theme.background.type === 'none' }"
          @click="selectNone"
        >
          <X :size="18" />
          <span class="bp-thumb-name">无背景</span>
        </button>
      </div>
    </section>

    <!-- 自定义上传 -->
    <section class="bp-section">
      <div class="bp-section-head">
        <ImageIcon :size="15" />
        <span>自定义背景图</span>
      </div>
      <label class="bp-upload">
        <input type="file" accept="image/*" class="bp-file-input" @change="onFileChange" />
        <div class="bp-upload-inner">
          <Upload :size="22" />
          <span>{{ uploading ? '读取中…' : '点击上传图片' }}</span>
          <span class="bp-upload-hint">支持 JPG / PNG / WebP，&lt;30MB（大图会自动压缩后保存）</span>
        </div>
      </label>
      <!-- 提取的色板 -->
      <div v-if="palette.length" class="bp-palette">
        <div class="bp-palette-head">
          <Sparkles :size="14" />
          <span>从图片提取的配色 — 点击设为主色</span>
        </div>
        <div class="bp-palette-swatches">
          <button
            v-for="c in palette"
            :key="c"
            class="bp-swatch"
            :style="{ background: c }"
            :title="`设为主色 ${c}`"
            @click="applyPaletteColor(c)"
          >
            <Check v-if="theme.accent.toLowerCase() === c.toLowerCase()" :size="14" class="bp-swatch-active" />
          </button>
        </div>
      </div>
      <div v-else-if="extracting" class="bp-palette-hint">正在提取配色…</div>
      <!-- 持久化失败必须可见（localStorage 配额溢出等），否则"设置没保存"无从察觉 -->
      <div v-if="theme.storageError" class="bp-storage-error">{{ theme.storageError }}</div>
    </section>

    <!-- 氛围遮罩：四层 scrim 各自可调，配合出"背景作为氛围层"的观感 -->
    <section class="bp-section">
      <div class="bp-section-head">
        <span>氛围遮罩</span>
      </div>
      <div class="bp-slider-row">
        <label>整体浓度</label>
        <el-slider v-model="dim" :min="0" :max="100" :step="1" style="flex:1" />
        <span class="bp-slider-val">{{ dim }}%</span>
      </div>
      <div class="bp-slider-row">
        <label>径向暗角</label>
        <el-slider v-model="vignette" :min="0" :max="100" :step="1" style="flex:1" />
        <span class="bp-slider-val">{{ vignette }}%</span>
      </div>
      <div class="bp-slider-row">
        <label>顶部渐晕</label>
        <el-slider v-model="top" :min="0" :max="100" :step="1" style="flex:1" />
        <span class="bp-slider-val">{{ top }}%</span>
      </div>
      <div class="bp-slider-row">
        <label>底部渐晕</label>
        <el-slider v-model="bottom" :min="0" :max="100" :step="1" style="flex:1" />
        <span class="bp-slider-val">{{ bottom }}%</span>
      </div>
      <div class="bp-slider-row">
        <label>背景模糊</label>
        <el-slider v-model="bgBlur" :min="0" :max="20" :step="1" style="flex:1" />
        <span class="bp-slider-val">{{ bgBlur }}px</span>
      </div>
    </section>

    <!-- 卡片材质：透明度基准驱动 surface-1/2/3 三档 -->
    <section class="bp-section">
      <div class="bp-section-head">
        <span>卡片材质</span>
      </div>
      <div class="bp-slider-row">
        <label>卡片透明度</label>
        <el-slider v-model="cardOpacity" :min="20" :max="100" :step="1" style="flex:1" />
        <span class="bp-slider-val">{{ cardOpacity }}%</span>
      </div>
      <div class="bp-slider-row">
        <label>玻璃模糊</label>
        <el-slider v-model="glassBlur" :min="0" :max="24" :step="1" style="flex:1" />
        <span class="bp-slider-val">{{ glassBlur }}px</span>
      </div>
    </section>

    <!-- 主色调 -->
    <section class="bp-section">
      <div class="bp-section-head">
        <Palette :size="15" />
        <span>界面主色调</span>
      </div>
      <div class="bp-accent-row">
        <button
          v-for="c in accentSwatches"
          :key="c"
          class="bp-swatch bp-swatch-lg"
          :style="{ background: c }"
          :title="c"
          @click="onAccent(c)"
        >
          <Check v-if="theme.accent.toLowerCase() === c.toLowerCase()" :size="16" class="bp-swatch-active" />
        </button>
        <label class="bp-swatch bp-swatch-custom" title="自定义颜色">
          <input type="color" :value="theme.accent" class="bp-color-input" @input="onAccent($event.target.value)" />
          <span class="bp-swatch-custom-label">{{ theme.accent }}</span>
        </label>
      </div>
    </section>

    <button class="bp-reset" @click="resetAll">
      <RotateCcw :size="14" /> 恢复默认
    </button>
  </div>
</template>

<style scoped>
.bp-panel { display: flex; flex-direction: column; gap: 18px; }

/* 皮肤选择卡片 */
.bp-skin-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}
.bp-skin-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
  border-radius: 10px;
  border: 2px solid var(--app-border);
  background: var(--app-input-bg);
  color: var(--app-text);
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s, transform 0.15s;
}
.bp-skin-card:hover { transform: translateY(-1px); border-color: var(--app-accent-1); }
.bp-skin-card.active {
  border-color: var(--app-accent-1);
  box-shadow: 0 0 0 2px rgba(var(--app-accent-1-rgb), 0.3);
}
.bp-skin-swatches { display: flex; gap: 3px; }
.bp-skin-swatches i { flex: 1; height: 14px; border-radius: 3px; display: block; }
.bp-skin-name { font-size: 11px; line-height: 1.25; }
.bp-skin-check {
  position: absolute;
  top: 6px; right: 6px;
  color: var(--app-accent-1);
}
.bp-skin-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.bp-skin-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  border-radius: 8px;
  border: 1px solid var(--app-border);
  background: transparent;
  color: var(--app-text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
}
.bp-skin-btn:hover { color: var(--app-text); border-color: var(--app-accent-1); }
.bp-skin-danger:hover { color: #f87171; border-color: #f87171; }
.bp-skin-notice { font-size: 12px; padding: 6px 10px; border-radius: 8px; line-height: 1.4; }
.bp-skin-notice.ok {
  color: #4ade80;
  background: rgba(74, 222, 128, 0.12);
  border: 1px solid rgba(74, 222, 128, 0.35);
}
.bp-skin-notice.err {
  color: #fca5a5;
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.35);
}
.bp-section { display: flex; flex-direction: column; gap: 10px; }
.bp-section-head {
  display: flex; align-items: center; gap: 7px;
  font-size: 13px; font-weight: 600;
  color: var(--app-text-heading);
}

/* 内置预设网格 */
.bp-builtin-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}
.bp-thumb {
  position: relative;
  height: 64px;
  border-radius: 8px;
  border: 2px solid var(--app-border);
  background-size: cover;
  background-position: center;
  cursor: pointer;
  overflow: hidden;
  display: flex;
  align-items: flex-end;
  padding: 0;
  transition: border-color 0.15s, transform 0.15s;
}
.bp-thumb:hover { transform: translateY(-1px); border-color: var(--app-accent-1); }
.bp-thumb.active { border-color: var(--app-accent-1); box-shadow: 0 0 0 2px rgba(var(--app-accent-1-rgb), 0.3); }
.bp-thumb-name {
  width: 100%;
  padding: 2px 0;
  font-size: 11px;
  color: #fff;
  text-align: center;
  background: linear-gradient(0deg, rgba(0,0,0,0.7), transparent);
}
.bp-thumb-check {
  position: absolute;
  top: 4px; right: 4px;
  width: 20px; height: 20px;
  border-radius: 50%;
  background: var(--app-accent-1);
  color: #fff;
  display: flex; align-items: center; justify-content: center;
}
.bp-thumb-none {
  background: var(--app-input-bg);
  color: var(--app-text-secondary);
  align-items: center;
  justify-content: center;
}

/* 上传 */
.bp-upload {
  display: block;
  cursor: pointer;
}
.bp-file-input { display: none; }
.bp-upload-inner {
  border: 2px dashed var(--app-input-border);
  border-radius: 10px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  color: var(--app-text-secondary);
  transition: border-color 0.15s, color 0.15s;
}
.bp-upload-inner:hover {
  border-color: var(--app-accent-1);
  color: var(--app-accent-1);
}
.bp-upload-hint { font-size: 11px; opacity: 0.7; }

/* 色板 */
.bp-palette { display: flex; flex-direction: column; gap: 8px; }
.bp-palette-head {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; color: var(--app-text-secondary);
}
.bp-palette-swatches, .bp-accent-row {
  display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
}
.bp-swatch {
  width: 30px; height: 30px;
  border-radius: 8px;
  border: 2px solid var(--app-border);
  cursor: pointer;
  position: relative;
  display: flex; align-items: center; justify-content: center;
  padding: 0;
  transition: transform 0.15s;
}
.bp-swatch:hover { transform: scale(1.1); }
.bp-swatch-lg { width: 34px; height: 34px; }
.bp-swatch-active { color: #fff; filter: drop-shadow(0 0 2px rgba(0,0,0,0.5)); }
.bp-swatch-custom {
  background: linear-gradient(135deg, #f43f5e, #fbbf24, #34d399, #38bdf8, #a78bfa);
  overflow: hidden;
  position: relative;
}
.bp-color-input {
  position: absolute; inset: 0;
  width: 100%; height: 100%;
  opacity: 0; cursor: pointer;
}
.bp-swatch-custom-label {
  position: absolute; bottom: -18px; left: 50%;
  transform: translateX(-50%);
  font-size: 9px; color: var(--app-text-secondary);
  white-space: nowrap;
}
.bp-palette-hint { font-size: 12px; color: var(--app-text-secondary); }
.bp-storage-error {
  margin-top: 6px;
  padding: 6px 10px;
  border-radius: 8px;
  font-size: 12px;
  color: #fca5a5;
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.35);
}

/* 滑块行 */
.bp-slider-row {
  display: flex; align-items: center; gap: 10px;
}
.bp-slider-row label { font-size: 12px; color: var(--app-text-secondary); width: 64px; flex-shrink: 0; }
.bp-slider-val { font-size: 12px; color: var(--app-text); width: 40px; text-align: right; font-variant-numeric: tabular-nums; }

/* 重置 */
.bp-reset {
  align-self: flex-start;
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 12px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: transparent;
  color: var(--app-text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.bp-reset:hover { color: var(--app-text); border-color: var(--app-accent-1); }
</style>
