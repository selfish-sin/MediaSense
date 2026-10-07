# AI 调用与缓存边界（2026-10-07）

## 1. 范围与触发

改 `services/ai_processor.py` 的流式/非流式调用、重试、缓存开关，或
`services/ai_cache.py` 的缓存故障降级时读取。复现证据见
`docs/quality-audit-2026-10-07.md`；以下是已落地契约，ASR 键扩展另行设计。

## 2. 签名

- `_call_ai(model_cfg, prompt, ..., state=None, bypass_cache=False) -> (content, in_tok, out_tok)`。
- `_post_chat_once(model_cfg, payload, timeout, bypass_cache=False, ...) -> (content, in_tok, out_tok)`。
- `cache_get(key) -> dict | None`；`cache_put(key, op, model, content, meta=None) -> bool`。
- 不新增 API 或 DB schema；`state` 是可选出参，不能是重试守卫的前置条件。

## 3. 契约

- `bypass_cache=True` **仅跳过读取**：请求必须发生；成功非空新结果仍写同一个键，替换旧结果。
- 缓存命中 token 消耗为 0；失败/空白结果不替换缓存；`AI_CACHE_ENABLED=False` 禁止读写。
- 流式键：`llm + endpoint + model_id + temperature + max_tokens + prompt`。
- 非流式键：`llm_once + endpoint + model_id + sort_keys 序列化 payload`。
- 流式函数内部始终维护 `emitted/content`，并在传入 state 时回填。
  当前 `emitted` 在收到非空正文 chunk 时置 True，早于 socket flush；因此重试策略偏保守。

## 4. 验证与错误矩阵

| 条件 | 行为 |
|---|---|
| 零正文断连 | 可按 max_retries 重试；耗尽返回连接断开哨兵 |
| 收到正文后断连（传或不传 state） | 不重试，返回 `[处理失败: 连接中断（已输出部分内容）]` |
| 总超时 | 不重试，返回超时哨兵 |
| 用户取消 | TaskCancelledError 上抛，不缓存、不重试 |
| 缓存建连/查询/写入失败 | warning 含原因，返回 None/False，不阻断主流程 |

缓存异常边界必须包含 `get_connection()`，不能只保护 execute；仅在连接已取得时 close。

## 5. 正常、基准与异常案例

- 正常：第一次 A → 忽略缓存生成 B → 正常调用得到 B；总共两次 HTTP。
- 基准：相同参数二次命中、token=0；缓存关闭时都走真实调用。
- 异常：推送十个正文 chunk 后 ReadError → 只请求一次，保留断连原因，无 NameError。

## 6. 必须保留的回归

- `TestAiBypassRefresh`：真实调用 + MockTransport，流式/非流式均断言三次调用结果和请求次数。
- `TestCallAiStreamDisconnect`：真实异步流，覆盖有/无正文 × 有/无 state，检查 socket 输出不拼接。
- `TestCachePutGet.test_connection_failure_is_optional`：OperationalError 建连注入，读写降级且告警可见。
- 用例不能 mock 掉 `_call_ai` 本身来声称覆盖其内部重试与缓存分支。

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
```
