<script setup>
/**
 * 总结配置卡片内部菜单（上下结构）
 * - 顶部：内部菜单栏（书签 tab）「维度预设」→「完全自定义」
 * - 维度预设顶部：预设配方卡片网格（点即应用，不展开内容）
 */
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useSettingsStore } from '../stores/settingsStore'
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
    // 切回维度预设：关闭 fullOverride
    settings.toggleFullOverride(false)
  }
}

function onApplyRecipe(recipe) {
  settings.applyRecipe(recipe)
  appliedRecipeKey.value = recipe.key
}

function goTemplates() {
  router.push('/templates')
}

function isUserRecipe(key) {
  return key && key.startsWith('usr_')
}
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
      <!-- 预设配方卡片（点即应用，不展示内容） -->
      <div class="recipe-section">
        <div class="recipe-section-head">
          <span class="recipe-section-title">预设配方</span>
          <el-button size="small" text type="primary" @click="goTemplates">编辑配方</el-button>
        </div>
        <div v-if="!settings.recipes?.length" class="empty-hint">暂无预设配方</div>
        <div v-else class="recipes-grid">
          <div
            v-for="r in settings.recipes"
            :key="r.key"
            class="recipe-card"
            :class="{ applied: appliedRecipeKey === r.key }"
            @click="onApplyRecipe(r)"
          >
            <div class="recipe-head">
              <span class="recipe-name">{{ r.name }}</span>
              <el-tag v-if="appliedRecipeKey === r.key" type="success" size="small">已应用</el-tag>
              <el-tag v-else-if="isUserRecipe(r.key)" size="small" type="info">我的</el-tag>
            </div>
            <div class="recipe-desc">{{ r.desc }}</div>
          </div>
        </div>
      </div>

      <el-divider style="margin: 8px 0;" />

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
  gap: 12px;
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
  background: var(--app-hover-bg, rgba(255,255,255,0.03));
}
.menu-item.active {
  color: var(--app-text, #e0e0e0);
  font-weight: 600;
  border-bottom-color: #409eff;
}

.tab-content {
  min-height: 300px;
}

/* ====== 预设配方卡片 ====== */
.recipe-section {
  margin-bottom: 2px;
}
.recipe-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.recipe-section-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--app-text);
}
.recipes-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 4px;
}
@media (max-width: 600px) {
  .recipes-grid {
    grid-template-columns: 1fr;
  }
}
.recipe-card {
  border: 1px solid var(--app-border, #2a2a2a);
  border-radius: 5px;
  padding: 5px 7px;
  transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;
  display: flex;
  flex-direction: column;
  cursor: pointer;
}
.recipe-card:hover {
  border-color: #409eff;
  box-shadow: 0 0 0 1px rgba(64, 158, 255, 0.15);
}
.recipe-card.applied {
  border-color: #67c23a;
  background: rgba(103, 194, 58, 0.04);
}
.recipe-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-bottom: 2px;
}
.recipe-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--app-text, #e0e0e0);
}
.recipe-desc {
  font-size: 10px;
  color: var(--app-text-secondary, #909399);
  line-height: 1.3;
}
.empty-hint {
  text-align: center;
  padding: 24px 0;
  color: var(--app-text-secondary, #909399);
  font-size: 13px;
}
</style>
