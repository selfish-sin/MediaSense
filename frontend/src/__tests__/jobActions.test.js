import { describe, expect, it } from 'vitest'
import {
  buildRerunRoute,
  buildRestoreRoute,
  getJobActionModel,
} from '../utils/jobActions'
import { jobStatusLabel, jobStatusTagType } from '../utils/jobStatus'

describe('job action model', () => {
  it('restores active tasks and preview tasks with distinct labels', () => {
    expect(getJobActionModel('downloading')).toMatchObject({
      primary: 'restore', primaryLabel: '查看进度', canCancel: true,
    })
    expect(getJobActionModel('preview')).toMatchObject({
      primary: 'restore', primaryLabel: '继续确认', canCancel: true,
    })
  })

  it('offers result and rerun for completed tasks', () => {
    expect(getJobActionModel('completed')).toEqual({
      primary: 'result',
      primaryLabel: '查看结果',
      secondary: 'rerun',
      secondaryLabel: '重新处理',
    })
  })

  it('offers resume and force retry for failed or cancelled tasks', () => {
    expect(getJobActionModel('failed')).toMatchObject({ primary: 'resume', secondary: 'forceRetry' })
    expect(getJobActionModel('cancelled')).toMatchObject({ primary: 'resume', secondary: 'forceRetry' })
  })

  it('builds unambiguous restore and rerun routes', () => {
    expect(buildRestoreRoute('job-1')).toEqual({ path: '/', query: { restore: 'job-1' } })
    expect(buildRerunRoute('job-2')).toEqual({ path: '/', query: { rerun: 'job-2' } })
  })
})

describe('job status labels', () => {
  it('uses unified Chinese labels across the app', () => {
    expect(jobStatusLabel('pending')).toBe('排队中')
    expect(jobStatusLabel('summarizing')).toBe('AI处理中')
    expect(jobStatusLabel('preview')).toBe('待确认')
    expect(jobStatusTagType('completed')).toBe('success')
    expect(jobStatusTagType('failed')).toBe('danger')
  })
})
