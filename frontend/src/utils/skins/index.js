/**
 * 内置皮肤数据（纯数据，无逻辑）。
 *
 * 皮肤支持两种声明模式（详见 task design.md §4）：
 * - tokens：全量覆盖 —— 直接给出 `--app-*` 的完整取值，所见即所得；
 * - palette（Phase 4 引入）：只给 ~20 个原始色，其余由 skin.js 表驱动派生。
 *
 * `legacy-*` 两套皮肤走 tokens 模式，取值与重构前 App.vue 的
 * `:root[data-theme="dark"|"light"]` 块**逐值相同**——这是「等价重构」的基准：
 * Phase 2 完成后界面观感必须与重构前一致（由 research/baseline 的
 * tokens-*.json 快照自动比对）。
 *
 * 约定：
 * - token 名与语义冻结，禁止改名/删除（21 个组件直接消费 var(--app-*)）；
 * - 含主色的 token 写成 `var(--app-accent-*)` 引用形式，由 themeStore.applyAccent()
 *   在注入后覆盖，从而随用户主色联动；
 * - family 用于「日间/夜间」切换时找同族另一 mode 的皮肤。
 */

/** 午夜青蓝（深） */
export const legacyDark = {
  id: 'legacy-dark',
  name: '午夜青蓝（深）',
  family: 'legacy',
  mode: 'dark',
  tokens: {
    // 底色 / 表面
    '--app-bg': '#0b1120',
    '--app-dim-color': '11, 17, 32',
    '--app-card-bg': '#111b2e',
    '--app-sidebar-bg': 'linear-gradient(180deg, rgba(15, 23, 38, 0.86) 0%, rgba(11, 17, 32, 0.92) 100%)',
    '--app-sidebar-solid': '#0f1726',
    // 描边 / 阴影
    '--app-border': '#1e2a3f',
    '--app-card-header-border': '#1e2a3f',
    '--app-card-glow': '0 8px 28px rgba(0, 0, 0, 0.42)',
    // 输入
    '--app-input-bg': '#16223a',
    '--app-input-border': '#243352',
    '--app-hover-bg': '#18243c',
    '--app-table-header-bg': '#16223a',
    // 文字
    '--app-text': '#d6dee8',
    '--app-text-heading': '#eef3f9',
    '--app-text-secondary': '#8b9bb3',
    '--app-text-placeholder': '#5c6b82',
    // 滚动条
    '--app-scrollbar-track': '#0b1120',
    '--app-scrollbar-thumb': '#2a3a55',
    '--app-scrollbar-thumb-hover': '#3d5174',
    // chip
    '--app-chip-bg': '#18243c',
    '--app-chip-border': '#243352',
    '--app-chip-text': '#d6dee8',
    '--app-chip-hover-bg': '#1f2d48',
    '--app-chip-active-bg': 'var(--app-accent-1, #38bdf8)',
    '--app-chip-active-soft': 'rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.16)',
    // 预览 / 配方
    '--app-preview-bg': '#16223a',
    '--app-preview-text': '#b8c4d6',
    '--app-recipes-bg': '#0f1828',
    // 覆盖提示条
    '--app-override-bar-bg': '#2a2318',
    '--app-override-bar-border': '#5d4a2a',
    // 主色（默认值；运行时由 applyAccent 覆盖为用户选定色）
    '--app-accent-1': '#38bdf8',
    '--app-accent-2': '#0ea5e9',
    '--app-accent-1-rgb': '56, 189, 248',
    '--app-accent-2-rgb': '14, 165, 233',
    '--app-accent': 'var(--app-accent-1)',
    '--app-accent-rgb': 'var(--app-accent-1-rgb)',
    // 日志
    '--app-log-bg': '#08101d',
    '--app-log-text': '#b8c4d6',
    '--app-log-time': '#7dd3fc',
    '--app-log-border': '#1a2538',
    '--app-log-line-border': '#142033',
    // 代码
    '--app-code-bg': '#16223a',
    '--app-code-text': '#7dd3fc',
    // 进度
    '--app-progress-card-bg-a': '#0f1828',
    '--app-progress-card-bg-b': '#0d1b2e',
    '--app-progress-meter-bg': 'rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.08)',
    '--app-progress-track': '#1a2538',
    // 搜索高亮
    '--app-search-hl-bg': '#6b5e00',
    '--app-search-hl-text': '#ffe066',
    '--app-search-hl-active-bg': '#ff9900',
    '--app-search-hl-active-text': '#111',
    // 思考态
    '--app-thinking-bg': 'rgba(var(--app-accent-1-rgb, 56, 189, 248), 0.1)',
    '--app-thinking-text': '#bae6fd',
    // 引擎状态
    '--app-engine-ok-bg': '#1a3a1a',
    '--app-engine-ok-text': '#4ade80',
    '--app-engine-ok-border': '#2d5a2d',
    '--app-engine-err-bg': '#2a1a1a',
    '--app-engine-err-text': '#f87171',
    '--app-engine-err-border': '#4a2020',
  },
}

