<script setup>
/**
 * 模板管理 → 两个 Tab：
 * 1. 维度模块编辑器：编辑任意维度模块的提示词文本，保存后覆盖默认，DimensionSelector 实时生效
 * 2. 完全自定义提示词库：从网上收集/自写的完整 prompt，保存为列表，可应用到 HomeView
 */
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Pencil, Trash2, Plus, RefreshCw, FileText, Files } from 'lucide-vue-next'
import { useSettingsStore } from '../stores/settingsStore'
import { summaryApi, templatesApi } from '../utils/api'
import { isDialogCancel } from '../utils/errorNotice'
import DimensionSelector from '../components/DimensionSelector.vue'

const settings = useSettingsStore()

// ============ Tab 切换 ============
const activeTab = ref('modules')  // modules | library

// ============ 预设配方编辑器（在维度模块编辑器顶部） ============
// 草稿 = 8 维度选择；选择器选中某配方会载入其维度；可保存为本地自定义配方
const recipeDraft = ref({})                 // { role, mode, content_type, ... }
const editingRecipeKey = ref('')            // 当前编辑的配方 key（'' 表示新建）
const editingRecipeName = ref('')
const editingRecipeDesc = ref('')

function loadRecipeToDraft(recipe) {
  recipeDraft.value = { ...(recipe.dimensions || {}) }
  editingRecipeKey.value = recipe.key
  editingRecipeName.value = recipe.name
  editingRecipeDesc.value = recipe.desc || ''
}

function onRecipeSelect(key) {
  const r = settings.recipes.find(x => x.key === key)
  if (r) loadRecipeToDraft(r)
}

function newRecipe() {
  editingRecipeKey.value = ''
  editingRecipeName.value = ''
  editingRecipeDesc.value = ''
  recipeDraft.value = {}
}

async function saveRecipe() {
  if (!editingRecipeName.value.trim()) {
    ElMessage.warning('请填写配方名称')
    return
  }
  // 内置配方不可覆盖：保存即「另存为」新的本地(usr_)配方；自定义配方则就地更新
  let key = editingRecipeKey.value
  if (!key || !key.startsWith('usr_')) {
    key = settings.genRecipeKey(editingRecipeName.value)
  }
  settings.saveUserRecipe({
    key,
    name: editingRecipeName.value.trim(),
    desc: editingRecipeDesc.value.trim(),
    dimensions: { ...recipeDraft.value },
  })
  editingRecipeKey.value = key
  ElMessage.success('配方已保存到本地（「我的配方」）')
}

function deleteRecipe() {
  if (!editingRecipeKey.value || !editingRecipeKey.value.startsWith('usr_')) {
    ElMessage.warning('内置配方不可删除，可基于它「新建」')
    return
  }
  ElMessageBox.confirm(`确认删除自定义配方「${editingRecipeName.value}」？`, '删除配方', { type: 'warning' })
    .then(() => {
      settings.deleteUserRecipe(editingRecipeKey.value)
      newRecipe()
      ElMessage.success('已删除')
    })
    .catch((err) => {
      if (!isDialogCancel(err)) ElMessage.error('删除失败: ' + (err?.message || err))
    })
}

function isCustomRecipe() {
  return !!editingRecipeKey.value && editingRecipeKey.value.startsWith('usr_')
}

// ============ 维度树 ============
const dimensionKeys = ['role', 'mode', 'content_type', 'rules', 'depth', 'output', 'tone', 'word_count']

const dimensionList = computed(() => {
  const dims = settings.promptDimensions || {}
  return dimensionKeys
    .map(key => ({ key, ...dims[key] }))
    .filter(d => d.modules)
})

// ============ 编辑状态 ============
const selectedDim = ref('')
const selectedModule = ref('')
const editingText = ref('')
const originalDefault = ref('')  // 代码默认文本（用于对比/恢复）
const isCustomized = ref(false)  // 当前模块是否已被覆盖
const loading = ref(false)
const saving = ref(false)
const dirty = ref(false)

onMounted(async () => {
  await settings.loadDeepConfig()
  if (dimensionList.value.length > 0) {
    const first = dimensionList.value[0]
    selectModule(first.key, first.modules[0].key)
  }
  await loadLibrary()
})

