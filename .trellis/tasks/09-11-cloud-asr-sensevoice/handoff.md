# 交接文档：云端 ASR（SenseVoice）接入 + 本地转录改造

> 交接时间：2026-09-11 | 任务：`09-11-cloud-asr-sensevoice` | 状态：**Build/Verify 完成，未 Archive**
> 工作树干净，所有改动已 commit。接手前先读本文件，再读 `prd.md` / `design.md`。

---

## 0. 给接手 Agent 的第一句话

本任务**主体已完成并验证通过**（云端 SenseVoice 主力转录 + 本地 Whisper 兜底 + 前端引擎选择器 + 上传上限 8GB + 本地模型清理）。

你接手要做两件事：
1. **收尾本任务**（Archive：更新 CONTEXT.md、归档 task）
2. **开新任务做调用链优化 + 小红书 FireRedASR 本地模型适配**（详见 §5，这是用户的核心新诉求）

---

## 1. 用户原始诉求与演进

| 阶段 | 诉求 |
|---|---|
| 初始 | 现用 faster-whisper 中文精度太差，要更高精度的方案；本地或云端 ASR 都要评估 |
| 决策 | **云端为主**（用户提供了 SiliconFlow API Key）+ 本地资源整理；放弃原纯 Whisper 路线 |
| 追加 | 质疑 B 站字幕抽查依赖 base 模型 → 优化抽查策略（已改为云端优先） |
| 追问 | 云端单次 1h/50MB 限制怎么办（2h 视频）→ 静音分片已天然绕开；上传 2GB 限制 → 提高 |
| 追问 | 几百个分片会不会触发并发限流 → 查证账号级 RPM 1000，并发上限放宽到 16 |
| **最新** | **① 优化整体调用链（云端优先→本地兜底）；② 本地模型换成/增加「小红书」FireRedASR（当前未适配）** |

---

## 2. 已完成内容（含 commit 与验证证据）

### 2.1 提交清单（按时间序，均在 master）

| commit | 内容 |
|---|---|
| `41603de` | 任务创建（PRD + Design 落盘） |
| `12736b5` | **云端 SenseVoice 引擎** `backend/services/cloud_asr.py` + 38 个单测 |
| `1b364e7` | **引擎路由**：`options.transcribe_engine` 全链路透传，云端失败回退本地（取消不回退） |
| `41c4315` | **B站字幕抽查云端优先**：SenseVoice 转写 90s 片段，失败回退本地 base，双失败保留字幕 |
| `bb90599` | **`GET /api/asr/status`** 端点（不回密钥） |
| `e6a0fa8` | fix：收尾切点不传 `segment_times`（ffprobe 毫秒偏差曾致多切微型尾片 4vs3） |
| `1e106bc` | **前端「转录引擎」选择器**（云端默认推荐；未配置自动禁用回落本地；云端模式隐藏 Whisper 模型/设备） |
| `f2b1a22` | **上传上限 2GB→8GB**（前后端同步） |
| `273de7f` | **分片级韧性 + 实测时间轴 + 超时 180s**（见 §3.2） |
| `cbc56af` | **并发上限 8→16、默认 4→8** |

### 2.2 验证证据（全部实际跑过，非推断）

| 验证项 | 结果 |
|---|---|
| 后端 pytest 全量 | **511 passed** |
| 前端 vitest 全量 | **55 passed** |
| vite build | 通过 |
| 真实 API 冒烟（直调模块） | 17s 音频/3 分片，**2.2s 完成（8.8x 实时）**，SRT 时间轴单调、文本准确 |
| 浏览器 E2E | 引擎选择器存在、默认云端；云端模式 Whisper 模型/设备隐藏；切本地后出现（medium/auto）；**无 console error**；`/api/asr/status` 200 |
| 真实 HTTP 端到端任务 | 任务 `6ccc473d` completed；SRT 2 段、时间轴单调、文本正确、无富文本标签 |

### 2.3 本地模型清理（已执行，models/ 不入 git）

- **已删除**：`whisper_tiny`(75MB)、`whisper_small`(464MB)、`whisper_small_broken`(464MB)、`whisper_medium_broken`(1460MB) —— 释放约 2.4GB
- **已保留**：`whisper_base`(141MB，B站字幕抽查本地回退依赖)、`whisper_medium`(1460MB，本地兜底)

---

## 3. 代码结构与关键设计（接手必读）

### 3.1 调用链路

```
前端 HomeView「语音转录 → 转录引擎」
  └ advancedOptions.transcribe_engine: 'cloud'(默认) | 'local' | 'auto'
      ↓ 随 options 整包提交（单任务/批量/重跑均透传）
后端 main._process_job_impl
  └ options.transcribe_engine（非法值按 auto）
  └ _handle_media_file / _handle_url_input（各自带 transcribe_engine 参数）
      └ transcriber.transcribe_audio(..., engine=engine)
          └ _transcribe_sync(engine=...)   ← 单点路由
              ├ engine != 'local' 且 cloud_asr_configured()
              │    → cloud_asr.transcribe_audio_cloud()
              │        成功 → (srt, txt, seg_count) 直接返回
              │        TranscriptionCancelledError → 原样抛（取消不是故障）
              │        其它异常 → 进度回调可见告警 → 落到本地路径
              └ 本地 faster-whisper 原路径（未改动）
```

