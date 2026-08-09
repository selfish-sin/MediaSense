<script setup>
/**
 * 完全自定义提示词面板（SplitPromptPanel 的右 pane 内容）
 * - 顶部：状态指示条（启用中 → 橙色条 + 关闭按钮）
 * - 中部：纯自定义 prompt textarea（即"空白"那个，不参与 7 维度拼装）
 * - 「应用此自定义提示词」按钮 → settings.setFullOverride + toggleFullOverride(true)
 * - 「+ 存入提示词库」→ 弹框输入名称，保存为 template_type='full_override' 模板
 * - 下方：提示词库卡片列表（templatesApi 过滤 full_override），点击即灌入 textarea 并应用
 *   - 卡片右侧：编辑（路由跳转 TemplatesView）/ 删除
 */
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, X, Plus, Trash2, Pencil } from 'lucide-vue-next'
import { useSettingsStore } from '../stores/settingsStore'
import { templatesApi } from '../utils/api'

const settings = useSettingsStore()
const router = useRouter()

// 本地编辑文本（独立于 settings.fullOverride，避免双向污染；点"应用"才写入 store）
const promptText = ref('')
const library = ref([])
const libraryLoading = ref(false)
const saveDialogVisible = ref(false)
const saveForm = ref({ name: '', desc: '' })
const saving = ref(false)

// 状态：是否已启用
const isActive = computed(() => settings.useFullOverride && !!settings.fullOverride)

// 进入面板时，若已启用则把当前 fullOverride 反填到 textarea
function syncFromStore() {
  if (isActive.value) promptText.value = settings.fullOverride
}
onMounted(() => {
  syncFromStore()
  loadLibrary()
})

async function loadLibrary() {
  libraryLoading.value = true
  try {
    const res = await templatesApi.list()
    library.value = (res.templates || []).filter(t => t.template_type === 'full_override')
  } catch (err) {
    ElMessage.error('加载提示词库失败: ' + (err.message || err))
  } finally {
    libraryLoading.value = false
  }
}

// 应用当前 textarea 为 fullOverride
function applyCurrent() {
  if (!promptText.value.trim()) {
    ElMessage.warning('请输入自定义提示词')
    return
  }
  settings.setFullOverride(promptText.value)
  settings.toggleFullOverride(true)
  ElMessage.success('已启用自定义提示词（覆盖一切维度拼装）')
}

// 关闭自定义，回到维度拼装
function disableOverride() {
  settings.setFullOverride('')
  settings.toggleFullOverride(false)
  ElMessage.info('已关闭自定义提示词，回到维度拼装')
}

// 点击库卡片：灌入 textarea + 立即应用
function applyCard(item) {
  promptText.value = item.prompt
  settings.setFullOverride(item.prompt)
  settings.toggleFullOverride(true)
  ElMessage.success(`已应用「${item.name}」`)
}

// 打开"存入库"弹窗
function openSaveDialog() {
  if (!promptText.value.trim()) {
    ElMessage.warning('提示词为空，无法保存')
    return
  }
  saveForm.value = { name: '', desc: '' }
  saveDialogVisible.value = true
}

// 保存到库
async function saveToLibrary() {
  if (!saveForm.value.name.trim()) {
    ElMessage.warning('请输入名称')
    return
  }
  saving.value = true
  try {
    await templatesApi.create({
      name: saveForm.value.name,
      prompt: promptText.value,
      template_type: 'full_override',
      options: { desc: saveForm.value.desc },
    })
    ElMessage.success('已存入提示词库')
    saveDialogVisible.value = false
    await loadLibrary()
  } catch (err) {
    ElMessage.error('保存失败: ' + (err.message || err))
  } finally {
    saving.value = false
  }
}

async function deleteCard(item) {
  try {
    await ElMessageBox.confirm(`确认删除「${item.name}」？`, '提示', { type: 'warning' })
    await templatesApi.delete(item.id)
    ElMessage.success('已删除')
    await loadLibrary()
  } catch (err) {
    if (err !== 'cancel') ElMessage.error('删除失败: ' + (err.message || err))
  }
}

// 跳转到 TemplatesView 库 Tab 做高级编辑
function editInTemplates() {
  router.push('/templates')
}

function previewText(p) {
  if (!p) return ''
  return p.length > 80 ? p.slice(0, 80) + '...' : p
}
</script>

