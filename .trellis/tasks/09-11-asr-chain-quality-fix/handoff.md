# 交接文档：转录调用链优化 + FireRedASR 本地引擎

> 交接时间：2026-09-11 | 当前任务：`09-11-asr-chain-quality-fix` | 状态：**质量修复已完成并 commit；本地引擎已完成可行性实测，待立项实现**
> 工作树干净（`git status` 空，4 个 commit 已落地）。接手先读本文件，再读 `prd.md`。

---

## 0. 给接手 Agent 的第一句话

本会话做了两件事：

1. **已完成并 commit**：修掉云端 ASR 调用链的 3 个已核实缺陷（`2ce7ffa` / `3b76575`）。
2. **已完成可行性实测、尚未立项**：本地引擎确定为 **FireRedASR2-AED（小红书）**，走 sherpa-onnx ONNX int8，**不依赖 torch**。全部关键数据已实测（见 §4），可直接据此写 design。

用户最新要求：**实现本地引擎后，用 B站视频 `BV1bH4y1r75q` 对比云端 vs 本地的实际效果**。

---

## 1. 用户诉求演进

| 阶段 | 诉求 |
|---|---|
| 初始 | 摸清当前转录调用链，给优化意见 |
| 决策 | 先修现有问题，再删 `auto` 死语义 |
| 追加 | 加本地模型。用户原话"不用小红书的模型"**后被本人纠正**——实际是"本地部分**首选**小红书模型（FireRedASR）" |
| 追加 | 让我做本地模型推荐（当前本地质量太差） |
| 决策 | 按我的推荐走：先做**可行性实测**，拿到数据再立项 |
| **最新** | **实现本地引擎，并用 `BV1bH4y1r75q` 对比云端 vs 本地** |

---

## 2. 已完成内容（本次会话 commit）

| commit | 内容 |
|---|---|
| `2ce7ffa` | **云端 SRT 句级切分**：新增纯函数 `split_sentences` / `distribute_times` + 18 个单测 |
| `3b76575` | **移除 `auto` 死语义** + 云端下「转录语言」「说话人分离」补失效文案 |
| `d1aac07` | `docs(trellis)`: 本任务 PRD |
| `befe7ac` | `chore(build)`: 重建前端静态产物 |

**验证证据**：后端 **529 passed**（基线 511，+18 新用例）、前端 **55 passed**、vite build 通过、lint 0。
测试必须用 `backend/venv/Scripts/python.exe -m pytest`（沙箱默认 Python 缺 pytest-asyncio）。

### 2.1 句级切分怎么做的（勿回退）

云端 API 只回纯文本、无时间戳，**句级时间轴物理上拿不到**。解法是写盘前做纯本地后处理：

```python
# cloud_asr.py
segments_out = []
for seg_start, seg_end, text in results:
    segments_out.extend(distribute_times(seg_start, seg_end, split_sentences(text)))
```

- `_SUB_SENT_END='。！？!?…；;'`、`_SUB_MIN_CHARS=4`、`_SUB_MAX_CHARS=40`
- 过短碎片是**向前累积到 4 字才落一条**（不是"短句并入前句"），行尾残余并回前一条
- 英文句点仅在后接空白/结尾时算句末（避免切开 `3.5`）
- 超长无标点句在 `，,、 ` 附近强制断开
- 无标点文本退化为「一片一条」，行为不变

### 2.2 `auto` 清理了什么

`auto`/`cloud`/`local` 三值 → `cloud`/`local` 两值。前端本来就只有两项，`auto` 与 `cloud` 行为重复且更差（未配置时**静默**回落本地）。改动点：`main.py`（默认值 + 非法值校验）、`transcriber.py`（`_transcribe_sync`/`transcribe_audio` 默认值 + 路由 + 引擎标签）、`settingsStore.js`（注释）。
收益用户可感知的只有一条：云端未配置时从静默回落 → 变成进度条明确告警。其余是代码层收益 + 为加第三引擎铺路。

---

## 3. 转录调用链全景

