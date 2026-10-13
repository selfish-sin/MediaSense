# Backend Spec（FastAPI + Socket.IO + SQLite）

## Pre-Development Checklist

改任何 backend 代码前确认：

- [ ] 读过 `CONTEXT.md` 文件索引表，定位到目标模块
- [ ] 跑过 `codegraph explore "<符号>"`（索引不可用时在 task 工件记录原因）
- [ ] 涉及任务编排时读过 `main.py` 的 `process_job`（信号量并发由 `config.MAX_CONCURRENT_JOBS` 提供，**默认 2**；设 `MAX_CONCURRENT_JOBS=1` 才是严格串行；preview 阶段释放许可的设计勿破坏）
- [ ] 涉及存储删除时读过 `scripts/cleanup_storage.py`（dry-run + `--apply --confirm DELETE` 双门槛不可绕过）
- [ ] 涉及 AI 调用/重试/缓存时读过 [ai-call-cache.md](ai-call-cache.md)（忽略缓存仍写回、流式断连守卫、缓存故障降级）
- [ ] 涉及转录缓存时同读 [ai-call-cache.md](ai-call-cache.md) 的 ASR v2 身份/整体降级契约
- [ ] 涉及 verify、依赖、启动或 CI 时读过 [quality-tooling.md](quality-tooling.md)

## 核心约定

### 异步纪律

- 同步阻塞调用（yt-dlp / faster-whisper / ffmpeg / paddle）**必须**包进 `asyncio.to_thread`，禁止在 async 路径直接调用——违反会卡死整个事件循环（Socket.IO 心跳、其他任务进度全停）。
- faster-whisper 是惰性编码：`model.transcribe()` 返回生成器，CUDA 运行时缺失（cublas64_12.dll）在**迭代期**才抛出。物化 `list(segs)` 必须留在 try 块内才能触发 GPU→CPU 降级（教训来源：任务 f40508bd 失败）。

### B站 API 风控（2026-08-26 加固，勿回退）

- 进程级锁 + 最小间隔 ≥1s（`_throttle_bili_api`，`BILI_API_MIN_INTERVAL` 可调）
- `_fetch_subtitles_via_player_v2` 先走旧版 `x/player/v2`（带 dm_img_str），wbi 降级备用，412 时强制刷新 wbi 密钥重试一次
- 完整 Chrome UA + 进程级缓存 buvid3/buvid4 + 真实视频页 Referer（`_bili_headers`）
- OpenAI 兼容流式接口（阿里云 MaaS）末尾会发 `choices: []` 的 usage chunk——取值必须用 `(chunk.get('choices') or [])` 防护，`[0]` 直接索引会越界（dict.get 默认值只在 key 缺失时生效）

### B站字幕重试语义（2026-09-21 定稿，两类失败勿混用）

- **可重试**：下载失败 / HTTP 错误 / 链接过期（换新 auth_key 才有意义）、疑似"风控降级"
  （body 只剩零星几条，`_SUBTITLE_DEGRADED_MAX_COUNT`=5 以内——稍后同视频能拿到完整内容）。
- **不可重试**：`_validate_subtitle_body` 硬校验不过且**条目不少**（条目够多但覆盖不足 =
  B站该视频字幕本身生成不全，实测 `BV17V4y1S7i1` 的 ai-zh 只有 477s/3101s）→ 直接放弃该轨
  换下一条候选。旧逻辑把这类失败也拿去"换链接重试"9 次（含 412 冷却 20s）→ 白等数分钟仍失败。
  修复后同一视频字幕阶段从数分钟降到 **3 秒**（21:43:30 提交 → 21:43:33 判完转下载）。

### 联网检索（2026-09-16 修复，勿回退）

- **单一事实源**：`services/web_search.py` 是唯一联网实现（追问 + 深度研究共用）。禁止在
  `research_processor.py` / 路由层再内联第二份智谱或 DDGS 调用（曾因两份实现漂移，
  追问链路用的是只读环境变量的空 Key，整条链路静默失效）。
