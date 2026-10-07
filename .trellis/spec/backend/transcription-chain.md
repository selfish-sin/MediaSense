# 识别链全链路权威文档（转录/识别引擎体系）

> 2026-09-12 定稿。汇聚 09-11/09-12 三个任务的全部实测结论，是转录链路的单一事实源；
> 改动链路任何环节前先读本文，改完必须同步本文（spec 规则）。
> 2026-09-15 更新：云端 provider 配置改「DB（asr_providers 表）优先 + config.py 兜底」、
> 槽位化 provider 解析、默认引擎用户设置优先（只降不升）、ASR 配置管理 API（§8）与坑点 11-15；
> 本地档改**引擎注册表**（`LOCAL_ENGINES`，本地端点按 `{engine}` 通用化、管理页本地段改引擎列表
> 且收费标识单点在后端）；LLM 解码类本地引擎（Fun-ASR-Nano / Qwen3-ASR）实机横评后**放弃**（§5.1）。
> 2026-09-15 补（付费档污染清理，task `09-15-asr-paid-default-cleanup`）：
> **付费档（槽位 `cloud`=MiMo）只能由用户显式选择触发**——自动路径（抽查）只挑免费槽位、
> `_provider_cfg`/默认引擎/默认 provider 一律去付费化、前端引擎占位值与 `engineTouched` 语义收紧（§2、§7 坑点 16）。
> 实测证据：`archive/2026-09/09-11-asr-chain-quality-fix/compare*`、`archive/2026-09/09-12-cloud-latency-routing/`、`archive/2026-09/09-12-asr-vendor-compare/`。

## 0. 一图总览

```
输入四策略（main.process_job → _handle_*_input）
├─ 文本输入  → 无识别链，直接进 AI 阶段
├─ 字幕文件  → 直接当转录产物（无引擎）
├─ 本地媒体  → 转录引擎路由（§2）──────────────┐
└─ URL 输入  → 字幕优先（§1）→ 拿到字幕？ ──是──→ SRT/TXT
                                  └─否→ 转录引擎路由（§2）┘
                                                  │
        SRT + TXT + seg_count（全引擎同契约）←────┘
                    ↓
        下游：分段 → 摘要/翻译/标签/导出（不感知引擎差异）
```

## 1. 字幕优先（仅 URL 输入，downloader.py + main._handle_url_input）

**B站字幕链路 = 单一自研路径 + 单一校验出口（2026-09-21 收敛，勿再引入第三方库到关键路径）**：

```
extract_bilibili_subtitles
├─① 视频信息 _fetch_video_info_http  (wbi/view → view，纯 httpx，共用指纹+限流)
├─② 轨列表   _fetch_subtitles_via_player_v2 (legacy player/v2 → wbi player/v2 → 匿名兜底)
├─③ 选轨     _pick_zh_subtitle (CC优先 · 简体优先 · 剔除 ai_status!=2)
├─④ 取体     _acquire_bilibili_subtitle (下载→覆盖校验→换新链接重试→候选轨回退)
└─⑤ 兜底     _extract_bilibili_subtitles_ytdlp (仅①/②全灭时；**必须吃同一套校验**)
```

- **不使用 `bilibili-api-python`**：它此前被放在①②两个关键位置，**一缺失/一报错就整条链路
  降级到 yt-dlp**（历史：`except ImportError → yt-dlp` 两处悬崖）。其 `get_info()` 等价于
  `x/web-interface/wbi/view`，`get_subtitle()` 等价于 `player/wbi/v2`，均为自研实现已覆盖，
  故移除；`requirements.txt` 里该包已无代码引用（清理见 §7 坑点 19）。
  **禁止**再把"某可选依赖是否安装"作为链路可用性的前置条件。
- 412 风控加固：串行限流 ≥1s / wbi 刷新 / 完整 Chrome UA+buvid / 明确无字幕才跳 yt-dlp；
  **登录态被 412 时匿名兜底重试一次**（实测带 SESSDATA 可能比匿名更严）；
  **412 后主动冷却（进程级冷却窗，连续触发升级为长冷却），不再立刻换接口重试**（见下）。
- 字幕选择单点 `downloader._pick_zh_subtitle`：
  **中文 CC（zh-* 无 ai- 前缀，创作者人工上传永不旧）优先 → 中文 AI（ai-zh）→ 任意 AI
  （外文视频兜底）**，同类内**简体优先**（zh/zh-Hans/zh-CN 排在 zh-TW/zh-Hant 之前），
  绝不把 ai-en/ai-jp 误当中文；**生成中（`ai_status` != 2）的 AI 字幕直接剔除**
  （body 尚未产出，选中只会拿到空/半成品）。拿到字幕**不直接信**，过两道防错位校验：
  ①时长校验（字幕时间跨度 vs 视频时长，明显不符即拒；见下"请求侧取链加固"）；
  ②内容抽查（默认开，`subtitle_check=false` 关）：**只下载开头 90s 片段**
  （`download_video(max_seconds=90)` → `--download-sections`，实测加固姿势 3.3s/6.9MB；
  片段放 `job_dir/_subtitle_check/` 子目录防断点续传误当完整音频复用；片段下载失败自动
  回退整段且日志可见），转写后与字幕头相似度 <0.3 判错位 → 弃字幕转全量转录。
  抽查转写**云端优先（只走免费档）**——provider 走 `cloud_asr.preferred_provider()` 单点
  （**只返回 `cloud_free` 槽位的可用 provider**；免费档不可用返回 None → 直接本地），
  `extract_sample` + `transcribe_file(provider=...)`；失败回退本地 `transcribe_audio_sample`
  （**固定 CPU Whisper base**，采样语言跟随任务转录语言 + 简中提示词防幻觉）。
  **抽查绝不消耗付费额度**：provider 必须显式传（`transcribe_file` 的缺省值已是"免费优先/无免费则报错"，
  不再是 mimo 付费档）。
  片段**不作转录输入与素材**：校验失败时转录由 `_acquire_audio` 重新下载完整音频。
