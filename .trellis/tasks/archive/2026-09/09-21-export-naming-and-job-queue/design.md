# 导出命名统一与单任务串行队列 — Design

## 架构

### R1 命名单点

```
services/naming.py
  ├── safe_basename(name, max_len=80, fallback='')  # 纯函数：剔非法字符、保标点、折叠空白
  └── export_basename(job)                          # 标题 → BV/YT id → job_{id}
        ↑                    ↑
  routers/export.py     routers/download.py
  (4 格式 + batch ZIP)  (下载素材 filename=)
```

- **单一事实源**：所有对外文件名只经 `naming.py`，删除 `export.py._sanitize_title`（5 处调用一并替换）。
- **磁盘名 vs 下载名解耦**：磁盘落盘名保持唯一（附 job_id / 时间戳），HTTP `filename=` 用可读名。避免"唯一性"与"可读性"互相妥协。

### R2 单任务串行队列

```
前端                                   后端
─────────────────────────────────────────────────────────────
HomeView 提交按钮（不再禁用）
  └→ jobStore.submitJob
       ├─ 队列为空且无任务 → 原逻辑（跟踪新任务）
       └─ 否则 → 只 POST 提交，不改 currentJobId

JobProgressPanel
  └── JobQueueList ── GET /api/jobs/queue ── routers/jobs_queue.py
      useJobQueue()                            ├─ get_running_jobs()  (4 个 active 状态)
      ├─ 事件触发刷新（progress/log，debounce） ├─ get_jobs(status='pending')
      ├─ socket 断线 10s 轮询                  └─ concurrency = MAX_CONCURRENT_JOBS
      └─ 单项取消复用 POST /jobs/{id}/cancel

                      process_job（结构不动）
                      sem = Semaphore(MAX_CONCURRENT_JOBS=1)  ← config 提供
```

- 串行由**既有信号量**保证，不新增调度器；`MAX_CONCURRENT_JOBS` 默认 1，环境变量可回 2。
- 队列"持久"= 读 DB 的 `pending` 任务，无需新表、无迁移。
- 不新增 socket 事件（spec：新增面板字段改 summary 而不是另造事件）→ 用既有 `progress`/`log` 事件做触发信号，兜底轮询 10s。

## 契约

### GET /api/jobs/queue（新增，挂 `/api/jobs` 前缀）

权限：localhost。幂等：是。

成功 200：
```json
{
  "concurrency": 1,
  "total": 3,
  "running": [
    { "id": "abc12345", "title": "视频名", "status": "downloading",
      "progress_msg": "下载中 30%", "input_url": "https://...", "created_at": "2026-09-21 12:00:00" }
  ],
  "pending": [
    { "id": "def67890", "title": null, "status": "pending",
      "progress_msg": "等待处理", "input_url": "https://...", "created_at": "2026-09-21 12:01:00" }
  ]
}
```
边界：空队列 → 200 + `running: []` / `pending: []`（不是 404）。

### GET /api/download/file（改，向后兼容）

| 参数 | 类型 | 说明 |
|---|---|---|
| path | str（必填） | 文件路径（既有） |
| job_id | str（可选） | 提供时用该任务标题生成可读下载名 |
| label | str（可选） | 素材类型文案，如「字幕文件」 |

- 两个新参都提供 → `{safe_basename(title)}_{label}.{ext}`；标题拿不到 → `{job_id}_{label}.{ext}`。
- 缺任一新参 → 回落磁盘名（既有行为，老调用方零影响）。

### export 端点（改）

- 4 个单格式端点：`filename=` 由 `_sanitize_title(title)` 改 `export_basename(job)`；磁盘路径改为 `{safe}_{job_id短码}.{ext}` 防覆盖。
- `/export/batch`：磁盘 zip 名保持带时间戳唯一；`filename=`（下载名）改为视频名/首名+数量。

## 数据模型

无变更（无新表、无新列、无迁移）。

## 回滚形态

- R2：环境变量 `MAX_CONCURRENT_JOBS=2` 恢复并发 2（无需改代码）。
- 代码层：两个需求各自独立 commit，可单独 `git revert`。
- 前端队列组件是新增文件 + `v-if` 挂载，移除即可回到「提交按钮禁用」旧交互。

## 协作模式

后端先行（R1 纯后端 → 立即可测；R2 先端点后前端），逐逻辑单元提交。
