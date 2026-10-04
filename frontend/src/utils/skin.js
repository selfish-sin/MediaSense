/**
 * 皮肤层：把「皮肤数据」合成为一套完整的 `--app-*` token，并注入 :root。
 *
 * 分层（详见 task design.md §2/§4）：
 *   皮肤数据（skins/*.js，纯数据）
 *     → composeTokens() 合成：palette 表驱动派生 → tokens 全量覆盖
 *     → writeTokens() 注入 :root 内联样式（优先级高于样式表，故能覆盖 App.vue 兜底）
 *
 * 皮肤两种声明模式（可同时使用，tokens 优先）：
 * - palette：只给 ~18 个原始色（Catppuccin 风格），其余 50+ token 由 DERIVE_RULES 派生；
 * - tokens ：直接给出完整取值（`legacy-*` 用此模式，保证与重构前逐值一致）。
 *
 * 全部函数均为纯函数（writeTokens/removeTokens 需传入目标元素），便于单测。
 */
import { hexToHsl, hexToRgb, hslToHex } from './color'
import { BUILTIN_SKINS, DEFAULT_SKIN_ID } from './skins'

/** 数值钳制。 */
function clampNum(v, min, max) {
  return Math.min(max, Math.max(min, v))
}

/** 按明度/饱和度微调一个 hex（越界自动钳制）；解析失败原样返回。 */
function shift(hex, dl = 0, ds = 0) {
  const hsl = hexToHsl(hex)
  if (!hsl) return hex
  return hslToHex([hsl[0], clampNum(hsl[1] + ds, 0, 100), clampNum(hsl[2] + dl, 0, 100)])
}

/**
 * 前景强调微调：dark **提亮**、light **加深**。
 * 浅色底上"提亮"会降低对比度，方向必须按材质翻转
 * （dark/light 是两套独立材质，不能简单反转——与 effects 层的材质补偿同一原则）。
 */
function emphasis(hex, amount, mode) {
  return shift(hex, mode === 'light' ? -amount : amount)
}

/** hex → "r, g, b" 三元组（供 `rgba(var(--x), a)` 形式消费）。 */
function triple(hex) {
  const rgb = hexToRgb(hex)
  return rgb ? rgb.join(', ') : '0, 0, 0'
}

/** hex → `rgba(r, g, b, a)`；a 为 0-1。 */
function rgba(hex, a) {
  const rgb = hexToRgb(hex)
  return rgb ? `rgba(${rgb.join(', ')}, ${a})` : 'transparent'
}

/**
 * palette → token 的**派生规则表**（表驱动：新增 token 只需加一行，自动获得全部皮肤支持）。
 * 每条规则 (palette, mode) => token 值；返回空值则跳过该 token。
 *
 * 与主色相关的 token 一律写成 `var(--app-accent-*)` 引用形式，
 * 由 themeStore.applyAccent() 在注入后覆盖，从而随用户选定主色联动。
 */
