# 质量优化：实时推送/日志修复/巨型文件拆分 - PRD

## 需求

我要解决什么问题：本次代码审查发现的高/中价值优化项 6 项——批量进度实时性、日志静默丢更新、两个巨型文件（main.py 的 _process_job_impl、HomeView.vue）、routers/jobs.py 职责混杂、死代码。

## 具体例子

1. 用户提交批量任务后，进度靠前端每 3 秒 HTTP 轮询 `/api/jobs/batch/{id}` 获取——Socket.IO 基础设施已在但未用于批量进度；期望子任务完成时后端主动推送。
2. HomeView 的 `fetchLogs()` 中 `delta > 0 && delta <= 200` 条件在日志爆发增长时静默丢弃增量；`catch {}` 吞掉网络错误，用户无感知。
3. `backend/main.py` 的 `_process_job_impl` 约 550 行单函数，B站/YouTube/文件/文本四种输入分支内联；B V 号解析逻辑重复两段。
4. `frontend/src/views/HomeView.vue` 2331 行，表单+提交+轮询+日志+批量+恢复+socket 全部内联。
5. `backend/routers/jobs.py` 916 行 26 个端点混装（提交/批量/重试/标签/截图/思维导图）。
6. HomeView L187-192 旧筛选死代码（"保留空变量避免模板引用报错"）。

## 验收标准

- [ ] 批量任务进度通过 Socket.IO 事件推送（`batch_progress`），前端订阅替代 3s 轮询；断线重连时兜底拉取一次 HTTP 状态
- [ ] 日志增量算法：delta 超界时回退全量重载而非静默丢弃；fetchLogs/loadAllHistory 失败时用户可见（非阻塞提示），不再空 catch
- [ ] `_process_job_impl` 拆为按输入类型的策略函数，主函数 ≤150 行；行为不变（398+ pytest 全绿）
- [ ] HomeView.vue 拆出 composable（useBackendLogs / useBatchProgress / useJobRestore），视图文件显著瘦身；死代码（L187-192 及模板引用）删除
- [ ] routers/jobs.py 按域拆分（core/batch/ai/files），对外路由路径与行为完全不变
- [ ] backend pytest 全绿 + frontend vitest 全绿 + frontend build 通过
- [ ] 每个逻辑单元独立 commit

## 不做项

- 不引入新的第三方库（不装 vueuse 等）
- 不改 SQLite 连接策略、不引入 schema_version 迁移表（低价值项，记录备查）
- 不拆 App.vue 主题 CSS（低价值项）
- 不改任何对外 API 路径/请求/响应契约（纯内部重构 + 新增 socket 事件）
- 不做移动端/可访问性改造（超出本次范围）

## 影响范围

- backend：main.py、socket_handlers.py、routers/jobs.py（拆分后新增模块）
- frontend：HomeView.vue、utils/socket.js、新增 composables/
- 风险点：任务编排重构可能影响断点续传/取消/信号量释放语义——有测试护航，逐切片验证

## 风险与回滚

- 风险等级：M2（跨模块全栈、需切片编排；无 schema/契约变化、可回滚）
- 不可逆操作：无
- 回滚方式：逐切片独立 commit，git revert 单个切片即可回退该切片
