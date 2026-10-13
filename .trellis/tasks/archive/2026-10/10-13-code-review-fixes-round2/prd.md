# PRD：第二轮全量代码审查缺陷修复（2026-10-13）

> 来源：2026-10-13 第二轮四域并行只读审查（转录下载链 / AI 调用缓存链 / API 数据层 / 前端）
> + 主 Agent 自审 `main.py` 编排层。基线 `verify.py all` 全绿（pytest 1272 / vitest 334 / lint + build）。

## 背景

上一轮（`10-13-code-review-fixes`）的 4 个 P0 经本轮复核**全部成立，无修坏**。本轮在更深的调用点回溯与
**可执行复现**下，挖出 4 个实证缺陷 + 6 个证据充分的 P1。其中两条与上一轮的修复直接相关：

- 上一轮给分批路径补了「无占位则追加内容」的保护（`ai_processor.py:673-676`），新注释写着
  「与单批路径的注入点一致」，但**单批路径恰恰没有这个保护**——注释的断言是错的。
- 上一轮给 frozen yt-dlp 补了超时（方向正确），但**超时之后的收尾没处理**，引入新的进程级破坏。

用户要求（2026-10-13）：**改动要谨慎，要全面解决，不要再做一半**。

## 范围

### P0（已实证，必须修）

| # | 缺陷 | 位置 | 证据 | 修复方向 |
|---|---|---|---|---|
| P0-1 | 深度研究模式 100% 崩溃：给 `web_search_collect` 传了签名里不存在的 `content_size="high"`，且无 `**kwargs` → 每个主题展开 `TypeError`；`_extract_topics` 的付费 LLM 调用已先发生（钱花、正文无产出） | `services/research_processor.py:82-84` | 主 Agent 实测 `inspect.signature` = `(query, max_results, snippet_limit, total_budget)` | 改用签名真实支持的参数；**必须补一条真正调用 `_expand_topic` 的契约测试** |
| P0-2 | 单批总结静默丢弃转录正文：`prompt_template.replace('{{CONTENT}}', text)` 在无占位符时是静默 no-op，发给模型的 prompt 不含任何视频内容；模型凭空写总结，任务 completed、结果入库 | `services/ai_processor.py:579` | 主 Agent 复核 + 前端 `CustomPromptPanel.vue:151` 明文承诺「不写则内容追加在末尾」 | 与分批路径共用同一注入约定（无占位则追加内容），抽成单点函数消除两处分支 |
| P0-3 | 打包版 yt-dlp 超时后**进程级 stdout 被永久损坏**：超时 `raise` 在锁块内 → 锁立即释放而僵尸线程仍持有 `sys.stdout`；后续调用的 stdio 交错会把进程 `sys.stdout` 留在已 detach 的 wrapper 上，此后 `print()` 永久抛 `ValueError` | `services/downloader.py:280-290` | **主 Agent 实测复现**（两版脚本：静默吞输出 + 危险交错致 `print()` 永久失效） | 把锁的获取/释放移入 worker 线程自身，调用方只等；僵尸未结束前新调用干净超时而非交错 |
| P0-4 | `POST /api/jobs/{id}/retry?force=true` 无本机限制且**不可逆 `shutil.rmtree(job_dir)`**：签名连 `request: Request` 都没有 | `routers/jobs.py:528` | 主 Agent 复核签名 + 全仓 `require_local_client` 仅 5 处 | 补 `require_local_client`；并按同一原则补齐其余不可逆破坏端点（见「安全边界」一节） |

### P1（一并解决，证据充分且改动小）

| # | 缺陷 | 位置 |
|---|---|---|
| P1-5 | 深度研究 token 恒记 0：`research_process` 全程无 `_record_token_usage`，`return article_md, 0, 0`，而真实花掉 N+1 次付费调用 | `services/research_processor.py` |
| P1-6 | 短密钥明文回显：`if raw_key and len(raw_key) > 8` 才脱敏，长度 ≤8 的密钥**原样返回**（同域 `asr.mask_provider` 处理更严） | `routers/models.py:144` |
| P1-7 | `/api/token-usage/history` 的 `limit` 裸奔：`?limit=-1` 在 SQLite 中等于无上限（主 Agent 实测确认），与全仓其它分页端点 `Query(ge/le)` 的约定不一致 | `main.py:297` |
| P1-8 | `resummarize` 忽略 `update_job` 返回值：任务已被取消时写入被 guard 挡下（返回 False）但代码不检查，仍继续追加历史版本并前移 `summary_version` 指针 → 用户点最新版本看到的是**上一版正文** | `routers/jobs_ai.py:155` |
| P1-9 | 思维导图版本竞态：上轮只给正文加了请求序号，导图路径在保护之外——切版本时在飞的导图回包会把旧版内容钉在新版本上 | `frontend/src/views/ResultView.vue:597-660` |
| P1-10 | `CONTEXT.md:55` 写「79 端点」，实际 80，同文件 115/121 行均写 80（内部自相矛盾） | `CONTEXT.md` |

