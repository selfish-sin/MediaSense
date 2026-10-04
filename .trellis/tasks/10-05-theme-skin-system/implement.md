# 背景与皮肤系统重构 - Implementation Plan

## 涉及文件总览

**改动**
- `frontend/src/stores/themeStore.js`（重写内部逻辑，对外 API 兼容）
- `frontend/src/utils/backgrounds.js`（+ 动态预设）
- `frontend/src/utils/color.js`（复用；必要时抽公共采样）
- `frontend/src/components/BackgroundPanel.vue`（面板重构）
- `frontend/src/App.vue`（背景层 + 卡片类 + 变量块）

**新增**
- `frontend/src/utils/skin.js`、`frontend/src/utils/skins/index.js`、`frontend/src/utils/skinSchema.js`
- `frontend/src/utils/effects.js`、`frontend/src/utils/imageFit.js`
- `frontend/src/__tests__/skin.spec.js`、`effects.spec.js`、`skinSchema.spec.js`、`imageFit.spec.js`

**禁止触碰**：`backend/**`、`contracts/**`、Element Plus 版本、其他 20 个消费 `var(--app-*)` 的组件（本设计前提是零改动）。
**移交子任务**：`utils/bgStore.js`（IndexedDB 图库）与背景图迁移 —— 见 design.md §0。

## 执行清单

1. [ ] **基线固化**（Phase 1）→ 验证：`research/baseline/` 截图 + token 快照齐全
2. [ ] **skin.js 合成器 + 表驱动派生** → 验证：`npm run test -- skin` 全绿（派生规则/回退/数量对齐）
3. [ ] **内置 `legacy-dark` / `legacy-light`（tokens 全量反填）** → 验证：`:root` 计算值与 Phase 1 快照逐项相等
4. [ ] **themeStore 接入皮肤层（保持对外 API）** → 验证：截图 diff 零视觉漂移；`BackgroundPanel`/`App.vue` 无需改
5. [ ] **Phase 2 闸门复核** → 验证：7 页 × 2 主题截图 diff 通过 → 写 `phase-2-result.md`
6. [ ] **effects.js + scrim 变量** → 验证：`npm run test -- effects`；暗角 0 时图片不被压暗
7. [ ] **`body::after` 4 层 scrim + 中性遮罩色** → 验证：最坏背景位对比度 ≥4.5:1
8. [ ] **卡片玻璃三件套 + surface 分档 + 降级** → 验证：`@supports` / `prefers-reduced-transparency` 生效；表格仍实底
9. [ ] **面板滑块重排（design.md §5.3 映射表）** → 验证：每个滑块都能独立影响视觉、无互相打架
10. [ ] **4 套 Catppuccin 皮肤数据** → 验证：6 套 × 明暗 × 7 页无不可读文字
11. [ ] **skinSchema 校验器 + 导入导出** → 验证：非法 JSON 必须报错（禁止静默）；导出可再导入
12. [ ] **动态背景预设（失焦暂停）+ 背景类型扩展** → 验证：切窗口后 CPU 不再增长；`prefers-reduced-motion` 回退静态帧
13. [ ] **imageFit 自动推荐 + 一键采用** → 验证：亮图/暗图各一次，推荐值合理；单测覆盖边界
14. [ ] **全量门禁** → 验证：`python backend/verify.py` 退出码 0；重建 `backend/static/`
15. [ ] **验收 A1–A4、A6–A9 逐条取证 + 回滚演练** → 验证：每条验收标准指向具体截图/测试/命令
16. [ ] **知识回写**（`CONTEXT.md`、`spec/frontend/`、`project-foundation.json`）→ 验证：索引表新增文件行齐全

## 切片清单

| 切片 | 名称 | 阶段 | 关键产出 | 依赖 |
|---|---|---|---|---|
| S1 | 基线固化 | Phase 1 | 截图 + token 快照 | 无 |
| S2 | 皮肤层等价重构 | Phase 2 | `skin.js` / `skins/` / `themeStore` | S1 |
| S3 | 效果层重构 | Phase 3 | scrim + 玻璃 + `effects.js` | S2 |
| S4 | 皮肤包 | Phase 4 | 4 套皮肤 + 校验 + 导入导出 | S3 |
| S5 | 背景扩展与验收 | Phase 5 | 动态预设 + `imageFit` + 门禁绿 | S4 |
| — | 背景图库（IndexedDB） | 子任务 | `bgStore.js` + 迁移 | S5 |

## 验证命令

```powershell
# 前端定向
cd d:\projects\youtube_tool_v2-theme\frontend; npm run test; npm run lint

# 全量一键（后端 pytest + 前端 vitest + lint + build）
cd d:\projects\youtube_tool_v2-theme; python backend\verify.py

# 构建产物同步（用户实际看到的是 backend/static/）
cd d:\projects\youtube_tool_v2-theme\frontend; npm run build
```

> 注：`npm test` 必须走 `frontend/scripts/run-vitest.mjs`（盘符大小写坑），不要直接 `npx vitest run`。

## Done Checklist

- [ ] 验收标准 A1–A4、A6–A9 全部满足且有证据
- [ ] 20 个既有组件零改动（`git diff --stat` 证明）
- [ ] 四态覆盖：皮肤加载失败 / 导入非法 / 图片解码失败 / 存储配额溢出 → 全部可见可行动
- [ ] 无静默吞错（`catch` 一律有用户可见反馈）
- [ ] `python backend/verify.py` 全绿
- [ ] `backend/static/` 已重建
- [ ] 回滚演练通过（单阶段 revert 可恢复）
- [ ] 每阶段已 commit（COMMIT-DISCIPLINE）
