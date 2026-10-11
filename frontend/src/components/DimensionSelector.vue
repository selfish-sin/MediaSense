<script setup>
/**
 * 模块化提示词 9 维度选择器
 * 用法：v-model="dimensions" 双向绑定 { role, content_type, mode, rules, depth, output, tone, word_count, speech_fix }
 *
 * 特性：
 * 1. 高对比度按钮样式（深色背景白字）
 * 2. 每个维度末尾的「+ 自定义」按钮（弹文本框写自定义指引）
 * 3. 顶部的「完全自定义提示词」总开关（开启后覆盖一切维度拼装）
 * 4. 已被 TemplatesView 编辑器覆盖的模块显示「已自定义」标记
 */
import { computed, watch, ref, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useSettingsStore } from '../stores/settingsStore'
import { summaryApi } from '../utils/api'
import { DIMENSION_KEYS, matchRecipeKey } from '../utils/recipes'
import { Pencil, Check } from 'lucide-vue-next'

const props = defineProps({
  modelValue: { type: Object, default: () => ({}) },
  compact: { type: Boolean, default: false },
  showRecipes: { type: Boolean, default: true },
  showPreview: { type: Boolean, default: true },
  // 「+ 自定义」chip 是给工作台加维度级追加指引的；模板页配方编辑器里
  // 会被误解成"在此新增模块"（真正的新增入口在下方模块树的 + 按钮），可关掉
  showCustomAdd: { type: Boolean, default: true },
})

const emit = defineEmits(['update:modelValue'])

const settings = useSettingsStore()
const router = useRouter()

// 当前选择（与 modelValue 同步）
const selected = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

// 维度列表（顺序与键集统一由 utils/recipes.js 的 DIMENSION_KEYS 决定）
const dimensionKeys = DIMENSION_KEYS

const dimensionList = computed(() => {
  const dims = settings.promptDimensions || {}
  return dimensionKeys
    .map(key => ({ key, ...dims[key] }))
    .filter(d => d.modules)
})

function selectModule(dimKey, moduleKey) {
  const next = { ...selected.value, [dimKey]: moduleKey }
  selected.value = next
}

function isSelected(dimKey, moduleKey) {
  return selected.value[dimKey] === moduleKey
}

// ============ 维度级自定义 ============
const customDialogVisible = ref(false)
const customDimKey = ref('')
const customText = ref('')

function openCustomDialog(dimKey) {
  customDimKey.value = dimKey
  customText.value = settings.customDimensions[dimKey] || ''
  customDialogVisible.value = true
}

function saveCustomDimension() {
  settings.setCustomDimension(customDimKey.value, customText.value)
  customDialogVisible.value = false
}

function clearCustomDimension() {
  settings.setCustomDimension(customDimKey.value, '')
  customDialogVisible.value = false
}

function hasCustomDimension(dimKey) {
  return !!settings.customDimensions[dimKey]
}

// ============ 预设配方应用条（紧凑下拉，选即应用） ============
const appliedRecipeKey = ref('')

// 监听维度选择变化：若与某配方完全一致则高亮该配方，否则清空（用户手动改了维度）
watch(() => selected.value, (cur) => {
  appliedRecipeKey.value = matchRecipeKey(settings.recipes, cur)
}, { deep: true })

function onRecipePick(recipeOrKey) {
  const recipe = typeof recipeOrKey === 'object' && recipeOrKey
    ? recipeOrKey
    : settings.recipes.find(r => r.key === recipeOrKey)
  if (!recipe) return
  // 写 store + 同步本组件 v-model（重新总结弹窗等独立 v-model 场景必需）
  settings.applyRecipe(recipe)
  if (recipe.dimensions) {
    selected.value = { ...recipe.dimensions }
  }
  appliedRecipeKey.value = recipe.key
}

function isUserRecipe(key) {
  return key && String(key).startsWith('usr_')
}

// 跳转到模板页编辑配方
function goToTemplates() {
  router.push('/templates')
}

// ============ Prompt 预览（自动加载） ============
const promptPreview = ref('')
const previewLoading = ref(false)
let previewTimer = null

watch(() => selected.value, () => {
  if (!props.showPreview) return
  if (previewTimer) clearTimeout(previewTimer)
  previewTimer = setTimeout(loadPreview, 300)
}, { deep: true })

// 组件卸载时取消待执行的预览加载，避免对已卸载组件写状态
onUnmounted(() => {
  if (previewTimer) {
    clearTimeout(previewTimer)
    previewTimer = null
  }
})

