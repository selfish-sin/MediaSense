# HomeView 巨型文件拆分 - Implementation Plan

## 执行清单（按序，每步独立 commit）

1. [ ] slice-1 `BackendLogPanel.vue`（日志面板，最安全）→ 验证: lint + vitest + build
2. [ ] slice-2 `HomeAdvancedOptions.vue`（高级选项，v-model 双向）→ 验证: lint + vitest + build
3. [ ] slice-3 `BatchResultPanel.vue`（批量面板，受控）→ 验证: lint + vitest + build
4. [ ] slice-4 `JobProgressPanel.vue`（进度卡 + socket 迁移）→ 验证: lint + vitest + build
5. [ ] slice-5 `HomeTokenPanel.vue`（Token 卡 + expose refresh）→ 验证: lint + vitest + build
6. [ ] 全量验证 `python verify.py` + 浏览器端到端（DevTools：console 无未解释 error，工作台渲染正常）
7. [ ] CONTEXT.md 文件索引/状态更新 → 归档

## 验证命令

```powershell
cd frontend; npm run lint; npm test; npx vite build
cd ..; python verify.py
```

## 硬规则

- 行为零变化：只搬移，不改逻辑/文案/视觉
- 每切片完成即 commit；失败留 [WIP-FAIL] 痕迹
- 同一根因失败 2 次换策略，禁第 3 次盲重试
- 拆出的 CSS 必须跟随组件（保持 scoped 作用域一致）
