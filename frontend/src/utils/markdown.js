import MarkdownIt from 'markdown-it'

// 创建 markdown-it 实例
const md = new MarkdownIt({
  // AI 输出、转录文本和用户编辑内容都可能包含任意字符串，禁止原始 HTML。
  html: false,
  linkify: true,    // 自动识别链接
  typographer: true, // 排版优化
  breaks: true,     // 换行转 <br>
})

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
