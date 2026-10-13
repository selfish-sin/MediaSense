# 实现结果（2026-10-13 第二轮）

## 交付

7 个 commit，按域并行、逐逻辑单元提交（COMMIT-DISCIPLINE，无攒批）：

| commit | 域 | 切片 |
|---|---|---|
| `0b21d32` | AI 链（Worker A） | P0-1 深度研究签名崩溃 + P1-5 token 记账 |
| `2796ae5` | AI 链（Worker A） | P0-2 内容注入抽单点 `_inject_content` |
| `cc6480c` | 转录链（Worker B） | P0-3 frozen yt-dlp 锁移入 worker |
| `e515955` | 前端（Worker C） | P1-9 思维导图版本竞态 |
| `ab3c911` / `5a34cce` / `50e2ba0` / `5d02e0c` / `fd71be8` | 前端（Worker C） | 复制旁路 / loadJob 序号 / blob 错误文案 / 多任务待确认预览 / lint |
| `70ab798` | 主 Agent | P0-4 安全边界补齐 + P1-6 密钥脱敏 + P1-8 版本指针 |
| `1caf95d` | 主 Agent | P1-7 limit 上下界 |
| `af26e00` | 主 Agent | 独立复核两项修正（chat 删除端点 / research 异常记账） |

## P0 逐条交付

| # | 缺陷 | 修法 | 回归测试 |
|---|---|---|---|
| P0-1 | 深度研究 100% 崩溃：`content_size="high"` 不在签名内，每主题 `TypeError`，而付费的主题提取已先发生 | 改用真实支持的 `snippet_limit=RESEARCH_SNIPPET_LIMIT`（900，取自 web_search 既有 strict 500 / broad 900 口径） | `test_research_processor.py` 9 条**真调用** `_expand_topic/_extract_topics`，桩函数逐字复刻真实签名；**红 7 → 绿 9** |
| P0-2 | 单批总结静默丢弃转录正文（`str.replace` 无占位符时静默 no-op），UI 却承诺「不写则内容追加在末尾」 | 抽 `_inject_content` 为唯一注入约定，4 处注入点全部改调；**结构性消除两分支分叉** | 单批/旧路径各 1 条捕获真实 prompt 断言正文在场；**红 3 / 绿 75** |
| P0-3 | 打包版 yt-dlp 超时后**进程级 stdout 永久损坏**（锁在调用方 → 提前释放 → 与僵尸交错 → `sys.stdout` 留在已 detach 的 wrapper 上） | 锁的 `with` 移入 worker 内部，覆盖 stdio 接管→还原→detach 全程；调用方改用 per-call `Event` 判断是否走完，未走完则干净超时且不碰 stdio | 危险时序用例（超时→立即再调→僵尸中途结束）断言缓冲完整 + `sys.stdout is` 原对象 + 可写；旧实现下红 |
| P0-4 | `retry?force=true` 无本机限制且 `rmtree` 不可逆（签名连 `request` 都没有） | 补 `require_local_client`，并按同一原则补齐 ASR provider/本地模型删除、模板删除、提示词覆盖重置 | 参数化 403（**15 条端点**）+ `assert_not_called` 钉住「守卫在 rmtree 之前」+ 一条钉住「`force=false` 刻意不收紧」 |

## 独立复核结论：先判 FAIL，修后通过

未参与实现的代理复核后端，判定 **FAIL**，唯一阻断项 + 一项残留：

1. **chat 的 5 个删除端点**（删会话 / 清空消息 / 单条 / 级联 / 多选批量）同样销毁不可逆用户数据，却既没纳入本机限制、也没写进排除清单——本轮把该原则宣布为「贯彻到底」，这是无声遗漏。**已修**（`af26e00`）。
2. **research token 在异常/超时下整段丢失**：记账在实现体末尾，而调用方用 `wait_for(3600s)` 包着。**已修**（拆公开入口 + `finally` 记账，`CancelledError` 是 `BaseException`，只有 `finally` 覆盖得到）。

复核同时确认：P0-3 的锁语义是**真修**（它把 `cc6480c^` 的旧实现装回模块跑同一时序，确认为红）；本轮**没有**发现「测试体内抄生产逻辑」的假测试，也没发现只靠源码正则的假契约测试。

## 验收

- `verify.py all` **ALL CHECKS PASSED**（exit 0）：backend ruff + pytest **1315**、frontend lint 0 问题 + vitest **34 文件 / 352 例** + build。
- 基线 1272 → 1315（后端 +43）、334 → 352（前端 +18）。
- **修复前对照**：每个切片都把源码换回修改前跑新增用例，记录红/绿条数；非回归用例全绿。
- OpenAPI 重导：仍 **80 路径**，仅 description 文本与新增的 `minimum/maximum` 约束，无路径/方法/参数变更。
- 前端产物已重建并随提交入库。

## Worker B 的一处既有测试改动（已复核认可）

`TestFrozenYtdlpTimeout::test_hanging_main_raises_timeout_expired` 的替身被改：输出语句从超时**之后**挪到之前、挂起 30s→3s。独立复核确认**未削弱覆盖**——真正的证伪锚点是 `pytest.raises(TimeoutExpired)`，换回「不消费 timeout」的实现仍红；而原 30s 僵尸线程醒来后的 `print` 确实会落进**下一条**用例的 capsys 缓冲（跨用例污染），缩短挂起同时消除了它与新锁语义的互相等待。

## 遗留与未验证项（如实记录）

- **打包版 yt-dlp 僵尸不可杀**：Python 线程杀不掉，僵尸会一直占锁，后续 yt-dlp 调用恒干净超时并提示重启。这是**刻意接受**的（应用此时已不可用，明确报错远优于静默损坏整个进程）。副作用：每次重试会多一个永久阻塞在锁上的 daemon 线程。
- **单次在飞 httpx 请求无法中断**：云端 ASR drain 超限只记 warning。
- **`_inject_content` 与 `build_modular_prompt` 各有一套注入逻辑**：约定一致（无占位则追加内容），但仍是两份实现。收敛需改 `prompt_builder.py`，属用户资产，本轮未动。
- **未做浏览器端到端实测**：本轮同上一轮，静态回溯 + 可执行复现 + 自动化测试；前端项以 vitest 结构断言覆盖。
- **未 push**：PUSH-GATE，需用户另行授权。

## 本轮发现并已推翻的疑点（供下轮免重复）

- 字幕缓存命中后 AI 校正会污染共享缓存——`lookup_subtitle` 是写副本到 job 目录，缓存本体不动。不成立。
- 校正全失败会截断原 txt——异常在 `open()` 之前抛出。不成立。
- frozen 竞态「第二次调用输出丢失」在简单时序下**不发生**（我第一次复现就被证伪），必须构造「僵尸在第二次执行**途中**结束」才触发。
- 追问 `preview` 事件「会确认错任务」——Worker C 查证 `jobs_batch.py` 强制 `preview_before_summary=False`、批量子任务根本到不了 preview，且 `currentJobId` 与 `previewText` 同步更新，改判为「先到的任务失去确认入口」而非「确认错任务」。**已按改判后的结论修复**。