export const DERIVE_RULES = {
  // —— 底色 / 表面 ——
  '--app-bg': (p) => p.crust,
  '--app-dim-color': (p) => triple(p.base),
  '--app-card-bg': (p) => p.base,
  '--app-sidebar-solid': (p) => p.mantle,
  '--app-sidebar-bg': (p) => `linear-gradient(180deg, ${rgba(p.mantle, 0.86)} 0%, ${rgba(p.crust, 0.92)} 100%)`,
  // —— 描边 / 阴影 ——
  '--app-border': (p) => p.surface0,
  '--app-card-header-border': (p) => p.surface0,
  '--app-card-glow': (p, mode) => (mode === 'light'
    ? '0 10px 30px rgba(15, 23, 42, 0.16)'
    : '0 8px 28px rgba(0, 0, 0, 0.42)'),
  // —— 输入 ——
  '--app-input-bg': (p) => p.surface0,
  '--app-input-border': (p) => p.surface1,
  '--app-hover-bg': (p) => shift(p.surface0, 4),
  '--app-table-header-bg': (p) => shift(p.surface0, -2),
  // —— 文字 ——
  '--app-text': (p) => p.text,
  '--app-text-heading': (p, mode) => emphasis(p.text, 5, mode),
  '--app-text-secondary': (p) => p.subtext0,
  '--app-text-placeholder': (p) => p.overlay0,
  // —— 滚动条 ——
  '--app-scrollbar-track': (p) => p.mantle,
  '--app-scrollbar-thumb': (p) => p.surface1,
  '--app-scrollbar-thumb-hover': (p) => p.surface2,
  // —— chip ——
  '--app-chip-bg': (p) => p.surface0,
  '--app-chip-border': (p) => p.surface1,
  '--app-chip-text': (p) => p.text,
  '--app-chip-hover-bg': (p) => p.surface1,
  '--app-chip-active-bg': () => 'var(--app-accent-1, #38bdf8)',
  '--app-chip-active-soft': () => 'rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.16)',
  // —— 预览 / 配方 ——
  '--app-preview-bg': (p) => p.surface0,
  '--app-preview-text': (p) => p.subtext1,
  '--app-recipes-bg': (p) => p.mantle,
  // —— 覆盖提示条（黄色系警示）——
  '--app-override-bar-bg': (p, mode) => (mode === 'light' ? rgba(p.yellow, 0.18) : rgba(p.yellow, 0.12)),
  '--app-override-bar-border': (p) => rgba(p.yellow, 0.45),
  // —— 日志 ——
  '--app-log-bg': (p) => p.crust,
  '--app-log-text': (p) => p.subtext1,
  '--app-log-time': (p, mode) => emphasis(p.blue, 12, mode),
  '--app-log-border': (p) => p.surface0,
  '--app-log-line-border': (p) => shift(p.surface0, -3),
  // —— 代码 ——
  '--app-code-bg': (p) => p.surface0,
  '--app-code-text': (p, mode) => emphasis(p.blue, 10, mode),
  // —— 进度 ——
  '--app-progress-card-bg-a': (p) => p.mantle,
  '--app-progress-card-bg-b': (p) => p.base,
  '--app-progress-meter-bg': () => 'rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.08)',
  '--app-progress-track': (p) => p.surface0,
  // —— 搜索高亮 ——
  '--app-search-hl-bg': (p, mode) => (mode === 'light' ? rgba(p.yellow, 0.5) : rgba(p.yellow, 0.38)),
  '--app-search-hl-text': (p, mode) => (mode === 'light' ? shift(p.yellow, -38, 10) : shift(p.yellow, 6)),
  '--app-search-hl-active-bg': (p) => p.peach,
  '--app-search-hl-active-text': (p) => p.crust,
  // —— 思考态 ——
  '--app-thinking-bg': () => 'rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.10)',
  '--app-thinking-text': (p, mode) => emphasis(p.blue, 8, mode),
  // —— 引擎状态（绿=可用 / 红=错误）——
  '--app-engine-ok-bg': (p, mode) => rgba(p.green, mode === 'light' ? 0.18 : 0.14),
  '--app-engine-ok-text': (p, mode) => (mode === 'light' ? shift(p.green, -8) : p.green),
  '--app-engine-ok-border': (p) => rgba(p.green, 0.4),
  '--app-engine-err-bg': (p, mode) => rgba(p.red, mode === 'light' ? 0.14 : 0.14),
  '--app-engine-err-text': (p, mode) => (mode === 'light' ? shift(p.red, -6) : p.red),
  '--app-engine-err-border': (p) => rgba(p.red, 0.4),
}

/**
 * 按 id 解析皮肤；未知/缺失一律回退默认皮肤（不抛错，避免整站白屏）。
 * @param {string} skinId
 * @param {Array} [extra] 额外皮肤（用户导入的），一并参与解析
 */
export function resolveSkin(skinId, extra = []) {
  const list = [...BUILTIN_SKINS, ...(extra || [])]
  return list.find((s) => s.id === skinId)
    || list.find((s) => s.id === DEFAULT_SKIN_ID)
    || list[0]
}

/** 皮肤的明暗分支，恒为 'dark' | 'light'。 */
export function skinMode(skin) {
  return skin?.mode === 'light' ? 'light' : 'dark'
}

/**
 * 由 palette 派生 token（逐条执行派生规则；单条失败只跳过该条，不影响整皮肤）。
 * @returns {Record<string, string>}
 */
export function deriveFromPalette(palette, mode) {
  const out = {}
  if (!palette) return out
  for (const [name, rule] of Object.entries(DERIVE_RULES)) {
    try {
      const value = rule(palette, mode)
      if (value !== null && value !== undefined && value !== '') out[name] = String(value)
    } catch (err) {
      // 不静默吞：单条规则异常要可见，但不应阻断整皮肤应用
      console.warn(`[skin] palette 派生失败，已跳过 ${name}:`, err)
    }
  }
  return out
}

/**
 * 合成完整 token 映射：palette 派生 → tokens 全量覆盖（后者优先）。
 * 注意：与主色相关的 token 不在此产出，由 themeStore.applyAccent() 注入。
 */
export function composeTokens(skin) {
  const derived = deriveFromPalette(skin?.palette, skinMode(skin))
  return { ...derived, ...(skin?.tokens || {}) }
}

/** 全部 token 名并集（派生规则 + 各皮肤 tokens）——用于精确清理内联覆盖。 */
export function allTokenNames(userSkins = []) {
  const names = new Set(Object.keys(DERIVE_RULES))
  for (const skin of [...BUILTIN_SKINS, ...userSkins]) {
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
