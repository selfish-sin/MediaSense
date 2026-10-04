import { describe, it, expect } from 'vitest'
import {
  resolveSkin,
  skinMode,
  composeTokens,
  allTokenNames,
  skinForMode,
  writeTokens,
  removeTokens,
} from '../utils/skin'
import { BUILTIN_SKINS, DEFAULT_SKIN_ID } from '../utils/skins'

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
})

describe('skinMode', () => {
  it('按皮肤声明返回 dark / light', () => {
    expect(skinMode(resolveSkin('legacy-dark'))).toBe('dark')
    expect(skinMode(resolveSkin('legacy-light'))).toBe('light')
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

describe('内置皮肤数据完整性', () => {
  it('每套皮肤的 token 值都是非空字符串', () => {
    for (const skin of BUILTIN_SKINS) {
      for (const [name, value] of Object.entries(skin.tokens)) {
        expect(typeof value, `${skin.id} ${name}`).toBe('string')
        expect(value.length, `${skin.id} ${name}`).toBeGreaterThan(0)
      }
    }
  })

  it('同一 family 的明暗两套皮肤 token 名集合必须完全一致', () => {
    // 否则换肤会残留上一套皮肤的变量（如 dark 有而 light 没有的 token）
    const names = (skin) => Object.keys(skin.tokens).sort()
    expect(names(resolveSkin('legacy-dark'))).toEqual(names(resolveSkin('legacy-light')))
  })

  it('token 数量覆盖重构前的全部皮肤变量（59 个，不含 4 个运行时/效果变量）', () => {
    expect(Object.keys(resolveSkin('legacy-dark').tokens)).toHaveLength(59)
  })

  it('皮肤 id 唯一且非空', () => {
    const ids = BUILTIN_SKINS.map((s) => s.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const id of ids) expect(id).toBeTruthy()
  })
})

describe('allTokenNames', () => {
  it('返回全部皮肤 token 名的并集', () => {
    const names = allTokenNames()
    expect(names).toContain('--app-bg')
    expect(names).toContain('--app-engine-err-border')
    expect(new Set(names).size).toBe(names.length)
    expect(names).toHaveLength(59)
  })
})

describe('skinForMode', () => {
  it('同 family 内切换到目标明暗分支', () => {
    const dark = resolveSkin('legacy-dark')
    expect(skinForMode(dark, 'light').id).toBe('legacy-light')
    expect(skinForMode(resolveSkin('legacy-light'), 'dark').id).toBe('legacy-dark')
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
