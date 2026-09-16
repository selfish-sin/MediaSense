<script setup>
import { ref, computed, onBeforeUnmount, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useJobStore } from './stores/jobStore'
import { useThemeStore } from './stores/themeStore'
import zhCn from 'element-plus/dist/locale/zh-cn.mjs'
import BilibiliCard from './components/BilibiliCard.vue'
import BackgroundPanel from './components/BackgroundPanel.vue'
import {
  Home, FileText, Cpu, ChevronsLeft, ChevronsRight, LayoutTemplate,
  Database, Sun, Moon, ArrowUp, ArrowDown, Palette,
} from 'lucide-vue-next'

const router = useRouter()
const route = useRoute()
const jobStore = useJobStore()
const theme = useThemeStore()

const activeMenu = computed(() => {
  // 结果页仍高亮「历史」更合理；工作台处理中高亮视频处理
  if (route.path.startsWith('/result')) return '/history'
  return route.path
})

/** 侧栏角标：有任务在跑时显示 */
const hasLiveJob = computed(() => {
  return jobStore.isProcessing || ['pending', 'downloading', 'transcribing', 'preview', 'summarizing'].includes(jobStore.jobStatus)
})
const liveJobLabel = computed(() => {
  const map = {
    pending: '排', downloading: '下', transcribing: '转',
    preview: '确', summarizing: 'AI',
  }
  return map[jobStore.jobStatus] || '…'
})

const handleMenuSelect = (path) => {
  router.push(path)
}

// 侧边栏折叠状态
const isCollapsed = ref(false)
const isNarrow = ref(window.innerWidth < 760)
const menuCollapsed = computed(() => isCollapsed.value || isNarrow.value)
const sidebarWidth = computed(() => isNarrow.value ? '56px' : (isCollapsed.value ? '64px' : '200px'))

// 背景设置抽屉
const showBgPanel = ref(false)

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
  <el-config-provider :locale="zhCn">
  <el-container class="app-shell">
    <!-- 侧边栏 -->
    <el-aside :width="sidebarWidth" class="app-sidebar">
      <div class="app-brand">
        <span v-if="!menuCollapsed" class="app-title">MediaSense</span>
        <span v-else class="app-title app-title-sm">MS</span>
        <span v-if="!menuCollapsed" class="app-subtitle">智能多媒体工作站</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        :collapse="menuCollapsed"
        @select="handleMenuSelect"
        class="app-menu"
      >
        <el-menu-item index="/" :class="{ 'is-live-job': hasLiveJob }">
          <span class="menu-icon-wrap">
            <el-icon><Home /></el-icon>
            <i v-if="hasLiveJob && menuCollapsed" class="menu-live-dot" aria-hidden="true" />
          </span>
          <template #title>
            <span class="menu-label">视频处理</span>
            <span v-if="hasLiveJob" class="menu-live-badge" :title="`任务进行中: ${jobStore.jobStatus}`">
              {{ liveJobLabel }}
            </span>
          </template>
        </el-menu-item>
        <el-menu-item index="/history">
          <el-icon><FileText /></el-icon>
          <template #title><span class="menu-label">历史记录</span></template>
        </el-menu-item>
        <el-menu-item index="/models">
          <el-icon><Cpu /></el-icon>
          <template #title><span class="menu-label">模型管理</span></template>
        </el-menu-item>
        <el-menu-item index="/templates">
          <el-icon><LayoutTemplate /></el-icon>
          <template #title><span class="menu-label">模板管理</span></template>
        </el-menu-item>
        <el-menu-item index="/storage">
          <el-icon><Database /></el-icon>
          <template #title><span class="menu-label">存储管理</span></template>
        </el-menu-item>
      </el-menu>
      <!-- 弹性空间，把底部项推到底部 -->
      <div style="flex: 1;" />
      <!-- 底部：主题切换 / 背景 / B站 / 折叠 -->
      <div class="sidebar-bottom">
        <button
          class="sidebar-action-btn"
          @click="theme.toggle()"
          :title="theme.theme === 'dark' ? '切换到日间模式' : '切换到夜间模式'"
        >
          <el-icon style="font-size: 16px;">
            <Sun v-if="theme.theme === 'dark'" />
            <Moon v-else />
          </el-icon>
          <span v-if="!menuCollapsed" class="sidebar-action-label">
            {{ theme.theme === 'dark' ? '日间' : '夜间' }}
          </span>
        </button>
        <button
          class="sidebar-action-btn"
          @click="showBgPanel = true"
          title="桌面背景与配色"
        >
          <el-icon style="font-size: 16px;"><Palette /></el-icon>
          <span v-if="!menuCollapsed" class="sidebar-action-label">背景</span>
        </button>
        <BilibiliCard :collapsed="menuCollapsed" />
        <button
          v-if="!isNarrow"
          class="sidebar-action-btn"
          @click="isCollapsed = !isCollapsed"
          :title="isCollapsed ? '展开侧栏' : '折叠侧栏'"
        >
          <el-icon style="font-size: 16px;">
            <ChevronsLeft v-if="!isCollapsed" /><ChevronsRight v-else />
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

    <!-- 背景与配色设置抽屉 -->
    <el-drawer
      v-model="showBgPanel"
      title="桌面背景与配色"
      direction="rtl"
      size="380px"
      class="bg-drawer"
    >
      <BackgroundPanel />
    </el-drawer>
  </el-container>
  </el-config-provider>