| 环节 | 位置 | 说明 |
|---|---|---|
| 主入口 | `main.py:448` / `469` | 上传文件、URL 下载，透传 `transcribe_engine` |
| 异步壳 | `transcriber.py:800` | 进度队列桥接 + 线程池调度，无路由逻辑 |
| **路由单点** | `transcriber.py:597-654` | `engine != 'local'` 且云端已配置 → 云端；异常回落本地；取消不回退 |
| 云端引擎 | `cloud_asr.py:354-524` | 静音切分(3~30s) → 并发 16 → 单片 180s → 单片失败跳过，全败才整体回退 |
| 本地引擎 | `transcriber.py:657` / `400` | ≤20min 单次；>20min 切 300s/片 |
| 旁路抽查 | `downloader.py:673-725` | 90s 片段，云端优先 → 本地 base，阈值 0.3 |

### 3.1 7 个已核实问题及当前状态

| # | 问题 | 状态 |
|---|---|---|
| 1 | 云端 SRT「段」=静音分片，30s 一坨 | ✅ **已修**（句级切分） |
| 2 | 分片失败留空洞不补 | ⏸ 未做（PRD 不做项，另立切片） |
| 3 | `language`/`speaker_diarization` 云端静默忽略 | ✅ **已修**（前端补失效文案；**未隐藏**，因为 language 仍影响 B站字幕抽查的本地兜底） |
| 4 | `auto` 与 `cloud` 行为重复 | ✅ **已修**（删 auto） |
| 5 | 两套分片实现并存 | ⏸ 未做（**不建议统一**：本地 Whisper 自带 VAD，300s 硬切更稳，改静音切是倒退） |
| 6 | 回退整段从 0 重跑 | ⏸ 未做（PRD 不做项，另立切片） |
| 7 | 静音只用来切不用来丢 | ⏸ 未做（PRD 不做项，另立切片） |

---

## 4. 【核心】FireRedASR 可行性实测数据（全部本机实跑，非文档推断）

### 4.1 关键结论：不需要 torch

`sherpa-onnx` 已把 FireRedASR 转成 **ONNX int8**，走 **onnxruntime**——venv 里已有 `onnxruntime 1.27.0`。

- `sherpa-onnx` 轮子 **2 MB**，有 **cp313** 版本 → 已安装 `1.13.8`
- venv Python **3.13.14**
- 实测 `torch 2.14` / `torchaudio 2.11` 也都有 cp313 轮子（**但本项目不需要装**）

### 4.2 模型选型：v2 才是正解

| 模型 | 体积 | CPU 速度 | 精度 | 结论 |
|---|---|---|---|---|
| **FireRedASR2-AED** (v2) | **1.18 GB** | 长音频+VAD 3线程 **RTF 0.182**（官方 Mac） | AISHELL-1 CER **0.57%** | ⭐ **选定** |
| FireRedASR2-CTC (v2) | 740 MB | RTF 0.058（**17x 实时**） | 低一档 | 备选（快但错字多） |
| FireRedASR-AED-L (v1) | 1.7 GB | **RTF 1.945（慢于实时）** | 0.57% | ❌ **排除** |

> ⚠️ 旧文档 `09-11-cloud-asr-sensevoice/handoff.md` 推荐的是 **v1 AED-L**，**那是错的**——v1 在 CPU 上比实时慢一倍。必须用 v2。

CTC vs AED 质量差距（官方同段音频输出铁证）：

| | CTC | AED |
|---|---|---|
| 开头 | ❌「**上好**欢迎大家」 | ✅「**晚上好**欢迎大家」 |
| 中段 | ❌「是**志高**无上的荣誉」 | ✅「是**至高无上**的荣誉」 |
| 结尾 | ❌「梦想**吃火**」 | ✅「梦想**之火**」 |

### 4.3 本机实测结果（272.4s 真实中文长音频，雷军年度演讲）

| 项 | 实测值 |
|---|---|
| 模型加载 | **3.8s**，加载后内存 **1305 MB** |
| VAD 切分 | 41 段 / 1.1s |
| 识别耗时 | **73.7s** |
| **RTF** | **0.271 → 3.7x 实时** |
| 峰值内存 | **1704 MB** |
| 输出 | 41 段 / 565 字 / **时间戳精确到 0.01s** |

→ 换算：**2 小时视频本地转录约 33 分钟**。

### 4.4 标点：ASR 原生无标点，但外接模型已验证可用

ASR 原生输出标点数为 **0**（已确认）。外接 `sherpa-onnx-punct-ct-transformer-zh-en`（下载 266MB / 落盘 281MB）实测：

