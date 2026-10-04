# 背景与皮肤系统重构 - ExecFlow（L 级）

> 恢复点用法：新会话读 `design.md` + 本文件 + 上一阶段 `phase-<N>-result.md`。
> 每阶段独立 commit；阶段失败可单独 revert，不阻断已完成阶段。

---

## Phase 1：基线固化（不改代码）

- **输入**：当前 `main` 工作树
- **动作**：启动服务（`cd backend && python main.py`），用 DevTools 对 **7 个页面 × dark/light 两主题 × 1440/1024 两宽度** 截图存档到 `research/baseline/`；记录当前 `--app-*` 计算值快照（DevTools 执行 `getComputedStyle` 导出 JSON）。
- **输出**：`research/baseline/*.png` + `research/baseline/tokens-dark.json` / `tokens-light.json`
- **验证**：截图齐全可读；token 快照含 3.1 节全部 60 个变量
- **完成定义**：后续 Phase 2 "零视觉变化"比对有客观依据
- **依赖**：无

## Phase 2：皮肤 token 层等价重构（**零视觉变化**）

- **输入**：Phase 1 的 token 快照
- **动作**：新增 `utils/skin.js`（合成 + 表驱动派生）+ `utils/skins/`（先只放 `legacy-dark`/`legacy-light`，以 tokens 全量反填现状）；`themeStore` 改为「读皮肤 → 合成 tokens → 写 `:root`」；`App.vue` 的 `:root[data-theme]` 两套块**保留为兜底**但被 JS 覆盖。
- **输出**：`frontend/src/utils/skin.js`、`frontend/src/utils/skins/index.js`、改造后的 `themeStore.js`
- **验证**：
  - `cd frontend && npm run test`（新增 `skin.spec.js`：派生规则、非法皮肤回退、token 数量对齐）
  - DevTools 对 7 页 × 2 主题重截图 → 与 Phase 1 基线**逐像素或逐项 diff**，差异仅允许抗锯齿级噪声
- **完成定义**：组件零改动 + 截图 diff 通过 + **证明"变量名不变 ⇒ 组件零侵入"**（写入 `phase-2-result.md`）
- **依赖**：Phase 1
- **本阶段是全局风险闸门**：不通过则后续不必开始

## Phase 3：效果层（scrim 多层 + 玻璃三件套 + surface 分档）

- **输入**：Phase 2 的皮肤层
- **动作**：新增 `--app-scrim-*` / `--app-glass-*` / `--app-surface-*` 变量与派生；`body::after` 改 4 层 scrim；`.app-card`/`.wb-card` 改玻璃三件套 + `@supports` / `prefers-reduced-transparency` 降级；`BackgroundPanel` 滑块按 5.3 节重排。
- **输出**：`App.vue`（背景层 + 卡片类）、`BackgroundPanel.vue`、`utils/effects.js`
- **验证**：
  - DevTools 在**最坏背景位置**测文字对比度（≥4.5:1）；暗角滑到 0 时图片完全不被压暗（视觉核对）
  - `npm run test`（新增 `effects.spec.js`：参数 → alpha 换算边界：0 / 上限 / 越界钳制）
- **完成定义**：A2 / A3 满足；7 页在 6 种 scrim 强度下无不可读文字
- **依赖**：Phase 2

## Phase 4：皮肤包（内置 6 套 + 导入导出 + 面板）

- **输入**：Phase 3 的效果层
- **动作**：填充 4 套 Catppuccin（palette 模式）；面板新增皮肤选择器、导出按钮、导入 `.json`（含校验 + 失败提示）；`stores/themeStore` 加 `skinId` 与导入导出动作。
- **输出**：`utils/skins/`（4 套）、`BackgroundPanel.vue`、`themeStore.js`、`utils/skinSchema.js`（校验器）
- **验证**：
  - `npm run test`（`skinSchema.spec.js`：合法/缺字段/类型错/未知 mode 全部覆盖；`skin-import.spec.js`：导入非法文件必须报错不静默）
  - 6 套皮肤 × 明暗 × 7 页截图核对（A1）
- **完成定义**：A4 满足；新增皮肤确实零组件改动（用第 6 套验证）
- **依赖**：Phase 3

## Phase 5：背景层（IndexedDB 图库 + 动态背景 + 自动适配）

- **输入**：Phase 4
- **动作**：新增 `utils/bgStore.js`（IndexedDB CRUD + 迁移）、`utils/imageFit.js`（亮度分析 + 推荐）；背景类型扩到 4 类；`backgrounds.js` 加 ≥1 个动态预设（失焦暂停）；面板加图库网格 + 「一键采用推荐参数」。
- **输出**：`bgStore.js`、`imageFit.js`、`backgrounds.js`、`BackgroundPanel.vue`、`themeStore.js`
- **验证**：
  - `npm run test`（`bgStore.spec.js` 用 `fake-indexeddb` 或注入 stub 覆盖 CRUD + 迁移幂等 + 失败路径；`imageFit.spec.js` 覆盖推荐规则边界）
  - 手工：上传 5 张图 → 刷新 → 图库仍在 → 切换正常；模拟 IndexedDB 不可用 → 退回单图路径且有提示
  - 迁移演练：手工写入旧格式 `{type:'custom', url:dataURL}` → 启动 → 验证已归档且渲染正常
- **完成定义**：A5 / A6 / A7 满足
- **依赖**：Phase 4

## Phase 6：验收与知识回写

- **输入**：全部阶段
- **动作**：`python backend/verify.py` 全量（后端 pytest + 前端 vitest + lint + build）；重建 `backend/static/`；逐条核对 A1–A9；回滚演练（revert 任一阶段 commit 并确认可恢复）；回写 `CONTEXT.md` + `.trellis/spec/frontend/`（新增主题/皮肤规范）+ 修正 `project-foundation.json` 的 `tokenSource`。
- **验证**：`python backend/verify.py` 退出码 0；A1–A9 逐条标注证据（截图路径/测试名/命令）
- **完成定义**：DONE（§1.3）= 验收全过 + 门禁绿 + 真实路径亲测 + 已 commit
- **依赖**：Phase 5

---

## 阶段顺序铁律

```
Phase 1 → 2 → 3 → 4 → 5 → 6   顺序执行，不跳
Phase 2 是风险闸门：不通过不得进入 Phase 3
```
