# MediaSense 质量检查（2026-10-07）

## 范围与方法

检查当前工作区的任务编排、AI 调用与失败回退、转录缓存、前端状态恢复、验证入口和依赖管理。
以 CodeGraph 为代码定位入口；无 graph MCP 工具，使用本地 `codegraph explore`。
索引报告 up to date，但查询缓存函数与 `_post_chat_once` 未返回目标实现且混入无关符号，
对这些目标补充读取当前源码。前后端规范已读取；仓库未配置共享 `spec/guides/`。
没有修改真实运行数据、调用付费模型、推送或重启服务。

## 自动检查基线

- `backend/venv/Scripts/python.exe verify.py all`：退出码 0。
- 后端：960 passed，6 warnings；前端：269 passed（22 文件）；eslint 和 Vite build 通过。
- 后端 ruff 未安装在项目 venv；`verify.py` 当前只执行后端 pytest，不执行 Python 静态检查。
- 警告包括 FastAPI 启动事件弃用、TestClient 依赖迁移，以及超时测试未清理协程。

## 修复记录

### 忽略缓存后的新 AI 结果未更新缓存（P1，S 级定向修复）

- 位置：`backend/services/ai_processor.py` 的 `_call_ai` 与 `_post_chat_once`。
- 触发：已有结果 A → 忽略缓存生成 B → 再次正常运行。
- 原行为：绕过读取时连缓存键也不生成，B 无法写回，下一次仍返回 A。
- 修复：始终生成缓存键，只有读取受 `bypass_cache` 控制；成功新结果照常写回。
- 回归：真实函数 + httpx MockTransport，覆盖流式与非流式的三次调用，验证只有两次请求。
  修复前两条用例均失败（第三次返回旧结果）；修复后 AI/cache/fallback 三组 97 条全通过。

### 流式断连守卫依赖可选出参、并掩盖原因（P1，S 级定向修复）

- CodeGraph 查询 `_call_ai` 后读取当前源码，确认断连分支在外层引用 `_stream` 内的计数变量。
- 真实 httpx 流式故障注入证明：传 state 时返回 NameError，未传 state 时在已推送正文后仍重试，
  导致前端可能累积“第一次的半截 + 第二次的完整”，校正和深度研究都直接调用该函数。
- 修复：函数内部始终维护输出状态；日志使用可访问的信息，保留明确断连哨兵原因。
- 四条回归覆盖传/不传 state × 有/无正文；修复前两条失败、两条通过；修复后三组 101 条全通过。

### 缓存建连异常会打断主流程（P2，S 级定向修复）

- 位置：`backend/services/ai_cache.py` 的 `cache_get` 和 `cache_put`。
- 建立 SQLite 连接在 try 外，建连或 WAL 初始化抛错时直接冒泡；读取可中断 AI/ASR，
  写入可将已生成的 AI 内容变为失败。与模块声明的“缓存故障只告警不影响主流程”冲突。
- 故障注入：让 get_connection 抛 OperationalError，两条回归修复前均失败。
- 修复：建连纳入异常边界，保持可见告警并返回 miss/False；仅在成功取得连接后释放。
- 验证：AI/cache/fallback 三组 103 条全通过。

## 待核实与改进候选

- ASR 缓存键只有音频哈希、引擎、语言和说话人分离，遗漏 Whisper 档位及云端 provider/model。
  需要扩展键契约并验证跨设置不串结果。
- Python 依赖以最低版本为主，缺可复现锁定；`bilibili-api-python` 已被规范列为退役但仍在依赖中。
- 两个未归档任务仍为 planning，而 CONTEXT 已记录对应功能交付；只记录差异，保留原任务。

## 验证边界

这是针对关键链路的质量检查，并非所有功能、第三方真实网络与媒体识别准确率的全面认证。
没有执行真实付费 ASR/LLM 调用；业务回归使用真实函数、隔离数据库及模拟网络。
