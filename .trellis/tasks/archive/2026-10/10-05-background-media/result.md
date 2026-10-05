# 背景媒体层 - 验收结果

> Parent: `10-05-theme-skin-system`｜设计来源：父任务 `design.md §6`

## 全量门禁

| 项 | 结果 |
|---|---|
| 后端 pytest | **837 passed**（源码零改动） |
| 前端 lint | **PASS** |
| 前端 vitest | **22 文件 / 259 用例全绿**（新增 bgStore 22 + imageFit 16） |
| 前端 build | **PASS**（`✓ built in 1.77s`，产物已刷新） |

## 验收标准逐条核对（B1–B8）

| # | 标准 | 判定 | 实测证据 |
|---|---|---|---|
| B1 | 图存 IndexedDB（Blob）、可多张切换/删除、localStorage 不再有图片 base64 | **达成** | 上传后 `mediasense_background = {type:'image', assetId:'bg-…', thumbUrl: <5.5KB 缩略图>}`，**localStorage 仅 5504 字符**；图库 `1/24`；原图经 `blob:` objectURL 渲染（`--app-bg-image = url("blob:http://localhost:517…")`）；删除后 `libCount 2→1` 且活跃项回退 `{type:'none'}` |
| B2 | 旧 `{type:'custom', url:dataURL}` 自动迁移；**write-then-clean**；幂等 | **达成** | 构造旧数据（4548 字符 dataURL）→ 启动后 `background = {type:'image', assetId:'bg-migrated-…'}`，**`url` 字段已不存在**，`localStorage` 降到 **54 字符**；图库 1→2 张；提示「已把原有背景图归档到本地图库」；**再 reload 仍为 2 张**（幂等，不产生副本） |
| B3 | IndexedDB 不可用时不白屏、退回旧路径、只提示一次 | **达成** | 注入 `window.indexedDB = undefined` 后 reload：提示「当前环境无法使用本地图库（可能是隐私模式），背景图会按旧方式保存」；图库 UI 不渲染；上传仍成功（`{type:'custom', url:'data:image/jpeg;base64…'}`） |
| B4 | ≥1 种程序化动态背景（CSS 动画非 JS 循环）、失焦暂停、reduced-motion 关闭 | **达成** | 点「极光流动」→ `{type:'animated', preset:'aurora-flow'}`、`html[data-bg-anim="aurora-flow"]`、计算样式 `animationName=app-bg-drift / backgroundSize=170% 170% / animationPlayState=running`；派发 `visibilitychange` 且 `hidden=true` → `data-bg-paused="1"` 且 **`animation-play-state: paused`**，恢复后回到 `running`；`@media (prefers-reduced-motion: reduce)` 下 `animation: none` |
| B5 | 上传后给推荐并可一键采用；推荐函数纯函数可单测 | **达成** | 上传后出现推荐「遮罩 57% · 暗角 67% · 卡片 87%」（该图为亮天空 + 暗地面 + 彩色块）；`recommendEffects` 16 个单测覆盖纯白/纯黑/高对比/缺失/越界/单调性/比例关系 |
| B6 | 图库容量上限 + 超限可行动提示 | **达成** | `LIBRARY_LIMITS = {maxCount:24, maxBytes:120MB}`；`checkCapacity` 7 个边界单测；超限返回「图库最多存 24 张背景，请先删除不用的」且**不落库**（有单测断言 `getBackground` 仍为 null） |
| B7 | `python verify.py` 全绿，既有 6 套皮肤 × 明暗不受影响 | **达成** | §门禁；皮肤/效果相关 259 用例全部仍绿（含 6 套皮肤对比度门禁） |
| B8 | objectURL 正确 revoke、无泄漏 | **达成** | 全项目 `createObjectURL` 只在 `themeStore` 出现一次，`revokeCurrentUrl()` 是唯一回收点，在「换图 / 删除活跃项 / 读取新图前」三处调用；图库缩略图**故意用 dataURL** 而非 objectURL（避免每张图一个生命周期） |

## 交付物

| 文件 | 类型 |
|---|---|
| `frontend/src/utils/bgStore.js` | 新增（IndexedDB 图库：CRUD + 用量 + 容量策略） |
| `frontend/src/utils/imageFit.js` | 新增（亮度分析 + 参数推荐，纯函数） |
| `frontend/src/utils/color.js` | 新增 `sampleImagePixels` / `analyzePixels`，`paletteFromPixels` 导出（供 imageFit 复用，**不写第二份采样**） |
| `frontend/src/utils/image.js` | 重构：产出 **Blob + 缩略图**（不再产生大 dataURL）；新增 `blobToDataUrl` 供降级路径 |
| `frontend/src/utils/backgrounds.js` | 新增 `ANIMATED_BACKGROUNDS`（2 个动态预设，复用程序化 SVG 作底图） |
| `frontend/src/stores/themeStore.js` | 背景升级为图库 + 迁移 + 降级 + 动态背景 + 失焦暂停 |
| `frontend/src/components/BackgroundPanel.vue` | 图库网格（切换/删除/用量）+ 动态背景卡片 + 亮度推荐块 + 背景提示 |
| `frontend/src/App.vue` | 动态背景 CSS（`background-position` 漂移）+ 暂停属性 + reduced-motion |
| `package.json` | 新增 devDependency `fake-indexeddb` |

## 偏差与坑点

1. **jsdom 的 Blob 与 `structuredClone` 不兼容**：`fake-indexeddb` 在 jsdom 环境下克隆 Blob 后不再是 `Blob` 实例（断言失败）。测试文件加 `// @vitest-environment node` 切到 Node 原生 Blob 才通过 —— **这是测试环境限制，不是生产代码问题**（真实浏览器 IndexedDB 原生支持 Blob）。
2. **事务提交语义**（写代码时自查发现）：第一版在所有操作里都用「request 成功即 `db.close()`」，对写操作可能在事务 commit 前就关闭连接。已改为**写等 `transaction.oncomplete`、读在 request 成功时 resolve**。
3. **`setBackground` 从「字段合并」改为「整体替换」**：合并会让上一类背景的字段残留（如切到 animated 后仍带着 builtin 的 `id`），虽然 `applyBackground` 按 type 分支不会出错，但存储会越来越脏。
4. **迁移不回写大 dataURL**：迁移后的 `{type:'image', assetId}` 故意不带 `thumbUrl`（首屏会闪一下空白，随后被原图替换），以避免把几 MB 字符串再塞回 localStorage —— 那正是本次要摆脱的东西。
5. **`analyzeImage` 复用缩略图而非原图**：分析用 `thumbUrl`（几十 KB，320px），一次采样同时产出提色板与推荐参数，避免为分析再解码大图。
6. **动态背景只动 `background-position`**：不跑 `requestAnimationFrame`、不动 `transform`（`transform` 已被 `body::before` 的 `scale(1.04)` 用于遮 blur 边），因此动画开在 CSS 里、暂停只需切一个属性。