</template>

<style>
/* ============ 全局光标策略（2026-09-13，"又有i光标了"） ============ */
/* 整个应用按桌面软件对待：非编辑区一律箭头光标、不出现文本光标与拖选
   （此前只修了模板中心页 dca5727，其他页面 I 光标复发，故提升为全局）。 */
body {
  cursor: default;
  user-select: none;
}
/* 可编辑控件显式还原文本光标（user-select 会波及表单控件内部，须逐类放开） */
body input,
body textarea,
body [contenteditable="true"] {
  cursor: text;
  user-select: text;
}
/* 内容区保留选中复制：结果/转录/日志/对话/通知 */
.markdown-body,
.transcript-text,
.thinking-body,
.thinking-text,
.bubble-body,
.user-text,
.log-panel,
.selectable-text,
.el-message,
.el-message-box,
.el-notification {
  cursor: text;
  user-select: text;
}

/* ============ 主题 CSS 变量 ============ */
/* 主色由 themeStore 动态写入 --app-accent-1/2 及 -rgb；此处为兜底默认值（午夜青蓝）。 */
:root[data-theme="dark"] {
  --app-bg: #0b1120;
  --app-dim-color: 11, 17, 32;
  --app-sidebar-bg: linear-gradient(180deg, rgba(15, 23, 38, 0.86) 0%, rgba(11, 17, 32, 0.92) 100%);
  --app-sidebar-solid: #0f1726;
  --app-border: #1e2a3f;
  --app-card-bg: #111b2e;
  --app-card-header-border: #1e2a3f;
  --app-card-glow: 0 8px 28px rgba(0, 0, 0, 0.42);
  --app-input-bg: #16223a;
  --app-input-border: #243352;
  --app-text: #d6dee8;
  --app-text-heading: #eef3f9;
  --app-text-secondary: #8b9bb3;
  --app-text-placeholder: #5c6b82;
  --app-hover-bg: #18243c;
  --app-table-header-bg: #16223a;
  --app-scrollbar-track: #0b1120;
  --app-scrollbar-thumb: #2a3a55;
  --app-scrollbar-thumb-hover: #3d5174;
  --app-chip-bg: #18243c;
  --app-chip-border: #243352;
  --app-chip-text: #d6dee8;
  --app-chip-hover-bg: #1f2d48;
  --app-chip-active-bg: var(--app-accent-1, #38bdf8);
  --app-chip-active-soft: rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.16);
  --app-preview-bg: #16223a;
  --app-preview-text: #b8c4d6;
  --app-override-bar-bg: #2a2318;
  --app-override-bar-border: #5d4a2a;
  --app-recipes-bg: #0f1828;
  --app-accent-1: #38bdf8;
  --app-accent-2: #0ea5e9;
  --app-accent-1-rgb: 56, 189, 248;
  --app-accent-2-rgb: 14, 165, 233;
  --app-accent: var(--app-accent-1);
  --app-accent-rgb: var(--app-accent-1-rgb);
  --app-log-bg: #08101d;
  --app-log-text: #b8c4d6;
  --app-log-time: #7dd3fc;
  --app-log-border: #1a2538;
  --app-log-line-border: #142033;
  --app-code-bg: #16223a;
  --app-code-text: #7dd3fc;
  --app-progress-card-bg-a: #0f1828;
  --app-progress-card-bg-b: #0d1b2e;
  --app-progress-meter-bg: rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.08);
  --app-progress-track: #1a2538;
  --app-search-hl-bg: #6b5e00;
  --app-search-hl-text: #ffe066;
  --app-search-hl-active-bg: #ff9900;
  --app-search-hl-active-text: #111;
  --app-thinking-bg: rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.1);
  --app-thinking-text: #bae6fd;
  --app-engine-ok-bg: #1a3a1a;
  --app-engine-ok-text: #4ade80;
  --app-engine-ok-border: #2d5a2d;
  --app-engine-err-bg: #2a1a1a;
  --app-engine-err-text: #f87171;
  --app-engine-err-border: #4a2020;
}