- **智谱姿势**：必须走独立端点 `POST /api/paas/v4/web_search`（body `search_query`（≤70 字符）/
  `search_engine` / `search_intent` / `count` / `content_size`，返回 `search_result[]`）。
  **不要**用 `chat/completions + tools=[{type:web_search}]` 老姿势——已被官方独立端点取代，
  且其 `message.web_search` 是**数组**，按 dict 取值会抛 `AttributeError` 并被 except 吞掉。
- **Key 三级兜底**：`resolve_zhipu_key()`（环境变量 → config.py → DB 里 endpoint 含 bigmodel/zhipu 的模型）。
  需要智谱 Key 时统一调它，不要直接读 `config.ZHIPU_API_KEY`。
- **DDGS 兜底**：`ddgs`/`primp` **不读** `HTTP_PROXY`/`HTTPS_PROXY`，必须经 `resolve_search_proxy()`
  显式传 `proxy=`，否则国内网络恒超时（实测 2~26s 波动，整体预算 25s）。
- **失败必须可见**：`web_search_collect()` 返回 `SearchOutcome`（`sources`/`text`/`provider`/`error`），
  绝不静默返回空；追问经 SSE `search` 事件透出 `searching|ok|failed`，前端气泡展示状态。

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

### 任务编排（2026-08-27 拆分，2026-09-21 串行队列）

- 并发上限来自 `config.MAX_CONCURRENT_JOBS`（**默认 2**——串行 1 实测太慢：一个任务卡在字幕阶段时
  后面全部干等，用户明确否决；设 `MAX_CONCURRENT_JOBS=1` 才是严格串行）。
  超过上限的任务停在 `pending`，由信号量排队——这就是「单任务队列」，**不要另造内存队列/调度器**。
- ★ **running/pending 不能只看 DB `status`**：字幕/下载阶段任务 status 仍停在 `pending`，
  只看 status 会把正在跑的任务显示成「排队中」；必须叠加内存标记
  `RUNNING_JOBS[jid]['status'] == 'processing'`（由 `_process_job_impl` 在拿到信号量后写入）。
- `process_job` 保持"信号量包装 + finally 单一收尾出口"结构；输入获取已拆为策略函数
  `_handle_text_input`/`_handle_subtitle_file`/`_handle_media_file`/`_handle_url_input`（统一返回
  `(srt_path, txt_path, seg_count, title, platform, files)`），新输入类型加策略函数，不要往
  `_process_job_impl` 回填大段 if/else
- 批量任务终态推送走 `_emit_batch_progress`（`process_job` finally 调用，负载复用
  `get_batch_summary` 单一事实源）；新增批量面板数据字段时同步改 summary 而不是另造事件
- **队列查询**：`GET /api/jobs/queue`（`routers/jobs.py`，注册在 `GET /{job_id}` 之前，否则被单段路径捕获）
  是队列唯一数据源，负载由 `db.job_repo.get_queue_snapshot()` 提供（running 4 态 + pending，
  排序 `created_at ASC, rowid ASC`——`CURRENT_TIMESTAMP` 只有秒级精度，同秒提交必须靠 rowid 保 FIFO）。
  **不新增 socket 事件**：队列是任务状态的投影，前端用既有 `progress`/`log` 事件触发 debounce 刷新 + 断线 10s 轮询。

### 破坏性端点的本机限制（2026-10-13 定稿，勿绕过）

- 本服务**无认证**且 `SERVER_HOST=0.0.0.0`，API 在局域网可达；CORS 白名单只挡浏览器
  的跨域读取，挡不住同网段其它主机的 curl/脚本。
- **所有无认证的破坏性端点**必须先调 `routers.jobs.require_local_client(request)`：
  清空任务（`/clear`、`/clear/{status}`）、批量删除（`/batch-delete`）、
  删除单任务（`DELETE /{job_id}`）、删除模型（`DELETE /api/models/{key}`）。
  口径与 `storage_cleanup._require_local` 一致（localhost 或 loopback IP 放行）。
