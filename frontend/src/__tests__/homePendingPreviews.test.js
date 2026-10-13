import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import HomeView from '../views/HomeView.vue'

const apiMocks = vi.hoisted(() => ({
  get: vi.fn(),
  getLogs: vi.fn().mockResolvedValue({ logs: [] }),
  batchStatus: vi.fn().mockResolvedValue({ batch_id: null }),
  batchDiskInfo: vi.fn(),
  queue: vi.fn().mockResolvedValue({ running: [], pending: [] }),
  running: vi.fn().mockResolvedValue([]),
  list: vi.fn().mockResolvedValue({ jobs: [] }),
  cancel: vi.fn(),
  batchCancel: vi.fn(),
  submit: vi.fn(),
  batch: vi.fn(),
}))
const socketMocks = vi.hoisted(() => ({
  handlers: {},
  continueSummary: vi.fn(),
  cancelPreview: vi.fn(),
  cancelJob: vi.fn(),
}))
const messageMocks = vi.hoisted(() => ({ error: vi.fn(), warning: vi.fn(), success: vi.fn(), info: vi.fn() }))

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

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: {}, query: {} }),
  useRouter: () => ({ back: vi.fn(), push: vi.fn(), replace: vi.fn().mockResolvedValue(undefined) }),
}))

// 捕获 socket 事件处理器，测试里直接模拟 preview / done 事件
function register(event, handler) {
  socketMocks.handlers[event] = handler
  return () => { delete socketMocks.handlers[event] }
}
vi.mock('../utils/socket', () => ({
  default: { on: vi.fn(), off: vi.fn(), emit: vi.fn() },
  onAiToken: (h) => register('ai_token', h),
  onAiThinking: (h) => register('ai_thinking', h),
  onAiDone: (h) => register('ai_done', h),
  onPreview: (h) => register('preview', h),
  // useBatchProgress / useJobQueue / useBackendLogs 也会注册
  onBatchProgress: () => vi.fn(),
  onProgress: () => vi.fn(),
  onLog: () => vi.fn(),
  onSocketConnect: () => vi.fn(),
  onSocketDisconnect: () => vi.fn(),
  continueSummary: socketMocks.continueSummary,
  cancelPreview: socketMocks.cancelPreview,
  cancelJob: socketMocks.cancelJob,
}))

vi.mock('../utils/api', () => ({
  jobsApi: apiMocks,
  exportApi: { docx: vi.fn(), pdf: vi.fn(), html: vi.fn(), txt: vi.fn(), batch: vi.fn() },
  modelsApi: { list: vi.fn().mockResolvedValue({ models: [], default: '' }) },
  chatApi: {
    sessions: vi.fn().mockResolvedValue({ sessions: [] }),
    createSession: vi.fn().mockResolvedValue({ id: 's1' }),
    sessionHistory: vi.fn().mockResolvedValue({ messages: [] }),
    streamChat: vi.fn(),
    deleteMessages: vi.fn(), deleteMessage: vi.fn(), deleteFrom: vi.fn(),
    deleteSession: vi.fn(), renameSession: vi.fn(), activateVariant: vi.fn(),
    clearSessionMessages: vi.fn(),
  },
  tokenApi: { usage: vi.fn().mockResolvedValue({ total_tokens: 0 }), history: vi.fn().mockResolvedValue({ items: [] }) },
  summaryApi: { deepConfig: vi.fn().mockResolvedValue({}), defaults: vi.fn().mockResolvedValue({}) },
  asrApi: { status: vi.fn().mockResolvedValue({}) },
}))

async function mountView() {
  setActivePinia(createPinia())
  // shallowMount 的自动 stub 没有 reveal()/refresh()，而 onMounted 的恢复流程会调到它们
  const wrapper = shallowMount(HomeView, {
    global: {
      stubs: {
        JobProgressPanel: { template: '<div />', methods: { reveal() {} } },
        HomeTokenPanel: { template: '<div />', methods: { refresh() {} } },
      },
    },
  })
  await flushPromises()
  return wrapper
}