:root[data-theme="light"] {
  --app-bg: #f0f4fa;
  --app-dim-color: 240, 244, 250;
  --app-sidebar-bg: linear-gradient(180deg, rgba(255, 255, 255, 0.9) 0%, rgba(244, 247, 252, 0.92) 100%);
  --app-sidebar-solid: #ffffff;
  --app-border: #dce4ee;
  --app-card-bg: #ffffff;
  --app-card-header-border: #e8eef6;
  --app-card-glow: 0 8px 24px rgba(15, 23, 42, 0.08);
  --app-input-bg: #ffffff;
  --app-input-border: #cbd6e5;
  --app-text: #1f2a3c;
  --app-text-heading: #0f1a2c;
  --app-text-secondary: #5c6b82;
  --app-text-placeholder: #9ca8bc;
  --app-hover-bg: #ebf1f9;
  --app-table-header-bg: #f4f7fb;
  --app-scrollbar-track: #ebeef3;
  --app-scrollbar-thumb: #c2cddc;
  --app-scrollbar-thumb-hover: #9cabc0;
  --app-chip-bg: #ebf1f9;
  --app-chip-border: #cbd6e5;
  --app-chip-text: #2d3b52;
  --app-chip-hover-bg: #dde6f2;
  --app-chip-active-bg: var(--app-accent-1, #0ea5e9);
  --app-chip-active-soft: rgba(var(--app-accent-1-rgb, 14, 165, 233), 0.1);
  --app-preview-bg: #f4f7fb;
  --app-preview-text: #4b5563;
  --app-override-bar-bg: #fff8eb;
  --app-override-bar-border: #f5dab1;
  --app-recipes-bg: #f7fafd;
  --app-accent-1: #0ea5e9;
  --app-accent-2: #38bdf8;
  --app-accent-1-rgb: 14, 165, 233;
  --app-accent-2-rgb: 56, 189, 248;
  --app-accent: var(--app-accent-1);
  --app-accent-rgb: var(--app-accent-1-rgb);
  --app-log-bg: #eef3f8;
  --app-log-text: #2d3b52;
  --app-log-time: #0ea5e9;
  --app-log-border: #dce4ee;
  --app-log-line-border: #e4ebf3;
  --app-code-bg: #ebf1f9;
  --app-code-text: #0369a1;
  --app-progress-card-bg-a: #ffffff;
  --app-progress-card-bg-b: #f0f9ff;
  --app-progress-meter-bg: rgba(var(--app-accent-1-rgb, 14, 165, 233), 0.06);
  --app-progress-track: #e4ebf3;
  --app-search-hl-bg: #fef08a;
  --app-search-hl-text: #713f12;
  --app-search-hl-active-bg: #f59e0b;
  --app-search-hl-active-text: #111;
  --app-thinking-bg: #f0f9ff;
  --app-thinking-text: #0369a1;
  --app-engine-ok-bg: #dcfce7;
  --app-engine-ok-text: #16a34a;
  --app-engine-ok-border: #bbf7d0;
  --app-engine-err-bg: #fee2e2;
  --app-engine-err-text: #dc2626;
  --app-engine-err-border: #fecaca;
}

/* ============ 全局背景层（图片 + 遮罩） ============ */

/* ============ 全站通用卡片（透明 + 毛玻璃 + 悬停 glow） ============
 * 首页 .wb-card 与各视图 .app-card 共用同一套视觉基准，
 * 由 --app-dim-color（主题底色 RGB）+ --app-card-opacity（透明度滑块）驱动。
 * el-card 上加 class="app-card" 即可继承圆角/悬停/透明；表格保持实底。 */
.app-card,
.wb-card {
  border-radius: 12px;
  border: 1px solid var(--app-border);
  background: rgba(var(--app-dim-color), var(--app-card-opacity, 1));
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  transition: box-shadow 0.2s, border-color 0.2s, transform 0.15s;
  overflow: hidden;
}
.app-card:hover,
.wb-card:hover {
  box-shadow: var(--app-card-glow, 0 8px 28px rgba(0, 0, 0, 0.42));
}
/* el-card 默认有自己的圆角/阴影，加 app-card 后统一覆盖 */
.el-card.app-card,
.el-card.wb-card {
  border-radius: 12px !important;
  overflow: hidden;
}

/* ============ 工作台卡片通用件（HomeView 及拆出的子组件共用） ============
 * 2026-09-12 从 HomeView scoped 提升为全局：拆分子组件后 scoped 样式不跨组件，
 * 这些类需全局才可用；值与 HomeView 原定义一致，视觉零变化。 */
.wb-card-title {
  font-weight: 650;
  letter-spacing: 0.02em;
  background: linear-gradient(135deg, var(--app-accent-1), var(--app-accent-2));
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
.card-head-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  width: 100%;
}
.card-head-hint {
  font-size: 11px;
  color: var(--app-text-secondary);
  letter-spacing: 0.04em;
}
.card-head-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.mono-tag {
  font-family: ui-monospace, Consolas, monospace;
  letter-spacing: 0.04em;
}
/* 列表操作列「更多」下拉按钮（历史页/模型页共用，2026-09-13 提升为全局） */
.row-more-btn {
  margin-left: 12px;
}
/* el-dropdown 默认 vertical-align: top，会让「更多」比同行主操作按钮高 5px（视觉不齐）；
   选择器带 .el-table 是为压过 Element Plus 按需注入的同特异度规则（同特异度时后加载者胜） */
.el-table .row-more-dropdown {
  vertical-align: middle;
}
.row-more-arrow {
  margin-left: 2px;
  font-size: 12px;
}
.log-panel {
  margin-top: 12px;
  max-height: 200px;
  overflow-y: auto;
  background: var(--app-log-bg);
  color: var(--app-log-text);
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid var(--app-log-border);
  font-family: 'Cascadia Code', 'Consolas', ui-monospace, monospace;
  font-size: 12px;
  line-height: 1.65;
}
.log-empty {
  color: var(--app-text-secondary);
  padding: 10px 0;
}
.log-time {
  color: var(--app-log-time);
  margin-right: 8px;
  flex-shrink: 0;
  opacity: 0.9;
}
/* 字段小提示（批量面板等复用；高级选项组件自带同名 scoped 定义） */
.adv-field-hint {
  margin: 0;
  font-size: 11px;
  color: var(--app-text-secondary);
  line-height: 1.4;
}
/* 输入控件通用宽度（输入区组件与转录预览卡共用；2026-09-12 slice-A 自 HomeView 提升） */
.input-main {
  width: 100%;
}

body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  background: var(--app-bg);
  color: var(--app-text);
  transition: background 0.2s, color 0.2s;
  position: relative;
}
/* 背景图片层 */
body::before {
  content: '';
  position: fixed;
  inset: 0;
  z-index: -2;
  background-image: var(--app-bg-image, none);
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
  filter: blur(var(--app-bg-blur, 0px));
  transform: scale(1.04); /* 避免 blur 边缘透出 */
}
/* 遮罩层：让背景图不喧宾夺主，保证内容可读 */
body::after {
  content: '';
  position: fixed;
  inset: 0;
  z-index: -1;
  background: rgba(var(--app-dim-color), 1);
  opacity: calc(var(--app-bg-dim, 28) / 100);
  pointer-events: none;
  transition: opacity 0.2s;
}