function selectModule(dimKey, moduleKey) {
  if (dirty.value) {
    ElMessageBox.confirm('当前模块有未保存的修改，切换将丢失。继续？', '提示', { type: 'warning' })
      .then(() => doSelect(dimKey, moduleKey))
      .catch((err) => {
        if (!isDialogCancel(err)) ElMessage.error('切换失败: ' + (err?.message || err))
      })
    return
  }
  doSelect(dimKey, moduleKey)
}

async function doSelect(dimKey, moduleKey) {
  selectedDim.value = dimKey
  selectedModule.value = moduleKey
  loading.value = true
  dirty.value = false
  try {
    const res = await summaryApi.getOverride(dimKey, moduleKey)
    originalDefault.value = res.default_text || ''
    isCustomized.value = res.is_customized
    editingText.value = res.custom_text || res.default_text || ''
  } catch (err) {
    ElMessage.error('加载模块失败: ' + (err.message || err))
    editingText.value = ''
  } finally {
    loading.value = false
  }
}

function onEditText() {
  dirty.value = true
}

async function handleSave() {
  if (!selectedDim.value || !selectedModule.value) return
  saving.value = true
  try {
    await summaryApi.upsertOverride({
      dimension_key: selectedDim.value,
      module_key: selectedModule.value,
      custom_text: editingText.value,
    })
    ElMessage.success('模块已保存，DimensionSelector 将实时生效')
    isCustomized.value = true
    dirty.value = false
    await settings.reloadDimensions()
  } catch (err) {
    ElMessage.error('保存失败: ' + (err.message || err))
  } finally {
    saving.value = false
  }
}

async function handleReset() {
  if (!selectedDim.value || !selectedModule.value) return
  try {
    await ElMessageBox.confirm(
      `确认恢复「${selectedDim.value} / ${selectedModule.value}」为代码默认？自定义内容将被删除。`,
      '恢复默认',
      { type: 'warning' }
    )
    await summaryApi.deleteOverride(selectedDim.value, selectedModule.value)
    ElMessage.success('已恢复默认')
    isCustomized.value = false
    dirty.value = false
    await doSelect(selectedDim.value, selectedModule.value)
    await settings.reloadDimensions()
  } catch (err) {
    if (!isDialogCancel(err)) ElMessage.error('恢复失败: ' + (err?.message || err))
  }
}

async function handleResetAll() {
  try {
    await ElMessageBox.confirm(
      '确认清空所有模块覆盖？全部恢复代码默认。此操作不可撤销。',
      '清空所有自定义',
      { type: 'error', confirmButtonText: '确认清空', cancelButtonText: '取消' }
    )
    await summaryApi.resetAllOverrides()
    ElMessage.success('已清空所有覆盖')
    await settings.reloadDimensions()
    if (selectedDim.value) await doSelect(selectedDim.value, selectedModule.value)
  } catch (err) {
    if (!isDialogCancel(err)) ElMessage.error('清空失败: ' + (err?.message || err))
  }
}

function getSelectedModuleInfo() {
  const dim = dimensionList.value.find(d => d.key === selectedDim.value)
  if (!dim) return null
  return dim.modules.find(m => m.key === selectedModule.value) || null
}

// ============ 维度级新增自定义模块 ============
const newModuleDialog = ref(false)
const newModuleDim = ref('')
const newModuleKey = ref('')
const newModuleName = ref('')
const newModuleText = ref('')

function openNewModuleDialog(dimKey) {
  newModuleDim.value = dimKey
  newModuleKey.value = ''
  newModuleName.value = ''
  newModuleText.value = ''
  newModuleDialog.value = true
}

async function saveNewModule() {
  if (!newModuleKey.value || !newModuleText.value.trim()) {
    ElMessage.warning('请填写模块 key 和提示词文本')
    return
  }
  saving.value = true
  try {
    await summaryApi.upsertOverride({
      dimension_key: newModuleDim.value,
      module_key: newModuleKey.value,
      custom_text: newModuleText.value,
    })
    ElMessage.success(`新模块「${newModuleName.value || newModuleKey.value}」已保存`)
    newModuleDialog.value = false
    await settings.reloadDimensions()
    await doSelect(newModuleDim.value, newModuleKey.value)
  } catch (err) {
    ElMessage.error('保存失败: ' + (err.message || err))
  } finally {
    saving.value = false
  }
}

