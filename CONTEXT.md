# youtube_tool_v2 CONTEXT

> 最近核对：2026-08-09 | FastAPI + Socket.IO | 默认端口：7001

## 一分钟速览

本地视频/音频处理工作站：下载或导入媒体，字幕优先，缺少字幕时回退 Whisper 转录，再做分段、摘要、翻译和导出。后端把 FastAPI 与 Socket.IO 组合为 ASGI 应用，任务并发上限为 2，SQLite 记录任务和 token 用量。

## 技术栈

- 后端：FastAPI + Socket.IO（ASGI 组合）、SQLite（job/token/model 持久化）、yt-dlp（下载）、faster-whisper（字幕缺失时转录）、python-socketio。
- 前端：Vue 3 + Element Plus + Pinia + Vue Router + Vite（构建产物输出到 `backend/static/`，同源 SPA）。
- 模型：Whisper `base`（本地，目录在项目同级 `models/`）；摘要/翻译走可配置的外部 LLM（`backend/config.py` 配 API Key，不入库）。
- 任务流治理：Trellis（`.trellis/` 任务/规范）+ CodeGraph（`.codegraph/` 代码索引，不入库）。
- 打包：PyInstaller（`MediaSense.spec`）+ Inno Setup（`installer.iss`）。

```text
URL/文件 -> downloader -> subtitle 优先或 Whisper
          -> process_job -> 分段/总结/翻译 -> export/download
          -> Socket.IO 日志/进度 + SQLite job/token history
```

## 文件索引

| 文件 | 核心内容 | 何时读 |
|---|---|---|
| `backend/main.py` | FastAPI、Socket.IO、`process_job`、启动恢复和 SPA 路由 | 改任务编排、HTTP 或 websocket |
| `backend/config.py` | 数据目录、7001、Whisper 模型和清理 TTL | 改路径、端口或默认值 |
| `backend/db/` | SQLite 初始化、job、模型、token 用量仓储 | 改持久化或任务状态 |
| `backend/services/downloader.py` | yt-dlp 下载/媒体准备 | 改 URL 下载和代理 |
| `backend/services/transcriber.py` | 字幕读取、Whisper 转录、语言/模型 | 改转录回退 |
| `backend/services/` | 分段、摘要、翻译、导出、搜索等处理 | 改具体媒体流程 |
| `backend/routers/` | API 路由，包括 storage cleanup | 加/改 HTTP API |
| `backend/socket_handlers.py` | 实时日志和进度事件 | 改前端实时反馈 |
| `backend/scripts/cleanup_storage.py` | staged/orphan/export/media 清理 | 改清理策略；删除必须显式确认 |
| `frontend/`、`static/` | 页面和同源静态资源 | 改 UI |

## 服务与任务

`main.py` 直接运行时使用 `SERVER_HOST=0.0.0.0`、`SERVER_PORT=7001`，挂载 `/ws/socket.io`，并提供 `/api/logs`、`/api/token-usage`、`/api/token-usage/history` 和 SPA 路由；其余任务 API 分散在 `backend/routers/`。全局 `MAX_CONCURRENT_JOBS=2`，启动时会把中断任务标记为失败/可恢复状态。

```powershell
cd backend
python main.py
# 或 uvicorn main:socket_app --host 0.0.0.0 --port 7001
```

配置默认：`data/youtube_tool.db`、`data/downloads/`、Whisper `base`，模型目录在项目同级 `models/`；可用 `YT_TEST_DATA_DIR` 覆盖测试数据目录。CORS 正则只允许 localhost/127.0.0.1 任意端口，不能扩成任意来源。

## 清理与风险

存储清理默认 dry-run；应用删除需要脚本的 `--apply --confirm DELETE`。staged 默认 72 小时、orphan 14 天、export/media 7 天、quarantine 30 天，具体阈值可由环境变量覆盖。不要把数据库、下载媒体、日志、模型或 API key 写入文档/Git。

## 当前状态

- 已实现：下载/导入、字幕/Whisper、任务并发、Socket.IO 进度、摘要/翻译、导出、token 用量和受保护的存储清理。
- 运行数据默认落在 `backend/data`；服务重启不会继续执行内存中的 Python 任务，但会处理启动时的中断记录。
- 治理基础设施（2026-08-09 bootstrap）：`.trellis/`（scripts/config/workflow/spec/tasks 已入库）+ CodeGraph 索引已 init（不入库）+ `.agents/skills/`（9 个 trellis skill 入库）。
- 批量 URL 已修（2026-08-09，task 08-09-fix-batch-url-dedup 已归档）：`batch_submit` 读取 `force_new`（默认 True，与单 URL 路径对齐），裸 BV/短链经 `sanitize_input_url` 补全。
- 历史搜索已修（2026-08-09，task 08-09-fix-history-search 已归档）：`get_jobs`/`list_jobs` 加 `keyword` 参数（`title`/`input_url` LIKE，`%`/`_` 转义），前端搜索框从转录全文搜索改接列表过滤。
- AI 校正展示与有效性已修（2026-08-09，task 08-09-fix-ai-correct-display-and-validity 已归档）：`correct_transcript` 加 4 项加固（标记完整性校验/长度异常检测/显式失败/边界分块），返回 4 元组含 stats；`correction_stats` 持久化到 jobs 表（新列）；`ResultView` 新增「转录文本」tab（原文/校正切换+校正统计告警），`do_summary=false` 时默认落转录 tab；`HomeView` 加校正模型选择器；backend pytest 370 全绿、frontend vitest 16 全绿。
