import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ResultView from '../views/ResultView.vue'

const apiMocks = vi.hoisted(() => ({
  get: vi.fn(),
  getSummaryHistory: vi.fn(),
  getSummaryVersion: vi.fn(),
  screenshots: vi.fn(),
  summarize: vi.fn(),
  generateMindmap: vi.fn(),
}))
const messageMocks = vi.hoisted(() => ({ error: vi.fn(), warning: vi.fn(), success: vi.fn(), info: vi.fn() }))
// 路由对象必须是 reactive：组件 watch(() => route.params.jobId) 才能感知同路由记录内的换任务
const routeHolder = vi.hoisted(() => ({ state: null }))

vi.mock('element-plus', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    ElMessage: {
      error: messageMocks.error, warning: messageMocks.warning,
      success: messageMocks.success, info: messageMocks.info,
    },
    ElMessageBox: { confirm: vi.fn() },
  }
})

vi.mock('vue-router', async () => {
  const { reactive } = await import('vue')
  routeHolder.state = reactive({ params: { jobId: 'job-1' }, query: {} })
  return {
    useRoute: () => routeHolder.state,
    useRouter: () => ({ back: vi.fn(), push: vi.fn(), replace: vi.fn() }),
  }
})

vi.mock('../utils/socket', () => ({
  onAiToken: () => vi.fn(),
  onAiDone: () => vi.fn(),
  onAiThinking: () => vi.fn(),
}))

vi.mock('../utils/api', () => ({
  jobsApi: apiMocks,
  exportApi: { docx: vi.fn(), pdf: vi.fn(), html: vi.fn(), txt: vi.fn(), batch: vi.fn() },
  modelsApi: { list: vi.fn().mockResolvedValue({ models: [], default: '' }) },
}))
vi.mock('../utils/clipboard', () => ({ copyText: vi.fn() }))

function deferred() {
  let resolve, reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function jobData(id, version) {
  return {
    id, status: 'completed', summary_version: version,
    result_text: `${id} 正文`, files: {}, summary_type: 'summary', model: 'm',
  }
}

function mountView() {
  setActivePinia(createPinia())
  return shallowMount(ResultView)
}

describe('ResultView 换任务时的加载竞态（待复核项 2）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    routeHolder.state.params.jobId = 'job-1'
    apiMocks.getSummaryHistory.mockResolvedValue({ versions: [] })
  })

  it('慢到的旧任务响应不得覆盖正在看的新任务', async () => {
    const slowJob1 = deferred()
    apiMocks.get.mockImplementation((id) => (id === 'job-1' ? slowJob1.promise : Promise.resolve(jobData(id, 1))))

    const wrapper = mountView()
    await flushPromises()
    // job-1 的请求还在飞
    expect(apiMocks.get).toHaveBeenCalledWith('job-1')

    // 用户已经跳到 job-2（浏览器后退 /result/A ⇄ /result/B 复用同一实例）
    routeHolder.state.params.jobId = 'job-2'
    await nextTick()
    await flushPromises()
    expect(wrapper.vm.job.id).toBe('job-2')

    // 旧请求此刻才回：绝不能把页面打回 job-1
    slowJob1.resolve(jobData('job-1', 4))
    await flushPromises()

    expect(wrapper.vm.job.id).toBe('job-2')
    expect(wrapper.vm.loading).toBe(false)
    expect(messageMocks.error).not.toHaveBeenCalled()
  })

  it('版本历史在换任务后到达时也不得落到当前页面', async () => {
    const slowHistory = deferred()
    apiMocks.get.mockImplementation((id) => Promise.resolve(jobData(id, 1)))
    // 只有 job-1 的版本列表迟到；job-2 的正常返回
    apiMocks.getSummaryHistory.mockImplementation((id) =>
      (id === 'job-1' ? slowHistory.promise : Promise.resolve({ versions: [{ version: 1 }] })))

    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.vm.job.id).toBe('job-1')

    routeHolder.state.params.jobId = 'job-2'
    await nextTick()
    await flushPromises()
    expect(wrapper.vm.job.id).toBe('job-2')

    // 旧任务的版本列表迟到：页面必须仍是 job-2 的版本列表
    slowHistory.resolve({ versions: [{ version: 2 }] })
    await flushPromises()

    expect(wrapper.vm.summaryHistory).toEqual([{ version: 1 }])
  })

  it('正常单次加载不受影响（非回归）', async () => {
    apiMocks.get.mockResolvedValue(jobData('job-1', 2))
    apiMocks.getSummaryHistory.mockResolvedValue({ versions: [{ version: 2 }, { version: 1 }] })

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.vm.job.id).toBe('job-1')
    expect(wrapper.vm.summaryHistory).toHaveLength(2)
    expect(wrapper.vm.loading).toBe(false)
  })
})