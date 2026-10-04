/**
 * 颜色工具：HEX ↔ RGB ↔ HSL 互转，以及从图片提取主色调。
 * 所有函数纯计算、无副作用，便于测试。
 */

/** 将 #rrggbb / #rgb 解析为 [r, g, b]（0-255）。非法输入返回 null。 */
export function hexToRgb(hex) {
  if (typeof hex !== 'string') return null
  let h = hex.trim().replace(/^#/, '')
  if (h.length === 3) h = h.split('').map((c) => c + c).join('')
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return null
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]
}

/** [r,g,b] → #rrggbb，小写。 */
export function rgbToHex(rgb) {
  const [r, g, b] = rgb
  const c = (n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0')
  return `#${c(r)}${c(g)}${c(b)}`
}

/** [r,g,b] → [h(0-360), s(0-100), l(0-100)]。 */
export function rgbToHsl(rgb) {
  const [r, g, b] = rgb.map((v) => v / 255)
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const l = (max + min) / 2
  let h = 0
  let s = 0
  if (max !== min) {
    const d = max - min
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
    switch (max) {
      case r: h = ((g - b) / d + (g < b ? 6 : 0)); break
      case g: h = ((b - r) / d + 2); break
      case b: h = ((r - g) / d + 4); break
    }
    h *= 60
  }
  return [h, s * 100, l * 100]
}

/** [h,s,l] → [r,g,b]。 */
export function hslToRgb(hsl) {
  const [h, s, l] = [hsl[0] / 360, hsl[1] / 100, hsl[2] / 100]
  if (s === 0) {
    const v = Math.round(l * 255)
    return [v, v, v]
  }
  const hue2rgb = (p, q, t) => {
    if (t < 0) t += 1
    if (t > 1) t -= 1
    if (t < 1 / 6) return p + (q - p) * 6 * t
    if (t < 1 / 2) return q
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6
    return p
  }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s
  const p = 2 * l - q
  return [
    Math.round(hue2rgb(p, q, h + 1 / 3) * 255),
    Math.round(hue2rgb(p, q, h) * 255),
    Math.round(hue2rgb(p, q, h - 1 / 3) * 255),
  ]
}

/** #hex → [h,s,l]。非法返回 null。 */
export function hexToHsl(hex) {
  const rgb = hexToRgb(hex)
  return rgb ? rgbToHsl(rgb) : null
}

/** [h,s,l] → #hex。 */
export function hslToHex(hsl) {
  return rgbToHex(hslToRgb(hsl))
}

/** 判断颜色是否偏亮（用于决定前景文字用黑还是白）。 */
export function isLight(hex) {
  const rgb = hexToRgb(hex)
  if (!rgb) return true
  // 感知亮度（ITU-R BT.601）
  const [r, g, b] = rgb
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.55
}

/**
 * 给定主色 hex，生成一套协调的强调色。
 * - accent1：主色提亮 6%
 * - accent2：色相 +24°（邻近色），明度略升
 * 用于 --app-accent-1 / --app-accent-2 渐变两端。
 */
export function deriveAccentPair(hex) {
  const hsl = hexToHsl(hex)
  if (!hsl) return { accent1: hex, accent2: hex }
  const [h, s, l] = hsl
  const accent1 = hslToHex([h, Math.min(100, s + 4), Math.min(85, l + 8)])
  const accent2 = hslToHex([(h + 24) % 360, Math.min(100, s - 2), Math.min(80, l + 4)])
  return { accent1, accent2 }
}

/**
 * 从图片提取主色调。返回色板数组（hex 字符串，无 # 重复）。
 * @param {string} imgSrc 图片 URL 或 dataURL
 * @param {object} opts
 * @param {number} opts.count 想要的颜色数，默认 5
 * @param {number} opts.sampleSize 缩放采样边长，默认 48（越大越准越慢）
 * @returns {Promise<string[]>} hex 数组，失败返回 []
 */
export async function extractPalette(imgSrc, { count = 5, sampleSize = 48 } = {}) {
  const sample = await sampleImagePixels(imgSrc, sampleSize)
  return sample ? paletteFromPixels(sample.data, count) : []
}

/**
 * 采样图片像素（缩放到 sampleSize 宽度）。
 * 抽为独立函数供 extractPalette / imageFit 共用——**不写第二份采样**。
 * 失败返回 null（无固有尺寸的 SVG、跨域污染、解码失败）：
 * 调用方据此走降级，而不是拿到 NaN 画布。
 */
export function sampleImagePixels(imgSrc, sampleSize = 48) {
  return new Promise((resolve) => {
    const img = new Image()
    // ★ 只对 http(s) 远端图请求 CORS：data:/blob: 是本地源，设 crossOrigin 在部分浏览器会直接加载失败
    if (/^https?:/i.test(imgSrc)) img.crossOrigin = 'anonymous'
    img.onload = () => {
      try {
        const iw = img.naturalWidth || img.width
        const ih = img.naturalHeight || img.height
        // 无固有尺寸（如只有 viewBox 的 SVG）→ 算不出采样高度，直接放弃
        if (!iw || !ih) { resolve(null); return }
        const w = sampleSize
        const h = Math.max(1, Math.round(sampleSize * ih / iw))
        const canvas = document.createElement('canvas')
        canvas.width = w
        canvas.height = h
        const ctx = canvas.getContext('2d', { willReadFrequently: true })
        ctx.drawImage(img, 0, 0, w, h)
        const { data } = ctx.getImageData(0, 0, w, h)
        resolve({ data, width: w, height: h })
      } catch {
        // 跨域污染等导致 getImageData 失败
        resolve(null)
      }
    }
    img.onerror = () => resolve(null)
    img.src = imgSrc
  })
}

/**
 * 统计像素的亮度/对比/饱和（背景图适配推荐用，纯计算）。
 * @returns {{avgLuma:number, contrast:number, saturation:number, samples:number}}
 * - avgLuma   0-1，越高图越亮
 * - contrast  0-1，亮度极差，越大图越"花"
 * - saturation 0-1，平均饱和度，越高越艳
 */
export function analyzePixels(data) {
  let sum = 0
  let satSum = 0
  let count = 0
  let min = 1
  let max = 0
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 125) continue
    const r = data[i] / 255
    const g = data[i + 1] / 255
    const b = data[i + 2] / 255
    const luma = 0.299 * r + 0.587 * g + 0.114 * b
    const mx = Math.max(r, g, b)
    const mn = Math.min(r, g, b)
    const sat = mx === 0 ? 0 : (mx - mn) / mx
    sum += luma
    satSum += sat
    count += 1
    if (luma < min) min = luma
    if (luma > max) max = luma
  }
  if (!count) return { avgLuma: 0, contrast: 0, saturation: 0, samples: 0 }
  return {
    avgLuma: sum / count,
    contrast: max - min,
    saturation: satSum / count,
    samples: count,
  }
}