- **请求侧取链加固（2026-09-21，纯请求侧、不依赖音频/抽查）**：字幕"给错/给残"在拿到 body
  之前就可自愈，全部收敛在 `_acquire_bilibili_subtitle`（单一出口，勿在别处再实现一遍）：
  - **候选轨依次尝试**（`_pick_zh_subtitle` 排序后取前 4 条）：首选轨不可用不再直接放弃整条字幕；
  - **换新链接重试** `_refresh_subtitle_urls`：`subtitle_url` 带时效 `auth_key`，过期即 403，
    重试前必须重新请求接口换链接（旧链接只会一直失败），优先用尚未试过的链接；
  - **时间覆盖自校验** `_validate_subtitle_body(body, video_duration, is_ai)`：只用"视频时长 +
    字幕时间轴"两个接口数据判断，**不跑 ASR**。AI 字幕覆盖率下限 `0.75×时长`
    （`BILI_SUBTITLE_MIN_COVERAGE` 可调），CC 保持宽松 `0.5×时长-30s`（创作者可只传片段）；
    超出 `时长+120s` 判非本视频字幕；
  - **"假残缺"判定** `_is_subtitle_body_incomplete`：风控降级会返回"轨道在、body 只有零星几条"
    （同一视频稍后复查能拿到完整内容），命中即重试；密度判断（<1 条/30s）**只对 AI 字幕生效**
    （CC 可以很稀疏，不误杀）；
  - 全部候选与重试都拿不到完整内容时返回 `success=False`（`reason=subtitle_incomplete`）→
    调用方走转录，**宁缺勿错**。
  - **递增退避（2026-09-21 对齐批量实战）**：`_subtitle_retry_delay(attempt)` 纯函数——首次不等待，
    随后 `5s + 3s×(n-2)` 线性递增、单次封顶 30s、叠加 ±25% 抖动；默认
    `_BILI_SUBTITLE_MAX_ATTEMPTS=9`（总窗口约 2 分钟）。**勿回退成固定短间隔**：风控是递增惩罚，
    固定间隔只会让惩罚越来越重（业界批量实测：递增退避后 128/128 全成功）。
    参数字段：`BILI_SUBTITLE_MAX_ATTEMPTS` / `BILI_SUBTITLE_RETRY_BASE` / `_RETRY_STEP` /
    `_RETRY_MAX` / `_RETRY_JITTER`；`_subtitle_retry_wait()` 单点可 patch。
  - **412 风控冷却（2026-09-21）**：412 不再"立刻换接口再敲一次"，而是记账并进入**进程级共享
    冷却窗**——`note_bili_risk_control()`（在 `_fetch_subtitles_via_player_v2._try` 内，
    HTTP 412 与 body `code=-412` 两种形态都记账）→ 连续 412 达 `BILI_RISK_STREAK_THRESHOLD`
    （默认 5）升级为长冷却（默认 120s，否则 20s）；`_throttle_bili_api` 前置
    `_wait_bili_risk_cooldown()`，所有 B站请求共享该窗口（批量任务因此整体减速而非齐射）。
    任一接口 `code==0` → `note_bili_api_success()` 清零 streak。冷却状态经 `bili_risk_snapshot()`
    透出给用户（"非故障，正在减速重试"）。
- **yt-dlp 兜底必须与主路径同标准（2026-09-21 补齐，此前是最大漏口）**：
  `_extract_bilibili_subtitles_ytdlp(url, output_dir, sio, job_id, video_duration=0.0)`
  此前**只判 `srt 大小≥50 字节`**，选轨靠 `max(mtime)`（多语言同时落盘时等于随机挑，可能挑到
  繁体/外语轨），且**完全绕开**主路径的覆盖校验——"覆盖不到视频一半的旧/残字幕"会被当成功结果
  直接送进分段/总结。现状：
  - 选轨用 `_rank_subtitle_file`（与 `_pick_zh_subtitle` 同一套规则：CC 优先 → 简中优先 → 新文件优先）；
  - 校验用 `_srt_to_entries` / `_parse_srt_time` 解析出 `[{from,to}]` 后喂给
    `_validate_subtitle_body` 与 `_is_subtitle_body_incomplete`（**与主路径同一套判据**）；
  - `video_duration` 由调用方传入（元数据已拿到时）；拿不到元数据（①全灭）时不传 → 只退化到
    非空判断，属已知边界。
  **教训**：同一不变量必须在所有出口生效。加校验时先列全"字幕从哪些出口产出"，再逐个对齐。
- **YouTube**：`extract_youtube_subtitles` 拿平台字幕，逻辑同上（无抽查）。
- **抖音（2026-10-05 接入，游客链路）**：`services/douyin.py` 单点——AI 字幕（cla_info.caption_infos→VTT→SRT，
  zh 优先选轨）游客态**实测常为空**，拿到时与B站同标准过 `verify_subtitle_against_audio` 内容抽查
  （90s 片段走 douyin 直链 ffmpeg 截取，不经 yt-dlp）；拿不到回退转录，宁缺勿错。音频获取走
  `_acquire_audio` 的 douyin 分支（play_addr 直链 httpx 流式下载 mp4，**yt-dlp 的 Douyin extractor
  需浏览器 cookie 且频繁 403，勿再尝试**）。签名 vendor 自 f2（`douyin_abogus.py`，SM3 用
  cryptography），坑点见 CONTEXT.md 当前状态区块。
