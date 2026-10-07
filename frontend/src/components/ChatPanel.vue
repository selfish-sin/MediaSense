<template>
  <section class="chat-panel app-card">
    <!-- 单行纤细头部：品牌 + 会话切换 + 模型选择 + 更多菜单（替代旧三行工具栏） -->
    <header class="cp-head">
      <div class="cp-head-left">
        <span class="cp-brand">
          <el-icon :size="15"><MessageCircle /></el-icon>
          <span>追问</span>
        </span>
        <el-select
          v-model="activeSessionId"
          size="small"
          class="cp-ghost-select cp-session-select"
          placeholder="选择对话"
          :disabled="streaming"
          @change="onSelectSession"
        >
          <el-option v-for="s in sessions" :key="s.id" :label="s.title" :value="s.id">
            <span class="session-opt">
              <span class="session-opt-title">{{ s.title }}</span>
              <span class="session-opt-count">{{ s.msg_count || 0 }}</span>
            </span>
          </el-option>
        </el-select>
      </div>
      <div class="cp-head-right">
        <el-select
          v-model="selectedModel"
          size="small"
          class="cp-ghost-select cp-model-select"
          placeholder="默认(沿用任务模型)"
        >
          <el-option label="默认(沿用任务模型)" value="" />
          <el-option
            v-for="m in modelStore.models"
            :key="m.key"
            :value="m.key"
            :label="modelDisplayName(m) || m.key"
          />
        </el-select>
        <span v-if="modelStore.loading" class="cp-hint">模型加载中…</span>
        <el-dropdown trigger="click" @command="onMenuCommand">
          <!-- ⚠ 不能再包一层 el-tooltip：tooltip 作为 dropdown 直接子元素会拦截触发点击（实测菜单弹不出来） -->
          <button class="cp-icon-btn" type="button" title="会话操作">
            <el-icon :size="16"><MoreVertical /></el-icon>
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="new" :icon="Plus">新建对话</el-dropdown-item>
              <el-dropdown-item command="rename" :icon="Pencil" :disabled="!activeSessionId || streaming">重命名</el-dropdown-item>
              <el-dropdown-item command="copy" :icon="Copy" :disabled="!messages.length">复制对话</el-dropdown-item>
              <el-dropdown-item command="clear" :icon="Trash2" :disabled="!messages.length || streaming" divided>清空当前对话</el-dropdown-item>
              <el-dropdown-item command="delete" :icon="Trash2" :disabled="!activeSessionId || streaming">删除当前对话</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </header>

    <!-- 消息区 + 右侧导航轨 -->
    <div class="cp-body">
      <div class="cp-scroll-wrap">
        <div ref="scrollRef" class="cp-scroll" @scroll="onScroll">
          <!-- 空态：欢迎 + 建议 chips（点击即发送） -->
          <div v-if="messages.length === 0 && !streaming" class="cp-empty">
            <div class="cp-empty-glyph"><el-icon :size="22"><MessageCircle /></el-icon></div>
            <div class="cp-empty-title">基于这份分析结果继续追问</div>
            <div class="cp-empty-sub">AI 会结合视频原文与分析结果回答，可开启联网检索补充外部资料</div>
            <div class="cp-suggestions">
              <button
                v-for="s in SUGGESTIONS"
                :key="s"
                type="button"
                class="cp-chip"
                @click="onSend(s)"
              >{{ s }}</button>
            </div>
          </div>

          <div class="cp-thread">
            <div
              v-for="msg in messages"
              :key="msg.id"
              :data-mid="msg.id"
              class="chat-row"
              :class="[msg.role === 'user' ? 'row-user' : 'row-ai', { 'row-active': String(msg.id) === String(activeMsgId) }]"
            >
              <!-- 用户消息：弱色右对齐气泡，操作 hover 显形 -->
              <div v-if="msg.role === 'user'" class="cp-user">
                <div class="cp-user-bubble">{{ msg.content }}</div>
                <div class="cp-msg-actions">
                  <span v-if="msgTime(msg)" class="cp-time">{{ msgTime(msg) }}</span>
                  <el-tooltip content="复制提问" placement="top">
                    <button class="cp-icon-btn" type="button" @click="copyText(msg.content)"><el-icon :size="14"><Copy /></el-icon></button>
                  </el-tooltip>
                  <el-tooltip content="编辑：回到此提问点重新发送" placement="top">
                    <button class="cp-icon-btn" type="button" :disabled="streaming" @click="onEdit(msg)"><el-icon :size="14"><Pencil /></el-icon></button>
                  </el-tooltip>
                  <el-tooltip content="删除该提问及其回答" placement="top">
                    <button class="cp-icon-btn cp-danger" type="button" :disabled="streaming" @click="onDeleteTurn(msg)"><el-icon :size="14"><Trash2 /></el-icon></button>
                  </el-tooltip>
                </div>
              </div>

              <!-- AI 回答：平铺无气泡（头像列 + 内容列） -->
              <div v-else class="cp-ai">
                <div class="cp-ai-glyph">AI</div>
                <div class="cp-ai-main" :class="{ 'cp-streaming': msg === activeMsg && streaming }">
                  <div class="cp-ai-head">
                    <span v-if="msgTime(msg)" class="cp-time">{{ msgTime(msg) }}</span>
                    <span v-if="msg.token_usage && (msg.token_usage.input || msg.token_usage.output)" class="cp-tokens">
                      {{ tokenLabel(msg.token_usage) }}
                    </span>
                    <div class="cp-msg-actions">
                      <el-dropdown
                        v-if="msg.sources && msg.sources.length"
                        trigger="click"
                        @command="(cmd) => onCopyAi(msg, cmd)"
                      >
                        <!-- ⚠ 同头部菜单：不能套 el-tooltip，会拦截 dropdown 触发 -->
                        <button class="cp-icon-btn" type="button" title="复制回答" :disabled="!msg.content">
                          <el-icon :size="14"><Copy /></el-icon>
                          <el-icon :size="10" class="cp-caret"><ChevronDown /></el-icon>
                        </button>
                        <template #dropdown>
                          <el-dropdown-menu>
                            <el-dropdown-item command="body">仅正文</el-dropdown-item>
                            <el-dropdown-item command="with-sources">正文 + 参考来源</el-dropdown-item>
                          </el-dropdown-menu>
                        </template>
                      </el-dropdown>
                      <el-tooltip v-else content="复制回答" placement="top">
                        <button class="cp-icon-btn" type="button" :disabled="!msg.content" @click="onCopyAi(msg, 'body')">
                          <el-icon :size="14"><Copy /></el-icon>
                        </button>
                      </el-tooltip>
                      <el-tooltip content="重新生成" placement="top">
                        <button class="cp-icon-btn" type="button" :disabled="streaming" @click="onRegenerate(msg)"><el-icon :size="14"><RefreshCw /></el-icon></button>
                      </el-tooltip>
                      <el-tooltip content="删除该回答" placement="top">
                        <button class="cp-icon-btn cp-danger" type="button" :disabled="streaming" @click="onDelete(msg)"><el-icon :size="14"><Trash2 /></el-icon></button>
                      </el-tooltip>
                    </div>
                  </div>

                  <!-- 思考过程（折叠面板，全局开关控制显示） -->
                  <details v-if="showThinking && msg.thinking" class="cp-thinking">
                    <summary><el-icon :size="12"><Sparkles /></el-icon> 思考过程</summary>
                    <pre class="cp-thinking-text">{{ msg.thinking }}</pre>
                  </details>

                  <!-- 正文（流式时尾部光标） -->
                  <div
                    v-if="msg.content"
                    class="cp-md markdown-body"
                    v-html="htmlOf(msg)"
                  />
                  <div v-else-if="msg === activeMsg" class="cp-stream-hint">
                    <span class="cp-dot" />{{ msg.thinking ? '思考中' : '生成中' }}
                  </div>

                  <!-- 联网检索状态 / 来源入口：有来源即可点击展开（历史消息 search 事件不落库，
                       故条件不能只看 msg.search，否则历史来源永远没有展开入口） -->
                  <button
                    v-if="msg.sources && msg.sources.length"
                    type="button"
                    class="cp-search-row cp-search-toggle"
                    @click="toggleSources(msg.id)"
                  >
                    <el-icon :size="13"><Globe /></el-icon>
                    <span class="cp-search-text">
                      {{ msg.search && msg.search.status === 'ok' ? searchLabel(msg.search) : `引用来源 · ${msg.sources.length} 条来源` }}
                    </span>
                    <el-icon :size="12" class="cp-caret" :class="{ 'is-open': sourcesOpen(msg.id) }"><ChevronDown /></el-icon>
                  </button>
                  <div v-else-if="msg.search" class="cp-search-row" :class="'cp-search-' + msg.search.status">
                    <el-icon :size="13"><Search /></el-icon>
                    <span class="cp-search-text">{{ searchLabel(msg.search) }}</span>
                  </div>

                  <!-- 引用来源：两列卡片栅格（默认折叠） -->
                  <div v-if="msg.sources && msg.sources.length && sourcesOpen(msg.id)" class="cp-sources">
                    <a
                      v-for="(s, i) in msg.sources"
                      :key="i"
                      :href="s.url"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="cp-source-card"
                    >
                      <span class="cp-source-idx">{{ i + 1 }}</span>
                      <span class="cp-source-body">
                        <span class="cp-source-title">{{ s.title || s.url }}</span>
                        <span class="cp-source-host">{{ hostOf(s.url) }}</span>
                      </span>
                    </a>
                  </div>
                </div>
              </div>
            </div>

            <div v-if="streaming && !activeMsg" class="cp-stream-hint"><span class="cp-dot" />AI 正在思考</div>
          </div>
        </div>

        <!-- 跳到最新（上滚后出现） -->
        <button v-if="!nearBottom && messages.length" type="button" class="cp-jump" @click="jumpToBottom">
          <el-icon :size="13"><ArrowDown /></el-icon> 跳到最新
        </button>

        <!-- 右侧会话记录导航轨：点击跳转、悬停显示提问 -->
        <div v-if="messages.length" class="chat-rail">
          <div
            v-for="(item, i) in railItems"
            :key="item.id"
            class="rail-tick"
            :class="{ 'rail-tick-user': item.role === 'user', 'rail-tick-active': String(item.id) === String(activeMsgId) }"
            :style="{ top: railTop(i) }"
            :title="railTooltip(item)"
            @click="scrollToMsg(item.id)"
          >
            <span class="rail-tip">{{ railTooltip(item) }}</span>
          </div>
          <button class="rail-bottom" title="跳到最新" @click="jumpToBottom">
            <el-icon><ArrowDown /></el-icon>
          </button>
        </div>
      </div>
    </div>

    <!-- Composer：圆角输入舱，模式/开关在底栏左侧，发送在右侧 -->
    <div class="cp-composer">
      <el-input
        v-model="input"
        type="textarea"
        :autosize="{ minRows: 1, maxRows: 8 }"
        resize="none"
        placeholder="继续追问…"
        :disabled="streaming"
        @keydown.enter.exact.prevent="onSend()"
      />
      <div class="cp-composer-bar">
        <div class="cp-composer-left">
          <span class="cp-mode-pill" :class="{ 'is-on': searchMode !== 'off' }">
            <el-icon :size="13"><Globe /></el-icon>
            <el-select v-model="searchMode" size="small" class="cp-mode-select" :disabled="streaming">
              <el-option v-for="m in SEARCH_MODES" :key="m.value" :value="m.value" :label="m.label" />
            </el-select>
          </span>
          <button
            type="button"
            class="cp-toggle"
            :class="{ 'is-on': showThinking }"
            :disabled="streaming"
            @click="showThinking = !showThinking"
          >
            <el-icon :size="13"><Sparkles /></el-icon> 思考
          </button>
        </div>
        <div class="cp-composer-right">
          <span class="cp-hint">Enter 发送 · Shift+Enter 换行</span>
          <button
            v-if="!streaming"
            type="button"
            class="cp-send"
            :disabled="!input.trim()"
            title="发送"
            @click="onSend()"
          ><el-icon :size="16"><ArrowUp /></el-icon></button>
          <button v-else type="button" class="cp-send cp-stop" title="停止生成" @click="onStop">
            <el-icon :size="13"><Square /></el-icon>
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import {
  MessageCircle, Plus, Pencil, Trash2, ArrowDown, ArrowUp, Sparkles,
  Search, Copy, ChevronDown, Globe, Square, MoreVertical, RefreshCw,
} from 'lucide-vue-next'
import { ElMessage, ElMessageBox } from 'element-plus'
import { chatApi } from '../utils/api'
import { copyText } from '../utils/clipboard'
import { buildAiText, buildConversationMarkdown, formatChatTime, SEARCH_MODES, searchStateLabel } from '../utils/chatExport'
import { isDialogCancel } from '../utils/errorNotice'
import { useMarkdownListCache } from '../composables/useMarkdownRender'
import { useModelStore } from '../stores/modelStore'
import { modelDisplayName } from '../utils/modelDisplay'

