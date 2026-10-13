import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from '../components/ChatPanel.vue'

const apiMocks = vi.hoisted(() => ({
  sessions: vi.fn(),
  sessionHistory: vi.fn(),
  streamChat: vi.fn(),
  deleteMessages: vi.fn(),
  deleteFrom: vi.fn(),
  createSession: vi.fn(),
  renameSession: vi.fn(),
  deleteSession: vi.fn(),
  clearSessionMessages: vi.fn(),
  activateVariant: vi.fn(),
}))

const messageMocks = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
const confirmMock = vi.hoisted(() => vi.fn())

vi.mock('element-plus', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    ElMessage: {
      success: messageMocks.success, error: messageMocks.error,
      warning: messageMocks.warning, info: messageMocks.info,
    },
    ElMessageBox: { confirm: confirmMock, prompt: vi.fn() },
  }
})

vi.mock('../utils/api', () => ({
  chatApi: apiMocks,
  modelsApi: { list: vi.fn().mockResolvedValue({ models: [], default: '' }) },
}))
vi.mock('../utils/clipboard', () => ({ copyText: vi.fn() }))

const SESSION = { id: 's1', title: '新对话', message_count: 2 }
const HISTORY = {
  messages: [
    { id: 1, role: 'user', content: '第一问', created_at: '2026-10-13 10:00:00' },
    { id: 2, role: 'assistant', content: '第一答', created_at: '2026-10-13 10:00:01' },
    { id: 3, role: 'user', content: '第二问', created_at: '2026-10-13 10:00:02' },
    { id: 4, role: 'assistant', content: '第二答', created_at: '2026-10-13 10:00:03' },
  ],
}

async function mountPanel() {
  setActivePinia(createPinia())
  const wrapper = shallowMount(ChatPanel, { props: { jobId: 'job-1' } })
  await flushPromises()
  return wrapper
}

describe('ChatPanel 删除整轮', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.sessions.mockResolvedValue({ sessions: [SESSION] })
    apiMocks.sessionHistory.mockResolvedValue(structuredClone(HISTORY))
    apiMocks.deleteMessages.mockResolvedValue({ deleted: 1 })
    apiMocks.deleteFrom.mockResolvedValue({ deleted: 4 })
    confirmMock.mockResolvedValue('confirm')
  })

  // C1（P0）：deleteFrom 是级联端点（删该条及其之后全部），与「删除整轮」文案相反且不可恢复
  it('onDeleteTurn 走不级联的批量端点，绝不调 deleteFrom', async () => {
    const wrapper = await mountPanel()
    expect(apiMocks.deleteFrom).not.toHaveBeenCalled()

    await wrapper.vm.onDeleteTurn({ id: 3, role: 'user', content: '第二问' })
    await flushPromises()

    expect(apiMocks.deleteMessages).toHaveBeenCalledWith('job-1', [3])
    expect(apiMocks.deleteFrom).not.toHaveBeenCalled()
  })

  // onEdit 的语义就是截断后续对话，级联端点必须保留
  it('onEdit 仍走级联端点（编辑本来就要截断后续对话）', async () => {
    const wrapper = await mountPanel()

    await wrapper.vm.onEdit({ id: 3, role: 'user', content: '第二问' })
    await flushPromises()

    expect(apiMocks.deleteFrom).toHaveBeenCalledWith('job-1', 3)
    expect(apiMocks.deleteMessages).not.toHaveBeenCalled()
  })

  it('删除失败时给可见错误且不静默', async () => {
    apiMocks.deleteMessages.mockRejectedValue(new Error('后端 500'))
    const wrapper = await mountPanel()

    await wrapper.vm.onDeleteTurn({ id: 3, role: 'user', content: '第二问' })
    await flushPromises()

    expect(messageMocks.error).toHaveBeenCalledWith('删除失败: 后端 500')
  })
})

describe('ChatPanel 流式正文渲染', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.sessions.mockResolvedValue({ sessions: [SESSION] })
    apiMocks.sessionHistory.mockResolvedValue(structuredClone(HISTORY))
    confirmMock.mockResolvedValue('confirm')
  })

  // C9：发送路径曾把裸对象 push 进 messages 后直接 += delta（raw 写入不触发视图更新），
  // 气泡会停在「生成中」直到 onEnd 才整段出现；取回响应式代理后逐 token 可见
  it('流式期间气泡逐段更新，而不是等到 onEnd 才一次性出现全文', async () => {
    let handlers = null
    apiMocks.streamChat.mockImplementation((_jobId, _sid, _payload, h) => {
      handlers = h
      return Promise.resolve()
    })

    const wrapper = await mountPanel()
    // 记录发送前的基线条数：组件挂载时会载入会话历史，不能假设数组是空的
    const baselineCount = wrapper.vm.messages.length
    const baselineAssistantCount =
      wrapper.vm.messages.filter(m => m.role === 'assistant').length
    wrapper.vm.input = '新问题'
    await wrapper.vm.onSend()
    await flushPromises()

    expect(handlers).not.toBeNull()
    expect(wrapper.text()).toContain('生成中')

    handlers.onEvent({ type: 'content', delta: '第一段' })
    await flushPromises()
    expect(wrapper.text()).toContain('第一段')

    handlers.onEvent({ type: 'content', delta: '第二段' })
    await flushPromises()
    expect(wrapper.text()).toContain('第一段第二段')

    // ★ 消息条数断言（2026-10-13 独立复核补的回归锁）：
    //   只断言文本包含是不够的——onSend 曾把同一个 assistant 对象 push 了两次
    //   （修响应式代理时多写了一行 push，没删原来那行），多出来的那个是**空对象**，
    //   不产生任何文本，所以 text() 断言照样通过，而用户每次追问回答上方会多出
    //   一行空「AI」占位、且 :key 重复，必须刷新页面才消失。
    //   必须在 onEnd **之前**断言：onEnd 会触发 loadHistory 用会话历史整体替换
    //   messages 数组，之后再数就数不到这次发送产生的那两条了。
    const sent = wrapper.vm.messages
    expect(sent).toHaveLength(baselineCount + 2)
    expect(sent.filter(m => m.role === 'assistant')).toHaveLength(baselineAssistantCount + 1)
    const ids = sent.map(m => m.id)
    expect(new Set(ids).size).toBe(ids.length)

    handlers.onEnd(false)
    await flushPromises()
  })

  it('思考过程同样逐段更新', async () => {
    let handlers = null
    apiMocks.streamChat.mockImplementation((_jobId, _sid, _payload, h) => {
      handlers = h
      return Promise.resolve()
    })

    const wrapper = await mountPanel()
    wrapper.vm.input = '新问题'
    // 思考过程默认折叠（跨会话偏好默认 off），打开才断言它逐段出现
    wrapper.vm.showThinking = true
    await wrapper.vm.onSend()
    await flushPromises()

    handlers.onEvent({ type: 'thinking', delta: '正在想' })
    await flushPromises()
    expect(wrapper.text()).toContain('正在想')

    handlers.onEnd(false)
    await flushPromises()
  })
})