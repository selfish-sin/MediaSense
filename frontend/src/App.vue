<script setup>
import { ref, computed, onBeforeUnmount, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useJobStore } from './stores/jobStore'
import { useThemeStore } from './stores/themeStore'
import BilibiliCard from './components/BilibiliCard.vue'
import {
  HomeFilled, Document, Cpu, Setting, Expand, Fold, Collection, Switch,
  Sunny, Moon, ArrowUp, ArrowDown, Files,
} from '@element-plus/icons-vue'

const router = useRouter()
const route = useRoute()
const jobStore = useJobStore()
const theme = useThemeStore()

const activeMenu = computed(() => route.path)

const handleMenuSelect = (path) => {
  router.push(path)
}

// 侧边栏折叠状态
const isCollapsed = ref(false)
const isNarrow = ref(window.innerWidth < 760)
const menuCollapsed = computed(() => isCollapsed.value || isNarrow.value)
const sidebarWidth = computed(() => isNarrow.value ? '56px' : (isCollapsed.value ? '64px' : '200px'))

function updateViewport() {
  isNarrow.value = window.innerWidth < 760
}

onMounted(() => window.addEventListener('resize', updateViewport))
onBeforeUnmount(() => window.removeEventListener('resize', updateViewport))

// 全局滚动：到达顶部 / 到达底部 两个按钮
const mainRef = ref(null)
function mainEl() {
  // el-main 的 ref 指向组件实例，真实 DOM 在 $el
  return mainRef.value?.$el || null
}
// 取当前真正可滚动的容器：优先 el-main，否则回退到 window
function scroller() {
  const el = mainEl()
  if (el && el.scrollHeight - el.clientHeight > 1) return el
  return window
}
function scrollToTop() {
  const s = scroller()
  if (s === window) window.scrollTo({ top: 0, behavior: 'smooth' })
  else s.scrollTo({ top: 0, behavior: 'smooth' })
}
function scrollToBottom() {
  const s = scroller()
  if (s === window) window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' })
  else s.scrollTo({ top: s.scrollHeight, behavior: 'smooth' })
}
</script>

<template>
  <el-container style="height: 100vh;">
    <!-- 侧边栏 -->
    <el-aside :width="sidebarWidth" class="app-sidebar">
      <div style="padding: 16px 12px 8px; text-align: center;">
        <span v-if="!menuCollapsed" class="app-title">
          智能处理工作站
        </span>
        <span v-else class="app-title app-title-sm">
          处理
        </span>
      </div>
      <el-menu
        :default-active="activeMenu"
        :collapse="menuCollapsed"
        @select="handleMenuSelect"
        style="border-right: none; flex: none; background: transparent;"
      >
        <el-menu-item index="/">
          <el-icon><HomeFilled /></el-icon>
          <span>视频处理</span>
        </el-menu-item>
        <el-menu-item index="/history">
          <el-icon><Document /></el-icon>
          <span>历史记录</span>
        </el-menu-item>
        <el-menu-item index="/models">
          <el-icon><Cpu /></el-icon>
          <span>模型管理</span>
        </el-menu-item>
        <el-menu-item index="/templates">
          <el-icon><Collection /></el-icon>
          <span>模板管理</span>
        </el-menu-item>
        <el-menu-item index="/storage">
          <el-icon><Files /></el-icon>
          <span>存储管理</span>
        </el-menu-item>
      </el-menu>
      <!-- 弹性空间，把底部三项推到底部 -->
      <div style="flex: 1;" />
      <!-- 底部三项：主题切换 / B站 / 折叠（纵向等宽） -->
      <div class="sidebar-bottom">
        <button
          class="sidebar-action-btn"
          @click="theme.toggle()"
          :title="theme.theme === 'dark' ? '切换到日间模式' : '切换到夜间模式'"
        >
          <el-icon style="font-size: 16px;">
            <Sunny v-if="theme.theme === 'dark'" />
            <Moon v-else />
          </el-icon>
          <span v-if="!menuCollapsed" class="sidebar-action-label">
            {{ theme.theme === 'dark' ? '日间' : '夜间' }}
          </span>
        </button>
        <BilibiliCard :collapsed="menuCollapsed" />
        <button
          v-if="!isNarrow"
          class="sidebar-action-btn"
          @click="isCollapsed = !isCollapsed"
          :title="isCollapsed ? '展开侧栏' : '折叠侧栏'"
        >
          <el-icon style="font-size: 16px;">
            <Fold v-if="!isCollapsed" /><Expand v-else />
          </el-icon>
          <span v-if="!menuCollapsed" class="sidebar-action-label">{{ isCollapsed ? '展开' : '折叠' }}</span>
        </button>
      </div>
    </el-aside>

    <!-- 主内容区 -->
    <el-main ref="mainRef" class="app-main">
      <router-view v-slot="{ Component }">
        <keep-alive :include="['HomeView']">
          <component :is="Component" />
        </keep-alive>
      </router-view>
    </el-main>
    <!-- 全局滚动：到达顶部 / 到达底部 两个独立按钮 -->
    <div class="scroll-jump">
      <button class="scroll-jump-btn" @click="scrollToTop" title="回到顶部">
        <el-icon><ArrowUp /></el-icon>
      </button>
      <button class="scroll-jump-btn" @click="scrollToBottom" title="回到底部">
        <el-icon><ArrowDown /></el-icon>
      </button>
    </div>
  </el-container>
