/**
 * 剪贴板工具：navigator.clipboard 优先，非 HTTPS/旧浏览器回退 execCommand
 */
import { ElMessage } from 'element-plus'

export async function copyText(text) {
  if (!text) {
    ElMessage.warning('没有可复制的内容')
    return false
  }
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(`已复制 ${text.length} 字符到剪贴板`)
    return true
  } catch {
    // fallback 兼容旧浏览器/非 HTTPS
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.left = '-9999px'
    document.body.appendChild(ta)
    ta.select()
    try {
      document.execCommand('copy')
      ElMessage.success(`已复制 ${text.length} 字符到剪贴板`)
      return true
    } catch {
      ElMessage.error('复制失败，请手动选择文本')
      return false
    } finally {
      document.body.removeChild(ta)
    }
  }
}
