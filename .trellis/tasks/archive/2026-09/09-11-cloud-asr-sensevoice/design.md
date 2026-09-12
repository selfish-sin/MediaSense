# cloud-asr-sensevoice - Design

## 架构

新引擎独立成模块，transcriber 只做路由；引擎选择沿 `options.whisper_device` 同款模式（options 整包透传，无 schema 变更）。

```text
main._process_job_impl
  └ options.transcribe_engine ('auto'|'cloud'|'local', 非法按 auto)
  └ _handle_media_file / _handle_url_input
      └ transcriber.transcribe_audio(..., engine=engine)
          └ _transcribe_sync(engine=...)
              ├ engine/cloud(auto且已配置) → cloud_asr.transcribe_audio_cloud()
              │     成功 → (srt, txt, seg_count)
              │     失败(401/403/404/网络/重试耗尽) → log+progress 告警 → 回退 _transcribe_sync_single
              └ engine/local → 原 faster-whisper 路径（不变）
```

### cloud_asr 内部供复用的两个原子
- `transcribe_audio_cloud(...)`：完整转录（见下）
- `asr_client.transcribe_file(path) -> str`：单文件一次 API 调用（B站字幕抽查复用：90s 样本一次调用，替代本地 base 转写；云端失败由 downloader 回退本地 base，双失败保留字幕——`verify_subtitle_against_audio` 改造，时长校验不动）

### cloud_asr.transcribe_audio_cloud 流程
1. **静音探测**：`ffmpeg -i <audio> -af silencedetect=noise=-35dB:d=0.5 -f null -`，解析 stderr 的 `silence_start/end` → 静音中点作候选切点
2. **切点计划（纯函数 `_plan_cuts`，单测覆盖）**：输入 (总时长, 候选切点)，输出分片区间：
   - 目标段长 6~20s，硬上限 30s（超长且无切点时强制中点二分），下限 3s（过短并入前段）
3. **导出分片**：一次 `ffmpeg -f segment -segment_times t1,t2,... -ac 1 -ar 16000 -c:a libmp3lame -b:a 48k chunk_%03d.mp3`（单片 ≤30s → ≤200KB，远小于 50MB 限制）
4. **逐片上传**：httpx multipart POST（file + model），超时 180s/片（硅基流动 SenseVoice 排队式推理，90s 常 ReadTimeout，冒烟实测放宽后显著减少重试；env `ASR_CLOUD_TIMEOUT` 可调）；429/5xx 指数退避重试 ≤3；片间检查 cancel_check；progress：`10 + 已完成音频秒/总秒 * 88`，消息含 速度/ETA（沿用现有文案风格）
5. **清洗**：去 `<|HAPPY|>`/`<|BGM|>` 等富文本标签（防御性，SiliconFlow 通常已清洗）；空文本分片丢弃（纯音乐/静音段）
6. **分片时长对账**：`export_chunks` 顺带写 `-segment_list csv`，SRT 时间轴按 ffmpeg 实测分片时长累计（探测时长与实际音/视频存在毫秒级偏差，曾致「4 片 vs 计划 3 片」），根治分片数与计划不一致
7. **落盘**：SRT（区间即字幕时间轴）+ TXT（`[MM:SS - MM:SS] 文本`，与现有格式一致）

### 分片级韧性（长视频数百片，单片抖动不应整体回退）
- 单片 Unavailable（429/5xx/网络重试耗尽）→ 跳过该段记 error 日志（含区间），其余分片照常转录，进度条显示「N 片失败」
- 全部分片失败 → 抛 CloudASRUnavailableError，调用方整体回退本地 Whisper
- 部分失败 → 返回部分结果 + 日志/进度告警失败区间（比整条回退本地精度更好）
- CloudASRConfigError（401/403/404）→ 立即抛出整体回退（重试无意义）

### 失败语义（错误必须可见可行动）
- 401/403 → 「ASR 密钥无效或无权限」；404 → 「模型不存在」；均回退本地并在日志给出修复指引
- 429/503 重试耗尽 → 回退本地，日志含 trace-id 响应体
- 回退 = 调 `_transcribe_sync_single`（本地 Whisper，行为与用户显式选 local 完全一致）

## 契约

### GET /api/asr/status（新端点，routers/asr.py）
```json
{ "cloud_configured": true, "model": "FunAudioLLM/SenseVoiceSmall", "provider": "siliconflow" }
```
不回密钥；未配置 → `cloud_configured: false`。前端 HomeView onMounted 拉取，未配置时云端选项禁用并提示。

### options.transcribe_engine
- `'auto'`（后端默认）：云端已配置 → cloud；否则 local
- `'cloud'`：强制云端（失败仍自动回退本地，日志可见）
- `'local'`：本地 Whisper（现行为）

## 数据模型
无变更。options 是自由 dict（同 whisper_device 先例）；密钥只在 `backend/config.py`（gitignored）：
```python
ASR_CLOUD_ENABLED = True
ASR_CLOUD_ENDPOINT = "https://api.siliconflow.cn/v1/audio/transcriptions"
ASR_CLOUD_API_KEY = "<用户提供，只在 config.py>"
ASR_CLOUD_MODEL = "FunAudioLLM/SenseVoiceSmall"
```
`config.example.py` 同段无 key（占位注释）。

## 本地资源整理
删除：`models/whisper_tiny`、`whisper_small`、`whisper_small_broken`、`whisper_medium_broken`
保留：`whisper_base`（`transcribe_audio_sample` B站字幕抽查固定依赖）、`whisper_medium`（本地兜底，前端 settings 默认）
`_select_model` 的 fallback 链引用 tiny/small 时由 `_model_complete` 自然跳过，无需改代码。

## 前端
- HomeView「语音转录」区首行加「转录引擎」select；`engine==='cloud'` 时 Whisper 模型/转录设备两个 adv-field 隐藏（v-if），hint 说明云端不依赖本地模型
- settingsStore：`advancedOptions.transcribe_engine: 'cloud'`；mounted 后按 /api/asr/status 校正（未配置 → 'local' + 禁用云端选项）
- 提交 options 直接带上 transcribe_engine（与 whisper_device 同车）

## 回滚形态
- 引擎默认改回 'local' → 完全恢复现行为（云端代码路径不再触发）
- git revert 单 commit；本地模型目录删除可通过重新下载恢复

## 协作模式
后端先行（引擎+路由+端点+测试），前端随后（UI+store），Verify 阶段真实 API 冒烟 + verify.py 全量。
