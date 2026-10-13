# 实现结果（2026-10-13）

## 交付

7 个逻辑单元 + 1 个复核修正，逐切片 commit（COMMIT-DISCIPLINE，无攒批）：

| commit | 切片 |
|---|---|
| `59286b4` | AI 链：分批 fail-fast / 打标签失败契约 / 流式 token 记账 / 取消轮询 / 检索墙钟预算 |
| `574b581` | 转录链：抖音原子下载 / frozen yt-dlp 超时 / 云端取消收尾 / 本地引擎互斥 / 样本与 cookie 清理 |
| `cea8aec` | 总结版本号 `append_summary_version` |
| `afd4669` | 派生文件名 `derived_sibling` + 续传 ffprobe 拦截截断媒体 |
| `2f9ce59` | 破坏性端点限本机 / ASR 设置原子化 / 批量上限 / 审计尾部读 |
| `dc81e8a` | 前端：追问删轮不连坐 / 历史全选错乱 / 版本切换竞态 / 流式渲染 |
| `53605d8` | 前端产物重建 + OpenAPI 契约重导 |
| `6896e4e` | 独立复核发现的 3 项修正 |
| `8125fe5` | CONTEXT.md + spec 回写 |

## 验收

- `verify.py all` **ALL CHECKS PASSED**（exit 0）：backend ruff + pytest **1272**、
  frontend lint 0 问题 + vitest 30 文件 **334** + build。
- **修复前对照**：各切片把源码换回 HEAD 版本跑新增用例，AI 链 18 红、转录链 18 红，
  红的是新增用例、非回归用例全绿——证明测试确实能证伪实现。
- **独立复核**（未参与实现的代理）结论「有条件通过」，发现的 3 项已全部修掉并重新全量验证。
- OpenAPI 重导仅 description 文本变化，**无路径/方法/参数变更**（80 端点）。

## P0 逐条交付情况

| # | 缺陷 | 修法 | 回归测试 |
|---|---|---|---|
| P0-1 | 分批总结某批失败，哨兵串被当正文写盘并标 completed | `ai_process` 分批模式任一批失败即抛异常，抛点在记账与 `emit_ai_token(done=True)` 之前 | `test_ai_processor.py` 3 例（含「单批保持原样」防过度修复） |
| P0-2 | 追问删一条提问连坐后续全部轮次 | `onDeleteTurn` 改走不级联的 `deleteMessages`，`onEdit` 保持级联 | `chatPanel.test.js` 断言 `deleteFrom` **从未被调用** |
| P0-3 | 重试覆盖归档的 v1，`summary_version` 打回 1 | `append_summary_version` 在 `BEGIN IMMEDIATE` 事务内由 `MAX+1` 推导；20 线程并发实测版本号全唯一 | `test_job_repo.py` 5 例 |
| P0-4 | 抖音截断 mp4 被续传当完整音频 → 静默截断转录 | 治本 `.part` + 原子改名；兜底续传对选中候选做 ffprobe 时长校验 | `test_douyin.py` 4 例（断言文件系统状态）+ `test_url_input.py` 3 例 |

## 已授权的产品取舍

1. 分批总结 fail-fast，不产出残缺文档。
2. 保留 `0.0.0.0` 监听；给无认证破坏性端点补与存储清理同级的本机限制，不改绑。
3. 删除一条提问不连坐后续轮次。
4. 在线编辑保存失败保留编辑态并提供重试。

## 遗留与未验证项（如实记录）

- **打包版 yt-dlp 超时后无法强杀 Python 线程**：僵尸线程只污染日志排版，daemon 不挡退出；
  真要硬杀需走子进程。
- **单次在飞 httpx 请求（最长 180s）无法中断**：云端 ASR drain 超限只记 warning 后照常删分片。
- **`run_with_fallback` 的非流式候选在 HTTP 失败时同样丢 token**，但服务端未返回 usage，
  属不可得数据，无法按流式路径的方式补。
- **`_ffprobe_available` 与实际探测路径的降级不对称**：当前环境判定一致，不构成缺陷；
  若将来 ffprobe 只存在于其他位置，会静默降级为「跳过校验」（安全方向）。
- **`_read_audit` 行为差异**：旧实现取最后 N **行**再过滤，新实现取最近 N **条有效事件**。
  新行为更合理且顺序一致（升序），属有意差异，已记录。
- **未做浏览器实测**：本轮全部为静态代码 + 调用点回溯 + 自动化测试，
  未启动服务做端到端点击验收。前端 4 个缺陷（C1/C2/C3/C9）建议真机点一遍确认。
- **未 push**：PUSH-GATE，需用户另行授权。