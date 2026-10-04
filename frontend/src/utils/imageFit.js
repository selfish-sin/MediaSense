/**
 * 背景图适配：分析亮度/对比，推荐遮罩与卡片参数。
 *
 * 解决"亮图看不清、暗图发平、用户只能靠试滑块"的问题：
 * 上传后给一次推荐，用户可一键采用，也可继续手调。
 * 采样与统计复用 `utils/color.js`（**不写第二份采样算法**）。
 */
import { sampleImagePixels, analyzePixels, paletteFromPixels } from './color'

/** 数值钳制；非法输入落回 min。 */
function clamp(value, min, max) {
  const n = Number(value)
  if (!Number.isFinite(n)) return min
  return Math.min(max, Math.max(min, n))
}

/**
 * 分析一张图（dataURL / blobURL / http URL 均可）。
 * @returns {Promise<null | {avgLuma:number, contrast:number, saturation:number, samples:number, palette:string[]}>}
 *   分析失败（跨域污染、无固有尺寸、解码失败）返回 **null**，调用方走降级而不是拿 0 值假装成功。
 */
export async function analyzeImage(imgSrc, { sampleSize = 48, paletteCount = 6 } = {}) {
  const sample = await sampleImagePixels(imgSrc, sampleSize)
  if (!sample) return null
  const stats = analyzePixels(sample.data)
  return { ...stats, palette: paletteFromPixels(sample.data, paletteCount) }
}

/**
 * 由图片统计推荐效果参数（**纯函数**，边界可单测）。
 *
 * 规则依据：
 * - 图越亮 → 遮罩越重（否则文字压在亮底上对比不足）；
 * - 图越"花"（亮度极差大）→ 遮罩与暗角都加强（压住杂乱元素）；
 * - 图越暗 → 暗角略加强（避免整体发平、顺便收拢焦点）；
 * - 亮底需要更实的卡片才压得住 → cardOpacity 随亮度上升。
 *
 * @param {{avgLuma?:number, contrast?:number}} stats 0-1 区间的分析结果
 * @returns {{dim:number, vignette:number, top:number, bottom:number, cardOpacity:number}}
 *   与 `utils/effects.js` 的滑块同量纲（0-100），可直接喂给 `themeStore.setEffect`
 */
export function recommendEffects(stats) {
  const luma = clamp(stats?.avgLuma ?? 0.5, 0, 1)
  const contrast = clamp(stats?.contrast ?? 0.5, 0, 1)

  const dim = clamp(Math.round(18 + luma * 42 + contrast * 18), 0, 100)
  const vignette = clamp(Math.round(32 + contrast * 40 + (1 - luma) * 16), 0, 100)
  const top = clamp(Math.round(vignette * 0.25), 0, 100)
  const bottom = clamp(Math.round(vignette * 0.4), 0, 100)
  const cardOpacity = clamp(Math.round(78 + luma * 14), 20, 100)

  return { dim, vignette, top, bottom, cardOpacity }
}
