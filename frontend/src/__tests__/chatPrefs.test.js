import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  loadChatPrefs,
  saveChatPrefs,
  CHAT_PREFS_KEY,
  DEFAULT_CHAT_PREFS,
  SEARCH_MODE_VALUES,
} from '../utils/chatPrefs'

/** 内存版 localStorage：jsdom 的实现在部分环境不可控，这里显式打桩保证可断言 */
let store

beforeEach(() => {
  store = new Map()
  vi.stubGlobal('localStorage', {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
    clear: () => store.clear(),
  })
  vi.spyOn(console, 'warn').mockImplementation(() => {})
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('loadChatPrefs', () => {
  it('无存储时返回默认 off / false', () => {
    expect(loadChatPrefs()).toEqual({ searchMode: 'off', showThinking: false })
  })

  it('读回已存的合法值', () => {
    store.set(CHAT_PREFS_KEY, JSON.stringify({ searchMode: 'strict', showThinking: true }))
    expect(loadChatPrefs()).toEqual({ searchMode: 'strict', showThinking: true })
  })

  it('损坏 JSON 回退默认并 warn（不静默吞）', () => {
    store.set(CHAT_PREFS_KEY, '{not json')
    expect(loadChatPrefs()).toEqual(DEFAULT_CHAT_PREFS)
    expect(console.warn).toHaveBeenCalled()
  })

  it('非法 searchMode 回退 off（后端只认 off|strict|broad）', () => {
    store.set(CHAT_PREFS_KEY, JSON.stringify({ searchMode: 'haha', showThinking: true }))
    const prefs = loadChatPrefs()
    expect(prefs.searchMode).toBe('off')
    expect(prefs.showThinking).toBe(true)
  })

  it('searchMode 大小写/空格变体也算非法', () => {
    for (const bad of ['STRICT', ' strict', 'broad ', '', null, 1]) {
      store.set(CHAT_PREFS_KEY, JSON.stringify({ searchMode: bad }))
      expect(loadChatPrefs().searchMode).toBe('off')
    }
  })

  it('showThinking 非布尔（字符串/数字）回退 false', () => {
    for (const bad of ['false', 'true', 0, 1, null]) {
      store.set(CHAT_PREFS_KEY, JSON.stringify({ showThinking: bad }))
      expect(loadChatPrefs().showThinking).toBe(false)
    }
  })

  it('缺字段的半截对象按默认补齐', () => {
    store.set(CHAT_PREFS_KEY, JSON.stringify({ searchMode: 'broad' }))
    expect(loadChatPrefs()).toEqual({ searchMode: 'broad', showThinking: false })
  })

  it('JSON 是数组/null 等非对象时回退默认', () => {
    for (const raw of ['[]', 'null', '"strict"', '123']) {
      store.set(CHAT_PREFS_KEY, raw)
      expect(loadChatPrefs()).toEqual(DEFAULT_CHAT_PREFS)
    }
  })

  it('localStorage 读取抛错（隐私模式）也回退默认', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('SecurityError') },
      setItem: () => { throw new Error('SecurityError') },
    })
    expect(loadChatPrefs()).toEqual(DEFAULT_CHAT_PREFS)
    expect(console.warn).toHaveBeenCalled()
  })
})

describe('saveChatPrefs', () => {
  it('写入后可读回（往返一致）', () => {
    expect(saveChatPrefs({ searchMode: 'broad', showThinking: true })).toBe(true)
    expect(loadChatPrefs()).toEqual({ searchMode: 'broad', showThinking: true })
  })

  it('写入前先归一化（非法值不会落盘）', () => {
    saveChatPrefs({ searchMode: 'nope', showThinking: 'yes' })
    expect(JSON.parse(store.get(CHAT_PREFS_KEY))).toEqual({ searchMode: 'off', showThinking: false })
  })

  it('写入失败返回 false 且 warn，不抛（不阻断追问）', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => null,
      setItem: () => { throw new Error('QuotaExceededError') },
    })
    expect(saveChatPrefs({ searchMode: 'strict' })).toBe(false)
    expect(console.warn).toHaveBeenCalled()
  })

  it('SEARCH_MODE_VALUES 与后端契约一致（off/strict/broad）', () => {
    expect(SEARCH_MODE_VALUES).toEqual(['off', 'strict', 'broad'])
  })
})