# HomeView 巨型文件拆分 - PRD

## 需求

`HomeView.vue` 约 2100 行（前端最大文件），08-27 已拆过一轮（3 个 composable）后仍远超 frontend spec 的 ~800 行建议值。继续把低耦合区块拆为子组件，**行为零变化**。

## 验收标准

- [ ] 拆出 5 个子组件：`BackendLogPanel` / `HomeAdvancedOptions` / `BatchResultPanel` / `JobProgressPanel` / `HomeTokenPanel`
- [ ] HomeView 行数降至 ≤1300 行（当前约 2100）
- [ ] 行为零变化：提交/预览/进度/日志/导出/编辑/重总结/token 统计/批量操作全部不变
- [ ] `npm run lint` 全绿 + vitest 61 全绿 + `vite build` 通过
- [ ] CONTEXT.md 文件索引更新（新组件 + 何时读）

## 不做项

- 不拆「输入区」「结果区」（与 `handleSubmit`/`useJobRestore`/编辑状态强耦合，留给后续切片）
- 不改任何业务逻辑、接口契约、视觉样式

## 风险与回滚

- 风险等级：M1。风险点：props/emit 契约漏参导致运行时报错（现有测试不覆盖 HomeView，靠 lint/build + 浏览器端到端验证兜底）
- 回滚：按切片逐个 commit，单点 `git revert` 即可回退
