import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const apiMocks = vi.hoisted(() => ({ submit: vi.fn(), get: vi.fn() }))

vi.mock('../utils/api', () => ({
  jobsApi: apiMocks,
}))

import { useJobStore } from '../stores/jobStore'
import { ACTIVE_JOB_STATUSES } from '../utils/jobActions'

const STATE_KEY = 'yt_tool_job_state'
const LOGS_KEY = 'yt_tool_job_logs'

function storedState() {
  return JSON.parse(localStorage.getItem(STATE_KEY) || 'null')
}
function storedLogs() {
  return JSON.parse(localStorage.getItem(LOGS_KEY) || '{}')
}

describe('jobStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  describe('addLog / 日志持久化节流', () => {
    it('addLog 追加日志，超过 500 条截断保留最新', () => {
      const store = useJobStore()
      for (let i = 0; i < 505; i++) store.addLog(`msg-${i}`)
      expect(store.logs.length).toBe(500)
      expect(store.logs[0].msg).toBe('msg-5')
      expect(store.logs[499].msg).toBe('msg-504')
    })

    it('有 currentJobId 时按 1.5s trailing debounce 落盘', async () => {
      vi.useFakeTimers()
      const store = useJobStore()
      store.currentJobId = 'j1'
      store.addLog('a')
      store.addLog('b')
      expect(storedLogs()['j1']).toBeUndefined()  // 未到 1.5s 不写
      await vi.advanceTimersByTimeAsync(1500)
      expect(storedLogs()['j1'].map((l) => l.msg)).toEqual(['a', 'b'])
    })

    it('无 currentJobId 时不落盘', async () => {
      vi.useFakeTimers()
      const store = useJobStore()
      store.addLog('a')
      await vi.advanceTimersByTimeAsync(2000)
      expect(storedLogs()).toEqual({})
    })

    it('flushLogsNow 立即落盘并取消待写定时器（防尾部丢失）', async () => {
      vi.useFakeTimers()
      const store = useJobStore()
      store.currentJobId = 'j1'
      store.addLog('tail')
      store.flushLogsNow()
      expect(storedLogs()['j1'].map((l) => l.msg)).toEqual(['tail'])
      // 定时器已取消：继续推进时间不再重复写
      await vi.advanceTimersByTimeAsync(5000)
      expect(storedLogs()['j1'].length).toBe(1)
    })
  })

  describe('状态持久化 watch', () => {
    it('active 状态持久化 currentJobId；终态落盘日志并清除状态', async () => {
      vi.useFakeTimers()
      const store = useJobStore()
      store.currentJobId = 'j1'
      store.jobStatus = 'summarizing'
      await vi.advanceTimersByTimeAsync(0)
      expect(storedState()).toMatchObject({ currentJobId: 'j1', jobStatus: 'summarizing' })

      store.addLog('final line')
      store.jobStatus = 'completed'
      await vi.advanceTimersByTimeAsync(0)
      // 终态：日志立即落盘（不等 1.5s）
      expect(storedLogs()['j1'].map((l) => l.msg)).toEqual(['final line'])
      // 状态清除，避免下次刷新错误恢复
      expect(storedState()).toBeNull()
    })

    it('终态后 watch 不会把状态写回', async () => {
      vi.useFakeTimers()
      const store = useJobStore()
      store.currentJobId = 'j1'
      store.jobStatus = 'failed'
      await vi.advanceTimersByTimeAsync(0)
      expect(storedState()).toBeNull()
    })
  })

  describe('clearLogs / reset', () => {
    it('clearLogs 清空内存日志并删除该 job 的持久化日志', async () => {
      vi.useFakeTimers()
      const store = useJobStore()
      store.currentJobId = 'j1'
      store.addLog('x')
      await vi.advanceTimersByTimeAsync(1500)
      expect(storedLogs()['j1']).toHaveLength(1)

      store.clearLogs()
      expect(store.logs).toEqual([])
      expect(storedLogs()['j1']).toBeUndefined()
      // 定时器已取消：不会再复活写入
      await vi.advanceTimersByTimeAsync(5000)
      expect(storedLogs()['j1']).toBeUndefined()
    })

    it('reset 清空全部状态与存储', () => {
      const store = useJobStore()
      store.currentJobId = 'j1'
      store.jobStatus = 'summarizing'
      store.resultText = 'R'
      store.progress.step = 's1'
      store.reset()
      expect(store.currentJobId).toBeNull()
      expect(store.jobStatus).toBe('idle')
      expect(store.resultText).toBe('')
      expect(store.progress.step).toBe('')
      expect(store.isProcessing).toBe(false)
      expect(storedState()).toBeNull()
    })
  })

  describe('submitJob', () => {
    it('成功：设置任务状态并记录日志', async () => {
      apiMocks.submit.mockResolvedValueOnce({ job_id: 'j1' })
      const store = useJobStore()
      const res = await store.submitJob({ url: 'https://x' })
      expect(res.job_id).toBe('j1')
      expect(store.currentJobId).toBe('j1')
      expect(store.jobStatus).toBe('pending')
      expect(store.isProcessing).toBe(true)
      expect(store.logs.at(-1).msg).toBe('任务已提交: j1')
    })

    it('同 URL 已有任务：跳转已有任务状态', async () => {
      apiMocks.submit.mockResolvedValueOnce({ job_id: 'j1', existing_job: true, existing_status: 'completed', existing_title: 'T' })
      const store = useJobStore()
      await store.submitJob({ url: 'https://x' })
      expect(store.jobStatus).toBe('completed')
      expect(store.jobTitle).toBe('T')
      expect(store.isProcessing).toBe(false)
    })

    it('force_new 且后端仍返回已有任务：报错提示重启后端', async () => {
      apiMocks.submit.mockResolvedValueOnce({ job_id: 'j1', existing_job: true })
      const store = useJobStore()
      await expect(store.submitJob({ url: 'https://x', force_new: true })).rejects.toThrow('请重启后端')
      expect(store.jobStatus).toBe('failed')
    })

    it('失败：标记 failed、记录日志并抛出', async () => {
      apiMocks.submit.mockRejectedValueOnce(new Error('network down'))
      const store = useJobStore()
      await expect(store.submitJob({ url: 'https://x' })).rejects.toThrow('network down')
      expect(store.jobStatus).toBe('failed')
      expect(store.isProcessing).toBe(false)
      expect(store.logs.at(-1).msg).toBe('提交失败: network down')
    })

    it('queueMode：入队不重置当前任务状态（进度/结果不被冲掉）', async () => {
      apiMocks.submit.mockResolvedValueOnce({ job_id: 'j2' })
      const store = useJobStore()
      // 模拟「任务 j1 正在跑」
      store.currentJobId = 'j1'
      store.jobStatus = 'downloading'
      store.isProcessing = true
      store.resultText = '当前结果'

      const res = await store.submitJob({ url: 'https://y' }, { queueMode: true })

      expect(res.job_id).toBe('j2')
      expect(store.currentJobId).toBe('j1')
      expect(store.jobStatus).toBe('downloading')
      expect(store.resultText).toBe('当前结果')
      expect(store.isProcessing).toBe(true)
    })

    it('queueMode：入队失败不把正在跑的任务标记为 failed', async () => {
      apiMocks.submit.mockRejectedValueOnce(new Error('boom'))
      const store = useJobStore()
      store.currentJobId = 'j1'
      store.jobStatus = 'downloading'
      store.isProcessing = true

      await expect(store.submitJob({ url: 'https://y' }, { queueMode: true })).rejects.toThrow('boom')

      expect(store.jobStatus).toBe('downloading')
      expect(store.isProcessing).toBe(true)
    })
  })

  describe('loadJob / tryRestore', () => {
    it('loadJob 成功：恢复任务字段与持久化日志', async () => {
      localStorage.setItem(LOGS_KEY, JSON.stringify({ j9: [{ time: 't', msg: 'saved' }] }))
      apiMocks.get.mockResolvedValueOnce({
        id: 'j9', status: 'summarizing', title: 'TT', result_text: 'R', progress_msg: 'P',
      })
      const store = useJobStore()
      await store.loadJob('j9')
      expect(store.currentJobId).toBe('j9')
      expect(store.jobStatus).toBe('summarizing')
      expect(store.jobTitle).toBe('TT')
      expect(store.resultText).toBe('R')
      expect(store.progress.msg).toBe('P')
      expect(store.logs.map((l) => l.msg)).toEqual(['saved'])
      expect(store.isProcessing).toBe(true)
    })

    it('loadJob 失败：记录日志并抛出', async () => {
      apiMocks.get.mockRejectedValueOnce(new Error('404'))
      const store = useJobStore()
      await expect(store.loadJob('missing')).rejects.toThrow('404')
      expect(store.logs.at(-1).msg).toBe('加载任务失败: 404')
    })

    it('tryRestore：有保存状态则恢复并返回 true', () => {
      localStorage.setItem(STATE_KEY, JSON.stringify({ currentJobId: 'j2', jobStatus: 'downloading', jobTitle: 'T' }))
      const store = useJobStore()
      expect(store.tryRestore()).toBe(true)
      expect(store.currentJobId).toBe('j2')
      expect(store.jobStatus).toBe('downloading')
      expect(store.jobTitle).toBe('T')
    })

    it('tryRestore：无保存状态返回 false', () => {
      const store = useJobStore()
      expect(store.tryRestore()).toBe(false)
    })
  })

  describe('appendAiToken', () => {
    it('流式片段拼接；done 信号覆盖完整结果并置完成态', () => {
      const store = useJobStore()
      store.appendAiToken({ token: 'hello ' })
      store.appendAiToken({ tokens: ['wor', 'ld'] })
      expect(store.resultText).toBe('hello world')
      store.appendAiToken({ done: true, token: '完整结果' })
      expect(store.resultText).toBe('完整结果')
      expect(store.jobStatus).toBe('completed')
      expect(store.isProcessing).toBe(false)
    })
  })

  // ===== 失败态（2026-10-05 静默失败修复）=====
  // 回归点：失败必须有 jobError + jobStatus='failed' + isProcessing=false 三者齐备，
  // 否则结果区会命中「暂无结果」空态，失败与未提交视觉上无法区分。
  describe('失败态可见性', () => {
    it('setFailed：落失败态、停转圈、记录原因', () => {
      const store = useJobStore()
      store.jobStatus = 'summarizing'
      store.isProcessing = true
      store.setFailed('HTTP 503: All available accounts exhausted')
      expect(store.jobStatus).toBe('failed')
      expect(store.isProcessing).toBe(false)
      expect(store.jobError).toBe('HTTP 503: All available accounts exhausted')
    })

    it('setFailed 原因缺失时给兜底文案（不得退回空态）', () => {
      const store = useJobStore()
      store.setFailed('')
      expect(store.jobStatus).toBe('failed')
      expect(store.jobError).toBeTruthy()   // 空串会让失败再次变得不可见
      store.setFailed(undefined)
      expect(store.jobError).toBeTruthy()
    })

    it('reset 清空 jobError，避免上个任务的失败原因串台', () => {
      const store = useJobStore()
      store.setFailed('boom')
      store.reset()
      expect(store.jobError).toBe('')
      expect(store.jobStatus).toBe('idle')
    })

    it('loadJob 拉到 failed 任务时带回后端 error', async () => {
      apiMocks.get.mockResolvedValueOnce({
        id: 'j3', status: 'failed', title: 'T',
        result_text: '', error: 'HTTP 503: All available accounts exhausted',
      })
      const store = useJobStore()
      await store.loadJob('j3')
      expect(store.jobError).toBe('HTTP 503: All available accounts exhausted')
      expect(store.isProcessing).toBe(false)
    })

    it('loadJob 拉到非 failed 任务时清空 jobError', async () => {
      apiMocks.get.mockResolvedValueOnce({
        id: 'j4', status: 'completed', title: 'T', result_text: 'R', error: '',
      })
      const store = useJobStore()
      store.setFailed('stale')
      await store.loadJob('j4')
      expect(store.jobError).toBe('')
    })

    it('submitJob 提交失败时经 setFailed 落可见失败态', async () => {
      apiMocks.submit.mockRejectedValueOnce(new Error('后端 500'))
      const store = useJobStore()
      await expect(store.submitJob({})).rejects.toThrow('后端 500')
      expect(store.jobStatus).toBe('failed')
      expect(store.isProcessing).toBe(false)
      expect(store.jobError).toBe('后端 500')
    })
  })

  // ===== AI 调用链（兜底环，2026-10-05）=====
  // 后端存的是 JSON 字符串，接口层可能已解析成数组，两种都要归一化。
  describe('AI 调用链', () => {
    it('loadJob 解析 JSON 字符串形式的调用链', async () => {
      apiMocks.get.mockResolvedValueOnce({
        id: 'j1', status: 'completed', result_text: 'R',
        ai_call_chain: '[{"model":"m1","ok":false,"reason":"HTTP 503"},{"model":"m2","ok":true,"reason":""}]',
      })
      const store = useJobStore()
      await store.loadJob('j1')
      expect(store.aiCallChain).toHaveLength(2)
      expect(store.aiCallChain[0].ok).toBe(false)
      expect(store.aiCallChain[1].model).toBe('m2')
    })

    it('loadJob 接受已解析的数组', async () => {
      apiMocks.get.mockResolvedValueOnce({
        id: 'j2', status: 'completed', result_text: 'R',
        ai_call_chain: [{ model: 'm1', ok: true, reason: '' }],
      })
      const store = useJobStore()
      await store.loadJob('j2')
      expect(store.aiCallChain).toHaveLength(1)
    })

    it('非法 JSON 归一化为空数组，不让脏数据打挂任务页', async () => {
      apiMocks.get.mockResolvedValueOnce({
        id: 'j3', status: 'completed', result_text: 'R', ai_call_chain: '{broken',
      })
      const store = useJobStore()
      await store.loadJob('j3')
      expect(store.aiCallChain).toEqual([])
    })

    it('无调用链字段时为空数组；reset 清空', async () => {
      apiMocks.get.mockResolvedValueOnce({ id: 'j4', status: 'completed', result_text: 'R' })
      const store = useJobStore()
      await store.loadJob('j4')
      expect(store.aiCallChain).toEqual([])
      store.aiCallChain = [{ model: 'm1', ok: true }]
      store.reset()
      expect(store.aiCallChain).toEqual([])
    })
  })

  it('ACTIVE_JOB_STATUSES 常量存在（watch 依赖）', () => {
    expect(ACTIVE_JOB_STATUSES).toContain('summarizing')
  })
})

