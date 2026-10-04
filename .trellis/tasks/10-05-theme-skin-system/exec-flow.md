# 背景与皮肤系统重构 - 阶段清单（M1 收缩版）

> 原 L 级 ExecFlow 已随 IndexedDB 拆分收缩为 5 阶段；IndexedDB 图库另立子任务。
> 恢复点用法：新会话读 `design.md`（含 §0 范围收缩说明）+ 本文件 + 上一阶段 `phase-<N>-result.md`。
> 每阶段独立 commit；阶段失败可单独 revert，不阻断已完成阶段。

---

## Phase 1：基线固化（不改代码）

- **输入**：当前 `feat/theme-skin-system` 工作树
- **动作**：启动服务，用 DevTools 对 **7 个页面 × dark/light 两主题 × 1440/1024 两宽度** 截图存档到 `research/baseline/`；用 `getComputedStyle` 导出当前 `--app-*` 计算值快照。
- **输出**：`research/baseline/*.png` + `research/baseline/tokens-dark.json` / `tokens-light.json`
- **验证**：截图齐全可读；token 快照覆盖 design.md §3.1 全部 60 个变量
- **完成定义**：Phase 2 的"零视觉变化"比对有客观依据
- **依赖**：无

## Phase 2：皮肤 token 层等价重构（**零视觉变化，风险闸门**）

- **输入**：Phase 1 的 token 快照
- **动作**：新增 `utils/skin.js`（合成 + 表驱动派生）+ `utils/skins/`（先只放 `legacy-dark`/`legacy-light`，以 tokens 全量反填现状）；`themeStore` 改为「读皮肤 → 合成 tokens → 写 `:root`」；`App.vue` 的 `:root[data-theme]` 两套块保留为兜底（JS 覆盖）。
- **输出**：`utils/skin.js`、`utils/skins/index.js`、改造后的 `stores/themeStore.js`、`__tests__/skin.spec.js`
- **验证**：
  - `cd frontend; npm run test`（`skin.spec.js`：派生规则、非法皮肤回退、token 数量对齐）
  - 7 页 × 2 主题重截图 → 与 Phase 1 基线逐项 diff，差异仅允许抗锯齿级噪声
- **完成定义**：组件零改动 + diff 通过 + 证明「变量名不变 ⇒ 组件零侵入」（写入 `phase-2-result.md`）
- **依赖**：Phase 1
- **本阶段是闸门**：不通过不得进入 Phase 3

## Phase 3：效果层（scrim 多层 + 玻璃三件套 + surface 分档）

- **输入**：Phase 2 的皮肤层
- **动作**：新增 `--app-scrim-*` / `--app-glass-*` / `--app-surface-*` 变量与派生（`utils/effects.js`）；`body::after` 改 4 层 scrim（中性遮罩色）；`.app-card`/`.wb-card` 改玻璃三件套 + `@supports` / `prefers-reduced-transparency` 降级；`BackgroundPanel` 滑块按 design.md §5.3 重排。
- **输出**：`App.vue`（背景层 + 卡片类）、`BackgroundPanel.vue`、`utils/effects.js`、`__tests__/effects.spec.js`
- **验证**：
  - DevTools 在**最坏背景位置**测文字对比度（≥4.5:1）；暗角滑到 0 时图片完全不被压暗
  - `npm run test`（`effects.spec.js`：参数 → alpha 换算边界 0 / 上限 / 越界钳制）
- **完成定义**：A2 / A3 满足；7 页在 6 种 scrim 强度下无不可读文字
- **依赖**：Phase 2

## Phase 4：皮肤包（内置 6 套 + 导入导出 + 面板）

- **输入**：Phase 3 的效果层
- **动作**：填充 4 套 Catppuccin（palette 模式）；面板新增皮肤选择器、导出按钮、导入 `.json`（含校验 + 失败提示）；`themeStore` 加 `skinId` 与导入导出动作。
- **输出**：`utils/skins/`（4 套）、`utils/skinSchema.js`、`BackgroundPanel.vue`、`themeStore.js`、`__tests__/skinSchema.spec.js`
- **验证**：
  - `npm run test`（校验器：合法/缺字段/类型错/未知 mode 全覆盖；导入非法文件必须报错不静默）
  - 6 套皮肤 × 明暗 × 7 页截图核对（A1）
- **完成定义**：A4 满足；新增皮肤确实零组件改动（用第 6 套验证）
- **依赖**：Phase 3

## Phase 5：验收与知识回写

- **输入**：全部阶段
- **动作**：`python backend/verify.py` 全量（后端 pytest + 前端 vitest + lint + build）；重建 `backend/static/`；逐条核对 A1–A4、A6–A9；回滚演练（revert 任一阶段 commit 并确认可恢复）；回写 `CONTEXT.md` + `.trellis/spec/frontend/`（新增主题/皮肤规范）+ 修正 `project-foundation.json` 的 `tokenSource`（当前仍指向已删除的 `frontend/src/style.css`）。
- **验证**：`python backend/verify.py` 退出码 0；验收标准逐条标注证据（截图路径/测试名/命令）
- **完成定义**：DONE（§1.3）= 验收全过 + 门禁绿 + 真实路径亲测 + 已 commit
- **依赖**：Phase 4

---

## 阶段顺序铁律

```
Phase 1 → 2 → 3 → 4 → 5   顺序执行，不跳
Phase 2 是风险闸门：不通过不得进入 Phase 3
```
