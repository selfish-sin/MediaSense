# fix-chat-web-search - PRD

## 需求

追问面板（`ChatPanel`）的「联网」开关是死按钮：打开后 AI 回答与关闭时无任何差异，不产生引用来源，也没有任何错误提示。要修到「打开后真的会联网检索、并把来源与失败原因如实展示」。

## 具体例子

- 输入：已完成任务的「追问」面板，打开「联网」开关，问「最近这个领域有什么新进展？」
- 期望：回答带 `[1][2]` 引用编号 + 末尾「参考来源」列表 + 气泡下方「引用来源 (N)」；右侧日志/Socket 有搜索过程与结果条数
- 实际（修复前）：与关闭开关完全一致，无来源、无提示、无报错
- 期望（失败时）：气泡下方显式提示「联网搜索失败：<真实原因>（已改用模型自身知识回答）」，不再静默

## 根因（已定位，四层叠加）

1. `research_processor.web_search_collect` 走模块级 `ZHIPU_API_KEY`（`config.py:68` 只读环境变量），运行时为空 → 智谱主路径被跳过。
2. DDGS 兜底 `import ddgs` 失败——`ddgs` 既不在 `requirements.txt` 也不在 `start.bat` 探测清单，环境未安装。
3. `research_processor.py:146` 把智谱 `message.web_search` 当 dict 解析（`msg.get("web_search", {}).get("results")`），实际是 list → `AttributeError` 被 `except` 吞掉，即使配了 Key 也拿不到来源。
4. 全程静默：无来源时返回 `([], "")`，SSE 无任何 search 状态事件，前端无从感知。

另有重复实现：`services/web_search.py` 已有正确的 `search_web()`（三级 Key 兜底 + list 解析），但追问链路没复用它。

## 验收标准

- [ ] 配置智谱 Key（env / `config.py` / `/models` 里 bigmodel 端点模型任一）后，打开联网开关 → 回答带引用编号与「参考来源」列表，气泡显示「引用来源 (N)」，socket 日志出现搜索条数
- [ ] 未配智谱 Key 时自动退化到 DDGS 免费兜底（环境能连通时同样出结果）
- [ ] 两条路径都不可用时，前端气泡显式展示失败原因 + 「已用模型自身知识回答」，socket 日志有 warning，**不再静默**
- [ ] 联网关闭时行为零变化（不触发任何搜索）
- [ ] 「深度研究」模式（`research_process`）同样走统一 Key 解析，配 Key 后不再落到 DDGS/「暂不可用」
- [ ] 搜索实现单一化：`services/web_search.py` 是唯一实现，`research_processor` 不再内联第二份智谱调用
- [ ] 后端 pytest / 前端 vitest / lint / build 全绿
- [ ] 后端 `requirements.txt` 与 `start.bat` 探测清单同步含 `ddgs`

## 不做项

- 不做联网开关的 UI 重构（保持现有 tooltip + switch 位置）
- 不新增可供用户选择的搜索引擎（沿用 `engine` 参数默认 `pro`）
- 不改 `research_process` 的研究流程编排（只统一 Key 解析与调用实现）
- 不改 `job_chats`/`chat_sessions` 数据模型，不新增数据库迁移

## 影响范围

- `backend/services/web_search.py`（收敛为唯一实现）
- `backend/services/research_processor.py`（删重复实现、改 Key 解析）
- `backend/routers/chat.py`（SSE 新增 `search` 状态事件）
- `frontend/src/components/ChatPanel.vue`（消费 `search` 事件并展示）
- `backend/requirements.txt` + `backend/start.bat`（ddgs 依赖）
- `backend/tests/`（新增联网链路单测）
- 不影响：转录链、任务编排、模型管理、导出

## 风险与回滚

- 风险等级：M1（跨模块 + 后端→前端 SSE 契约新增事件，无 schema 变更、无数据迁移）
- 不可逆操作：无
- 回滚方式：`git revert` 对应提交；`ddgs` 为新增可选依赖，卸载不影响既有功能；`search` 事件为新增类型，旧前端遇到会忽略（`onEvent` 有 type 分支兜底）
- 附带风险：DDGS 在国内网络可能不可达 → 已通过「失败可见化」降级为可用提示，而非静默
