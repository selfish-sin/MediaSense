/**
 * 图片预处理（背景图上传用）。
 *
 * ★ 背景图是以 dataURL 存进 localStorage 的（themeStore BG_KEY），而浏览器
 *   localStorage 配额只有 ~5MB（按 UTF-16 字符计），base64 又比原图大约 1.37 倍：
 *   一张 4MB 的图 → dataURL ≈ 5.5M 字符 → setItem 直接 QuotaExceededError，
 *   且 deep watch 每次拖动滑块都要重新 stringify 几 MB 字符串（同步阻塞主线程 → 界面卡）。
 *   所以上传时必须先压到能安全持久化的体积。
 */

const SMALL_FILE_LIMIT = 600 * 1024 // 小于此值直接用原图，避免有损压缩与透明丢失

/** File → dataURL */
export function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = () => reject(new Error('图片读取失败'))
    reader.readAsDataURL(file)
  })
}

/** dataURL → 已解码的 <img>（拿真实尺寸用） */
function loadImageElement(url) {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error('图片无法解码（格式可能不受浏览器支持）'))
    img.src = url
  })
}

/**
 * 把上传的图片压成可安全持久化的 dataURL。
 * - 小图（≤600KB）原样返回，保留透明与原始编码
 * - 大图按最长边缩放后重编码为 JPEG（背景图不需要透明），先铺深色底防透明区变黑
 * - 压缩结果没变小则退回原图
 * @returns {Promise<{dataUrl: string, compressed: boolean, width?: number, height?: number}>}
 */
export async function prepareBackgroundImage(file, { maxEdge = 1920, quality = 0.85 } = {}) {
  const rawUrl = await readAsDataUrl(file)
  if (file.size <= SMALL_FILE_LIMIT) {
    return { dataUrl: rawUrl, compressed: false }
  }

  const img = await loadImageElement(rawUrl)
  const w = img.naturalWidth || img.width
  const h = img.naturalHeight || img.height
  if (!w || !h) throw new Error('无法读取图片尺寸（可能是无固有尺寸的 SVG）')

  const scale = Math.min(1, maxEdge / Math.max(w, h))
  const cw = Math.max(1, Math.round(w * scale))
  const ch = Math.max(1, Math.round(h * scale))
  const canvas = document.createElement('canvas')
  canvas.width = cw
  canvas.height = ch
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('无法创建画布（浏览器不支持 canvas 2d）')
  ctx.fillStyle = '#0b1220'
  ctx.fillRect(0, 0, cw, ch)
  ctx.drawImage(img, 0, 0, cw, ch)

  const out = canvas.toDataURL('image/jpeg', quality)
  if (!out || out.length >= rawUrl.length) {
    return { dataUrl: rawUrl, compressed: false }
  }
  return { dataUrl: out, compressed: true, width: cw, height: ch }
}
