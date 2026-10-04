import { describe, it, expect } from 'vitest'
import { DEFAULT_EFFECTS, effectVars, loadEffects } from '../utils/effects'

/** 把 "0.182" 这类 alpha 字符串转成数字断言（避免浮点 toFixed 的字符串抖动）。 */
const num = (v) => parseFloat(v)

describe('effectVars — scrim 多层遮罩', () => {
  it('遮罩色随明暗分支切换为中性黑/白（这是"背景图不被染脏"的关键）', () => {
    expect(effectVars(DEFAULT_EFFECTS, 'dark')['--app-scrim-rgb']).toBe('0, 0, 0')
    expect(effectVars(DEFAULT_EFFECTS, 'light')['--app-scrim-rgb']).toBe('255, 255, 255')
  })

  it('mode 缺省按 dark 处理', () => {
    expect(effectVars(DEFAULT_EFFECTS)['--app-scrim-rgb']).toBe('0, 0, 0')
  })

  it('各层 alpha 按上限系数换算（滑块 0-100 → 实际 alpha）', () => {
    const v = effectVars({ dim: 26, vignette: 55, top: 12, bottom: 22 }, 'dark')
    expect(num(v['--app-scrim-dim-a'])).toBeCloseTo(0.182, 3) // 26/100*0.70
    expect(num(v['--app-scrim-vignette-a'])).toBeCloseTo(0.358, 3) // 55/100*0.65
    expect(num(v['--app-scrim-top-a'])).toBeCloseTo(0.054, 3) // 12/100*0.45
    expect(num(v['--app-scrim-bottom-a'])).toBeCloseTo(0.132, 3) // 22/100*0.60
  })

  it('滑块值为 0 时完全不压暗（不叠任何遮罩）', () => {
    const v = effectVars({ dim: 0, vignette: 0, top: 0, bottom: 0 }, 'dark')
    expect(v['--app-scrim-dim-a']).toBe('0.000')
    expect(v['--app-scrim-vignette-a']).toBe('0.000')
    expect(v['--app-scrim-top-a']).toBe('0.000')
    expect(v['--app-scrim-bottom-a']).toBe('0.000')
  })

  it('滑块拉满时按上限钳制（不会全黑）', () => {
    const v = effectVars({ dim: 100, vignette: 100, top: 100, bottom: 100 }, 'dark')
    expect(v['--app-scrim-dim-a']).toBe('0.700')
    expect(v['--app-scrim-vignette-a']).toBe('0.650')
    expect(v['--app-scrim-top-a']).toBe('0.450')
    expect(v['--app-scrim-bottom-a']).toBe('0.600')
  })

  it('越界与非法输入被钳制，不产出非法 CSS 值', () => {
    const v = effectVars({ dim: 999, vignette: -50, top: 'abc', bottom: null }, 'dark')
    expect(v['--app-scrim-dim-a']).toBe('0.700')
    expect(v['--app-scrim-vignette-a']).toBe('0.000')
    expect(v['--app-scrim-top-a']).toBe('0.000')
    expect(v['--app-scrim-bottom-a']).toBe('0.000')
    expect(v['--app-scrim-top-a']).not.toBe('NaN')
  })

  it('参数为空对象 / undefined 时全部落 0，不抛错', () => {
    expect(() => effectVars({}, 'dark')).not.toThrow()
    expect(() => effectVars(undefined, 'dark')).not.toThrow()
    expect(effectVars({}, 'dark')['--app-scrim-dim-a']).toBe('0.000')
  })
})

describe('effectVars — 玻璃三件套', () => {
  it('blur / saturate 带上正确单位', () => {
    const v = effectVars({ glassBlur: 14, glassSaturate: 170 }, 'dark')
    expect(v['--app-glass-blur']).toBe('14px')
    expect(v['--app-glass-saturate']).toBe('170%')
  })

  it('blur 与 saturate 越界被钳制', () => {
    const v = effectVars({ glassBlur: 999, glassSaturate: 5 }, 'dark')
    expect(v['--app-glass-blur']).toBe('40px')
    expect(v['--app-glass-saturate']).toBe('100%')
  })

  it('saturate 缺省为 170%（纯 blur 会把背景色推向灰，必须补偿）', () => {
    expect(effectVars({}, 'dark')['--app-glass-saturate']).toBe('170%')
  })

  it('描边分材质：dark 用白色高光边，light 用主题深色细边', () => {
    expect(effectVars(DEFAULT_EFFECTS, 'dark')['--app-glass-border']).toBe('rgba(255, 255, 255, 0.14)')
    expect(effectVars(DEFAULT_EFFECTS, 'light')['--app-glass-border']).toBe('var(--app-border)')
  })

  it('高光层分材质：dark 叠极淡白让卡片比背景亮，light 不需要', () => {
    expect(effectVars(DEFAULT_EFFECTS, 'dark')['--app-glass-sheen']).toBe('rgba(255, 255, 255, 0.045)')
    expect(effectVars(DEFAULT_EFFECTS, 'light')['--app-glass-sheen']).toBe('transparent')
  })

  it('提供卡片常态投影，且 Element Plus 阴影变量与之同源（避免两套阴影打架）', () => {
    const dark = effectVars(DEFAULT_EFFECTS, 'dark')
    const light = effectVars(DEFAULT_EFFECTS, 'light')
    expect(dark['--app-card-rest']).toContain('rgba(0, 0, 0')
    expect(light['--app-card-rest']).toContain('rgba(15, 23, 42')
    expect(dark['--el-card-box-shadow']).toBe(dark['--app-card-rest'])
    expect(light['--el-card-box-shadow']).toBe(light['--app-card-rest'])
  })
})