- 其他平台：直接下载 → 引擎路由。
- **音频获取单点**：`main._acquire_audio`（断点续传复用 → 按 options 下载），成功返回的
  媒体一律登记 `files['audio'/'video']`（素材登记不变量，勿在别处散落登记）。

## 2. 引擎路由（单点：transcriber._transcribe_sync，勿散落）

```
选项流：前端 settingsStore.advancedOptions.transcribe_engine（cloud/cloud_free/firered/local 四态下拉）
  → submit 整包进 options → main.py 白名单校验（非法值归一 'cloud'）
  → transcriber.transcribe_audio（async 壳：进度队列桥接 + 线程池，无路由逻辑）
  → _transcribe_sync（回退链循环）
```

**provider 配置来源（2026-09-15 起，勿回退成 config 硬编码）**：云端 provider 落
`asr_providers` 表（模型管理页「ASR 模型 → 云端 ASR」可增删改启停），运行时经
`cloud_asr._provider_cfg(key)` 读取——**DB 优先、config.py 兜底**（表为空/未初始化时行为与改造前
完全一致，可平滑回滚）。`engine` 字段是回退链**槽位**（cloud / cloud_free），一个槽位可挂多个
provider，运行时取「该槽位 enabled 且 sort_order 最小」者：单点函数
`cloud_asr.provider_for_engine(engine)` → `asr_repo.get_provider_for_engine`。
改 provider 配置**无需重启服务**。旧 config.py 里已填的 endpoint/model/api_key 在首次
`init_db()` 时一次性迁入 DB（播种仅发生在表为空时）。

**默认引擎推荐（免费优先）**：`GET /api/asr/status` 的 `recommended_engine`（`routers/asr.py`
单点）——用户在模型管理页显式设置的 `asr_default_engine` 优先（可用性约束下**只降不升**：
设了免费档而它不可用 → 落 local，绝不自动转付费）；未设置则免费档已配置 → `cloud_free`；
否则付费档 → `cloud`；都不可用 → `local`（firered 不参与**自动**推荐，避免默认触发 1.1GB
下载；用户显式设为默认则尊重其选择）。前端 `settingsStore` 只消费推荐值（用户手选不覆盖），
不再自行降级。
**付费档只能显式选择（2026-09-15 立，勿放宽）**：槽位 `cloud`（MiMo，¥0.5/h）**只能由用户在
工作台「转录引擎」里显式选择**触发。所有"缺省/兜底/自动"入口都必须是免费档：

| 入口 | 行为 |
|---|---|
| `options['transcribe_engine']` 缺省/非法 | `main.resolve_transcribe_engine()`（单点）→ `recommended_engine()`（免费优先） |
| `transcriber._transcribe_sync` / `transcribe_audio` 的 `engine` 缺省/未知值 | 落 `cloud_free` 链（`_fallback_chain` 未命中 → `cloud_free` 链，**永不回付费**） |
| `cloud_asr.transcribe_file` / `transcribe_audio_cloud` 的 `provider` 缺省 | `preferred_provider()`（**只挑免费槽位**）；无免费档 → 抛 `CloudASRConfigError`，不静默付费 |
| 前端工作台引擎占位值 | `cloud_free`（不是付费 `cloud`）；`engineTouched` 标记用户手选，之后不再被推荐值覆盖 |

历史教训：抽查此前漏传 `provider` → 落到 `transcribe_file` 旧默认 `'mimo'`（付费档），
**每个 B站 任务静默消耗一次付费额度**，且不落在 jobs.options 里（查库查不出来）。

**引擎显示名**：`transcriber.engine_label()`（唯一事实源；日志/UI/文案勿再复制映射表）。

**回退链（递归降级，每级失败原因经 progress_callback 可见）：**

| 用户选择 | 实际回退链 |
|---|---|
| cloud（默认） | 云端 MiMo（付费主力）→ 云端 XingChen（免费档）→ firered（仅模型就绪时）→ Whisper |
| cloud_free | 云端 XingChen Ultra（免费档，简单视频）→ firered → Whisper |
| firered | FireRedASR → Whisper |
| local | Whisper（无回退） |

- **成本不变量**：付费档失败只往更便宜的方向降（MiMo→免费 XingChen→本地）；反向不成立——
  用户选免费档失败绝不转付费引擎（`test_cloud_free_never_falls_back_to_paid` 锁定）。

- 取消（`TranscriptionCancelledError`）在任何一环直接上抛，不回退。
- 链尾 Whisper 失败无路可退，上抛给 job。
- **firered 作兜底绝不触发模型下载**（下载只发生在用户显式选 firered 且模型缺失）。
- `local` 永远=Whisper，语义不可改（历史 job options 已存该值）。
- 引擎标签显示：`_ENGINE_LABELS`（云端 MiMo/云端 XingChen（免费）/本地 FireRedASR/本地 Whisper）。

## 3. 各引擎内部

### 3.1 云端双 provider（cloud_asr.py，2026-09-12 起共存）

