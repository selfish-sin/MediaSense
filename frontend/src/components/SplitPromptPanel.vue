<script setup>
/**
 * 总结配置卡片内部菜单（上下结构）
 * - 顶部：内部菜单栏「维度预设」→「完全自定义」
 * - 维度预设顶部：预设配方小卡片（点即应用）
 */
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useSettingsStore } from '../stores/settingsStore'
import { matchRecipeKey } from '../utils/recipes'
import { Check } from 'lucide-vue-next'
import DimensionSelector from './DimensionSelector.vue'
import CustomPromptPanel from './CustomPromptPanel.vue'

const settings = useSettingsStore()
const router = useRouter()
// 'dimensions' | 'custom'
const activeTab = ref('dimensions')
const appliedRecipeKey = ref('')

function switchTab(tab) {
  activeTab.value = tab
  if (tab !== 'custom') {
    settings.toggleFullOverride(false)
  }
}

function onApplyRecipe(recipe) {
  if (!recipe) return
  settings.applyRecipe(recipe)
  appliedRecipeKey.value = recipe.key
  // 确保 v-model 维度与配方一致（applyRecipe 已写 store）
  if (recipe.dimensions) {
    settings.selectedDimensions = { ...recipe.dimensions }
  }
}

function goTemplates() {
  router.push('/templates')
}

function isUserRecipe(key) {
  return key && String(key).startsWith('usr_')
}

// 手动改维度时同步高亮：完全匹配某配方则点亮
watch(
  () => settings.selectedDimensions,
  (cur) => {
    appliedRecipeKey.value = matchRecipeKey(settings.recipes, cur)
  },
  { deep: true, immediate: true },
)
</script>

<template>
  <div class="prompt-tabs-card">
    <!-- 内部菜单栏 -->
    <div class="inner-menu">
      <button
        class="menu-item"
        :class="{ active: activeTab === 'dimensions' }"
        @click="switchTab('dimensions')"
      >
        维度预设
      </button>
      <button
        class="menu-item"
        :class="{ active: activeTab === 'custom' }"
        @click="switchTab('custom')"
      >
        完全自定义
      </button>
    </div>

    <!-- ── 维度预设 Tab ── -->
    <div v-if="activeTab === 'dimensions'" class="tab-content">
      <!-- 预设配方：紧凑小卡片 -->
      <div class="recipe-section">
        <div class="recipe-section-head">
          <span class="recipe-section-title">预设配方</span>
          <span class="recipe-section-hint">点选即用</span>
          <el-button size="small" text type="primary" class="recipe-edit-btn" @click="goTemplates">
            编辑
          </el-button>
        </div>
        <div v-if="!settings.recipes?.length" class="empty-hint">暂无预设配方</div>
        <div v-else class="recipes-grid">
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
            @click="onApplyRecipe(r)"
          >
            <span class="recipe-mini-name">{{ r.name }}</span>
            <span v-if="appliedRecipeKey === r.key" class="recipe-mini-mark"><el-icon :size="12"><Check /></el-icon></span>
            <span v-else-if="isUserRecipe(r.key)" class="recipe-mini-badge">我的</span>
          </button>
        </div>
      </div>

      <el-divider style="margin: 10px 0 8px;" />

      <DimensionSelector
        v-model="settings.selectedDimensions"
        :show-recipes="false"
      />
    </div>

    <!-- ── 完全自定义 Tab ── -->
    <div v-if="activeTab === 'custom'" class="tab-content">
      <CustomPromptPanel />
    </div>
  </div>
</template>

<style scoped>
.prompt-tabs-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* 内部菜单栏：书签样式 */
.inner-menu {
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--app-border, #2a2a2a);
}
.menu-item {
  position: relative;
  padding: 8px 18px;
  border: none;
  background: transparent;
  color: var(--app-text-secondary, #909399);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: color 0.15s, background 0.15s;
  font-family: inherit;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.menu-item:hover {
  color: var(--app-text, #e0e0e0);
  background: var(--app-hover-bg, rgba(255, 255, 255, 0.03));
}
.menu-item.active {
  color: var(--app-accent-1);
  font-weight: 600;
  border-bottom-color: var(--app-accent-1);
}

.tab-content {
  min-height: 280px;
}

/* ====== 预设配方：紧凑小卡片 ====== */
.recipe-section {
  margin-bottom: 0;
}
.recipe-section-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.recipe-section-title {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--app-text-secondary);
}
.recipe-section-hint {
  font-size: 10px;
  color: var(--app-text-placeholder, #6b7085);
}
.recipe-edit-btn {
  margin-left: auto;
  font-size: 12px !important;
}

.recipes-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

/* 小胶囊：宽度随文字，名称完整显示、尽量醒目 */
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
.recipe-mini:active {
  transform: translateY(0);
}
.recipe-mini.applied {
  border-color: transparent;
  color: #fff;
  background: linear-gradient(135deg, var(--app-accent-1), var(--app-accent-2));
  box-shadow: 0 4px 14px rgba(var(--app-accent-1-rgb), 0.35);
}
.recipe-mini.applied:hover {
  filter: brightness(1.05);
  border-color: transparent;
  color: #fff;
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
  opacity: 1;
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
.recipe-mini.applied .recipe-mini-badge {
  display: none;
}

.empty-hint {
  text-align: center;
  padding: 12px 0;
  color: var(--app-text-secondary, #909399);
  font-size: 12px;
}
</style>
