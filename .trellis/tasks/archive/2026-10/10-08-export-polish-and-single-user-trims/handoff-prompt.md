# 交接提示词（复制下面整段到新会话/新 Agent 即可接手）

```text
接手任务：.trellis/tasks/10-08-export-polish-and-single-user-trims（M1，跨模块但方案清晰，无需切片）

## 开工前强制动作（顺序执行，不要跳）
1. READ: AGENTS.md（项目治理）+ CONTEXT.md（架构/坑点）+ AI_AGENT_WORKFLOW.md §0/§1/§5/§6/§7
2. EXEC: git status && git log --oneline -8   # 工作树应干净；最近提交是 docs(chore) 任务工件
3. EXEC: python .trellis/scripts/task.py current
4. READ: .trellis/tasks/10-08-export-polish-and-single-user-trims/prd.md（本任务权威范围，用户已授权：其"开始"即批准 ①②③+顺手④⑤）
5. EXEC: python .trellis/scripts/task.py start 10-08-export-polish-and-single-user-trims
6. EXEC: codegraph explore "<要改的符号>"（编码前必须查重复实现/影响面；索引 215 文件已就绪）

## 任务目标（用户原话背景）
用户是**本地单用户**项目的唯一使用者，认为产品"还是毛坯"。上一轮评估后他砍掉了所有 SaaS 式设计点，
只批准这五件（按此顺序做，每件一个 commit）：

### ① 导出成品化（主件）
问题（已核实）：`backend/routers/export.py` 的 docx 生成是**按行手工解析 Markdown**——
`**加粗**` 被 `re.sub(r'\*{1,2}(.*?)\*{1,2}')` 直接剥掉（约 143 行附近）、列表标记不处理、
表格完全不认（`elif line.startswith('```')` 只是跳过该行）；fpdf2 的 PDF 同样只有标题/项目符号（约 227~235 行）；
`_md_to_plain_text`（29 行起）把有序列表的 `1.` 直接删掉；四个格式各写一套解析（docx 行循环 / fpdf 行循环 /
`_md_to_plain_text` 正则 / `_md_to_html` 用 markdown-it）。用户结果里有大量表格与列表，所以要**收敛到一份解析**。
做法（prd 语义决策表已定）：
- 新增 `backend/services/md_render.py`：内部用 markdown-it（`MarkdownIt("commonmark", {"html": False}).enable("table")`，
  markdown-it-py==4.2.0 已在 requirements.txt 锁定，**不得新增依赖**）解析成 token 流，四个渲染器只消费 token：
  `render_docx(job, text, path)` / `render_pdf(job, text, path)` / `render_html(job, text)` / `render_text(job, text)`；
  另有 `doc_meta(job)`（元信息块）与 `doc_toc(tokens)`（目录，二级/三级标题 ≥3 个才插）。
- docx：真表格（表头加粗）、Word 原生 List Bullet/List Number 样式、run 级加粗/斜体/行内代码、标题层级、引用、代码块、水平线。
- PDF：fpdf2 手写渲染（字体 `C:\Windows\Fonts\msyh.ttc`，`add_font('zh','')`/`add_font('zh','B')`），
  加粗切 B 字体、列表带符号/序号、表格按列数平分宽度画边框；保留 weasyprint 优先 → fpdf2 兜底结构。
- txt：保留列表序号（打击现有丢序号行为），项目符号用 `•`。
- 元信息块（人看的）：来源平台 / 来源链接 / 模型 / 任务创建时间 / 正文字数（len(result_text)）/ 标签 / 转录来源
  （`transcript_meta` 的 `engine` + `seg_count`）。
- **★ 时间坑（已实测）**：DB 里 `jobs.created_at` 是 **UTC 且无时区标记**（本地 11:39 写行存 `03:39:04`，前端靠
  `parseChatTime` 补 Z 再转本地）。导出件里写时间**必须 +8 转北京时间**，否则整体早 8 小时。
- `routers/export.py` 四个端点变薄（校验 404/400 + 调 md_render + FileResponse），删掉旧解析函数。

### ② 历史/队列内联失败原因（前端小改）
问题（已核实）：`db/job_repo.py:114` 的列表列清单**已含 `error` 字段**，但 `HistoryView.vue` 与
`components/JobQueueList.vue` 都不渲染它（grep 过，只有 ElMessage 自己的错误），失败任务要点进去才知道原因。
做法：新增纯函数 `frontend/src/utils/jobFailure.js` 的 `failureLabel(job, maxLen=60)`（截断 + 空失败回退
「失败（无错误详情）」），两处视图渲染一行 + `title` 悬停全文；配 `__tests__/jobFailure.test.js`。

### ③ 追问偏好记忆（前端小改）
问题（已核实）：`components/ChatPanel.vue:379` 是 `const searchMode = ref('off')`，无任何持久化
（该文件里没有 localStorage）；用户几乎每次都要手动切「严格依据材料」。
做法：新增 `frontend/src/utils/chatPrefs.js`（`loadChatPrefs()`/`saveChatPrefs()`，键 `ms.chat.prefs`，
存联网模式 + 思考开关），非法/损坏值一律回退默认 `off`/`false`，写失败只 warn；ChatPanel 初值取自它、改动即存；
配 `__tests__/chatPrefs.test.js`（含非法值回退用例）。

### ④ 追问超时并入预算体系（后端小改）
问题（已核实）：`routers/chat.py:37` 是 `HTTPX_TIMEOUT = httpx.Timeout(180.0, connect=15.0)`，
而批量流式在 `services/ai_processor.py:246` 用 `asyncio.wait_for(..., timeout=600.0)`——
同一模型批里能跑完、追问会断。
做法：抽成 env 可配常量（默认 `CHAT_MODEL_TIMEOUT_SECONDS=600`、`CHAT_MODEL_CONNECT_TIMEOUT_SECONDS=15`），
超时错误文案点明「模型 N 秒内没有返回内容」并给出可行动建议；`tests/test_routers.py` 补断言。

### ⑤ 删掉没人调的旧 /api/search（清理）
证据（已核实）：`frontend/src/utils/api.js:301` 定义 `searchApi.search`，**全前端零调用方**（grep 只有定义处）；
`services/web_search.py:search_web()` 的唯一调用者是 `routers/search.py`；它失败静默返回 `[]`（上一轮点名的死按钮风险面）。
做法：删 `backend/routers/search.py` + `main.py` 里的注册 + 前端 `searchApi` + `web_search.search_web`/`format_search_context`
（删前再 grep 一次确认无其他调用者）+ `tests/test_routers.py` 的 `TestSearchRouter` 与 app fixture 里的注册；
**删完必须重导 OpenAPI**：`backend/venv/Scripts/python.exe backend/scripts/export_openapi.py`（80 → 79 端点）。

## 硬约束（违反=严重违规）
- **COMMIT-DISCIPLINE**：一个逻辑单元完成即当场 `git commit`（英文前缀 + 中文说明，如 `feat(export): …`），
  禁止攒批、禁止 `git stash` 躲避；失败单元也要留 `[WIP-FAIL]` 提交痕迹。
- **PUSH-GATE**：严禁 `git push` / 部署（用户未授权），只做本地 commit。
- 密钥/隐私：任何 Key、cookie、DB、下载文件、模型文件都不入库、不写文档（搜索 Key 只在 gitignored `backend/config.py`）。
- 禁止 `except: return []` / `.catch(() => [])` 静默吞错；错误必须可见可行动。
- 改 API/端点后**必须**重导 OpenAPI；CONTEXT.md 归档时必须同步（≤300 行，含"何时读"列）。
- 后端测试必须用 `backend/venv/Scripts/python.exe -m pytest`（沙箱 python 缺 pytest-asyncio 会批量假失败）。

## ★ 服务重启坑（这条上次踩过）
7001 的服务**没有 `--reload`**，改了后端必须重启才生效，否则浏览器/接口会测到旧行为：
1) `netstat -ano | grep ":7001" | grep LISTENING` 拿 PID；
2) `powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter 'ProcessId=<PID>' | Select CreationDate,CommandLine"`
   对比文件 mtime / commit 时间，确认它是不是旧进程；