describe('HomeView 多个待确认预览任务（待复核项 1）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getLogs.mockResolvedValue({ logs: [] })
    apiMocks.batchStatus.mockResolvedValue({ batch_id: null })
    apiMocks.queue.mockResolvedValue({ running: [], pending: [] })
    apiMocks.modelsList = undefined
  })

  it('A 停在待确认、B 也到预览：A 仍留在待确认列表里可确认（不被 B 顶掉）', async () => {
    const wrapper = await mountView()
    expect(typeof socketMocks.handlers.preview).toBe('function')

    socketMocks.handlers.preview({ job_id: 'job-A', text: 'A 转录正文', title: 'A 视频' })
    await flushPromises()
    socketMocks.handlers.preview({ job_id: 'job-B', text: 'B 转录正文', title: 'B 视频' })
    await flushPromises()

    // 预览卡跟踪的是最新的 B（与既有行为一致）
    expect(wrapper.vm.jobStore.currentJobId).toBe('job-B')
    expect(wrapper.vm.jobStore.previewText).toBe('B 转录正文')
    // A 仍然可达：待确认列表里带着 A 的正文，不会凭空消失
    expect(wrapper.vm.otherPendingPreviews.map(p => p.jobId)).toEqual(['job-A'])
    expect(wrapper.vm.otherPendingPreviews[0].text).toBe('A 转录正文')
  })

  it('按 A 的 jobId 确认：发出的 continue_summary 落在 A 身上，不受 currentJobId 影响', async () => {
    apiMocks.get.mockResolvedValue({ id: 'job-A', status: 'preview', preview_text: 'A 后端转录正文' })
    const wrapper = await mountView()

    socketMocks.handlers.preview({ job_id: 'job-A', text: 'A 转录正文', title: 'A 视频' })
    await flushPromises()
    socketMocks.handlers.preview({ job_id: 'job-B', text: 'B 转录正文', title: 'B 视频' })
    await flushPromises()
    expect(wrapper.vm.jobStore.currentJobId).toBe('job-B')

    await wrapper.vm.confirmBatchPreview('job-A')

    expect(apiMocks.get).toHaveBeenCalledWith('job-A')
    expect(socketMocks.continueSummary).toHaveBeenCalledWith('job-A', 'A 后端转录正文')
    // 确认后 A 离开待确认列表（此时跟踪的变成 A，仍在等的只剩 B）
    expect(wrapper.vm.pendingPreviews.map(p => p.jobId)).toEqual(['job-B'])
  })

  it('取消 A 也会把它移出待确认列表', async () => {
    const wrapper = await mountView()
    socketMocks.handlers.preview({ job_id: 'job-A', text: 'A 转录正文' })
    await flushPromises()
    socketMocks.handlers.preview({ job_id: 'job-B', text: 'B 转录正文' })
    await flushPromises()

    wrapper.vm.cancelBatchPreview('job-A')

    expect(socketMocks.cancelPreview).toHaveBeenCalledWith('job-A')
    expect(wrapper.vm.otherPendingPreviews).toEqual([])
  })

  it('任务 done 事件自动清掉已离开预览态的待确认项', async () => {
    const wrapper = await mountView()
    socketMocks.handlers.preview({ job_id: 'job-A', text: 'A 转录正文' })
    await flushPromises()
    socketMocks.handlers.preview({ job_id: 'job-B', text: 'B 转录正文' })
    await flushPromises()
    expect(wrapper.vm.otherPendingPreviews).toHaveLength(1)

    socketMocks.handlers.ai_done({ job_id: 'job-A', success: true })
    await flushPromises()

    expect(wrapper.vm.otherPendingPreviews).toEqual([])
  })

  it('只有一个待确认任务时不额外列出（无冗余 UI）', async () => {
    const wrapper = await mountView()
    socketMocks.handlers.preview({ job_id: 'job-A', text: 'A 转录正文' })
    await flushPromises()

    expect(wrapper.vm.otherPendingPreviews).toEqual([])
    expect(wrapper.vm.isPreviewState).toBe(true)
  })
})