公共管线：ffmpeg silencedetect（-35dB，静音≥0.5s，间隔<3s 不切，无静音 30s 强制等分）
→ 并发 `_concurrency()`（默认 8，env `ASR_CLOUD_CONCURRENCY`，钳制 1~16；实测 c16 更慢勿调高）
→ 单片 `transcribe_file(path, provider=...)`（timeout 180s，重试 3 次指数退避 ≤8s，
401/402/403/404 为 ConfigError 立即抛——402 是计费类错误（余额不足），重试永远不可能成功；
400 归重试——可能是单片内容问题，耗尽后跳过交本地补录）
→ 写盘前 `split_sentences`+`distribute_times` 句级化（纯本地后处理：句末标点切句、
短句累积≥4 字、超 40 字强制断、字数比例分配时长）。
- 失败语义：单片失败 → **分片级本地补录**（§4）；全部失败且补录无收获 → 抛 Unavailable 走回退链。
- 结束打**延迟画像日志**（带 provider 标签，min/中位/p90/max）——诊断"云端变慢"先看这行。

| provider | 引擎值 | API 姿势 | 计费 | 实测 |
|---|---|---|---|---|
| mimo（默认） | cloud | chat/completions + base64 input_audio（**禁带 text**） | ¥0.5/h | 10min 素材 9.4s，术语最准 |
| xingchen | cloud_free | OpenAI multipart /audio/transcriptions（同旧 SenseVoice 接口） | **限时免费** | 单片 1.7~4.7s 稳定无抖动，原生标点 |

- 旧 SenseVoiceSmall 已退役（同基建单片 46~83s 排队抖动，服务端问题无参数解）。
- provider 配置键：mimo=`ASR_CLOUD_*`；xingchen=`ASR_FREE_*`（config.example.py 有模板；
  现仅作 DB 缺行时的兜底与首次播种初始值）。
- 400 归重试不判配置错：可能是单片内容问题，重试耗尽 → 跳过 → 本地补录，不能拖垮整体。
- **响应解析按请求姿势分支**：`_extract_asr_text(api, payload)`——`multipart` 取 `{'text': ...}`，
  `chat` 取 `choices[0].message.content`（空数组/None 必须防越界）。**不要改回按 provider 名分支**，
  否则自定义 provider 会解析错响应取到空文本。

### 3.2 本地引擎注册表 + FireRedASR2-AED（local_asr.py，sherpa-onnx int8/CPU，不依赖 torch）

**注册表是本地引擎的单一事实源**（`local_asr.LOCAL_ENGINES`）：每个引擎一条声明
（key / 显示名 / 语言 / 说明 / 文件明细 `parts` / 可删路径 `removable` / 下载包 `archives` /
体积提示 / `fee`），**就绪判定、文件明细、磁盘占用、后台下载（含取消与进度）、删除、
引擎级互斥锁全部按 key 自动生效**——加引擎只改注册表 + 写一个 `transcribe_audio_<key>()`，
路由/前端/其它服务零改动。当前**只注册 `firered`**（Whisper 不在此表：它是独立模型族，
四档明细见 §3.3，链尾语义不可改）。

- 生命周期：`engine_ready/engine_status/list_engines_status/engine_busy` +
  `start_engine_download/cancel_model_download/remove_engine`；`_ensure_engine_ready(key)`
  在用户显式选择该引擎且模型缺失时按需下载（**兜底链绝不触发下载**）。
- 分发入口 `transcribe_audio_local(engine, ...)`：transcriber 只调这一个；未知 key 抛 KeyError。
- 实测横评（2026-09-15，见 §5 与 task `09-15-local-asr-engine-registry` 报告）：通义
  **Fun-ASR-Nano**（800M）与 **Qwen3-ASR**（ONNX）两条 LLM 解码路线**已放弃**——
  同一段 90s 中文素材上 RTF 0.5~1.0（FireRed 0.27，慢 2~4 倍）且出现成段重复循环幻觉、
  中英混出、原生标点不稳；调短 VAD 段上限、显式 `language=zh` 均未消除，故**不再引入
  LLM 自回归解码类本地引擎**。本地档 = FireRed（主力）+ Whisper（兜底）。

FireRedASR2-AED 内部管线：文件级校验（三件套+标点+VAD）→ 缺失则按需下载（GitHub release，
`follow_redirects=True`，tar `filter='data'`；手动放置 `models/` 兜底）→ **引擎级锁串行**
（峰值 1.7GB×并发 2 会翻倍）→ ffmpeg 16k 单声道 wav → silero VAD 分段（**必须 512/块喂
accept_waveform**，整段喂只报尾部；段首尾 +0.15s padding 防吞字）→ **段间并行解码**
（threads=3 + 6 workers，RTF 0.40→0.21，env `FIRERED_NUM_THREADS`/`FIRERED_DECODE_WORKERS`；
线程数抬高反而劣化）→ 标点模型恢复（主线程串行 0.01s/段）→ 复用云端口径句级化写盘。
- 进度：0~8 模型下载 / 8~9 加载+VAD / 10~99 识别 / 99~100 完成。
- 仅中/英；language 非 zh/en 仅记录。不支持说话人分离。
- 模型路径常量在 `config.example.py`（真机放 gitignored `config.py`），目录与 Whisper 同级 `models/`。

### 3.3 本地 Whisper（transcriber.py，末位兜底，永远保留）

`_resolve_device`（auto 探测 CUDA 缺 DLL 回退 CPU / 显式 cuda / 显式 cpu）→ ≤20min 单次
`_transcribe_sync_single`；>20min `_transcribe_chunked` 300s/片（模型 LRU 缓存上限 2，
`(path,device,compute_type)` 键）。CUDA 推理期缺 DLL 自动降级 CPU 重跑（faster-whisper
惰性编码，物化 `list(segs)` 必须留在 try 内）。tiny+B站自动升 base。