3) `taskkill //PID <PID> //F`；
4) `cd backend && venv/Scripts/python.exe -m uvicorn main:socket_app --host 0.0.0.0 --port 7001 >> uvicorn.log 2>&1`（后台）；
5) 双验证：`netstat` 看 LISTENING + `curl -s http://127.0.0.1:7001/api/jobs?limit=1` 有 JSON 返回。
   （注意：后台任务卡片可能报 "failed" 但进程其实活着——**别信通知，信端口和接口**。）

## 门禁与验收
- 门禁：`python verify.py all`（ruff + 后端 pytest + eslint + vitest + build）必须 `ALL CHECKS PASSED`；
  改完 export 单独跑 `backend/venv/Scripts/python.exe -m pytest tests/test_export_render.py tests/test_export_batch.py tests/test_routers.py -q`。
- 真实链路：重启服务后**用真实任务**导出 docx/pdf/html/txt 各一份，用 python-docx 读回或直接打开确认
  （标题层级/加粗/列表/表格/元信息时间正确）；浏览器确认 ②失败行内联原因 与 ③偏好刷新保持。
- 验收标准以 prd.md 的 Acceptance Criteria 为准（8 条），逐条给出证据（代码行 + 命令输出），
  禁止"文档齐全"冒充"做完了"（§6.1 理解层审查）。

## 已知环境事实
- 服务：http://127.0.0.1:7001（本地单用户）；DB `backend/data/youtube_tool.db`（jobs 348 条左右）；
  模型里 `glm-5-2`（商汤）实测可用，用于真实链路。
- 检索链：Ollama/Tavily/Firecrawl 三家 Key 已配（在 config.py）+ ddgs 兜底；本次任务**不需要**动搜索链。
- 前端测试必须走 `npm test`（内含 `scripts/run-vitest.mjs` 盘符大小写规范化包装，别直接 `npx vitest run`）。

## 完成后
1. `python .trellis/scripts/task.py archive 10-08-export-polish-and-single-user-trims --no-commit`
2. 同步 CONTEXT.md（新增 md_render.py 索引行与"何时读"、export/chat 行更新、当前状态加一条、删掉 search.py 相关行）
3. 一个 `docs: archive … + refresh CONTEXT.md` 提交；**不 push**。
```

## 备注（给用户看的，不用复制）

- 交接文件路径：`.trellis/tasks/10-08-export-polish-and-single-user-trims/` 下的 `prd.md`（权威范围）与本文件。
- 本任务**尚未动任何业务代码**，也未 `task.py start`——接手方第一步就是读 prd + start。
- 上面提示词里的行号/证据都是 2026-10-08 实测的（`codegraph explore` + 真机 grep），若接手时间较晚请重新核一遍行号。