/**
 * 中位切分（median-cut）算法：从 RGBA 像素数组提取 count 种主色。
 * 同时丢弃接近纯黑/纯白/低饱和的噪音色。
 */
export function paletteFromPixels(data, count = 5) {
  // 收集像素，跳过近透明像素
  const pixels = []
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 125) continue
    pixels.push([data[i], data[i + 1], data[i + 2]])
  }
  if (!pixels.length) return []

  // 中位切分
  let buckets = [pixels]
  while (buckets.length < count) {
    // 找 R/G/B 跨度最大的桶来切
    let targetIdx = -1
    let maxRange = -1
    let splitChannel = 0
    for (let bi = 0; bi < buckets.length; bi++) {
      const bucket = buckets[bi]
      if (bucket.length < 2) continue
      for (let ch = 0; ch < 3; ch++) {
        let min = 255
        let max = 0
        for (const p of bucket) {
          if (p[ch] < min) min = p[ch]
          if (p[ch] > max) max = p[ch]
        }
        const range = max - min
        if (range > maxRange) {
          maxRange = range
          targetIdx = bi
          splitChannel = ch
        }
      }
    }
    if (targetIdx === -1) break
    const bucket = buckets[targetIdx]
    bucket.sort((a, b) => a[splitChannel] - b[splitChannel])
    const mid = bucket.length >> 1
    buckets.splice(targetIdx, 1, bucket.slice(0, mid), bucket.slice(mid))
  }

  // 每个桶取平均色
  const candidates = buckets
    .filter((b) => b.length)
    .map((bucket) => {
      let r = 0, g = 0, bl = 0
      for (const p of bucket) { r += p[0]; g += p[1]; bl += p[2] }
      const n = bucket.length
      return rgbToHex([r / n, g / n, bl / n])
    })

  // 过滤过暗/过亮/低饱和，保证 UI 可用
  const usable = candidates.filter((hex) => {
    const hsl = hexToHsl(hex)
    if (!hsl) return false
    const [, s, l] = hsl
    return l > 18 && l < 88 && s > 12
  })

  // 去重 + 补齐
  const seen = new Set()
  const result = []
  for (const hex of [...usable, ...candidates]) {
    if (!seen.has(hex)) {
      seen.add(hex)
      result.push(hex)
    }
    if (result.length >= count) break
  }
  return result
}