async function loadPreview() {
  if (!Object.keys(selected.value).length) return
  previewLoading.value = true
  try {
    const payload = { dimensions: selected.value }
    const customPrompt = settings.buildCustomPrompt()
    if (customPrompt) payload.custom_prompt = customPrompt
    const res = await summaryApi.promptPreview(payload)
    promptPreview.value = res.prompt || ''
  } catch {
    promptPreview.value = '预览加载失败'
  } finally {
    previewLoading.value = false
  }
}
</script>

<template>
  <div class="dimension-selector" :class="{ compact }">
    <!-- 预设配方：紧凑小卡片（点即应用） -->
    <div v-if="showRecipes && settings.recipes?.length" class="recipes-block">
      <div class="recipes-block-head">
        <span class="dim-label recipes-bar-label">预设配方</span>
        <span class="recipes-hint">点选即用</span>
        <el-button
          size="small"
          text
          type="primary"
          title="到「模板」页编辑预设配方"
          @click="goToTemplates"
        >编辑</el-button>
      </div>
      <div class="recipes-mini-grid">
        <button
          v-for="r in settings.recipes"
          :key="r.key"
          type="button"
          class="recipe-mini"
          :class="{
            applied: appliedRecipeKey === r.key,
            'is-mine': isUserRecipe(r.key),
          }"
          :title="r.desc || r.name"
          @click="onRecipePick(r)"
        >
          <span class="recipe-mini-name">{{ r.name }}</span>
          <span v-if="appliedRecipeKey === r.key" class="recipe-mini-mark"><el-icon :size="12"><Check /></el-icon></span>
          <span v-else-if="isUserRecipe(r.key)" class="recipe-mini-badge">我的</span>
        </button>
      </div>
    </div>

    <el-divider v-if="showRecipes && settings.recipes?.length" style="margin: 10px 0 8px;" />

    <!-- 9 维度选择器 -->
    <div v-for="dim in dimensionList" :key="dim.key" class="dim-row">
      <div class="dim-header">
        <span class="dim-label">{{ dim.label }}</span>
        <span class="dim-desc">{{ dim.desc }}</span>
        <el-tag v-if="hasCustomDimension(dim.key)" type="success" size="small" style="margin-left: 6px;">含自定义</el-tag>
      </div>
      <div class="module-chips">
        <div
          v-for="m in dim.modules"
          :key="m.key"
          class="module-chip"
          :class="{ active: isSelected(dim.key, m.key) }"
          :title="m.desc"
          @click="selectModule(dim.key, m.key)"
        >
          <span class="module-name">{{ m.name }}</span>
          <el-icon v-if="m.is_customized" class="customized-badge" title="此模块已在模板编辑器中自定义"><Pencil /></el-icon>
        </div>
        <!-- + 自定义按钮（工作台语境专用，模板页编辑器可关） -->
        <div
          v-if="showCustomAdd"
          class="module-chip custom-add"
          :class="{ active: hasCustomDimension(dim.key) }"
          @click="openCustomDialog(dim.key)"
          title="为该维度添加自定义指引"
        >
          <span class="module-name">+ 自定义</span>
        </div>
      </div>
    </div>

    <!-- Prompt 预览 -->
    <template v-if="showPreview">
      <el-divider style="margin: 12px 0 8px;" />
      <div class="preview-section">
        <div class="dim-label">
          提示词预览
          <span v-if="previewLoading" style="font-size: 11px; color: var(--app-text-secondary);">加载中...</span>
        </div>
        <pre class="prompt-preview">{{ promptPreview || '选择维度后将显示拼装后的提示词...' }}</pre>
      </div>
    </template>

    <!-- 维度级自定义弹窗 -->
    <el-dialog
      v-model="customDialogVisible"
      :title="`「${dimensionList.find(d => d.key === customDimKey)?.label || customDimKey}」自定义指引`"
      width="600px"
      destroy-on-close
    >
      <div style="font-size: 12px; color: var(--app-text-secondary); margin-bottom: 8px;">
        此文本将作为该维度的额外指引追加到 prompt 中（不影响其他维度）。
        留空并保存可清除该维度的自定义。
      </div>
      <el-input
        v-model="customText"
        type="textarea"
        :rows="8"
        placeholder="例如：请特别关注视频中对XX概念的定义演变，逐阶段记录..."
      />
      <template #footer>
        <el-button v-if="hasCustomDimension(customDimKey)" type="danger" @click="clearCustomDimension">清除自定义</el-button>
        <el-button @click="customDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveCustomDimension">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.dimension-selector {
  width: 100%;
}
.dimension-selector.compact .dim-row {
  margin-bottom: 6px;
}

