import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

/**
 * 主题管理：日间 / 夜间模式
 * - 持久化到 localStorage
 * - 通过 document.documentElement 的 data-theme 属性切换
 * - Element Plus 的 dark class 同步切换
 */
const STORAGE_KEY = 'mediasense_theme'

export const useThemeStore = defineStore('theme', () => {
  const theme = ref(localStorage.getItem(STORAGE_KEY) || 'dark')

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

  function toggle() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
  }

  function setTheme(t) {
    theme.value = t
  }

  // 初始化应用一次
  applyTheme(theme.value)

  watch(theme, (t) => {
    applyTheme(t)
    localStorage.setItem(STORAGE_KEY, t)
  })

  return { theme, toggle, setTheme }
})
