import { describe, it, expect } from 'vitest'
import { analyzePixels } from '../utils/color'
import { recommendEffects } from '../utils/imageFit'

/** 把 [[r,g,b,a], ...] 拍平成 Uint8ClampedArray（模拟 canvas getImageData().data）。 */
const rgba = (pixels) => new Uint8ClampedArray(pixels.flat())

describe('analyzePixels — 亮度/对比统计', () => {
  it('全白：亮度≈1、对比 0、样本数正确', () => {
    const data = rgba(Array.from({ length: 16 }, () => [255, 255, 255, 255]))
    const s = analyzePixels(data)
    expect(s.avgLuma).toBeCloseTo(1, 3)
    expect(s.contrast).toBeCloseTo(0, 3)
    expect(s.samples).toBe(16)
  })

  it('全黑：亮度 0、对比 0', () => {
    const data = rgba(Array.from({ length: 8 }, () => [0, 0, 0, 255]))
    const s = analyzePixels(data)
    expect(s.avgLuma).toBeCloseTo(0, 3)
    expect(s.contrast).toBeCloseTo(0, 3)
  })

  it('黑白各半：亮度≈0.5、对比≈1（"花"的图）', () => {
    const data = rgba([
      ...Array.from({ length: 4 }, () => [255, 255, 255, 255]),
      ...Array.from({ length: 4 }, () => [0, 0, 0, 255]),
    ])
    const s = analyzePixels(data)
    expect(s.avgLuma).toBeCloseTo(0.5, 2)
    expect(s.contrast).toBeCloseTo(1, 2)
  })

  it('跳过近透明像素（alpha < 125）', () => {
    const data = rgba([
      [255, 255, 255, 255],
      [0, 0, 0, 0], // 全透明，不计入
      [0, 0, 0, 10], // 近透明，不计入
    ])
    const s = analyzePixels(data)
    expect(s.samples).toBe(1)
    expect(s.avgLuma).toBeCloseTo(1, 2)
  })

  it('全透明 / 空数据返回全 0 且 samples=0（falsy 值不参与运算）', () => {
    const empty = analyzePixels(rgba([]))
    expect(empty).toEqual({ avgLuma: 0, contrast: 0, saturation: 0, samples: 0 })
    const transparent = analyzePixels(rgba([[255, 0, 0, 0]]))
    expect(transparent.samples).toBe(0)
  })

  it('饱和度：纯色高、灰色为 0', () => {
    const gray = analyzePixels(rgba(Array.from({ length: 4 }, () => [128, 128, 128, 255])))
    expect(gray.saturation).toBeCloseTo(0, 3)
    const red = analyzePixels(rgba(Array.from({ length: 4 }, () => [255, 0, 0, 255])))
    expect(red.saturation).toBeCloseTo(1, 3)
  })

  it('亮度采用 BT.601 权重（绿>红>蓝）', () => {
    const green = analyzePixels(rgba([[0, 255, 0, 255]]))
    const blue = analyzePixels(rgba([[0, 0, 255, 255]]))
    expect(green.avgLuma).toBeGreaterThan(blue.avgLuma)
  })
})

describe('recommendEffects — 推荐遮罩与卡片参数', () => {
  it('纯白图：遮罩重、卡片实（否则压不住亮底）', () => {
    const r = recommendEffects({ avgLuma: 1, contrast: 0 })
    expect(r.dim).toBe(60)
    expect(r.cardOpacity).toBe(92)
    expect(r.vignette).toBe(32)
  })

  it('纯黑图：遮罩最轻、暗角略强（避免整体发平）', () => {
    const r = recommendEffects({ avgLuma: 0, contrast: 0 })
    expect(r.dim).toBe(18)
    expect(r.vignette).toBe(48)
    expect(r.cardOpacity).toBe(78)
  })

  it('高对比亮图：遮罩与暗角都拉高（压杂乱元素）', () => {
    const r = recommendEffects({ avgLuma: 1, contrast: 1 })
    expect(r.dim).toBe(78)
    expect(r.vignette).toBe(72)
  })

  it('统计缺失或非法时用 0.5/0.5 兜底，绝不产出 NaN', () => {
    for (const input of [undefined, null, {}, { avgLuma: 'x', contrast: NaN }]) {
      const r = recommendEffects(input)
      for (const key of ['dim', 'vignette', 'top', 'bottom', 'cardOpacity']) {
        expect(Number.isFinite(r[key]), `${JSON.stringify(input)} ${key}`).toBe(true)
      }
    }
  })

  it('越界输入被钳制在 0-100 / 20-100 内', () => {
    const high = recommendEffects({ avgLuma: 9, contrast: 9 })
    const low = recommendEffects({ avgLuma: -9, contrast: -9 })
    for (const key of ['dim', 'vignette', 'top', 'bottom']) {
      expect(high[key]).toBeLessThanOrEqual(100)
      expect(low[key]).toBeGreaterThanOrEqual(0)
    }
    expect(high.cardOpacity).toBeLessThanOrEqual(100)
    expect(low.cardOpacity).toBeGreaterThanOrEqual(20)
  })

  it('单调性：图越亮 → 遮罩与卡片不透明度越高', () => {
    const dark = recommendEffects({ avgLuma: 0.2, contrast: 0.5 })
    const light = recommendEffects({ avgLuma: 0.8, contrast: 0.5 })
    expect(light.dim).toBeGreaterThan(dark.dim)
    expect(light.cardOpacity).toBeGreaterThan(dark.cardOpacity)
  })

  it('单调性：图越"花" → 遮罩与暗角越强', () => {
    const flat = recommendEffects({ avgLuma: 0.5, contrast: 0.1 })
    const busy = recommendEffects({ avgLuma: 0.5, contrast: 0.9 })
    expect(busy.dim).toBeGreaterThan(flat.dim)
    expect(busy.vignette).toBeGreaterThan(flat.vignette)
  })

  it('上下渐晕与暗角保持固定比例（同一视觉语言）', () => {
    const r = recommendEffects({ avgLuma: 0.5, contrast: 0.5 })
    expect(r.top).toBe(Math.round(r.vignette * 0.25))
    expect(r.bottom).toBe(Math.round(r.vignette * 0.4))
  })

  it('返回值与 effects 滑块同量纲：全为整数、落在滑块范围内', () => {
    const r = recommendEffects({ avgLuma: 0.37, contrast: 0.62 })
    for (const key of ['dim', 'vignette', 'top', 'bottom', 'cardOpacity']) {
      expect(Number.isInteger(r[key]), key).toBe(true)
    }
    expect(r.dim).toBeGreaterThanOrEqual(0)
    expect(r.dim).toBeLessThanOrEqual(100)
    expect(r.cardOpacity).toBeGreaterThanOrEqual(20)
  })
})
