# 背景与皮肤系统重构 - Design

> 形态：`PROJECT-SHAPE = Web 全栈（frontend-only 改动）`｜核心价值 = 交互/体验 → **前端先行**
> 契约一致性：**不适用**（不改任何 API/IPC/schema）

---

## 0. 范围收缩说明（2026-10-05 用户确认，L → M1）

**本任务只做**：§2 皮肤层 token 化、§3 token 清单、§4 皮肤格式与内置皮肤包、§5 效果层（scrim + 玻璃 + surface）、§6.1 背景类型扩展（含动态预设）、§6.4 自动适配。

**移交子任务「背景图库（IndexedDB）」**：§6.2 的 IndexedDB 存储、§6.3 的迁移方案、§8 风险表中与迁移相关的条目。

保留这些章节原文是刻意的：子任务可直接继承，不必重新调研。
本任务的回滚因此退化为单纯 `git revert`（无数据迁移 = 无不可逆操作）。

---

## 1. 用户路径（先定行为，再定实现）

| # | 起点 | 操作 | 成功看到 | 失败看到 |
|---|---|---|---|---|
| U1 | 工作台 | 打开设置面板 → 点「Catppuccin Mocha」 | 全站立即换色，无闪白、无错位 | 皮肤数据非法 → 「皮肤已损坏，已回退默认」并保持原皮肤 |
| U2 | 面板 | 上传一张亮色照片 | 图片成为氛围背景；系统给出「建议遮罩 42%」，一键采用后文字清晰 | 图太大/格式不支持/存储失败 → 对应可行动提示 |
| U3 | 面板 | 拖动「暗角强度」 | 图片边缘压暗、卡片更突出；滑到 0 图片完全不被压暗 | — |
| U4 | 面板 | 从图库切换第 3 张背景 | 立即切换，无闪烁 | 图库不可用 → 提示并回到单图模式 |
| U5 | 面板 | 点「导出」皮肤 | 下载 `catppuccin-mocha.json` | 序列化失败 → 提示 |
| U6 | 面板 | 选一个动态背景并切到别的窗口 | 动态背景暂停（CPU 不再消耗） | 不支持动效 → 静默用静态帧（并说明） |
| U7 | 老用户升级 | 打开应用 | 原有自定义背景仍正常显示 | 归档失败 → 背景照常显示 + 提示「建议重新上传以启用图库」 |

---

## 2. 架构：三层解耦

```
┌─────────────────────────────────────────────────────────────┐
│ 皮肤层 skin（纯数据）                                        │
│   utils/skins/*.js  →  { palette?, tokens?, glass?, scrim? } │
│   utils/skin.js     →  合成 + 派生 → 60+ 语义 token          │
│                          ↓（写入 :root CSS 变量）            │
├─────────────────────────────────────────────────────────────┤
│ 效果层 effects（参数化样式）                                 │
│   scrim 多层渐变 / 径向暗角 / 玻璃三件套 / surface 分档      │
│   消费方：App.vue 的 body::before|after + .app-card|.wb-card │
├─────────────────────────────────────────────────────────────┤
│ 背景层 background（媒体）                                    │
│   none | builtin(程序化 SVG) | image(IndexedDB 图库)         │
│   | animated(Canvas/CSS，失焦暂停)                           │
└─────────────────────────────────────────────────────────────┘
```

**唯一写入点**：`themeStore` 是唯一把 CSS 变量写到 `document.documentElement` 的地方。
**唯一消费面**：组件只读 `var(--app-*)`（当前 21 个文件已在这么做）。
**推论（本设计的基石）**：只要**不改变既有变量名与语义**，全部组件零改动。这是 Phase 1 必须证明的命题。

### 2.1 模块职责

