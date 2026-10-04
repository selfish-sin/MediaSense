import { describe, it, expect } from 'vitest'
import { normalizeSkin, skinToJson, REQUIRED_PALETTE_KEYS } from '../utils/skinSchema'

/** 造一份合法的完整 palette。 */
function fullPalette(overrides = {}) {
  const palette = {}
  for (const key of REQUIRED_PALETTE_KEYS) palette[key] = '#123456'
  return { ...palette, ...overrides }
}

const baseSkin = (extra = {}) => ({
  id: 'my-skin',
  name: '我的皮肤',
  mode: 'dark',
  palette: fullPalette(),
  ...extra,
})

describe('normalizeSkin — 合法输入', () => {
  it('接受 palette 模式皮肤并归一化', () => {
    const res = normalizeSkin(baseSkin())
    expect(res.ok).toBe(true)
    expect(res.skin.id).toBe('my-skin')
    expect(res.skin.mode).toBe('dark')
    expect(res.skin.family).toBe('custom') // 未声明时给默认 family
    expect(Object.keys(res.skin.palette).length).toBeGreaterThanOrEqual(REQUIRED_PALETTE_KEYS.length)
  })

  it('接受 tokens 模式皮肤', () => {
    const res = normalizeSkin({
      id: 'token-skin', name: 'Token 皮肤', mode: 'light',
      tokens: { '--app-bg': '#fff', '--app-text': '#000' },
    })
    expect(res.ok).toBe(true)
    expect(res.skin.tokens['--app-bg']).toBe('#fff')
  })

  it('接受 { skin: {...} } 包装形态（导出文件的格式）', () => {
    const res = normalizeSkin({ skin: baseSkin(), author: 'someone', version: 2 })
    expect(res.ok).toBe(true)
    expect(res.skin.id).toBe('my-skin')
  })

  it('接受并保留合法的 accent 与额外 palette 色', () => {
    const res = normalizeSkin(baseSkin({ accent: '#ABCDEF', palette: fullPalette({ mauve: '#cba6f7' }) }))
    expect(res.ok).toBe(true)
    expect(res.skin.accent).toBe('#ABCDEF')
    expect(res.skin.palette.mauve).toBe('#cba6f7')
  })

  it('tokens 数值转成字符串（CSS 值允许数字，如透明度）', () => {
    const res = normalizeSkin({
      id: 'num-skin', name: 'N', mode: 'dark', tokens: { '--app-x': 0.5 },
    })
    expect(res.ok).toBe(true)
    expect(res.skin.tokens['--app-x']).toBe('0.5')
  })
})

describe('normalizeSkin — 归一化白名单', () => {
  it('夹带的未知字段被丢弃（不把外部数据原样带进 store）', () => {
    const res = normalizeSkin(baseSkin({ evil: { nested: true }, __proto__hack: 1 }))
    expect(res.ok).toBe(true)
    expect(res.skin).not.toHaveProperty('evil')
    expect(res.skin).not.toHaveProperty('__proto__hack')
    expect(Object.keys(res.skin).sort()).toEqual(['family', 'id', 'mode', 'name', 'palette'])
  })

  it('id / name 两端空白被裁剪', () => {
    const res = normalizeSkin(baseSkin({ id: '  padded-id  ', name: '  空白名  ' }))
    expect(res.ok).toBe(true)
    expect(res.skin.id).toBe('padded-id')
    expect(res.skin.name).toBe('空白名')
  })
})

describe('normalizeSkin — 非法输入给出可行动原因', () => {
  it('非对象 / 数组 / null', () => {
    expect(normalizeSkin('not json').ok).toBe(false)
    expect(normalizeSkin(42).ok).toBe(false)
    expect(normalizeSkin(null).ok).toBe(false)
    expect(normalizeSkin([1, 2]).ok).toBe(false)
    expect(normalizeSkin('not json').error).toContain('皮肤对象')
  })

  it('id 非法（含大写/空格/过短/过长）', () => {
    for (const id of ['A-bad', 'has space', 'x', '', '-lead', 'a'.repeat(60)]) {
      const res = normalizeSkin(baseSkin({ id }))
      expect(res.ok, `id=${JSON.stringify(id)}`).toBe(false)
      expect(res.error).toContain('id')
    }
  })

  it('name 缺失或过长', () => {
    expect(normalizeSkin(baseSkin({ name: '' })).error).toContain('name')
    expect(normalizeSkin(baseSkin({ name: '   ' })).error).toContain('name')
    expect(normalizeSkin(baseSkin({ name: 'x'.repeat(61) })).error).toContain('过长')
  })

  it('mode 非法', () => {
    const res = normalizeSkin(baseSkin({ mode: 'dim' }))
    expect(res.ok).toBe(false)
    expect(res.error).toContain('mode')
  })

  it('既无 palette 也无 tokens', () => {
    const res = normalizeSkin({ id: 'empty-skin', name: '空', mode: 'dark' })
    expect(res.ok).toBe(false)
    expect(res.error).toContain('palette')
  })

  it('palette 缺色时列出缺哪些（可行动）', () => {
    const palette = fullPalette()
    delete palette.text
    delete palette.surface1
    const res = normalizeSkin({ id: 'missing', name: 'M', mode: 'dark', palette })
    expect(res.ok).toBe(false)
    expect(res.error).toContain('text')
    expect(res.error).toContain('surface1')
  })

  it('palette 色值非法时视为缺失', () => {
    const res = normalizeSkin({ id: 'badhex', name: 'B', mode: 'dark', palette: fullPalette({ base: 'red' }) })
    expect(res.ok).toBe(false)
    expect(res.error).toContain('base')
  })

  it('tokens 键不以 -- 开头', () => {
    const res = normalizeSkin({
      id: 'badkey', name: 'B', mode: 'dark', tokens: { 'app-bg': '#fff' },
    })
    expect(res.ok).toBe(false)
    expect(res.error).toContain('--')
  })

  it('tokens 值类型非法（对象/数组）', () => {
    const res = normalizeSkin({
      id: 'badval', name: 'B', mode: 'dark', tokens: { '--app-bg': { a: 1 } },
    })
    expect(res.ok).toBe(false)
    expect(res.error).toContain('--app-bg')
  })

  it('accent 非法 hex', () => {
    const res = normalizeSkin(baseSkin({ accent: 'blue' }))
    expect(res.ok).toBe(false)
    expect(res.error).toContain('accent')
  })
})

describe('skinToJson', () => {
  it('导出后能再导入（往返一致）', () => {
    const original = normalizeSkin(baseSkin({ accent: '#123456' })).skin
    const roundTrip = normalizeSkin(JSON.parse(skinToJson(original)))
    expect(roundTrip.ok).toBe(true)
    expect(roundTrip.skin).toEqual(original)
  })

  it('只导出白名单字段', () => {
    const parsed = JSON.parse(skinToJson({ id: 'a-b', name: 'N', mode: 'dark', tokens: {}, secret: 'x' }))
    expect(parsed.skin).not.toHaveProperty('secret')
    expect(parsed.skin.id).toBe('a-b')
  })
})
