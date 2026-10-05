/**
 * 图片预处理（背景图上传用）。
 *
 * ★ 背景图现在存 **IndexedDB 的 Blob**（见 utils/bgStore.js），不再往 localStorage
 *   塞 base64。历史教训：localStorage 配额只有 ~5MB（按 UTF-16 字符计），base64 又比原图
 *   大约 1.37 倍 —— 一张 4MB 的图 → dataURL ≈ 5.5M 字符 → setItem 直接 QuotaExceededError，
 *   且 deep watch 每次拖滑块都要重新 stringify 几 MB 字符串（同步阻塞主线程 → 界面卡）。
 *   → 所以这里产出的是 **Blob**（无膨胀），另配一张几百像素的 `thumbUrl` 小图
 *   （体积可安全进 localStorage，用作图库网格缩略图与首屏占位）。
 *
 * IndexedDB 不可用的降级路径才需要 dataURL，用 `blobToDataUrl()` 现转。
 */

const SMALL_FILE_LIMIT = 600 * 1024 // 小于此值直接用原图，避免有损压缩与透明丢失
/** 单张位图解码上限（像素数）：超过这个尺寸浏览器多半解不出来，先明确报错而不是卡住 */
const MAX_DECODE_PIXELS = 50_000_000
/** 压缩结果的安全线：超过就再压一轮（大图仍要控制体积，避免图库被一张图占满） */
const SAFE_BYTES = 3 * 1024 * 1024
/** 缩略图最长边与质量（图库网格 / 首屏占位用，几十 KB 量级） */
const THUMB_EDGE = 320
const THUMB_QUALITY = 0.7

/** File → dataURL（降级路径用） */
export function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = () => reject(new Error('图片读取失败'))
    reader.readAsDataURL(file)
  })
}

/** Blob → dataURL（仅在 IndexedDB 不可用、需要回落 localStorage 时使用）。 */
export function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = () => reject(new Error('图片转换失败'))
    reader.readAsDataURL(blob)
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

/** canvas → Blob（JPEG）。toBlob 失败（浏览器不支持重编码）时给出可读原因。 */
function canvasToBlob(canvas, quality) {
  return new Promise((resolve, reject) => {
    try {
      canvas.toBlob((blob) => {
        if (blob) resolve(blob)
        else reject(new Error('图片压缩失败（浏览器无法重编码该图片）'))
      }, 'image/jpeg', quality)
    } catch (err) {
      reject(err)
    }
  })
}

/** 把 img 按最长边 edge 缩放后画到 canvas 上（先铺深色底，防透明区变黑）。 */
function drawToCanvas(img, w, h, edge) {
  const scale = Math.min(1, edge / Math.max(w, h))
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
  return { canvas, width: cw, height: ch }
}

/**
 * 把上传的图片处理成可入库的资产。
 * - 小图（≤600KB）：`blob` 用原文件（保留透明与原始编码），不重编码
 * - 大图：按最长边缩放到 `maxEdge` 后重编码为 JPEG；仍超安全线则再压一轮
 * - 无论大小都生成 `thumbUrl` 小图（图库网格 + 首屏占位）
 * @returns {Promise<{blob: Blob, thumbUrl: string, width: number, height: number, bytes: number, compressed: boolean}>}
 */
export async function prepareBackgroundImage(file, { maxEdge = 1920, quality = 0.85 } = {}) {
  // ★ 大图解码走 objectURL：不先读成 dataURL（30MB 的图会先变成约 40MB 的 base64 中间字符串）
  const objUrl = URL.createObjectURL(file)
  let img
  try {
    img = await loadImageElement(objUrl)
  } catch (err) {
    URL.revokeObjectURL(objUrl)
    throw err
  }

  try {
    const w = img.naturalWidth || img.width
    const h = img.naturalHeight || img.height
    if (!w || !h) throw new Error('无法读取图片尺寸（可能是无固有尺寸的 SVG）')
    if (w * h > MAX_DECODE_PIXELS) {
      throw new Error(`图片尺寸过大（${w}×${h}），请先用工具缩到 8000×6000 以内`)
    }

    const thumb = drawToCanvas(img, w, h, THUMB_EDGE)
    const thumbUrl = thumb.canvas.toDataURL('image/jpeg', THUMB_QUALITY)
    if (!thumbUrl) throw new Error('缩略图生成失败（浏览器不支持 canvas 导出）')

    if (file.size <= SMALL_FILE_LIMIT) {
      return { blob: file, thumbUrl, width: w, height: h, bytes: file.size, compressed: false }
    }

    let best = drawToCanvas(img, w, h, maxEdge)
    let blob = await canvasToBlob(best.canvas, quality)
    // ★ 一轮不够小（噪点/高细节图）就再狠压一轮
    if (blob.size > SAFE_BYTES) {
      const harder = drawToCanvas(img, w, h, Math.min(1280, maxEdge))
      const harderBlob = await canvasToBlob(harder.canvas, 0.72)
      if (harderBlob.size < blob.size) {
        best = harder
        blob = harderBlob
      }
    }
    // 极端情况：小尺寸 PNG 转 JPEG 反而更大 → 退回原文件
    if (blob.size > file.size) {
      return { blob: file, thumbUrl, width: w, height: h, bytes: file.size, compressed: false }
    }
    return {
      blob,
      thumbUrl,
      width: best.width,
      height: best.height,
      bytes: blob.size,
      compressed: true,
    }
  } finally {
    URL.revokeObjectURL(objUrl)
  }
}
