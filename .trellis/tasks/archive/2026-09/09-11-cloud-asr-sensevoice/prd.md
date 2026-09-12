# cloud-asr-sensevoice - PRD

## 需求
当前 faster-whisper 中文转录精度太差，用户要求：接入云端 ASR（SiliconFlow SenseVoiceSmall）作为主力转录引擎，本地 Whisper 降级为兜底；B站字幕内容抽查转写引擎换云端（更准防误杀）；同时整理本地模型资源（删除废弃/低档模型目录）。

## 背景事实（调研结论，2026-09-11）
- SiliconFlow ASR：`POST https://api.siliconflow.cn/v1/audio/transcriptions`，multipart(file, model)，模型 `FunAudioLLM/SenseVoiceSmall`；限制：单次时长 ≤1h、文件 ≤50MB；**响应只有 `{"text": "..."}`，无时间戳**。
- SenseVoiceSmall 中文长音频 CER ~7.8%（Whisper large-v3 ~20%），非自回归极快。
- API Key 用户已在对话中提供，存 `backend/config.py`（gitignored，已验证），**绝不入库、不写入任何 task/spec/文档**。
- 本地硬件：RTX 3060 Laptop 6GB。httpx、ffmpeg 项目已自带，**零新增依赖**。

## 具体例子
给一个 B站视频 URL（无字幕）→ 提交任务（引擎=云端）→ 后端下载音频 → 静音切分成 6~30s 分片 → 逐片调 SiliconFlow API → 得到句级时间戳的 SRT + TXT → 后续分段/摘要流程不变。

## 验收标准
- [ ] 工作台「语音转录」区新增「转录引擎」选择：云端 SenseVoice（默认，推荐）/ 本地 Whisper；选云端时隐藏 Whisper 模型与设备选择器
- [ ] 云端引擎产出合法 SRT/TXT（句级时间戳、序号连续、文本无 `<|xxx|>` 富文本标签），seg_count 与 SRT 条目数一致
- [ ] B站字幕内容抽查（`verify_subtitle_against_audio`）转写引擎改云端优先：云端失败回退本地 base，双失败按现行语义保留字幕；时长校验（第一道闸）不动
- [ ] 后端 `options.transcribe_engine` 全链路透传（单任务/批量/重跑），非法值按 auto
- [ ] 云端失败（网络/429 重试耗尽/401）→ 自动回退本地 Whisper，日志与进度条可见回退原因，任务不中断
- [ ] `GET /api/asr/status` 返回云端配置状态（不含密钥），前端云端未配置时禁用云端选项
- [ ] 取消任务能在分片间中止（TranscriptionCancelledError）
- [ ] 上传上限从 2GB 提高到 8GB（后端 `jobs.py MAX_UPLOAD_SIZE` 与前端 `uploadRules.js` 同步，流式落盘已验证无内存风险；上传/云端的 1h/50MB 限制由静音分片天然绕开）
- [ ] 本地模型清理：删除 `models/whisper_tiny`、`whisper_small`、`whisper_small_broken`、`whisper_medium_broken`；保留 `whisper_base`（B站字幕抽查依赖）与 `whisper_medium`（本地兜底）
- [ ] 后端 pytest 全绿（含新增 cloud_asr 单测：切点计划、标签清洗、SRT 组装、mock httpx 成功/429/401）、前端 vitest 全绿、vite build 通过
- [ ] 真实 API 冒烟：用真实 key 转录一段真实音频成功（用户提供 key 即为授权）

## 不做项
- 不做本地 SenseVoice/FunASR 集成（torch 依赖太重，后续单独立项）
- 不做百炼/火山第二云厂商（接口抽象预留 engine 值空间即可）
- 不做字级/词级时间戳（API 不提供；静音切分≈句级已够字幕用）
- 不改 B站字幕时长校验阈值（现界 0.5x-30s/+120s 是防误杀好字幕的刻意放宽，收紧到 90% 会误杀片头片尾无语音的正常字幕）
- 不做 ASR 用量计费统计页

## 影响范围
- 后端：`services/transcriber.py`（引擎路由）、新文件 `services/cloud_asr.py`、`services/downloader.py`（字幕抽查云端优先）、`main.py`（options 透传）、`config.py`/`config.example.py`（ASR 配置段）、新路由 `routers/asr.py`
- 前端：`views/HomeView.vue`（引擎选择器）、`stores/settingsStore.js`（默认值）、`utils/api.js`（status 拉取）
- 本地资源：`models/` 删 4 个目录

## 风险与回滚
- 风险等级：M1（跨模块全栈，方案清晰，无 schema 变更、无新依赖、无新数据表）
- 不可逆操作：删本地模型目录（可重新下载恢复，非不可逆）
- 回滚方式：引擎默认值切回 local 即恢复原行为；git revert 代码提交
