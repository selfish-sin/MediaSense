// 与后端 backend/routers/jobs.py 的 MAX_UPLOAD_SIZE 保持一致：单文件 2GB 上限
export const MAX_UPLOAD_SIZE = 2 * 1024 ** 3

export function formatSizeGB(bytes) {
  return (bytes / 1024 ** 3).toFixed(2)
}

// 返回拒绝原因文案；null 表示允许上传
export function uploadRejectReason(name, size) {
  if (size > MAX_UPLOAD_SIZE) {
    return `文件「${name}」超过 2GB 上限（约 ${formatSizeGB(size)}GB），已拒绝上传`
  }
  return null
}