| 模块 | 职责 | 不做什么 |
|---|---|---|
| `utils/skin.js` | 皮肤合成 + 语义 token 派生（纯函数，可单测） | 不碰 DOM、不碰存储 |
| `utils/skins/index.js` | 内置皮肤数据（含 `legacy-dark`/`legacy-light` 零变化基线） | 不含逻辑 |
| `stores/themeStore.js` | 状态 + 持久化 + 把 token 写到 `:root` | 不含派生算法（委托 skin.js） |
| `utils/effects.js` | 效果参数 → CSS 变量的换算（纯函数） | 不碰背景媒体 |
| `utils/bgStore.js` | IndexedDB 图库（增删查）+ 迁移 | 不含渲染 |
| `utils/backgrounds.js` | 程序化背景生成（含新增动态预设） | 不含状态 |
| `utils/imageFit.js` | 图片亮度/对比分析 → 推荐参数（纯计算） | 不碰存储 |
| `components/BackgroundPanel.vue` | 面板 UI（渲染 + 事件） | 不含业务算法 |

---

## 3. Token 清单（关键产出）

### 3.1 既有 token（**60 个，名字与语义全部冻结，禁止改名/删除**）

来源：`App.vue:231-353`。分组：

| 组 | token |
|---|---|
| 底色/表面 | `--app-bg` `--app-dim-color` `--app-card-bg` `--app-sidebar-bg` `--app-sidebar-solid` |
| 描边/阴影 | `--app-border` `--app-card-header-border` `--app-card-glow` |
| 输入 | `--app-input-bg` `--app-input-border` `--app-hover-bg` `--app-table-header-bg` |
| 文字 | `--app-text` `--app-text-heading` `--app-text-secondary` `--app-text-placeholder` |
| 滚动条 | `--app-scrollbar-track` `--app-scrollbar-thumb` `--app-scrollbar-thumb-hover` |
| chip | `--app-chip-bg` `--app-chip-border` `--app-chip-text` `--app-chip-hover-bg` `--app-chip-active-bg` `--app-chip-active-soft` |
| 预览/配方 | `--app-preview-bg` `--app-preview-text` `--app-recipes-bg` |
| 覆盖提示条 | `--app-override-bar-bg` `--app-override-bar-border` |
| 主色 | `--app-accent-1` `--app-accent-2` `--app-accent-1-rgb` `--app-accent-2-rgb` `--app-accent` `--app-accent-rgb` |
| 日志 | `--app-log-bg` `--app-log-text` `--app-log-time` `--app-log-border` `--app-log-line-border` |
| 代码 | `--app-code-bg` `--app-code-text` |
| 进度 | `--app-progress-card-bg-a` `--app-progress-card-bg-b` `--app-progress-meter-bg` `--app-progress-track` |
| 搜索高亮 | `--app-search-hl-bg` `--app-search-hl-text` `--app-search-hl-active-bg` `--app-search-hl-active-text` |
| 思考态 | `--app-thinking-bg` `--app-thinking-text` |
| 引擎状态 | `--app-engine-ok-bg` `--app-engine-ok-text` `--app-engine-ok-border` `--app-engine-err-bg` `--app-engine-err-text` `--app-engine-err-border` |

运行时由 `themeStore` 写入的 4 个：`--app-bg-image` `--app-bg-blur`（保留）、`--app-bg-dim`（**废弃**，见 3.3）、`--app-card-opacity`（保留为 surface-1 别名）。

### 3.2 新增 token

**效果层（scrim 多层）**

| token | 含义 | 值域 |
|---|---|---|
| `--app-scrim-rgb` | 中性遮罩色（dark = `0,0,0`；light = `255,255,255`） | RGB 三元组 |
| `--app-scrim-dim-a` | 整体浓度 alpha | 0–0.70 |
| `--app-scrim-top-a` | 顶部渐晕 alpha | 0–0.45 |
| `--app-scrim-bottom-a` | 底部渐晕 alpha | 0–0.60 |
| `--app-scrim-vignette-a` | 径向暗角 alpha | 0–0.65 |

**玻璃层（三件套 + surface 分档）**

| token | 含义 | 默认 |
|---|---|---|
| `--app-glass-blur` | 卡片模糊半径 | `14px` |
| `--app-glass-saturate` | 饱和度补偿 | `170%` |
| `--app-glass-border` | 高光描边（带 alpha 的白/黑） | dark `rgba(255,255,255,.14)` / light `rgba(255,255,255,.75)` |
| `--app-surface-1-opacity` | 宿主卡片透明度 | `cardOpacity` 基准 |
| `--app-surface-2-opacity` | 内嵌块透明度 | 基准 +8% |
| `--app-surface-3-opacity` | chip/输入透明度 | 基准 +16% |

