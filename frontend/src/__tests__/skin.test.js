import { describe, it, expect } from 'vitest'
import {
  resolveSkin,
  skinMode,
  composeTokens,
  deriveFromPalette,
  allTokenNames,
  skinForMode,
  writeTokens,
  removeTokens,
  DERIVE_RULES,
} from '../utils/skin'
import { BUILTIN_SKINS, DEFAULT_SKIN_ID } from '../utils/skins'
import { REQUIRED_PALETTE_KEYS } from '../utils/skinSchema'

/** 极简 style 宿主 stub：只记录 setProperty/removeProperty，不依赖 jsdom 的真实 CSSOM。 */
function fakeEl() {
  const props = {}
  return {
    props,
    style: {
      setProperty: (k, v) => { props[k] = String(v) },
      removeProperty: (k) => { delete props[k] },
    },
  }
}

describe('resolveSkin', () => {
  it('已知 id 返回对应皮肤', () => {
    expect(resolveSkin('legacy-light').id).toBe('legacy-light')
  })

  it('未知 / 缺失 / 空 id 一律回退默认皮肤，绝不抛错', () => {
    expect(resolveSkin('not-exist').id).toBe(DEFAULT_SKIN_ID)
    expect(resolveSkin(undefined).id).toBe(DEFAULT_SKIN_ID)
    expect(resolveSkin(null).id).toBe(DEFAULT_SKIN_ID)
    expect(resolveSkin('').id).toBe(DEFAULT_SKIN_ID)
  })

  it('额外皮肤（用户导入）参与解析，且未知 id 仍回退默认', () => {
    const custom = { id: 'my-skin', name: '自定义', mode: 'dark', palette: {} }
    expect(resolveSkin('my-skin', [custom]).id).toBe('my-skin')
    expect(resolveSkin('my-skin').id).toBe(DEFAULT_SKIN_ID)
    expect(resolveSkin('nope', [custom]).id).toBe(DEFAULT_SKIN_ID)
  })
})

describe('skinMode', () => {
  it('按皮肤声明返回 dark / light', () => {
    expect(skinMode(resolveSkin('legacy-dark'))).toBe('dark')
    expect(skinMode(resolveSkin('legacy-light'))).toBe('light')
    expect(skinMode(resolveSkin('catppuccin-latte'))).toBe('light')
    expect(skinMode(resolveSkin('catppuccin-mocha'))).toBe('dark')
  })

  it('mode 缺失或非法时兜底为 dark（不返回 undefined，避免 class 切换失效）', () => {
    expect(skinMode({})).toBe('dark')
    expect(skinMode({ mode: 'weird' })).toBe('dark')
    expect(skinMode(undefined)).toBe('dark')
  })
})

describe('composeTokens', () => {
  it('输出皮肤的完整 token 映射', () => {
    const tokens = composeTokens(resolveSkin('legacy-dark'))
    expect(tokens['--app-bg']).toBe('#0b1120')
    expect(tokens['--app-border']).toBe('#1e2a3f')
  })

  it('皮肤数据缺失时返回空对象而不是抛错', () => {
    expect(composeTokens(undefined)).toEqual({})
    expect(composeTokens({})).toEqual({})
  })

  it('返回的是副本，改动结果不会污染内置皮肤数据', () => {
    const tokens = composeTokens(resolveSkin('legacy-dark'))
    tokens['--app-bg'] = '#000000'
    expect(resolveSkin('legacy-dark').tokens['--app-bg']).toBe('#0b1120')
  })
})

