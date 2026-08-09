# 历史记录搜索无效 — 诊断报告

> 2026-08-09 | 诊断阶段，未改代码 | 诊断 agent: agent_6ba1be07

## 结论

「历史记录搜索」**不是参数不匹配的 bug，是功能根本没接上**。HistoryView.vue 里唯一的搜索框接的是转录文本全文搜索（`/api/jobs/search/transcript`），与历史列表过滤是完全分离的两条路径。历史列表本身从未实现关键词过滤。

## 证据（均含行号）

### 1. 前端搜索框接的是转录搜索，不是列表过滤

`frontend/src/views/HistoryView.vue`：
- 搜索变量：`const searchQuery = ref('')`（line 137）
- 模板绑定（line 320-330）：`<el-input v-model="searchQuery" placeholder="搜索所有任务的转录文本内容（输入关键词）" @keyup.enter="doSearch">` + `<el-button @click="doSearch">搜索</el-button>`
- `doSearch`（line 141-153）：`const res = await jobsApi.searchTranscript(q, 50, 5)` —— 调的是转录搜索，**不碰 `loadJobs()`**
- 触发方式：Enter 键或按钮点击，无 `@input`/`@change`/`watch`/debounce

### 2. 历史列表加载与搜索框无关

`HistoryView.vue` 的 `loadJobs`（line 24-35）：
```js
const res = await jobsApi.list({ limit: pageSize.value, offset: (page.value - 1) * pageSize.value })
```
只发 `limit` 和 `offset`，**不发任何 keyword**。

### 3. 前端 API client

`frontend/src/utils/api.js`：
- `list: (params = {}) => api.get('/jobs', { params })`（line 40）—— 转发调用方给的参数，但 `loadJobs` 只给 `{limit, offset}`
- `searchTranscript: (q, limit=50, maxMatchesPerJob=5) => api.get('/jobs/search/transcript', { params: { q, limit, max_matches_per_job: maxMatchesPerJob } })`（line 55-56）—— 走另一个端点

### 4. 后端列表端点无关键词参数

`backend/routers/jobs.py` 的 `list_jobs`（line 251-259）：
```python
@router.get("", response_model=JobListResponse)
async def list_jobs(
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000),
):
    jobs, total = get_jobs(status=status, limit=limit, offset=offset)
    return JobListResponse(jobs=jobs, total=total)
```
**只读 `status/limit/offset`，无 keyword/q/search/title 参数，无 title/url 过滤。**

### 5. 后端 repo 层无关键词过滤

`backend/db/job_repo.py` 的 `get_jobs`（line 87-103）：
```python
def get_jobs(status=None, limit=50, offset=0):
    if status:
        rows = conn.execute("SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?", (status, limit, offset))
        total = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = ?", (status,)).fetchone()[0]
    else:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset))
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
```
**只按 `status` 过滤，无 `WHERE title LIKE` / `WHERE input_url LIKE`。** 全 repo 唯一的 `LIKE` 用在 `find_job_by_url`（line 485, 493），是提交时 URL 去重，与历史搜索无关。

### 6. 转录搜索端点本身接线正确，但有 on-disk 依赖

`backend/routers/jobs.py` 的 `search_transcripts`（line 849-961）：
- 读 `q`（line 851）✓ 与前端发的参数名一致
- 路由 `/search/transcript`（两段）不会被 `/{job_id}`（一段，line 269）遮挡 ✓
- **关键**：line 906 `if not txt_path or not Path(txt_path).exists(): continue` —— 跳过无 on-disk txt 文件的任务。若历史任务无 txt 文件（文件被删/移动/从未生成），`searched` 保持 0，用户看到「未找到匹配内容」。

## 两种可能的用户期望与对应修法

| 用户期望 | 现状 | 修法 |
|---|---|---|
| A. 按标题/URL 过滤历史列表 | 从未实现 | 前端搜索框接 `loadJobs` + 发 `keyword`；`list_jobs` 加 `keyword` 参数；`get_jobs` 加 `WHERE title LIKE ? OR input_url LIKE ?` |
| B. 转录全文搜索有结果 | 接线对，但 on-disk txt 缺失导致 0 结果 | 排查为何 jobs 无 txt（`main.py:619` 的 `files['txt']` 赋值条件）；或对无 txt 任务给明确提示而非静默跳过 |

## 阻塞

需用户确认期望是 A 还是 B（或两者都要），本 task 才能进入 Design/Build。
