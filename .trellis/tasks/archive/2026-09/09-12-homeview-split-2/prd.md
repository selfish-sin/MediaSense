# HomeView 输入区/结果区拆分 - PRD

## 需求

承接 `09-12-homeview-split`（已归档）留下的「未拆」项：`HomeView.vue` 当前 1159 行，输入区与结果区两个大块仍内联。本任务把这两块拆为子组件，**行为零变化**，把 HomeView 压到 frontend spec 的 ~800 行建议值以内。

## 验收标准

- [ ] 拆出 2 个子组件：`HomeInputPanel`（输入区）、`HomeResultPanel`（结果区）
- [ ] HomeView 行数 ≤ 800（当前 1159）
- [ ] 行为零变化：提交/上传/批量提交与确认/结果展示/在线编辑/导出/下载素材/重新总结/思维导图/复制全部不变
- [ ] `npm run lint` + vitest + `vite build` 全绿；`python verify.py` 全绿
- [ ] 浏览器端到端：工作台渲染正常、console 无未解释 error
- [ ] CONTEXT.md 文件索引更新（新增组件 + 何时读）

## 不做项

- 不拆「转录预览卡」（与 socket preview 事件、批量预览确认、`jobStore.previewText` 双向绑定耦合，保留 HomeView）
- 不改业务逻辑、接口契约、视觉样式、文案
- 不动已拆的 5 个组件与 3 个 composable

## 影响范围

- `frontend/src/views/HomeView.vue`（减负）
- 新增 `frontend/src/components/HomeInputPanel.vue`、`HomeResultPanel.vue`
- `frontend/src/App.vue`（`.input-main` 提升全局，供输入区组件与预览卡共用）

## 风险与回滚

- 风险等级：M1。风险点：props/emit 契约漏参（现有测试不覆盖 HomeView，靠 lint/build + 浏览器端到端兜底）
- 回滚：按切片逐个 commit，单点 `git revert` 即可回退
