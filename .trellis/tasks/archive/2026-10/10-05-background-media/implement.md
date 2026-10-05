# 背景媒体层 - Implementation Plan

## 执行清单

1. [ ] **装测试依赖** `fake-indexeddb`（devDependency，IndexedDB 单测必需）→ 验证：`npm test` 仍绿
2. [ ] **`utils/bgStore.js`**：IndexedDB 封装（open/put/list/get/delete/usage）+ 上限策略 → 验证：`bgStore.test.js`
3. [ ] **`utils/imageFit.js`**：`analyzeImage()` 亮度/对比 + `recommendEffects()` 纯函数 → 验证：`imageFit.test.js` 边界
4. [ ] **`utils/color.js`** 抽出可复用的像素统计（`analyzePixels`），供 `extractPalette` 与 `imageFit` 共用（不写第二份采样）
5. [ ] **`themeStore` 接入图库**：背景类型加 `image` + 异步解析 assetId → objectURL；objectURL 生命周期管理 → 验证：切换/删除不泄漏
6. [ ] **迁移链（write-then-clean）**：启动时迁移旧 `{type:'custom',url}`；失败保留旧路径并提示；幂等 → 验证：手工构造旧数据 → 启动 → 图库有图 + 旧字段清理
7. [ ] **IndexedDB 不可用降级**：退回 localStorage 旧路径 + 只提示一次 → 验证：注入抛错的 indexedDB stub
8. [ ] **动态背景预设**（`backgrounds.js`）+ `App.vue` CSS 动画（非 JS 循环）→ 验证：`data-bg-anim` 属性生效
9. [ ] **失焦暂停** + `prefers-reduced-motion` → 验证：切窗口后 `animation-play-state: paused`
10. [ ] **面板 UI**：图库网格（切换/删除/用量）+ 动态背景选择 + 推荐一键采用 → 验证：浏览器实测
11. [ ] **全量门禁** → `python verify.py` 退出码 0；重建 `backend/static/`
12. [ ] **浏览器验收 B1–B8** → 逐条取证（截图/计算值/测试名）
13. [ ] **回写 CONTEXT.md + spec/frontend**（图库/动态/推荐的约定与坑点）

## 切片清单

| 切片 | 名称 | 关键产出 | 依赖 |
|---|---|---|---|
| S1 | 图库存储 | `utils/bgStore.js` + 测试 | 无 |
| S2 | 存储接入与迁移 | `themeStore` 异步背景 + 迁移链 | S1 |
| S3 | 亮度分析与推荐 | `utils/imageFit.js` + `color.js` 复用 | 无 |
| S4 | 动态背景 | `backgrounds.js` 预设 + `App.vue` 动画/暂停 | 无 |
| S5 | 面板 UI | `BackgroundPanel.vue`（图库/动态/推荐） | S2+S3+S4 |
| S6 | 验收与回写 | 门禁绿 + 证据 + 文档 | S5 |

## 验证命令

```powershell
cd d:\projects\youtube_tool_v2-theme\frontend; npm test; npm run lint
cd d:\projects\youtube_tool_v2-theme; python verify.py
```

## 关键约定（防跑偏）

- **objectURL 只在一处创建、一处 revoke**（放 `themeStore`），组件不自己 `createObjectURL`。
- **不删旧分支**：`applyBackground` 的 localStorage 路径保留为降级通道。
- **不静默失败**：配额/上限/迁移/IDB 不可用全部要有用户可见提示。
- **不写第二份采样算法**：`extractPalette` 与 `analyzeImage` 共用 `color.js` 的像素统计。
