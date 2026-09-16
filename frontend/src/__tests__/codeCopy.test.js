/**
 * Markdown 代码块「复制」按钮的全局委托测试
 *
 * 锁定契约：按钮本身没有 onClick（v-html 注入绑不上 Vue 事件），行为完全由
 * utils/codeCopy.js 的 document 委托提供——任何渲染 markdown 的页面都自动可用，
 * 漏接即死按钮（本项目踩过的坑，见 CONTEXT 2026-09-16 追问联网开关）。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { installMarkdownCodeCopy, uninstallMarkdownCodeCopy } from '../utils/codeCopy'
import { renderMarkdown } from '../utils/markdown'

function stubClipboard() {
  const writeText = vi.fn(() => Promise.resolve())
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  })
  return writeText
}

const flush = () => new Promise((r) => setTimeout(r, 0))

describe('markdown code copy delegation', () => {
  let writeText

  beforeEach(() => {
    writeText = stubClipboard()
    document.body.innerHTML = renderMarkdown('```python\nprint(1)\n```')
    installMarkdownCodeCopy()
  })

  afterEach(() => {
    uninstallMarkdownCodeCopy()
    document.body.innerHTML = ''
    // 假定时器必须无条件还原：用例中途失败会跳过 useRealTimers()，污染后续用例
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('copies the code text when the button is clicked', async () => {
    const btn = document.querySelector('[data-md-code-copy]')
    btn.click()

    await flush()

    expect(writeText).toHaveBeenCalledTimes(1)
    expect(writeText.mock.calls[0][0]).toContain('print(1)')
    expect(btn.textContent).toBe('已复制')
    expect(btn.classList.contains('is-copied')).toBe(true)
  })

  it('resets the button label after a while', async () => {
    // ★ 假定时器必须在点击前开启：重置计时器是 click 时排的，晚了就挂在真实时间轴上推不动。
    //   用 advanceTimersByTimeAsync 推进（同时跑微任务 + 到期定时器），不要用 setTimeout 自等。
    vi.useFakeTimers()
    const btn = document.querySelector('[data-md-code-copy]')
    btn.click()

    await vi.advanceTimersByTimeAsync(0)
    expect(btn.textContent).toBe('已复制')

    await vi.advanceTimersByTimeAsync(1600)
    expect(btn.textContent).toBe('复制')
    expect(btn.classList.contains('is-copied')).toBe(false)
  })

  it('ignores clicks outside a code block', async () => {
    document.body.innerHTML = '<div><button id="other">hi</button></div>'
    document.getElementById('other').click()

    await flush()

    expect(writeText).not.toHaveBeenCalled()
  })
})
