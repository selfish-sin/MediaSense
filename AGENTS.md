# MediaSense (youtube_tool_v2) Guide

- `CONTEXT.md` 是总体规划入口（另有 `README.md` 给人类看）。
- 本地视频/音频处理工作站：下载或导入媒体，字幕优先，缺字幕回退 Whisper 转录，再做分段、摘要、翻译、导出。
- CORS 正则只允许 localhost/127.0.0.1 任意端口，不得扩成任意外部来源。
- 存储清理默认 dry-run；应用层删除必须脚本 `--apply --confirm DELETE`，且后端只允许 localhost、无活动任务时执行。
- 不把真实 API Key、cookie、数据库、下载文件、模型文件提交到仓库或写入文档。

## 工作流基础设施

- **硬性前置条件**：开始任何需要修改或调查项目的任务前，必须读取本目录的 `CONTEXT.md`、`.trellis/workflow.md`、与当前任务相关的 `.trellis/spec/` 和 active task。若本地规范文件不存在或无法读取，停止并报告，不使用项目外路径猜测规范内容。
- 任务流程由 Trellis 管理：Plan -> Execute -> Finish。
- 长期约定写入 `.trellis/spec/`，当前任务工件写入 `.trellis/tasks/<name>/`，临时想法写入 `ideas.md`。
- 修改代码前先运行 `codegraph explore "<目标符号或问题>"`；没有可靠索引时明确记录不适用原因。

## CONTEXT.md 维护（强制）

- 任务归档时必须同步 CONTEXT.md（M/L 级强制，S 级可跳过）
- 触发条件：新增/删除/重命名源码文件、API/路由/IPC 变化、数据模型变化、架构变化、新坑点、功能状态变化、启动方式变化
- 文件索引表必须有"何时读"列——标注"改什么功能时需要读它"
- 行号/函数名与实际代码一致，过时即更新
- 新文件加入索引，删除的文件移除
- 当前状态区块每次归档刷新
- 总长 ≤ 300 行，超出拆子文档

## 工具优先级（四件套）

- 代码定位：CodeGraph > grep/搜索
- 联网检索：Exa MCP
- 浏览器/调试：DevTools MCP
- 任务流：Trellis
