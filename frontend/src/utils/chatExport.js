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

// ===== 联网搜索三态与状态文案（与后端 search_mode 契约对齐：off/strict/broad） =====

/** 联网模式选项（ChatPanel 选择器与状态文案共用一份取值） */
export const SEARCH_MODES = [
  { value: 'off', label: '关闭' },
  { value: 'strict', label: '严格依据材料' },
  { value: 'broad', label: '广泛收集' },
]

/** 搜索后端显示名（provider key 来自 SSE search 事件） */
export function searchProviderName(provider) {
  return { ollama: 'Ollama', tavily: 'Tavily', firecrawl: 'Firecrawl', ddgs: 'DuckDuckGo' }[provider] || '联网'
}

/**
 * 联网检索状态气泡文案。
 * @param {null|{status:'searching'|'ok'|'failed', mode?:string, provider?:string, count?:number, message?:string}} state
 */
export function searchStateLabel(state) {
  if (!state) return ''
  if (state.status === 'searching') {
    return state.mode === 'broad' ? '正在广泛检索资料…' : '正在联网检索…'
  }
  if (state.status === 'ok') {
    const verb = state.mode === 'broad' ? '已广泛检索' : '已联网检索'
    return `${verb} · ${searchProviderName(state.provider)} · ${state.count} 条来源`
  }
  return `联网检索失败：${state.message}（已改用模型自身知识回答）`
}

/**
 * 上下文精简提示文案（SSE `context` 事件）。
 * 不是错误：模型这次看不到被省略的旧对话，用户得知道，否则会以为"它忘了"。
 * @param {null|{omitted_turns?:number, omitted_chars?:number, truncated_messages?:number, budget?:number}} info
 */
export function contextTrimLabel(info) {
  if (!info) return ''
  const turns = info.omitted_turns || 0
  const truncated = info.truncated_messages || 0
  if (!turns && !truncated) return ''
  const budget = info.budget ? `${Math.round(info.budget / 1000)}k 字符` : ''
  const parts = []
  if (turns) parts.push(`已省略较早的 ${turns} 轮对话`)
  if (truncated) parts.push(`${truncated} 条过长消息已精简`)
  const reason = budget ? `（超出上下文预算 ${budget}）` : ''
  return `${parts.join('、')}${reason}，仅最新轮次完整参与提问`
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
