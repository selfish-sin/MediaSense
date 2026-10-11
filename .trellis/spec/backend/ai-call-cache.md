# AI 调用与缓存边界（2026-10-07）

## 1. 范围与触发

改 `services/ai_processor.py` 的流式/非流式调用、重试、缓存开关，或
`services/ai_cache.py` 的缓存故障降级时读取。复现证据见
`docs/quality-audit-2026-10-07.md`；下文同时约束 AI 与 ASR v2 缓存。

## 2. 签名

- `_call_ai(model_cfg, prompt, ..., state=None, bypass_cache=False) -> (content, in_tok, out_tok)`。
- `_post_chat_once(model_cfg, payload, timeout, bypass_cache=False, ...) -> (content, in_tok, out_tok)`。
- `cache_get(key) -> dict | None`；`cache_put(key, op, model, content, meta=None) -> bool`。
- 不新增 API 或 DB schema；`state` 是可选出参，不能是重试守卫的前置条件。
- `transcription_key(audio_path, engine, language, speaker_diarization, context=None) -> str`。
- `_transcription_cache_key(audio_path, engine, language, speaker_diarization, model_size, platform, device) -> str`。
- `lookup_transcription(..., *, cache_key=None) -> dict | None`。
- `store_transcription(..., *, cache_key=None, actual_engine=None) -> bool`。
- `_transcribe_sync(..., execution_state=None) -> (srt_path, txt_path, seg_count)`；
  内部可选出参 `execution_state['engine']` 记录成功的整体引擎，不改变三元组。

## 3. 契约

- `bypass_cache=True` **仅跳过读取**：请求必须发生；成功非空新结果仍写同一个键，替换旧结果。
- 缓存命中 token 消耗为 0；失败/空白结果不替换缓存；`AI_CACHE_ENABLED=False` 禁止读写。
- 流式键：`llm + endpoint + model_id + temperature + max_tokens + prompt`。
- 非流式键：`llm_once + endpoint + model_id + sort_keys 序列化 payload`。
- 流式函数内部始终维护 `emitted/content`，并在传入 state 时回填。
  当前 `emitted` 在收到非空正文 chunk 时置 True，早于 socket flush；因此重试策略偏保守。
- ASR 键前缀 `asr_v2`，包含音频 SHA256、请求引擎、语言、说话人分离和排序 JSON 上下文：
  `model_size/platform/device`；回退链 provider 的 `key/enabled/endpoint/model/api`；
  Whisper/ONNX 模型相关文件的路径、size、mtime_ns。白名单不包含 api_key/cookie。
- `transcribe_audio` 在线程内一次生成读写共用键；身份生成失败会告警并跳过本次缓存。
  未启用缓存时不计算文件哈希；缓存命中前仍检查取消。
- 写入前在线程内重新核对身份；provider、模型文件或音频在识别期间有变化时告警并跳过。
  写入始终沿用最初捕获的键，不能直接改用新配置键冒认实际识别配置。
- `actual_engine != requested_engine` 的整体降级结果正常返回，**不写入首选引擎缓存**。
  provider 内部分片补录仍属于该 provider；实际引擎仅表示整体路由，不宣称逐片溯源。
  临时故障恢复后再次尝试首选；元数据 `model/actual_engine` 使用实际引擎。
- 旧 `asr` 键不读取，历史记录保留；换模型配置会产生新键，无数据库迁移/删除。
- 字幕键前缀 `subtitle_v1`，含规范化视频标识与语言，**不含音频哈希**（字幕路径不下载音频）。
  只有已判定正确的字幕才入库（B站/抖音抽查 `passed`；YouTube 抓取成功）；
  `bypass_cache` 只跳过读取、成功仍写回；命中不重新抽查（缓存条目当初已验证）。
  读写在 `main._handle_url_input`，三平台分支前读、抽查/抓取成功后写。

## 4. 验证与错误矩阵

| 条件 | 行为 |
|---|---|
| 零正文断连 | 可按 max_retries 重试；耗尽返回连接断开哨兵 |
| 收到正文后断连（传或不传 state） | 不重试，返回 `[处理失败: 连接中断（已输出部分内容）]` |
| 总超时 | 不重试，返回超时哨兵 |
| 用户取消 | TaskCancelledError 上抛，不缓存、不重试 |
| 缓存建连/查询/写入失败 | warning 含原因，返回 None/False，不阻断主流程 |
| 同音频同配置 | ASR 命中并写回 SRT/TXT；不运行推理 |
| 换模型档位、provider、端点、model、API 或模型文件签名 | ASR 未命中，重新识别 |
| 整体引擎降级 | 结果可交付，不冒充首选缓存，下次恢复可重试首选 |
| ASR 身份生成失败 | warning，继续识别，本次不缓存 |
| 完成时 ASR 身份与开始时不同 | warning，结果正常返回，不写旧键或新键 |

缓存异常边界必须包含 `get_connection()`，不能只保护 execute；仅在连接已取得时 close。

## 5. 正常、基准与异常案例

- 正常：第一次 A → 忽略缓存生成 B → 正常调用得到 B；总共两次 HTTP。
- 基准：相同参数二次命中、token=0；缓存关闭时都走真实调用。
- 异常：推送十个正文 chunk 后 ReadError → 只请求一次，保留断连原因，无 NameError。
- ASR：base → medium → medium，推理只发生两次，第三次命中 medium。
- 降级：cloud_free 失败 → local 成功 → 下次 cloud_free 恢复，第二次必须调用 cloud_free。

## 6. 必须保留的回归

- `TestAiBypassRefresh`：真实调用 + MockTransport，流式/非流式均断言三次调用结果和请求次数。
- `TestCallAiStreamDisconnect`：真实异步流，覆盖有/无正文 × 有/无 state，检查 socket 输出不拼接。
- `TestCachePutGet.test_connection_failure_is_optional`：OperationalError 建连注入，读写降级且告警可见。
- 用例不能 mock 掉 `_call_ai` 本身来声称覆盖其内部重试与缓存分支。
- `TestTranscriptionIdentity`：换 Whisper 档位、provider key/端点/model/api、降级恢复、旧 v1 键；
  真实异步入口 + SQLite 缓存 + 临时结果文件，只替换识别边界，并断言推理次数与最终文本。
- `test_config_changed_during_inference_does_not_poison_old_identity`：识别期间换 model，恢复旧配置后必须重新推理。
- `TestTranscribeAudioBypass`：忽略缓存只跳过 lookup，仍 store；
  整体降级不写成首选缓存由 `TestTranscriptionIdentity.test_fallback_does_not_cache_as_primary` 验证。

## 7. 错误与正确写法

```python
# 错误：跳过读时也失去写入键；绕过 get_connection 的异常保护。
if not bypass_cache:
    cache_key = make_key(...)
conn = get_connection()
try:
    ...

# 正确：读写语义分离；将建连包含在缓存降级边界中。
cache_key = make_key(...)
cached = None if bypass_cache else cache_get(cache_key)
conn = None
try:
    conn = get_connection()
    ...

# 错误：用请求引擎存整体降级结果，临时故障变成永久复用低质量结果。
store_transcription(..., actual_engine=requested_engine)
# 正确：读写共用捕获的 v2 身份，并检查实际整体引擎。
if actual_engine == requested_engine:
    store_transcription(..., cache_key=cache_key, actual_engine=actual_engine)
```
