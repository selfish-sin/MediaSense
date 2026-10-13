import { flushPromises, mount } from '@vue/test-utils'
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
  // ChatPanel 在完整 mount 下会挂载并拉会话列表
  chatApi: {
    sessions: vi.fn().mockResolvedValue({ sessions: [] }),
    createSession: vi.fn().mockResolvedValue({ id: 's1' }),
    sessionHistory: vi.fn().mockResolvedValue({ messages: [] }),
    streamChat: vi.fn(),
    deleteMessages: vi.fn(), deleteMessage: vi.fn(), deleteFrom: vi.fn(),
    deleteSession: vi.fn(), renameSession: vi.fn(), activateVariant: vi.fn(),
    clearSessionMessages: vi.fn(),
  },
}))
vi.mock('../utils/clipboard', () => ({ copyText: vi.fn() }))

function deferred() {
  let resolve, reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

// 导图容器在 el-card 的默认插槽里，shallowMount 的 el-card stub 不渲染插槽 → 容器恒为 null，
// 只能用完整 mount（el-* 未在测试里注册，按未知原生元素渲染，子节点照常渲染）
async function mountView() {
  setActivePinia(createPinia())
  apiMocks.get.mockResolvedValue({
    id: 'job-1', status: 'completed', summary_version: 4, result_text: 'v4 最新版正文', files: {},
  })
  apiMocks.getSummaryHistory.mockResolvedValue({ versions: [{ version: 4 }, { version: 3 }] })
  apiMocks.getSummaryVersion.mockResolvedValue({ version: 2, result_text: 'v2 正文' })
  const wrapper = mount(ResultView)
  await flushPromises()
  return wrapper
}

describe('ResultView 思维导图版本竞态（P1-9）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('导图在飞时切版本：旧回包不得被标记为就绪，也不得写进导图容器', async () => {
    const mapReq = deferred()
    apiMocks.generateMindmap.mockReturnValue(mapReq.promise)

    const wrapper = await mountView()

    // 点思维导图 tab 发起生成（此时看的是最新版 v4）
    wrapper.vm.switchToMindmapTab()
    await flushPromises()
    expect(apiMocks.generateMindmap).toHaveBeenCalledTimes(1)
    expect(wrapper.vm.mindmapLoading).toBe(true)
    // 导图请求带的是"当前查看的版本号"，最新版不传 version
    expect(apiMocks.generateMindmap.mock.calls[0][2]).toBeUndefined()

    // 转圈期间用户点了 v2 版本 chip
    await wrapper.vm.switchToVersion(2)
    await flushPromises()
    expect(wrapper.vm.currentVersionNo).toBe(2)

    // 旧回包此刻才到：内容是 v4 正文的导图，绝不能落到正在看 v2 的页面上
    mapReq.resolve({ data: { mindmap_md: '# 旧版导图根\n## 旧版子节点' } })
    // 等这次请求真正收尾（回包后还要动态 import markmap-lib 再渲染），否则断言可能抢跑
    await vi.waitFor(() => expect(wrapper.vm.mindmapLoading).toBe(false))

    // 结构不变量 1：不得被标记为就绪（否则 switchToMindmapTab 不再重新生成）
    expect(wrapper.vm.mindmapReady).toBe(false)
    // 结构不变量 2：导图归属版本必须与当前查看版本一致
    expect(wrapper.vm.mindmapVersion).toBe(0)
    // 结构不变量 3：容器里一个导图节点都没有（旧回包渲染出来的节点都带 border-left 行内样式）
    expect(wrapper.vm.mindmapContainer.querySelectorAll('div[style*="border-left"]').length).toBe(0)
    // 结构不变量 4：loading 不能卡死，否则用户再也点不出导图
    expect(wrapper.vm.mindmapLoading).toBe(false)
    // 被取代的回包不是错误，不该弹失败提示
    expect(messageMocks.error).not.toHaveBeenCalled()
  })

  it('切回最新版同样作废在飞导图（回包属于历史版本正文）', async () => {
    const mapReq = deferred()
    apiMocks.generateMindmap.mockReturnValue(mapReq.promise)

    const wrapper = await mountView()
    await wrapper.vm.switchToVersion(2)
    await flushPromises()

    wrapper.vm.switchToMindmapTab()
    await flushPromises()
    expect(apiMocks.generateMindmap.mock.calls[0][2]).toBe(2)

    await wrapper.vm.switchToVersion(0)
    mapReq.resolve({ data: { mindmap_md: '# v2 导图根' } })
    await vi.waitFor(() => expect(wrapper.vm.mindmapLoading).toBe(false))

    expect(wrapper.vm.currentVersionNo).toBe(0)
    expect(wrapper.vm.mindmapReady).toBe(false)
    expect(wrapper.vm.mindmapContainer.querySelectorAll('div[style*="border-left"]').length).toBe(0)
    expect(wrapper.vm.mindmapLoading).toBe(false)
  })

  it('未发生版本切换时导图正常渲染，且记录归属版本', async () => {
    apiMocks.generateMindmap.mockResolvedValue({ data: { mindmap_md: '# 导图根\n## 子节点' } })

    const wrapper = await mountView()
    wrapper.vm.switchToMindmapTab()
    // markmap-lib 是动态 import，模块解析要额外时间 → 等渲染完成而不是只 flush 一次
    await vi.waitFor(() => expect(wrapper.vm.mindmapReady).toBe(true))

    expect(wrapper.vm.mindmapVersion).toBe(0)
    // 导图节点真的渲染进容器了（每个节点一根 border-left 竖线）
    expect(wrapper.vm.mindmapContainer.querySelectorAll('div[style*="border-left"]').length).toBe(2)
    expect(wrapper.vm.mindmapLoading).toBe(false)
  })

  it('按 v2 正文生成的导图：渲染后再切版本必须作废（归属版本号归零）', async () => {
    apiMocks.generateMindmap.mockResolvedValue({ data: { mindmap_md: '# v2 导图根' } })

    const wrapper = await mountView()
    await wrapper.vm.switchToVersion(2)
    await flushPromises()

    wrapper.vm.switchToMindmapTab()
    await vi.waitFor(() => expect(wrapper.vm.mindmapReady).toBe(true))
    expect(wrapper.vm.mindmapVersion).toBe(2)

    // 再切到 v3：旧导图必须失效，不能留着 v2 的图配 v3 的标签
    apiMocks.getSummaryVersion.mockResolvedValue({ version: 3, result_text: 'v3 正文' })
    await wrapper.vm.switchToVersion(3)
    await flushPromises()

    expect(wrapper.vm.mindmapReady).toBe(false)
    expect(wrapper.vm.mindmapVersion).toBe(0)
  })
})