</template>

<style>
/* ============ 主题 CSS 变量 ============ */
:root[data-theme="dark"] {
  --app-bg: #141414;
  --app-sidebar-bg: #1a1a1a;
  --app-border: #2a2a2a;
  --app-card-bg: #1e1e1e;
  --app-card-header-border: #2a2a2a;
  --app-input-bg: #252525;
  --app-input-border: #333;
  --app-text: #e0e0e0;
  --app-text-heading: #e8e8e8;
  --app-text-secondary: #909399;
  --app-text-placeholder: #666;
  --app-hover-bg: #2a2a2a;
  --app-table-header-bg: #252525;
  --app-scrollbar-track: #1a1a1a;
  --app-scrollbar-thumb: #444;
  --app-scrollbar-thumb-hover: #555;
  --app-chip-bg: #545659;
  --app-chip-border: #4c4d4f;
  --app-chip-text: #e6e8eb;
  --app-chip-hover-bg: #6a6d70;
  --app-chip-active-bg: #409eff;
  --app-preview-bg: #2d2f33;
  --app-preview-text: #d6d8db;
  --app-override-bar-bg: #3a2f1a;
  --app-override-bar-border: #5d4a2a;
  --app-recipes-bg: #1f1f1f;
}

:root[data-theme="light"] {
  --app-bg: #f5f7fa;
  --app-sidebar-bg: #ffffff;
  --app-border: #e4e7ed;
  --app-card-bg: #ffffff;
  --app-card-header-border: #ebeef5;
  --app-input-bg: #ffffff;
  --app-input-border: #dcdfe6;
  --app-text: #303133;
  --app-text-heading: #303133;
  --app-text-secondary: #909399;
  --app-text-placeholder: #c0c4cc;
  --app-hover-bg: #f5f7fa;
  --app-table-header-bg: #f5f7fa;
  --app-scrollbar-track: #f0f0f0;
  --app-scrollbar-thumb: #c0c4cc;
  --app-scrollbar-thumb-hover: #909399;
  --app-chip-bg: #ecf0f5;
  --app-chip-border: #c0c6cc;
  --app-chip-text: #303133;
  --app-chip-hover-bg: #e0e6ed;
  --app-chip-active-bg: #409eff;
  --app-preview-bg: #f5f7fa;
  --app-preview-text: #606266;
  --app-override-bar-bg: #fdf6ec;
  --app-override-bar-border: #f5dab1;
  --app-recipes-bg: #f8f9fb;
}

/* 全局 */
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  background: var(--app-bg);
  color: var(--app-text);
  transition: background 0.15s, color 0.15s;
}

.app-sidebar {
  background: var(--app-sidebar-bg);
  border-right: 1px solid var(--app-border);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  transition: width 0.15s ease-out, background 0.2s, border-color 0.2s;
  will-change: width;
}
/* 覆盖 Element Plus el-aside 默认 0.3s width transition */
.el-aside.app-sidebar {
  transition: width 0.15s ease-out, background 0.2s, border-color 0.2s !important;
}
.app-main {
  background: var(--app-bg);
  color: var(--app-text);
}

