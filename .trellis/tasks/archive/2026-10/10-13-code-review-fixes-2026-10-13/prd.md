# PRD：全项目代码审查缺陷修复（2026-10-13）

> 来源：2026-10-13 四域并行只读代码审查（转录下载链 / AI 调用缓存链 / API 数据层 / 前端），
> 外加主 Agent 自审 `main.py` 任务编排层。全部为**静态代码 + 调用点回溯**结论，
> 其中「重试覆盖历史版本」已在沙箱复现、「追问 max_tokens 为 NULL」已只读查询生产库确认。

## 背景

审查共产出 4 个 P0、约 12 个 P1、11 个 P2。四份报告中被判定为**会静默产出错误数据或
造成不可恢复丢失**的共 4 条，是本次修复的硬目标。其余按「改动小、收益直接」排序处理。

用户已授权：按推荐方案直接修，取舍由 AI 判断（2026-10-13 本轮对话）。

## 范围

### P0（必须修，会静默产出错误数据 / 不可恢复丢失）

| # | 缺陷 | 位置 | 修复方向 |
|---|---|---|---|
| P0-1 | 长视频分批总结，某批 AI 失败时哨兵串被当正文写盘并标 completed | `services/ai_processor.py`（合并逻辑），调用方防线 `main.py`、`routers/jobs_ai.py` | **fail-fast**：分批模式下任一批失败即整体失败，不产出残缺文档 |
| P0-2 | 追问里删一条提问，连坐删除后续全部轮次（确认框只承诺删这一轮） | `components/ChatPanel.vue` 复用了给「编辑」用的级联端点 | 改走不级联的批量删除端点；文案与行为对齐 |
| P0-3 | 重试任务覆盖归档的 v1 总结，并把 `summary_version` 打回 1 | `db/job_repo.py` 的 `INSERT OR REPLACE`、`main.py` 收尾硬编码 `version=1`、retry 不清历史 | 版本号从 `max(history.version)+1` 递增；并发用乐观锁 |
| P0-4 | 抖音直链下载失败的半截 mp4 被断点续传当完整音频 → 静默截断转录标 completed | `services/douyin.py` 直写最终名 + `main.py` 续传扫描只看后缀和大小 | 下载先写 `.part` 再原子改名 |

### P1

- `routers/chat.py`：`max_tokens` 为 NULL 时被发成 `null`（当前 100% 命中）
- `services/downloader.py`：打包版（frozen）yt-dlp 无超时，三处超时提示是死代码
- `services/cloud_asr.py`：取消后在飞线程继续烧额度；分片导出中途失败残片滞留
- `services/local_asr.py`：模型按需下载与转录内置检查无互斥；补录路径不走引擎锁
- `services/ai_processor.py`：流式失败一律返 0 token；流式卡住时取消不触发；校正无整体超时
- `services/ai_processor.py`：`edited_prompt` 在分批模式下被丢弃
- `routers/jobs_ai.py`：生成标签失败会清空已有标签并返回成功
- `services/web_search.py`：总预算不是墙钟上限，会挤掉免 Key 的 DDGS 兜底
- `components/HistoryView.vue`：全选模式两处错乱，可致批量误删
- `views/ResultView.vue`：版本切换竞态，正文与版本号/导出对不上
- `routers/asr_admin.py`：设置批量更新非原子，报错时已写入字段不回滚

### P2

`.replace('.txt')` 路径错乱（3 处）／`generate_tags` 从不记 token／并发重新总结版本号互相覆盖／
`summarize` 传 `options: null` 直接 500／审计文件全量读／空更新返回成功／两处连接未 `try/finally`／
B站临时 cookie 异常路径残留明文 SESSDATA／批量端点 `job_ids` 无上限／未知维度值静默丢提示词／注释与代码不符。

## 已确认的产品取舍（用户 2026-10-13 授权「按推荐」）

1. **分批总结 fail-fast**，不产出残缺文档（残缺文档比明确失败更危险）。
2. **监听 `0.0.0.0` 保持不变**，但给无认证的破坏性端点（批量清空、模型删除）补**与存储清理同级的
   localhost 限制**——不改绑定地址，避免影响手机访问。
3. **删除一条提问不连坐后续轮次**（走已有的不级联批量端点）。
4. **在线编辑保存失败时保留编辑态并提供重试**，不再只弹 warning 就覆盖前端正文。

## 不在范围

- 不改任何提示词正文（模块 template / build_modular_prompt 段序）——属用户资产。
- 不改 CORS 白名单、不改 `MAX_CONCURRENT_JOBS` 默认值、不改存储清理的 dry-run 双门槛。
- 不删任何兼容别名与历史数据解读口径。
- 不 push（PUSH-GATE，需用户另行授权）。

## 验收标准

1. 每条 P0 都有对应的失败路径回归测试（能证明修复前会失败、修复后不失败）。
2. `python verify.py backend` 全绿；`python verify.py frontend` 全绿。
3. 前端产物重建（`backend/static/`）并随改动提交。
4. 无新增 `except: return []` 类静默吞错；失败一律可见可行动。
5. CONTEXT.md 与相关 spec 同步更新。

## 回滚

每个逻辑单元一次 commit，可逐条 `git revert`。无数据迁移、无 schema 变更，纯代码修复。