describe('jobStore.refreshMeta / transcript_meta（2026-10-07 结果可解释性）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('refreshMeta 只回填来源信息与调用链，不扰动流式结果状态', async () => {
    apiMocks.get.mockResolvedValueOnce({
      id: 'j9', status: 'completed', result_text: 'DB里的文本',
      transcript_meta: JSON.stringify({ source: 'transcription', engine: 'cloud_free', degraded: true }),
      ai_call_chain: [{ model: 'm1', ok: false, reason: 'x' }, { model: 'm2', ok: true, reason: '' }],
    })
    const store = useJobStore()
    store.resultText = '流式累积的文本'
    await store.refreshMeta('j9')

    expect(store.transcriptMeta).toEqual({ source: 'transcription', engine: 'cloud_free', degraded: true })
    expect(store.aiCallChain.length).toBe(2)
    expect(store.resultText).toBe('流式累积的文本')
  })

  it('refreshMeta 拉取失败不抛错（来源信息缺失可容忍）', async () => {
    apiMocks.get.mockRejectedValueOnce(new Error('网络断开'))
    const store = useJobStore()
    await expect(store.refreshMeta('j9')).resolves.toBeUndefined()
    expect(store.transcriptMeta).toBeNull()
  })

  it('loadJob 归一化 transcript_meta（对象/JSON串/null），reset 清空', async () => {
    const store = useJobStore()
    apiMocks.get.mockResolvedValueOnce({ id: 'j1', status: 'completed', transcript_meta: { source: 'text_input' } })
    await store.loadJob('j1')
    expect(store.transcriptMeta).toEqual({ source: 'text_input' })

    apiMocks.get.mockResolvedValueOnce({ id: 'j2', status: 'completed', transcript_meta: '{"source":"subtitle_file"}' })
    await store.loadJob('j2')
    expect(store.transcriptMeta).toEqual({ source: 'subtitle_file' })

    apiMocks.get.mockResolvedValueOnce({ id: 'j3', status: 'completed' })
    await store.loadJob('j3')
    expect(store.transcriptMeta).toBeNull()

    store.reset()
    expect(store.transcriptMeta).toBeNull()
  })
})