.app-shell {
  height: 100vh;
  background: transparent;
}

.app-sidebar {
  background: var(--app-sidebar-bg);
  background-color: var(--app-sidebar-solid);
  border-right: 1px solid var(--app-border);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  transition: width 0.15s ease-out, background 0.2s, border-color 0.2s;
  will-change: width;
  position: relative;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}
.app-sidebar::after {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(ellipse 120% 40% at 50% -10%, rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.18), transparent 70%);
  opacity: 0.9;
}
/* 覆盖 Element Plus el-aside 默认 0.3s width transition */
.el-aside.app-sidebar {
  transition: width 0.15s ease-out, background 0.2s, border-color 0.2s !important;
}
.app-main {
  background: transparent;
  color: var(--app-text);
}

.app-brand {
  position: relative;
  z-index: 1;
  padding: 18px 14px 14px;
  text-align: center;
  display: flex;
  flex-direction: column;
  gap: 2px;
  align-items: center;
}
.app-title {
  font-size: 16px;
  font-weight: 800;
  letter-spacing: 0.04em;
  background: linear-gradient(135deg, var(--app-accent-1, #38bdf8), var(--app-accent-2, #0ea5e9));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}
.app-title-sm {
  font-size: 15px;
  letter-spacing: 0.08em;
}
.app-subtitle {
  font-size: 10px;
  color: var(--app-text-secondary);
  letter-spacing: 0.12em;
  text-transform: uppercase;
  opacity: 0.85;
}

.app-menu {
  border-right: none !important;
  flex: none;
  background: transparent !important;
  position: relative;
  z-index: 1;
  padding: 4px 8px;
}
.app-menu .el-menu-item {
  border-radius: 10px !important;
  margin: 2px 0;
  height: 44px !important;
  line-height: 44px !important;
}
.app-menu .el-menu-item.is-active {
  background: linear-gradient(90deg, rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.22), rgba(var(--app-accent-2-rgb, 14, 165, 233), 0.1)) !important;
  color: var(--app-accent-1) !important;
  box-shadow: inset 3px 0 0 var(--app-accent-1);
}
.menu-label {
  flex: 1;
  display: inline-flex;
  align-items: center;
}
.menu-icon-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
}
.menu-live-dot {
  position: absolute;
  top: 0;
  right: -2px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #22c55e;
  box-shadow: 0 0 0 2px var(--app-sidebar-solid, #0f1726);
  animation: live-badge-pulse 1.6s ease-out infinite;
}
.menu-live-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 18px;
  padding: 0 6px;
  margin-left: 6px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: #fff;
  background: linear-gradient(135deg, #22c55e, #16a34a);
  box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.5);
  animation: live-badge-pulse 1.6s ease-out infinite;
}
.app-menu .is-live-job:not(.is-active) {
  color: var(--app-text) !important;
}
@keyframes live-badge-pulse {
  0% { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.45); }
  70% { box-shadow: 0 0 0 6px rgba(34, 197, 94, 0); }
  100% { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0); }
}

