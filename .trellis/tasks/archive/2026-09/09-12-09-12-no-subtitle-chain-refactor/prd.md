# 无字幕链路结构修复 + 免费优先 - PRD

> 来源：2026-09-12 无字幕链路审查（上一轮会话结论）；用户确认"都要修一修，希望免费使用"。

## 需求
修复无字幕处理链路审查发现的 3 个缺陷与 2 个结构问题，并把默认转录引擎调整为"免费优先"
（云端 XingChen 免费档 → 云端 MiMo → 本地 Whisper），让"免费使用"成为默认路径而非手动操作。

## 具体例子
- 仅配置 ASR_FREE_*（免费档）→ 打开工作台，默认引擎应为「云端 XingChen（免费）」，
  而不是被前端强制落回「本地 Whisper」（当前缺陷 A2）。
- 选 cloud_free 提交 → 日志首条应为「引擎: 云端 XingChen（免费）」（当前显示"云端 MiMo"，缺陷 A1）。
- B站字幕抽查失败（错位字幕）→ 丢弃字幕转转录，结果页「下载素材」能看到「纯音频」（当前丢失，缺陷 A3）。
- B站字幕抽查通过 → 抽查前已下载的完整音频登记为「纯音频」素材（当前白白闲置，B2）。
- 无字幕回退日志不再误称"转Whisper转录"，体现实际引擎（B3 文案）。

## 验收标准
- [ ] `GET /api/asr/status` 返回 `recommended_engine`：free_configured→cloud_free；否则 cloud_configured→cloud；否则 local（4 组合契约测试）
- [ ] 前端默认引擎消费 `recommended_engine`；仅配置免费档时默认=cloud_free（vitest 断言）
- [ ] 转录首条日志/进度与 `_ENGINE_LABELS` 四态一致（cloud_free 不再显示"云端 MiMo"）
- [ ] 音频获取收敛为 `_acquire_audio` 单点：复用预下载/断点续传/正常下载三条路径统一登记 `files['audio']`
- [ ] 抽查通过时已下载音频登记进 `files['audio']`
- [ ] 无字幕/兜底日志不再写死 "Whisper"（中性表述或体现实际引擎）
- [ ] 新增 `_handle_url_input` 路径测试（抽查通过/抽查失败/无字幕/_retry），覆盖本轮三类修复
- [ ] `download_video(max_seconds)` 只下前 90s 片段（失败自动回退完整下载）；单测锁定参数构造与回退
- [ ] 后端 pytest 全绿、前端 vitest 全绿、vite build 通过
- [x] 真网实测免费档质量（10min 素材：21.2s / 零幻觉 / 2109 字，报告见 research/free-asr-eval.md）

## 不做项
- YouTube/平台字幕内容抽查框架（无实测错位案例，避免过度设计）
- 本地媒体文件内嵌字幕/同名字幕识别
- `options.download_video` / `download_video_only` 与字幕路径的冲突（前端无入口，API-only）
- 运行时回退链 `_FALLBACK_CHAINS` 的语义（已有测试锁定，不改）

## 影响范围
- `backend/main.py`（`_handle_url_input` 收敛 + 新增 `_acquire_audio`）
- `backend/services/transcriber.py`（引擎标签复用 `_ENGINE_LABELS`）
- `backend/services/downloader.py`（无字幕文案）
- `backend/routers/asr.py`（`recommended_engine`）
- `frontend/src/stores/settingsStore.js`（默认引擎消费推荐值）
- 测试：`backend/tests/test_main.py`、`test_routers.py`、`test_transcriber.py` 新增用例；前端 vitest

## 风险与回滚
- 风险等级：M1（跨前后端但方案清晰；无 schema 变更、无数据迁移）
- 不可逆操作：无
- 回滚方式：每逻辑单元独立 commit，逐条 `git revert`；`recommended_engine` 为新增字段（旧前端忽略未知字段）
