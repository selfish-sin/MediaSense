# Design：第二轮代码审查缺陷修复（2026-10-13）

风险等级：**L**（并发/锁语义变化 + 安全边界变化 + 跨 4 域牵连）
项目形态：Web 全栈（FastAPI + Socket.IO + SQLite + Vue 3 SPA）
协作模式：**垂直切片**（每片独立可验证、互斥写入）

## 文件所有权（并行写入必须互斥，开工即生效）

| 执行者 | 独占文件 | 独占任务 |
|---|---|---|
| Worker A（AI 链） | `backend/services/ai_processor.py`、`backend/services/research_processor.py`、`backend/tests/test_ai_processor.py`、`backend/tests/test_research_processor.py`（新建）、`backend/tests/test_web_search.py` | P0-1、P0-2、P1-5 |
| Worker B（转录链） | `backend/services/downloader.py`、`backend/tests/test_downloader.py` | P0-3 |
| Worker C（前端） | `frontend/src/**`（含 `frontend/src/__tests__/**`） | P1-9 + 待复核项 |
| 主 Agent | `backend/main.py`、`backend/routers/**`、`backend/tests/{test_main,test_routers,test_asr_management}.py`、`verify.py`、`CONTEXT.md`、`.trellis/**`、`backend/static/**`、`contracts/openapi.json` | P0-4、P1-6、P1-7、P1-8、P1-10、安全边界 |

**红线**：任何执行者都不得修改自己所有权之外的文件。需要动别人文件时，把诉求写进
`.trellis/tasks/10-13-code-review-fixes-round2/cross-notes.md`，由主 Agent 统一改。
特别地：Worker C 不得碰 `frontend/src/utils/api.js` 之外的契约方，Worker A 不得碰 `routers/**`。

## 关键设计决策

### 决策 1（P0-3）：锁由 worker 线程自己持有，调用方只等

**问题**：`raise subprocess.TimeoutExpired` 写在 `with _YTDLP_STDIO_LOCK:` 块内，
异常传播时锁立即释放，而僵尸线程仍在 `_yt_dlp.main()` 里、仍持有 `sys.stdout`。
下一次调用拿到锁时捕获到的 `old_out` 是**僵尸的 wrapper**，随后两者交错
（僵尸中途结束 → 把 `sys.stdout` 顶回真实 stdout；新调用 finally 又把它指回已 detach 的 wrapper）
→ 进程级 `print()` 永久失效。

**改法**：锁的获取与释放移入 worker 线程自身，覆盖「重定向 → 调 main → 还原 → detach」全过程；
调用方不再持锁，只用一个 per-call `Event` 判断 worker 是否**真正走完**。

```
worker = Thread(target=_invoke)   # _invoke 内部 with _YTDLP_STDIO_LOCK: ... finally: _done.set()
worker.start()
worker.join(timeout)
if not _done.is_set():            # 还在跑，或卡在等上一个僵尸的锁
    raise TimeoutExpired(可行动文案)
```

**为什么这么选**：
- 锁的持有期 == stdio 被接管的完整周期 → **结构上不可能再交错**，不是靠时序凑安全。
- 卡死的僵尸会一直占锁，后续调用拿不到锁就干净超时（不碰 stdio、不抛原始异常），
  而不是像现在这样破坏整个进程。代价是「僵尸不死则后续下载恒超时」——
  但那种情况下应用已经处于不可用状态，且新文案会明确要求重启应用，比静默损坏可诊断得多。
- 调用点零改动：既有 `except subprocess.TimeoutExpired` 分支自动生效（沿用上一轮的契约 B）。

**测试要求**：必须有一条用例构造「超时 → 立即再调一次 → 僵尸中途结束」的时序，
断言 (a) 第二次调用的 stdout 缓冲**完整**、(b) 全程结束后 `sys.stdout` 仍可写、
(c) 换回修改前的实现该用例变红。

### 决策 2（P0-2）：注入约定抽成单点函数，结构性消除分支分叉

