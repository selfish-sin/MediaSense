import { describe, expect, it, vi, beforeEach } from 'vitest'
import { ref } from 'vue'

const apiMocks = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('../utils/api', () => ({ jobsApi: apiMocks }))
vi.mock('element-plus', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, ElMessage: { info: vi.fn(), error: vi.fn() } }
})

import { useJobRestore } from '../composables/useJobRestore'

function makeDeps(query = {}) {
  return {
    inputType: ref('url'),
    inputUrl: ref(''),
    inputText: ref(''),
    uploadedFilePath: ref(''),
    uploadedFileName: ref(''),
    customPrompt: ref(''),
    promptOverride: ref(''),
    usePromptOverride: ref(false),
    rerunSourceJobId: ref(null),
    settings: {
      selectedModel: '', selectedSummaryType: '',
      selectedDimensions: {}, advancedOptions: {}, doSummary: true,
      previewBeforeSummary: false, outputOptions: [], fullOverride: '', useFullOverride: false,
    },
    jobStore: {
      currentJobId: null, jobStatus: '', previewText: '', isProcessing: false,
      reset: vi.fn(), loadJob: vi.fn(), tryRestore: vi.fn(),
      addLog: vi.fn(),
    },
    route: { query },
    router: { replace: vi.fn().mockResolvedValue(undefined) },
    scrollToJobProgress: vi.fn(),
  }
}

describe('useJobRestore.handleRouteQuery 并发互斥（C7）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // 首次挂载时 onMounted 与 keep-alive 的 onActivated 会并发调进来：
  // 两次都判定「没有 query 参数」的那一次会落进 tryRestore()，把 localStorage 里另一个
  // 仍在跟踪的任务 load 进来，覆盖刚回填的配置
  it('并发两次调用只跑一遍，tryRestore 不会被调第二次', async () => {
    const deps = makeDeps()
    deps.jobStore.currentJobId = 'job-r'
    deps.jobStore.jobStatus = 'completed'
    deps.jobStore.tryRestore.mockReturnValue(true)
    deps.jobStore.loadJob.mockResolvedValue({ id: 'job-r', options: {}, input_url: 'u' })

    const { handleRouteQuery } = useJobRestore(deps)
    const a = handleRouteQuery()
    const b = handleRouteQuery()
    await Promise.all([a, b])

    expect(deps.jobStore.tryRestore).toHaveBeenCalledTimes(1)
    expect(deps.jobStore.loadJob).toHaveBeenCalledTimes(1)
  })

  it('执行期间再次调用复用同一次执行，不并发拉取', async () => {
    const deps = makeDeps({ restore: 'job-9' })
    let resolveGet
    apiMocks.get.mockImplementation(() => new Promise(res => { resolveGet = res }))
    deps.jobStore.loadJob.mockImplementation(async () => {
      await apiMocks.get('job-9')
      return { id: 'job-9', options: {}, input_url: 'u' }
    })

    const { handleRouteQuery } = useJobRestore(deps)
    const a = handleRouteQuery()
    const b = handleRouteQuery()
    resolveGet({ id: 'job-9', options: {}, input_url: 'u' })
    await Promise.all([a, b])

    expect(apiMocks.get).toHaveBeenCalledTimes(1)
    expect(deps.router.replace).toHaveBeenCalledTimes(1)
  })

  it('执行完之后仍可再次调用（keep-alive 切回要能重新处理新 query）', async () => {
    const deps = makeDeps({ restore: 'job-9' })
    deps.jobStore.loadJob.mockResolvedValue({ id: 'job-9', options: {}, input_url: 'u' })

    const { handleRouteQuery } = useJobRestore(deps)
    await handleRouteQuery()
    await handleRouteQuery()

    expect(deps.jobStore.loadJob).toHaveBeenCalledTimes(2)
  })
})