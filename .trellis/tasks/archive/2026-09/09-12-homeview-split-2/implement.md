# HomeView 输入区/结果区拆分 - Implementation Plan

## 执行清单（按序，每步独立 commit）

1. [x] slice-A `HomeInputPanel.vue`（输入区 + 上传逻辑内聚 + BatchResultPanel 转发）→ 验证: lint + vitest + build（commit 1fa3f44）
2. [x] slice-B `HomeResultPanel.vue`（结果区 + expose reset）→ 验证: lint + vitest + build（commit bd4ee13）
3. [x] 全量验证 `python verify.py` 全绿（pytest 602 / vitest 61）+ 浏览器端到端（页面渲染/输入区 v-model 切换/结果区 restore 渲染/编辑模式往返，console 无 error/warn）
4. [ ] CONTEXT.md 文件索引/状态更新 → 归档

## 验证命令

```powershell
cd frontend; npm run lint; npm test; npx vite build
cd ..; python verify.py
```

## 硬规则

- 行为零变化：只搬移，不改逻辑/文案/视觉
- 每切片完成即 commit；失败留 [WIP-FAIL] 痕迹
- 同一根因失败 2 次换策略，禁第 3 次盲重试
- 拆出的 CSS 必须跟随组件（保持一致作用域）