describe('effectVars — surface 分档与兼容别名', () => {
  it('三档透明度按基准递进（宿主卡片 < 抽屉 < 弹窗）', () => {
    const v = effectVars({ cardOpacity: 60 }, 'dark')
    expect(num(v['--app-surface-1-opacity'])).toBeCloseTo(0.6, 3)
    expect(num(v['--app-surface-2-opacity'])).toBeCloseTo(0.68, 3)
    expect(num(v['--app-surface-3-opacity'])).toBeCloseTo(0.76, 3)
    expect(num(v['--app-surface-1-opacity'])).toBeLessThan(num(v['--app-surface-2-opacity']))
    expect(num(v['--app-surface-2-opacity'])).toBeLessThan(num(v['--app-surface-3-opacity']))
  })

  it('三档透明度都不超过 1', () => {
    const v = effectVars({ cardOpacity: 100 }, 'dark')
    expect(num(v['--app-surface-1-opacity'])).toBeLessThanOrEqual(1)
    expect(num(v['--app-surface-3-opacity'])).toBeLessThanOrEqual(1)
  })

  it('light 模式 surface 整体提高一档（浅色卡片压浅色背景，够实才立得住边界）', () => {
    const darkV = effectVars({ cardOpacity: 60 }, 'dark')
    const lightV = effectVars({ cardOpacity: 60 }, 'light')
    expect(num(darkV['--app-surface-1-opacity'])).toBeCloseTo(0.6, 3)
    expect(num(lightV['--app-surface-1-opacity'])).toBeCloseTo(0.72, 3) // 0.60 + 0.12
    expect(num(lightV['--app-surface-1-opacity']))
      .toBeGreaterThan(num(darkV['--app-surface-1-opacity']))
  })

  it('light 模式加档后仍不超过 1（高透明度设置不会溢出）', () => {
    const v = effectVars({ cardOpacity: 95 }, 'light')
    expect(num(v['--app-surface-1-opacity'])).toBeLessThanOrEqual(1)
    expect(num(v['--app-surface-3-opacity'])).toBeLessThanOrEqual(1)
  })

  it('light 模式下兼容别名仍与 surface-1 保持一致', () => {
    const v = effectVars({ cardOpacity: 60 }, 'light')
    expect(v['--app-card-opacity']).toBe(v['--app-surface-1-opacity'])
  })

  it('--app-card-opacity 作为兼容别名与 surface-1 一致（旧消费方不受影响）', () => {
    const v = effectVars({ cardOpacity: 73 }, 'dark')
    expect(v['--app-card-opacity']).toBe(v['--app-surface-1-opacity'])
  })

  it('cardOpacity 缺省视为 100%（不透明）', () => {
    expect(effectVars({}, 'dark')['--app-surface-1-opacity']).toBe('1.000')
  })

  it('背景模糊带上 px 并钳制上限', () => {
    expect(effectVars({ bgBlur: 8 }, 'dark')['--app-bg-blur']).toBe('8px')
    expect(effectVars({ bgBlur: 999 }, 'dark')['--app-bg-blur']).toBe('40px')
  })
})

describe('loadEffects', () => {
  it('有已存参数时与默认值合并（缺失字段补默认）', () => {
    const e = loadEffects({ dim: 5 }, null)
    expect(e.dim).toBe(5)
    expect(e.glassBlur).toBe(DEFAULT_EFFECTS.glassBlur)
    expect(e.vignette).toBe(DEFAULT_EFFECTS.vignette)
  })

  it('无已存参数时用默认值', () => {
    expect(loadEffects(null, null)).toEqual(DEFAULT_EFFECTS)
  })

  it('兼容旧存储：从 background 对象继承 dim / blur / cardOpacity（零迁移成本）', () => {
    const e = loadEffects(null, { type: 'builtin', id: 'abyss', dim: 1, blur: 4, cardOpacity: 72 })
    expect(e.dim).toBe(1)
    expect(e.bgBlur).toBe(4)
    expect(e.cardOpacity).toBe(72)
    // 旧版本没有的新字段仍取默认
    expect(e.vignette).toBe(DEFAULT_EFFECTS.vignette)
    expect(e.glassBlur).toBe(DEFAULT_EFFECTS.glassBlur)
  })

  it('旧 background 缺字段时落默认，不产生 undefined', () => {
    const e = loadEffects(null, { type: 'none' })
    expect(e.dim).toBe(DEFAULT_EFFECTS.dim)
    expect(e.bgBlur).toBe(DEFAULT_EFFECTS.bgBlur)
    expect(e.cardOpacity).toBe(DEFAULT_EFFECTS.cardOpacity)
  })
})
