# AI 预算总账（时间 + 上下文）：联网检索总预算 & 聊天上下文预算

## Goal

给两个"无上限"的地方补上明确预算，并把降级结果**可见地**告诉用户：

1. **联网检索链加总预算**：`web_search_collect` 现在是四级串行 await（Ollama 20s → Tavily 20s → Firecrawl 25s → DDGS 25s，未配 Key 的级会跳过），最坏 ~90s（当前配置下 ≈45s）用户只能干等。改为**整条链一个总预算**（默认 25s），每一级只拿到"剩余时间"作为超时，预算用尽即停并如实说明卡在哪一级。
2. **聊天上下文加字符预算**：`_get_session_history(limit=30)` 只按**行数**截断，单条正文一字不截 → 长会话（15 轮 × 实测最长 10.2k 字符回答 ≈ 7.5 万字符）会把 prompt 顶到模型窗口之外硬失败。改为**按轮 + 字符预算**裁剪（默认 24000 字符，从最旧整轮丢弃，最新一轮永远保留），并通过 SSE 新事件 `context` 把"省略了几轮"透出到界面。

## Requirements

- 用户 2026-10-08 确认：按「上下文预算 → 搜索链总预算 → 文档更正」顺序落地（本轮 "可以"）。
- 等级 M1：跨模块（`services/web_search.py` 被 chat 与 research 共用；chat 新增 SSE 事件类型；前端消费），方案清晰、无需切片。

## 语义决策

| 项 | 决策 |
|---|---|
| 预算单位 | 上下文用**字符**（与既有 `_truncate` 的 12000/16000 同量纲，避免引入分词依赖）；检索用**秒**（`time.monotonic()` 单调时钟） |
| 上下文裁剪粒度 | **整轮**：从最旧一轮开始丢，绝不半截丢一轮（避免"有问无答"进上下文）；最新一轮无论多长都保留 |
| 单条超长保护 | 单条 > `HISTORY_MESSAGE_MAX`(16000) 字符时用既有 `_truncate` 打省略标记（与转录原文/结果同款标记），并计入 meta |
| 降级可见性 | 新增 SSE `{"type":"context","omitted_turns":N,"omitted_chars":M,"budget":B}`；前端在消息上方显示一条提示行（不是错误，是"已精简"说明） |
| 检索预算不打折 | 总预算 25s 覆盖**整条链**（不是每级 25s）；每级超时 = `min(该级默认超时, 剩余预算)`；剩余 < 3s 不再启动下一级 |
| 检索失败语义不变 | 仍返回 `SearchOutcome`；预算截断的原因（哪几级没试、还剩多少）拼进 `error`，前端气泡照旧显示 |
| research 路径 | `research_processor` 共用同一默认预算（无需改调用方） |
| 兼容性 | `_get_session_history` **签名与返回值一行不改**（现有 5 处测试直接消费它）——新增 `_history_for_prompt()` 包装层负责裁剪与元信息 |

## 改动清单

- `backend/services/web_search.py`：`SEARCH_TOTAL_BUDGET`(=25s, env `SEARCH_TOTAL_BUDGET_SECONDS`) + `SEARCH_MIN_SLICE`(=3s)；
  `web_search_collect(..., total_budget=SEARCH_TOTAL_BUDGET)` 内部维护 deadline，逐级分配剩余时间，预算耗尽即停并写清原因。
- `backend/routers/chat.py`：`HISTORY_MAX_CHARS`(=24000, env `CHAT_HISTORY_MAX_CHARS`)、`HISTORY_MESSAGE_MAX`(=16000)；
  新增纯函数 `_trim_history_by_budget(rows, max_chars, message_max)` → `(kept, meta)`；
  新增 `_history_for_prompt(job_id, session_id, before_id=None)` = 取历史 + 裁剪；
  `event_generator` 两处取历史改用它，裁剪发生时 yield `context` 事件。