**分片检查点（2026-10-07 断点恢复）**：每片识别完成立即写
`{stem}_chunkNNN.transcript.json`（offset+segments dict），原片 WAV 才删；
失败/取消后重试逐片复用（进度条显示「♻ 复用已完成分片」），只重跑缺失片。
校验：offset 与本次切分差 >0.5s 或 JSON 残缺按未命中；`bypass_cache=true` 不读
（忽略缓存=全新识别语义优先）但照常写；合并 SRT/TXT 落盘后清理全部检查点
（按文件名前缀过滤，glob 会把标题里的 `[` 当字符类）。
`_split_audio_chunks` 复用上次遗留的有效 WAV 免重切——**必须 ffprobe 校验时长
与预期片长一致（±2s）才复用**（崩溃写一半的截断 WAV 同样 size>0，复用会让该片
推理必炸）。`bypass_cache` 参数沿
`transcribe_audio → _transcribe_sync → _transcribe_by_engine → _transcribe_chunked`
串接。segments 统一为 `{'start','end','text'}` dict（检查点可序列化 + 两路径同形态）。
**配套（main._acquire_audio）**：断点续传扫描跳过 `*_chunkNNN.wav`——遗留分片
mtime 比原始音频新，误捡 = 只转录 5 分钟且检查点恢复链失效。

### 3.4 备选未接入（已实测打通，报告见 09-12-asr-vendor-compare）

- **百炼 qwen-audio-3.0-asr-flash**：DashScope 原生协议
  `POST .../api/v1/services/aigc/multimodal-generation/generation` + 头 `X-DashScope-SSE: disable`，
  `parameters.format` 必填；`output.sentence` 带**词级时间戳**（未来真·句级时间轴升级路径）；
  c8 即 429 限流必须退避重试；¥0.79/h；10min 素材 33s。接入需新增 provider（api='dashscope'）。
- XingChen 系其他模型（V3.2 普通版/Diarize 说话人分离版）同为 SiliconFlow multipart 接口，
  接入只需改 `ASR_FREE_MODEL` 或加 provider 配置，零代码。

## 4. 分片级备用引擎补录（cloud_asr._rescue_failed_chunks，09-12 升级）

云端单片失败不再留空洞：失败片**在分片文件清理前**递归补录——
XingChen（`transcribe_file(provider='xingchen')`，**免费云端先行**：主 provider 被 429 限流时
换一家成功率最高；主 provider 就是 xingchen 时跳过此级，不拿同一家限流接口重试）
→ firered（`transcribe_chunk_text`，模型就绪才参与，不触发下载）→ Whisper base
（`transcribe_audio_sample` 固定 CPU，中文带简中提示词，与字幕抽查同路径）→ 都失败才留空
（日志可见）。补录文本按原时间轴并入结果并句级化；全失败但补录有收获则不再抛 Unavailable。
完成文案透出实际用到的引擎，如 `N 片由备用引擎（XingChen（免费））补录`；
部分失败告警**只列真正未恢复的区间**（09-12 前误列全部失败段，已修）。

## 5. 引擎横评结论（同素材 BV1bH4y1r75q 前 10 分钟，88 片 c8）

| | mimo-v2.5-asr | qwen-asr-flash | SenseVoice | FireRedASR |
|---|---|---|---|---|
| 总耗时 | **9.4s** | 33.0s | 848.4s | 168.8s |
| 幻觉 | 0 | 0 | **8 处假名** | 0 |
| 术语（内关/太冲/扎） | **全对** | 全对 | 全错 | 错一 |
| 单价 | **¥0.5/h** | ¥0.79/h | ~免费 | ¥0 |

**SenseVoice 已退役**（慢 90 倍+唯一幻觉）；mimo 为付费主力，XingChen Ultra（限时免费）为免费档；
FireRedASR 免费离线保留。XingChen 未参与 10min 全量横评（单片实测 1.7s 稳定、TTS 样本质量与 mimo 同档）。

### 5.1 本地 LLM 解码类引擎：实测后放弃（2026-09-15）

曾评估并接入两条 sherpa-onnx LLM 路线（同素材：齐泽克中配 90s 16k 单声道，CPU）：

| | 段数 | 转换耗时/RTF | 文本字数 | 质量 |
|---|---|---|---|---|
| FireRedASR2-AED int8 | 18 | **18s / 0.27** | 686 | 标点自然、无幻觉 |
| Fun-ASR-Nano-2512 int8（800M） | 68~130 | 36~82s / 0.5~1.0 | 3265~6444 | **成段重复循环幻觉**（"those are the ones that are…"）、中英混出、人名听成英文串 |

- 已试无效的修法：VAD 段上限 20s→12s（更碎片化、反而更慢）、显式 `language=zh`、
  `max_total_len/max_new_tokens` 放大、tokenizer 传目录（这条是**真 bug**，修了才跑起来）。
- **结论：本地档不再引入 LLM 自回归解码引擎**（CPU 上既慢又不可靠）；模型目录与相关代码已删除，
  只保留注册表基础设施（加引擎仍是"一条声明 + 一个解码函数"）。若将来换 GPU 重试，
  先按本表口径复测再决定。

### 5.2 中医术语素材横评（2026-09-15，免费档 vs 本地；付费档欠费未参与）

素材：BV1Av4y1A796 p41《中药学·柴胡》前 90s（术语密集）；基准 = B站字幕头 231 字。

