/**
 * 皮肤层：把「皮肤数据」合成为一套完整的 `--app-*` token，并注入 :root。
 *
 * 分层（详见 task design.md §2）：
 *   皮肤数据（skins/index.js，纯数据）
 *     → composeTokens() 合成（Phase 4 起在此加入 palette 表驱动派生）
 *     → writeTokens() 注入 :root 内联样式（优先级高于样式表，故能覆盖 App.vue 兜底）
 *
 * 全部函数均为纯函数（writeTokens/removeTokens 需传入目标元素），便于单测。
 */
import { BUILTIN_SKINS, DEFAULT_SKIN_ID } from './skins'

/** 按 id 解析皮肤；未知/缺失一律回退默认皮肤（不抛错，避免整站白屏）。 */
export function resolveSkin(skinId) {
  const list = BUILTIN_SKINS
  return list.find((s) => s.id === skinId)
    || list.find((s) => s.id === DEFAULT_SKIN_ID)
    || list[0]
}

/** 皮肤的明暗分支，恒为 'dark' | 'light'。 */
export function skinMode(skin) {
  return skin?.mode === 'light' ? 'light' : 'dark'
}

/**
 * 合成完整 token 映射（name → value）。
 * Phase 2 只走「tokens 全量覆盖」模式；palette 精简模式的派生规则在 Phase 4 引入。
 */
export function composeTokens(skin) {
  return { ...(skin?.tokens || {}) }
}

/** 全部内置皮肤用到的 token 名并集（用于精确清理内联覆盖）。 */
export function allTokenNames() {
  const names = new Set()
  for (const skin of BUILTIN_SKINS) {
    for (const name of Object.keys(skin.tokens || {})) names.add(name)
  }
  return [...names]
}

/**
 * 在同一 family 内切换到目标明暗分支。
 * 找不到同族时回退到该 mode 的默认皮肤（保证「日间/夜间」按钮永远可用）。
 */
export function skinForMode(currentSkin, mode) {
  const target = mode === 'light' ? 'light' : 'dark'
  const sameFamily = BUILTIN_SKINS.find(
    (s) => s.family === currentSkin?.family && s.mode === target,
  )
  return sameFamily || resolveSkin(target === 'light' ? 'legacy-light' : 'legacy-dark')
}

/**
 * 把 token 写入 :root 内联样式。值为空视为「清除该变量」，回落到样式表兜底。
 * 注意：内联样式优先级高于 `:root[data-theme=...]`，这正是皮肤能覆盖默认主题的原因。
 */
export function writeTokens(tokens, target) {
  const el = target || document.documentElement
  for (const [name, value] of Object.entries(tokens || {})) {
    if (value === null || value === undefined || value === '') el.style.removeProperty(name)
    else el.style.setProperty(name, String(value))
  }
}

/** 移除指定 token 的内联覆盖，回落到样式表兜底值。 */
export function removeTokens(names, target) {
  const el = target || document.documentElement
  for (const name of names || []) el.style.removeProperty(name)
}
