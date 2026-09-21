# 执行清单

## 单元 1：R1 命名统一（M0）

1. [ ] 新建 `backend/services/naming.py`（`safe_basename` / `export_basename`）→ 验证：`pytest tests/test_naming.py`
2. [ ] `routers/export.py`：删 `_sanitize_title`，4 格式 + batch 接新单点 → 验证：`pytest tests/test_export_batch.py`
3. [ ] `routers/download.py`：`/file` 支持 `job_id` + `label` → 验证：`pytest tests/test_routers.py -k download`
4. [ ] 前端 `HomeResultPanel.downloadJobFile` 传 `job_id`/`label` → 验证：`npm test`
5. [ ] commit：`feat(export): 导出与下载文件名统一为视频名优先`

## 单元 2：R2 单任务串行队列（M1）

6. [ ] `config.py` 加 `MAX_CONCURRENT_JOBS`（env 覆盖，默认 1）；`main.py` 引用 → 验证：`pytest tests/test_main.py`
7. [ ] 新建 `routers/jobs_queue.py` + `GET /api/jobs/queue`，main.py 注册 → 验证：`pytest tests/test_job_queue.py`
8. [ ] 前端 `HomeView` 提交按钮解除禁用 + 文案「加入队列」；`jobStore.submitJob` 不清空当前跟踪 → 验证：`npm test`
9. [ ] 新建 `composables/useJobQueue.js` + `components/JobQueueList.vue`，挂载到 `JobProgressPanel` → 验证：`npm test`
10. [ ] `utils/api.js` 加 `jobsApi.queue`；`HomeResultPanel` 队列切换 → 验证：`npm run lint`
11. [ ] commit：`feat(jobs): 单任务支持串行队列，运行中可继续提交`

## 收尾

12. [ ] 更新 `.trellis/spec/backend/index.md`（并发默认 1）与 `CONTEXT.md`
13. [ ] `python verify.py` 全绿
14. [ ] 浏览器实测（7001）：连提 3 链接 → 排队 → 逐个跑；导出/下载文件名核对