const props = defineProps({
  jobId: { type: String, required: true },
})

const modelStore = useModelStore()

const messages = ref([])
// 消息列表 Markdown 渲染缓存：内容未变的条目直接命中，流式追加只按帧重渲染变化条目
const { htmlOf } = useMarkdownListCache(
  () => messages.value,
  (msg) => msg.content,
)
const input = ref('')
const streaming = ref(false)
// 联网三态：off 关闭 / strict 严格依据材料 / broad 广泛收集（见 utils/chatExport SEARCH_MODES）
const searchMode = ref('off')
const showThinking = ref(false)
const selectedModel = ref('')
const scrollRef = ref(null)
const nearBottom = ref(true)
const activeMsgId = ref(null)
const activeMsg = ref(null)
let abortCtrl = null
let historyTimer = null
let disposed = false
let tmpSeq = 0

// 引用来源展开状态（按消息 id；Set 为 Vue reactive 支持的集合类型）
const expandedSources = ref(new Set())
function sourcesOpen(id) {
  return expandedSources.value.has(id)
}
function toggleSources(id) {
  const next = new Set(expandedSources.value)
  next.has(id) ? next.delete(id) : next.add(id)
  expandedSources.value = next
}

// 空态建议（点击即发送；通用问法，不依赖任务内容）
const SUGGESTIONS = [
  '总结这份结果的核心观点',
  '列出内容里的关键要点',
  '围绕这个主题，给我 3 个值得深挖的问题',
]

