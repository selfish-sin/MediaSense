import { onMounted, onUnmounted, ref } from 'vue'

/**
 * 跟踪某个元素的实际内容宽度（ResizeObserver）。
 *
 * 表格响应式列布局用它拿「容器真实宽度」——窗口宽度减侧栏、减内边距这种口算容易过时。
 * 元素不存在（v-if 未渲染）或环境没有 ResizeObserver（如 jsdom）时保留 fallback，
 * 不做假响应式。
 *
 * @param {import('vue').Ref<HTMLElement|null>} targetRef 目标元素 ref
 * @param {number} fallback 观测到之前的兜底宽度
 */
export function useContainerWidth(targetRef, fallback = 0) {
  const width = ref(fallback)
  let observer = null

  function measure() {
    const el = targetRef.value
    if (el && el.clientWidth > 0) width.value = el.clientWidth
  }

  onMounted(() => {
    measure()
    const el = targetRef.value
    if (!el || typeof ResizeObserver === 'undefined') return
    observer = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect?.width
      // 被 v-show 隐藏时宽度为 0，保留上一次有效值，避免布局来回翻档
      if (typeof w === 'number' && w > 0) width.value = w
    })
    observer.observe(el)
  })

  onUnmounted(() => {
    observer?.disconnect()
    observer = null
  })

  return width
}