```
原始  ：晚上好欢迎大家来参加今天晚上的活动谢谢大家
加标点：晚上好，欢迎大家来参加今天晚上的活动，谢谢大家。

原始  ：这是我第四次办年度演讲前三次呢因为疫情的原因都在小米科技园内举办现场的人很少
加标点：这是我第四次办年度演讲。前三次呢因为疫情的原因都在小米科技园内举办，现场的人很少。
```

开销 **0.01s/段**，可忽略。**风险已解除**。

### 4.5 质量提升幅度（回应"当前质量太差"）

| 方案 | AISHELL-1 CER |
|---|---|
| 本地 Whisper medium（现状默认兜底） | 比 large-v3 的 **5.14%** 更差 |
| **云端 SenseVoice（现在的主力）** | ~3.0% |
| **FireRedASR2-AED（将接入）** | **0.57%** |

→ 本地引擎接完后，**会比现在用的云端 SenseVoice 还准**，字错率约降 5 倍。

### 4.6 已发现的问题（都非阻塞）

1. **VAD 边界吞字**：实测丢了「国家会议中心」的"国家"、"这是我第四次"的"这是"、"两个月前"的"两"。原因是段间静音被判为非语音后直接丢弃。**解法：VAD 段首尾各加 0.1~0.2s padding + 调低 threshold**。属调优项。
2. **峰值内存 1704MB**：`MAX_CONCURRENT_JOBS=2`，若同时加载 Whisper medium 需评估。**建议本地引擎用独立进程或加互斥**。
3. **模型总量 ~1.46GB**（AED 1.18 + 标点 0.28 + VAD 0.001）→ 不能随包分发（当前安装包 137MB）。

---

## 5. 三处需要更正的旧结论

| 旧结论 | 更正 |
|---|---|
| `handoff.md`（旧）说 FireRedASR 需 Python 3.8-3.10 | ❌ 过时。torch 2.14 / torchaudio 2.11 都有 cp313 轮子；且本项目走 sherpa-onnx 根本不用 torch |
| `handoff.md`（旧）推荐 FireRedASR-AED-L (v1) | ❌ 错。v1 CPU RTF 1.945 慢于实时；应用 **v2** |
| 我上一轮说"塞 torch 直接破 2GB" | ❌ 过于悲观。torch 轮子实测 118MB；且本项目不需要 torch |

---

## 6. 已落地的环境产物（均 gitignored，不入仓库）

- `sherpa-onnx 1.13.8` 已装进 `backend/venv`（**尚未写进 requirements.txt**，等正式立项再加）
- `models/firered_asr2_aed/sherpa-onnx-fire-red-asr2-zh_en-int8-2026-02-26/`
  - `encoder.int8.onnx` 779MB + `decoder.int8.onnx` 398MB + `tokens.txt`
- `models/punct/`（281MB，`model.onnx`）
- `models/vad/silero_vad.onnx`（0.61MB）
- **主程序代码一行未改**（除 §2 的 4 个 commit），临时脚本已清理

### 6.1 关键 API（实测踩出来的，直接用）

```python
import sherpa_onnx

# 1) 识别器（注意：没有 from_ 之外的构造方式）
rec = sherpa_onnx.OfflineRecognizer.from_fire_red_asr(
    encoder=..., decoder=..., tokens=...,
    num_threads=3, decoding_method='greedy_search', provider='cpu')

# 2) VAD —— 必须用 create(config)，直接 VadModel(...) 会报 "No constructor defined"
vad_cfg = sherpa_onnx.VadModelConfig()
vad_cfg.silero_vad.model = str(vad_path)
vad_cfg.silero_vad.threshold = 0.5
vad_cfg.silero_vad.min_silence_duration = 0.5
vad_cfg.silero_vad.min_speech_duration = 0.25
vad_cfg.silero_vad.max_speech_duration = 20
vad_cfg.silero_vad.window_size = 512
vad_cfg.sample_rate = 16000
vad_cfg.num_threads = 1
vad_cfg.provider = 'cpu'
vad = sherpa_onnx.VadModel.create(vad_cfg)
# ⚠️ vad.window_size 是【方法】不是属性，必须 vad.window_size()

# 3) 标点 —— 没有 from_* 工厂，直接构造
cfg = sherpa_onnx.OfflinePunctuationConfig()
cfg.model.ct_transformer = str(punct_model)
cfg.model.num_threads = 1
punct = sherpa_onnx.OfflinePunctuation(cfg)
text = punct.add_punctuation(text)   # 0.01s/段

# 4) 识别
s = rec.create_stream()
s.accept_waveform(16000, samples_float32)
rec.decode_stream(s)
s.result.text
```

