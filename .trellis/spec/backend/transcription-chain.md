# 识别链全链路权威文档（转录/识别引擎体系）

> 2026-09-12 定稿。汇聚 09-11/09-12 三个任务的全部实测结论，是转录链路的单一事实源；
> 改动链路任何环节前先读本文，改完必须同步本文（spec 规则）。
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
  ②内容抽查（默认开，`subtitle_check=false` 关）：抽开头 ~90s 音频用
  `transcribe_audio_sample`（**固定 CPU Whisper base，与引擎选择无关**，采样语言跟随
  任务转录语言 + 简中提示词防幻觉）与字幕头相似度 <0.3 判错位 → 弃字幕转全量转录。
  抽查预下载的音频在校验失败时复用，不二次下载。
- **YouTube**：`extract_youtube_subtitles` 拿平台字幕，逻辑同上（无抽查）。
- 其他平台：直接下载 → 引擎路由。

## 2. 引擎路由（单点：transcriber._transcribe_sync，勿散落）

```
选项流：前端 settingsStore.advancedOptions.transcribe_engine（cloud/cloud_free/firered/local 四态下拉）
  → submit 整包进 options → main.py 白名单校验（非法值归一 'cloud'，单点 main.py:773）
  → transcriber.transcribe_audio（async 壳：进度队列桥接 + 线程池，无路由逻辑）
  → _transcribe_sync（回退链循环）
```

**回退链（递归降级，每级失败原因经 progress_callback 可见）：**

| 用户选择 | 实际回退链 |
|---|---|
| cloud（默认） | 云端 MiMo（付费主力）→ firered（仅模型就绪时）→ Whisper |
| cloud_free | 云端 XingChen Ultra（免费档，简单视频）→ firered → Whisper |
| firered | FireRedASR → Whisper |
| local | Whisper（无回退） |

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
401/403/404 为 ConfigError 立即抛；400 归重试——可能是单片内容问题，耗尽后跳过交本地补录）
→ 写盘前 `split_sentences`+`distribute_times` 句级化（纯本地后处理：句末标点切句、
短句累积≥4 字、超 40 字强制断、字数比例分配时长）。
- 失败语义：单片失败 → **分片级本地补录**（§4）；全部失败且补录无收获 → 抛 Unavailable 走回退链。
- 结束打**延迟画像日志**（带 provider 标签，min/中位/p90/max）——诊断"云端变慢"先看这行。

| provider | 引擎值 | API 姿势 | 计费 | 实测 |
|---|---|---|---|---|
| mimo（默认） | cloud | chat/completions + base64 input_audio（**禁带 text**） | ¥0.5/h | 10min 素材 9.4s，术语最准 |
| xingchen | cloud_free | OpenAI multipart /audio/transcriptions（同旧 SenseVoice 接口） | **限时免费** | 单片 1.7~4.7s 稳定无抖动，原生标点 |

- 旧 SenseVoiceSmall 已退役（同基建单片 46~83s 排队抖动，服务端问题无参数解）。
- provider 配置键：mimo=`ASR_CLOUD_*`；xingchen=`ASR_FREE_*`（config.example.py 有模板）。
- 400 归重试不判配置错：可能是单片内容问题，重试耗尽 → 跳过 → 本地补录，不能拖垮整体。

### 3.2 本地 FireRedASR2-AED（local_asr.py，sherpa-onnx int8/CPU，不依赖 torch）

`firered_model_ready()`（三件套+标点+VAD 文件级校验）→ 缺失则按需下载（GitHub release，
`follow_redirects=True`，tar `filter='data'`；手动放置 `models/` 兜底）→ **模块级锁串行**
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

## 4. 分片级递归补救（cloud_asr._rescue_chunks_locally，09-12 新增）

云端单片失败不再留空洞：失败片**在分片文件清理前**递归补录——
firered（`transcribe_chunk_text`，模型就绪优先）→ Whisper base（`transcribe_audio_sample`
固定 CPU，中文带简中提示词，与字幕抽查同路径）→ 都失败才留空（日志可见）。
补录文本按原时间轴并入结果并句级化；全失败但补录有收获则不再抛 Unavailable。
完成文案透出：`N 片由本地引擎（FireRedASR）补录`。

## 5. 引擎横评结论（同素材 BV1bH4y1r75q 前 10 分钟，88 片 c8）

| | mimo-v2.5-asr | qwen-asr-flash | SenseVoice | FireRedASR |
|---|---|---|---|---|
| 总耗时 | **9.4s** | 33.0s | 848.4s | 168.8s |
| 幻觉 | 0 | 0 | **8 处假名** | 0 |
| 术语（内关/太冲/扎） | **全对** | 全对 | 全错 | 错一 |
| 单价 | **¥0.5/h** | ¥0.79/h | ~免费 | ¥0 |

**SenseVoice 已退役**（慢 90 倍+唯一幻觉）；mimo 为付费主力，XingChen Ultra（限时免费）为免费档；
FireRedASR 免费离线保留。XingChen 未参与 10min 全量横评（单片实测 1.7s 稳定、TTS 样本质量与 mimo 同档）。

## 6. 参数与环境变量速查

| 变量/常量 | 默认 | 说明 |
|---|---|---|
| `ASR_CLOUD_CONCURRENCY` | 8（cap 16） | 云端并发；实测调高更慢 |
| `ASR_CLOUD_TIMEOUT` | 180s | 单片单次超时 |
| `FIRERED_NUM_THREADS` / `FIRERED_DECODE_WORKERS` | 3 / 6 | 本地识别线程/并行度（扫参甜点勿动） |
| `BILI_API_MIN_INTERVAL` | 1s | B站接口限流间隔 |
| `subtitle_check`（options） | true | B站字幕内容抽查开关 |
| `force_transcribe`（options） | false | 跳过B站字幕直接转录 |
| `transcribe_language`（options） | 'zh' | 云端忽略但影响本地兜底与字幕抽查 |

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
