import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { deriveAccentPair, hexToRgb } from '../utils/color'
import { renderBuiltin } from '../utils/backgrounds'
import { resolveSkin, skinMode, composeTokens, writeTokens, skinForMode } from '../utils/skin'
import { BUILTIN_SKINS, DEFAULT_SKIN_ID } from '../utils/skins'
import { normalizeSkin, skinToJson } from '../utils/skinSchema'
import { DEFAULT_EFFECTS, effectVars, loadEffects } from '../utils/effects'

/**
 * 主题管理：皮肤 + 效果 + 主色调 + 背景媒体。
 * - skinId: 当前皮肤 id（内置或用户导入）——决定整套配色与明暗分支
 * - effects: 氛围参数（遮罩/暗角/玻璃/透明度）——决定观感
 * - accent: 主色 hex——换肤时取皮肤自带主色，用户可再手动微调
 * - background: { type:'builtin'|'custom'|'none', id?, url? }——背景媒体本身
 *
 * 应用顺序 applyVisuals()：皮肤 tokens → data-theme → 效果变量 → accent
 * （accent 最后写，用户选定主色优先级最高；effects 与皮肤互不重叠，顺序无强约束）。
 */

const SKIN_KEY = 'mediasense_skin'
const USER_SKINS_KEY = 'mediasense_user_skins'
const EFFECTS_KEY = 'mediasense_effects'
const ACCENT_KEY = 'mediasense_accent'
const BG_KEY = 'mediasense_background'

/** 午夜青蓝默认主色。 */
const DEFAULT_ACCENT = '#38BDF8'

/** 背景媒体默认值（效果参数已迁出到 effects，见 utils/effects.js）。 */
const DEFAULT_BG = { type: 'builtin', id: 'abyss' }

function loadJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : fallback
  } catch {
    return fallback
  }
}