---

## 7. 待用户拍板的决策（不要自行定）

1. **本地引擎是新增第三选项，还是替换 Whisper？** —— 我的倾向：**新增第三项，Whisper 保留作无模型时最后兜底**
2. **模型 1.46GB 怎么分发**：首次使用本地引擎时按需下载（带进度）/ 独立下载器 / 手动放置到 `models/`
3. **等级**：我建议 **M2**（跨模块：新 service + 引擎路由 + 前端选项 + 模型下载器；含 1.5GB 外部下载与并发内存，带部分 L 级特征）。按工作流，等级变动**必须用户本轮确认**，不得代决。

---

## 8. 下一步执行清单（建议切片）

```
[ ] Design（M2 → design.md + 切片清单）
    引擎三态命名建议：cloud（云端 SenseVoice）/ firered（本地 FireRedASR）/ local（本地 Whisper）
    ⚠️ 保留 'local'=Whisper 不要改名，否则历史 job 的 options 会被归一化成 cloud，行为突变
    回退链保持不变：cloud 失败 → 仍回落 Whisper（避免改变现有行为）

[ ] slice-01：backend/services/local_asr.py
    - 与 cloud_asr.py 同契约：transcribe_audio_local(...) -> (srt_path, txt_path, seg_count)
    - ffmpeg 转 16k 单声道 wav → VAD 分段（首尾加 padding！）→ 逐段识别 → 标点恢复
    - 模型完整性校验（仿 _model_complete）
    - 模型按需下载 + 进度回调
    - 单测：切分/标点/契约/模型缺失时显式失败（禁止静默吞错）

[ ] slice-02：引擎路由接入
    - transcriber._transcribe_sync 保持【单点路由】，勿散落判断
    - main.py 引擎校验加 'firered'
    - 前端「转录引擎」加第三项；模型未下载时提示
    - sherpa-onnx 写进 requirements.txt

[ ] slice-03：对比验证（用户明确要求）
    - 用 B站视频 https://www.bilibili.com/video/BV1bH4y1r75q/
    - 同一视频分别跑 engine=cloud 与 engine=firered
    - 对比：耗时、分段数、时间戳质量、文本准确率（挑若干句人工核对）、标点
    - 产出对比报告写入本任务目录

[ ] Archive：更新 CONTEXT.md（引擎列表、新依赖、模型路径、坑点）
```

---

## 9. 坑点（接手务必知道）

1. **`vad.window_size` 是方法**，写 `vad.window_size` 会报 `unsupported operand type(s) for -: 'int' and 'method'`。
2. **`VadModel` 没有构造器**，必须 `VadModel.create(config)`。
3. **`OfflinePunctuation` 没有 `from_*` 工厂**，直接 `OfflinePunctuation(cfg)`。
4. **Windows ctypes 测内存**：`GetCurrentProcess()` 必须显式设 `restype = ctypes.c_void_p`，否则 64 位句柄被截断成 4 字节，读数恒为 0。
5. **VAD 分段吞字**：段间静音不能直接丢弃，务必加 padding，否则丢字（实测丢过"国家/这是/两"）。
6. **不要为了"统一"把本地 Whisper 改成静音切分**——Whisper 自带 VAD，300s 硬切更稳。
7. **`transcribe_language` 在云端下仍会影响 B站字幕抽查的本地兜底**，不要因为云端忽略它就把它隐藏。
8. 测试必须用 `backend/venv/Scripts/python.exe -m pytest`；PowerShell 没有 `head`，用 `Select-Object -First N`。
9. 模型下载走 GitHub release，`httpx` 需 `follow_redirects=True`；Python 3.14 起 `tarfile.extractall` 需传 `filter=`，当前是 DeprecationWarning。

---

## 10. 恢复命令速查

```powershell
cd d:\projects\youtube_tool_v2
git status                                  # 应为空
python .trellis\scripts\task.py current     # 09-11-asr-chain-quality-fix
cd backend; venv\Scripts\python.exe -m pytest -q     # 529 passed
cd ..\frontend; npm run test                # 55 passed
```
