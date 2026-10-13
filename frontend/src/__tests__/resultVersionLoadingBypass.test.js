import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
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
const clipboardMocks = vi.hoisted(() => ({ copyText: vi.fn() }))

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
  useRoute: () => ({ params: { jobId: 'job-1' }, query: {} }),
  useRouter: () => ({ back: vi.fn(), push: vi.fn(), replace: vi.fn() }),
}))

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
vi.mock('../utils/clipboard', () => ({ copyText: clipboardMocks.copyText }))

function deferred() {
  let resolve, reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

async function mountView() {
  setActivePinia(createPinia())
  apiMocks.get.mockResolvedValue({
    id: 'job-1', status: 'completed', summary_version: 4, result_text: 'v4 最新版正文', files: {},
  })
  apiMocks.getSummaryHistory.mockResolvedValue({ versions: [{ version: 4 }, { version: 3 }] })
  const wrapper = shallowMount(ResultView)
  await flushPromises()
  return wrapper
}

describe('ResultView 版本加载窗口内的复制/预览旁路（待复核项 3）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('加载 v2 正文期间点一键复制：不得把最新版正文复制走', async () => {
    const v2 = deferred()
    apiMocks.getSummaryVersion.mockReturnValue(v2.promise)

    const wrapper = await mountView()
    const p = wrapper.vm.switchToVersion(2)
    await flushPromises()
    // 加载窗口：activeVersion 已是 2，但正文还没到 → displayResult 回落成最新版
    expect(wrapper.vm.versionLoading).toBe(true)
    expect(wrapper.vm.displayResult).toBe('v4 最新版正文')

    await wrapper.vm.handleCopyResult()

    expect(clipboardMocks.copyText).not.toHaveBeenCalled()
    expect(messageMocks.warning).toHaveBeenCalled()

    v2.resolve({ version: 2, result_text: 'v2 正文' })
    await flushPromises()
    await p
  })

  it('加载 v2 正文期间点 HTML 预览：不得用最新版正文开预览窗口', async () => {
    const v2 = deferred()
    apiMocks.getSummaryVersion.mockReturnValue(v2.promise)
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)

    const wrapper = await mountView()
    const p = wrapper.vm.switchToVersion(2)
    await flushPromises()

    wrapper.vm.handlePreviewHtml()

    expect(openSpy).not.toHaveBeenCalled()
    expect(messageMocks.warning).toHaveBeenCalled()

    v2.resolve({ version: 2, result_text: 'v2 正文' })
    await flushPromises()
    await p
    openSpy.mockRestore()
  })

  it('加载完成后复制的是当前查看的版本正文（未受影响）', async () => {
    apiMocks.getSummaryVersion.mockResolvedValue({ version: 2, result_text: 'v2 正文' })

    const wrapper = await mountView()
    await wrapper.vm.switchToVersion(2)
    await flushPromises()

    expect(wrapper.vm.versionLoading).toBe(false)
    await wrapper.vm.handleCopyResult()

    expect(clipboardMocks.copyText).toHaveBeenCalledWith('v2 正文')
    expect(messageMocks.warning).not.toHaveBeenCalled()
  })
})