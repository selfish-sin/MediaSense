# youtube_tool_v2 CONTEXT

> 最近核对：2026-09-12 | FastAPI + Socket.IO | 默认端口：7001

## 一分钟速览

本地视频/音频处理工作站：下载或导入媒体，字幕优先，缺少字幕时回退 Whisper 转录，再做分段、摘要、翻译和导出。后端把 FastAPI 与 Socket.IO 组合为 ASGI 应用，任务并发上限为 2，SQLite 记录任务和 token 用量。

## 技术栈

- 后端：FastAPI + Socket.IO（ASGI 组合）、SQLite（job/token/model 持久化）、yt-dlp（下载）、faster-whisper（字幕缺失时转录）、python-socketio。
- 前端：Vue 3 + Element Plus + Pinia + Vue Router + Vite（构建产物输出到 `backend/static/`，同源 SPA）。
- 模型：Whisper `base`（本地，目录在项目同级 `models/`）；本地 FireRedASR2-AED（sherpa-onnx int8，模型 ~1.5GB 同级 `models/` 下，缺失自动下载）；摘要/翻译走可配置的外部 LLM（`backend/config.py` 配 API Key，不入库）。
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
| `backend/main.py` | FastAPI、Socket.IO、`process_job`（策略函数拆分：`_handle_text_input`/`_handle_subtitle_file`/`_handle_media_file`/`_handle_url_input`）、`_emit_batch_progress`、启动恢复和 SPA 路由 | 改任务编排、输入处理策略或批量进度推送 |
| `backend/config.py` | 数据目录、7001、Whisper 模型和清理 TTL | 改路径、端口或默认值 |
| `backend/db/` | SQLite 初始化、job、模型、token 用量仓储 | 改持久化或任务状态 |
| `backend/services/downloader.py` | yt-dlp 下载/媒体准备 | 改 URL 下载和代理 |
| `backend/services/transcriber.py` | 字幕读取、Whisper 转录、语言/模型/设备（auto/cuda/cpu）、`_transcribe_sync` 引擎路由单点（回退链 cloud→firered→local，firered 仅模型就绪时参与且不触发下载，取消不回退） | 改转录回退、引擎路由或设备选择 |
| `backend/services/cloud_asr.py` | 云端 SenseVoice 转录（静音切分→并发分片→句级化写盘；单片失败本地补录不留空洞；结束打延迟画像日志）；`split_sentences`/`distribute_times` 被本地引擎复用 | 改云端引擎、句级切分或补录策略 |
| `backend/services/local_asr.py` | 本地 FireRedASR2-AED（sherpa-onnx int8/CPU）：模型按需下载→VAD 分段（+padding）→段间并行解码→标点恢复→句级写盘 | 改本地 FireRed 引擎、模型下载或 VAD/标点参数 |
| `.trellis/spec/backend/transcription-chain.md` | **识别链全链路权威文档**：输入四策略/字幕优先防错位/引擎回退链/三引擎内部/分片补录/横评结论/参数速查/坑点汇总 | 改转录链任何环节前必读 |
| `backend/services/` | 分段、摘要、翻译、导出、搜索等处理 | 改具体媒体流程 |
| `backend/routers/jobs.py` | 任务核心域：上传/提交/列表/删除/清理/取消/重试/编辑结果 + 共享的 `normalize_url`/`extract_urls` | 改任务生命周期 API |
| `backend/routers/jobs_ai.py` | AI 域：重新总结/总结历史/标签/思维导图 | 改 AI 重跑相关端点 |
| `backend/routers/jobs_batch.py` | 批量域：批量提交/批量状态/转录全文搜索 | 改批量提交或跨任务搜索 |
| `backend/routers/` | 其余 API 路由（models/summary/export/translate/bilibili/download/templates/chat/search/storage cleanup；export 含单任务 4 格式与 `/export/batch` 批量 ZIP） | 加/改其他 HTTP API |
| `backend/socket_handlers.py` | 实时日志和进度事件 | 改前端实时反馈 |
| `backend/scripts/cleanup_storage.py` | staged/orphan/export/media 清理 | 改清理策略；删除必须显式确认 |
| `backend/verify.py` | 一键验证：backend pytest + frontend vitest + vite build | 改完代码做全量验证，或想快速回归 |
| `frontend/src/utils/` | api 封装（axios 拦截器/错误归一化）、jobActions、modelDisplay（引擎-模型显示名）、clipboard/recipes/errorNotice 等工具 | 改 HTTP 调用或共享工具行为 |
| `frontend/src/views/HomeView.vue` | 核心工作台视图（业务逻辑已拆到 composables） | 改表单/提交/结果展示 |
| `frontend/src/composables/` | `useBackendLogs`（日志增量拉取+全量回退）、`useBatchProgress`（Socket 推送+断线降级轮询）、`useJobRestore`（路由参数回填） | 改日志面板、批量进度或任务恢复逻辑 |
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
- 模型默认/失败重试/新增模板优化（2026-08-24，task 08-24-model-default-resume-template）：`app_settings` 键值表存默认模型（`db/model_repo.py` 的 `get_default_model_key`/`set_default_model`），`GET /api/models` 的 `default` 字段来自 DB（回退 config），新增 `POST /api/models/default`；运行时模型回退点（submit/resummarize/batch/translate/create_job/process_job）统一走 DB 默认；`POST /api/jobs/{id}/summarize` 放宽到 failed（保留转录文本即可，仅重跑 AI 步骤）并补 AI 哨兵串检查；修复流式 chunk `choices=[]` 越界（`services/ai_processor.py`，阿里云 MaaS usage chunk 引发）；模型管理页加「设为默认」/默认标签、6 个服务商快捷模板、拉列表一键添加、端点自动补全。
- 总结失败根因记录：OpenAI 兼容流式接口（阿里云 MaaS）末尾发 `choices: []` 的 usage chunk，`chunk.get('choices', [{}])[0]` 会越界（dict.get 只在 key 缺失时给默认值）；已在 `ai_processor.py` 与 `routers/models.py` 测试接口统一改为 `(chunk.get('choices') or [])` 防护。
- 重新总结交互重构（2026-08-24，08-24 任务后续）：删除 `components/ResummarizeDialog.vue` 弹出页（70% 宽、完整 7 维选择器、形同视频处理主页），新增 `components/ResummarizeControl.vue` 内联控件（模型下拉 + 重新总结按钮），HomeView 结果栏与 ResultView 工具栏/失败横幅统一接入。「重新总结」= 相同提示词（透传任务原始 dimensions/options）+ 换模型，仅重跑 AI 步骤；「到工作台重做」= `?rerun=jobId` 跳工作台回填输入与原始提示词，可改提示词后完整重跑（已有能力，未改）。
- 清理工作台实测正常（2026-08-24）：dry-run 预览出 41 项/约 24MB，候选表/计划有效期/操作日志/审计 tab 正常，「确认并执行」需输入 DELETE 的二次确认门槛生效。
- B站字幕防错位加固（2026-08-26，未建 task）：B站 AI 字幕在换源/重传后可能返回旧内容（历史数据），此前被无条件信任并跳过本地转录，导致分段/总结基于错误文本。新增两道防线：①字幕时长校验（body 时间跨度 vs 视频时长，明显不符即拒，转本地转录）；②内容抽查（默认开启，`subtitle_check=false` 可关：抽取音频开头 ~90s 用 Whisper CPU 转写，与字幕开头文本相似度 <0.3 判定错位，丢弃字幕转全量 Whisper，复用抽查时已下载的音频不二次下载）。顺带修复：中文字幕选取不再把 `ai-en` 等非中文 AI 字幕当中文；faster-whisper 分片转录在 GPU 运行时缺失（cublas64_12.dll）于生成器迭代期抛出时也能自动回落 CPU（此前只在 transcribe 调用期兜底，漏掉惰性编码段，曾致任务 f40508bd 失败）。
- B站 API 风控加固（2026-08-26，未建 task）：批量任务齐射触发 412（request was banned）后旧代码全部白等 yt-dlp auto-sub。六项修复，全在 `backend/services/downloader.py`/`transcriber.py`：①限流串行化：进程级锁 + 最小间隔 ≥1s（`BILI_API_MIN_INTERVAL` 环境变量可调，`_throttle_bili_api`）；②接口顺序反转：`_fetch_subtitles_via_player_v2` 先走实测更稳的旧版 `x/player/v2`（带 dm_img_str 等浏览器常参数），wbi/v2 降级备用，412 时强制刷新 wbi 签名密钥重试一次；③浏览器指纹：完整 Chrome UA（此前截断 UA 是风控高权重特征）+ 进程级缓存 buvid3/buvid4 + 真实视频页 Referer（`_bili_headers`），yt-dlp 兜底同样注入 buvid cookie 与完整 UA；④兜底提速：接口明确"无字幕"（api_ok=True 且空列表）时跳过 yt-dlp auto-sub 直转 Whisper，只有接口被风控/异常（api_ok=False）才走 yt-dlp；⑤412 文案可见化：拦截时提示"B站风控(412)，已自动回退"，不再只甩裸错误码；⑥Whisper 事前探测：`_cuda_runtime_usable()` 在模型加载点探测 cublas/cudnn DLL，缺失直接走 CPU。后端 pytest 398 全绿；真网实测 nav/view/player 接口在完整指纹下正常响应（含真实视频 ai-zh 字幕）。
- 质量与实时性重构（2026-08-27，task 08-27-quality-realtime-refactor 已归档）：①批量进度改 Socket.IO 推送——`process_job` finally 单一出口调 `_emit_batch_progress`（负载与 `GET /api/jobs/batch/{id}` 同构，单一事实源 `get_batch_summary`），前端 `useBatchProgress` 订阅更新，断线降级 10s 轮询；②前端日志增量修复——`delta > 200` 超界不再静默丢弃，回退 `loadAllHistory()` 全量重载，连续失败 ≥3 次才告警一次（成功重置）；③`_process_job_impl` Step 1 拆为 `_handle_text_input`/`_handle_subtitle_file`/`_handle_media_file`/`_handle_url_input`/`_extract_bvid` 策略函数，统一返回 `(srt_path, txt_path, seg_count, title, platform, files)`；④`routers/jobs.py` 按域拆为 core/`jobs_ai`/`jobs_batch` 三路由（对外路径零变化）；⑤HomeView 拆出 `useBackendLogs`/`useBatchProgress`/`useJobRestore` 三个 composable 并清理死代码。后端 pytest 402 全绿（须用 `backend/venv` 跑，含 pytest-asyncio）、前端 vitest 24 全绿。
- 测试环境坑：沙箱默认 Python 缺 pytest-asyncio，异步用例会以 "async def functions are not natively supported" 批量失败；跑后端测试必须用 `backend/venv/Scripts/python.exe -m pytest`。
- 质量与清理冲刺（2026-09-06，无 task，逐单元提交）：后端——Socket.IO CORS 改 callable 白名单（此前 allowed_origins 漏跨域来源）；chat 追问 SSE 与 socket 双发修复；翻译结果 AI 哨兵串校验；任务列表 LIKE 转义；`_extract_bvid` 失败不再静默吞错；取消传播 + `cancelled` 状态守护（防 canceled 被后续状态覆盖）；任务列表查询/响应瘦身；Whisper 进程级模型缓存单例（double-check lock）；搜索/logs/大文件读改 `to_thread` 防阻塞事件循环；存储清理 status 轮询瘦身、DB 连接 try/finally、bilibili cookie 落盘位置；死代码清理（converter 路由迁为 `cleanup_expired_files` 启动清理、ai_processor checkpoints、`emit_ai_tokens_batch`、`get_latest_summary_version`）。打包——PyInstaller spec 按 `_KEEP_CUDA` 环境变量裁剪 CUDA DLL、installer.iss 用 `MyAppVersion` define、`version.txt` 单一版本源。前端——HomeView -877 行死代码（搜索块/双语块/提示词预览/截图画廊）、删除 ConverterView/HelloWorld、移除 icons 依赖；`useMarkdownRender`（rAF 节流 + 列表缓存）统一流式渲染、markmap 动态导入；ChatPanel SSE AbortController 生命周期治理；jobStore 日志 1.5s trailing debounce 落盘 + `flushLogsNow`/`clearLogs`；Element Plus 按需加载（树摇）+ zhCn locale；api 拦截器统一 422 数组 detail 逐条拼接；工具去重（clipboard/recipes/errorNotice）。测试——共享 `db_path` fixture（真实 init_db 取代 4 份手抄 DDL）、socketio mock 自动恢复、MOCK_CFG 常量；前端补 api/jobStore 26 用例。质量门禁：`verify.py` 一键验证全绿（后端 pytest 397、前端 vitest 50、vite build），ruff 最小配置（line-length 120）。
- 冲刺复盘审查修复（2026-09-07，无 task，4 提交）：①系统日志面板增量失效——`/api/logs` 尾部回读改造时 `total` 硬编码 0，前端 `delta=total-lastTotal` 恒 0 致自动轮询永不显示（前端测试全 mock 非零 total 未拦截，教训：mock 必须覆盖空值/缺省契约形态）；契约重构为前端以旧窗口末行为锚点做尾部同步，后端不再返回 `total`；②`_read_log_tail` 仅在回读起点落在文件中间时丢不完整首行（文件 ≤256KB 时不再误丢完整首行）；③Whisper 模型缓存加 LRU 淘汰（上限 2，4 档尺寸 × GPU/CPU 降级双份会常驻多个大模型）；④哨兵拦截异常文案去掉脆弱的 `strip('[]')` 直接透传完整哨兵串；⑤删除 converter_router 墓碑注释。
- v1.1.0 已发布（2026-09-07，无 task）：GitHub Release `v1.1.0`（tag 指向 48036ab，v1.0.0 以来 78 commits），安装包 137.6MB CPU-only（CUDA DLL 裁剪，v1.0.0 为 629MB）。构建坑点：①`build_installer.py` 的 `run()` 曾因 cwd 参数冲突必崩（已修，`kw.setdefault`）；②Inno Setup 6.7.3 装在用户目录 `C:\Users\21886\AppData\Local\Programs\Inno Setup 6\`，不在脚本搜索路径，构建前需把它注入 PATH；③PyInstaller 装在 `backend/venv`（6.22.2），须用 venv python 运行 `build_installer.py`。
- 批量字幕三连修复（2026-09-08，无 task，2 提交 `ee446a4`/`a81a586`，pytest 401 全绿）：①字幕内容抽查采样语言跟随任务转录语言（默认中文）+ 中文简体提示词——此前采样用语言自动检测把清晰中文幻觉成英文（"什么是经方"→"What is the old?"），相似度恒≈0 致好字幕全被误杀、批量退化成整段 Whisper（实测 0.02→0.846，错字幕仍≈0 被丢）；②日志按天轮转（`backend.log` 只含当天、跨天归档 `backend.log.YYYY-MM-DD`、归档不自动删）+ 时间戳带日期——`/api/logs` 面板不再混入 7 月以来无日期的旧条目（"412 还在发生/日志时间不对"误判的根源，旧 412 实为 08-26 修复前旧日志）；③批量任务严格串行：`jobs_batch.batch_submit` 只 spawn 一个编排任务 `_run_batch_sequential` 逐个子任务 await 完成再启动下一个（"任务序列，一次只跑一个"），防批量齐射触发 B站 风控降级。
- 模型引擎命名 + 历史批量导出（2026-09-08，无 task，5 提交，pytest 413 全绿、vite build 通过）：①`model_configs` 加 `engine` 列（迁移 `ALTER TABLE ... DEFAULT ''`，旧行回填空串），`add_model`/`get_all_models`/schema/router 贯通；模型页「名称」列显示 `引擎-名称`（`frontend/src/utils/modelDisplay.js` 的 `modelDisplayName`），表单引擎用 el-select `allow-create`（常见引擎候选 + 任意自由输入，不加「自定义」特殊选项/默认值），6 个快捷模板自动带出引擎，HomeView/ResummarizeControl/ChatPanel 下拉统一同显示（存储/传输仍用 `key`）；②新端点 `POST /api/export/batch`（body `{job_ids:[]}`，`routers/export.py`）：逐任务 `get_job` 取 `result_text`（无结果/不存在自动跳过，全空 400），每任务一个 `.md`（`# 标题` + 全文），重名标题文件名加序号，ZIP 落 `EXPORT_DIR`（storage_cleanup 7 天 TTL 覆盖）；历史页批量操作栏加「批量导出结果」按钮（只导出 completed，按钮上显示可导出数量），前端 `exportApi.batch` 复用 blob 下载 helper（api.js 抽出通用 `_downloadBlob`）。
- 端点自动补全统一 /v1 + 星号密钥污染修复（2026-09-08，pytest 422 全绿）：①`_normalize_endpoint` 幂等补全为 `/v1/chat/completions`——手填一般不带 /v1（如 `api.deepseek.com/chat/completions`）保存自动加回，已含完整端点原样保留；前端端点输入框下方实时显示「保存为」预览（与后端同逻辑）。②星号污染根治：历史 bug 把脱敏串（`sk-****xxxx`）写回 DB 致 3 个模型 401——`update_model_api` 在 api_key 为空或含 `****` 时保留 DB 原 key（防回写）；`GET /api/models` 新增 `key_broken` 字段（脱敏前用原始值判断，合法 key 脱敏后不误标）；前端编辑弹窗不再预填/回传密钥，留空 = 不修改，`key_broken` 模型表格标「密钥无效」红标、编辑弹窗弹警告引导重新粘贴。已污染的 3 个 key（deepseek-v4-flash/longcat-2-0/sensenova-6-8-flash-lite）中间段丢失不可恢复，须手动重新粘贴。
- 模型端点跳过 SSL 证书校验（2026-09-08，pytest 427 全绿）：自签名证书/IP 直连的中转端点（如 `https://103.236.67.108/v1`）被 httpx 严格校验拒绝（CERTIFICATE_VERIFY_FAILED）。`model_configs` 加 `skip_ssl_verify` 列（INTEGER DEFAULT 0，迁移+建表双覆盖），schema/repo/router 贯通；`POST /api/models/fetch` 新增同查询参数（直连模式用传参、按 key 拉取用 DB 标记）；测试连接、AI 总结/翻译/打标签/思维导图（`ai_processor._client_verify`）与 chat 追问流（`routers/chat.py`）统一 `verify=not skip_ssl_verify`。前端模型表单加「跳过 SSL 证书校验」勾选，添加/编辑均透传。
- 自签名证书自动放行（2026-09-08，pytest 430 全绿）：`routers/models.py` 加 `_is_ssl_cert_error`/`_get_with_ssl_bypass`——fetch 与 test 首次严格校验遇 CERTIFICATE_VERIFY_FAILED 自动 verify=False 重试一次，成功即返回 `ssl_bypassed` 标记（前端收到后自动勾选「跳过SSL校验」，添加流程保存即入库）；按已存 key 拉取/测试成功时顺手 `update_model(key, skip_ssl_verify=True)` 自愈，后续 AI 调用无需用户任何操作。已实测该中转（new-api 类型）：跳过校验后链路通、`/v1/models` 路径正确，卡点为令牌有效性（服务端 "Invalid token"）。
- 裸域名端点获取模型修复（2026-09-08，pytest 432 全绿）：用户手填端点常不带路径（如 `https://xxx.top`），此前 fetch 直接 `replace('/chat/completions','/models')` 推导，路径缺失时去请求域名根目录拿到网站首页 HTML（200 非 JSON）。改为 fetch 前先 `_normalize_endpoint` 补全为 `/v1/chat/completions` 再推导 `/v1/models`（与保存时同逻辑，幂等）；响应为 HTML 时提示「端点返回的是网页而不是 API，请填写完整端点如 https://主机/v1/chat/completions」。实测两个 new-api 中转 `/v1/models` 均返回标准 JSON（4xx 报错含令牌原因）。
- 模型获取链路打通 + 选用回填（2026-09-09，pytest 439 全绿，5 提交）：①`main.py` 新增 `merge_no_proxy`/`apply_proxy_bypass_hosts`——本地 `config.py`（gitignored）的 `PROXY_BYPASS_HOSTS` 启动时合并进 `NO_PROXY` 环境变量，httpx 全客户端（fetch/测试/AI/chat）对该域名直连（场景：Clash 代理隧道对中转域名 ConnectError 空消息、直连正常；当前配置 redumbrellacloudlink.himscp.top）；②fetch 失败的空消息 ConnectError 给出可行动提示（代理放行/直连排查）；③前端获取结果列表每行加「选用」——点选（或点行）把模型ID回填表单（名称空缺自动带出），确认后「添加/保存」落库，「添加」仍为直接存新模型；④**fetch 支持第三种姿势 `key+endpoint`（编辑表单获取）：端点用表单值，api_key 留空回退 DB 原密钥**（编辑态不回填密钥，空密钥直发必 401）；前端编辑态获取自动带 key；⑤gpt-5-6-luna 中转已用有效令牌打通，model_id 修正为 gemini-3.1-pro（该令牌分组仅 gemini 系），测试连接 200。浏览器 E2E：添加/编辑两种表单获取→列表→选用回填→取消均通过。
- 转录设备自选（2026-09-09，task 09-09-whisper-device-selector 已归档，pytest 447 / vitest 51 / build 全绿）：工作台「语音转录」新增「转录设备」下拉（自动/CUDA/CPU，默认自动）。`options.whisper_device` 存于任务 options（提交/批量/重跑整包透传，无 schema 变更、无新端点），`transcriber._resolve_device` 单点解析贯穿单段/分片转录；显式 CUDA 本机不可用（无 GPU/缺 cublas/cudnn DLL）→ 进度回调可见告警并回退 CPU 保流程跑通；显式 CPU → 跳过 CUDA 探测；`WHISPER_FORCE_CPU` 环境变量仍最高优先级；B站字幕抽查 `transcribe_audio_sample` 固定 CPU 不受影响。新增 `_resolve_device` 9 个单测。
- 云端 ASR 句级切分 + 引擎三态（2026-09-11/12，task 09-11-asr-chain-quality-fix，pytest 545 / vitest 55 / build 全绿）：①云端 SRT 写盘前句级化（`split_sentences`/`distribute_times` 纯函数，18 单测）；②删 `auto` 死语义（cloud/local 两态）；③新增第三引擎 `firered`（本地 FireRedASR2-AED，sherpa-onnx int8 CPU，`services/local_asr.py`）：模型 ~1.5GB 缺失时 GitHub release 按需下载（手动放置 `models/` 兜底，路径常量在 config.example.py），silero VAD 分段（首尾 +0.15s padding 防吞字）→ 段间并行解码（threads=3 + 6 workers，RTF≈0.21，`FIRERED_NUM_THREADS`/`FIRERED_DECODE_WORKERS` 可调）→ 标点模型恢复 → 复用云端句级切分写盘；模块级锁串行化（峰值内存 ~1.7GB×并发 2 会翻倍）；失败回退 Whisper（取消不回退）；`/api/asr/status` 加 `firered_ready`；前端引擎下拉三选项。实测（BV1bH4y1r75q 前 10 分钟，报告在 task 目录 compare-report.md）：本地 168.8s（3.6x 实时）vs 云端 848.4s（0.71x），本地快 5 倍；质量本地更优（多 100 字、云端 8 处日文假名幻觉、本地 0）。
- FireRedASR 坑点（勿回退）：silero VAD 必须**分块喂** `VoiceActivityDetector.accept_waveform`（512 样本/块，一次喂整段只报尾部一段）；`vad.window_size`/`front` 是属性/方法易混；`VadModel.create(config)` 不能直接构造；Windows 下 `tempfile.mkstemp` 句柄须先 close 再 unlink；GitHub 下载 httpx 必须 `follow_redirects=True`，tar 解包 `filter='data'`。
