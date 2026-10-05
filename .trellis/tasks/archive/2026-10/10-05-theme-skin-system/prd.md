# 背景与皮肤系统重构 - PRD

> **范围收缩（2026-10-05，用户确认）**：IndexedDB 背景图库（含数据迁移）拆分到独立子任务，本任务等级由 **L → M1**。
> 本任务只做「皮肤 token 层 + 效果层 + 内置皮肤包 + 背景类型扩展」，**不涉及任何数据迁移**，回滚退化为单纯 revert。

## 需求

我要解决什么问题：现有"背景系统"实际只是「换主色 + 传一张图 + 拉两个滑块」，背景图被单色遮罩平涂成灰蒙一片、卡片玻璃感缺失、整体只有一套配色，导致自定义背景观感差（用户原话"好丑"）。需要把它升级为三层解耦的**皮肤 / 效果 / 背景**系统。

## 具体例子

- 给当前汽车背景图 + 深色主题，期望：图片作为氛围层（边缘压暗、中心保留），卡片成为视觉主角、有清晰玻璃边界与层次，任何一张图都不出现"灰蒙一片"。
- 给「Catppuccin Mocha」皮肤 + 无背景，期望：整套界面（含侧栏/卡片/表格/输入框/弹窗）换为 Mocha 配色，无需改任何组件代码。
- 给一张亮色照片，期望：系统按图片亮度自动给出合理遮罩强度，文字仍可读（对比度达标），不需要用户手动试滑块。

## 验收标准

- [ ] A1 所有既有页面（工作台/历史/模型/模板/存储/结果/追问）在「6 套内置皮肤 × dark/light」下无不可读文字、无错位、无颜色残留（逐套截图核对）。
- [ ] A2 背景遮罩由「单色平涂」升级为「渐变 scrim + 径向暗角」多层合成，遮罩浓度为 0 时完全不压暗图片，为最大时内容区文字对比度 ≥ 4.5:1。
- [ ] A3 卡片玻璃为三件套（blur + saturate + 高光描边）且分 3 档 surface，视觉层级可辨（宿主卡片 > 内嵌块 > chip）。
- [ ] A4 皮肤为纯数据（JSON），新增一套皮肤不改任何组件代码；面板可见并可切换；皮肤可导出为 `.json` 文件、可再导入生效。
- [ ] ~~A5 背景图可保存多张并切换~~ → **移交子任务 `背景图库（IndexedDB）`**
- [ ] ~~A6 背景支持「程序化动态」与「静态图」两类，动态背景失焦暂停~~ → **移交子任务 `背景图库（IndexedDB）`**（本任务仅保留程序化静态预设）
- [ ] ~~A7 上传背景图后自动推荐遮罩强度与卡片透明度~~ → **移交子任务 `背景图库（IndexedDB）`**
- [ ] A8 `npm run lint` / `npm run test` / `npm run build` 全绿；后端 pytest 不受影响（本任务不应触碰后端）。
- [ ] A9 无静默失败：图片解码失败、皮肤 JSON 非法等均给出用户可见提示；localStorage 配额溢出沿用现有可见提示。

## 不做项

- **不做 IndexedDB 背景图库与数据迁移**（移交子任务）。
- 不做 Windows 桌面层动态壁纸（Lively/Wallpaper Engine 式注入 WorkerW），背景只在 WebView 内渲染。
- 不做 GLSL/WebGL shader 背景（至少本版不做），动态背景限 Canvas 2D / CSS 动画。
- 不做皮肤市场 / 在线下载 / 社区分享。
- 不改后端任何代码、不改任何 API 契约、不动 Element Plus 版本。
- 不做 Element Plus 全量 CSS 变量体系替换（保留现有覆盖策略，只把硬编码色值改为 token 引用），避免大面积视觉回归。
- 不做「按图片内容语义自动生成整皮肤」（只做亮度 → 遮罩推荐的轻量自动适配）。

## 影响范围

- 直接改动：`frontend/src/stores/themeStore.js`、`frontend/src/utils/backgrounds.js`、`frontend/src/utils/color.js`、`frontend/src/components/BackgroundPanel.vue`、`frontend/src/App.vue`（主题变量块 + body 背景层 + 卡片类）。
- 新增：`frontend/src/utils/skin.js`（派生器）、`frontend/src/utils/skins/`（皮肤数据）、`frontend/src/utils/skinSchema.js`（校验）、`frontend/src/utils/effects.js`、`frontend/src/utils/imageFit.js` 与配套测试。
- 间接受影响：**21 个消费 `var(--app-*)` 的组件**——本设计以「变量名与语义不变」为前提，组件预期零改动；Phase 2 等价重构必须证明这一点。
- 构建产物：`backend/static/` 需重新 build（同源 SPA，用户实际看到的是构建产物）。

## 风险与回滚

- 风险等级：**M1**（原 L 级，2026-10-05 用户确认降级——拆分 IndexedDB 后本任务不含数据迁移与不可逆操作）。
- 不可逆操作：**无**。全程只改前端源码与 CSS 变量，无数据迁移、无 schema 变更、无后端改动。
- 回滚方式：
  - 每阶段独立 commit；任一阶段出问题 → `git revert` 该 commit 即恢复。
  - 皮肤层设计为"可控降级"：`skinId` 缺失/非法 → 回退内置「午夜青蓝」默认皮肤，渲染路径与旧版逐值等价。
  - 整体：本任务在 `feat/theme-skin-system` worktree 分支进行，`master` 保持可发布；不合并不影响线上。