> `--app-card-opacity` **保留**并恒等于 `--app-surface-1-opacity`（向后兼容：现有 `.app-card`/`.wb-card` 与第三方覆盖不会失效）。

### 3.3 废弃与兼容

- `--app-bg-dim`：语义被拆为 4 个 scrim 变量 → 保留写入（恒等于 `dim` 值）但不再被消费，Phase 4 再删。
- `--app-bg-blur`：保留（作用于 `body::before` 的 `filter: blur()`，与卡片玻璃是两回事，不合并）。
- 旧背景对象字段 `dim`：解释为「整体浓度」，映射到 `--app-scrim-dim-a`。

---

## 4. 皮肤格式（关键产出）

### 4.1 Schema

```jsonc
{
  "id": "catppuccin-mocha",          // 必填，唯一，^[a-z0-9-]+$
  "name": "Catppuccin Mocha",        // 必填，面板显示名
  "mode": "dark",                    // 必填，dark | light（决定 EP 与 scrim 的明暗分支）
  "author": "…",                     // 可选
  "accent": "#89b4fa",               // 可选，皮肤自带主色（用户手动选色优先级更高）

  // —— 模式 A：精简调色板（推荐，~20 色，其余派生）——
  "palette": {
    "crust": "#11111b", "mantle": "#181825", "base": "#1e1e2e",
    "surface0": "#313244", "surface1": "#45475a", "surface2": "#585b70",
    "overlay0": "#6c7086", "overlay1": "#7f849c", "overlay2": "#9399b2",
    "text": "#cdd6f4", "subtext1": "#bac2de", "subtext0": "#a6adc8",
    "red": "#f38ba8", "green": "#a6e3a1", "yellow": "#f9e2af",
    "blue": "#89b4fa", "teal": "#94e2d5", "peach": "#fab387"
  },

  // —— 模式 B：完整 token 覆盖（用于零变化迁移与高定制）——
  "tokens": { "--app-bg": "#0b1120", "--app-border": "#1e2a3f" /* …*/ },

  // —— 效果默认值（可被用户滑块覆盖）——
  "glass": { "blur": 14, "saturate": 170, "borderAlpha": 0.14 },
  "scrim": { "dim": 26, "vignette": 55, "top": 12, "bottom": 22 }
}
```

**合成顺序（单一实现于 `skin.js`）**：
```
默认皮肤 → palette 派生 → tokens 覆盖 → glass/scrim 默认值
                                              ↓
                              { tokens, effects } → themeStore 写 :root
```

### 4.2 派生规则表（`palette` → `--app-*`）

派生统一走 `color.js` 的 HSL 工具（微调明度/饱和度后回转 hex/rgb），规则表：

