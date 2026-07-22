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

  it('renderMarkdown linkifies URLs', () => {
    const html = renderMarkdown('visit https://example.com')
    expect(html).toContain('href')
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
