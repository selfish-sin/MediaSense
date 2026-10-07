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

## 整体评估

作为本地媒体工作站，工程基础较扎实：输入策略、引擎回退和命名规则已有清晰单点，
测试能覆盖多类历史回归，数据库/模型目录测试隔离也有实际事故后的加固。
主要短板是复杂边界的正确性：缓存选项与写入联动、可选参数对重试安全的影响，
以及静态检查未进入后端验证入口。这轮发现的问题都是原有全绿测试没拦住的分支。

## 剩余问题与改进优先级

### P1：ASR 缓存遗漏模型参数（已复现，未修改）

- 位置：`ai_cache.lookup_transcription/store_transcription` 与 `transcriber.transcribe_audio`。
- 键只有音频哈希、引擎、语言和说话人分离，遗漏 Whisper 档位及云端 provider/model。
- 隔离复现使用真实转录异步入口和真实缓存键函数，内存替代 DB、临时文件替代真实媒体，
  模拟推理仅用于记录本次选择，不做实际模型调用：

```text
requested=base result=base
requested=medium result=base
inference_calls=['base']
```

- 说明：同音频换更高质量的模型仍拿旧结果；云端换 provider/model 同样没有进入键。
- 临时方案：勾选“忽略缓存”。永久修复建议新建 M1 任务，扩展 ASR 缓存身份（版本化键、
  请求模型档位、相关云端端点与模型标识），读写共用唯一身份构造函数；避免继续命中旧键，
  不删除历史任务/数据库；再审查回退结果缓存的实际引擎溯源。
- 验收：同参数命中、换 Whisper 档位未命中、换云端 provider/model 未命中，
  现有免费优先与取消链保持成立。涉及两模块契约，超出本轮 S 级定向修复。

### P2：后端静态检查未进入质量门禁

- `verify.py` 后端仅跑 pytest，ruff 配置存在但项目 venv 未安装 ruff。
- 本次 NameError 是静态检查能直接识别的缺陷，原 960 个测试全绿仍漏掉。
- 建议声明开发依赖并把 ruff（至少 F821 等明确错误）接进 verify/CI，先修基线再收紧规则。
  前端已有 eslint；未发现独立 Python 类型检查入口。

### P2：补测“操作前后”的真实函数路径

- 原缓存测试只验证 bypass 是否发请求，没有验证刷新后再读是否得到新值；
  既有兜底测试大量替换核心调用，未覆盖真实流式异常分支。
- 建议在缓存、取消、回退、切页恢复等关键链路保留小规模真实函数集成测试，
  只模拟网络/识别引擎等外部边界，检查请求次数、落盘结果和最终状态。
- 全量测试仍有三条超时用例未 close 被替代 wait_for 接收的协程；应清理测试装置后开启警告门禁。

### P2：依赖可复现性与维护

- 前端有 package-lock，Python requirements 主要采用最低版本，缺可复现锁定。
- `bilibili-api-python` 已被规范列为退役，Python 源码未找到使用，依赖列表仍保留；
  清理前应把抖音签名直接使用的 cryptography 明确声明，避免依赖某个退役包间接带入。
- FastAPI on_event 与 TestClient 出现弃用警告，应纳入后续升级计划。

### P3：任务与规范记录收敛

- 两个未归档任务 `10-05-bg-gallery-indexeddb`、`10-05-10-05-douyin-url-pipeline` 仍为 planning，
  而 CONTEXT 已记录对应功能交付；可能误导后续会话重复实现。建议核对提交后收尾，当前保留原任务。
- backend spec 测试基线仍为 748，CONTEXT 中最近历史值为 961，本次实测基线 960；
  不能用旧文档计数作为“当前验证已执行”的证据。
- CodeGraph 显示 up to date 但目标查询多次漏目标/混入构建产物，定位结果需核对源码。

## 修复后验收

- 后端全量 `venv/Scripts/python.exe -m pytest tests/ -q --tb=short`：968 passed，退出码 0。
- 本轮增加 8 条回归（参数化展开）：刷新缓存 2、断连 4、缓存建连失败 2；
  断连无正文两条原本就通过，其余 6 条在修复前失败，修复后全部通过。
- 前端源码未修改，沿用本轮初始 269 测试 / eslint / build 成功证据。
- 三个逻辑单元独立提交；没有 push。后端业务修复需下次重启服务才进入正在运行的进程。

## Bug Analysis: 缓存与流式边界

### 1. Root Cause Category

- D（测试缺口）：只测操作当次、或替换掉核心函数，漏了第三次读取和真流异常。
- E（隐含假设）：将“是否传 state”误当成重试安全条件，将“建立连接一定成功”放在 try 外。

### 2. Why Fixes Failed

本轮每项均一次修复通过；问题在此前测试的覆盖粒度，不是断言数量不足。

### 3. Prevention Mechanisms

| 优先级 | 机制 | 行动 | 状态 |
|---|---|---|---|
| P1 | 测试 | 真实函数 + 外部边界模拟，验证后续读取与输出次数 | 已完成 8 条 |
| P1 | 结构 | 输出状态由函数自己维护；bypass 只控制读取 | 已完成 |
| P2 | 静态检查 | ruff 纳入后端 verify/CI | 建议 |

### 4. Systematic Expansion

- 横向检查流式/非流式两入口，均补了缓存刷新。
- 检查直接调用方，校正/深度研究同样从断连修复受益。
- 转录缓存另有模型参数遗漏，已复现并列为下一任务。

### 5. Knowledge Capture

- 已新增 `.trellis/spec/backend/ai-call-cache.md`，含签名、契约、错误矩阵和具体测试点。
- 已更新 backend spec 入口与 CONTEXT；项目没有 `src/templates/markdown/spec/` 镜像，模板同步不适用。

## 验证边界

这是针对关键链路的质量检查，并非所有功能、第三方真实网络与媒体识别准确率的全面认证。
没有执行真实付费 ASR/LLM 调用；业务回归使用真实函数、隔离数据库及模拟网络。