| 目标 token | 派生自 |
|---|---|
| `--app-bg` | `crust` |
| `--app-dim-color` | `base`（转 `r, g, b`） |
| `--app-card-bg` | `base` |
| `--app-sidebar-solid` | `mantle` |
| `--app-sidebar-bg` | `mantle` → rgba 渐变（.86/.92） |
| `--app-border` | `surface0` |
| `--app-card-header-border` | `surface0` |
| `--app-card-glow` | 固定阴影公式，alpha 按 mode（dark .42 / light .08） |
| `--app-input-bg` | `surface0` |
| `--app-input-border` | `surface1` |
| `--app-hover-bg` | `surface0` +4% 明度 |
| `--app-table-header-bg` | `surface0` −2% 明度 |
| `--app-text` | `text` |
| `--app-text-heading` | `text` +5% 明度 |
| `--app-text-secondary` | `subtext0` |
| `--app-text-placeholder` | `overlay0` |
| `--app-scrollbar-track` | `mantle` |
| `--app-scrollbar-thumb` | `surface1` |
| `--app-scrollbar-thumb-hover` | `surface2` |
| `--app-chip-bg` | `surface0` |
| `--app-chip-border` | `surface1` |
| `--app-chip-text` | `text` |
| `--app-chip-hover-bg` | `surface1` |
| `--app-chip-active-bg` | `accent` |
| `--app-chip-active-soft` | `accent` @ α .16 |
| `--app-preview-bg` | `surface0` |
| `--app-preview-text` | `subtext1` |
| `--app-recipes-bg` | `mantle` |
| `--app-override-bar-bg` | `yellow` @ α .12 叠 `mantle` |
| `--app-override-bar-border` | `yellow` @ α .45 |
| `--app-accent-1` / `-2` | `accent` / accent 色相 +24°（复用现有 `deriveAccentPair`） |
| `--app-accent-*-rgb` | 上述转 RGB 三元组 |
| `--app-log-bg` | `crust` |
| `--app-log-text` | `subtext1` |
| `--app-log-time` | `accent` +12% 明度 |
| `--app-log-border` | `surface0` |
| `--app-log-line-border` | `surface0` −3% 明度 |
| `--app-code-bg` | `surface0` |
| `--app-code-text` | `accent` +10% 明度 |
| `--app-progress-card-bg-a` / `-b` | `mantle` / `base` |
| `--app-progress-meter-bg` | `accent` @ α .08 |
| `--app-progress-track` | `surface0` |
| `--app-search-hl-bg` / `-text` / `-active-*` | `yellow` 系四档 |
| `--app-thinking-bg` / `-text` | `accent` @ α .10 / `accent` +8% |
| `--app-engine-ok-*` | `green` 系三档（bg α .14 / text / border α .40） |
| `--app-engine-err-*` | `red` 系三档 |
| `--app-scrim-rgb` | mode=dark → `0,0,0`；light → `255,255,255` |

> 派生规则与默认值必须**逐条写成表驱动数据**（如 `DERIVE_RULES` 数组 + 变换函数名），不写 60 个手写分支，便于新增 token 与单测覆盖。

### 4.3 内置皮肤清单

| id | 名称 | mode | 来源 |
|---|---|---|---|
| `legacy-dark` | 午夜青蓝（深） | dark | **现状反填 → tokens 全量覆盖，零视觉变化** |
| `legacy-light` | 午夜青蓝（浅） | light | 同上 |
| `catppuccin-mocha` | Catppuccin Mocha | dark | 官方色值（palette 模式） |
| `catppuccin-macchiato` | Catppuccin Macchiato | dark | 官方色值 |
| `catppuccin-frappe` | Catppuccin Frappé | dark | 官方色值 |
| `catppuccin-latte` | Catppuccin Latte | light | 官方色值 |

> 取舍：不引 `@catppuccin/palette` npm 依赖（26×4 的静态数据内嵌成本更低，且项目已有"减依赖"取向）。

---

## 5. 效果层实现

### 5.1 多层 scrim（替代单色平涂）

```css
body::after {
  content: '';
  position: fixed; inset: 0; z-index: -1; pointer-events: none;
  background-image:
    /* 顶部渐晕 */ linear-gradient(to bottom,
        rgba(var(--app-scrim-rgb), var(--app-scrim-top-a)), transparent 40%),
    /* 底部渐晕 */ linear-gradient(to top,
        rgba(var(--app-scrim-rgb), var(--app-scrim-bottom-a)), transparent 55%),
    /* 径向暗角（焦点收拢） */ radial-gradient(ellipse at 50% 42%,
        transparent 38%, rgba(var(--app-scrim-rgb), var(--app-scrim-vignette-a)) 100%),
    /* 整体中性遮罩 */ linear-gradient(rgba(var(--app-scrim-rgb), var(--app-scrim-dim-a)),
        rgba(var(--app-scrim-rgb), var(--app-scrim-dim-a)));
}
```

