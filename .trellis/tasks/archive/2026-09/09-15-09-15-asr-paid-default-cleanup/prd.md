# ASR 付费档默认值污染清理 - PRD

> 等级：M1（跨模块：main.py / services.transcriber / services.cloud_asr / 前端 settingsStore + 测试）

## 需求

我要解决什么问题：**付费云端 MiMo（¥0.5/h）的额度在用户完全没有主动选择它的情况下被消耗掉了。**

用户原话：「MiMo 不是效果最好吗？但它之前因为太浪费了没用它。它也能搭上它的线，就非常浪费，
浪费最后没用多少就没了。现在是要清理那个浪费和污染。」

诉求 = 让**付费档只能被用户显式选择触发**；任何默认值、兜底、一次性自动调用都不得落到付费档。

## 证据（2026-09-15 审计）

1. **实际泄漏点**：B站字幕内容抽查（每个 B站 任务都会跑一次）此前调用
   `cloud_asr.transcribe_file(sample_mp3)` 未传 provider → 落到函数默认值 `provider='mimo'`（付费档）
   → 每个任务静默烧一次付费额度，且任务 options 里查不到（不落在 jobs 表）。
   日志实证（`backend/data/logs/backend.log`，修复提交前）：
   ```
   09-15 14:15:26  POST https://api.xiaomimimo.com/v1/chat/completions "HTTP/1.1 402"
   09-15 14:15:26  services.cloud_asr: 云端 ASR 服务错误（第 1/3 次）
   ...（第 3/3 次）→ 回退本地 Whisper base
   ```
2. 历史任务引擎分布（`data/youtube_tool.db`）：`<缺失> 144 / cloud_free 32 / cloud 1`
   —— 说明付费档的消耗**主要不来自显式选择**，而是来自 1 这类不落库的自动调用。
3. **同类未清理的默认值**（都是「忘传参数 = 付费」的隐患）：
   - `main.py:799-801`：`options.get('transcribe_engine', 'cloud')`、非法值 → `'cloud'`（付费）
   - `_handle_media_file` / `_handle_url_input` 形参默认 `transcribe_engine: str = 'cloud'`
   - `transcriber._transcribe_sync` / `transcribe_audio` 形参默认 `engine='cloud'`；
     `_fallback_chain` 未知引擎 → `_FALLBACK_CHAINS['cloud']`（付费链）
   - `cloud_asr.transcribe_file(provider='mimo')`、`transcribe_audio_cloud(provider='mimo')`
   - 前端 `settingsStore.advancedOptions.transcribe_engine` 占位值 `'cloud'`（付费）
4. 2026-09-15 已修（`2daca1e`）：抽查改走 `preferred_provider()`（免费优先）+ 402 立即抛不重试。
   但**服务当时跑的是旧代码**（12:52 启动，修复 14:24 提交），该修复从未真正跑过。

## 具体例子

- 输入：给任意 B站 任务（用户未改任何引擎设置）→ 期望：抽查只用**免费档**；付费档零调用。
- 输入：调用方漏传 `transcribe_engine`（旧前端/脚本/API 直调）→ 期望：落到后端推荐引擎（免费优先），
  绝不隐式落到 `cloud`（付费）。
- 输入：免费档不可用（停用/缺密钥）→ 期望：抽查回退**本地 Whisper**，而不是偷偷用付费档。
- 输入：用户显式把「转录引擎」选成「云端 MiMo-ASR」→ 期望：正常使用付费档（这是唯一入口）。

## 验收标准

- [ ] `cloud_asr` 自动路径（抽查/默认 provider）**只返回免费档**；免费档不可用返回 None（回退本地），
      不再返回付费档。
- [ ] `transcribe_file` / `transcribe_audio_cloud` 的 provider 参数不再默认 `'mimo'`；
      未指定时走免费档，无可用免费档 → 立即抛可行动 ConfigError（不静默付费）。
- [ ] `main.py` 缺省/非法 `transcribe_engine` → 走 `routers.asr.recommended_engine()`（免费优先），
      不再硬编码 `'cloud'`。
- [ ] `transcriber` 的 `engine` 缺省与未知值 → 落「永不回付费」的 `cloud_free` 链。
- [ ] 前端工作台引擎占位不再以付费档为默认（引入 `engineTouched`：只有用户没碰过才消费推荐值）。
- [ ] 新增测试锁定上述不变量；原有用例中「免费档不可用 → 用付费档」的断言按新策略改写。
- [ ] 全量后端 pytest 绿、前端 vitest + lint + build 绿。
- [ ] spec（transcription-chain）+ CONTEXT.md 同步。

## 不做项

- 不改用户 DB 里已配的 provider（不自动停用 MiMo）——是否停用由用户在「模型管理 → ASR 模型」决定。
- 不改付费档 `cloud → cloud_free → firered → local` 的回退链（用户显式选付费档时仍可用）。
- 不做「欠费自动熔断」（本轮不引入新状态机；默认值去付费化已消除自动路径）。
- 不动历史 jobs 的 options（仅 1 条 `cloud`，无实际影响）。

## 影响范围

- 后端：`services/cloud_asr.py`、`services/transcriber.py`、`services/downloader.py`、`main.py`
- 前端：`stores/settingsStore.js`、`components/HomeAdvancedOptions.vue`
- 测试：`tests/test_cloud_asr.py`、`tests/test_downloader.py`、`tests/test_transcriber.py`（+ 前端 settingsStore 用例）
- 文档：`.trellis/spec/backend/transcription-chain.md`、`CONTEXT.md`

## 风险与回滚

- 风险等级：M1
- 不可逆操作：无（纯代码 + 无数据迁移）
- 回滚方式：`git revert` 对应提交；provider 配置未动，行为可平滑回退
- 行为变化提示：抽查在「免费档不可用」时改为走本地 Whisper（原先会用付费档）——这是本次诉求的
  直接体现（校验不该烧钱），需在文档中写明。
