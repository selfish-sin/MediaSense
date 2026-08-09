# 修复历史记录搜索无效

## Goal

用户反馈「历史记录的搜索没有用」。经诊断（见 `research.md`），这不是一个参数不匹配的 bug，而是**功能根本没接上**——需要先和用户确认期望的搜索行为，再决定修法。

## 需求

我要解决什么问题：在历史记录页输入关键词搜索，期望筛出相关任务，实际无效果。

## 诊断结论摘要（详见 research.md）

诊断发现 HistoryView.vue 里唯一的搜索框接的是**转录文本全文搜索**（`/api/jobs/search/transcript`），不是历史列表过滤。两条路径完全分离：

- 搜索框 → `searchTranscript(q)` → `/api/jobs/search/transcript?q=...` → `search_transcripts()`（`jobs.py:849-961`）：端到端接线正确，但 `jobs.py:906` 会跳过无 txt 文件的任务——若历史任务无 on-disk txt，`total_jobs_searched=0`，用户看到「未找到匹配内容」。
- 历史列表 → `loadJobs()` 只发 `{limit, offset}` → `/api/jobs` → `list_jobs()`（`jobs.py:251-259`）只读 `status/limit/offset` → `get_jobs()`（`job_repo.py:87-103`）只按 `status` 过滤。**没有任何 title/url 关键词过滤**，前后端都没有。

## 用户确认的期望（2026-08-09）

用户确认期望 = **路径1：按标题/URL 过滤历史列表**。即：在历史记录页输入关键词，列表只显示标题或 URL 含该词的任务。

注意：HistoryView.vue 当前的搜索框接的是转录全文搜索（`searchTranscript`），placeholder 也是"搜索所有任务的转录文本内容"。本任务要把搜索行为改为列表过滤。需考虑：是替换转录搜索，还是新增一个列表过滤框？默认按"替换"处理（当前转录搜索因 on-disk txt 缺失实际也无结果），若用户要保留转录搜索再调整。

## 验收标准

- [ ] `get_jobs`（`job_repo.py:87`）加 `keyword` 参数，`WHERE title LIKE ? OR input_url LIKE ?` 过滤，total 计数同步
- [ ] `list_jobs`（`jobs.py:251`）加 `keyword` 查询参数，透传给 `get_jobs`
- [ ] 前端 `loadJobs`（`HistoryView.vue:24`）把搜索框关键词作为 `keyword` 传给 `jobsApi.list`
- [ ] 搜索框触发方式：输入即过滤（debounce）或回车/按钮触发（与现有 doSearch 行为一致）
- [ ] 空关键词时恢复完整列表
- [ ] 覆盖测试：有结果 / 无结果 / 特殊字符（如 `%`、`_` 需转义，防 SQL LIKE 通配符注入）
- [ ] backend pytest + frontend vitest/build 全绿

## 不做项

- 不改转录全文搜索端点（`search_transcripts`）本身——它接线正确，本任务不碰
- 不改 `find_job_by_url` 等其他查询

## 影响范围

- `frontend/src/views/HistoryView.vue`（搜索框 320-330、`doSearch` 141-153、`loadJobs` 24-35）
- `frontend/src/utils/api.js`（`list` 40）
- `backend/routers/jobs.py`（`list_jobs` 251-259）
- `backend/db/job_repo.py`（`get_jobs` 87-103）
- `backend/tests/test_routers.py` / `test_job_repo.py`（新增 keyword 过滤测试）

## 风险与回滚

- 风险等级：M1（跨前后端 4 文件 + 测试，但方案清晰、无 schema 变化）
- 不可逆操作：无
- 回滚方式：revert commit