### 3.2 `cloud_asr.py` 关键设计（含实测教训）

- **静音分片**：`detect_silences`（ffmpeg silencedetect）→ `plan_cuts`（纯函数，段长 3~30s）
- **导出**：`export_chunks` 一次 ffmpeg `-f segment`，**只传内部切点**（收尾切点会因毫秒偏差多切尾片）
- **时间轴**：以 `-segment_list csv` 实测每片时长累计（比计划切点更准，根治 4vs3 偏差）
- **并发**：`ThreadPoolExecutor`，默认 8、上限 16，env `ASR_CLOUD_CONCURRENCY` / config 可调
- **超时**：180s/片（免费档排队延迟常 >90s），env `ASR_CLOUD_TIMEOUT` 可调
- **重试**：429/5xx/网络 → 指数退避 ≤3；401/403/404 → `CloudASRConfigError` 立即抛不重试
- **分片级韧性**：单片 `Unavailable` → 跳过记 error 日志（含失败区间），其余照常；**全部分片失败**才抛 `Unavailable` 整体回退本地
- **产出**：`(srt_path, txt_path, seg_count)`，与本地 Whisper 引擎**同契约**；富文本标签 `<|xxx|>` 清洗
- **不支持**：说话人分离（云端忽略并告警）、字级时间戳（API 不提供，用静音切边界≈句级）

### 3.3 关键文件

| 文件 | 说明 |
|---|---|
| `backend/services/cloud_asr.py` | 云端引擎全部逻辑（新增） |
| `backend/services/transcriber.py` | `_transcribe_sync` 是引擎路由**单点**；`transcribe_audio` 接收 `engine` |
| `backend/main.py` | `options.transcribe_engine` 校验与透传（约 L766-770、L820、L828） |
| `backend/services/downloader.py` | `verify_subtitle_against_audio` 抽查云端优先 |
| `backend/routers/asr.py` | `GET /api/asr/status`（新增） |
| `backend/config.py`（gitignored） | `ASR_CLOUD_*` 配置，**含真实 Key** |
| `backend/config.example.py` | 配置模板（无 Key） |
| `frontend/src/views/HomeView.vue` | 转录引擎选择器（约 L996-1029） |
| `frontend/src/stores/settingsStore.js` | `transcribe_engine` 默认值 + `loadCloudAsrStatus` |
| `backend/tests/test_cloud_asr.py` | 42 个单测 |

---

## 4. 踩过的坑（接手务必知道）

1. **收尾切点不能传给 ffmpeg**：ffprobe 时长与实际音频有毫秒偏差，传了会多切一个微型尾片（实测 4 片 vs 计划 3 片）。
2. **SiliconFlow 免费档排队延迟抖动极大**：同一段音频 3 个分片分别用了 2s / 26s / 90s；直调模块测 2.2s，走完整任务测 97s。**这是外部服务特性，不是代码 bug**——不要为此改分片逻辑。
3. **`shutil.which('ffmpeg')` 可能返回大写 `ffmpeg.EXE`**：字符串替换 `.replace('ffmpeg.exe','ffprobe.exe')` 会失败，必须用 `Path(...).with_name('ffprobe.exe')` 或 `which('ffprobe')`。
4. **ffprobe 在 pytest 子进程下偶发返回空**：测试里用 ffmpeg `-i` 自检更稳。
5. **B站字幕时长校验阈值不要收紧**：现界 `0.5x-30s / +120s` 是**刻意放宽**（片头片尾无语音的正常字幕跨度天然不满）。用户曾提议按 90% 判定，已论证会误杀好字幕——详见 `prd.md` 不做项。
6. **云端 1h/50MB 限制已天然绕开**：分片 ≤30s/≤200KB，2h 视频也只是几百个小分片。
7. 测试必须用 `backend/venv/Scripts/python.exe -m pytest`（沙箱默认 Python 缺 pytest-asyncio）。

---

## 5. 【下一步】新任务：调用链优化 + FireRedASR 本地适配

> 这是用户最新明确诉求，建议**新建独立 task**（预估 M1~M2），本任务先 Archive。

### 5.1 诉求原文

> "优化当前的这个调用链。首先是云端的，然后本地的模型，是小红书的一个模型，我不知道当前适配了没有，我看应该没有适配。整体上的话，要这样做吧"

**已核实：FireRedASR 当前完全未适配。** 代码里唯一「小红书」字样是 `downloader.py` 的下载平台 `xiaohongshu.com`，与 ASR 模型无关。

### 5.2 目标调用链（建议形态）

