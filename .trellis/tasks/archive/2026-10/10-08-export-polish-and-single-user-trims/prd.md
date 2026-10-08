# 导出成品化 + 单用户场景的三处摩擦清理

## Goal

按「本地单用户」视角重审设计点后，只做真正值得做的五件：

1. **导出成品化（主件）**：Word/PDF 现在把 Markdown 抹平——`routers/export.py` 的 docx 是按行手工解析，
   `**加粗**`/`*斜体*` 被正则剥掉、列表标记不处理、**表格完全不认**；fpdf2 的 PDF 同样只有标题与项目符号。
   四个格式各写了一套解析（docx 行循环、fpdf 行循环、`_md_to_plain_text` 正则、`_md_to_html`），
   改成**一份解析（markdown-it token）→ 四个渲染器**，并补"给人看的"元信息与目录。
2. **历史/队列内联失败原因**：列表接口早已返回 `error`（`db/job_repo.py:114` 的列清单里有），
   但 `HistoryView`/`JobQueueList` 都没渲染，失败任务必须点进去才知道为什么。
3. **追问偏好记忆**：`ChatPanel.vue:379` 的 `searchMode = ref('off')` 零持久化，
   每次刷新/换会话都要手动切到「严格依据材料」。
4. **追问超时并入预算体系**：追问流是 180s 硬超时（`routers/chat.py:37`），批量总结能到 600s——
   同一模型在批里跑得完、在追问里被断，是"莫名其妙失败"的一类。
5. **删掉没人调的旧 `/api/search`**：`frontend/src/utils/api.js:301` 定义了 `searchApi` 但全前端零调用方，
   后端 `services/web_search.py:search_web()` 也只剩它一个调用者；`search_web` 失败静默返回空列表，
   正是上一轮点名的"死按钮风险面"。删端点 + 删这层静默包装。

## Requirements

- 本轮用户授权范围：上一轮给出的「①导出成品化 ②历史内联失败原因 ③追问偏好记忆（+顺手④超时⑤删旧端点）」，
  用户回复"开始"。
- 等级 **M1**（跨模块但方案清晰、无需切片）：改 export 路由与服务、chat 路由、两个前端视图、删一个路由模块。
  不涉及 DB schema、不做数据迁移、可整段回滚（`git revert`）。
- 导出不得新增第三方依赖：markdown-it-py 已在 `requirements.txt`（4.2.0，仓库锁定），python-docx / fpdf2 已在用。
- 元信息只放任务自身字段，不外发任何内容。

## 语义决策

| 项 | 决策 |
|---|---|
| 解析单点 | markdown-it（commonmark + table）token 流是唯一解析器；四个格式只消费 token，不再各写正则/行循环 |
| 元信息 | 导出件顶部一块「文档信息」：来源平台、来源链接、模型、任务创建时间、正文字数、标签、转录来源（引擎 + 字幕段数，取 `transcript_meta`） |
| 时间处理 | DB 的 `created_at` 是 **UTC 无时区标记**（实测：本地 11:39 写行存 03:39），导出件必须转北京时间再写，否则整整差 8 小时 |
| 目录 | 仅当二级/三级标题 ≥3 个时插入「目录」（静态列出标题，不假装有页码）；docx 与 HTML 有，PDF/TXT 不插 |
| docx 保真度 | 标题层级、**加粗/斜体/行内代码**、无序/有序列表（Word 原生 List Bullet/List Number 样式）、表格（真表格 + 表头加粗）、引用、代码块、水平线 |
| PDF 保真度 | fpdf2 手写渲染：标题分级、加粗（字体切 B）、列表带符号/序号、表格按列数平分宽度画边框；不做目录 |
| txt | 由同一 token 流生成纯文本：保留列表序号（现在 `_md_to_plain_text` 把 `1.` 直接删掉）、项目符号 `•`、去标记但不丢结构 |
| 失败原因呈现 | 历史表格/队列行的失败状态显示一行原因（截断 ~60 字 + `title` 悬停全文）；空失败回退「失败（无错误详情）」 |
| 追问偏好存储 | `localStorage`（键 `ms.chat.prefs`）：联网模式 + 思考开关；读取失败/取值非法一律回退默认 `off`/`false`（写失败只记 warn，不静默吞） |
| 追问超时 | 抽 `CHAT_MODEL_TIMEOUT_SECONDS`（默认 **600**，与批量流式对齐）+ `CHAT_MODEL_CONNECT_TIMEOUT_SECONDS`（15）；超时文案点明「模型 N 秒内没有返回内容」并可行动 |
| 删旧搜索端点 | 删 `routers/search.py` + `main.py` 注册 + 前端 `searchApi` + `web_search.search_web`/`format_search_context`（确认无其他调用者后）；OpenAPI 80 → 79，需重导 |

