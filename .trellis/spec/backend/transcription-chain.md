# 识别链全链路权威文档（转录/识别引擎体系）

> 2026-09-12 定稿。汇聚 09-11/09-12 三个任务的全部实测结论，是转录链路的单一事实源；
> 改动链路任何环节前先读本文，改完必须同步本文（spec 规则）。
> 2026-09-15 更新：云端 provider 配置改「DB（asr_providers 表）优先 + config.py 兜底」、
> 槽位化 provider 解析、默认引擎用户设置优先（只降不升）、ASR 配置管理 API（§8）与坑点 11-15；
> 本地档改**引擎注册表**（`LOCAL_ENGINES`，本地端点按 `{engine}` 通用化、管理页本地段改引擎列表
> 且收费标识单点在后端）；LLM 解码类本地引擎（Fun-ASR-Nano / Qwen3-ASR）实机横评后**放弃**（§5.1）。
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

- **B站**：`force_transcribe=true` 跳过字幕直接转录；否则 `extract_bilibili_subtitles`
  （CC + AI 双路径，旧版 x/player/v2 优先，412 风控六项加固：串行限流≥1s/wbi 刷新/完整
  Chrome UA+buvid/明确无字幕才跳 yt-dlp）。拿到字幕**不直接信**，过两道防错位校验：
  ①时长校验（字幕时间跨度 vs 视频时长，明显不符即拒）；
  ②内容抽查（默认开，`subtitle_check=false` 关）：**只下载开头 90s 片段**
  （`download_video(max_seconds=90)` → `--download-sections`，实测加固姿势 3.3s/6.9MB；
  片段放 `job_dir/_subtitle_check/` 子目录防断点续传误当完整音频复用；片段下载失败自动
  回退整段且日志可见），转写后与字幕头相似度 <0.3 判错位 → 弃字幕转全量转录。
  抽查转写**云端优先**——provider 走 `cloud_asr.preferred_provider()` 单点
  （`cloud_free` 槽位优先，免费档不可用才用 `cloud`；都不可用直接本地），
  `extract_sample` + `transcribe_file(provider=...)`；失败回退本地 `transcribe_audio_sample`
  （**固定 CPU Whisper base**，采样语言跟随任务转录语言 + 简中提示词防幻觉）。
  **勿用 `transcribe_file` 的默认 provider**（那是 mimo 付费档，欠费时会白重试 3 次）。
  片段**不作转录输入与素材**：校验失败时转录由 `_acquire_audio` 重新下载完整音频。
- **YouTube**：`extract_youtube_subtitles` 拿平台字幕，逻辑同上（无抽查）。
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

## 6. 参数与环境变量速查

| 变量/常量 | 默认 | 说明 |
|---|---|---|
| `ASR_CLOUD_CONCURRENCY` | 8（cap 16） | 云端并发；实测调高更慢 |
| `ASR_CLOUD_TIMEOUT` | 180s | 单片单次超时 |
| `FIRERED_NUM_THREADS` / `FIRERED_DECODE_WORKERS` | 3 / 6 | 本地识别线程/并行度（扫参甜点勿动） |
| `BILI_API_MIN_INTERVAL` | 1s | B站接口限流间隔 |
| `subtitle_check`（options） | true | B站字幕内容抽查开关 |
| `force_transcribe`（options） | false | 跳过B站字幕直接转录 |
| `download_video(max_seconds)` | 无 | 只取开头 N 秒片段（抽查用 90s；失败自动回退完整下载） |
| `transcribe_language`（options） | 'zh' | 云端忽略但影响本地兜底与字幕抽查 |
| `asr_default_engine`（app_settings） | 空 | 用户设置的默认转录引擎（空=按免费优先自动推荐）；可用性不足时只降不升 |
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
   provider 由 `preferred_provider()` 选（免费槽位优先）。省略该参数会落到默认 `'mimo'`
   付费档——2026-09-15 实测 MiMo 欠费（HTTP 402）时，每个 B站字幕任务白重试 3 次退避
   （≈11s）后才回退本地 Whisper，前端只看到「云端抽查失败（CloudASRUnavailableError）」。
15. **测试必须隔离模型目录，且 `LOCAL_MODEL_DIRS` 只能就地改不能换对象**（2026-09-15 真实事故）：
   ①删除模型端点的用例若没隔离目录，会**真的删掉 `models/` 下的模型**（当时删掉了 FireRedASR
   三件套+标点+VAD ≈1.5GB，只表现为一条 "DID NOT RAISE"）；`tests/conftest.py` 的
   session 级 `_isolate_model_dirs` 把 FireRed 三个路径 + `LOCAL_MODEL_DIRS` 全部重定向到
   临时目录，并在会话开始自检「没有任何模型路径仍指向真实 `models/`」。
   ②`LOCAL_MODEL_DIRS` **必须 `clear()+update()` 就地改**：`routers/asr_admin.py` 在 import 时
   就绑定了同一个 dict 对象，替换成新对象会让路由仍按真实路径删（测试以为在删 tmp 路径）。

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