| 引擎 | 耗时 | 字数 | 相似度 | 术语表现 |
|---|---|---|---|---|
| 云端 XingChen（免费，现默认） | **10.3s** | 203 | 0.837 | 辛凉解表药/伞形科 对；「狭叶柴胡」→夏叶柴胡（同音错） |
| 本地 FireRedASR2-AED | 44.2s | **255** | **0.932** | 术语几乎全对、文本最完整（比 B站字幕还准：字幕把「饮片」写成「影片」） |
| Whisper base（本地，链尾兜底默认档） | 20.0s | 223 | 0.713 | 柴胡→柴糊、辛凉解表药→新两节表药、伞形科→散心克植物 |
| Whisper medium（本地，已下载未默认使用） | 37.4s | 241 | 0.866 | 伞形科→陕西科植物、辛凉→心良节 |

结论：**质量最高的是本地 FireRed（免费离线，慢 4 倍）**，免费云端 XingChen 是速度/性价比最优；
Whisper **base 是链路里最弱一环**（`asr_whisper_model` 未设置时即是它）——术语密集素材不建议走 base。
付费 MiMo 未参与（账户欠费 402，不可用），故此表不构成"要不要充值"的证据。

## 6. 参数与环境变量速查

| 变量/常量 | 默认 | 说明 |
|---|---|---|
| `ASR_CLOUD_CONCURRENCY` | 8（cap 16） | 云端并发；实测调高更慢 |
| `ASR_CLOUD_TIMEOUT` | 180s | 单片单次超时 |
| `FIRERED_NUM_THREADS` / `FIRERED_DECODE_WORKERS` | 3 / 6 | 本地识别线程/并行度（扫参甜点勿动） |
| `BILI_API_MIN_INTERVAL` | 1s | B站接口限流间隔 |
| `BILI_RISK_COOLDOWN` | 20s | 单次 412 后的冷却窗 |
| `BILI_RISK_LONG_COOLDOWN` | 120s | 连续 412 达阈值后的长冷却窗 |
| `BILI_RISK_STREAK_THRESHOLD` | 5 | 连续 412 几次后升级为长冷却 |
| `BILI_SUBTITLE_MAX_ATTEMPTS` | 9 | 单轨字幕重试上限（总窗口约 2 分钟） |
| `BILI_SUBTITLE_RETRY_BASE` | 5s | 递增退避首步（attempt=2 的基准） |
| `BILI_SUBTITLE_RETRY_STEP` | 3s | 每多一次尝试的递增步长 |
| `BILI_SUBTITLE_RETRY_MAX` | 30s | 单次退避封顶 |
| `BILI_SUBTITLE_RETRY_JITTER` | 0.25 | 退避抖动比例（±25%） |
| `subtitle_check`（options） | true | B站字幕内容抽查开关 |
| `force_transcribe`（options） | false | 跳过B站字幕直接转录 |
| `download_video(max_seconds)` | 无 | 只取开头 N 秒片段（抽查用 90s；失败自动回退完整下载） |
| `transcribe_language`（options） | 'zh' | 云端忽略但影响本地兜底与字幕抽查 |
| `asr_default_engine`（app_settings） | 空 | 用户设置的默认转录引擎（空=按免费优先自动推荐）；可用性不足时只降不升 |
| `transcribe_engine`（options） | 无 | 任务级引擎；**缺省/非法值 → 推荐引擎（免费优先），绝不落付费档**（`main.resolve_transcribe_engine` 单点） |
| `provider`（云端调用） | 无 | **缺省 → 只挑免费槽位**；无免费档即抛 ConfigError（付费档必须显式传入） |
| `asr_whisper_model`（app_settings） | base | 默认 Whisper 档位（模型管理页可改；未设置回退 config.DEFAULT_WHISPER_MODEL） |
| `asr_whisper_device`（app_settings） | auto | 转录设备（auto/cuda/cpu；任务 options 仍可逐任务覆盖） |

## 7. 坑点汇总（有解法的才记，全链路适用）

1. silero VAD 必须 512/块分块喂，整段喂只报尾部一段；段首尾 +0.15s padding 防吞字。
2. `vad.window_size`/`front` 属性与方法易混；`VadModel.create(config)` 不能直接构造。
3. Windows `tempfile.mkstemp` 句柄先 close 再 unlink，否则 ffmpeg 写入冲突。
4. mimo ASR 请求禁带 text；qwen `parameters.format` 必填且 c8 即 429（需退避重试）。
5. firered 兜底不触发下载；测试注入分片失败必须禁用补录路径（否则真实加载 1.3GB 模型）。
6. Whisper 惰性编码：CUDA DLL 缺失在迭代期才炸，`list(segs)` 留 try 内。
7. 采样/补录用 Whisper 必须 language 跟随 + 简中提示词，否则中文幻觉英文（相似度恒 0）。
8. GitHub release 下载 `follow_redirects=True`，tar 解包 `filter='data'`；标点模型 tag 是
   `punctuation-models`（不是 punct-models）。
9. `local` 引擎值语义冻结；改回退链只动 `_FALLBACK_CHAINS` 单点。
10. **服务运行环境 ≠ backend/venv**：服务实际跑在 PATH 上的 python（D:\path\python.exe），
   「No module named sherpa_onnx」排查先看进程解释器（`Get-CimInstance Win32_Process`）再装包；
   新依赖进 requirements.txt 必须同步 start.bat 探测 import 列表，否则启动不自动补装。
11. **provider 配置只有一个读取点**：`cloud_asr._provider_cfg()`。任何模块都不要直接查
   `asr_providers` 表——否则 DB 优先/兜底语义会在多处漂移（漏了兜底就变成"表空即崩"）。
