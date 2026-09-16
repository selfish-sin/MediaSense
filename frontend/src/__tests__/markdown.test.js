import { describe, it, expect } from 'vitest'
import { renderMarkdown, renderPlainText } from '../utils/markdown'

describe('markdown utils', () => {
  it('renderMarkdown returns empty for falsy input', () => {
    expect(renderMarkdown('')).toBe('')
    expect(renderMarkdown(null)).toBe('')
    expect(renderMarkdown(undefined)).toBe('')
  })

  it('renderMarkdown renders headings', () => {
    const html = renderMarkdown('# Hello')
    expect(html).toContain('<h1>')
    expect(html).toContain('Hello')
  })

  it('renderMarkdown renders lists', () => {
    const html = renderMarkdown('- item1\n- item2')
    expect(html).toContain('<ul>')
    expect(html).toContain('<li>')
    expect(html).toContain('item1')
  })

  it('renderMarkdown renders bold', () => {
    const html = renderMarkdown('**bold text**')
    expect(html).toContain('<strong>')
  })

  it('renderMarkdown renders code blocks', () => {
    const html = renderMarkdown('```\ncode\n```')
    expect(html).toContain('<code>')
  })

  // ★ 代码块带「复制」按钮容器（utils/codeCopy.js 全局委托消费 data-md-code-copy）
  it('renderMarkdown wraps fenced code with copy button', () => {
    const html = renderMarkdown('```python\nprint(1)\n```')
    expect(html).toContain('data-md-code-copy')
    expect(html).toContain('md-code-block')
    expect(html).toContain('<span class="md-code-lang">python</span>')
    expect(html).toContain('language-python')
    expect(html).toContain('print(1)')
  })

  it('renderMarkdown escapes html inside fenced code', () => {
    const html = renderMarkdown('```\n<script>alert(1)</script>\n```')
    expect(html).not.toContain('<script>')
    expect(html).toContain('&lt;script&gt;')
  })

  it('renderMarkdown defaults code lang label to text', () => {
    const html = renderMarkdown('```\nplain\n```')
    expect(html).toContain('<span class="md-code-lang">text</span>')
  })

  it('renderMarkdown linkifies URLs', () => {
    const html = renderMarkdown('visit https://example.com')
    expect(html).toContain('href')
  })

  it('escapes raw HTML from untrusted result text', () => {
    const html = renderMarkdown('<img src=x onerror=alert(1)>')
    expect(html).not.toContain('<img')
    expect(html).toContain('&lt;img')
  })

  it('renderPlainText returns empty for falsy input', () => {
    expect(renderPlainText('')).toBe('')
    expect(renderPlainText(null)).toBe('')
  })

  it('renderPlainText renders inline markdown', () => {
    const html = renderPlainText('**bold**')
    expect(html).toContain('<strong>')
  })
})
