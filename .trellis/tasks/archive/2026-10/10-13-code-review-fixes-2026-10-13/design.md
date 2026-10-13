# Design：代码审查缺陷修复（2026-10-13）

风险等级：**L**（并发/事务逻辑 + 安全边界变化 + 跨 4 域牵连）
项目形态：Web 全栈（FastAPI + Socket.IO + SQLite + Vue 3 SPA）
协作模式：**垂直切片**（每个切片独立可验证）

## 文件所有权划分（并行写入必须互斥）

| 执行者 | 独占文件 |
|---|---|
| Worker A（AI 链） | `backend/services/ai_processor.py`、`backend/services/web_search.py`、`backend/tests/test_ai_processor.py`、`backend/tests/test_web_search.py` |
| Worker B（转录链） | `backend/services/{downloader,douyin,cloud_asr,local_asr,transcriber}.py`、`backend/tests/{test_downloader,test_douyin,test_cloud_asr,test_local_asr,test_transcriber}.py` |
| Worker C（前端） | `frontend/src/**`（含 `frontend/src/__tests__/**`） |
| 主 Agent（主 Agent） | `backend/main.py`、`backend/db/job_repo.py`、`backend/routers/**`、`backend/services/prompt_builder.py`、`backend/tests/{test_main,test_routers,test_job_repo,test_asr_management,test_url_input}.py`、`verify.py`、`CONTEXT.md`、`.trellis/**`、`backend/static/**`（前端产物） |

**契约边界**：A 改 `generate_tags` 返回值语义、B 改 `_run_ytdlp` 超时语义，都会影响主 Agent 拥有的调用点。
以下三条是**约定好的接口**，双方都按此实现，不需要额外协商：

### 契约 A：`generate_tags` 返回值语义变更

```
旧：成功有标签 -> List[str]；成功无标签/任何失败 -> []
新：成功有标签 -> List[str]；成功但解析不出 -> []；调用失败（兜底环全挂/全部无法解析） -> None
调用方判定：tags is None 视为「调用失败」，必须可见可行动，不得静默当成「无标签」
```

- Worker A 只负责改 `services/ai_processor.py` 与其测试。
- 主 Agent 负责适配 `main.py`（失败只发 warning 日志、不写 `result['tags']`，不拖垮主流程）
  与 `routers/jobs_ai.py`（失败返回 4xx 可行动文案，**不写库**）。

### 契约 B：`_run_ytdlp` 超时语义

frozen 分支必须与 subprocess 分支一样**真正消费 `timeout`**，超时抛 `subprocess.TimeoutExpired`。
调用点已有的 `except subprocess.TimeoutExpired` 分支因此自动生效，无需改调用方。

### 契约 C：`ai_process` 分批失败

分批模式下任一批哨兵 → **抛异常**（不是返回哨兵串），消息需指名第几批、失败原因、可行动建议。
主 Agent 拥有的 `main.py` / `jobs_ai.py` 现有 try/except 自动路由到 `finish_job(False)`，无需改动。

## 切片清单（按顺序执行，垂直可验证）

| 切片 | 内容 | 验证方式 |
|---|---|---|
| 01 | **P0-1** 分批总结 fail-fast（契约 C） | 新增测试：构造 2 批、第 2 批全候选失败 → 断言抛异常而非返回含哨兵的正文 |
| 02 | **P0-3** 总结版本号：递增 + 乐观锁 | 沙箱式测试：已有 v1..v3 时收尾写入应为 v4；并发两请求只有一个成功 |
| 03 | **P0-2** 追问删除轮次不连坐（契约外的纯前端接线 + 端点复用） | 前端测试：单删走批量端点、编辑仍走级联端点 |
| 04 | **P0-4** 抖音下载 `.part` 原子写 + 续传扫描加解码校验 | 测试：模拟中途失败 → 目录无残留 `.mp4`；续传不再捡起截断文件 |
| 05 | **P1** AI 链：token 记账 / 取消轮询 / 校正超时 / edited_prompt 分批 | 对应单测 |
| 06 | **P1** 转录链：frozen 超时（契约 B）/ 本地模型互斥 / 分片残留 / cookie finally | 对应单测 |
| 07 | **P1** 前端：全选错乱 / 版本竞态 / 轮询刷屏 / 请求序号 / 双跑 | vitest |
| 08 | **P1** 后端杂项：chat max_tokens / asr_admin 原子性 / 标签失败不写库 / options null | 对应单测 |
| 09 | **P2** 机械清理：`.replace('.txt')` × 3 / try-finally / 上限 / 空更新 / 审计尾部读 / 注释 | 对应单测 |
| 10 | **安全** 破坏性端点 localhost 限制 | 新增测试：非 localhost 请求被拒 |
| 11 | **提示词** 未知维度值告警（不改正文） | 对应单测 |
| 12 | 全量 verify + 前端产物重建 + CONTEXT/spec 回写 | `verify.py all` |

## 关键设计决策

1. **P0-1 用抛异常而非返回哨兵**：调用方已有 try/except → `finish_job(False)`，无需改两处调用点，
   且能从 `jobs.error` 直接看到「第 2/5 批失败 + 可行动建议」。返回哨兵则要改两处 `startswith` 判定，
   且未来新增调用点极易漏判。
2. **P0-3 版本号递增而非清空历史**：`save_summary_version` 改 `INSERT OR REPLACE` → 版本号取自
   `MAX(version)+1`；并发用 `UPDATE jobs SET summary_version=? WHERE id=? AND summary_version=<读到的值>`
   乐观锁，冲突则重读重试一次。**不清空 `summary_history`**——用户归档的版本是资产。
3. **P0-4 双重防护**：抖音侧改 `.part` 原子改名（治本）；续传扫描对候选做 ffprobe 校验（兜底，
   同时覆盖其它平台的历史残留）。只做前者不足以清理已经存在的坏文件。
4. **安全切片只加限制不改绑定**：`SERVER_HOST` 保持 `0.0.0.0`（手机访问是既有能力），
   对 `POST /api/jobs/clear`、`DELETE /api/models/{key}` 等无认证破坏性端点复用
   `main._is_localhost_origin` 的同源判定逻辑，抽成可复用依赖。
5. **不引入新依赖**：并发控制用 `asyncio.Event` / `threading.Lock`，文件原子写用 `Path.replace`。

## 影响面（codegraph）

- `ai_process` 调用点：`main.py`、`routers/jobs_ai.py`（均为主 Agent 所有，契约已对齐）
- `generate_tags` 调用点：`main.py`、`routers/jobs_ai.py`（同上）
- `_run_ytdlp` 调用点：`downloader.py` 内部 3 处（已有 TimeoutExpired 分支）
- `_delete_chat_from` 调用点：`routers/chat.py` 两处（级联端点保留给「编辑」用）
- `save_summary_version` 调用点：`main.py`、`routers/jobs_ai.py`

## 回滚

逐 commit revert。无 schema 变更、无数据迁移。唯一涉及历史数据语义的是 P0-3，
其效果是把「已经被覆盖的 v1」恢复为「不再被覆盖」——修复方向单调安全。