- **覆盖范围（2026-10-13 第二轮补齐，此前只覆盖了前四项）**：另加
  `POST /api/jobs/{id}/retry?force=true`、ASR provider 删除、ASR 本地模型删除
  （`/local/{engine}` 与 `/local/whisper/{size}`）、模板删除、提示词覆盖重置
  （`/summary/overrides` 与 `/summary/overrides/{dim}/{mod}`）。
- **判定标准＝「操作是否不可逆销毁用户已有资产」，不是「是不是 DELETE」**。
  据此**刻意不覆盖**：`/{job_id}/cancel`、`/batch-cancel`（只停止工作，可再次重试；
  收紧会打断手机端远程取消）、`retry?force=false`（断点续传不删任何文件）。
  **`retry` 的限制只挂在 `force=true` 分支上**——这是有意差异不是遗漏，改一边必须同步另一边。
- **不要改绑回 `127.0.0.1`**：手机/平板访问是既有能力，改绑等于砍功能。加端点级限制
  既堵住局域网横向调用，又不动正常访问路径。
- 新增破坏性端点时同样要加；测试里的 `TestClient` 必须显式传
  `client=('127.0.0.1', 50000)`（默认 host 是 `'testclient'`，本来就不该被放行）。
- **守卫必须挂在任何 DB/文件操作之前**（先删了再报错＝数据已丢）；`test_routers.py` 的
  `TestDestructiveEndpointsRequireLocalhost` 用 `mock.assert_not_called()` 钉住这一点。
- ⚠️ **该测试的 `app` fixture 必须注册 `asr_admin_router`**：漏注册会让 `/api/asr/local/*`
  等端点返回 404 而**不是** 403，测试变成假绿。fixture 要与 `main.py` 的真实装配保持一致。

### 总结版本写入（2026-10-13，勿再用 INSERT OR REPLACE 写新版本）

- **写新总结版本一律走 `db.job_repo.append_summary_version`**：版本号在
  `BEGIN IMMEDIATE` 事务内由 `MAX(version)+1` 推导并写入，返回实际版本号。
  三个必须解决的问题：①调用方推导/硬编码的版本号会**覆盖已归档版本**
  （历史上 `main.py` 硬编码 `version=1`，任务重试成功后用 RETRY-RUN 覆盖用户归档的
  v1，并把 `summary_version` 打回 1）；②`jobs.summary_version` 与历史表可能**脱节**
  （重新总结失败/取消只改状态不写历史），拿它 `+1` 推出的版本号同样会撞上已有版本；
  ③并发「重新总结」两个请求读到同一指针 → 算出同一个 `new_version` 互相覆盖。
- `save_summary_version`（显式传版本号的 `INSERT OR REPLACE`）**只保留给
  「幂等刷新同一版本」用**（如重新总结前把当前正文存档到它已有的版本号上）。
  调用前先看历史表：指针命中已有行才刷新；指针是孤儿（历史非空但无此版）就改用追加。

### 派生文件名（2026-10-13）

- 同目录派生新文件名一律走 `services.naming.derived_sibling(path, stem_suffix=, suffix=, extension=)`
  （`extension` 不传则保留原扩展名）。
- **禁止 `path.replace('.txt', ...)`**：`str.replace` 是全串替换，而媒体文件名来自
  yt-dlp 的 `%(title)s`（见 `downloader.output_template`），标题含 `.txt`
  （如「Python.txt 教程」）时每一处都会被替换，产出错乱文件名，长了还会撞
  Windows ENAMETOOLONG 让整个任务失败。

### 批量端点上限（2026-10-13）

- 任何按 `job_ids` 列表工作的端点都要先调 `routers.jobs.assert_batch_size`
  （上限 `MAX_BATCH_JOB_IDS`）。`/api/export/batch` 与 `/batch-disk-info` 都会
  **逐条把完整 result_text 读进内存**（前者）或逐条递归统计磁盘（后者），
  全在一次请求内同步完成——无上限时是一次长时间阻塞的请求，内存峰值随选中数量线性增长。
