<template>
  <el-card shadow="never" class="chat-panel">
    <template #header>
      <div class="chat-header">
        <span class="chat-title">
          <el-icon style="margin-right: 4px; vertical-align: -2px;"><MessageCircle /></el-icon>追问
        </span>
        <div class="chat-header-tools">
          <el-tooltip content="开启后，追问时会先通过智谱API联网搜索（与所选模型无关），结果带来源引用" placement="top">
            <span class="search-switch">
              <el-switch v-model="enableSearch" size="small" active-text="联网" inline-prompt />
            </span>
          </el-tooltip>
          <el-checkbox v-model="showThinking" value="show-thinking" size="small" class="think-toggle">显示思考过程</el-checkbox>
          <el-button v-if="messages.length" size="small" text type="danger" @click="onClear">清空当前</el-button>
        </div>
      </div>
    </template>

    <!-- 工具栏：模型选择 -->
    <div class="chat-toolbar">
      <span class="toolbar-label">模型:</span>
      <el-select v-model="selectedModel" size="small" class="model-select" placeholder="默认(沿用任务模型)">
        <el-option label="默认(沿用任务模型)" value="" />
        <el-option
          v-for="m in modelStore.models"
          :key="m.key"
          :value="m.key"
          :label="m.name || m.key"
        />
      </el-select>
      <span v-if="modelStore.loading" class="model-hint">模型加载中…</span>
    </div>

    <!-- 会话栏：切换 / 新建 / 重命名 / 删除 -->
    <div class="session-bar">
      <el-select
        v-model="activeSessionId"
        size="small"
        class="session-select"
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
      <el-button size="small" :icon="Plus" @click="createSession" :disabled="streaming">新建</el-button>
      <el-button
        size="small" text :icon="Pencil" title="重命名当前对话"
        :disabled="!activeSessionId || streaming" @click="renameSession"
      />
      <el-button
        size="small" text type="danger" :icon="Trash2" title="删除当前对话"
        :disabled="!activeSessionId || streaming" @click="deleteSession"
      />
    </div>

    <!-- 消息区 + 右侧导航轨（独立一列，不与输入区重叠） -->
    <div class="chat-body">
      <div ref="scrollRef" class="chat-messages" @scroll="onScroll">
      <el-empty v-if="messages.length === 0 && !streaming" description="基于分析结果继续提问，AI 会结合原文与结果回答" :image-size="60" />

      <div
        v-for="msg in messages"
        :key="msg.id"
        :data-mid="msg.id"
        class="chat-row"
        :class="[msg.role === 'user' ? 'row-user' : 'row-ai', { 'row-active': msg.id === activeMsgId }]"
      >
        <!-- 用户消息 -->
        <div v-if="msg.role === 'user'" class="bubble bubble-user">
          <div class="bubble-head">
            <span class="role-tag user-tag">你</span>
            <el-button size="small" text class="edit-btn" @click="onEdit(msg)">编辑</el-button>
          </div>
          <div class="bubble-body user-text">{{ msg.content }}</div>
        </div>

        <!-- AI 消息 -->
        <div v-else class="bubble bubble-ai">
          <div class="bubble-head">
            <span class="role-tag ai-tag">AI</span>
            <span v-if="msg.token_usage && (msg.token_usage.input || msg.token_usage.output)" class="token-tag">
              {{ (msg.token_usage.input || 0) + (msg.token_usage.output || 0) }} tokens
            </span>
            <span class="head-actions">
              <el-button size="small" text @click="onRegenerate(msg)">重新生成</el-button>
              <el-button size="small" text type="danger" @click="onDelete(msg)">删除</el-button>
            </span>
          </div>

          <!-- 思考过程 -->
          <div v-if="showThinking && msg.thinking" class="thinking-box">
            <div class="thinking-label"><el-icon :size="13" style="vertical-align: -2px;"><Sparkles /></el-icon> 思考过程</div>
            <pre class="thinking-text">{{ msg.thinking }}</pre>
          </div>

          <!-- 正文 -->
          <div
            v-if="msg.content"
            class="bubble-body markdown-body"
            v-html="renderMarkdown(msg.content)"
          />
          <div v-else-if="msg === activeMsg" class="bubble-body streaming-placeholder">
            <span class="dot-flashing">{{ msg.thinking && !msg.content ? '思考中' : '生成中' }}</span>
          </div>

          <!-- 引用来源 -->
          <div v-if="msg.sources && msg.sources.length" class="sources-box">
            <div class="sources-label"><el-icon :size="13" style="vertical-align: -2px;"><Link /></el-icon> 引用来源 ({{ msg.sources.length }})</div>
            <div v-for="(s, i) in msg.sources" :key="i" class="source-item">
              <span class="source-index">{{ i + 1 }}.</span>
              <a :href="s.url" target="_blank" rel="noopener noreferrer" class="source-link">
                {{ s.title || s.url }}
              </a>
            </div>
          </div>
        </div>
      </div>

      <div v-if="streaming && !activeMsg" class="streaming-hint">AI 正在思考…</div>
      </div>

      <!-- 右侧会话记录导航轨（Cherry Studio 风格）：点击跳转、悬停显示提问 -->
      <div v-if="messages.length" class="chat-rail">
        <div
          v-for="(item, i) in railItems"
          :key="item.id"
          class="rail-tick"
          :class="{ 'rail-tick-user': item.role === 'user', 'rail-tick-active': item.id === activeMsgId }"
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

    <!-- 输入区 -->
    <div class="chat-input-area">
      <el-input
        v-model="input"
        type="textarea"
        :autosize="{ minRows: 1, maxRows: 10 }"
        resize="none"
        placeholder="输入追问内容…（Enter 发送，Shift+Enter 换行）"
        :disabled="streaming"
        @keydown.enter.exact.prevent="onSend()"
      />
      <div class="input-actions">
        <el-button
          v-if="!streaming"
          type="primary"
          size="small"
          :disabled="!input.trim()"
          @click="onSend()"
        >发送</el-button>
        <el-button
          v-else
          type="warning"
          size="small"
          @click="onStop"
        >停止</el-button>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { MessageCircle, Plus, Pencil, Trash2, ArrowDown, Sparkles, Link } from 'lucide-vue-next'