### 待复核后再定（代理报告，主 Agent 尚未复现）

由各域负责人**先复现再决定修不修**，复现不成立就写进 result.md 的「排除清单」，不得为了凑数硬改：

- `HomeView.vue:206-220` `preview` 事件无条件抢占 `currentJobId`（多任务/批量下确认错任务）
- `ResultView.vue:115-135` `loadJob` 无请求序号；`ResultView.vue:89-95` 版本加载窗口内「一键复制」复制的是最新版正文
- `utils/api.js:30-38` blob 响应失败时错误信息退化为 axios 通用串
- `ai_processor.py:1019` 校正失败/超时后已烧 token 一条不记账
- `routers/jobs.py:467,501` `async def` 内同步 `shutil.rmtree` / `_dir_size` 阻塞事件循环
- `POST /api/models/fetch` 任意 endpoint 的 SSRF 读原语（本机单用户工具，可能属既有设计取舍）

## 安全边界（延续上一轮已授权的口径）

上一轮用户已授权的口径是：**不改 `0.0.0.0` 绑定（保留手机访问），给无认证的不可逆破坏端点补与存储清理
同级的 localhost 限制**。本轮把该口径**贯彻到底**，不再只覆盖任务删除/清空：

纳入本机限制（不可逆破坏）：
- `POST /api/jobs/{id}/retry?force=true`（rmtree job_dir）
- `DELETE /api/asr/local/{engine}`、`DELETE /api/asr/local/whisper/{size}`（删除已下载的本地模型，数 GB）
- `DELETE /api/asr/providers/{key}`（删除 provider 配置）
- `DELETE /api/templates/{id}`（用户自建模板）
- `DELETE /api/summary/overrides`、`DELETE /api/summary/overrides/{dim}/{model}`（重置提示词覆盖）

**明确不纳入**（避免打断手机端既有能力，且非数据销毁）：`POST /api/jobs/{id}/cancel`、
`POST /api/jobs/batch-cancel`、`POST /api/models/*` 配置写入类、`POST /api/bilibili/logout`。
如需一并收紧，属于产品取舍，另行提出。

## 不在范围（红线）

- **不改任何提示词正文**（`services/prompt_builder.py` 各维度模板文字与拼装段序）——用户资产。
  P0-2 修的是**内容注入方式**，一个字的模板正文都不动。
- 不改 CORS 白名单、不改 `MAX_CONCURRENT_JOBS` 默认值、不改存储清理的 dry-run 双门槛。
- 不删兼容别名与历史数据解读口径。
- 不 push（PUSH-GATE，需用户另行授权）。
- **不做浏览器端到端实测**（本轮同上一轮，静态回溯 + 可执行复现 + 自动化测试；前端项以 vitest 结构断言覆盖）。

## 验收标准

1. **每条修复都有能证伪的回归测试**：把被测源码换回修改前版本跑该用例，必须变红；非回归用例必须全绿。
   记录红/绿的具体用例数。（上一轮教训：注释声称「与另一分支一致」而实际不一致；测试写在测试体内抄生产
   逻辑再断言自己抄的字典＝假测试，本轮明令禁止。）
2. `python verify.py backend` 全绿、`python verify.py frontend` 全绿、`verify.py all` exit 0。
3. **独立复核**：由未参与实现的代理复核，重点看 P0-3 的并发语义与 P0-2 的注入一致性。
4. 前端产物重建（`backend/static/`）并随改动提交。
5. 无新增静默吞错；失败一律可见可行动。
6. `CONTEXT.md`（含 P1-10 的端点数订正）与相关 spec 同步更新。
7. 逐切片 commit（COMMIT-DISCIPLINE），无攒批。

## 回滚

每个逻辑单元一次 commit，可逐条 `git revert`。无 schema 变更、无数据迁移。
P0-3 的修复方向单调安全（把「交错破坏 stdout」变成「干净超时」）。