import { describe, it, expect } from 'vitest'
import { failureLabel, failureTitle, NO_FAILURE_DETAIL } from '../utils/jobFailure'

describe('failureLabel', () => {
  it('非失败态返回空串（不占位渲染）', () => {
    expect(failureLabel({ status: 'completed', error: 'x' })).toBe('')
    expect(failureLabel({ status: 'pending' })).toBe('')
    expect(failureLabel(null)).toBe('')
  })

  it('返回失败原因原文（未超长时）', () => {
    expect(failureLabel({ status: 'failed', error: '下载失败：HTTP 412 风控' })).toBe('下载失败：HTTP 412 风控')
  })

  it('空/缺失/空白 error 回退到「无错误详情」', () => {
    expect(failureLabel({ status: 'failed' })).toBe(NO_FAILURE_DETAIL)
    expect(failureLabel({ status: 'failed', error: '' })).toBe(NO_FAILURE_DETAIL)
    expect(failureLabel({ status: 'failed', error: '   \n\t ' })).toBe(NO_FAILURE_DETAIL)
    expect(failureLabel({ status: 'failed', error: null })).toBe(NO_FAILURE_DETAIL)
  })

  it('超长时按 maxLen 截断并加省略号', () => {
    const err = 'x'.repeat(200)
    const label = failureLabel({ status: 'failed', error: err })
    expect(label).toHaveLength(61)
    expect(label.endsWith('…')).toBe(true)
  })

  it('maxLen 可自定义，非法值回退 60', () => {
    const err = 'y'.repeat(100)
    expect(failureLabel({ status: 'failed', error: err }, 10)).toHaveLength(11)
    expect(failureLabel({ status: 'failed', error: err }, 0)).toHaveLength(61)
    expect(failureLabel({ status: 'failed', error: err }, -5)).toHaveLength(61)
  })

  it('压平换行与多余空格（DB 里 error 常带缩进换行）', () => {
    const label = failureLabel({
      status: 'failed',
      error: 'Traceback (most recent call last):\n  File "x.py", line 1\n    boom',
    })
    expect(label).toBe('Traceback (most recent call last): File "x.py", line 1 boom')
  })

  it('nullish 不炸（非对象字段安全）', () => {
    expect(failureLabel({ status: 'failed', error: undefined })).toBe(NO_FAILURE_DETAIL)
  })
})

describe('failureTitle', () => {
  it('非失败态或无原因时给空串（tooltip 不显示空气泡）', () => {
    expect(failureTitle({ status: 'failed' })).toBe('')
    expect(failureTitle({ status: 'completed', error: 'x' })).toBe('')
    expect(failureTitle(null)).toBe('')
  })

  it('失败态给完整原文（不截断，供悬停查看）', () => {
    const err = 'z'.repeat(300)
    expect(failureTitle({ status: 'failed', error: err })).toHaveLength(300)
  })
})