function hostOf(url) {
  try {
    return new URL(url).host
  } catch {
    return ''
  }
}

function tokenLabel(usage) {
  const total = (usage.input || 0) + (usage.output || 0)
  return total >= 1000 ? `${(total / 1000).toFixed(1)}k tokens` : `${total} tokens`
}

// ===== 多会话状态 =====
const sessions = ref([])
const activeSessionId = ref(null)

// 加载模型列表 + 默认模型
async function ensureModels() {
  if (!modelStore.models.length) await modelStore.loadModels()
}

// 加载会话列表；selectId 指定要切换到的会话（不选则回退到首个/保持）
async function loadSessions(selectId) {
  if (!props.jobId) return
  try {
    const res = await chatApi.sessions(props.jobId)
    sessions.value = res.sessions || []
    if (selectId) {
      activeSessionId.value = selectId
    } else if (!activeSessionId.value || !sessions.value.find((s) => s.id === activeSessionId.value)) {
      activeSessionId.value = sessions.value.length ? sessions.value[0].id : null
    }
    if (activeSessionId.value) {
      await loadHistory()
    } else {
      messages.value = []
    }
  } catch (e) {
    console.warn('加载会话列表失败', e)
  }
}

// 轻量刷新会话标题/消息数（流式结束后调用，不重排消息）
async function refreshSessionMeta() {
  try {
    const res = await chatApi.sessions(props.jobId)
    sessions.value = res.sessions || []
  } catch (e) {
    console.warn('刷新会话列表失败', e)
  }
}

// 切换会话
async function onSelectSession(id) {
  activeSessionId.value = id
  streaming.value = false
  activeMsg.value = null
  abortCtrl = null
  await loadHistory()
}

