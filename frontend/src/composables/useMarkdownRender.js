/**
 * Markdown 渲染节流与缓存
 *
 * 流式输出场景（AI token 高频追加）下直接在模板里调 renderMarkdown 会让
 * 每个新 token 触发一次 O(n) 全量重渲染；这里统一收口成两种模式：
 *  - useRafMarkdown：单文本，rAF 每帧最多渲染一次（工作台结果区）
 *  - useMarkdownListCache：消息列表，逐条缓存 + 变化条目按帧节流（追问聊天）
 */
import { ref, reactive, watch, onUnmounted } from 'vue'
import { renderMarkdown } from '../utils/markdown'

/**
 * 单文本 rAF 节流渲染
 * @param {() => string} source 文本来源（getter 或 ref 的 .value 访问器）
 * @returns {{ html: import('vue').Ref<string> }}
 */
export function useRafMarkdown(source) {
  const html = ref('')
  let rafId = null

  function schedule() {
    if (rafId !== null) return // 已有等待中的渲染
    rafId = requestAnimationFrame(() => {
      rafId = null
      html.value = renderMarkdown(source() || '')
    })
  }

  watch(source, schedule, { immediate: true })
  onUnmounted(() => {
    if (rafId !== null) {
      cancelAnimationFrame(rafId)
      rafId = null
    }
  })

  return { html }
}

/**
 * 消息列表渲染缓存：内容未变的条目直接命中，变化条目按帧合并重渲染
 * @param {() => Array} items 列表来源（getter）
 * @param {(item: any) => string} getText 取条目文本
 * @returns {{ htmlOf: (item: any) => string }} 模板里 v-html="htmlOf(msg)"
 */
export function useMarkdownListCache(items, getText) {
  // item 对象引用 -> { text, html }；reactive 化让模板读取被依赖追踪
  const cache = reactive(new Map())
  let rafId = null

  function renderPass() {
    rafId = null
    const live = new Set(items() || [])
    for (const [item, entry] of cache) {
      if (!live.has(item)) {
        cache.delete(item)
        continue
      }
      const text = getText(item) || ''
      if (entry.text !== text) {
        entry.text = text
        entry.html = renderMarkdown(text)
      }
    }
  }

  function schedule() {
    if (rafId !== null) return
    rafId = requestAnimationFrame(renderPass)
  }

  function htmlOf(item) {
    const text = getText(item) || ''
    const hit = cache.get(item)
    if (!hit) {
      // 首次出现：同步渲染，避免新消息闪一帧空白
      cache.set(item, { text, html: renderMarkdown(text) })
      return cache.get(item).html
    }
    if (hit.text !== text) schedule() // 流式更新：帧内节流
    return hit.html
  }

  watch(items, schedule)
  onUnmounted(() => {
    if (rafId !== null) {
      cancelAnimationFrame(rafId)
      rafId = null
    }
  })

  return { htmlOf }
}