**问题**：`ai_processor.py:579`（单批）只有 `.replace()`，`:673-676`（分批）有「无占位则追加」。
两处各自实现 → 上一轮只补了分批侧并写下「与单批路径一致」的错误断言。

**改法**：抽 `_inject_content(template, body) -> str` 作为**唯一**注入约定
（有 `{{CONTENT}}` 则替换；无则按 `CustomPromptPanel.vue:151` 的 UI 承诺追加在末尾），
单批与分批**都调用它**。这样「两条路径行为分叉」这个缺陷类别被消除，而不是被补上。

提示词正文一个字不动——本改只决定**内容怎么注入**。

### 决策 3（P0-1）：修调用点 + 补真契约测试

`web_search_collect` 的真实签名是 `(query, max_results, snippet_limit, total_budget)`。
`content_size="high"` 应改用 `snippet_limit` 表达，**取值必须按 `web_search.py` 自身的
strict/broad 约定选**（不要凭空发明数值）。同时必须补一条**真正调用** `_expand_topic` 的测试——
上一轮之所以漏掉，正是因为原测试只断言 `hasattr(rp, '_expand_topic')` 这类结构，
对调用契约零覆盖。

### 决策 4（P0-4 + 安全边界）：按既有授权口径补齐，不再半覆盖

复用 `routers.jobs.require_local_client`（与 `storage_cleanup._require_local` 同口径），
逐个端点加 `request: Request` 参数。纳入清单见 prd.md「安全边界」。
`cancel` / `batch-cancel` / 配置写入类**明确不纳入**（非数据销毁，且收紧会打断手机端既有能力）。

### 决策 5（测试纪律，本轮强制）

每个修复必须附一条能证伪的用例，并**实际执行「换回旧代码」对照**，把红/绿用例数写进 result.md。

- 禁止在测试体内抄一份生产逻辑再断言它。
- 渲染/列表/竞态类问题必须断言**结构不变量**（条数、id 唯一性、状态取值、缓冲完整性），
  `toContain` 类文本断言对「多出空对象」无效。
- 契约测试必须**真调用**被测函数，不许只断言符号存在。

## 影响面

- `_run_ytdlp`：调用点全在 `services/downloader.py` 内部（3 处），已有 `except TimeoutExpired` 分支。
- `ai_process` 单批/分批：调用点 `main.py`、`routers/jobs_ai.py`（均为主 Agent 所有，契约不变）。
- `research_process`：调用点 `main.py:1320`（返回三元组形状不变，仅 token 值变准）。
- `retry_job` 签名新增 `request: Request` → 前端 `api.js` 的调用形状不变（query 参数不变）。
- `models.py` 密钥脱敏 → 前端 `mask_provider` 消费方需确认对 `****` 的判读不变（短密钥现在也返回 `****`）。

## 切片清单（按顺序执行）

| 切片 | 内容 | 验证方式 |
|---|---|---|
| 01 | **P0-1** 深度研究签名修复 + 真契约测试 | 调用 `_expand_topic` 的测试；换回旧代码变红 |
| 02 | **P0-2** 注入约定抽单点 + 单批/分批共用 | 单批无占位符时 prompt 必须含正文；换回旧代码变红 |
| 03 | **P0-3** 锁移入 worker + 危险时序测试 | 三断言（缓冲完整/stdout 可写/证伪） |
| 04 | **P0-4 + 安全边界** 本机限制补齐 | 非 localhost 请求被拒的端点参数化测试 |
| 05 | **P1-5** 深度研究 token 记账 | 断言 `token_usage` 表出现该任务的真实非零 token |
| 06 | **P1-6/7/8** 密钥脱敏 / limit 上限 / 版本指针 | 各自单测 |
| 07 | **P1-9 + 前端待复核项** 思维导图竞态等 | vitest 结构断言 |
| 08 | **P1-10 + 全量** verify + 前端产物 + OpenAPI + CONTEXT/spec 回写 | `verify.py all` exit 0 |

## 回滚

逐 commit revert。无 schema 变更、无数据迁移、无提示词正文改动。