// 新建对话
async function createSession() {
  try {
    const res = await chatApi.createSession(props.jobId, { title: '新对话' })
    await loadSessions(res.id)
    ElMessage.success('已新建对话')
  } catch (e) {
    ElMessage.error('新建对话失败: ' + (e?.message || '未知错误'))
  }
}

// 重命名当前对话
async function renameSession() {
  if (!activeSessionId.value) return
  const cur = sessions.value.find((s) => s.id === activeSessionId.value)
  try {
    const { value } = await ElMessageBox.prompt('输入对话标题', '重命名', {
      inputValue: cur?.title || '',
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      inputValidator: (v) => (v && v.trim()) ? true : '标题不能为空',
    })
    await chatApi.renameSession(props.jobId, activeSessionId.value, { title: value.trim() })
    await refreshSessionMeta()
  } catch (e) {
    if (!isDialogCancel(e) && e?.message) ElMessage.error('重命名失败: ' + e.message)
  }
}

// 删除当前对话（含消息）
async function deleteSession() {
  if (!activeSessionId.value) return
  try {
    await ElMessageBox.confirm('确定删除当前对话及其所有消息？', '删除对话', { type: 'warning' })
  } catch {
    return
  }
  try {
    await chatApi.deleteSession(props.jobId, activeSessionId.value)
    const remain = sessions.value.filter((s) => s.id !== activeSessionId.value)
    await loadSessions(remain.length ? remain[0].id : null)
    ElMessage.success('对话已删除')
  } catch (e) {
    if (e?.message) ElMessage.error('删除失败: ' + e.message)
  }
}

// 头部「⋯」菜单分发
function onMenuCommand(cmd) {
  if (cmd === 'new') createSession()
  else if (cmd === 'rename') renameSession()
  else if (cmd === 'copy') copyConversation()
  else if (cmd === 'clear') onClear()
  else if (cmd === 'delete') deleteSession()
}

// 加载当前会话历史
async function loadHistory() {
  if (!props.jobId || !activeSessionId.value) {
    messages.value = []
    return
  }
  try {
    const res = await chatApi.sessionHistory(props.jobId, activeSessionId.value)
    messages.value = (res.messages || []).map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content || '',
      thinking: m.thinking || '',
      sources: m.sources || [],
      token_usage: m.token_usage || null,
      created_at: m.created_at || '',
      search: null,
    }))
  } catch (e) {
    console.warn('加载追问历史失败', e)
  }
  nearBottom.value = true
  scrollToBottom(true)
}

// 滚动时更新「是否贴近底部」与当前激活消息（用于自动跟随 + 导航轨高亮）
function onScroll() {
  const el = scrollRef.value
  if (!el) return
  nearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40
  const rows = el.querySelectorAll('.chat-row')
  let cur = null
  for (const r of rows) {
    if (r.offsetTop - el.scrollTop >= -8) { cur = r.getAttribute('data-mid'); break }
  }
  if (cur) activeMsgId.value = cur
}

// 点击导航轨圆点：滚动到对应消息
function scrollToMsg(id) {
  const el = scrollRef.value
  if (!el) return
  const node = el.querySelector(`.chat-row[data-mid="${id}"]`)
  if (node) {
    el.scrollTo({ top: node.offsetTop - 12, behavior: 'smooth' })
    activeMsgId.value = id
  }
}

// 跳到最新（点击底部按钮 / 发送 / 切换会话时）
function jumpToBottom() {
  nearBottom.value = true
  scrollToBottom(true)
}

// 导航轨：每个消息一个 tick；用户提问用实心，AI 用空心
const railItems = computed(() => messages.value.map((m) => ({ id: m.id, role: m.role, content: m.content || '' })))
function railTop(i) {
  const n = railItems.value.length
  if (n <= 1) return 'calc(100% - 18px)'
  const pad = 14
  const ratio = i / (n - 1)
  return `calc(${pad}px + ${ratio} * (100% - ${pad * 2}px))`
}
function railTooltip(item) {
  const text = (item.content || '').replace(/\s+/g, ' ').slice(0, 60)
  return item.role === 'user' ? '你：' + text : 'AI：' + text
}

// force=true 时无视状态强制滚到底（发送/切换会话/初次加载场景）
function scrollToBottom(force = false) {
  if (!force && !nearBottom.value) return
  nextTick(() => {
    if (scrollRef.value) scrollRef.value.scrollTop = scrollRef.value.scrollHeight
  })
}

