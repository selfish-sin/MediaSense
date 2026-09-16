# fix-chat-web-search - Design

## 关键外部事实（research，2026-09-16 抓取官方文档）

智谱 Web Search 现行 API 是**独立端点**，不是老代码里用的 `chat/completions + tools=[{type:web_search}]`：

```
POST https://open.bigmodel.cn/api/paas/v4/web_search
Header: Authorization: Bearer <key>
Body:   { search_query(≤70字符), search_engine, search_intent(bool, 必填),
          count(1-50), content_size(medium|high), search_domain_filter, search_recency_filter }
200:    { search_result: [{ title, content, link, media, icon, refer, publish_date }] }
Err:    { error: { code, message } }
```

`search_engine`: `search_std` | `search_pro` | `search_pro_sogou` | `search_pro_quark`

结论：**搜索与「让模型总结」解耦** —— 搜索只取原始链接/摘要，总结交给用户自己选的 AI 模型（追问链路本来就这么用，system prompt 已要求 `[1][2]` 引用编号）。这同时消除了对智谱 chat 模型的依赖（用户只需一个 Key，不需在 /models 配智谱模型）。

## 架构：单一事实源

```
追问 / 深度研究
      │
      ▼
services/web_search.py   ← 唯一搜索实现
  ├─ resolve_zhipu_key()      三级兜底：env → config.py → DB(bigmodel 端点模型)
  ├─ zhipu_search_api(q,n,..) 独立 web_search 端点（主路径）
  ├─ ddgs_search(q,n)         DuckDuckGo 免费兜底（无 Key 场景）
  ├─ search_web(q,n,engine)   → List[result]    （routers/search.py 复用，签名不变）
  └─ web_search_collect(q,ctx) → SearchOutcome  （sources / text / provider / error）
```

`SearchOutcome`（dataclass）：

| 字段 | 说明 |
|---|---|
| `sources` | `[{title,url,snippet}]`，前端「引用来源」直接消费 |
| `text` | 编号后的资料文本，注入回答模型上下文 |
| `provider` | `zhipu` / `ddgs` / `''`（都没成功） |
| `error` | 人类可读原因（`''` 表示无错误）——**失败可见化的载体** |

删除 `research_processor` 里内联的第二份智谱调用（`_zhipu_web_search`）与 `web_search_collect` 重复实现。

## 契约变更（后端 → 前端 SSE）

`POST /api/jobs/{id}/chat/sessions/{sid}/stream` 新增事件（在既有 `thinking/content/sources/done/error` 之外）：

```json
{ "type": "search", "status": "searching" }
{ "type": "search", "status": "ok",      "provider": "zhipu", "count": 5 }
{ "type": "search", "status": "failed",  "message": "未配置智谱 Key，且 DuckDuckGo 兜底不可用（ddgs 未安装 / 网络不通）" }
```

向后兼容：新增事件类型，旧前端 `onEvent` 的 type 分支不匹配即忽略，不破坏既有行为。

HTTP 端点零变化 → `contracts/openapi.json` 无需重导出。

## 数据模型

无变化。不改 `job_chats` / `chat_sessions`，无迁移。

## 依赖

- 新增 `ddgs`（DDGS 兜底，免 Key）→ `requirements.txt` + `start.bat` 依赖探测清单同步（坑点：探测清单不同步会导致启动不自动补装）
- **移除** `zai` SDK 调用路径（HTTP 已覆盖，且 SDK 响应无 `web_search` 字段类型定义，解析不可靠）
- 智谱搜索模型常量 `glm-4-flash`/`glm-4.5-air` 不再需要（不走 chat），删除

## 深度研究（同根因，一并修）

`_expand_with_zhipu`（智谱 chat+tools 写段落）与 `_expand_with_ddgs`（DDGS + `_call_ai` 写段落）两条支路合并为一条 `_expand_topic`：

```
搜索主题（web_search.search_web，智谱→DDGS）
   → 拼进提示词 → _call_ai（用户自己选的模型）→ 研究段落
```

收益：单一搜索实现；不再依赖智谱 chat 模型；去掉脆弱的 `'暂不可用' in section` 字符串判定；搜索 0 条时如实写入「（无搜索结果）」而不是整段「暂不可用」。

## 回滚形态

逐单元独立 commit，`git revert` 对应提交即可。`ddgs` 为新增可选依赖，缺失时兜底路径降级为「失败可见」而非崩溃。SSE 新事件为增量契约，回滚前端不影响后端。

## 协作模式

后端先行 → 前端消费新事件（垂直切片，3 个切片）：
- slice-1：搜索链路单一化（web_search.py + research_processor + ddgs 依赖）
- slice-2：追问失败可见化（routers/chat.py SSE 事件 + ChatPanel 展示）
- slice-3：深度研究同根因收敛