## 改动清单

- **新增** `backend/services/md_render.py`：`doc_meta(job)`、`doc_toc(tokens)`、`render_docx(job, text, path)`、
  `render_html(job, text)`、`render_text(job, text)`、`render_pdf(job, text, path)`；内部 `_tokens(text)`、`_inline_runs(children)`、`_fmt_local(utc_str)`。
- **改** `backend/routers/export.py`：四个端点变薄（校验 + 调用 + FileResponse）；删除 `_md_to_plain_text`、`_md_to_html`、
  docx 行循环、fpdf2 行循环；PDF 保留 weasyprint 优先 → fpdf2 兜底。
- **新增** `backend/tests/test_export_render.py`：元信息/时间转换/目录阈值、docx 读回断言（样式/加粗/表格）、
  txt 结构保留、PDF 生成合法性、HTML 含表格与元信息；端点级 404/400 保持。
- **改** `frontend/src/views/HistoryView.vue`、`frontend/src/components/JobQueueList.vue`：失败行内联原因。
- **新增** `frontend/src/utils/jobFailure.js`（纯函数 `failureLabel(job, maxLen)`）+ `frontend/src/__tests__/jobFailure.test.js`。
- **新增** `frontend/src/utils/chatPrefs.js`（`loadChatPrefs`/`saveChatPrefs`）+ `frontend/src/__tests__/chatPrefs.test.js`。
- **改** `frontend/src/components/ChatPanel.vue`：初值来自 chatPrefs，改动即存。
- **改** `backend/routers/chat.py`：超时常量抽成 env 可配 + 超时文案可行动；`backend/tests/test_routers.py` 加断言。
- **删** `backend/routers/search.py`、前端 `searchApi`、`services/web_search.py` 的 `search_web`/`format_search_context`、
  `main.py` 注册、`tests/test_routers.py::TestSearchRouter`。
- **改** `contracts/openapi.json`（重导 80→79）、`CONTEXT.md`、必要时 `.trellis/spec/`。

## Acceptance Criteria

- [ ] 含标题/加粗/列表/表格的结果导出 Word：读回后标题层级正确、加粗是 run 级、列表是 Word 列表样式、表格是真表格；不再出现字面 `**` 或 `|`
- [ ] 导出件顶部有「文档信息」块，时间与本地时间一致（不是 UTC 早 8 小时）；≥3 个标题时 Word/HTML 有目录
- [ ] PDF 生成成功且含加粗与表格；txt 保留列表序号
- [ ] 失败任务在历史表格与队列行可见一行原因；无原因时回退文案
- [ ] 追问联网模式/思考开关刷新后保持；非法存储值回退默认
- [ ] 追问超时值可配且与批量对齐；超时错误可行动
- [ ] `/api/search`、`searchApi`、`search_web` 全链移除且无残留调用者（grep 证明）；OpenAPI 重导 79 端点
- [ ] `python verify.py all` 全绿（ruff + pytest + eslint + vitest + build）

## 测试

- 后端新增 `test_export_render.py`；现有 `test_export_batch.py`、`TestExportRouter` 保持通过；删除 `TestSearchRouter`。
- 前端新增 `jobFailure.test.js`、`chatPrefs.test.js`（含非法值回退）。
- 真实链路：重启 7001 → 用真实任务导出 4 个格式并打开确认；浏览器确认失败行与偏好保持。

## 回滚

五个单元各自独立 commit，可逐个 `git revert`；无 DB 迁移、无数据破坏。

## Notes

- 明确不做（单用户本地项目不需要）：成本/配额账本、备份恢复系统（只留"每天一份 db 快照"另议）、
  失败分类看板、提示词版本化（git 即版本）、diff 版本对比、专题合集、多模态关键帧、追更订阅。
- 局部重写（选中段落让 AI 改）本轮不做，排在之后。
