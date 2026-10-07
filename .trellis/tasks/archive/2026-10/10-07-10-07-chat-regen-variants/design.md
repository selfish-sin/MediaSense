# 10-07 重新生成与历史回复版本 - Design

## 数据模型（db/database.py，沿用 try/except ALTER 惯例）
```sql
ALTER TABLE job_chats ADD COLUMN variant_group TEXT DEFAULT ''   -- 变体组 id（uuid4 前 12 位）
ALTER TABLE job_chats ADD COLUMN is_active INTEGER DEFAULT 1     -- 组内生效版本
```
存量行兜底：variant_group='' 视为独立组（组员=自身），查询层兜底，无需回填脚本。

## 契约
### POST /{job_id}/chat/sessions/{session_id}/stream（已有，语义扩展）
请求体已有 `regenerate: bool`；regenerate=true 时：
- 不插入新 user 行（现有逻辑）
- 定位该会话最后一条 user 消息 → 其后当前生效的 assistant 行 → 取其 variant_group（无则新造）
- 新 assistant 行落库：variant_group=组id、is_active=1；组内其它行 is_active=0
- done 事件增 `variant_count`（组内总数）
### GET /{job_id}/chat/sessions/{session_id}/history
assistant 消息增：`variant_group`、`is_active`、`variants: [{id, created_at, token_usage}]`（组内全部变体摘要，按 id 升序）
### POST /api/jobs/{job_id}/chat/variants/{msg_id}/activate（新）
组内 is_active 互斥切换；返回 {id, variant_count}。

## 上下文构建（chat.py `_get_session_history`）
上下文查询排除 is_active=0：`... AND (role='user' OR is_active=1)`——后续提问只引用生效版本。

## 删除语义（chat.py）
- DELETE /chat/{msg_id}（单条）：目标是 assistant 且组内 >1 变体 → 只删该变体；若删的是生效版本 → 组内最新一条接管 is_active=1；组空 → 连同所属 user 行删除。目标是 user → 等同整轮（全组+后续）。
- DELETE /chat/from/{msg_id}（整轮/编辑回填）：id >= msg_id 的行全删，天然覆盖全组（变体 id 均 > user 行 id），语义不变。

## 前端（ChatPanel.vue + api.js）
- history 载入后按 variant_group 分组渲染：每个 user 轮挂一个 AI 块，块内按 is_active 选显
- 版本切换器 `‹ 2/3 ›`：显示在 AI 消息 meta 行（仅变体>1）；‹›点击换显 + 异步调 activate（失败 toast，不阻塞换显）
- 重新生成（仅最后一轮可用，否则 disabled+tooltip）：不再 deleteFrom，改发 `{regenerate:true}`；本地把流式中的消息作为「新变体」追加到组尾，版本计数实时 +1；done 后用 assistant_id 落位
- sources/thinking/token/耗时随激活变体整套切换

## 回滚
增量列+新端点，revert 即回旧行为；存量数据不受影响。