// 发送 / 重新生成（overrideContent 为建议 chip 或重新生成的原文）
async function onSend(overrideContent) {
  // overrideContent 可能是事件对象（@click 默认传参），仅当其为字符串时才使用
  const raw = typeof overrideContent === 'string' ? overrideContent : input.value
  const content = (raw || '').trim()
  if (!content || streaming.value || !props.jobId) return

  // 确保存在可用会话：新任务首次追问时还没有会话，需先自动新建，否则会静默无法发送
  let sessionId = activeSessionId.value
  if (!sessionId) {
    try {
      const res = await chatApi.createSession(props.jobId, { title: '新对话' })
      sessionId = res.id
      activeSessionId.value = sessionId
      await loadSessions(sessionId)
    } catch (e) {
      ElMessage.error('创建对话失败: ' + (e?.message || '未知错误'))
      return
    }
  }

  const uid = 'tmp-u-' + ++tmpSeq
  const aid = 'tmp-a-' + tmpSeq
  // 本地消息先打自己的时间戳（服务端 created_at 要等重载后才有）
  const nowIso = new Date().toISOString()
  messages.value.push({ id: uid, role: 'user', content, thinking: '', sources: [], token_usage: null, created_at: nowIso })
  const assistant = { id: aid, role: 'assistant', content: '', thinking: '', sources: [], search: null, token_usage: null, created_at: nowIso }
  messages.value.push(assistant)
  activeMsg.value = assistant
  streaming.value = true
  input.value = ''
  nearBottom.value = true
  scrollToBottom(true)

  abortCtrl = new AbortController()
  chatApi.streamChat(
    props.jobId,
    sessionId,
    { content, model: selectedModel.value, searchMode: searchMode.value },
    {
      signal: abortCtrl.signal,
      onEvent: (ev) => {
        if (ev.type === 'thinking') {
          assistant.thinking += ev.delta || ''
        } else if (ev.type === 'content') {
          assistant.content += ev.delta || ''
        } else if (ev.type === 'search') {
          setSearchState(assistant, ev)
        } else if (ev.type === 'sources') {
          assistant.sources = ev.sources || []
        } else if (ev.type === 'done') {
          if (ev.user_id != null) {
            const u = messages.value.find((m) => m.id === uid)
            if (u) u.id = ev.user_id
          }
          if (ev.assistant_id != null) assistant.id = ev.assistant_id
          if (ev.token_usage) assistant.token_usage = ev.token_usage
        } else if (ev.type === 'error') {
          assistant.content = (assistant.content || '') + '\n\n**[错误]** ' + (ev.message || '生成出错')
        }
        scrollToBottom()
      },
      onEnd: (aborted) => {
        streaming.value = false
        activeMsg.value = null
        abortCtrl = null
        scrollToBottom()
        refreshSessionMeta()
        if (aborted && !disposed) {
          // 后端在断连时已落库，稍后重载以获取真实 id 与最终内容（卸载后不再触发）
          clearTimeout(historyTimer)
          historyTimer = setTimeout(() => {
            historyTimer = null
            loadHistory()
          }, 300)
        }
      },
      onError: (err) => {
        streaming.value = false
        activeMsg.value = null
        abortCtrl = null
        assistant.content = (assistant.content || '') + '\n\n**[错误]** ' + (err?.message || '网络错误')
        ElMessage.error('追问失败: ' + (err?.message || '未知错误'))
        scrollToBottom()
      },
    },
  )
}

function onStop() {
  if (abortCtrl) abortCtrl.abort()
}

// 联网检索状态写入该条消息的响应式代理（直接改局部裸对象不会触发视图更新）
function setSearchState(assistant, ev) {
  let state
  if (ev.status === 'ok') {
    state = { status: 'ok', provider: ev.provider || '', count: ev.count || 0, mode: ev.mode || 'strict' }
  } else if (ev.status === 'failed') {
    state = { status: 'failed', message: ev.message || '未知原因', mode: ev.mode || '' }
  } else {
    state = { status: 'searching', mode: ev.mode || 'strict' }
  }
  const live = messages.value.find((m) => m.id === assistant.id)
  if (live) live.search = state
  else assistant.search = state
}

// 联网检索状态文案：纯函数在 utils/chatExport（searchStateLabel），此处沿用旧名供模板调用
function searchLabel(state) {
  return searchStateLabel(state)
}

// ===== 复制（格式化见 utils/chatExport.js 纯函数；复制反馈走 utils/clipboard 单点） =====

function msgTime(msg) {
  return formatChatTime(msg.created_at)
}

function onCopyAi(msg, mode) {
  return copyText(buildAiText(msg, mode === 'with-sources'))
}

function copyConversation() {
  if (!messages.value.length) {
    ElMessage.warning('当前对话还没有消息')
    return
  }
  const title = sessions.value.find((s) => s.id === activeSessionId.value)?.title || '追问对话'
  return copyText(buildConversationMarkdown(messages.value, title))
}

// 删除该提问及其回答（整轮删除；只删提问会留下一段孤儿的 AI 回答）
async function onDeleteTurn(msg) {
  if (streaming.value) return
  try {
    await ElMessageBox.confirm('删除该提问及其回答？', '删除整轮对话', { type: 'warning' })
  } catch {
    return
  }
  try {
    await chatApi.deleteFrom(props.jobId, msg.id)
    await loadHistory()
  } catch (e) {
    ElMessage.error('删除失败: ' + (e?.message || '未知错误'))
  }
}

// 编辑：删除该条（含）之后所有消息，并把内容填入输入框，等待重新发送
async function onEdit(msg) {
  if (streaming.value) return
  try {
    await chatApi.deleteFrom(props.jobId, msg.id)
    input.value = msg.content
    await loadHistory()
    ElMessage.info('已回到该提问点，修改后重新发送即可')
  } catch (e) {
    ElMessage.error('编辑失败: ' + (e?.message || '未知错误'))
  }
}

// 重新生成：删除该条（含）之后，用相同内容重发
async function onRegenerate(msg) {
  if (streaming.value) return
  try {
    await chatApi.deleteFrom(props.jobId, msg.id)
    await loadHistory()
    onSend(msg.content)
  } catch (e) {
    ElMessage.error('重新生成失败: ' + (e?.message || '未知错误'))
  }
}

async function onDelete(msg) {
  if (streaming.value) return
  try {
    await chatApi.deleteMessage(props.jobId, msg.id)
    await loadHistory()
  } catch (e) {
    ElMessage.error('删除失败: ' + (e?.message || '未知错误'))
  }
}

