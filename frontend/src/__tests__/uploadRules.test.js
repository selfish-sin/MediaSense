import { describe, it, expect } from 'vitest'
import { MAX_UPLOAD_SIZE, formatSizeGB, uploadRejectReason } from '../utils/uploadRules'

describe('uploadRules', () => {
  it('MAX_UPLOAD_SIZE 为 8GB', () => {
    expect(MAX_UPLOAD_SIZE).toBe(8 * 1024 ** 3)
  })

  it('不超过 8GB 的文件允许上传', () => {
    expect(uploadRejectReason('video.mp4', 100)).toBeNull()
    expect(uploadRejectReason('video.mp4', MAX_UPLOAD_SIZE)).toBeNull()
  })

  it('超过 8GB 的文件拒绝并给出明确提示', () => {
    const reason = uploadRejectReason('movie.mp4', MAX_UPLOAD_SIZE + 1)
    expect(reason).toContain('movie.mp4')
    expect(reason).toContain('超过 8GB 上限')
    expect(reason).toContain('8.00GB')
  })

  it('formatSizeGB 按二进制 GB 换算', () => {
    expect(formatSizeGB(8 * 1024 ** 3)).toBe('8.00')
    expect(formatSizeGB(2306867200)).toBe('2.15')
  })
})
