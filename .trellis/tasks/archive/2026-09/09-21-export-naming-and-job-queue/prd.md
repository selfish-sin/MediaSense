# 导出命名统一与单任务串行队列

## Goal

1) 导出/下载文件名统一为「视频名优先」，保留可读标点；2) 单任务支持串行队列：任务进行中可继续提交链接，按提交顺序逐个处理。

## Requirements

### R1 命名统一（视频名优先）

- R1.1 新建命名单点 `backend/services/naming.py`：`safe_basename(name, max_len=80)` 只剔除 Windows 非法字符 `\/:*?"<>|` 与控制字符，**保留**《》、-、·、，、空格；折叠连续空白；≤80 字符不截断；去首尾空格/点。
- R1.2 `export_basename(job)` 兜底链：`title` → 平台 ID（BV 号 / YouTube video id）→ `job_{id}`；杜绝 `AI总结_<job_id>` 这类不可读名。
- R1.3 单任务导出 4 格式（docx/pdf/html/txt）文件名走 R1.2；磁盘落盘名含 job_id 短码保证唯一，响应头 `filename=` 用可读名。
- R1.4 批量导出 ZIP：下载名 1 个视频 → `{视频名}.zip`；多个 → `{首个视频名}等{N}个.zip`；去掉随机 hex 段（磁盘名仍唯一化）。
- R1.5 批量 ZIP 内每个 `.md` 用 `safe_basename`（不再 50 字硬截断），重名补序号。
- R1.6 `/api/download/file`（下载素材）新增可选 `job_id` + `label` 参数 → `{视频名}_{素材类型}.{ext}`；缺参回落磁盘名（向后兼容）。

### R2 单任务串行队列

- R2.1 `MAX_CONCURRENT_JOBS` 改由 config 提供，默认 **1**（严格串行），可用环境变量覆盖回 2；`process_job` 的信号量/preview 释放/finally 单一出口结构不变。
- R2.2 新增 `GET /api/jobs/queue`：返回 `{running[], pending[]（created_at ASC）, concurrency, total}`；pending 取 `status='pending'`。
- R2.3 不新增 socket 事件（遵守 spec 单一事实源原则），队列刷新靠既有 progress/log 事件触发 + 断线兜底轮询。
- R2.4 工作台提交按钮在任务进行中不再禁用，文案「加入队列」；队列非空时提交不重置当前任务跟踪。
- R2.5 新增 `composables/useJobQueue.js` + `components/JobQueueList.vue`，挂在 `JobProgressPanel.vue` 下方；每项可单独取消。
- R2.6 队列刷新/切页后仍在（数据源为 DB 的 pending 任务；服务重启后不自动续跑，与现状一致）。

## Acceptance Criteria

- [ ] 批量导出 1 个视频：下载名 = 视频名（保留《》、- 等标点），ZIP 内 `.md` 同名，无随机 hex。
- [ ] 单任务导出 Word/PDF/HTML/TXT：下载名 = 视频名；标题为空的任务落 BV 号，不出现 `AI总结_e923c16b`。
- [ ] 下载素材：`{视频名}_字幕文件.srt`、`{视频名}_纯音频.m4a`；不传新参数时行为不变。
- [ ] 连提 3 个链接：第 1 个跑，2/3 显示「排队中」；逐个自动开始至全部完成。
- [ ] F5 刷新 / 切历史页再回来：队列仍在，顺序不变。
- [ ] 排队中任务取消 → 状态变已取消，不阻塞后续任务。
- [ ] 批量模式（batch 面板 + 批量子任务严格串行）行为零变化。
- [ ] 后端 pytest 全绿（基线 748）+ 前端 vitest / lint / build 全绿 + 7001 浏览器实测无 console 错误。

## 不做项

- 不做队列拖拽排序 / 暂停恢复 / 手动插队。
- 不做服务重启后自动续跑 pending 队列。
- 不改批量子任务编排（`_run_batch_sequential`）与 preview 释放信号量语义。
- 不改任务提交的 URL 去重（`force_new`）行为。

## 影响范围

- 后端：`routers/export.py`、`routers/download.py`、新增 `services/naming.py`、`routers/jobs_queue.py`、`main.py`（并发常量来源）、`config.py`。
- 前端：`views/HomeView.vue`、`stores/jobStore.js`、`components/JobProgressPanel.vue`、新增 `composables/useJobQueue.js`、`components/JobQueueList.vue`、`utils/api.js`、`components/HomeResultPanel.vue`（下载素材传参）。
- 文档：`.trellis/spec/backend/index.md`（并发=2 的描述更新）、`CONTEXT.md`。

## 风险与回滚

- 风险等级：M1（R1 为 M0，R2 跨前后端）。
- 不可逆操作：无（无 DB 迁移、无删除）。
- 回滚方式：R2 设 `MAX_CONCURRENT_JOBS=2` 即回并发 2；R1/R2 均独立提交，可单独 revert。
- 已知取舍：并发 1 会让「两个任务并行」变慢（用户明确要求逐个做）；preview 等待期间仍释放槽位（既有设计，保持）。