describe('palette 派生', () => {
  const mocha = resolveSkin('catppuccin-mocha')

  it('Catppuccin 皮肤走 palette 模式（不含 tokens 全量覆盖）', () => {
    expect(mocha.palette).toBeTruthy()
    expect(mocha.tokens).toBeUndefined()
  })

  it('palette 派生出的 token 覆盖全部派生规则', () => {
    const derived = deriveFromPalette(mocha.palette, 'dark')
    for (const name of Object.keys(DERIVE_RULES)) {
      expect(derived[name], name).toBeTruthy()
    }
  })

  it('派生值取自 palette 且语义正确（底色/文字/描边）', () => {
    const tokens = composeTokens(mocha)
    expect(tokens['--app-bg']).toBe(mocha.palette.crust)
    expect(tokens['--app-card-bg']).toBe(mocha.palette.base)
    expect(tokens['--app-text']).toBe(mocha.palette.text)
    expect(tokens['--app-text-secondary']).toBe(mocha.palette.subtext0)
    expect(tokens['--app-border']).toBe(mocha.palette.surface0)
    expect(tokens['--app-dim-color']).toBe('30, 30, 46') // base #1e1e2e 的 rgb 三元组
  })

  it('主色相关 token 写成引用形式，从而跟随用户选定主色', () => {
    const tokens = composeTokens(mocha)
    expect(tokens['--app-chip-active-bg']).toContain('var(--app-accent-1')
    expect(tokens['--app-chip-active-soft']).toContain('var(--app-accent-1-rgb')
    expect(tokens['--app-thinking-bg']).toContain('var(--app-accent-1-rgb')
  })

  it('dark / light 分支产出不同的材质参数（shadow / 半透明底色）', () => {
    const dark = composeTokens(resolveSkin('catppuccin-mocha'))
    const light = composeTokens(resolveSkin('catppuccin-latte'))
    expect(dark['--app-card-glow']).toContain('rgba(0, 0, 0')
    expect(light['--app-card-glow']).toContain('rgba(15, 23, 42')
  })

  it('palette 缺失时派生返回空对象，不抛错', () => {
    expect(deriveFromPalette(null, 'dark')).toEqual({})
    expect(deriveFromPalette(undefined, 'dark')).toEqual({})
  })

  it('palette 色值非法时不产出该 token（不写入 NaN/undefined）', () => {
    const derived = deriveFromPalette({ base: 'not-a-color' }, 'dark')
    expect(derived['--app-dim-color']).toBe('0, 0, 0')
  })
})