/* ============ 侧边栏底部项（纵向等宽） ============ */
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
  background: rgba(var(--app-dim-color), var(--app-card-opacity, 1)) !important;
  border-color: var(--app-border) !important;
  color: var(--app-text) !important;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}
:root[data-theme="light"] .el-card__header {
  border-bottom-color: var(--app-card-header-border) !important;
}
:root[data-theme="light"] body {
  color: var(--app-text);
}

/* 暗黑模式卡片 */
:root[data-theme="dark"] .el-card {
  background: rgba(var(--app-dim-color), var(--app-card-opacity, 1)) !important;
  border-color: var(--app-border) !important;
  color: var(--app-text) !important;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}
:root[data-theme="dark"] .el-card__header {
  border-bottom-color: var(--app-card-header-border) !important;
}

/* 对话框 / 抽屉 */
:root[data-theme="dark"] .el-dialog {
  --el-dialog-bg-color: #16223a;
}
:root[data-theme="dark"] .el-drawer {
  --el-drawer-bg-color: #0f1828;
}
:root[data-theme="light"] .el-dialog {
  --el-dialog-bg-color: #ffffff;
}
:root[data-theme="light"] .el-drawer {
  --el-drawer-bg-color: #ffffff;
}

/* 表格 — 透明化，跟随卡片透明度透出背景图 */
:root[data-theme="dark"] .el-table {
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: rgba(var(--app-dim-color), 0.5);
  --el-table-border-color: var(--app-border);
  --el-table-row-hover-bg-color: rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.08);
}
/* ★ stripe 斑马纹行：半透明叠加，比奇数行略亮一点点（不突兀） */
:root[data-theme="dark"] .el-table--striped .el-table__body tr.el-table__row--striped td.el-table__cell {
  background-color: rgba(var(--app-dim-color), 0.25) !important;
}
/* 亮色主题表格透明化 */
:root[data-theme="light"] .el-table {
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: rgba(var(--app-dim-color), 0.5);
  --el-table-border-color: var(--app-border);
  --el-table-row-hover-bg-color: rgba(var(--app-accent-1-rgb, 14, 165, 233), 0.06);
}
:root[data-theme="light"] .el-table--striped .el-table__body tr.el-table__row--striped td.el-table__cell {
  background-color: rgba(var(--app-dim-color), 0.25) !important;
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
  background: linear-gradient(135deg, var(--app-accent-1, #38bdf8), var(--app-accent-2, #0ea5e9));
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 6px 18px rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.4);
  transition: transform 0.15s, filter 0.15s;
}
.scroll-jump-btn:hover {
  filter: brightness(1.08);
  transform: translateY(-1px);
}
.scroll-jump-btn .el-icon { font-size: 18px; }

/* 全局 Markdown（结果页等未 scoped 处也能用） */
.markdown-body {
  color: var(--app-text);
  line-height: 1.75;
}
.markdown-body h1,
.markdown-body h2,
.markdown-body h3 {
  color: var(--app-text-heading);
}
.markdown-body code {
  background: var(--app-code-bg);
  color: var(--app-code-text);
  padding: 2px 6px;
  border-radius: 4px;
}
.markdown-body pre {
  background: var(--app-log-bg);
  color: var(--app-log-text);
  padding: 12px 14px;
  border-radius: 8px;
  border: 1px solid var(--app-border);
  overflow-x: auto;
}
.markdown-body blockquote {
  border-left: 4px solid var(--app-accent-1);
  color: var(--app-text-secondary);
  padding-left: 14px;
  margin: 8px 0;
}
.markdown-body th,
.markdown-body td {
  border: 1px solid var(--app-border);
  padding: 8px 12px;
}
.markdown-body th {
  background: var(--app-table-header-bg);
}

/* Markdown 代码块容器 + 右上「复制」按钮（行为见 utils/codeCopy.js 全局委托）
   ⚠ 选择器带 .md-code-block > pre.md-code-pre（特异性 0-3-2）：压制各组件 scoped 的
   `.markdown-body[x] pre`（0-2-1）里自带的圆角/外边距，保证标题条与代码区严丝合缝。 */
.markdown-body .md-code-block {
  position: relative;
  margin: 10px 0;
}
.markdown-body .md-code-block > .md-code-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 10px;
  font-size: 12px;
  color: var(--app-text-secondary);
  background: var(--app-log-bg);
  border: 1px solid var(--app-border);
  border-bottom: none;
  border-radius: 8px 8px 0 0;
}
.markdown-body .md-code-block > .md-code-head > .md-code-lang {
  letter-spacing: 0.3px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.markdown-body .md-code-block > .md-code-head > .md-code-copy {
  flex-shrink: 0;
  padding: 1px 8px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--app-text-secondary);
  background: transparent;
  border: 1px solid var(--app-border);
  border-radius: 4px;
  cursor: pointer;
}
.markdown-body .md-code-block > .md-code-head > .md-code-copy:hover {
  color: var(--app-accent-1);
  border-color: var(--app-accent-1);
}
.markdown-body .md-code-block > .md-code-head > .md-code-copy.is-copied {
  color: #34d399;
  border-color: #34d399;
}
.markdown-body .md-code-block > pre.md-code-pre {
  margin: 0;
  border-radius: 0 0 8px 8px;
}