async function onClear() {
  if (streaming.value || !activeSessionId.value) return
  try {
    await ElMessageBox.confirm('清空当前对话的全部消息？', '清空对话', { type: 'warning' })
  } catch {
    return
  }
  try {
    await chatApi.clearSessionMessages(props.jobId, activeSessionId.value)
    messages.value = []
    await refreshSessionMeta()
    ElMessage.success('当前对话已清空')
  } catch (e) {
    if (!isDialogCancel(e) && e?.message) ElMessage.error('清空失败: ' + e.message)
  }
}

onMounted(async () => {
  await ensureModels()
  await loadSessions()
})

// 组件卸载：终止流式请求 + 清理延时重载，避免对已卸载组件写状态
onUnmounted(() => {
  disposed = true
  if (abortCtrl) {
    abortCtrl.abort()
    abortCtrl = null
  }
  clearTimeout(historyTimer)
  historyTimer = null
})

watch(
  () => props.jobId,
  async (newId) => {
    if (newId) {
      // 先终止旧任务的流式请求，避免跨任务串流
      if (abortCtrl) {
        abortCtrl.abort()
        abortCtrl = null
      }
      clearTimeout(historyTimer)
      historyTimer = null
      streaming.value = false
      activeMsg.value = null
      await loadSessions()
    }
  },
)
</script>

<style scoped>
/* ============ 面板容器：继承全局 app-card 玻璃材质 ============ */
.chat-panel {
  margin-top: 16px;
  display: flex;
  flex-direction: column;
}

/* ============ 单行纤细头部 ============ */
.cp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 9px 12px 9px 16px;
  border-bottom: 1px solid var(--app-glass-border, var(--app-border));
  flex-wrap: wrap;
}
.cp-head-left,
.cp-head-right {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.cp-brand {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 650;
  font-size: 14px;
  color: var(--app-text-heading, var(--app-text));
  margin-right: 4px;
  flex-shrink: 0;
}
.cp-brand .el-icon {
  color: var(--app-accent-1);
}

/* 幽灵下拉：透明无边框，hover 才起底色（头部与会话/模型选择共用） */
.cp-ghost-select {
  --el-font-size-base: 12.5px;
}
.cp-ghost-select :deep(.el-select__wrapper) {
  background: transparent;
  box-shadow: none !important;
  min-height: 28px;
  padding: 0 8px;
  border-radius: 8px;
  font-size: 12.5px;
}
.cp-ghost-select :deep(.el-select__wrapper:hover) {
  background: var(--app-hover-bg);
}
.cp-session-select {
  max-width: 240px;
  min-width: 120px;
}
.cp-session-select :deep(.el-select__selected-item) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cp-model-select {
  width: 176px;
}
.session-opt {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  gap: 8px;
}
.session-opt-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-opt-count {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--app-text-secondary);
  background: var(--app-chip-bg);
  border-radius: 8px;
  padding: 0 6px;
}

/* 图标按钮（头部 ⋯ 与消息操作共用） */
.cp-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 1px;
  width: 26px;
  height: 26px;
  padding: 0;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--app-text-secondary);
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
}
.cp-icon-btn:hover:not(:disabled) {
  background: var(--app-hover-bg);
  color: var(--app-text);
}
.cp-icon-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.cp-icon-btn.cp-danger:hover:not(:disabled) {
  color: #f87171;
}

/* ============ 消息区 ============ */
.cp-body {
  position: relative;
}
.cp-scroll-wrap {
  position: relative;
}
.cp-scroll {
  max-height: min(680px, 76vh);
  min-height: 220px;
  overflow-y: auto;
  padding: 18px 20px 14px;
  scrollbar-gutter: stable;
}
.cp-thread {
  max-width: 860px;
  margin: 0 auto;
}

/* ---- 空态：欢迎 + 建议 chips ---- */
.cp-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 44px 16px 36px;
}
.cp-empty-glyph {
  width: 52px;
  height: 52px;
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--app-accent-1);
  background: rgba(var(--app-accent-1-rgb), 0.12);
  border: 1px solid rgba(var(--app-accent-1-rgb), 0.18);
  margin-bottom: 14px;
}
.cp-empty-title {
  font-size: 16px;
  font-weight: 650;
  color: var(--app-text-heading, var(--app-text));
}
.cp-empty-sub {
  font-size: 12.5px;
  color: var(--app-text-secondary);
  margin-top: 6px;
}
.cp-suggestions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin-top: 18px;
}
.cp-chip {
  border: 1px solid var(--app-chip-border, var(--app-border));
  background: var(--app-chip-bg, transparent);
  color: var(--app-chip-text, var(--app-text-secondary));
  font-size: 12.5px;
  padding: 7px 14px;
  border-radius: 999px;
  cursor: pointer;
  transition: background 0.12s, border-color 0.12s, color 0.12s;
}
.cp-chip:hover {
  background: var(--app-chip-hover-bg, var(--app-hover-bg));
  border-color: rgba(var(--app-accent-1-rgb), 0.45);
  color: var(--app-text);
}

/* ---- 消息行（保留 .chat-row/data-mid：onScroll 导航轨跟踪依赖） ---- */
.chat-row {
  margin: 14px 0;
  display: flex;
}
.row-user {
  justify-content: flex-end;
}
.row-ai {
  justify-content: flex-start;
}