```
引擎选择：云端优先 → 本地兜底
  ├ cloud   : SiliconFlow SenseVoice（现状，已实现）
  ├ local   : FireRedASR（小红书，待接入）  ← 新增
  └ whisper : 现 faster-whisper（保留为最后兜底？需与用户确认是否保留）
失败回退链：cloud 失败 → FireRedASR → (可选) Whisper
```

**待与用户确认的关键决策点**（不要自行拍板）：
- 本地引擎是**替换** Whisper 还是**叠加**（三引擎）？
- 是否保留 faster-whisper 作为最后兜底（依赖轻、纯 CPU 也能跑）？
- 前端「转录引擎」下拉是否增加第三个选项？

### 5.3 FireRedASR 调研事实（2026-09-11，供设计参考）

| 项 | FireRedASR-AED-L | FireRedASR2S（新版全家桶） |
|---|---|---|
| 参数/体积 | 1.1B，模型文件约 4GB | 更大，Docker GPU 模式 **需 ~15GB 显存** |
| 中文精度 | aishell1 CER 0.57%（当时最低） | 普通话 CER 2.89%（SOTA） |
| 依赖 | **PyTorch + torchaudio**（Python 3.8-3.10）、librosa/soundfile、ffmpeg | 同上 + VAD/LID/Punc 模块 |
| 能力 | ASR；支持 20+ 方言、中英混、歌词 | ASR + VAD + LID + 标点，词级/句级时间戳 |
| 许可 | Apache-2.0 | Apache-2.0 |

**⚠️ 关键约束（必须写进设计文档）**：
1. **PyTorch 依赖与当前打包策略冲突**：v1.1.0 安装包仅 **137MB**（CPU-only，裁剪了 CUDA DLL）。引入 torch 会让安装包膨胀到 **2GB+**。需要决定：随包分发 / 运行时按需下载 / 独立进程服务。
2. **本机显存 6GB（RTX 3060 Laptop）**：FireRedASR-AED-L（1.1B）fp16 约 2.2GB，**可行**；FireRedASR2S Docker 需 15GB，**不可行**——优先选 AED-L 或 FireRedASR2-AED。
3. **时间戳能力是关键加分项**：FireRedASR 支持**词级/句级时间戳**，可解决当前云端方案「只能靠静音切分近似句级」的短板（用户当初想优化字幕时间轴）。
4. 参考仓库：`github.com/FireRedTeam/FireRedASR`（官方）、`github.com/gwures/FireRedASR2-cli`（CLI 封装）、`github.com/neosun100/FireRedASR2S`（Docker 化，需大显存）。
5. 模型下载：HuggingFace `FireRedTeam/FireRedASR-AED-L`；国内可走 ModelScope。

### 5.4 建议的实施步骤（给接手 Agent）

1. **Open**：与用户确认 §5.2 的决策点（替换/叠加、是否保留 Whisper、打包策略）
2. **Design**：写清「引擎优先级 + 回退链 + 依赖与打包方案 + 显存适配（6GB）」
3. **Build**：
   - 新增 `backend/services/firered_asr.py`（与 `cloud_asr.py` 同契约：`(srt, txt, seg_count)`）
   - `transcriber._transcribe_sync` 的**单点路由**扩展（保持单点，勿散落判断）
   - 前端引擎下拉加选项；`/api/asr/status` 扩展返回本地引擎可用性
   - 模型目录约定（如 `models/firered_asr_aed/`），复用现有 `_model_complete` 式完整性校验思路
4. **Verify**：真实音频对比三引擎精度（**建议用同一段中文音频实测 CER 感受差异**）+ 现有 511 单测不回归
5. **Archive**：更新 CONTEXT.md（引擎列表、依赖、坑点）

---

## 6. 收尾清单（本任务 Archive 用）

```
[ ] 更新 CONTEXT.md：
      - 技术栈加云端 ASR（SiliconFlow SenseVoice）
      - 文件索引加 cloud_asr.py / routers/asr.py，标注「何时读」
      - 记录坑点：收尾切点、免费档排队延迟、ffmpeg.exe 大小写
      - 当前状态区块刷新（引擎选择、上传上限 8GB、本地模型已清理）
[ ] python .trellis\scripts\task.py archive
[ ] git add + commit（仅 CONTEXT.md / spec 增量 / 复盘）
[ ] git push —— 必须先获用户本轮明确授权（PUSH-GATE）
```

### 已知遗留（非阻塞，供决策）

- **SiliconFlow 免费档排队延迟**导致长视频可能较慢；如需提速可考虑：升级账号等级（RPM 更高）、或提高 `ASR_CLOUD_CONCURRENCY`。属外部服务特性。
- `transcribe_engine='auto'` 与 `'cloud'` 当前行为**几乎一致**（都是云端优先）；差异仅在云端**未配置**时 `'cloud'` 会给显式告警而 `'auto'` 静默——如需语义区分可在新任务中明确。
- 用户提到「AI 校正」开关仍在（`ai_correct`），与本次转录引擎是**独立**的两步，勿混淆。
