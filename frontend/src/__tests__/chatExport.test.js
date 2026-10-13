import { describe, it, expect } from 'vitest'
import {
  parseChatTime,
  formatChatTime,
  buildAiText,
  buildConversationMarkdown,
  SEARCH_MODES,
  searchProviderName,
  searchStateLabel,
  contextTrimLabel,
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
    expect(formatChatTime('2026-09-17 03:20:00')).toBe('11:20') // 套件固定 TZ=Asia/Shanghai（vitest.config.js）
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

describe('searchStateLabel（联网三态文案）', () => {
  it('SEARCH_MODES 与后端 search_mode 契约对齐', () => {
    expect(SEARCH_MODES.map((m) => m.value)).toEqual(['off', 'strict', 'broad'])
  })

  it('provider 显示名覆盖四个引擎，未知回落「联网」', () => {
    expect(searchProviderName('ollama')).toBe('Ollama')
    expect(searchProviderName('tavily')).toBe('Tavily')
    expect(searchProviderName('firecrawl')).toBe('Firecrawl')
    expect(searchProviderName('ddgs')).toBe('DuckDuckGo')
    expect(searchProviderName('')).toBe('联网')
  })

  it('searching/ok 文案区分严格与广泛模式', () => {
    expect(searchStateLabel({ status: 'searching', mode: 'strict' })).toContain('正在联网检索')
    expect(searchStateLabel({ status: 'searching', mode: 'broad' })).toContain('正在广泛检索')
    expect(searchStateLabel({ status: 'ok', provider: 'ollama', count: 5, mode: 'strict' })).toBe(
      '已联网检索 · Ollama · 5 条来源',
    )
    expect(searchStateLabel({ status: 'ok', provider: 'ollama', count: 9, mode: 'broad' })).toBe(
      '已广泛检索 · Ollama · 9 条来源',
    )
  })

  it('failed 文案透出原因并说明降级', () => {
    expect(searchStateLabel({ status: 'failed', message: '未配置 Ollama 搜索 Key' })).toContain(
      '联网检索失败：未配置 Ollama 搜索 Key（已改用模型自身知识回答）',
    )
    expect(searchStateLabel(null)).toBe('')
  })
})

describe('contextTrimLabel（上下文精简提示行）', () => {
  it('无精简信息不显示（null/全零都返回空串）', () => {
    expect(contextTrimLabel(null)).toBe('')
    expect(contextTrimLabel({ budget: 24000, omitted_turns: 0, truncated_messages: 0 })).toBe('')
  })

  it('省略轮次带上预算与「仅最新轮完整」说明', () => {
    const label = contextTrimLabel({ budget: 24000, omitted_turns: 3, truncated_messages: 0 })
    expect(label).toContain('已省略较早的 3 轮对话')
    expect(label).toContain('24k 字符')
    expect(label).toContain('仅最新轮次完整参与提问')
  })

  it('单条截断与轮次省略可同时出现', () => {
    const label = contextTrimLabel({ budget: 8000, omitted_turns: 1, truncated_messages: 2 })
    expect(label).toContain('已省略较早的 1 轮对话')
    expect(label).toContain('2 条过长消息已精简')
  })

  it('预算字段缺失时不编造数字', () => {
    const label = contextTrimLabel({ omitted_turns: 1 })
    expect(label).toContain('已省略较早的 1 轮对话')
    expect(label).not.toContain('字符')
  })
})
