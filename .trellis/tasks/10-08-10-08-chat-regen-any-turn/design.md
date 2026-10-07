# design（M1，一页）

## 契约

- `POST /api/jobs/{job_id}/chat/sessions/{session_id}/stream`
  - body 新增语义：`regenerate=true` 时可带 `user_message_id: int`（目标提问行）。
    - 传了 → 重生成该轮；行不存在/不是本会话 user 行/该轮无回答 → SSE `error`「没有可重新生成的回答」
    - 不传 → 重生成最后一轮（现行为）
  - SSE `done` 不变（`variant_group`/`variant_count` 已是组级信息）。
- 无新端点；OpenAPI 仅 body schema 变化（`user_message_id` 从占位字段变成有语义）。

## 数据流

```
前端点提问行 ↻
  → streamChat(..., { regenerate: true, user_message_id: <提问行 id> })
  → 后端 _resolve_regenerate_turn(job_id, session_id, user_message_id)
       返回 {question, user_id, prev, group}；缺省 user_message_id → 取最后一轮
  → history = _get_session_history(before_id=user_id)   # 该轮之前
  → _build_messages(..., question=<该轮提问>, regenerate=False)  # 中间轮的提问不在末尾，必须显式带回
  → 回答流式落库：新行 appended（id 最大）；组内旧版本 is_active=0
  → 前端 buildChatTurns() 按轮归位 → 新版本显示在该轮位置
```

## 后端改动（routers/chat.py）

1. `_get_session_history(job_id, session_id, limit=30, before_id=None)`：
   - 取会话内（可选 `id < before_id`）全部行，**按「所属提问行 id，再 id」排序**（中间轮新变体的 id 在会话末尾，
     纯 id 排序会让它的上下文位置错到最后一轮之后）。
   - 仍然只保留 `role='user' or is_active=1`；仍取尾部 `limit` 条。
2. `_resolve_regenerate_turn(...)`：定位目标轮（提问内容 / 提问行 id / 该轮现有回答里生效或最新的一条 / 变体组）。
3. `chat_stream` 的 regenerate 分支改为消费它；`question` 用目标轮提问，`regenerate=False` 传 `_build_messages`
   （与旧参数等价，见 prd 语义表）。

## 前端改动

1. 新增 `utils/chatTurns.js`：`buildChatTurns(rows)` 纯函数——assistant 行归属其前最近一条 user 行（与后端
   `_answer_owner_user_id` 同规则），每轮只展示生效版本（无激活取最新），`variants` = 该组全部行。单测覆盖。
2. `ChatPanel.vue`：
   - `loadHistory` 改用 `buildChatTurns`；`answerByUser` computed 提供「提问行 → 其回答消息」映射。
   - ↻ 移到提问行操作区（回答行移除）；`onRegenerate(userMsg)` 取该轮回答消息做原地流式占位，并带 `user_message_id`。
   - 流式滚动：目标轮是最后一轮 → 保持跳到最新；否则滚到该轮。
3. `utils/api.js`：`streamChat` body 增加 `userMessageId` 透传。

## 测试

- 后端：中间轮重生成 → 该轮组新增变体且生效、后续轮次不动；无效 `user_message_id` → error 事件；
  中间轮重生成后 `_get_session_history` 顺序（新版本紧跟其提问）；最后一轮缺省行为回归（既有用例）。
- 前端：`chatTurns.test.js`（中间轮变体归位 / 无激活取最新 / 无组单行 / 无前置提问兜底）。
- 浏览器：中间轮重生成 → 位置正确、后续轮完整、‹1/2› 可切、刷新保持。

## 回滚

回退 commit；无 DB 迁移。`user_message_id` 缺省路径与旧行为逐字节等价（回归用例锁定）。
