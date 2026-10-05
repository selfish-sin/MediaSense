# 背景媒体层 - PRD

> Parent: `10-05-theme-skin-system`｜技术方案直接复用父任务 `design.md §6`（存储结构 / write-then-clean 迁移链 / `imageFit` 规则已定义）

## 需求

把背景从"单张 dataURL 塞 localStorage"升级为**媒体层**：多张图库（不受 localStorage ~5MB 配额限制）、至少一种程序化动态背景（失焦暂停）、以及按图片亮度/对比自动推荐遮罩与卡片参数。

对应父任务 PRD 的 A5 / A6 / A7（当时按用户决定移出，现要求补做）。

## 具体例子

- 给同一张照片连续上传 6 次并收藏，期望：面板出现 6 张缩略图可切换、刷新后仍在、localStorage 里不再有 base64 大字符串。
- 给「极光流动」动态背景并切到别的窗口，期望：动画暂停（CPU 不再消耗），切回来继续。
- 给一张高亮照片，期望：提示「建议遮罩 42% / 卡片 88%」，点一下即采用，文字清晰。

## 验收标准

- [ ] B1 背景图存 **IndexedDB（Blob）**，可保存 ≥5 张并在面板切换/删除；localStorage 中不再出现图片 base64（仅存元数据与当前 assetId）。
- [ ] B2 旧版本已存的 `{type:'custom', url:dataURL}` 背景**自动迁移**进图库：迁移成功才清理旧字段；失败则保留旧路径继续渲染并给出可见提示；重复启动不产生副本（幂等）。
- [ ] B3 IndexedDB 整体不可用（隐私模式等）时不白屏：退回单图旧路径渲染 + 只提示一次。
- [ ] B4 至少 1 种程序化动态背景（CSS 动画，非 JS 循环），窗口失焦/隐藏时暂停，`prefers-reduced-motion: reduce` 下不播放。
- [ ] B5 上传背景图后给出推荐（遮罩浓度 / 暗角 / 卡片透明度）并可一键采用；推荐函数为纯函数、边界可单测。
- [ ] B6 图库容量有上限（张数或总字节）并在超限时给可行动提示（禁止静默失败）。
- [ ] B7 `python verify.py` 全绿（后端 pytest + 前端 lint/vitest/build），且既有 6 套皮肤 × 明暗不受影响。
- [ ] B8 对象 URL（objectURL）在背景切换/组件卸载时正确 revoke，无泄漏（DevTools 可核对）。

## 不做项

- 不做视频背景（只做 CSS 动画）。
- 不做图库的分类/标签/搜索/排序 UI（只做网格 + 切换 + 删除）。
- 不做 AI 内容识别式的配色推荐（只做亮度/对比的数值推荐）。
- 不改任何后端代码。

## 影响范围

- 新增：`frontend/src/utils/bgStore.js`、`frontend/src/utils/imageFit.js`、2-3 个测试文件。
- 改动：`utils/backgrounds.js`（动态预设）、`utils/color.js`（暴露可复用的像素分析）、`stores/themeStore.js`（异步背景 + 迁移 + 暂停状态）、`components/BackgroundPanel.vue`（图库网格 + 推荐 + 动态选择）、`App.vue`（动态背景 CSS + 暂停属性）。
- 不触碰：21 个消费 `var(--app-*)` 的组件、后端、`backend/static/`（构建产物统一在收尾刷新）。

## 风险与回滚

- 风险等级：**M1**（跨 5 个前端模块，方案已在 design.md §6 定死；无后端/schema 变化）。
- 不可逆操作：旧 `mediasense_background.url` 迁移后清理 —— 严格遵守 **write-then-clean**（先写 IndexedDB 成功、再清理旧字段），失败一律保留旧数据。
- 回滚方式：每切片独立 commit，`git revert` 即可；`applyBackground` 保留 localStorage 旧分支（不删代码），IndexedDB 不可用时自动走旧路径。
