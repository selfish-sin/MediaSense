/**
 * 追问对话的复制/导出格式化（纯函数，单测见 src/__tests__/chatExport.test.js）
 *
 * 放这里而不是写在 ChatPanel.vue 里：格式化是可单测的纯逻辑，
 * 组件只负责调 copyText()/拿 clipboard 反馈。
 */

const TIME_FMT = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })

/**
 * 解析消息时间。
 * ★ SQLite CURRENT_TIMESTAMP 落库为 UTC 的 "YYYY-MM-DD HH:MM:SS"（**无时区标记**）：
 *   直接 new Date() 会按本地时区解析，东八区会整整差 8 小时，故必须补 Z 当 UTC 读。
 * @returns {Date|null} 解析失败（含空值）返回 null
 */
export function parseChatTime(raw) {
  if (!raw) return null
  const naive = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?$/
  if (typeof raw === 'string' && naive.test(raw)) {
    const utc = new Date(`${raw.replace(' ', 'T')}Z`)
    return Number.isNaN(utc.getTime()) ? null : utc
  }
  const d = new Date(raw)
  return Number.isNaN(d.getTime()) ? null : d
}

/** 消息时间戳文案（HH:mm）；无时间返回空串，由调用方决定是否渲染 */
export function formatChatTime(raw) {
  const d = parseChatTime(raw)
  return d ? TIME_FMT.format(d) : ''
}

function sourceLine(source, index) {
  const title = source?.title || source?.url || '来源'
  const url = source?.url ? ` — ${source.url}` : ''
  return `${index + 1}. ${title}${url}`
}

/** AI 正文（+ 可选编号参考来源），单条复制与整对话导出共用同一口径 */
export function buildAiText(msg, withSources = false) {
  if (!msg) return ''
  const lines = [msg.content || '']
  if (withSources && Array.isArray(msg.sources) && msg.sources.length) {
    lines.push('', '## 参考来源', '')
    msg.sources.forEach((s, i) => lines.push(sourceLine(s, i)))
  }
  return lines.join('\n').trim()
}

/**
 * 整个对话 → Markdown：「### 你 / ### AI」分节，
 * AI 回答保留原文 Markdown，联网来源作为「**参考来源**」附在该条末尾。
 */
export function buildConversationMarkdown(messages, title = '追问对话', exportedAt = new Date()) {
  const lines = [`# ${title}`, '', `> 导出时间：${exportedAt.toLocaleString('zh-CN')}`, '']
  for (const m of messages || []) {
    if (!m) continue
    if (m.role === 'user') {
      lines.push('### 你', '', m.content || '', '')
      continue
    }
    lines.push('### AI', '', m.content || '(空回答)', '')
    if (Array.isArray(m.sources) && m.sources.length) {
      lines.push('**参考来源**', '')
      m.sources.forEach((s, i) => lines.push(sourceLine(s, i)))
      lines.push('')
    }
  }
  return `${lines.join('\n').trim()}\n`
}