- 超限要**显式报错并给分批建议**，不做静默截断。
- ★ **上限必须与前端 `HistoryView.SELECT_ALL_CAP` 对齐**（当前两侧都是 2000，
  `test_routers.py::test_limit_constant_is_sane` 直接读前端源码断言两侧相等）。
  前端「全选当前筛选」刻意以 2000 为安全上限并承诺「可跨页批量操作」，后端若取更小的
  值，用户全选 500 条后点批量删除就会被凭空多出来的限制挡住。改任一侧必须同步改另一侧。
  日后真成为性能问题，正确方向是服务端流式出 ZIP，而不是压低上限。

### AI 失败契约（2026-10-13 补充）

- **分批总结必须 fail-fast**：`ai_process` 的分批模式里任一批返回失败哨兵即**抛异常**，
  不产出「正文中间夹一段 `[处理失败: ...]`」的残缺文档。原因是合并时会加
  `# 分批处理结果` 标题前缀，调用方（`main.py` / `routers/jobs_ai.py`）的
  `result_text.lstrip().startswith('[处理失败')` 防线在分批模式下**永远不成立**——
  任务会被标 completed、残缺文档写进 `jobs.result_text` 与 `summary_history`。
  抛异常后可由现有 try/except 路由到 `finish_job(False)`，调用方无需改动。
  抛点必须在 `_record_token_usage` 与 `emit_ai_token(done=True)` 之前。
- **`generate_tags` 返回值是三态**：`list`=成功有标签；`[]`=调用成功但解析不出；
  `None`=调用失败（兜底环内全部候选失败）。调用方必须按 `is None` 判定失败并
  保留原有标签——把失败当 `[]` 写库会**清空用户已有标签**，且失败伪装成成功。
- **流式失败路径必须返回真实 token**：`_call_ai` 的 `usage_state` 出参在流读取过程中
  就累计 usage，失败分支（超时/断连/HTTP/兜底全挂）返回真实值而不是 `0, 0`，
  否则 `token_usage` 表系统性少记，真实账单高于系统显示。
- **token 记账按实际使用的模型 key**（兜底环换过模型时），与 `generate_mindmap`
  的 `_on_ok` 一致。
- **`max_tokens` 等可空请求字段用真值守卫**（`if max_tokens: payload[...] = ...`），
  不要用 `model_cfg.get(k, 默认值)`：默认值只在键**缺失**时生效，键存在值为 NULL 时
  会把 `null` 发给 OpenAI 兼容网关被 400 拒绝。模型表该列允许 NULL（生产库普遍为 NULL）。
- 取消检查要有**时间维度**：`CANCEL_POLL_INTERVAL=1.0`——只在「每 20 个 token」时查，
  端点连上但不吐字（半死连接）时全程不查取消，用户点了取消最多 8 分钟无响应。

### 文件命名（2026-09-21 统一）

- 导出/下载的文件名**只能**走 `services/naming.py`（`safe_basename` / `export_basename`），
  禁止再写第二份清洗逻辑（旧 `routers/export.py._sanitize_title` 会把《》、，、- 等标点全删掉、
  硬截断 50 字符，长标题变成不可读的粘连文字，是用户明确投诉点）。
- 规则：只剔除 Windows 非法字符（`\ / : * ? " < > |`）与控制字符，**保留中文标点与空格**；
  兜底链 `title` → 平台视频 ID（BV / YouTube id）→ `job_{id}`，不允许出现无信息量名字。
- **磁盘名与下载名解耦**：磁盘落盘名附唯一后缀（job_id 短码/时间戳）防互相覆盖，
  `FileResponse(filename=...)` 用可读名。磁盘名不进 `FileResponse`。

### 提示词维度与提示词正文（2026-10-11 定稿）

`services/prompt_builder.py` 是 9 维模块化提示词的单一实现（角色/内容类型/认知模式/分析立场/深度/
输出形态/语气/字数/口误纠错）。**提示词正文（各模块的 `template`/`name`/`desc`）与
`build_modular_prompt` 的拼装段序属用户资产**——"整理维度"这类需求只动**档位与展示层**，
不得改写模板文字、不得合并模块、不得调整段序；确需改动必须先拿到用户明确要求
（2026-10-11 有过一次擅自改写被整批回退的先例）。

