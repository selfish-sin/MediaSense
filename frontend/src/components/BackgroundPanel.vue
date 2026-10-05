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
import { BUILTIN_BACKGROUNDS, ANIMATED_BACKGROUNDS, renderAnimated } from '../utils/backgrounds'
import { analyzeImage, recommendEffects } from '../utils/imageFit'
import { LIBRARY_LIMITS } from '../utils/bgStore'

const theme = useThemeStore()

// 内置预设缩略图：用各预设的 render 在小尺寸下生成 dataURI
const builtins = BUILTIN_BACKGROUNDS.map((b) => ({
  id: b.id,
  name: b.name,
  thumb: b.render(b.accent),
}))

// 动态背景缩略图（底图复用程序化预设，动效由 CSS 驱动、失焦自动暂停）
const animated = ANIMATED_BACKGROUNDS.map((a) => ({
  id: a.id,
  name: a.name,
  thumb: renderAnimated(a.id, '#38BDF8'),
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
const customPreview = ref(theme.background.thumbUrl || (theme.background.type === 'custom' ? theme.background.url : ''))
const palette = ref([]) // 从上传图提取的色板
const analyzing = ref(false)
/** 依据图片亮度给出的推荐参数（null = 尚未分析 / 分析失败） */
const recommendation = ref(null)

/** 图库容量上限（单一事实源在 utils/bgStore.js）。 */
const maxCount = LIBRARY_LIMITS.maxCount

/** 图库用量展示（MB，保留一位小数）。 */
const usageMb = computed(() => (theme.usage.bytes / 1024 / 1024).toFixed(1))

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
  recommendation.value = null
  try {
    // 压缩 + 入库（IndexedDB 存 Blob；不可用时 store 自动降级到 localStorage 旧路径）
    const result = await theme.uploadBackground(file)
    if (!result.ok) {
      ElMessage.error(result.error || '背景图保存失败')
      return
    }
    customPreview.value = result.thumbUrl || ''
    ElMessage.success(result.compressed ? '背景已应用（大图已压缩以减轻占用）' : '背景已应用')
    // 依据缩略图分析亮度 → 给一次推荐（可一键采用，也可继续手调）
    await analyzeCurrent(result.thumbUrl)
  } catch (err) {
    ElMessage.error(err?.message || '图片处理失败')
  } finally {
    uploading.value = false
  }
}

/** 分析图片：同一次采样同时得到提色板与推荐参数。 */
async function analyzeCurrent(src) {
  if (!src) return
  analyzing.value = true
  try {
    const result = await analyzeImage(src, { sampleSize: 64, paletteCount: 6 })
    if (!result) {
      recommendation.value = null
      ElMessage.info('未能分析这张图（像素无法读取），可在下方手动调参')
      return
    }
    palette.value = result.palette
    recommendation.value = recommendEffects(result)
  } catch (err) {
    recommendation.value = null
    ElMessage.warning(`图片分析失败：${err?.message || '未知错误'}`)
  } finally {
    analyzing.value = false
  }
}

/** 采用推荐参数（只覆盖效果层，不动背景媒体）。 */
function applyRecommendation() {
  if (!recommendation.value) return
  theme.setEffect(recommendation.value)
  ElMessage.success('已采用推荐参数')
}

/** 动态背景：换预设即切换背景类型（动效由 CSS 驱动）。 */
function selectAnimated(id) {
  theme.setBackground({ type: 'animated', preset: id })
}

/** 从图库删除一张图（正在用时 store 会回退无背景）。 */
async function onRemoveImage(item) {
  const res = await theme.removeBackground(item.id)
  if (!res.ok) ElMessage.error(res.error)
  else ElMessage.success('已从图库删除')
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
          v-for="a in animated"
          :key="a.id"
          class="bp-thumb bp-thumb-anim"
          :class="{ active: theme.background.type === 'animated' && theme.background.preset === a.id }"
          :style="{ backgroundImage: a.thumb }"
          :title="`${a.name}（动态，失焦自动暂停）`"
          @click="selectAnimated(a.id)"
        >
          <span class="bp-thumb-name">{{ a.name }}</span>
          <span
            v-if="theme.background.type === 'animated' && theme.background.preset === a.id"
            class="bp-thumb-check"
          >
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

    <!-- 背景图库：上传 + 多图切换 + 删除（存 IndexedDB，不受 localStorage 配额限制） -->
    <section class="bp-section">
      <div class="bp-section-head">
        <ImageIcon :size="15" />
        <span>背景图库</span>
        <span v-if="theme.libraryReady" class="bp-usage">{{ theme.usage.count }}/{{ maxCount }} · {{ usageMb }}MB</span>
      </div>
      <label class="bp-upload">
        <input type="file" accept="image/*" class="bp-file-input" @change="onFileChange" />
        <div class="bp-upload-inner">
          <Upload :size="22" />
          <span>{{ uploading ? '读取中…' : '点击上传图片' }}</span>
          <span class="bp-upload-hint">支持 JPG / PNG / WebP，&lt;30MB（大图会自动压缩后入库）</span>
        </div>
      </label>

      <!-- 图库网格 -->
      <div v-if="theme.library.length" class="bp-lib-grid">
        <div
          v-for="item in theme.library"
          :key="item.id"
          class="bp-lib-item"
          :class="{ active: theme.background.type === 'image' && theme.background.assetId === item.id }"
          :title="item.name"
        >
          <button
            class="bp-lib-thumb"
            :style="{ backgroundImage: `url(${item.thumbUrl})` }"
            @click="theme.selectBackground(item.id)"
          />
          <button class="bp-lib-del" title="从图库删除" @click.stop="onRemoveImage(item)">
            <X :size="12" />
          </button>
        </div>
      </div>
      <div v-else-if="theme.libraryReady" class="bp-palette-hint">图库还是空的，上传一张试试</div>

      <!-- 亮度推荐（上传后自动分析，可一键采用） -->
      <div v-if="analyzing" class="bp-palette-hint">正在分析图片亮度…</div>
      <div v-else-if="recommendation" class="bp-reco">
        <div class="bp-reco-head">
          <Sparkles :size="14" />
          <span>按这张图的亮度推荐</span>
        </div>
        <div class="bp-reco-values">
          <span>遮罩 {{ recommendation.dim }}%</span>
          <span>暗角 {{ recommendation.vignette }}%</span>
          <span>卡片 {{ recommendation.cardOpacity }}%</span>
        </div>
        <button class="bp-skin-btn" @click="applyRecommendation">
          <Check :size="13" /> 一键采用
        </button>
      </div>

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

      <!-- 背景层提示（图库不可用 / 迁移结果 / 读取失败）——必须可见 -->
      <div v-if="theme.bgNotice" class="bp-storage-error">{{ theme.bgNotice }}</div>
      <!-- 持久化失败必须可见（配额溢出等），否则"设置没保存"无从察觉 -->
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

/* 图库容量标签（贴在 section 标题右侧） */
.bp-usage {
  margin-left: auto;
  font-size: 11px;
  color: var(--app-text-secondary);
  font-variant-numeric: tabular-nums;
}

/* 背景图库网格 */
.bp-lib-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}
.bp-lib-item {
  position: relative;
  border-radius: 8px;
  overflow: hidden;
  border: 2px solid var(--app-border);
  transition: border-color 0.15s;
}
.bp-lib-item:hover { border-color: var(--app-accent-1); }
.bp-lib-item.active {
  border-color: var(--app-accent-1);
  box-shadow: 0 0 0 2px rgba(var(--app-accent-1-rgb), 0.3);
}
.bp-lib-thumb {
  display: block;
  width: 100%;
  height: 56px;
  padding: 0;
  border: none;
  cursor: pointer;
  background-color: var(--app-input-bg);
  background-size: cover;
  background-position: center;
}
.bp-lib-del {
  position: absolute;
  top: 4px; right: 4px;
  width: 20px; height: 20px;
  padding: 0;
  border: none;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, background 0.15s;
}
.bp-lib-item:hover .bp-lib-del { opacity: 1; }
.bp-lib-del:hover { background: rgba(239, 68, 68, 0.9); }

/* 亮度推荐块 */
.bp-reco {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
  border-radius: 10px;
  border: 1px dashed var(--app-accent-1);
  background: rgba(var(--app-accent-1-rgb), 0.06);
}
.bp-reco-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--app-text-heading);
}
.bp-reco-values {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 11px;
  color: var(--app-text-secondary);
  font-variant-numeric: tabular-nums;
}

/* 动态背景缩略图：左上角一个发光小点作标记 */
.bp-thumb-anim::after {
  content: '';
  position: absolute;
  top: 4px; left: 4px;
  width: 6px; height: 6px;
  border-radius: 50%;
  background: var(--app-accent-1);
  box-shadow: 0 0 6px rgba(var(--app-accent-1-rgb), 0.9);
}
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
