/**
 * 追问面板偏好持久化（localStorage）
 *
 * 背景（2026-10-08）：ChatPanel 的 `searchMode = ref('off')` 与 `showThinking = ref(false)`
 * 是零持久化的纯内存状态——每次刷新页面、每个新任务都要重新切到「严格依据材料」。
 * 用户明确抱怨过这个摩擦，这里把两个偏好落到 localStorage。
 *
 * 契约：
 *  - 读取失败 / JSON 损坏 / 取值非法（如 searchMode='haha'）一律回退默认值
 *    （off / false），绝不把坏值带进请求（后端只认 off|strict|broad）。
 *  - 写入失败（隐私模式 / 配额满）只 warn 不抛：偏好存不下不该让追问功能崩掉，
 *    但也不能静默吞——console.warn 留痕。
 */

/** localStorage 键（prd 指定的命名空间，与旧 yt_tool_* 并存不做迁移） */
export const CHAT_PREFS_KEY = 'ms.chat.prefs'

/** 合法联网模式（须与 utils/chatExport.js 的 SEARCH_MODES、后端 Literal 一致） */
export const SEARCH_MODE_VALUES = ['off', 'strict', 'broad']

export const DEFAULT_CHAT_PREFS = Object.freeze({
  searchMode: 'off',
  showThinking: false,
})

function normalize(raw) {
  const mode = SEARCH_MODE_VALUES.includes(raw?.searchMode) ? raw.searchMode : DEFAULT_CHAT_PREFS.searchMode
  // 只接受真正的布尔值：'false' 字符串/0/1 都算非法值回退
  const thinking = typeof raw?.showThinking === 'boolean' ? raw.showThinking : DEFAULT_CHAT_PREFS.showThinking
  return { searchMode: mode, showThinking: thinking }
}

/**
 * 读偏好。任何异常/非法值都回退默认，不抛。
 * @returns {{searchMode: string, showThinking: boolean}}
 */
export function loadChatPrefs() {
  try {
    const raw = localStorage.getItem(CHAT_PREFS_KEY)
    if (!raw) return { ...DEFAULT_CHAT_PREFS }
    return normalize(JSON.parse(raw))
  } catch (err) {
    // 损坏 JSON 或 localStorage 不可用（隐私模式）：回退默认并留痕
    console.warn('[chatPrefs] 读取失败，回退默认偏好:', err)
    return { ...DEFAULT_CHAT_PREFS }
  }
}

/**
 * 写偏好。失败只 warn（返回 false），调用方不必中断。
 * @returns {boolean} 是否写入成功
 */
export function saveChatPrefs(prefs) {
  try {
    const value = normalize(prefs)
    localStorage.setItem(CHAT_PREFS_KEY, JSON.stringify(value))
    return true
  } catch (err) {
    console.warn('[chatPrefs] 保存失败，本次偏好不会在刷新后保留:', err)
    return false
  }
}