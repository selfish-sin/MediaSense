import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { deriveAccentPair, hexToRgb } from '../utils/color'
import { renderBuiltin } from '../utils/backgrounds'
import { resolveSkin, skinMode, composeTokens, writeTokens, skinForMode } from '../utils/skin'
import { DEFAULT_SKIN_ID } from '../utils/skins'

/**
 * 主题管理：皮肤 + 主色调 + 桌面背景。
 * - skinId: 当前皮肤 id（BUILTIN_SKINS 之一），决定整套 --app-* 取值
 * - theme: 由皮肤派生的明暗分支（'dark' | 'light'），对外 API 与重构前一致
 * - accent: 主色 hex（如 #38BDF8），覆盖皮肤自带主色，驱动 --app-accent-1/2 及背景
 * - background: { type:'builtin'|'custom'|'none', id?, url?, dim, blur, cardOpacity }
 *
 * 应用顺序 applySkin()：皮肤 tokens → data-theme → accent（主色最后写，优先级最高）。
 * 持久化到 localStorage；切换时写 CSS 变量到 :root，组件直接用 var()。
 */

const SKIN_KEY = 'mediasense_skin'
const ACCENT_KEY = 'mediasense_accent'
const BG_KEY = 'mediasense_background'

/** 午夜青蓝默认主色。 */
const DEFAULT_ACCENT = '#38BDF8'

const DEFAULT_BG = { type: 'builtin', id: 'abyss', dim: 22, blur: 0, cardOpacity: 72 }

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
  /** 持久化失败原因（配额溢出等）——必须可见，否则"设置没保存"变成看不见的幽灵问题 */
  const storageError = ref('')

  /** 当前皮肤对象（未知 id 自动回退内置默认）。 */
  const skin = computed(() => resolveSkin(skinId.value))
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

  /** 把背景应用到 body::before 伪元素（通过 CSS 变量传递）。 */
  function applyBackground(bg) {
    const root = document.documentElement
    let url = 'none'
    if (bg.type === 'builtin') {
      url = renderBuiltin(bg.id, accent.value)
    } else if (bg.type === 'custom' && bg.url) {
      url = `url("${bg.url}")`
    }
    root.style.setProperty('--app-bg-image', url)
    // 遮罩浓度：纯数字 0-70，消费端用 calc(var(--app-bg-dim) / 100) 得到 0-0.7
    // 注意：不能带 %，否则 calc(28% / 100) 在 opacity 上下文非法 → 滑块失效
    root.style.setProperty('--app-bg-dim', `${bg.dim ?? 0}`)
    root.style.setProperty('--app-bg-blur', `${bg.blur ?? 0}px`)
    // 卡片透明度：0-100 → 0-1，让背景图透过卡片显出
    root.style.setProperty('--app-card-opacity', `${(bg.cardOpacity ?? 100) / 100}`)
  }

  /**
   * 把当前皮肤完整应用到 :root。
   * 顺序不可调换：皮肤 tokens 先写，主色最后写（皮肤里的 accent 值是兜底默认，
   * 用户选定的主色优先级最高）。
   */
  function applySkin() {
    writeTokens(composeTokens(skin.value))
    applyTheme(theme.value)
    applyAccent(accent.value)
  }

  // 初始化：应用一次（CSS 变量设在 :root 上即生效，无需等 DOM 就绪）
  applySkin()
  applyBackground(background.value)

  // —— 皮肤 / 明暗分支 ——
  /** 直接指定皮肤（面板选择用）。 */
  function setSkin(id) {
    skinId.value = id
  }
  /** 切到指定明暗分支——在同 family 内换肤，无同族则回退默认皮肤。 */
  function setTheme(t) {
    skinId.value = skinForMode(skin.value, t).id
  }
  function toggle() {
    setTheme(theme.value === 'dark' ? 'light' : 'dark')
  }

  // —— 主色调 ——
  function setAccent(hex) {
    accent.value = hex
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
    applySkin()
    persist(SKIN_KEY, id)
  })

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
    skinId, skin, theme, accent, background, storageError,
    toggle, setTheme, setSkin,
    setAccent,
    setBackground, resetBackground,
  }
})
