import { describe, expect, it } from 'vitest'
import { isTerminalStatus, pickActiveJobId } from '../utils/batchProgress'

function job(id, status) {
  return { id, status }
}

describe('isTerminalStatus', () => {
  it('只有 completed/failed/cancelled 是终态', () => {
    expect(isTerminalStatus('completed')).toBe(true)
    expect(isTerminalStatus('failed')).toBe(true)
    expect(isTerminalStatus('cancelled')).toBe(true)
    expect(isTerminalStatus('pending')).toBe(false)
    expect(isTerminalStatus('downloading')).toBe(false)
    expect(isTerminalStatus('preview')).toBe(false)
    expect(isTerminalStatus('')).toBe(false)
  })
})

describe('pickActiveJobId', () => {
  it('全部 pending（批量刚提交）：取第一个', () => {
    const jobs = [job('a', 'pending'), job('b', 'pending'), job('c', 'pending')]
    expect(pickActiveJobId(jobs, null)).toBe('a')
  })

  it('当前任务仍在跑：保持不变（不因轮询刷新而闪烁）', () => {
    const jobs = [job('a', 'completed'), job('b', 'transcribing'), job('c', 'pending')]
    expect(pickActiveJobId(jobs, 'b')).toBe('b')
  })

  it('当前任务完成：顺延到下一个未终态任务（自动切换）', () => {
    const jobs = [job('a', 'completed'), job('b', 'pending'), job('c', 'pending')]
    expect(pickActiveJobId(jobs, 'a')).toBe('b')
  })

  it('跳过已失败/取消的任务', () => {
    const jobs = [job('a', 'failed'), job('b', 'cancelled'), job('c', 'pending')]
    expect(pickActiveJobId(jobs, 'a')).toBe('c')
  })

  it('全部终态：保留当前任务，让最后一个任务的进度与日志留屏', () => {
    const jobs = [job('a', 'completed'), job('b', 'failed')]
    expect(pickActiveJobId(jobs, 'b')).toBe('b')
  })

  it('批次内找不到当前 id 且全部终态：返回 null（调用方隐藏详情块）', () => {
    const jobs = [job('a', 'completed')]
    expect(pickActiveJobId(jobs, 'z')).toBe(null)
  })

  it('批次内找不到当前 id 但有未终态任务：取第一个未终态', () => {
    const jobs = [job('a', 'completed'), job('b', 'pending')]
    expect(pickActiveJobId(jobs, 'z')).toBe('b')
  })

  it('空 jobs / 非法入参：返回 null', () => {
    expect(pickActiveJobId([], 'a')).toBe(null)
    expect(pickActiveJobId(null, 'a')).toBe(null)
  })
})