- `frontend/src/utils/chatExport.js`：`contextTrimLabel(info)` 纯函数（文案单点）。
- `frontend/src/components/ChatPanel.vue`：消费 `context` 事件（流式与重生成两条路径），在回答上方渲染提示行。
- `docs/quality-audit-2026-10-07.md`：更正「强制重试备份保护尚未修复」的过期结论（`55480ab` 已修 + 2 个测试）。

## Acceptance Criteria

- [x] 检索总预算：单级慢/失败时总耗时 ≤ 预算 + 单级最小粒度；错误文案说明被截断
- [x] 聊天上下文：超预算时丢最旧整轮、最新一轮完整保留；meta 计数准确；未超预算时行为与改前逐字节一致
- [x] `context` 事件只在真的裁剪时发出；前端提示行可读且不误导（不是错误）
- [x] 既有用例全绿（后端 1075 / 前端 293；`_get_session_history` 契约未变）
- [x] 门禁：pytest / vitest / lint / build 全绿

## 测试

- 后端新增：`_trim_history_by_budget`（不超预算原样、超预算丢整轮、最新轮保留、单条超长打标记、meta 计数）、
  `_history_for_prompt`（与 `_get_session_history` 组合）、SSE `context` 事件（裁剪时发、不裁剪时不发）、
  `web_search_collect` 预算（剩余时间分配、耗尽即停、错误文案含预算、未超预算路径回归）。
- 前端新增：`contextTrimLabel` 文案用例。

## 回滚

回退 commit 即可，无 DB 迁移。两个预算均为常量 + 环境变量，可临时调大回退到"无预算"行为。

## Notes

- 明确不做：不引入 token 计费/分词（字符预算已足够解决问题且与既有量纲一致）；不改模型上下文内容排序（上一任务刚锁定）；
  不做按模型的差异化窗口（模型表没有 context 字段，先不做契约扩张）。
- 提示行只在本次流式回答时显示，**刷新后不保留**（`context` 事件与 `search` 状态同款不落库）；要长期可见需先设计持久化字段。
- 提示行位置：渲染在回答正文之下、来源入口与追问建议之上（同 search 状态气泡），不是消息顶部。

## 验收证据（2026-10-08）

- **门禁**：`python verify.py all` → ruff + pytest（1075）+ eslint + vitest（293）+ build 全绿；`ALL CHECKS PASSED`。
- **OpenAPI 零 diff**：重导 `backend/scripts/export_openapi.py` 仍为 `paths=80`，`contracts/openapi.json` 无改动
  （本次只新增 SSE 事件类型与内部纯函数，未动 REST 契约）。
- **密钥接入实测**：Tavily / Firecrawl Key 只写入 gitignored `backend/config.py`（不入库、不进文档）；
  `describe_search_backends()` 现为 ollama/tavily/firecrawl 全 configured + ddgs available。
- **检索真实链路**：`web_search_collect("中医 附子 用法 用量")` → `provider="ollama"`、4 条来源、1.8s（远在 25s 预算内）；
  `total_budget=0.5` → 四级全部"未尝试（总预算已用尽，剩余 0.5s）"，总耗时 0.0s（不再串行白等）。
- **上下文真实链路**：临时造 job/session（历史 2 轮 × 18006 字符 > 24000 预算）→ 真实服务流式追问：
  SSE `{"type":"context","budget":24000,"omitted_turns":1,"omitted_chars":18012,"kept_messages":2}` →
  `search` ok（provider=ollama，5 条来源）→ 模型正常作答；测试行已清理（残留 0）。
- **浏览器 UI**：结果页追问面板实测回答下方出现提示行「已省略较早的 1 轮对话（超出上下文预算 24k 字符），仅最新轮次完整参与提问」，
  虚线 chip + 剪刀图标，不与正文/来源/追问建议重叠。
- **踩坑**：运行中的 7001 服务启动于 10:53，而代码 11:17/11:24 才改 → 浏览器会测到旧行为；
  必须 `taskkill` + 重启（无 `--reload`）并用 `netstat` + 接口响应双验证后再验收。