/* 顶部完全自定义 bar（用主题变量） */
.full-override-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: var(--app-override-bar-bg);
  border: 1px solid var(--app-override-bar-border);
  border-radius: 6px;
  transition: background 0.25s, border-color 0.25s;
}
.full-override-controls {
  display: flex;
  align-items: center;
  gap: 8px;
}

.recipes-block {
  margin-bottom: 2px;
}
.recipes-block-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.recipes-bar-label {
  margin-bottom: 0;
  white-space: nowrap;
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--app-text-secondary);
}
.recipes-hint {
  font-size: 10px;
  color: var(--app-text-placeholder, #6b7085);
}
.recipes-block-head .el-button {
  margin-left: auto;
}
.recipes-mini-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
.recipe-mini {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex: 0 0 auto;
  width: auto;
  max-width: none;
  padding: 4px 10px;
  min-height: 26px;
  border-radius: 999px;
  border: 1px solid var(--app-border, #2a2d38);
  background: var(--app-chip-bg, #2a2e3c);
  color: var(--app-text-heading, #f3f4f8);
  font-family: inherit;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.02em;
  line-height: 1.25;
  cursor: pointer;
  transition:
    border-color 0.15s,
    background 0.15s,
    box-shadow 0.15s,
    transform 0.12s,
    color 0.15s;
  user-select: none;
}
.recipe-mini:hover {
  border-color: rgba(var(--app-accent-1-rgb), 0.55);
  background: var(--app-hover-bg, #242734);
  color: var(--app-text-heading, #fff);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(var(--app-accent-1-rgb), 0.12);
}
.recipe-mini.applied {
  border-color: transparent;
  color: #fff;
  background: linear-gradient(135deg, var(--app-accent-1), var(--app-accent-2));
  box-shadow: 0 4px 14px rgba(var(--app-accent-1-rgb), 0.35);
}
.recipe-mini.is-mine:not(.applied) {
  border-style: dashed;
}
.recipe-mini-name {
  white-space: nowrap;
  overflow: visible;
  text-overflow: clip;
  max-width: none;
}
.recipe-mini-mark {
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 800;
}
.recipe-mini-badge {
  flex-shrink: 0;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.04em;
  padding: 1px 4px;
  border-radius: 4px;
  color: var(--app-text-secondary);
  background: var(--app-card-bg, rgba(0, 0, 0, 0.15));
  border: 1px solid var(--app-border);
}
.dim-label {
  font-size: 12px;
  color: var(--app-text);
  font-weight: 600;
  margin-bottom: 2px;
}
.dim-desc {
  font-size: 11px;
  color: var(--app-text-secondary);
  margin-left: 8px;
  font-weight: normal;
}
.dim-row {
  margin-bottom: 9px;
}
.dim-header {
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
}
.module-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

/* ★ 高对比度按钮：用主题变量驱动，日间/夜间都清晰 */
.module-chip {
  padding: 4px 8px;
  border: 1px solid var(--app-chip-border);
  border-radius: 5px;
  cursor: pointer;
  font-size: 11px;
  transition: all 0.15s;
  background: var(--app-chip-bg);
  color: var(--app-chip-text);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  user-select: none;
}
.module-chip:hover {
  border-color: var(--app-chip-active-bg);
  background: var(--app-chip-hover-bg);
  color: var(--app-text);
}
.module-chip.active {
  border-color: var(--app-chip-active-bg);
  background: var(--app-chip-active-bg);
  color: #fff;
  font-weight: 500;
  box-shadow: 0 2px 6px rgba(64, 158, 255, 0.35);
}

/* 自定义按钮特殊样式 */
.module-chip.custom-add {
  border-style: dashed;
  border-color: var(--app-text-secondary);
  background: transparent;
  color: var(--app-text-secondary);
}
.module-chip.custom-add:hover {
  border-color: #67c23a;
  color: #67c23a;
  background: rgba(103, 194, 58, 0.1);
}
.module-chip.custom-add.active {
  border-color: #67c23a;
  background: #67c23a;
  color: #fff;
  border-style: solid;
}

/* 已自定义标记 */
.customized-badge {
  font-size: 11px;
  color: #e6a23c;
}
.module-chip.active .customized-badge {
  color: #fff;
}

.module-name {
  white-space: nowrap;
}

.preview-section {
  margin-top: 4px;
}
.prompt-preview {
  font-size: 11px;
  background: var(--app-preview-bg);
  color: var(--app-preview-text);
  border-left: 3px solid #409eff;
  padding: 10px 12px;
  border-radius: 0 4px 4px 0;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 240px;
  overflow-y: auto;
  margin: 4px 0 0;
  line-height: 1.5;
  font-family: 'Consolas', 'Monaco', monospace;
  transition: background 0.25s, color 0.25s;
}
</style>
