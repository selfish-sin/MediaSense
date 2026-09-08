# 09-09-whisper-device-selector

## Goal

Whisper 转录设备可由用户自选（自动 / CUDA GPU / CPU），选择结果贯穿整条任务流程（单任务、批量、重跑），流程始终跑通。

## Requirements

- 工作台「语音转录」高级选项新增「转录设备」下拉：自动（推荐）/ CUDA GPU / CPU，默认「自动」（= 现状自动探测行为）。
- 后端按显式选择解析设备，选项存于 job `options.whisper_device`（自由 JSON，提交/批量/重跑天然透传，无需 schema 变更）。
- 选 CUDA 但本机不可用（无 GPU / 缺 cublas/cudnn 运行时 DLL）：可见告警 + 自动回退 CPU，任务不失败。
- 选 CPU：跳过 CUDA 探测，直接 CPU。
- 保留既有安全网：GPU 加载失败 / 推理期 CUDA 运行时缺失仍可降级 CPU；`WHISPER_FORCE_CPU` 环境变量继续生效（最高优先级）。
- B站字幕内容抽查 `transcribe_audio_sample` 保持固定 CPU（不依赖 CUDA 环境），不受本选项影响。

## Acceptance Criteria

- [ ] `options.whisper_device ∈ {auto, cuda, cpu}`，非法值回退 auto
- [ ] 显式 cuda + 本机 CUDA 可用 → 实际用 cuda + int8_float16
- [ ] 显式 cuda + CUDA 不可用 → 日志/进度可见告警，回退 cpu 跑完
- [ ] 显式 cpu → 不探测 CUDA，直接 cpu + int8（含 WHISPER_FORCE_CPU 等价）
- [ ] auto → 与原自动探测行为一致
- [ ] 分片转录（>20 分钟长音频）与单段转录行为一致
- [ ] 前端表单可自选，默认 auto；提交后后端日志「设备: cuda|cpu」符合所选
- [ ] backend pytest 全绿（新增设备解析单测）；frontend vitest + vite build 通过

## Non-goals

- 不做全局默认设置项（无设置页；如需全局默认再迭代）
- 不改 DB schema、不加新 API 端点（沿用 options 透传）

## Risks & Rollback

- 风险等级：M0（照已有 options 字段模式加字段，跨后端服务 + 前端表单）
- 回滚：还原 transcriber.py / main.py / HomeView.vue / settingsStore.js 的 whisper_device 相关改动即可；`whisper_device` 缺失时默认 auto，老数据不受影响
