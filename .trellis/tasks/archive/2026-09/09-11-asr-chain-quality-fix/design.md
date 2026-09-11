# asr-chain-quality-fix（追加范围：FireRedASR 本地引擎） - Design

> 2026-09-11 | 等级 M2（用户本轮已确认） | 三项决策用户已拍板：
> ①新增第三引擎，保留 Whisper；②模型按需下载 + 手动放置兜底；③M2 全流程。

## 0. 追加范围说明（prd 修订）

原 prd「不做项：不接 FireRedASR」基于用户被误转述的原话，用户本人已纠正为
「本地部分**首选**小红书模型」。本 design 只覆盖追加范围（FireRedASR 引擎），
原质量修复 4 commit（`2ce7ffa`/`3b76575`/`d1aac07`/`befe7ac`）已完成，不再重复。

## 1. 架构

```
transcriber._transcribe_sync（路由单点，保持不散落）
  engine == 'cloud'   → cloud_asr.transcribe_audio_cloud（现状不变，失败回退 Whisper）
  engine == 'firered' → local_asr.transcribe_audio_firered（新增）
                          ├─ 模型就绪检查（缺失 → 按需下载，进度可见；下载失败显式报错）
                          ├─ ffmpeg 转 16k 单声道 wav
                          ├─ silero VAD 分段（首尾 +0.15s padding，防吞字）
                          ├─ FireRedASR2-AED 逐段识别（sherpa-onnx int8，CPU 3 线程）
                          ├─ 标点恢复（punct-ct-transformer，0.01s/段）
                          ├─ 复用 cloud_asr.split_sentences + distribute_times 句级化
                          └─ 写 SRT/TXT（与 Whisper/云端同格式）
                          失败（除取消）→ 回退本地 Whisper，原因可见
  engine == 'local'   → Whisper（现状不变）
  engine == 'cloud' 但未配置 → 告警 + Whisper（现状不变）
```

### 引擎三态（命名约束）

- `cloud`（云端 SenseVoice）/ `firered`（本地 FireRedASR2-AED）/ `local`（本地 Whisper）
- **`local` 语义不变**（=Whisper）：历史 job options 已存 `'local'`，改名会被归一化成 `cloud`，行为突变
- 回退链不变：cloud 失败 → Whisper；firered 失败 → Whisper（取消不回退，同现状）

## 2. 模块与契约

### 2.1 `backend/services/local_asr.py`（新增，与 cloud_asr 同契约）

```python
def firered_model_ready() -> bool          # 三件套（encoder/decoder/tokens）+ 标点 + VAD 完整性
def ensure_firered_model(progress_cb)      # 缺失则下载（GitHub release，follow_redirects，
                                           #   tar.bz2 解包；手动放置 models/ 下即跳过下载）
def transcribe_audio_firered(audio_path, progress_callback=None, cancel_check=None,
                             language=None, speaker_diarization=False)
                                           # -> (srt_path, txt_path, seg_count)
```

- 模型路径（复用 `config.WHISPER_BASE`，与 Whisper 同级 `models/`）：
  - `models/firered_asr2_aed/sherpa-onnx-fire-red-asr2-zh_en-int8-2026-02-26/`（encoder.int8 779MB + decoder.int8 398MB + tokens.txt）
  - `models/punct/model.onnx`（281MB）
  - `models/vad/silero_vad.onnx`（0.61MB）
  - 完整性校验仿 `transcriber._model_complete`（关键文件存在且 >0 字节）
- **并发内存互斥**：模块级 `threading.Lock` 串行化 FireRedASR 转录
  （实测峰值 1.7GB；MAX_CONCURRENT_JOBS=2 下两个 firered 任务并发会翻倍；Whisper 不受影响）
- 进度：下载阶段 0~8%（分模型报），识别阶段按已处理音频秒数 10~99%（同云端口径）
- 取消：每段识别前查 `cancel_check`，抛 `TranscriptionCancelledError`
- 错误：不静默吞——下载失败/模型文件损坏/识别异常均上抛（带可行动原因），由路由回退 Whisper
- sherpa-onnx API 坑点见 handoff §9（VadModel.create、vad.window_size() 是方法、OfflinePunctuation 无工厂）

### 2.2 路由与校验（改动点）

- `transcriber.py:_transcribe_sync`：`engine == 'firered'` 分支（惰性导入 `local_asr`，同 cloud 模式；异常回退 Whisper + 可见原因）
- `main.py:773-775` 校验白名单加 `'firered'`；`main.py:448/469` 默认参不变（仍 `'cloud'`）
- `routers/asr.py:/status`：加 `firered_ready` 字段（前端提示用，只读无副作用）
- `requirements.txt`：加 `sherpa-onnx>=1.13.8`

### 2.3 前端（改动点）

- `HomeView.vue` 引擎下拉加第三项「本地 FireRedASR（本地最准）」value=`firered`；
  hint：模型未就绪时「首次使用将自动下载约 1.5GB 模型」，就绪时「中英识别，比云端 SenseVoice 更准，CPU 可跑」
- `firered` 选中时：隐藏 Whisper 模型/转录设备字段（不适用）；说话人分离/转录语言 hint 提示「FireRedASR 支持中英，自动识别」
- `settingsStore.js`：云未配置回落 local 的逻辑不变；`firered` 无需回落（本地无前置配置）
- 下载进度走既有任务进度通道（progress_callback → Socket.IO），不加新端点

## 3. 数据流/数据模型

- 无 schema 变更、无新表。引擎值经 `options.transcribe_engine` 透传（main.py 校验白名单单点）。
- SRT/TXT 输出格式与云端/Whisper 完全一致，下游（分段/摘要/翻译/导出）零感知。

## 4. 切片清单（顺序执行，不跳）

| 切片 | 内容 | 验证 |
|---|---|---|
| **slice-01** | `local_asr.py`（模型就绪检查/下载/ffmpeg 预处理/VAD+padding/识别/标点/句级化写盘）+ config 路径常量 + 单测（句级化复用断言、契约、模型缺失显式失败、下载校验逻辑 mock） | venv pytest 定向全绿；本机模型已就位，真实音频冒烟 1 段 |
| **slice-02** | 路由接入（transcriber/main.py/asr.py）+ 前端第三选项 + requirements.txt + 引擎标签显示 | 后端 pytest 全量 + 前端 vitest + vite build；服务启动后前端选 firered 真跑一个短音频 |
| **slice-03** | BV1bH4y1r75q 对比：同视频 engine=cloud vs engine=firered，对比耗时/分段数/时间戳/文本准确率（抽句人工核对）/标点，报告写 `compare-report.md` | 报告产出 + 用户确认 |
| **Archive** | CONTEXT.md（引擎三态、新依赖、模型路径、坑点）+ spec delta | verify 全绿 |

## 5. 回滚形态

- 新文件 `local_asr.py` 删除即净；路由/校验/前端各一处分支 `git revert` 单 commit 可回
- 模型文件在 gitignored `models/`，不入库不影响仓库
- 句级化复用已测纯函数，无新切分实现

## 6. 协作模式

后端先行（核心价值=转录质量），slice-01 纯后端，slice-02 垂直联调，slice-03 真实端到端。

## 7. 已知风险

- GitHub release 下载在国内网络可能慢/失败 → 手动放置兜底 + 错误文案给手动下载指引（含确切 URL 与目标路径）
- firered 仅支持中/英，其他语种任务选它会差 → 前端 hint 明示「支持中英」
- 首次使用需下载 1.5GB，用户无预期会困惑 → 选中引擎时 hint 预告 + 转录时进度可见