要点：
- **用中性黑/白**（`--app-scrim-rgb`），不再用带蓝的 `--app-dim-color` —— 这是"图不发脏"的根因修复。
- 渐变端点用 `transparent` 会有黑边问题？→ 在 `rgba(0,0,0,0)` 语境下现代浏览器按预乘 alpha 插值，无色偏；若出现色晕则改用 `rgba(var(--app-scrim-rgb), 0)` 显式零 alpha（Phase 2 浏览器核对项）。
- 4 层合成控制在单伪元素内，GPU 成本低（无 blur）。

### 5.2 玻璃三件套 + surface 分档

```css
.app-card, .wb-card {
  background: rgba(var(--app-dim-color), var(--app-surface-1-opacity, 1));
  backdrop-filter: blur(var(--app-glass-blur, 14px)) saturate(var(--app-glass-saturate, 170%));
  -webkit-backdrop-filter: blur(var(--app-glass-blur, 14px)) saturate(var(--app-glass-saturate, 170%));
  border: 1px solid var(--app-glass-border);
  box-shadow: var(--app-card-glow);
}
@supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  .app-card, .wb-card { background: rgba(var(--app-dim-color), 0.96); }
}
@media (prefers-reduced-transparency: reduce) {
  .app-card, .wb-card { backdrop-filter: none; -webkit-backdrop-filter: none;
                        background: rgba(var(--app-dim-color), 1); }
}
```

分档落地：宿主卡片用 surface-1（现状 `.app-card`/`.wb-card`）；内嵌块（如高级选项分区）改 surface-2；chip/输入类读 surface-3。**表格保持实底不动**（数据密集区，业界共识）。

### 5.3 遮罩/玻璃参数与 UI 控件的映射

| 面板控件 | 值域 | 写入 |
|---|---|---|
| 整体遮罩浓度 | 0–70 | `--app-scrim-dim-a` = v/100 |
| 暗角强度 | 0–100 | `--app-scrim-vignette-a` = v/100 × 0.65 |
| 顶部渐晕 | 0–100 | `--app-scrim-top-a` = v/100 × 0.45 |
| 底部渐晕 | 0–100 | `--app-scrim-bottom-a` = v/100 × 0.60 |
| 卡片透明度 | 20–100 | `--app-surface-1..3-opacity`（三档） |
| 卡片模糊 | 0–24 | `--app-glass-blur` |
| 背景模糊 | 0–20 | `--app-bg-blur`（保留） |

---

## 6. 背景层与数据模型

### 6.1 背景类型枚举

```js
{ type: 'none' }
{ type: 'builtin',  id: 'abyss' }                    // 程序化 SVG（现有 5 套 + 新增）
{ type: 'image',    assetId: 'bg-1690000000' }       // IndexedDB 图库
{ type: 'animated', preset: 'aurora-flow', fps?: 30 } // Canvas/CSS，失焦暂停
```

兼容：旧 `type: 'custom'` 读作 `image`；旧字段 `url`（dataURL）走迁移链。

### 6.2 存储

**localStorage（保留 + 新增）**

| key | 内容 | 变化 |
|---|---|---|
| `mediasense_theme` | `'dark' \| 'light'` | 不变 |
| `mediasense_accent` | hex | 不变（语义改为「覆盖皮肤自带主色」） |
| `mediasense_skin` | skinId | **新增** |
| `mediasense_effects` | `{ dim, vignette, top, bottom, cardOpacity, glassBlur, bgBlur }` | **新增**（从 background 拆出） |
| `mediasense_background` | `{ type, id?, assetId?, url?(legacy), dim?, blur?, cardOpacity? }` | 兼容读取；写入时只留新字段 |

**IndexedDB（新增）**

```
db:  mediasense-media  (version 1)
├─ store: backgrounds  (keyPath: 'id')
│    { id, name, blob, width, height, bytes, addedAt, avgLuma }
└─ store: skins        (keyPath: 'id')   // 用户导入的皮肤
```

存 **Blob**（非 dataURL）：无 base64 的 +33% 膨胀，配额从 ~5MB 提升到磁盘可用级。

### 6.3 迁移方案（不可逆操作，最高风险项）