.app-title {
  font-size: 15px;
  font-weight: 700;
  background: linear-gradient(135deg, #6366f1, #a855f7);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}
.app-title-sm {
  font-size: 14px;
}

/* ============ 侧边栏底部三项（纵向等宽） ============ */
.sidebar-bottom {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 8px 12px;
  border-top: 1px solid var(--app-border);
}
.sidebar-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  height: 36px;
  padding: 0 12px;
  box-sizing: border-box;
  border: none;
  background: transparent;
  color: var(--app-text-secondary, #c0c4cc);
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  font-family: inherit;
  transition: background 0.2s, color 0.2s;
}
.sidebar-action-btn:hover {
  background: var(--app-hover-bg, #2a2a2a);
  color: var(--app-text, #e0e0e0);
}
.sidebar-action-label {
  white-space: nowrap;
  overflow: hidden;
}

/* ============ Element Plus 暗黑/日间覆盖（通过 CSS 变量驱动） ============ */

/* 日间模式：移除 dark class 的影响，恢复 Element Plus 默认亮色 */
:root[data-theme="light"] .el-card {
  background: var(--app-card-bg) !important;
  border-color: var(--app-border) !important;
  color: var(--app-text) !important;
}
:root[data-theme="light"] .el-card__header {
  border-bottom-color: var(--app-card-header-border) !important;
}
:root[data-theme="light"] body {
  color: var(--app-text);
}

/* 暗黑模式卡片 */
:root[data-theme="dark"] .el-card {
  background: var(--app-card-bg) !important;
  border-color: var(--app-border) !important;
  color: var(--app-text) !important;
}
:root[data-theme="dark"] .el-card__header {
  border-bottom-color: var(--app-card-header-border) !important;
}

/* 对话框 */
:root[data-theme="dark"] .el-dialog {
  --el-dialog-bg-color: #1e1e1e;
}
:root[data-theme="dark"] .el-drawer {
  --el-drawer-bg-color: #1e1e1e;
}
:root[data-theme="light"] .el-dialog {
  --el-dialog-bg-color: #ffffff;
}

/* 表格 */
:root[data-theme="dark"] .el-table {
  --el-table-bg-color: #1e1e1e;
  --el-table-tr-bg-color: #1e1e1e;
  --el-table-header-bg-color: #252525;
  --el-table-border-color: #2a2a2a;
  --el-table-row-hover-bg-color: #2a2a2a;
}

/* 页面标题 */
h2, h3 { color: var(--app-text-heading) !important; }

/* 输入框/选择器 */
:root[data-theme="dark"] .el-input__wrapper,
:root[data-theme="dark"] .el-textarea__inner,
:root[data-theme="dark"] .el-select .el-input__wrapper {
  background-color: var(--app-input-bg) !important;
  box-shadow: 0 0 0 1px var(--app-input-border) !important;
}
:root[data-theme="dark"] .el-input__inner,
:root[data-theme="dark"] .el-textarea__inner {
  color: var(--app-text) !important;
}
:root[data-theme="dark"] .el-input__inner::placeholder,
:root[data-theme="dark"] .el-textarea__inner::placeholder {
  color: var(--app-text-placeholder) !important;
}

/* 滚动条 */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--app-scrollbar-track); }
::-webkit-scrollbar-thumb { background: var(--app-scrollbar-thumb); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--app-scrollbar-thumb-hover); }

.scroll-jump {
  position: fixed;
  right: 28px;
  bottom: 28px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  z-index: 2000;
}
.scroll-jump-btn {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  border: none;
  background: #6366f1;
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
  transition: background 0.2s;
}
.scroll-jump-btn:hover { background: #4f46e5; }
.scroll-jump-btn .el-icon { font-size: 18px; }

/* 分页 */
.el-pagination {
  --el-pagination-bg-color: transparent;
  --el-pagination-button-bg-color: var(--app-input-bg);
}

/* 标签/Tag */
:root[data-theme="dark"] .el-tag {
  --el-tag-bg-color: #252525;
  --el-tag-border-color: #333;
}

/* el-menu 透明背景（继承侧边栏） */
.el-menu {
  background: transparent !important;
}
.el-menu-item {
  font-size: 14px !important;
}
:root[data-theme="dark"] .el-menu {
  --el-menu-bg-color: transparent;
  --el-menu-text-color: #c0c4cc;
  --el-menu-hover-bg-color: #2a2a2a;
  --el-menu-active-color: #409eff;
}

/* ============ 业务组件暗色主题 ============ */

/* 批量任务进度面板 */
:root[data-theme="dark"] .batch-panel {
  background: #252525 !important;
  border: 1px solid #333 !important;
}

/* 思维导图 SVG */
:root[data-theme="dark"] .mindmap-container {
  background: #1a1a1a !important;
}
:root[data-theme="dark"] .mindmap-container svg {
  background: #1a1a1a;
}
/* markmap 节点文字强制亮色 */
:root[data-theme="dark"] .markmap-container svg text {
  fill: #e0e0e0 !important;
}
:root[data-theme="dark"] .markmap-container svg path {
  stroke: #444 !important;
}
</style>
