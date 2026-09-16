import { describe, it, expect } from 'vitest'
import {
  parseChatTime,
  formatChatTime,
  buildAiText,
  buildConversationMarkdown,
} from '../utils/chatExport'

describe('parseChatTime', () => {
  it('treats SQLite naive timestamp as UTC', () => {
    // ★ 回归锚点：SQLite CURRENT_TIMESTAMP 是 UTC 且无时区标记。
    //   按本地时区解析会整整差一个偏移（东八区 +8h），必须补 Z 当 UTC 读。
    const d = parseChatTime('2026-09-17 03:20:00')
    expect(d).toBeInstanceOf(Date)
    expect(d.toISOString()).toBe('2026-09-17T03:20:00.000Z')
  })

  it('keeps explicit ISO/UTC values as-is', () => {
    expect(parseChatTime('2026-09-17T03:20:00Z').toISOString()).toBe('2026-09-17T03:20:00.000Z')
  })

  it('returns null for empty or garbage input', () => {
    expect(parseChatTime('')).toBeNull()
    expect(parseChatTime(null)).toBeNull()
    expect(parseChatTime('not-a-time')).toBeNull()
  })

  it('formats as HH:mm', () => {
    expect(formatChatTime('2026-09-17 03:20:00')).toBe('11:20') // UTC+8
    expect(formatChatTime('')).toBe('')
  })
})

describe('buildAiText', () => {
  const msg = {
    content: '正文内容',
    sources: [
      { title: '来源A', url: 'https://a.com' },
      { title: '', url: 'https://b.com' },
    ],
  }

  it('returns body only by default', () => {
    expect(buildAiText(msg)).toBe('正文内容')
  })

  it('appends numbered sources when asked', () => {
    const text = buildAiText(msg, true)
    expect(text).toContain('## 参考来源')
    expect(text).toContain('1. 来源A — https://a.com')
    expect(text).toContain('2. https://b.com')
  })

  it('handles empty message', () => {
    expect(buildAiText(null)).toBe('')
    expect(buildAiText({ content: '' })).toBe('')
  })
})

describe('buildConversationMarkdown', () => {
  const messages = [
    { role: 'user', content: '问题一' },
    {
      role: 'assistant',
      content: '回答一',
      sources: [{ title: '来源A', url: 'https://a.com' }],
    },
    { role: 'user', content: '问题二' },
    { role: 'assistant', content: '' },
  ]

  it('renders 你/AI sections with sources and placeholder for empty answers', () => {
    const md = buildConversationMarkdown(messages, '会话标题', new Date('2026-09-17T03:20:00Z'))
    expect(md.startsWith('# 会话标题')).toBe(true)
    expect(md).toContain('### 你')
    expect(md).toContain('问题一')
    expect(md).toContain('### AI')
    expect(md).toContain('回答一')
    expect(md).toContain('**参考来源**')
    expect(md).toContain('1. 来源A — https://a.com')
    expect(md).toContain('(空回答)')
  })

  it('keeps 你/AI order and covers empty conversation', () => {
    expect(buildConversationMarkdown(messages).indexOf('问题二')).toBeGreaterThan(
      buildConversationMarkdown(messages).indexOf('回答一'),
    )
    expect(buildConversationMarkdown([], '空')).toContain('# 空')
  })
})
