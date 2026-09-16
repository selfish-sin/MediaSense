/**
 * Markdown 代码块「复制」按钮的行为——全局事件委托单点
 *
 * 背景：代码块由 markdown-it 渲染成 HTML 后经 v-html 注入，Vue 事件绑不到按钮上。
 * 若在 ChatPanel / ResultView / HomeResultPanel 各接一遍，逻辑必然漂移，
 * 且漏接的页面按钮就是个死按钮（本项目已为此踩过坑）。故在 document 上挂一次监听：
 * 任何渲染了 markdown 的地方（追问、结果区、工作台结果卡…）自动生效，无需组件配合。
 */
import { copyText } from './clipboard'

const RESET_MS = 1500

let installed = false

function handleClick(event) {
  const btn = event.target.closest?.('[data-md-code-copy]')
  if (!btn) return
  const block = btn.closest('.md-code-block')
  const code = block?.querySelector('code')?.textContent
  if (!code) return

  copyText(code).then((ok) => {
    if (!ok) return
    btn.textContent = '已复制'
    btn.classList.add('is-copied')
    clearTimeout(btn._resetTimer)
    btn._resetTimer = setTimeout(() => {
      btn.textContent = '复制'
      btn.classList.remove('is-copied')
      btn._resetTimer = null
    }, RESET_MS)
  })
}

/** 在应用启动时调用一次（幂等） */
export function installMarkdownCodeCopy() {
  if (installed) return
  document.addEventListener('click', handleClick)
  installed = true
}

/** 测试用：移除监听 */
export function uninstallMarkdownCodeCopy() {
  document.removeEventListener('click', handleClick)
  installed = false
}