/* 分页 */
.el-pagination {
  --el-pagination-bg-color: transparent;
  --el-pagination-button-bg-color: var(--app-input-bg);
}

/* 标签/Tag */
:root[data-theme="dark"] .el-tag {
  --el-tag-bg-color: #16223a;
  --el-tag-border-color: #243352;
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
  --el-menu-text-color: #8b9bb3;
  --el-menu-hover-bg-color: #18243c;
  --el-menu-active-color: var(--app-accent-1, #38bdf8);
}
:root[data-theme="light"] .el-menu {
  --el-menu-bg-color: transparent;
  --el-menu-text-color: #5c6b82;
  --el-menu-hover-bg-color: #ebf1f9;
  --el-menu-active-color: var(--app-accent-1, #0ea5e9);
}

/* ============ 业务组件暗色主题 ============ */

/* 批量任务进度面板 */
:root[data-theme="dark"] .batch-panel {
  background: #16223a !important;
  border: 1px solid #243352 !important;
}

/* 思维导图 SVG */
:root[data-theme="dark"] .mindmap-container {
  background: #0f1828 !important;
}
:root[data-theme="dark"] .mindmap-container svg {
  background: #0f1828;
}
/* markmap 节点文字强制亮色 */
:root[data-theme="dark"] .markmap-container svg text {
  fill: #d6dee8 !important;
}
:root[data-theme="dark"] .markmap-container svg path {
  stroke: #2a3a55 !important;
}

/* 背景抽屉内边距 */
.bg-drawer .el-drawer__body {
  padding: 20px;
}

/* ============ 页面容器（2026-09-13 统一；2026-09-17 补弹性间距） ============ */
/* 纵向间距由容器 gap 单点定义：子块不要再写 margin-bottom，否则间距翻倍 */
.page-container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ============ 页面标题栏（2026-09-17 统一：历史/模型/模板/存储/结果五视图共用） ============
 * 此前各页标题各自为政（h2 默认字号 / 18px / 26px h1 + eyebrow），基线和高低不齐。
 * 结构固定为：左 标题(+可选副标题) · 右 操作区，横向 gap 12。 */
.page-header {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  min-height: 32px;
}
/* flex-basis 0：标题块先占满剩余宽度再让位给右侧操作区，
   否则长标题（结果页）会因内容宽度过大被挤到单独一行，标题栏折成三行 */
.page-header-text {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1 1 0%;
  min-width: 0;
}
.page-title {
  margin: 0;
  font-size: 20px;
  font-weight: 650;
  line-height: 1.3;
  letter-spacing: 0.01em;
  color: var(--app-text-heading);
}
.page-subtitle {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--app-text-secondary);
}
.page-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
/* 标题栏内控件统一 32px 高（默认尺寸），消除 default(32) 与 small(24) 同排高低差 */
.page-header-actions .el-button,
.page-header-actions .el-input,
.page-header-actions .el-select {
  height: 32px;
  box-sizing: border-box;
}

/* ============ el-tabs 统一（2026-09-17） ============
 * 全站约定：tabs 自身不套外壳卡片，卡片只出现在各 pane 内容里 ——
 * 避免「卡片套卡片」的双层边框，也让三个带 tabs 的页面（模型/模板/存储）结构一致。 */
.page-container > .el-tabs {
  margin-top: 4px;
}

/* ============ 空态统一（2026-09-13） ============ */
/* Element Plus 默认插图是浅灰纸箱，深色主题下很突兀：换成主色柔光圆点 + 次级色文案 */
.el-empty {
  padding: 16px 0 !important;
}
.el-empty__image svg {
  display: none;
}
.el-empty__image::before {
  content: '';
  display: block;
  width: 52px;
  height: 52px;
  margin: 0 auto 6px;
  border-radius: 50%;
  background: radial-gradient(circle at 32% 32%, var(--app-accent-1, #38bdf8), var(--app-accent-2, #7b5cff));
  opacity: 0.18;
}
.el-empty__description p {
  color: var(--app-text-secondary) !important;
  font-size: 13px !important;
}
</style>