```
启动时 migrateBackground():
  1. 读 mediasense_background
  2. IF type==='custom' AND url 存在:
       a. fetch(url) → blob                    // dataURL 可直接 fetch
       b. bgStore.put({ id: 'bg-migrated-'+ts, blob, name:'迁移背景', ... })
       c. 成功 → 写回 { type:'image', assetId: id }（不含 url）
       d. 失败 → 保留 url 原字段，继续用 dataURL 渲染，
                  UI 提示「背景仍可用，但建议重新上传以获得图库能力」
  3. IF IndexedDB 整体不可用（隐私模式等）:
       全程退回 localStorage 单图路径（保留该分支代码），提示一次
```

**幂等性**：迁移按 `assetId` 是否已存在判重；重复启动不产生副本。
**前置校验**：迁移前不可删除旧字段；先写成功再清理（`write-then-clean`，禁止先删后写）。

### 6.4 自动适配（A7）

`utils/imageFit.js`：
```
analyzeImage(blob) → { avgLuma (0-1), contrast, palette[] }
recommendScrim(avgLuma, contrast) → { dim, vignette, top, bottom }
recommendCardOpacity(avgLuma) → 20-100
```
规则（可单测的纯函数）：
- `avgLuma` 高（亮图）→ 提高 `dim`（保证文字对比度）；低（暗图）→ 降低 `dim`、提高 `vignette`。
- `contrast` 高（杂乱图）→ 提高 `dim` 与 `vignette`。
- 与现有 `extractPalette`（`color.js`）复用同一采样实现，避免两份中位切分。

---

## 7. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| token 变量名 | **冻结不变** | 21 个文件已消费；改名 = 全站回归风险。新增只增不改 |
| 皮肤数据形态 | JS 模块（内置）+ JSON（导入导出） | 内置可 tree-shake、无网络依赖；导出满足分享 |
| 皮肤分层 | palette 派生 + tokens 覆盖双模式 | 既支持"20 色低成本做皮肤"，又支持"legacy 全量零变化迁移" |
| 背景图存储 | IndexedDB 存 Blob | localStorage ~5MB 已被实测踩坑（4MB 图必 `QuotaExceededError`） |
| 遮罩色 | 中性黑/白（按 mode） | 带色相深色会把所有图染脏（当前丑的直接原因） |
| 玻璃参数 | dark/light 两套独立 | 业界共识：暗底上低 alpha 玻璃不可辨 |
| EP 覆盖策略 | 保留现有覆盖结构，仅把硬编码色值换 `var(--app-*)` | 全量改 EP 变量体系会引入大面积视觉回归，收益不成比例 |
| 动态背景 | CSS 动画优先，Canvas 兜底；失焦暂停 | 性能可控、可 `prefers-reduced-motion` 降级 |

---

## 8. 风险与回滚形态

| 风险 | 缓解 | 回滚 |
|---|---|---|
| Phase 1 等价重构引入视觉漂移 | 内置 `legacy-*` 皮肤以 tokens 全量反填现状；Phase 1 完成后逐页截图与原版 diff | `git revert` 该阶段 commit |
| CSS 变量派生规则错误导致对比度崩坏 | 派生规则表驱动 + 单测；6 套皮肤 × 明暗逐页核对 | 切回 `legacy-*` 皮肤 |
| IndexedDB 迁移丢图 | write-then-clean + 失败保留 `url` + 不删旧字段直到成功 | 退回 localStorage 路径（代码保留） |
| 面板被旧 localStorage 数据击穿 | `loadJson` 已有 try/catch；新增字段一律给默认值 | — |
| 动态背景吃 CPU | 失焦/隐藏暂停 + `prefers-reduced-motion` 回退静态帧 | 切静态背景 |

**阶段隔离**：每阶段独立 commit，任一阶段失败可单独 revert 且不阻断前一阶段成果。

---

## 9. 不适用项声明

- 契约（OpenAPI/JSON Schema）：**不适用**（无 API 变更）。
- 数据库迁移：**不适用**（后端 SQLite 不受影响）；前端 IndexedDB 首次建库属新功能而非迁移。
- 后端四态覆盖：**不适用**（纯前端）。
