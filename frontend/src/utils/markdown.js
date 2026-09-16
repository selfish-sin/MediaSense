import MarkdownIt from 'markdown-it'

// 创建 markdown-it 实例
const md = new MarkdownIt({
  // AI 输出、转录文本和用户编辑内容都可能包含任意字符串，禁止原始 HTML。
  html: false,
  linkify: true,    // 自动识别链接
  typographer: true, // 排版优化
  breaks: true,     // 换行转 <br>
})

const escapeHtml = (text) => md.utils.escapeHtml(text || '')

/**
 * 代码块渲染：包一层带「复制」按钮的容器。
 *
 * 按钮**不带任何 onClick**——HTML 是经 v-html 注入的，Vue 事件绑不上去；
 * 若在 ChatPanel / ResultView / HomeResultPanel 各接一遍逻辑必然漂移，
 * 漏接的那个页面就是个假按钮。故统一由 utils/codeCopy.js 在 document 上挂一次
 * 事件委托（data-md-code-copy），所有 markdown 渲染处自动可用。
 */
md.renderer.rules.fence = (tokens, idx) => {
  const token = tokens[idx]
  const lang = ((token.info || '').trim().split(/\s+/)[0]) || ''
  const langAttr = lang ? ` class="language-${escapeHtml(lang)}"` : ''
  return (
    '<div class="md-code-block">' +
      '<div class="md-code-head">' +
        `<span class="md-code-lang">${escapeHtml(lang || 'text')}</span>` +
        '<button type="button" class="md-code-copy" data-md-code-copy>复制</button>' +
      '</div>' +
      `<pre class="md-code-pre"><code${langAttr}>${escapeHtml(token.content)}</code></pre>` +
    '</div>'
  )
}

// 渲染 Markdown
export function renderMarkdown(text) {
  if (!text) return ''
  return md.render(text)
}

// 渲染纯文本（转义HTML）
export function renderPlainText(text) {
  if (!text) return ''
  return md.renderInline(text)
}

export default md
