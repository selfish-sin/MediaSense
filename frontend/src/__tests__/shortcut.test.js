import { describe, it, expect } from 'vitest'
import { isSubmitShortcut, modifierLabel, shortcutHint } from '../utils/shortcut'

describe('shortcut', () => {
  describe('isSubmitShortcut', () => {
    it('Ctrl + Enter 命中', () => {
      expect(isSubmitShortcut({ ctrlKey: true, key: 'Enter' })).toBe(true)
    })

    it('⌘ + Enter 命中（macOS）', () => {
      expect(isSubmitShortcut({ metaKey: true, key: 'Enter' })).toBe(true)
    })

    it('小键盘 Enter 命中', () => {
      expect(isSubmitShortcut({ ctrlKey: true, key: 'Enter', code: 'NumpadEnter' })).toBe(true)
      expect(isSubmitShortcut({ ctrlKey: true, keyCode: 13 })).toBe(true)
    })

    it('裸 Enter / 其他修饰键组合不命中', () => {
      expect(isSubmitShortcut({ key: 'Enter' })).toBe(false)
      expect(isSubmitShortcut({ ctrlKey: true, key: 'a' })).toBe(false)
      expect(isSubmitShortcut({ shiftKey: true, key: 'Enter' })).toBe(false)
    })

    it('空事件不命中（不抛错）', () => {
      expect(isSubmitShortcut(null)).toBe(false)
      expect(isSubmitShortcut(undefined)).toBe(false)
      expect(isSubmitShortcut({})).toBe(false)
    })
  })

  describe('modifierLabel / shortcutHint', () => {
    it('macOS 平台用 ⌘，其余用 Ctrl', () => {
      expect(modifierLabel('MacIntel')).toBe('⌘')
      expect(modifierLabel('Win32')).toBe('Ctrl')
      expect(modifierLabel('')).toBe('Ctrl')
    })

    it('提示文案含修饰键与动作', () => {
      expect(shortcutHint('Win32')).toBe('Ctrl + Enter 快速开始')
      expect(shortcutHint('MacIntel')).toBe('⌘ + Enter 快速开始')
    })
  })
})