12. **带 provider 的测试用例必须隔离 DB 层**：`asr_providers` 在 `init_db()` 时会从本机 config.py
   播种，不隔离的话「monkeypatch config → 断言 config 分支」的用例会被真实 DB 值污染而假通过/假失败。
   既有做法：模块级 autouse fixture patch `db.asr_repo.get_provider` → None 且
   `get_provider_for_engine` → 固定槽位映射（见 `tests/test_cloud_asr.py` / `test_transcriber.py` /
   `test_routers.py::TestAsrStatusRouter`）。
13. **删除本地模型要挡两件事**：①下载进行中禁删（`_download_state.status == 'downloading'`）；
   ②FireRedASR 转录进行中禁删（`firered_busy()` = `_FIRERED_LOCK.locked()`）。Whisper 档位删除
   还必须清 `_model_cache`（不清则下次转录仍用内存旧实例，「删了还在跑」被误判为删除无效）。
14. **一次性云端调用（字幕抽查）必须显式带 provider**：`transcribe_file(path, provider=...)`，
   provider 由 `preferred_provider()` 选（**只挑免费槽位**，免费档不可用返回 None → 本地 Whisper）。
   函数缺省值已改为"免费优先/无免费则报错"，不再是 `'mimo'` 付费档——但调用方仍应显式传，
   否则语义依赖函数内部默认（见坑点 16）。
15. **测试必须隔离模型目录，且 `LOCAL_MODEL_DIRS` 只能就地改不能换对象**（2026-09-15 真实事故）：
   ①删除模型端点的用例若没隔离目录，会**真的删掉 `models/` 下的模型**（当时删掉了 FireRedASR
   三件套+标点+VAD ≈1.5GB，只表现为一条 "DID NOT RAISE"）；`tests/conftest.py` 的
   session 级 `_isolate_model_dirs` 把 FireRed 三个路径 + `LOCAL_MODEL_DIRS` 全部重定向到
   临时目录，并在会话开始自检「没有任何模型路径仍指向真实 `models/`」。
   ②`LOCAL_MODEL_DIRS` **必须 `clear()+update()` 就地改**：`routers/asr_admin.py` 在 import 时
   就绑定了同一个 dict 对象，替换成新对象会让路由仍按真实路径删（测试以为在删 tmp 路径）。
16. **默认值不许是付费档**（2026-09-15 付费额度泄漏）：漏传 `provider` / `transcribe_engine`
   落到函数默认值 `'mimo'` / `'cloud'`，就是"用户没选、系统却花钱"。规矩是——
   ①功能函数（`transcribe_file` / `transcribe_audio_cloud`）的 provider 缺省 = **免费优先**，
   无免费档就抛可行动错误；②引擎类缺省值（`main.resolve_transcribe_engine`、
   `transcriber._transcribe_sync`/`transcribe_audio`、`_fallback_chain` 未命中）**一律落免费档链**；
   ③前端占位值也必须是免费档，并用显式 `engineTouched` 标记区分"用户主动选了付费档"与"仍是占位"。
   回归锁：`tests/test_cloud_asr.py::TestNoPaidDefault`、`tests/test_main.py::TestResolveTranscribeEngine`、
   `tests/test_transcriber.py::TestEngineValuesAndChains::test_unknown_engine_falls_back_to_free_chain_not_paid`。
   17. **B站字幕只有 `x/player/v2`（含 wbi 变体）一条真源，别再找"第二数据源"**（2026-09-21 真网否证）：
    `x/player/wbi/playurl` 与 `x/player/playurl` 的 `data.subtitle` **恒为 `null`**
    （`fnval=4048` 与 `16` 都试过，`code=0` 且无字幕字段）——B站已不在播放地址接口返回字幕元数据；
    网页 `__initial_state__`/`__playinfo__` 里的字幕同样是 playurl 的回显，一并无效。
    所以"多源交叉取链"这条路不成立，字幕获取的对错只能靠**同一源内的新链接 + 时间覆盖自校验**兜。
    （诊断脚本 `backend/scripts/diag/diag_bilibili*.py` 已覆盖该结论；勿重复实验。）
   18. **AI 字幕"轨道在但 body 残"是 B站侧真实状态，不是接口调用姿势问题**（2026-09-21 实证）：
      `BV1DZeK64ETW`（单分P，`duration=9687s`，`videos=1`）的 `ai-zh` body 只有 **74 条 / 覆盖 137s**，
      多次刷新链接均为同一份 → 属于 B站生成不全，重试拿不到更全的内容，只能按 `subtitle_incomplete`
      交转录。判据别放松成"有 body 就用"（那会把前 2 分钟字幕配 2.7 小时视频 = 错字幕）。
      **补充实证（同日复测）**：约 1 小时后同一视频的 body 变成 **2757 条 / 覆盖 9680s**（完整）——
      即 `ai_status=2` 但 body 截断是**瞬态**，B站侧还在补写；所以"重试 + 宁缺勿错"是对的：
      当时拒掉不是误杀，稍后再取就完整了。
      顺带：`x/web-interface/view` 的 `duration` 与 `pages[i].duration` 对单分P一致；多分P 时
      `view.duration` 是**各分P之和**（实测 185 分P 的 `BV1Av4y1A796`：`view.duration=327831s`，
      P1 真实时长仅约 824s），而 `cid` 只对应第 1P——多P 视频取 `video_duration` 必须用
      `pages[page-1].duration`（`_resolve_video_target` 单点），否则分P字幕会被整篇误判为"覆盖不足"。
   19. **第三方库不放在关键路径上；同一不变量必须在所有出口生效**（2026-09-21 结构性教训）：
      `bilibili-api-python` 曾被放在"元数据+轨列表"两个关键位置，结果：①它缺失/报错就整条链路
      降级到最弱的 yt-dlp（两处 `except ImportError → yt-dlp` 悬崖）；②字幕有两个产出出口
      （自研 HTTP 路径 / yt-dlp 路径），新加的覆盖校验与选轨规则**只覆盖了前一个**，
      yt-dlp 出口仍按"文件≥50 字节"放行——加固实际只生效一半。
      规则：新增/修改字幕类不变量时，先穷举"字幕从哪些出口产出"（当前两处：
      `_acquire_bilibili_subtitle` 与 `_extract_bilibili_subtitles_ytdlp`），逐个对齐后再收工；
      加"可选依赖"前先问"它没装会怎样"，答案不能是"链路降级"。
      附带清理项（未做，涉及打包，需单独决策）：`requirements.txt` 的
      `bilibili-api-python>=17.4.0` 与 `start.bat` 第 22/26 行的 import 探测列表里，
      `bilibili_api` 已无代码引用，可移除以缩小安装包与启动探测面。
   20. **412 风控是"递增惩罚"，必须冷却而不是立刻重试**（2026-09-21 批量实测）：
      真实日志（批量任务 `8bb3004f`）显示每个视频的 `x/player/wbi/v2` 必现 412，且同一视频
      **2 秒内连撞两次**——原实现 412 后立即换接口重试，等于在惩罚窗口内反复敲门，越敲越严。
      旧退避是 `3s×(attempt-1)` 共 4 次（总窗口约 18s），比批量实战（递增到 41s、12 次）弱一个
      数量级。修复：①`_throttle_bili_api` 前置进程级冷却窗，412 记账后升级冷却（20s → 连续 5 次
      后 120s），所有 B站请求共享；②字幕重试改递增退避 + 抖动、上限 9 次。
      **判据边界**：拿不到完整字幕仍走 `subtitle_incomplete` 转转录（宁缺勿错），不因加长重试
      而放宽覆盖校验。

