# 修复历史记录搜索无效

## Goal

用户反馈「历史记录的搜索没有用」。经诊断（见 `research.md`），这不是一个参数不匹配的 bug，而是**功能根本没接上**——需要先和用户确认期望的搜索行为，再决定修法。

## 需求

我要解决什么问题：在历史记录页输入关键词搜索，期望筛出相关任务，实际无效果。

## 诊断结论摘要（详见 research.md）

诊断发现 HistoryView.vue 里唯一的搜索框接的是**转录文本全文搜索**（`/api/jobs/search/transcript`），不是历史列表过滤。两条路径完全分离：

- 搜索框 → `searchTranscript(q)` → `/api/jobs/search/transcript?q=...` → `search_transcripts()`（`jobs.py:849-961`）：端到端接线正确，但 `jobs.py:906` 会跳过无 txt 文件的任务——若历史任务无 on-disk txt，`total_jobs_searched=0`，用户看到「未找到匹配内容」。
- 历史列表 → `loadJobs()` 只发 `{limit, offset}` → `/api/jobs` → `list_jobs()`（`jobs.py:251-259`）只读 `status/limit/offset` → `get_jobs()`（`job_repo.py:87-103`）只按 `status` 过滤。**没有任何 title/url 关键词过滤**，前后端都没有。

## 待用户确认（阻塞本任务）

用户说的「搜索没用」指的是哪种期望？

1. 期望「按标题/URL 过滤历史列表」→ 该功能从未实现，需新增 list 端点的 keyword 参数 + `get_jobs` 的 `WHERE title LIKE / input_url LIKE` + 前端把搜索框接 to list。
2. 期望「转录全文搜索有结果」但看到「未找到」→ 接线正确，问题在 on-disk txt 缺失（环境问题），需排查为何 jobs 无 txt 文件。
3. 期望搜索框同时过滤列表 + 搜转录 → 需明确产品行为。

## 验收标准

- [ ] 待用户确认期望行为后补全
- [ ] 若为路径1（列表过滤）：新增 `keyword` 参数贯穿前端→`/api/jobs`→`get_jobs`，覆盖有结果/无结果/特殊字符
- [ ] 若为路径2（转录搜索）：修复 on-disk txt 缺失或给出明确提示
- [ ] 相关测试通过

## 不做项

- 待确认期望行为后定

## 影响范围

- `frontend/src/views/HistoryView.vue`（搜索框 320-330、`doSearch` 141-153、`loadJobs` 24-35）
- `frontend/src/utils/api.js`（`list` 40、`searchTranscript` 55-56）
- `backend/routers/jobs.py`（`list_jobs` 251-259、`search_transcripts` 849-961）
- `backend/db/job_repo.py`（`get_jobs` 87-103）

## 风险与回滚

- 风险等级：M0-M1（视确认的期望行为而定）
- 回滚方式：revert commit