// ============ 完全自定义提示词库 ============
// 复用 templates 表：template_type='full_override' 区分
const library = ref([])
const libraryLoading = ref(false)
const libDialogVisible = ref(false)
const libDialogMode = ref('add')  // add | edit
const libEditId = ref(null)
const libForm = ref({ name: '', prompt: '', desc: '' })

async function loadLibrary() {
  libraryLoading.value = true
  try {
    const res = await templatesApi.list()
    // 过滤出 full_override 类型的模板
    library.value = (res.templates || []).filter(t => t.template_type === 'full_override')
  } catch (err) {
    ElMessage.error('加载提示词库失败: ' + (err.message || err))
  } finally {
    libraryLoading.value = false
  }
}

function openAddLibDialog() {
  libDialogMode.value = 'add'
  libEditId.value = null
  libForm.value = { name: '', prompt: '', desc: '' }
  libDialogVisible.value = true
}

function openEditLibDialog(item) {
  libDialogMode.value = 'edit'
  libEditId.value = item.id
  libForm.value = {
    name: item.name,
    prompt: item.prompt,
    desc: (item.options && item.options.desc) || '',
  }
  libDialogVisible.value = true
}

async function saveLib() {
  if (!libForm.value.name || !libForm.value.prompt) {
    ElMessage.warning('请填写名称和提示词')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: libForm.value.name,
      prompt: libForm.value.prompt,
      template_type: 'full_override',
      options: { desc: libForm.value.desc },
    }
    if (libDialogMode.value === 'add') {
      await templatesApi.create(payload)
      ElMessage.success('已添加到提示词库')
    } else {
      await templatesApi.update(libEditId.value, payload)
      ElMessage.success('已更新')
    }
    libDialogVisible.value = false
    await loadLibrary()
  } catch (err) {
    ElMessage.error('保存失败: ' + (err.message || err))
  } finally {
    saving.value = false
  }
}

async function deleteLib(item) {
  try {
    await ElMessageBox.confirm(`确认删除「${item.name}」？`, '提示', { type: 'warning' })
    await templatesApi.delete(item.id)
    ElMessage.success('已删除')
    await loadLibrary()
  } catch (err) {
    if (!isDialogCancel(err)) ElMessage.error('删除失败: ' + (err?.message || err))
  }
}

async function applyLib(item) {
  // 应用到 HomeView：写入 settingsStore 的 fullOverride 并启用
  settings.setFullOverride(item.prompt)
  settings.toggleFullOverride(true)
  ElMessage.success(`已应用「${item.name}」，返回视频处理页即可生效`)
}
</script>

