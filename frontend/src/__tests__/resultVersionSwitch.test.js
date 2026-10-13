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
vi.mock('../utils/clipboard', () => ({ copyText: vi.fn() }))

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
  apiMocks.getSummaryHistory.mockResolvedValue({ versions: [{ version: 4 }, { version: 3 }, { version: 2 }] })
  const wrapper = shallowMount(ResultView)
  await flushPromises()
  return wrapper
}

describe('ResultView 历史版本切换竞态（C3）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('先发的 v2 响应后到也不得覆盖正在看的 v3', async () => {
    const v2 = deferred()
    const v3 = deferred()
    apiMocks.getSummaryVersion.mockImplementation((_id, version) => (version === 2 ? v2.promise : v3.promise))

    const wrapper = await mountView()

    const p2 = wrapper.vm.switchToVersion(2)
    const p3 = wrapper.vm.switchToVersion(3)
    // v3 先回，v2 后回（用户先点 v2 又改主意点 v3）
    v3.resolve({ version: 3, result_text: 'v3 正文' })
    await flushPromises()
    v2.resolve({ version: 2, result_text: 'v2 正文' })
    await flushPromises()
    await Promise.all([p2, p3])

    expect(wrapper.vm.activeVersion).toBe(3)
    expect(wrapper.vm.activeVersionResult.result_text).toBe('v3 正文')
    // 导出/思维导图取的就是这个版本号，必须与正文一致
    expect(wrapper.vm.currentVersionNo).toBe(3)
  })

  it('被取代的 v2 请求失败时不得把用户从 v3 弹回最新版', async () => {
    const v2 = deferred()
    const v3 = deferred()
    apiMocks.getSummaryVersion.mockImplementation((_id, version) => (version === 2 ? v2.promise : v3.promise))

    const wrapper = await mountView()

    const p2 = wrapper.vm.switchToVersion(2)
    const p3 = wrapper.vm.switchToVersion(3)
    v3.resolve({ version: 3, result_text: 'v3 正文' })
    await flushPromises()
    v2.reject(new Error('网络中断'))
    await flushPromises()
    await Promise.all([p2, p3])

    expect(wrapper.vm.activeVersion).toBe(3)
    expect(wrapper.vm.activeVersionResult.result_text).toBe('v3 正文')
    expect(messageMocks.error).not.toHaveBeenCalled()
  })

  it('当前版本的请求失败仍回落到最新版并提示（未被取代时行为不变）', async () => {
    apiMocks.getSummaryVersion.mockRejectedValue(new Error('后端 500'))

    const wrapper = await mountView()
    await wrapper.vm.switchToVersion(2)
    await flushPromises()

    expect(wrapper.vm.activeVersion).toBe(0)
    expect(messageMocks.error).toHaveBeenCalledWith('加载版本失败: 后端 500')
  })

  it('切回最新版会作废在飞请求，旧响应不能塞回历史正文', async () => {
    const v2 = deferred()
    apiMocks.getSummaryVersion.mockImplementation(() => v2.promise)

    const wrapper = await mountView()
    const p2 = wrapper.vm.switchToVersion(2)
    await wrapper.vm.switchToVersion(0)
    v2.resolve({ version: 2, result_text: 'v2 正文' })
    await flushPromises()
    await p2

    expect(wrapper.vm.activeVersion).toBe(0)
    expect(wrapper.vm.activeVersionResult).toBe(null)
    expect(wrapper.vm.versionLoading).toBe(false)
  })
})