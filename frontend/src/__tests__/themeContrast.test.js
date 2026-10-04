import { describe, it, expect } from 'vitest'
import { BUILTIN_SKINS } from '../utils/skins'
import { composeTokens, deriveFromPalette, DERIVE_RULES } from '../utils/skin'
import { hexToRgb } from '../utils/color'

/**
 * 皮肤配色可读性门禁（对应验收标准 A1「无不可读文字」的量化形式）。
 *
 * 为什么要有：皮肤是纯数据，新增一套皮肤只改 JSON/JS。
 * 若没有这条门禁，"加了套好看但读不清的皮肤"会静默上线。
 * 判定用 WCAG 2.1 相对亮度公式：正文 4.5:1、大字号/次要信息 3:1。
 */

/** WCAG 相对亮度（sRGB 线性化）。 */
function luminance([r, g, b]) {
  const lin = (c) => {
    const v = c / 255
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
}

/** 对比度比值（≥1）。 */
function contrast(a, b) {
  const l1 = luminance(a)
  const l2 = luminance(b)
  const hi = Math.max(l1, l2)
  const lo = Math.min(l1, l2)
  return (hi + 0.05) / (lo + 0.05)
}

/** 只解析纯 hex 值（#rgb / #rrggbb）；var()/rgba() 引用形式返回 null。 */
function solidRgb(value) {
  if (typeof value !== 'string') return null
  const m = value.trim().match(/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/)
  if (!m) return null
  const h = m[1].length === 3 ? m[1].split('').map((c) => c + c).join('') : m[1]
  return hexToRgb(`#${h}`)
}

const round2 = (n) => Math.round(n * 100) / 100

describe('皮肤配色可读性门禁（WCAG AA）', () => {
  for (const skin of BUILTIN_SKINS) {
    it(`${skin.name}：正文文字 vs 卡片底色 ≥ 4.5:1`, () => {
      const tokens = composeTokens(skin)
      const text = solidRgb(tokens['--app-text'])
      const cardBg = solidRgb(tokens['--app-card-bg'])
      expect(text, `${skin.id} --app-text 应为纯 hex`).toBeTruthy()
      expect(cardBg, `${skin.id} --app-card-bg 应为纯 hex`).toBeTruthy()
      expect(round2(contrast(text, cardBg)), `${skin.id} 正文对比度`).toBeGreaterThanOrEqual(4.5)
    })

    it(`${skin.name}：正文文字 vs 页面底色 ≥ 4.5:1`, () => {
      const tokens = composeTokens(skin)
      const text = solidRgb(tokens['--app-text'])
      const bg = solidRgb(tokens['--app-bg'])
      expect(text && bg, `${skin.id} 文字/底色应为纯 hex`).toBeTruthy()
      expect(round2(contrast(text, bg)), `${skin.id} 页面级对比度`).toBeGreaterThanOrEqual(4.5)
    })

    it(`${skin.name}：次要文字 vs 卡片底色 ≥ 3:1（次要信息按大字号标准）`, () => {
      const tokens = composeTokens(skin)
      const secondary = solidRgb(tokens['--app-text-secondary'])
      const cardBg = solidRgb(tokens['--app-card-bg'])
      expect(secondary && cardBg, `${skin.id} 次要文字/卡片底应为纯 hex`).toBeTruthy()
      expect(round2(contrast(secondary, cardBg)), `${skin.id} 次要文字对比度`).toBeGreaterThanOrEqual(3)
    })

    it(`${skin.name}：标题文字比正文更亮或同亮（层级不倒置）`, () => {
      const tokens = composeTokens(skin)
      const text = solidRgb(tokens['--app-text'])
      const heading = solidRgb(tokens['--app-text-heading'])
      if (!text || !heading) return
      const light = skin.mode === 'light'
      // light 主题下"更亮"意味着更暗（对比更强），故方向按主题翻转
      const textL = luminance(text)
      const headingL = luminance(heading)
      if (light) expect(headingL).toBeLessThanOrEqual(textL + 0.02)
      else expect(headingL).toBeGreaterThanOrEqual(textL - 0.02)
    })
  }
})

describe('派生规则健全性', () => {
  it('全部派生规则产出可解析的 CSS 值（无 undefined / NaN）', () => {
    for (const skin of BUILTIN_SKINS) {
      if (!skin.palette) continue
      const derived = deriveFromPalette(skin.palette, skin.mode)
      for (const name of Object.keys(DERIVE_RULES)) {
        const value = derived[name]
        expect(value, `${skin.id} ${name}`).toBeTruthy()
        expect(String(value), `${skin.id} ${name}`).not.toContain('undefined')
        expect(String(value), `${skin.id} ${name}`).not.toContain('NaN')
        expect(String(value), `${skin.id} ${name}`).not.toContain('null')
      }
    }
  })

  it('引擎状态色（可用/错误）在两套材质下都能解析出颜色', () => {
    for (const skin of BUILTIN_SKINS) {
      const tokens = composeTokens(skin)
      expect(tokens['--app-engine-ok-text']).toMatch(/^#[0-9a-fA-F]{6}$/)
      expect(tokens['--app-engine-err-text']).toMatch(/^#[0-9a-fA-F]{6}$/)
    }
  })
})
