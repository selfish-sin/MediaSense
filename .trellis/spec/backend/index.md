# Backend Spec（FastAPI + Socket.IO + SQLite）

## Pre-Development Checklist

改任何 backend 代码前确认：

- [ ] 读过 `CONTEXT.md` 文件索引表，定位到目标模块
- [ ] 跑过 `codegraph explore "<符号>"`（索引不可用时在 task 工件记录原因）
- [ ] 涉及任务编排时读过 `main.py` 的 `process_job`（信号量并发=2，preview 阶段释放许可的设计勿破坏）
- [ ] 涉及存储删除时读过 `scripts/cleanup_storage.py`（dry-run + `--apply --confirm DELETE` 双门槛不可绕过）

## 核心约定

### 异步纪律

- 同步阻塞调用（yt-dlp / faster-whisper / ffmpeg / paddle）**必须**包进 `asyncio.to_thread`，禁止在 async 路径直接调用——违反会卡死整个事件循环（Socket.IO 心跳、其他任务进度全停）。
- faster-whisper 是惰性编码：`model.transcribe()` 返回生成器，CUDA 运行时缺失（cublas64_12.dll）在**迭代期**才抛出。物化 `list(segs)` 必须留在 try 块内才能触发 GPU→CPU 降级（教训来源：任务 f40508bd 失败）。

### B站 API 风控（2026-08-26 加固，勿回退）

- 进程级锁 + 最小间隔 ≥1s（`_throttle_bili_api`，`BILI_API_MIN_INTERVAL` 可调）
- `_fetch_subtitles_via_player_v2` 先走旧版 `x/player/v2`（带 dm_img_str），wbi 降级备用，412 时强制刷新 wbi 密钥重试一次
- 完整 Chrome UA + 进程级缓存 buvid3/buvid4 + 真实视频页 Referer（`_bili_headers`）
- OpenAI 兼容流式接口（阿里云 MaaS）末尾会发 `choices: []` 的 usage chunk——取值必须用 `(chunk.get('choices') or [])` 防护，`[0]` 直接索引会越界（dict.get 默认值只在 key 缺失时生效）

### 数据库

- SQLite 连接：WAL + busy_timeout=5000 + check_same_thread=False，每次操作独立连接、用完即关
- 迁移用 `ALTER TABLE ... ADD COLUMN` 链（`db/database.py` init_db），新列必须有默认值
- 播种只发生在表为空时（`db/asr_repo.seed_builtin_providers` 是范例）：把 config.py 的既有值
  一次性迁进 DB，之后 DB 为准、config 兜底 → 升级与回滚都平滑

### 安全边界

- CORS 正则只允许 localhost/127.0.0.1 任意端口
- SPA 静态路由必须先 `resolve()` 再 `is_relative_to()` 校验，防 `../` 穿越（`main.py` serve_vue_spa）
- 应用层删除仅限 localhost + 无活动任务

### 错误处理

- 禁止 `except: return []` 静默吞错；异常要么处理（降级 + 日志）要么上抛（转成用户可见的 job error）
- 任务失败必须 `update_job(status='failed', error=...)`，不留 pending 僵尸

### 路由组织（2026-08-27 拆分，勿回退）

- `routers/jobs.py` 按域拆为三路由，全部挂 `/api/jobs` 前缀，对外路径零变化：
  - `jobs.py`（核心域：上传/提交/列表/删除/清理/取消/重试/编辑结果）+ 共享工具 `normalize_url`/`extract_urls`
  - `jobs_ai.py`（AI 域：重新总结/总结历史/标签/思维导图）
  - `jobs_batch.py`（批量域：批量提交/批量状态/转录搜索）
- `routers/asr.py`（只读状态/总览）与 `routers/asr_admin.py`（写：provider CRUD、测试连接、
  默认设置、本地模型生命周期）同挂 `/api/asr`，读写分文件；脱敏与推荐策略在 `asr.py` 内单点，
  `asr_admin.py` `from routers.asr import` 复用（勿各写一份）
- 新增任务相关端点时先判断归属域，不要再往 `jobs.py` 堆
- 跨域共享的纯函数（如 URL 规范化）放核心 `jobs.py`，其他路由 `from routers.jobs import` 复用
- 出错一律走「可行动文案」：provider 配置类错误要指名到「模型管理 → ASR 模型」哪个 provider 的
  哪一项，不能只甩 HTTP 码（`transcribe_file` 的 401/403/404 分支是范例）

### 任务编排（2026-08-27 拆分）

- `process_job` 保持"信号量包装 + finally 单一收尾出口"结构；输入获取已拆为策略函数
  `_handle_text_input`/`_handle_subtitle_file`/`_handle_media_file`/`_handle_url_input`（统一返回
  `(srt_path, txt_path, seg_count, title, platform, files)`），新输入类型加策略函数，不要往
  `_process_job_impl` 回填大段 if/else
- 批量任务终态推送走 `_emit_batch_progress`（`process_job` finally 调用，负载复用
  `get_batch_summary` 单一事实源）；新增批量面板数据字段时同步改 summary 而不是另造事件

## Quality Check

- [ ] `cd backend && venv/Scripts/python.exe -m pytest` 全绿（基线 692，必须用项目 venv——沙箱默认 Python 缺 pytest-asyncio，异步用例会批量假失败）
- [ ] 新增/修改的 service 函数有对应测试（成功 + 主要错误路径）
- [ ] 没有在 async 函数中引入新的同步阻塞调用
