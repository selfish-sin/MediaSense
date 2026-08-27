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

### 安全边界

- CORS 正则只允许 localhost/127.0.0.1 任意端口
- SPA 静态路由必须先 `resolve()` 再 `is_relative_to()` 校验，防 `../` 穿越（`main.py` serve_vue_spa）
- 应用层删除仅限 localhost + 无活动任务

### 错误处理

- 禁止 `except: return []` 静默吞错；异常要么处理（降级 + 日志）要么上抛（转成用户可见的 job error）
- 任务失败必须 `update_job(status='failed', error=...)`，不留 pending 僵尸

## Quality Check

- [ ] `cd backend && venv/Scripts/python.exe -m pytest` 全绿（基线 398+）
- [ ] 新增/修改的 service 函数有对应测试（成功 + 主要错误路径）
- [ ] 没有在 async 函数中引入新的同步阻塞调用