/** 午夜青蓝（浅） */
export const legacyLight = {
  id: 'legacy-light',
  name: '午夜青蓝（浅）',
  family: 'legacy',
  mode: 'light',
  tokens: {
    // 底色 / 表面
    '--app-bg': '#f0f4fa',
    '--app-dim-color': '240, 244, 250',
    '--app-card-bg': '#ffffff',
    '--app-sidebar-bg': 'linear-gradient(180deg, rgba(255, 255, 255, 0.9) 0%, rgba(244, 247, 252, 0.92) 100%)',
    '--app-sidebar-solid': '#ffffff',
    // 描边 / 阴影
    '--app-border': '#dce4ee',
    '--app-card-header-border': '#e8eef6',
    '--app-card-glow': '0 8px 24px rgba(15, 23, 42, 0.08)',
    // 输入
    '--app-input-bg': '#ffffff',
    '--app-input-border': '#cbd6e5',
    '--app-hover-bg': '#ebf1f9',
    '--app-table-header-bg': '#f4f7fb',
    // 文字
    '--app-text': '#1f2a3c',
    '--app-text-heading': '#0f1a2c',
    '--app-text-secondary': '#5c6b82',
    '--app-text-placeholder': '#9ca8bc',
    // 滚动条
    '--app-scrollbar-track': '#ebeef3',
    '--app-scrollbar-thumb': '#c2cddc',
    '--app-scrollbar-thumb-hover': '#9cabc0',
    // chip
    '--app-chip-bg': '#ebf1f9',
    '--app-chip-border': '#cbd6e5',
    '--app-chip-text': '#2d3b52',
    '--app-chip-hover-bg': '#dde6f2',
    '--app-chip-active-bg': 'var(--app-accent-1, #0ea5e9)',
    '--app-chip-active-soft': 'rgba(var(--app-accent-1-rgb, 14, 165, 233), 0.1)',
    // 预览 / 配方
    '--app-preview-bg': '#f4f7fb',
    '--app-preview-text': '#4b5563',
    '--app-recipes-bg': '#f7fafd',
    // 覆盖提示条
    '--app-override-bar-bg': '#fff8eb',
    '--app-override-bar-border': '#f5dab1',
    // 主色（默认值；运行时由 applyAccent 覆盖为用户选定色）
    '--app-accent-1': '#0ea5e9',
    '--app-accent-2': '#38bdf8',
    '--app-accent-1-rgb': '14, 165, 233',
    '--app-accent-2-rgb': '56, 189, 248',
    '--app-accent': 'var(--app-accent-1)',
    '--app-accent-rgb': 'var(--app-accent-1-rgb)',
    // 日志
    '--app-log-bg': '#eef3f8',
    '--app-log-text': '#2d3b52',
    '--app-log-time': '#0ea5e9',
    '--app-log-border': '#dce4ee',
    '--app-log-line-border': '#e4ebf3',
    // 代码
    '--app-code-bg': '#ebf1f9',
    '--app-code-text': '#0369a1',
    // 进度
    '--app-progress-card-bg-a': '#ffffff',
    '--app-progress-card-bg-b': '#f0f9ff',
    '--app-progress-meter-bg': 'rgba(var(--app-accent-1-rgb, 14, 165, 233), 0.06)',
    '--app-progress-track': '#e4ebf3',
    // 搜索高亮
    '--app-search-hl-bg': '#fef08a',
    '--app-search-hl-text': '#713f12',
    '--app-search-hl-active-bg': '#f59e0b',
    '--app-search-hl-active-text': '#111',
    // 思考态
    '--app-thinking-bg': '#f0f9ff',
    '--app-thinking-text': '#0369a1',
    // 引擎状态
    '--app-engine-ok-bg': '#dcfce7',
    '--app-engine-ok-text': '#16a34a',
    '--app-engine-ok-border': '#bbf7d0',
    '--app-engine-err-bg': '#fee2e2',
    '--app-engine-err-text': '#dc2626',
    '--app-engine-err-border': '#fecaca',
  },
}

/** 全部内置皮肤（面板按此顺序渲染）。 */
export const BUILTIN_SKINS = [legacyDark, legacyLight]

/** 默认皮肤 id（缺失/非法时的回退目标）。 */
export const DEFAULT_SKIN_ID = 'legacy-dark'
