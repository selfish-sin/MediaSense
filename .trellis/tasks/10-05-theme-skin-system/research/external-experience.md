# 外部可迁移经验（Exa 调研，2026-10-05）

> 用途：为皮肤/效果/背景三层架构选型提供依据。结论均标注「可直接迁移 / 仅可借鉴 / 不适用」。

## 1. 主题 token 体系：Catppuccin（**可直接迁移**）

来源：`github.com/catppuccin/catppuccin`（style-guide.md）、`github.com/catppuccin/palette`（css.md / sass.md）。

- 4 个 flavor（Latte / Frappé / Macchiato / Mocha）× 26 色，**语义化固定命名**：
  - 背景层级：`crust < mantle < base`
  - 表面：`surface0 / surface1 / surface2`
  - 描边/弱化：`overlay0 / overlay1 / overlay2`
  - 文字：`text` / `subtext1` / `subtext0`
  - 强调：`rosewater flamingo pink mauve red maroon peach yellow green teal sky sapphire blue lavender`（14 个）
- 官方 npm 包 `@catppuccin/palette` 直接导出 CSS 变量，含 `--ctp-<flavor>-<name>` 及 `-rgb` / `-hsl` 变体。
- style-guide.md 有**「语义 → 具体色」映射表**（General Usage），可直接照抄成我们的语义槽映射：
  - 链接 = Blue，Subtle/次要 = Overlay1，On Accent = Base/Crust，surface = Base/Surface0，background = Crust/Mantle，error = Red
- 社区 CSS port 额外提供**意图命名变量**（`--bg / --bg-secondary / --surface-* / --text / --label / --link / --accent`），正是本项目缺的中间层。
- **迁移结论**：色值直接内嵌为静态 JSON（26×4 行规模，不引 npm 依赖）；映射思路照抄其「原始色 → 语义槽」分层。

## 2. 玻璃拟态参数（**可直接迁移**）

来源：clay.global/blog/glassmorphism-ui、orizon.co（2026 指南）、pixcode.io、socialanimal.dev、toolboxhubs（2026 指南）。

业界共识参数区间：

| 属性 | 建议值 | 说明 |
|---|---|---|
| `backdrop-filter` | `blur(8–16px) saturate(160–180%)` | <6px 看不出磨砂；>20px 像磨砂屏且 GPU 昂贵。`saturate` 是"高级感"关键：纯 blur 会把背后颜色均值化推向灰 |
| 半透明底 | dark `rgba(255,255,255,.08–.15)`；light `rgba(255,255,255,.5+)` | alpha >0.25 玻璃效果即死；dark 模式必须**提高不透明度**（0.2–0.3）才有可辨层级 |
| 描边 | `1px solid rgba(255,255,255,.15–.3)` | 无描边时玻璃边界不可辨（**本项目当前用不透明主题边框，正是"糊成一团"的原因**） |
| 阴影 | `0 8px 32px rgba(0,0,0,.12)` 柔和扩散 | 太重与玻璃语义冲突 |

其他硬约束：
- **surface 分档**（glass-sm chip / glass-md card / glass-lg modal），每类固定厚度，全站一致。
- **禁止玻璃叠玻璃**（>2 层模糊 = 视觉噪音 + GPU 掉帧）；数据密集区（表格/长文/表单）用实底——本项目 `.el-table` 已是实底，方向正确。
- **`@supports (backdrop-filter: blur(1px))` 渐进增强**：基础样式给近实底，支持时才开玻璃。
- **`@media (prefers-reduced-transparency: reduce)`** 降级为实底；`prefers-contrast: more` 提高不透明度 + 加粗描边。
- dark 与 light 是**两套独立材质参数**，不能简单反转。

## 3. 背景图可读性 scrim（**可直接迁移**）

- **2 个 stop 的渐变会有 Mach band（视觉硬边）**；用 4+ 个 stop 缓和过渡更自然（Google Photos / 编辑类卡片普遍做法）：
  `linear-gradient(to top, α .80 @0%, .50 @30%, .10 @55%, 0 @70%)`
- 用 **`oklch(0 0 0 / α)` 中性黑**，不带色偏；用带色相的深色（本项目当前 `--app-dim-color: 11,17,32` 带蓝）会把所有图染成脏蓝灰。
- 补充手段：`text-shadow: 0 1px 3px rgba(0,0,0,.5)` 作对比度兜底（视觉几乎不可见，但提升可读性）。
- **必须在最坏背景位置测对比度**（图片最亮 / 最暗 / 最饱和处各测一次），WCAG AA 正文 4.5:1。
- 结论：把「单层纯色 opacity」换成「多层渐变 + 径向暗角」，并让遮罩用中性黑/白。

## 4. 壁纸引擎：Lively Wallpaper（**仅可借鉴结构**）

来源：`github.com/rocksdanister/lively`（GPL-3.0）、livelywallpaper.app。

- 支持类型：视频 MP4/WebM/AVI/MOV/MKV、GIF、Web/HTML、GLSL shader（含 Shadertoy 导入）、URL、Unity/Godot 应用、静态图。
- **产品结构可借鉴**：①壁纸类型枚举；②每张壁纸用 `LivelyProperties.json` 声明自定义属性（滑块/色板/下拉），UI 由 schema 生成；③右键「导出 ZIP」= 可移植包，双击导入；④**Smart Pause**：全屏/游戏/电池/远程桌面时暂停渲染（性能与省电）。
- 引擎本体是 Windows 桌面层注入（`WorkerW` 窗口），**对本项目不适用**（背景只能在 WebView 内）。
- 对本项目的映射：类型限「程序化动画 + 静态图」；属性 schema 思路用于皮肤参数面板；「导入导出」用于皮肤 `.json`；Smart Pause 用于动态背景（失焦/降低动效偏好时暂停）。

## 5. 不适用项

- **Wallpaper Engine**：付费闭源，桌面层方案，不适用。
- **Mica / Acrylic（Windows 11 材质）**：需 Electron/Tauri 原生窗口能力，本项目是浏览器内 SPA，不适用。（若未来打包为桌面壳可再评估。）