import { ElMessage, ElMessageBox } from 'element-plus'
import { chatApi } from '../utils/api'
import { renderMarkdown } from '../utils/markdown'
import { useModelStore } from '../stores/modelStore'

const props = defineProps({
  jobId: { type: String, required: true },
})

const modelStore = useModelStore()

const messages = ref([])
const input = ref('')
const streaming = ref(false)
const enableSearch = ref(false)
const showThinking = ref(false)
const selectedModel = ref('')
const scrollRef = ref(null)
const nearBottom = ref(true)
const activeMsgId = ref(null)
const activeMsg = ref(null)
let abortCtrl = null
let tmpSeq = 0

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
    if (e !== 'cancel' && e?.message) ElMessage.error('重命名失败: ' + e.message)
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

// 发送 / 重新生成
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
  messages.value.push({ id: uid, role: 'user', content, thinking: '', sources: [], token_usage: null })
  const assistant = { id: aid, role: 'assistant', content: '', thinking: '', sources: [], token_usage: null }
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
    { content, model: selectedModel.value, enableSearch: enableSearch.value },
    {
      signal: abortCtrl.signal,
      onEvent: (ev) => {
        if (ev.type === 'thinking') {
          assistant.thinking += ev.delta || ''
        } else if (ev.type === 'content') {
          assistant.content += ev.delta || ''
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
        if (aborted) {
          // 后端在断连时已落库，稍后重载以获取真实 id 与最终内容
          setTimeout(loadHistory, 300)
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
    await chatApi.clearSessionMessages(props.jobId, activeSessionId.value)
    messages.value = []
    await refreshSessionMeta()
    ElMessage.success('当前对话已清空')
  } catch (e) {
    ElMessage.error('清空失败: ' + (e?.message || '未知错误'))
  }
}

onMounted(async () => {
  await ensureModels()
  await loadSessions()
})

watch(
  () => props.jobId,
  async (newId) => {
    if (newId) {
      streaming.value = false
      activeMsg.value = null
      abortCtrl = null
      await loadSessions()
    }
  },
)
</script>

<style scoped>
.chat-panel {
  margin-top: 16px;
}
.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.chat-title {
  font-weight: 600;
  display: inline-flex;
  align-items: center;
}
.chat-header-tools {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.think-toggle {
  margin-right: 4px;
}
.chat-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
.toolbar-label {
  font-size: 12px;
  color: #aaa;
}
.model-select {
  width: 220px;
}
.model-hint {
  font-size: 12px;
  color: #909399;
}
.session-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
.session-select {
  flex: 1;
  min-width: 140px;
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
  color: #909399;
  background: var(--el-fill-color-light, rgba(255, 255, 255, 0.08));
  border-radius: 8px;
  padding: 0 6px;
}
.chat-body {
  display: flex;
  align-items: stretch;
  gap: 4px;
  margin-bottom: 12px;
  min-height: 200px;
}
.chat-messages {
  flex: 1;
  min-width: 0;
  max-height: min(720px, 78vh);
  min-height: 200px;
  overflow-y: auto;
  padding-right: 4px;
}
.chat-rail {
  position: relative;
  flex: 0 0 18px;
  width: 18px;
  align-self: stretch;
  pointer-events: none;
}
.rail-tick {
  position: absolute;
  right: 5px;
  width: 8px;
  height: 8px;
  margin-top: -4px;
  border-radius: 50%;
  background: var(--app-scrollbar-thumb, #c0c4cc);
  border: 1px solid transparent;
  cursor: pointer;
  pointer-events: auto;
  transition: transform 0.12s, background 0.12s;
}
.rail-tick-user {
  background: var(--app-accent-1);
}
.rail-tick-active {
  background: var(--app-accent-2);
  border-color: var(--app-accent-1);
  transform: scale(1.5);
}
.rail-tick:hover {
  transform: scale(1.6);
}
.rail-tick .rail-tip {
  position: absolute;
  right: 16px;
  top: 50%;
  transform: translateY(-50%);
  max-width: 220px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding: 4px 10px;
  border-radius: 6px;
  background: #1e1e1e;
  color: #e6e6e6;
  border: 1px solid #333;
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
  right: 1px;
  bottom: 6px;
  width: 16px;
  height: 16px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: rgba(var(--app-accent-1-rgb), 0.85);
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: auto;
}
.rail-bottom .el-icon {
  font-size: 12px;
}
.rail-bottom:hover {
  background: var(--app-accent-1);
}
.chat-row {
  margin: 10px 0;
  display: flex;
}
.row-user {
  justify-content: flex-end;
}
.row-ai {
  justify-content: flex-start;
}
.bubble {
  max-width: 85%;
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.65;
}
.bubble-user {
  background: var(--app-accent-1);
  color: #fff;
}
.bubble-ai {
  background: rgba(255, 255, 255, 0.07);
  color: #e6e6e6;
  border-left: 3px solid var(--app-accent-1);
}
.bubble-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}
.role-tag {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 10px;
  font-weight: 600;
}
.user-tag {
  background: rgba(255, 255, 255, 0.25);
  color: #fff;
}
.ai-tag {
  background: var(--app-accent-1);
  color: #fff;
}
.token-tag {
  font-size: 11px;
  color: #909399;
}
.head-actions {
  margin-left: auto;
  display: flex;
  gap: 2px;
}
.edit-btn {
  margin-left: auto;
}
.user-text {
  white-space: pre-wrap;
  word-break: break-word;
}
.thinking-box {
  background: rgba(var(--app-accent-1-rgb), 0.08);
  border: 1px dashed rgba(var(--app-accent-1-rgb), 0.4);
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 8px;
}
.thinking-label {
  font-size: 12px;
  color: var(--app-accent-1);
  margin-bottom: 4px;
}
.thinking-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--app-thinking-text);
  max-height: 220px;
  overflow-y: auto;
}
.streaming-placeholder {
  color: var(--app-accent-1);
  font-style: italic;
}
.dot-flashing::after {
  content: '…';
  animation: blink 1s steps(4, start) infinite;
}
@keyframes blink {
  to { visibility: hidden; }
}
.sources-box {
  margin-top: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
  padding-top: 6px;
}
.sources-label {
  font-size: 12px;
  color: #94a3b8;
  margin-bottom: 4px;
}
.source-item {
  font-size: 12.5px;
  margin: 2px 0;
  display: flex;
  gap: 4px;
}
.source-index {
  color: var(--app-accent-1);
}
.source-link {
  color: #60a5fa;
  text-decoration: none;
  word-break: break-all;
}
.source-link:hover {
  text-decoration: underline;
}
.streaming-hint {
  text-align: center;
  color: var(--app-accent-1);
  font-size: 13px;
  padding: 8px;
}
.chat-input-area {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
.chat-input-area .el-textarea {
  flex: 1;
}
.input-actions {
  flex-shrink: 0;
}
</style>
