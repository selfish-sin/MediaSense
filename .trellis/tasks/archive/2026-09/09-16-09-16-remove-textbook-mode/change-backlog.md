# Change Backlog（task: 09-16-remove-textbook-mode）

执行中新增需求（§8 范围锁）记录：

## 1. 工作台进度卡缺取消入口
- 来源：用户反馈（找不到取消按钮，只能去历史页「更多」下拉）
- 价值：任务从工作台提交后无法就地终止，取消入口隐藏过深
- 影响：仅前端单组件（`JobProgressPanel.vue`）+ 复用既有 `jobsApi.cancel`，不影响本 task 正确性
- 处理：**已在本会话即时实现并独立提交**（`feat(home): 工作台进度卡新增「取消任务」按钮`），S 级，未扩大本 task 范围
