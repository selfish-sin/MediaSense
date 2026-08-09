import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { deriveAccentPair, hexToRgb } from '../utils/color'
import { renderBuiltin } from '../utils/backgrounds'

/**
 * 主题管理：日间/夜间 + 主色调 + 桌面背景。
 * - theme: 'dark' | 'light'
 * - accent: 主色 hex（如 #38BDF8），驱动 --app-accent-1/2 及背景
 * - background: { type:'builtin'|'custom'|'none', id?, url?, dim, blur }
 *
 * 持久化到 localStorage；切换时写 CSS 变量到 :root，组件直接用 var()。
 */

const THEME_KEY = 'mediasense_theme'
const ACCENT_KEY = 'mediasense_accent'
const BG_KEY = 'mediasense_background'

/** 午夜青蓝默认主色。 */
const DEFAULT_ACCENT = '#38BDF8'

const DEFAULT_BG = { type: 'builtin', id: 'abyss', dim: 28, blur: 0, cardOpacity: 100 }

function loadJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : fallback
  } catch {
    return fallback
  }
}

export const useThemeStore = defineStore('theme', () => {
  const theme = ref(localStorage.getItem(THEME_KEY) || 'dark')
  const accent = ref(localStorage.getItem(ACCENT_KEY) || DEFAULT_ACCENT)
  const background = ref(loadJson(BG_KEY, { ...DEFAULT_BG }))

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
    root.style.setProperty('--app-bg-dim', `${bg.dim ?? 0}%`)
    root.style.setProperty('--app-bg-blur', `${bg.blur ?? 0}px`)
    // 卡片透明度：0-100 → 0-1，让背景图透过卡片显出
    root.style.setProperty('--app-card-opacity', `${(bg.cardOpacity ?? 100) / 100}`)
    // 标记是否有背景，控制背景层显隐
    root.style.setProperty('--app-bg-on', bg.type === 'none' ? '0' : '1')
  }

  // 初始化：应用一次
  applyTheme(theme.value)
  applyAccent(accent.value)
  // 背景需在 DOM 就绪后应用；store 在 main.js import 时 body 可能尚未渲染，
  // 但 CSS 变量设在 :root 上即生效，无需等待。
  applyBackground(background.value)

  // —— 主题模式 ——
  function toggle() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
  }
  function setTheme(t) {
    theme.value = t
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
  watch(theme, (t) => {
    applyTheme(t)
    localStorage.setItem(THEME_KEY, t)
  })

  watch(accent, (hex) => {
    applyAccent(hex)
    localStorage.setItem(ACCENT_KEY, hex)
    // 内置背景跟随主色重渲染（自定义图不联动）
    if (background.value.type === 'builtin') applyBackground(background.value)
  })

  watch(background, (bg) => {
    applyBackground(bg)
    localStorage.setItem(BG_KEY, JSON.stringify(bg))
  }, { deep: true })

  return {
    theme, accent, background,
    toggle, setTheme,
    setAccent,
    setBackground, resetBackground,
  }
})