describe('内置皮肤数据完整性', () => {
  it('每套皮肤都能合成出完整且非空的 token（tokens 模式与 palette 派生模式统一验证）', () => {
    for (const skin of BUILTIN_SKINS) {
      const tokens = composeTokens(skin)
      expect(Object.keys(tokens).length, `${skin.id} 合成 token 数`).toBeGreaterThan(50)
      for (const [name, value] of Object.entries(tokens)) {
        expect(typeof value, `${skin.id} ${name}`).toBe('string')
        expect(value.length, `${skin.id} ${name}`).toBeGreaterThan(0)
      }
    }
  })

  it('palette 模式皮肤声明了全部必需色（缺任一都会派生出错误配色）', () => {
    for (const skin of BUILTIN_SKINS) {
      if (!skin.palette) continue
      for (const key of REQUIRED_PALETTE_KEYS) {
        expect(skin.palette[key], `${skin.id}.${key}`).toMatch(/^#[0-9a-fA-F]{6}$/)
      }
    }
  })

  it('同一 family 的明暗两套皮肤 token 名集合必须完全一致', () => {
    // 否则换肤会残留上一套皮肤的变量（如 dark 有而 light 没有的 token）
    const names = (skin) => Object.keys(skin.tokens).sort()
    expect(names(resolveSkin('legacy-dark'))).toEqual(names(resolveSkin('legacy-light')))
  })

  it('legacy 皮肤仍保持重构前的 59 个 token（等价基准）', () => {
    expect(Object.keys(resolveSkin('legacy-dark').tokens)).toHaveLength(59)
    expect(Object.keys(resolveSkin('legacy-light').tokens)).toHaveLength(59)
  })

  it('皮肤 id 唯一且非空', () => {
    const ids = BUILTIN_SKINS.map((s) => s.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const id of ids) expect(id).toBeTruthy()
  })

  it('每套皮肤都有 name / mode / family', () => {
    for (const skin of BUILTIN_SKINS) {
      expect(skin.name, skin.id).toBeTruthy()
      expect(['dark', 'light'], skin.id).toContain(skin.mode)
      expect(skin.family, skin.id).toBeTruthy()
    }
  })
})

describe('allTokenNames', () => {
  it('返回派生规则与各皮肤 token 的并集，且无重复', () => {
    const names = allTokenNames()
    expect(names).toContain('--app-bg')
    expect(names).toContain('--app-engine-err-border')
    expect(new Set(names).size).toBe(names.length)
    // 至少覆盖 legacy 的全部 token
    const legacyCount = Object.keys(resolveSkin('legacy-dark').tokens).length
    expect(names.length).toBeGreaterThanOrEqual(legacyCount)
  })

  it('可包含用户导入皮肤的 token 名', () => {
    const names = allTokenNames([{ id: 'x', tokens: { '--app-custom-thing': '#fff' } }])
    expect(names).toContain('--app-custom-thing')
  })
})

describe('skinForMode', () => {
  it('同 family 内切换到目标明暗分支', () => {
    const dark = resolveSkin('legacy-dark')
    expect(skinForMode(dark, 'light').id).toBe('legacy-light')
    expect(skinForMode(resolveSkin('legacy-light'), 'dark').id).toBe('legacy-dark')
  })

  it('Catppuccin 族内 Mocha ⇄ Latte 互切', () => {
    expect(skinForMode(resolveSkin('catppuccin-mocha'), 'light').id).toBe('catppuccin-latte')
    expect(skinForMode(resolveSkin('catppuccin-latte'), 'dark').id).toBe('catppuccin-mocha')
  })

  it('family 无对应分支时回退该 mode 的默认皮肤', () => {
    expect(skinForMode({ family: 'unknown', mode: 'dark' }, 'light').id).toBe('legacy-light')
    expect(skinForMode(undefined, 'dark').id).toBe('legacy-dark')
  })

  it('非法 mode 视为 dark', () => {
    expect(skinForMode(resolveSkin('legacy-light'), 'nonsense').id).toBe('legacy-dark')
  })
})

describe('writeTokens / removeTokens', () => {
  it('写入 token 到目标元素的 style', () => {
    const el = fakeEl()
    writeTokens({ '--app-bg': '#fff', '--app-text': '#000' }, el)
    expect(el.props['--app-bg']).toBe('#fff')
    expect(el.props['--app-text']).toBe('#000')
  })

  it('空值 / null / undefined 视为清除该变量（回落样式表兜底）', () => {
    const el = fakeEl()
    writeTokens({ '--app-bg': '#fff', '--app-text': '#000' }, el)
    writeTokens({ '--app-bg': '', '--app-text': null, '--app-border': undefined }, el)
    expect(el.props).not.toHaveProperty('--app-bg')
    expect(el.props).not.toHaveProperty('--app-text')
    expect(el.props).not.toHaveProperty('--app-border')
  })

  it('非字符串值被转成字符串写入', () => {
    const el = fakeEl()
    writeTokens({ '--app-bg-dim': 22 }, el)
    expect(el.props['--app-bg-dim']).toBe('22')
  })

  it('tokens 为空 / 缺失时不报错', () => {
    const el = fakeEl()
    expect(() => writeTokens(undefined, el)).not.toThrow()
    expect(() => writeTokens({}, el)).not.toThrow()
  })

  it('removeTokens 只清除指定变量，保留其他', () => {
    const el = fakeEl()
    writeTokens({ '--app-bg': '#fff', '--app-text': '#000' }, el)
    removeTokens(['--app-bg'], el)
    expect(el.props).not.toHaveProperty('--app-bg')
    expect(el.props['--app-text']).toBe('#000')
  })

  it('removeTokens 传入空列表不报错', () => {
    const el = fakeEl()
    expect(() => removeTokens(undefined, el)).not.toThrow()
    expect(() => removeTokens([], el)).not.toThrow()
  })
})