/* 用户消息：弱强调色右对齐气泡 */
.cp-user {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  max-width: min(78%, 560px);
}
.cp-user-bubble {
  background: rgba(var(--app-accent-1-rgb), 0.14);
  border: 1px solid rgba(var(--app-accent-1-rgb), 0.2);
  color: var(--app-text);
  border-radius: 16px 16px 4px 16px;
  padding: 9px 14px;
  font-size: 14px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

/* AI 回答：头像列 + 内容列，正文平铺（无气泡/无描边） */
.cp-ai {
  display: flex;
  gap: 10px;
  width: 100%;
  min-width: 0;
}
.cp-ai-glyph {
  flex-shrink: 0;
  width: 26px;
  height: 26px;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: #fff;
  background: linear-gradient(135deg, var(--app-accent-1), var(--app-accent-2));
  margin-top: 1px;
}
.cp-ai-main {
  flex: 1;
  min-width: 0;
}
.cp-ai-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
  min-height: 22px;
}
.cp-time {
  font-size: 11.5px;
  color: var(--app-text-placeholder, var(--app-text-secondary));
}
.cp-tokens {
  font-size: 11.5px;
  color: var(--app-text-placeholder, var(--app-text-secondary));
}

/* 消息操作：hover / 激活行显形（DOM 在正文之后，符合可访问性顺序要求） */
.cp-msg-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.15s;
}
.chat-row:hover .cp-msg-actions,
.chat-row.row-active .cp-msg-actions,
.cp-msg-actions:focus-within {
  opacity: 1;
}
.cp-caret {
  transition: transform 0.15s;
}
.cp-caret.is-open {
  transform: rotate(180deg);
}

/* 思考过程：折叠面板 */
.cp-thinking {
  border: 1px solid var(--app-border);
  background: var(--app-thinking-bg, rgba(var(--app-accent-1-rgb), 0.06));
  border-radius: 10px;
  padding: 7px 11px;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--app-text-secondary);
}
.cp-thinking summary {
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--app-accent-1);
  user-select: none;
  list-style: none;
}
.cp-thinking summary::-webkit-details-marker {
  display: none;
}
.cp-thinking-text {
  margin: 7px 0 2px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.6;
  color: var(--app-thinking-text, var(--app-text-secondary));
  max-height: 220px;
  overflow-y: auto;
  font-family: inherit;
}

/* 正文 */
.cp-md {
  font-size: 14px;
  line-height: 1.7;
  color: var(--app-text);
  word-break: break-word;
}
/* 流式尾部光标（挂在最后一个块级元素后，行内显示） */
.cp-streaming .cp-md > :last-child::after {
  content: '▍';
  margin-left: 2px;
  color: var(--app-accent-1);
  animation: cp-caret 0.9s steps(2) infinite;
}
@keyframes cp-caret {
  50% { opacity: 0; }
}

/* 生成中占位 */
.cp-stream-hint {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  color: var(--app-accent-1);
  padding: 4px 0;
}
.cp-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--app-accent-1);
  animation: cp-pulse 1.1s ease-in-out infinite;
}
@keyframes cp-pulse {
  0%, 100% { opacity: 0.25; transform: scale(0.85); }
  50% { opacity: 1; transform: scale(1); }
}

/* ---- 联网检索状态行 ---- */
.cp-search-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--app-text-secondary);
  width: fit-content;
  max-width: 100%;
}
.cp-search-text {
  word-break: break-word;
}
button.cp-search-row {
  border: 1px solid transparent;
  background: transparent;
  padding: 4px 9px;
  margin-left: -9px;
  border-radius: 8px;
  cursor: pointer;
  text-align: left;
}
button.cp-search-row:hover {
  background: var(--app-hover-bg);
  border-color: var(--app-border);
}
.cp-search-failed {
  color: #fbbf24;
}
.cp-search-failed .el-icon {
  flex-shrink: 0;
}