<template>
  <div class="templates-page page-container">
    <!-- 顶部标题 + Tab 切换 -->
    <el-card class="app-card" shadow="never" style="margin-bottom: 16px;">
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <div>
          <h2 style="margin: 0;">模板中心</h2>
          <div style="font-size: 12px; color: var(--app-text-secondary); margin-top: 4px;">
            维度模块编辑器（修改内置模块提示词） · 完全自定义提示词库（收集/应用完整 prompt）
          </div>
        </div>
      </div>
    </el-card>

    <el-tabs v-model="activeTab" class="templates-tabs">
      <!-- Tab 1: 维度模块编辑器 -->
      <el-tab-pane label="维度模块编辑器" name="modules">
        <!-- 预设配方编辑器（位于维度树/角色之上） -->
        <div class="recipe-editor">
          <div class="recipe-editor-head">
            <span class="recipe-editor-title">预设配方</span>
            <div class="recipe-editor-bar">
              <el-select
                :model-value="editingRecipeKey"
                placeholder="选择配方进行查看/编辑"
                filterable
                clearable
                style="flex: 1; min-width: 0;"
                @change="onRecipeSelect"
                @clear="newRecipe"
              >
                <el-option
                  v-for="r in settings.recipes"
                  :key="r.key"
                  :label="r.name"
                  :value="r.key"
                >
                  <span style="float: left;">{{ r.name }}</span>
                  <span style="float: right; color: var(--app-text-secondary); font-size: 12px; margin-left: 12px;">
                    {{ r.key.startsWith('usr_') ? '我的' : '内置' }}
                  </span>
                </el-option>
              </el-select>
              <el-button @click="newRecipe">新建</el-button>
              <el-button type="primary" :loading="saving" @click="saveRecipe">保存</el-button>
              <el-button type="danger" :disabled="!isCustomRecipe()" @click="deleteRecipe">删除</el-button>
            </div>
          </div>
          <div class="recipe-editor-fields">
            <el-input v-model="editingRecipeName" placeholder="配方名称" style="width: 220px;" maxlength="40" />
            <el-input v-model="editingRecipeDesc" placeholder="描述（可选）" style="flex: 1;" maxlength="100" />
          </div>
          <div class="recipe-editor-dims">
            <DimensionSelector v-model="recipeDraft" :show-recipes="false" compact :show-preview="false" />
          </div>
          <div class="recipe-editor-tip">
            在下方各维度选择模块，点击「保存」即可作为「我的配方」保存到本地（浏览器）；内置配方仅可查看，可基于它「新建」。
            保存后回到视频处理页的「维度预设」顶部，点对应卡片即可一键应用。
          </div>
        </div>

        <div style="display: flex; justify-content: flex-end; margin: 12px 0;">
          <el-button type="danger" :icon="RefreshCw" plain size="small" @click="handleResetAll">
            清空所有模块覆盖
          </el-button>
        </div>

        <div class="editor-layout">
          <!-- 左侧：维度 + 模块树 -->
          <div class="dim-tree" v-loading="loading && !dimensionList.length">
            <div v-for="dim in dimensionList" :key="dim.key" class="dim-tree-item">
              <div class="dim-tree-label">
                <span>{{ dim.label }}</span>
                <el-button
                  size="small"
                  text
                  :icon="Plus"
                  @click="openNewModuleDialog(dim.key)"
                  title="新增自定义模块"
                />
              </div>
              <div class="module-tree">
                <div
                  v-for="m in dim.modules"
                  :key="m.key"
                  class="module-tree-item"
                  :class="{ active: selectedDim === dim.key && selectedModule === m.key }"
                  @click="selectModule(dim.key, m.key)"
                >
                  <span class="module-tree-name" :title="m.name">{{ m.name }}</span>
                  <el-tag v-if="m.is_customized" type="warning" size="small">自定义</el-tag>
                </div>
              </div>
            </div>
          </div>

          <!-- 右侧：编辑区 -->
          <div class="edit-area" v-loading="loading">
            <template v-if="selectedDim && selectedModule">
              <div class="edit-header">
                <div>
                  <span style="font-weight: 600; font-size: 15px;">
                    {{ dimensionList.find(d => d.key === selectedDim)?.label }}
                    <span style="color: var(--app-text-secondary); font-weight: normal;">/</span>
                    {{ getSelectedModuleInfo()?.name || selectedModule }}
                  </span>
                  <el-tag v-if="isCustomized" type="warning" size="small" style="margin-left: 8px;">已自定义</el-tag>
                  <el-tag v-else type="info" size="small" style="margin-left: 8px;">代码默认</el-tag>
                </div>
                <div class="edit-actions">
                  <el-button size="small" @click="editingText = originalDefault; dirty = true" :disabled="!originalDefault">
                    恢复到默认文本
                  </el-button>
                  <el-button v-if="isCustomized" size="small" type="danger" :icon="Trash2" @click="handleReset">
                    删除覆盖
                  </el-button>
                  <el-button size="small" type="primary" :icon="Pencil" :loading="saving" @click="handleSave" :disabled="!dirty">
                    保存
                  </el-button>
                </div>
              </div>

              <div style="font-size: 12px; color: var(--app-text-secondary); margin-bottom: 8px;">
                {{ getSelectedModuleInfo()?.desc }}
                <span v-if="dirty" style="color: #e6a23c; margin-left: 8px;">● 未保存</span>
              </div>

              <el-input
                v-model="editingText"
                type="textarea"
                :rows="20"
                @input="onEditText"
                placeholder="输入该模块的提示词文本..."
                style="font-family: 'Consolas', 'Monaco', monospace; font-size: 13px; line-height: 1.6;"
              />

              <el-collapse style="margin-top: 12px;">
                <el-collapse-item title="查看代码默认文本" name="default">
                  <pre class="default-text-ref">{{ originalDefault || '（无默认文本）' }}</pre>
                </el-collapse-item>
              </el-collapse>
            </template>

            <div v-else class="empty-hint">
              <el-icon :size="40" color="#c0c4cc"><FileText /></el-icon>
              <div style="margin-top: 12px; color: var(--app-text-secondary);">从左侧选择一个维度模块开始编辑</div>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- Tab 2: 完全自定义提示词库 -->
      <el-tab-pane label="完全自定义提示词库" name="library">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
          <div style="font-size: 13px; color: var(--app-text-secondary);">
            <el-icon style="vertical-align: middle;"><Files /></el-icon>
            从网上收集或自写的完整 prompt，保存后可一键应用到视频处理页（覆盖一切维度拼装）
          </div>
          <el-button type="primary" :icon="Plus" size="small" @click="openAddLibDialog">添加提示词</el-button>
        </div>

        <el-card class="app-card" shadow="never" v-loading="libraryLoading">
          <el-table :data="library" style="width: 100%;" :empty-text="libraryLoading ? '加载中...' : '暂无提示词，点击右上角添加'">
            <el-table-column prop="name" label="名称" width="180" />
            <el-table-column label="描述" width="200">
              <template #default="{ row }">
                <span style="font-size: 12px; color: var(--app-text-secondary);">{{ row.options?.desc || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="提示词内容" min-width="300" show-overflow-tooltip>
              <template #default="{ row }">
                <span style="font-size: 12px; color: var(--app-text-secondary); font-family: 'Consolas', monospace;">{{ row.prompt.slice(0, 120) }}{{ row.prompt.length > 120 ? '...' : '' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="220" align="center">
              <template #default="{ row }">
                <el-button size="small" type="primary" @click="applyLib(row)">应用</el-button>
                <el-button size="small" :icon="Pencil" @click="openEditLibDialog(row)" />
                <el-button size="small" :icon="Trash2" type="danger" @click="deleteLib(row)" />
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- 添加/编辑提示词弹窗 -->
        <el-dialog
          v-model="libDialogVisible"
          :title="libDialogMode === 'add' ? '添加提示词' : '编辑提示词'"
          width="700px"
          destroy-on-close
        >
          <el-form label-width="80px">
            <el-form-item label="名称" required>
              <el-input v-model="libForm.name" placeholder="如：B站视频深度分析（来自XX网友分享）" maxlength="60" />
            </el-form-item>
            <el-form-item label="描述">
              <el-input v-model="libForm.desc" placeholder="简短描述这个提示词的适用场景" maxlength="100" />
            </el-form-item>
            <el-form-item label="提示词" required>
              <div style="font-size: 11px; color: var(--app-text-secondary); margin-bottom: 4px;">
                完整 prompt，可用 <code>{'{{CONTENT}}'}</code> 标记内容插入位置
              </div>
              <el-input
                v-model="libForm.prompt"
                type="textarea"
                :rows="14"
                placeholder="粘贴完整的提示词..."
                style="font-family: 'Consolas', monospace; font-size: 13px;"
              />
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="libDialogVisible = false">取消</el-button>
            <el-button type="primary" :loading="saving" @click="saveLib">保存</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>
    </el-tabs>

    <!-- 新增自定义模块弹窗 -->
    <el-dialog v-model="newModuleDialog" title="新增自定义模块" width="600px" destroy-on-close>
      <div style="font-size: 12px; color: var(--app-text-secondary); margin-bottom: 12px;">
        为维度「{{ dimensionList.find(d => d.key === newModuleDim)?.label }}」新增一个自定义模块。
        保存后会出现在 DimensionSelector 的对应维度中。
      </div>
      <el-form label-width="80px">
        <el-form-item label="模块 key" required>
          <el-input v-model="newModuleKey" placeholder="如：my_custom_role（英文标识符）" />
        </el-form-item>
        <el-form-item label="显示名称">
          <el-input v-model="newModuleName" placeholder="如：我的自定义角色（留空则用 key）" />
        </el-form-item>
        <el-form-item label="提示词" required>
          <el-input
            v-model="newModuleText"
            type="textarea"
            :rows="10"
            placeholder="输入该模块的提示词文本..."
            style="font-family: 'Consolas', monospace; font-size: 13px;"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="newModuleDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveNewModule">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 整页按应用 UI 对待：非编辑区一律箭头光标、不出现文本光标与拖选 */
.templates-page {
  cursor: default;
  user-select: none;
}
/* 可编辑控件显式还原文本光标与选择（父级 user-select 会波及表单控件内部） */
.templates-page :deep(.el-textarea__inner),
.templates-page :deep(.el-input__inner),
.templates-page :deep(.el-select__input) {
  cursor: text;
  user-select: text;
}

.templates-tabs {
  background: rgba(var(--app-dim-color), var(--app-card-opacity, 1));
  border: 1px solid var(--app-border);
  border-radius: 12px;
  padding: 16px;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  transition: box-shadow 0.2s, border-color 0.2s;
}
.templates-tabs:hover {
  box-shadow: var(--app-card-glow, 0 8px 28px rgba(0, 0, 0, 0.42));
}

.editor-layout {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 16px;
  min-height: 600px;
}

/* ====== 预设配方编辑器（维度树之上，紧凑） ====== */
.recipe-editor {
  background: var(--app-hover-bg);
  border: 1px solid var(--app-border);
  border-radius: 6px;
  padding: 8px;
  margin-bottom: 8px;
  transition: background 0.25s, border-color 0.25s;
}
.recipe-editor-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
  flex-wrap: wrap;
}
.recipe-editor-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--app-text);
  white-space: nowrap;
}
.recipe-editor-bar {
  display: flex;
  align-items: center;
  gap: 5px;
  flex: 1;
  min-width: 0;
}
.recipe-editor-fields {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}
.recipe-editor-dims {
  border-top: 1px dashed var(--app-border);
  padding-top: 6px;
}
/* 编辑器内维度选择器进一步紧凑 */
.recipe-editor-dims :deep(.dimension-selector) .dim-row {
  margin-bottom: 5px;
}
.recipe-editor-dims :deep(.module-chip) {
  padding: 3px 6px;
  font-size: 11px;
}
.recipe-editor-dims :deep(.dim-label) {
  font-size: 11.5px;
}
.recipe-editor-tip {
  font-size: 11px;
  color: var(--app-text-secondary);
  margin-top: 8px;
  line-height: 1.5;
}

/* 左侧维度树（透明卡片） */
.dim-tree {
  background: rgba(var(--app-dim-color), var(--app-card-opacity, 1));
  border: 1px solid var(--app-border);
  border-radius: 12px;
  padding: 12px;
  max-height: 70vh;
  overflow-y: auto;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  transition: box-shadow 0.2s, border-color 0.2s;
}
.dim-tree:hover {
  box-shadow: var(--app-card-glow, 0 8px 28px rgba(0, 0, 0, 0.42));
}
.dim-tree-item {
  margin-bottom: 16px;
}
.dim-tree-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-text);
  padding: 4px 0;
  border-bottom: 1px solid var(--app-border);
  margin-bottom: 6px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  transition: color 0.25s, border-color 0.25s;
}
.module-tree {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.module-tree-item {
  padding: 6px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  color: var(--app-text);
  display: flex;
  justify-content: space-between;
  align-items: center;
  transition: all 0.15s;
}
.module-tree-item:hover {
  background: var(--app-chip-hover-bg);
}
.module-tree-item.active {
  background: var(--app-accent-1);
  color: #fff;
}
.module-tree-item.active :deep(.el-tag) {
  --el-tag-bg-color: rgba(255,255,255,0.2);
  color: #fff;
}
.module-tree-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 右侧编辑区（透明卡片） */
.edit-area {
  background: rgba(var(--app-dim-color), var(--app-card-opacity, 1));
  border: 1px solid var(--app-border);
  border-radius: 12px;
  padding: 16px;
  max-height: 70vh;
  overflow-y: auto;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  transition: box-shadow 0.2s, border-color 0.2s;
}
.edit-area:hover {
  box-shadow: var(--app-card-glow, 0 8px 28px rgba(0, 0, 0, 0.42));
}
.edit-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  flex-wrap: wrap;
  gap: 8px;
}
.edit-actions {
  display: flex;
  gap: 8px;
}
.default-text-ref {
  font-size: 12px;
  color: var(--app-text-secondary);
  background: var(--app-hover-bg);
  padding: 10px;
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.5;
  font-family: 'Consolas', monospace;
  max-height: 200px;
  overflow-y: auto;
  margin: 0;
  transition: background 0.25s, color 0.25s;
}
.empty-hint {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 400px;
}
</style>