## 8. ASR 配置管理 API（模型管理页「ASR 模型」域，2026-09-15）

只读 `routers/asr.py`，写操作 `routers/asr_admin.py`（同挂 `/api/asr` 前缀，对外路径连续）：

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/asr/status` | 工作台单点：`cloud_configured`/`free_configured`/`firered_ready`/`recommended_engine`/`default_engine`/`local_engines`（下拉用轻量清单：key+名+就绪，不遍历磁盘） |
| GET | `/api/asr/overview` | 管理页总览：providers（脱敏+`configured`/`runtime`/`fee`）+ **local.engines（本地引擎列表：名称/免费标识/语言/说明/体积/就绪/占用/文件明细/下载态）** + local.whisper 档位明细 + `sherpa_available` + settings |
| POST/PUT/DELETE | `/api/asr/providers[/{key}]` | provider CRUD（内置不可删可停用；密钥脱敏展示，编辑留空/含 `****` = 不修改） |
| POST | `/api/asr/providers/{key}/test` | 真实调用一次（2s 正弦探测音频，`_PROBE_SECONDS`）；HTTP 200 + `success` 字段承载成功/失败 |
| POST | `/api/asr/settings` | 默认引擎（空串=清除→自动推荐）/ Whisper 档位 / 转录设备 |
| GET/POST | `/api/asr/local/{engine}/download` | **按引擎 key 通用**：下载状态轻量轮询 / 启动后台下载（daemon 线程 + 内存态，重启回 idle 可重试）；未注册 key → 404 并列出可选值 |
| POST | `/api/asr/local/{engine}/cancel` · DELETE `/api/asr/local/{engine}` | 取消下载 / 删除该引擎模型文件（下载中与转录中一律 409） |
| DELETE | `/api/asr/local/whisper/{size}` | 删除某档 Whisper 模型目录 |

> 本地端点用 `{engine}` 通配（不再硬编码 `firered`）：旧路径 `/local/firered/*` 天然命中，
> 前端与外部脚本无需改动；**收费标识单点在后端**（provider `fee`=收费/免费、本地引擎恒定免费），
> 前端只显示不推断。

- 测试连接**不返回 HTTP 错误码承载业务失败**（与 AI 模型测试连接同契约），前端按 `success` 分支。
- 探测音频用 ffmpeg 生成正弦波：仓库不存媒体；无人声返回空文本属正常，`note` 字段会说明。
- 管理页数据流：`stores/asrStore.js`（失败落 `error` 供失败态展示；写操作抛错由视图 ElMessage 提示）
  → `components/models/AsrPanel.vue`（云端/本地二级分段）→ `AsrCloudPanel` / `AsrLocalPanel`。


## 9. ASR 结果缓存身份（2026-10-07）

`transcribe_audio` 的 v2 缓存键纳入请求模型档位、语言/说话人、设备/平台、回退链 provider
白名单配置与本地模型文件签名；旧四要素键不再读取。身份由 `_transcription_cache_key` 单点生成，
一次调用读写共用；配置不含 API Key。整体回退到其它引擎的结果可正常交付，但不存成首选缓存，
避免临时故障恢复后仍锁定 Whisper。`execution_state['engine']` 表示成功的整体引擎，
不表示云端逐片补录来源；三元返回值不变。完整签名、错误矩阵和回归要求见
[ai-call-cache.md](ai-call-cache.md)。写入前再次核对开始时身份，识别期间配置变化时只返回结果，不缓存。