<template>
  <div class="custom-prompt-panel">
    <div class="panel-header">
      <span class="panel-title">完全自定义提示词</span>
      <span class="panel-sub">纯空白 prompt，启用后覆盖一切维度拼装</span>
    </div>

    <!-- 启用状态指示条 -->
    <div v-if="isActive" class="override-active-bar">
      <el-icon><Check /></el-icon>
      <span>已启用自定义提示词（覆盖维度拼装）</span>
      <el-button size="small" text :icon="X" @click="disableOverride" class="close-btn">关闭</el-button>
    </div>

    <!-- 编辑区 -->
    <div class="edit-hint">
      可用 <code>{'{{CONTENT}}'}</code> 标记内容插入位置；不写则内容追加在末尾
    </div>
    <el-input
      v-model="promptText"
      type="textarea"
      :rows="10"
      placeholder="在此粘贴或编写完整提示词...&#10;例如：你是一位资深内容编辑，请将以下视频内容整理为结构化笔记...&#10;支持 {{CONTENT}} 占位符"
      style="font-family: 'Consolas', 'Monaco', monospace; font-size: 13px; line-height: 1.6;"
    />

    <!-- 操作按钮 -->
    <div class="action-row">
      <el-button type="primary" :icon="Check" @click="applyCurrent">应用此自定义提示词</el-button>
      <el-button :icon="Plus" @click="openSaveDialog">存入提示词库</el-button>
    </div>

    <!-- 库卡片列表 -->
    <div class="library-section">
      <div class="library-header">
        <span class="library-title">提示词库</span>
        <el-button text size="small" :icon="Pencil" @click="editInTemplates">在模板中心管理</el-button>
      </div>

      <div v-loading="libraryLoading" class="library-list">
        <div v-if="!library.length && !libraryLoading" class="empty-hint">
          暂无库中提示词。编写后点「存入提示词库」即可收藏
        </div>
        <div
          v-for="item in library"
          :key="item.id"
          class="lib-card"
          :class="{ active: isActive && settings.fullOverride === item.prompt }"
          @click="applyCard(item)"
        >
          <div class="lib-card-main">
            <div class="lib-card-name">
              {{ item.name }}
              <el-tag v-if="isActive && settings.fullOverride === item.prompt" size="small" type="warning">使用中</el-tag>
            </div>
            <div class="lib-card-desc">{{ item.options?.desc || '—' }}</div>
            <div class="lib-card-preview">{{ previewText(item.prompt) }}</div>
          </div>
          <div class="lib-card-actions" @click.stop>
            <el-button text size="small" :icon="Pencil" @click="editInTemplates" />
            <el-button text size="small" :icon="Trash2" type="danger" @click="deleteCard(item)" />
          </div>
        </div>
      </div>
    </div>

    <!-- 存入库弹窗 -->
    <el-dialog v-model="saveDialogVisible" title="存入提示词库" width="460px" destroy-on-close>
      <el-form label-width="60px">
        <el-form-item label="名称" required>
          <el-input v-model="saveForm.name" placeholder="如：B站视频深度分析" maxlength="60" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="saveForm.desc" placeholder="简短描述适用场景" maxlength="100" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="saveDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveToLibrary">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.custom-prompt-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
}
.panel-header {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.panel-title {
  font-weight: 600;
  font-size: 15px;
  color: var(--app-text, #e0e0e0);
}
.panel-sub {
  font-size: 12px;
  color: var(--app-text-secondary, #909399);
}

/* 启用状态条 */
.override-active-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  background: var(--app-override-bar-bg, #3a2f1a);
  border: 1px solid var(--app-override-bar-border, #5d4a2a);
  color: var(--app-text, #e0e0e0);
  font-size: 13px;
}
.override-active-bar .close-btn {
  margin-left: auto;
  color: var(--app-text-secondary, #909399);
}

.edit-hint {
  font-size: 11px;
  color: var(--app-text-secondary, #909399);
}
.edit-hint code {
  background: var(--app-hover-bg, #2a2a2a);
  padding: 1px 4px;
  border-radius: 3px;
  font-family: 'Consolas', monospace;
}

.action-row {
  display: flex;
  gap: 8px;
}

/* 库列表 */
.library-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
  min-height: 0;
}
.library-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.library-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-text, #e0e0e0);
}
.library-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
  max-height: 360px;
  padding-right: 4px;
}
.empty-hint {
  padding: 24px;
  text-align: center;
  color: var(--app-text-secondary, #909399);
  font-size: 13px;
  border: 1px dashed var(--app-border, #2a2a2a);
  border-radius: 6px;
}

.lib-card {
  display: flex;
  align-items: stretch;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--app-border, #2a2a2a);
  border-radius: 6px;
  background: var(--app-card-bg, #1e1e1e);
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
}
.lib-card:hover {
  border-color: var(--app-text-secondary, #909399);
  background: var(--app-hover-bg, #2a2a2a);
}
.lib-card.active {
  border-color: #e6a23c;
  background: var(--app-override-bar-bg, #3a2f1a);
}
.lib-card-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.lib-card-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-text, #e0e0e0);
  display: flex;
  align-items: center;
  gap: 6px;
}
.lib-card-desc {
  font-size: 12px;
  color: var(--app-text-secondary, #909399);
}
.lib-card-preview {
  font-size: 11px;
  color: var(--app-text-secondary, #909399);
  font-family: 'Consolas', monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.lib-card-actions {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 2px;
}
</style>