/* ---- 引用来源：两列卡片栅格 ---- */
.cp-sources {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px;
  margin-top: 8px;
}
.cp-source-card {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid var(--app-chip-border, var(--app-border));
  background: var(--app-chip-bg, transparent);
  text-decoration: none;
  transition: border-color 0.12s, background 0.12s, transform 0.12s;
  min-width: 0;
}
.cp-source-card:hover {
  border-color: rgba(var(--app-accent-1-rgb), 0.5);
  background: var(--app-chip-hover-bg, var(--app-hover-bg));
  transform: translateY(-1px);
}
.cp-source-idx {
  flex-shrink: 0;
  width: 17px;
  height: 17px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10.5px;
  font-weight: 650;
  color: var(--app-accent-1);
  background: rgba(var(--app-accent-1-rgb), 0.13);
  margin-top: 1px;
}
.cp-source-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.cp-source-title {
  font-size: 12.5px;
  line-height: 1.45;
  color: var(--app-text);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.cp-source-host {
  font-size: 11px;
  color: var(--app-text-placeholder, var(--app-text-secondary));
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---- 跳到最新浮动按钮 ---- */
.cp-jump {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  bottom: 14px;
  z-index: 5;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 13px;
  border-radius: 999px;
  border: 1px solid var(--app-glass-border, var(--app-border));
  background: rgba(var(--app-dim-color), 0.92);
  color: var(--app-text);
  font-size: 12px;
  cursor: pointer;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.22);
  transition: border-color 0.12s, color 0.12s;
}
.cp-jump:hover {
  border-color: rgba(var(--app-accent-1-rgb), 0.55);
  color: var(--app-accent-1);
}

/* ---- 右侧导航轨（保留交互，配色走 token） ---- */
.chat-rail {
  position: absolute;
  top: 0;
  right: 2px;
  bottom: 0;
  width: 16px;
  pointer-events: none;
}
.rail-tick {
  position: absolute;
  right: 4px;
  width: 7px;
  height: 7px;
  margin-top: -4px;
  border-radius: 50%;
  background: var(--app-scrollbar-thumb);
  border: 1px solid transparent;
  cursor: pointer;
  pointer-events: auto;
  transition: transform 0.12s, background 0.12s;
}
.rail-tick-user {
  background: rgba(var(--app-accent-1-rgb), 0.75);
}
.rail-tick-active {
  background: var(--app-accent-1);
  transform: scale(1.45);
}
.rail-tick:hover {
  transform: scale(1.55);
}
.rail-tick .rail-tip {
  position: absolute;
  right: 15px;
  top: 50%;
  transform: translateY(-50%);
  max-width: 220px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding: 4px 10px;
  border-radius: 6px;
  background: var(--app-code-bg, #1e1e1e);
  color: var(--app-code-text, #e6e6e6);
  border: 1px solid var(--app-border);
  font-size: 12px;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.12s;
  z-index: 20;
}
.rail-tick:hover .rail-tip {
  opacity: 1;
}
.rail-bottom {
  position: absolute;
  right: 0;
  bottom: 6px;
  width: 18px;
  height: 18px;
  padding: 0;
  border: 1px solid var(--app-border);
  border-radius: 50%;
  background: var(--app-chip-bg, transparent);
  color: var(--app-text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: auto;
}
.rail-bottom .el-icon {
  font-size: 11px;
}
.rail-bottom:hover {
  color: var(--app-accent-1);
  border-color: rgba(var(--app-accent-1-rgb), 0.5);
}

/* ============ Composer 输入舱 ============ */
.cp-composer {
  margin: 4px 14px 14px;
  border: 1px solid var(--app-input-border, var(--app-border));
  border-radius: 16px;
  background: var(--app-input-bg);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.cp-composer:focus-within {
  border-color: rgba(var(--app-accent-1-rgb), 0.55);
  box-shadow: 0 0 0 3px rgba(var(--app-accent-1-rgb), 0.1);
}
.cp-composer :deep(.el-textarea__inner) {
  background: transparent;
  border: none;
  box-shadow: none !important;
  padding: 11px 14px 2px;
  font-size: 14px;
  line-height: 1.6;
  color: var(--app-text);
}
.cp-composer :deep(.el-textarea__inner::placeholder) {
  color: var(--app-text-placeholder, var(--app-text-secondary));
}
.cp-composer-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 5px 8px 8px 10px;
}
.cp-composer-left,
.cp-composer-right {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

/* 联网模式 pill：内嵌无边框 select */
.cp-mode-pill {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 6px 2px 9px;
  border-radius: 999px;
  border: 1px solid transparent;
  color: var(--app-text-secondary);
  transition: background 0.12s, border-color 0.12s, color 0.12s;
}
.cp-mode-pill .el-icon {
  flex-shrink: 0;
}
.cp-mode-pill.is-on {
  background: rgba(var(--app-accent-1-rgb), 0.12);
  border-color: rgba(var(--app-accent-1-rgb), 0.3);
  color: var(--app-accent-1);
}
.cp-mode-select {
  width: 96px;
}
.cp-mode-select :deep(.el-select__wrapper) {
  background: transparent;
  box-shadow: none !important;
  min-height: 24px;
  padding: 0 2px;
  font-size: 12.5px;
  color: inherit;
}
.cp-mode-pill.is-on .cp-mode-select :deep(.el-select__wrapper) {
  color: var(--app-accent-1);
}
.cp-mode-select :deep(.el-select__selected-item),
.cp-mode-select :deep(.el-select__placeholder) {
  color: inherit;
}

/* 思考开关 pill */
.cp-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--app-text-secondary);
  font-size: 12.5px;
  padding: 4px 10px;
  border-radius: 999px;
  cursor: pointer;
  transition: background 0.12s, border-color 0.12s, color 0.12s;
}
.cp-toggle:hover:not(:disabled) {
  background: var(--app-hover-bg);
  color: var(--app-text);
}
.cp-toggle.is-on {
  background: rgba(var(--app-accent-1-rgb), 0.12);
  border-color: rgba(var(--app-accent-1-rgb), 0.3);
  color: var(--app-accent-1);
}
.cp-toggle:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.cp-hint {
  font-size: 11.5px;
  color: var(--app-text-placeholder, var(--app-text-secondary));
  white-space: nowrap;
}

/* 发送 / 停止：圆形按钮 */
.cp-send {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  background: linear-gradient(135deg, var(--app-accent-1), var(--app-accent-2));
  box-shadow: 0 2px 8px rgba(var(--app-accent-1-rgb), 0.35);
  cursor: pointer;
  transition: opacity 0.12s, transform 0.12s, box-shadow 0.12s;
}
.cp-send:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(var(--app-accent-1-rgb), 0.45);
}
.cp-send:disabled {
  opacity: 0.35;
  cursor: not-allowed;
  box-shadow: none;
}
.cp-send.cp-stop {
  background: transparent;
  border: 1.5px solid var(--app-accent-1);
  color: var(--app-accent-1);
  box-shadow: none;
  animation: cp-pulse 1.4s ease-in-out infinite;
}

/* 减少动态效果偏好 */
@media (prefers-reduced-motion: reduce) {
  .cp-streaming .cp-md > :last-child::after,
  .cp-dot,
  .cp-send.cp-stop {
    animation: none;
  }
  .cp-source-card,
  .cp-send {
    transition: none;
  }
}
</style>
