/**
 * 效果层：把「用户可调的视觉参数」换算为 :root 的 CSS 变量。
 *
 * 与皮肤层职责分离：
 * - 皮肤层（skin.js / skins/）决定**配色**（--app-bg / --app-text …）；
 * - 效果层（本模块）决定**氛围**（遮罩 scrim / 径向暗角 / 玻璃厚度 / surface 分档）。
 *
 * 设计要点：
 * - 遮罩一律用**中性黑/白**（--app-scrim-rgb），不再用带色相的 --app-dim-color：
 *   带色相的遮罩会把任何背景图统一染成同一种脏色，这是重构前"背景发灰蒙"的根因；
 * - scrim 分四层（整体浓度 / 顶部渐晕 / 底部渐晕 / 径向暗角），由 body::after 合成；
 * - surface 分三档（宿主卡片 / 抽屉面板 / 弹窗），保证不同浮层有可辨的层级。
 */

/** 效果默认值（首次使用或重置时）。 */
export const DEFAULT_EFFECTS = {
  dim: 26,            // 整体遮罩浓度 0-70（滑块 0-100，换算上限 0.70）
  vignette: 55,       // 径向暗角强度 0-100（上限 0.65）
  top: 12,            // 顶部渐晕 0-100（上限 0.45）
  bottom: 22,         // 底部渐晕 0-100（上限 0.60）
  cardOpacity: 72,    // 卡片透明度基准 20-100（→ surface-1）
  glassBlur: 14,      // 卡片玻璃模糊 0-24px
  glassSaturate: 170, // 玻璃饱和度补偿 %（纯 blur 会把背后颜色推向灰，需补偿）
  bgBlur: 0,          // 背景图模糊 0-20px（作用于 body::before，与卡片玻璃无关）
}

/** 各层 scrim 的 alpha 上限：滑块 0-100 按此系数折算，避免滑到顶就全黑。 */
const SCRIM_MAX = { dim: 0.7, vignette: 0.65, top: 0.45, bottom: 0.6 }

/** 数值钳制；非法输入（NaN/字符串/undefined）落回 min，避免写出非法 CSS 值。 */
function clamp(value, min, max) {
  const n = Number(value)
  if (!Number.isFinite(n)) return min
  return Math.min(max, Math.max(min, n))
}

/** 0-100 的滑块值 → 0..max 的 alpha 字符串（3 位小数，避免浮点噪声）。 */
function alpha(value, max) {
  return ((clamp(value, 0, 100) / 100) * max).toFixed(3)
}

/**
 * 把效果参数换算为 CSS 变量映射（纯函数，可单测）。
 * @param {object} effects 效果参数（缺字段时按 0/默认兜底）
 * @param {'dark'|'light'} mode 明暗分支——决定遮罩用中性黑还是中性白
 * @returns {Record<string, string>}
 */
export function effectVars(effects, mode = 'dark') {
  const e = effects || {}
  const card = clamp(e.cardOpacity ?? 100, 0, 100) / 100
  const light = mode === 'light'
  // dark / light 是两套独立材质参数（业界共识，不能简单反转）：
  // 浅色主题里"浅色卡片压在浅色背景上"天然缺乏对比，边界立不住，故整体加一档不透明度。
  const surfaceBase = Math.min(1, card + (light ? 0.12 : 0))
  // 卡片常态投影：dark 用纯黑（暗底上要够重才分得出层），
  // light 用低透明度冷色（浅底上用重黑会显脏）
  const cardRest = light ? '0 2px 12px rgba(15, 23, 42, 0.10)' : '0 4px 18px rgba(0, 0, 0, 0.3)'

  return {
    // —— scrim 多层遮罩（由 body::after 以 4 层 background-image 合成）——
    '--app-scrim-rgb': light ? '255, 255, 255' : '0, 0, 0',
    '--app-scrim-dim-a': alpha(e.dim, SCRIM_MAX.dim),
    '--app-scrim-vignette-a': alpha(e.vignette, SCRIM_MAX.vignette),
    '--app-scrim-top-a': alpha(e.top, SCRIM_MAX.top),
    '--app-scrim-bottom-a': alpha(e.bottom, SCRIM_MAX.bottom),
    // —— 玻璃三件套（blur + saturate + 高光描边）——
    '--app-glass-blur': `${clamp(e.glassBlur, 0, 40)}px`,
    '--app-glass-saturate': `${clamp(e.glassSaturate ?? 170, 100, 250)}%`,
    // dark 用白色半透明高光边（暗底上低 alpha 玻璃若无描边则边界不可辨）；
    // light 用主题深色细边——白底上的白色描边既不可见又显脏，两者是独立材质。
    '--app-glass-border': light ? 'var(--app-border)' : 'rgba(255, 255, 255, 0.14)',
    // 玻璃高光层：dark 模式在卡片半透明深色底上再叠一层极淡白色，
    // 使卡片"比背景亮"从而产生浮起感；否则深色半透明卡片在深色背景上等于隐形。
    // light 模式不需要（浅色卡片本就比背景亮）。
    '--app-glass-sheen': light ? 'transparent' : 'rgba(255, 255, 255, 0.045)',
    // 卡片常态投影：玻璃卡片靠投影才"浮"得起来。重构前只在 :hover 给阴影，
    // 常态无投影 → 浅色模式下白卡片压在浅色背景上完全分不开（实测截图确认）。
    '--app-card-rest': cardRest,
    // Element Plus 的卡片阴影走它自己的变量，一并覆盖，避免两套阴影打架
    '--el-card-box-shadow': cardRest,
    // —— surface 分档 ——
    '--app-surface-1-opacity': surfaceBase.toFixed(3),
    '--app-surface-2-opacity': Math.min(1, surfaceBase + 0.08).toFixed(3),
    '--app-surface-3-opacity': Math.min(1, surfaceBase + 0.16).toFixed(3),
    // 兼容别名：既有消费方直接读 --app-card-opacity
    '--app-card-opacity': surfaceBase.toFixed(3),
    // 背景图模糊（作用于 body::before）
    '--app-bg-blur': `${clamp(e.bgBlur, 0, 40)}px`,
  }
}

/**
 * 读取持久化的效果参数，缺失时用默认值；
 * 并兼容重构前的存储形态（效果参数曾与背景混存在 mediasense_background 里）。
 */
export function loadEffects(saved, legacyBackground) {
  if (saved && typeof saved === 'object') return { ...DEFAULT_EFFECTS, ...saved }
  const legacy = legacyBackground || {}
  return {
    ...DEFAULT_EFFECTS,
    dim: legacy.dim ?? DEFAULT_EFFECTS.dim,
    bgBlur: legacy.blur ?? DEFAULT_EFFECTS.bgBlur,
    cardOpacity: legacy.cardOpacity ?? DEFAULT_EFFECTS.cardOpacity,
  }
}