- **`DIMENSION_ORDER` 只管展示顺序**（决定 `/api/summary/dimensions` 返回顺序，前端
  `utils/recipes.js` 的 `DIMENSION_KEYS` 必须与之一致），**不决定拼装段序**；
  `_DIMENSION_DEFS` 是物理定义，导出时按 ORDER 重排为 `PROMPT_DIMENSIONS`。
- **输出语言不是维度**：固定简体中文，纪律由 `_output_language_discipline()`（复用
  `prompts.LANGUAGE_DESCS["简体中文"]`，避免两份文案）拼在字数之后；历史任务的 `language`
  键被忽略（有测试锁定）。
- **不要顺手删"0 次使用"的档位**：实测使用次数只作参考，删档位/裁配方属需求变更，必须单独确认，
  并同步 `test_prompt_builder.py` 的档位锁与 `TestPromptTextRestored`（13 个档位 + depth 原字数
  文案 + 摘要不与要点列表合并）。
- **改名要连正文标题一起改**：维度展示名与模块模板里的段落标题必须一致（2026-10-11「输出格式→
  输出形态」曾在正文留下不一致，后经用户授权同步了 output 的 9 个模块标题；回归锁
  `test_output_section_headers_match_dimension_name` 锁定此一致性）。
- **改维度要同步 4 处**：`_DIMENSION_DEFS`/`DIMENSION_ORDER`、前端 `DIMENSION_KEYS`、
  `settingsStore.selectedDimensions`、`build_modular_prompt`（段序，非明确需求不动）。
- **新增"授权类"档位时必须逐条核对同配方的相反纪律**：`reality`「现实研判」授权引入 `[延伸]` 外部对照，
  而同配方的 `rules=standard`「标准提炼」首条是"不添加视频中未提及的信息"——两条相反指令同时进 prompt，
  模型行为不可预期。落盘时改用不含该禁令的 `rules=deep`。**加新档位/新授权前，先通读同配方其他档位的正文。**
- **`prompts.py` 的 `TONE_DESCS`/`WORD_COUNT_DESCS`/`LANGUAGE_DESCS` 是旧路径（`build_prompt_vars`）
  与历史任务兜底共用常量**：删维度档位时不要连带删常量，否则历史任务重跑落到兜底文案。
- **校验"提示词是否被改动"的方法**：不要把两份中文交给 diff 肉眼看（PowerShell 会把 UTF-8 按
  Latin-1 解码成乱码，`åæç«åº`），要用 `git show <base>:<file>` 落字节文件 + 脚本逐组合比对
  `build_modular_prompt` 的输出。
- **跨维度对撞靠「主从句」调和，禁止靠删纪律/合并模块解决**（2026-10-11 第二批调和固化）：
  role/mode/rules 是三个正交作用点（人设/认知框架/边界纪律），同议题在多层出现时，在较弱一层
  写"以所选认知框架为准"——rules=deep 的「框架标签优先、不另加标签」、standard 的「框架授权
  补充除外」、chapters 评价段的「框架不允许外部补充时只指缺口、依据分类领域中性」是范例；
  形态层不得写死单一领域（中医临床）的依据分类，字数档不得与 depth=quick 对撞（500 字档是
  区间不是硬下限）。新增特化框架/档位时必须通读同配方其他档位正文并加「装配后无对撞」断言，
  回归锁 `TestCrossDimensionTuning`；输出形态缺口以纯增量档位补（dialogue/steps），
  改配方指向即可，旧档位保留给自由搭配与历史任务。

## Quality Check

- [ ] 仓库根 `backend/venv/Scripts/python.exe verify.py backend` 全绿（ruff + pytest；2026-10-07 基线 979；必须用已安装开发锁的环境）
- [ ] 新增/修改的 service 函数有对应测试（成功 + 主要错误路径）
- [ ] 没有在 async 函数中引入新的同步阻塞调用
