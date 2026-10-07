import { describe, it, expect } from 'vitest'
import { buildChatTurns } from '../utils/chatTurns'

/** 造一行 job_chats（只保留归位逻辑关心的字段） */
const row = (id, role, extra = {}) => ({
  id,
  role,
  content: `${role}-${id}`,
  variant_group: '',
  is_active: 1,
  ...extra,
})

describe('buildChatTurns —— 追问消息按轮归位', () => {
  it('基本结构：提问 + 生效回答成对出现，回答带 variants', () => {
    const rows = [row(1, 'user'), row(2, 'assistant')]
    const out = buildChatTurns(rows)
    expect(out.map((m) => [m.id, m.role])).toEqual([[1, 'user'], [2, 'assistant']])
    expect(out[1].variants.map((v) => v.id)).toEqual([2])
    expect(out[0].variants).toEqual([])
  })

  it('★ 中间轮的新变体 id 在会话末尾 → 仍归位到该轮（不跑位到最后一轮之后）', () => {
    const rows = [
      row(1, 'user'),
      row(2, 'assistant', { variant_group: 'g1', is_active: 0 }),
      row(3, 'assistant', { variant_group: 'g1', is_active: 0 }),
      row(4, 'user'),
      row(5, 'assistant'),
      row(6, 'user'),
      row(7, 'assistant'),
      row(8, 'assistant', { variant_group: 'g1', is_active: 1 }), // 重生成第 1 轮的新版本
    ]
    const out = buildChatTurns(rows)
    expect(out.map((m) => m.id)).toEqual([1, 8, 4, 5, 6, 7])
    // 新版本显示在第 1 轮位置，且带完整版本列表（旧版本供 ‹N/M› 切换）
    expect(out[1].variants.map((v) => v.id)).toEqual([2, 3, 8])
    expect(out[3].id).toBe(5) // 后续轮次不受影响
  })

  it('组内无 is_active 行（双失败边缘）→ 兜底展示组内最新一条', () => {
    const rows = [
      row(1, 'user'),
      row(2, 'assistant', { variant_group: 'g1', is_active: 0 }),
      row(3, 'assistant', { variant_group: 'g1', is_active: 0 }),
    ]
    const out = buildChatTurns(rows)
    expect(out[1].id).toBe(3)
    expect(out[1].variants.map((v) => v.id)).toEqual([2, 3])
  })

  it('多个组各自归位（第 1、2 轮都有变体组，且新版本都在末尾）', () => {
    const rows = [
      row(1, 'user'),
      row(2, 'assistant', { variant_group: 'g1', is_active: 0 }),
      row(3, 'user'),
      row(4, 'assistant', { variant_group: 'g2', is_active: 0 }),
      row(5, 'assistant', { variant_group: 'g1', is_active: 1 }),
      row(6, 'assistant', { variant_group: 'g2', is_active: 1 }),
    ]
    const out = buildChatTurns(rows)
    expect(out.map((m) => m.id)).toEqual([1, 5, 3, 6])
    expect(out[1].variants.map((v) => v.id)).toEqual([2, 5])
    expect(out[3].variants.map((v) => v.id)).toEqual([4, 6])
  })

  it('无前置提问的孤立回答 → 追加在末尾（不静默丢数据）', () => {
    const rows = [row(1, 'assistant'), row(2, 'user'), row(3, 'assistant')]
    const out = buildChatTurns(rows)
    expect(out.map((m) => m.id)).toEqual([2, 3, 1])
  })

  it('空/非法入参不炸', () => {
    expect(buildChatTurns([])).toEqual([])
    expect(buildChatTurns(null)).toEqual([])
    expect(buildChatTurns(undefined)).toEqual([])
  })
})