export const useThemeStore = defineStore('theme', () => {
  const skinId = ref(localStorage.getItem(SKIN_KEY) || DEFAULT_SKIN_ID)
  const accent = ref(localStorage.getItem(ACCENT_KEY) || DEFAULT_ACCENT)
  const background = ref(loadJson(BG_KEY, { ...DEFAULT_BG }))
  // 效果参数独立存储；首次运行从旧 background 对象里继承 dim/blur/cardOpacity（零迁移成本）
  const effects = ref(loadEffects(loadJson(EFFECTS_KEY, null), background.value))
  /** 用户导入的皮肤（存 localStorage；图库化存储留给后续子任务） */
  const userSkins = ref(loadJson(USER_SKINS_KEY, []) || [])
  /** 持久化失败原因（配额溢出等）——必须可见，否则"设置没保存"变成看不见的幽灵问题 */
  const storageError = ref('')

  /** 全部可用皮肤（内置 + 用户导入），面板按此渲染。 */
  const availableSkins = computed(() => [...BUILTIN_SKINS, ...userSkins.value])
  /** 当前皮肤对象（未知 id 自动回退内置默认）。 */
  const skin = computed(() => resolveSkin(skinId.value, userSkins.value))
  /** 明暗分支：由皮肤声明派生，取代重构前独立可写的 theme 状态。 */
  const theme = computed(() => skinMode(skin.value))

  function persist(key, value) {
    try {
      localStorage.setItem(key, value)
      storageError.value = ''
      return true
    } catch (err) {
      // 不静默吞：localStorage 配额约 5MB，背景图 dataURL 超限会走到这里
      console.error('[themeStore] 持久化失败:', key, err)
      storageError.value = err?.name === 'QuotaExceededError'
        ? '浏览器本地存储已满，本次设置不会被保存（换一张小一些的图即可）'
        : '本地存储写入失败，本次设置不会被保存'
      return false
    }
  }

  /** 把一个 hex 主色写成 --app-accent-1/2 及 -rgb 三元组到 :root。 */
  function applyAccent(hex) {
    const { accent1, accent2 } = deriveAccentPair(hex)
    const root = document.documentElement
    root.style.setProperty('--app-accent-1', accent1)
    root.style.setProperty('--app-accent-2', accent2)
    const rgb1 = hexToRgb(accent1)
    const rgb2 = hexToRgb(accent2)
    if (rgb1) root.style.setProperty('--app-accent-1-rgb', rgb1.join(', '))
    if (rgb2) root.style.setProperty('--app-accent-2-rgb', rgb2.join(', '))
    // 旧变量别名，供组件直接 rgba(var(--app-accent-1-rgb), .x) 使用
    root.style.setProperty('--app-accent', accent1)
    const rgbA = hexToRgb(accent1)
    if (rgbA) root.style.setProperty('--app-accent-rgb', rgbA.join(', '))
  }

  function applyTheme(t) {
    const html = document.documentElement
    if (t === 'dark') {
      html.setAttribute('data-theme', 'dark')
      html.classList.add('dark')
    } else {
      html.setAttribute('data-theme', 'light')
      html.classList.remove('dark')
    }
  }

  /** 背景媒体只负责 --app-bg-image；遮罩/模糊由效果层负责。 */
  function applyBackground(bg) {
    const root = document.documentElement
    let url = 'none'
    // 'image' 为子任务（IndexedDB 图库）预留，当前等价于 'custom'
    if (bg.type === 'builtin') {
      url = renderBuiltin(bg.id, accent.value)
    } else if ((bg.type === 'custom' || bg.type === 'image') && bg.url) {
      url = `url("${bg.url}")`
    }
    root.style.setProperty('--app-bg-image', url)
  }

  /**
   * 把当前皮肤 + 效果完整应用到 :root。
   * accent 最后写：皮肤里的 accent 值是兜底默认，用户选定色优先级最高。
   */
  function applyVisuals() {
    writeTokens(composeTokens(skin.value))
    applyTheme(theme.value)
    writeTokens(effectVars(effects.value, theme.value))
    applyAccent(accent.value)
  }

  // 初始化：应用一次（CSS 变量设在 :root 上即生效，无需等 DOM 就绪）
  applyVisuals()
  applyBackground(background.value)

  // —— 皮肤 / 明暗分支 ——
  /**
   * 切换皮肤。换肤即换主色（皮肤自带 accent 优先），
   * 用户随后可在面板里手动微调——这符合"换皮肤=换整套配色"的直觉。
   */
  function setSkin(id) {
    skinId.value = id
    const target = resolveSkin(id, userSkins.value)
    if (target?.accent) accent.value = target.accent
  }
  /** 切到指定明暗分支——在同 family 内换肤，无同族则回退默认皮肤。 */
  function setTheme(t) {
    setSkin(skinForMode(skin.value, t).id)
  }
  function toggle() {
    setTheme(theme.value === 'dark' ? 'light' : 'dark')
  }

  /**
   * 导入一份皮肤 JSON 文本。
   * @returns {{ok: true, skin: object} | {ok: false, error: string}} 失败原因可行动
   */
  function importSkin(jsonText) {
    let parsed
    try {
      parsed = JSON.parse(jsonText)
    } catch (err) {
      return { ok: false, error: `文件不是合法 JSON：${err?.message || '解析失败'}` }
    }
    const result = normalizeSkin(parsed)
    if (!result.ok) return result
    const next = result.skin
    if (BUILTIN_SKINS.some((s) => s.id === next.id)) {
      return { ok: false, error: `id "${next.id}" 与内置皮肤冲突，请修改文件中的 id 后重试` }
    }
    // 同 id 覆盖式导入（便于更新自己做的皮肤）
    userSkins.value = [...userSkins.value.filter((s) => s.id !== next.id), next]
    persist(USER_SKINS_KEY, JSON.stringify(userSkins.value))
    // 若正用着这套皮肤，导入后立即刷新（否则界面停留在旧值）
    if (skinId.value === next.id) applyVisuals()
    return { ok: true, skin: next }
  }

  /** 导出皮肤为 JSON 文本（用于下载/分享）。 */
  function exportSkinJson(id) {
    return skinToJson(resolveSkin(id, userSkins.value))
  }

  // —— 主色调 ——
  function setAccent(hex) {
    accent.value = hex
  }

  // —— 效果 ——
  function setEffect(partial) {
    effects.value = { ...effects.value, ...partial }
  }
  function resetEffects() {
    effects.value = { ...DEFAULT_EFFECTS }
  }

  // —— 背景 ——
  function setBackground(partial) {
    background.value = { ...background.value, ...partial }
  }
  function resetBackground() {
    background.value = { ...DEFAULT_BG }
  }

  // 监听并持久化
  watch(skinId, (id) => {
    applyVisuals()
    persist(SKIN_KEY, id)
  })

  // 效果参数变化只需重写效果变量（皮肤 60+ 项不动，避免拖滑块时全量重算样式）
  watch(effects, (e) => {
    writeTokens(effectVars(e, theme.value))
    persist(EFFECTS_KEY, JSON.stringify(e))
  }, { deep: true })

  watch(accent, (hex) => {
    applyAccent(hex)
    persist(ACCENT_KEY, hex)
    // 内置背景跟随主色重渲染（自定义图不联动）
    if (background.value.type === 'builtin') applyBackground(background.value)
  })

  watch(background, (bg) => {
    applyBackground(bg)
    persist(BG_KEY, JSON.stringify(bg))
  }, { deep: true })

  return {
    skinId, skin, theme, accent, background, effects, storageError,
    availableSkins, userSkins,
    toggle, setTheme, setSkin,
    importSkin, exportSkinJson,
    setAccent,
    setEffect, resetEffects,
    setBackground, resetBackground,
  }
})
