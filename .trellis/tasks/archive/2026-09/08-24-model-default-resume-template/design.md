# Design — 模型默认 / 失败重试 / 新增模板优化

## D1 默认模型设置

### 存储与读取
- 新表 `app_settings(key TEXT PRIMARY KEY, value TEXT)`（`CREATE TABLE IF NOT EXISTS`，老库自动补表，无迁移成本）。
- 默认模型 = 行 `('default_model', <model_key>)`。
- `db/model_repo.py` 新增：
  - `get_default_model_key() -> str`：读 `app_settings`；值存在且 `get_model_by_key` 仍找得到则返回，否则回退 `config.DEFAULT_MODEL`。
  - `set_default_model(key) -> bool`：校验模型存在，`INSERT OR REPLACE` 到 `app_settings`。

### 接口
- `GET /api/models`：`default` 字段改为 `get_default_model_key()`（前端 `modelStore.defaultModel` 已消费该字段，无需改协议）。
- 新增 `POST /api/models/default`，body `{"key": ...}`；模型不存在 → 404；成功 → `{"success": true, "default": key}`。

### 后端运行时回退点统一（前端总是显式传 model，这里是 API 直调兜底）
- `routers/jobs.py`：batch 提交 `model = body.get("model", DEFAULT_MODEL)`（742/751 两处）、resummarize `body.get('model', job.get('model', DEFAULT_MODEL))`（534）。
- `routers/translate.py:48-49`：`req.model or get_default_model_key()`。
- `db/job_repo.py:40` `create_job` 默认参数。
- `main.py:376` `job.get('model', DEFAULT_MODEL)`。
- `schemas.py` 的静态 `Field(DEFAULT_MODEL)` **保持不动**：仅客户端缺省时兜底，前端从不缺省；避免 pydantic 版本相关的 default_factory 风险。

### 前端
- `modelsApi.setDefault(key)` → `POST /models/default`；`modelStore.setDefaultModel(key)` action（成功后 `loadModels()` 刷新）。
- `ModelsView.vue`：操作列加「设为默认」（当前默认行禁用并显示「默认」tag）。
- `HomeView.vue`：
  - `loadModels()` 完成后：若 `!modelTouched && isLegacyDefault(selectedModel)`，`selectedModel = modelStore.defaultModel`（`isLegacyDefault` = `'' | 'glm-4.5-air' | 'deepseek-v4-flash'`）。
  - 字数推荐 watcher 仅在 `!modelTouched && isLegacyDefault(selectedModel)` 时生效 → 用户设置的默认模型永不被自动推荐覆盖；旧行为（无默认时按字数推荐）保留。
  - `applyJobConfig`（rerun/restore 恢复任务）设置 `selectedModel = job.model` 的现有逻辑不动，恢复任务优先用任务模型。
- `ResummarizeDialog.vue`：打开时 `model = settings.selectedModel || modelStore.defaultModel || 'glm-4.5-air'`。

## D2 失败重试总结（仅重跑 AI 步骤）+ 根因修复

### 根因（本次总结失败）
`backend/services/ai_processor.py:128`：
```python
delta = chunk.get('choices', [{}])[0].get('delta', {})
```
OpenAI 兼容接口（阿里云 MaaS）在流式响应末尾发送 `choices: []` 的 usage chunk——`dict.get(key, default)` 只在 **key 缺失** 时返回默认值，`choices=[]` 时返回空数组，`[0]` 抛 `IndexError`。该异常在 `_stream` 内部、整个流式内容已生成完毕后抛出 → 被 `except Exception` 捕获 → 返回哨兵 `[处理失败: list index out of range]` → `main.py:803` 显式失败。日志佐证：POST 200 后约 108s 才报错（流读完，最后一个 usage chunk 引爆）。

修复：
```python
choices = chunk.get('choices') or []
delta = choices[0].get('delta', {}) if choices else {}
```
`routers/models.py:109` 测试接口同模式加固（非流式，防御性）。

### resummarize_job 放宽（`routers/jobs.py`）
- 状态白名单：`job['status'] not in ('completed', 'failed')` → 400（放宽 failed，cancelled 仍拒绝——cancelled 无保留文本语义，且 txt 存在性检查已兜底大部分场景；保守起见 cancelled 不放宽）。
- 保留 `files.txt` 存在且非空校验 → 下载/转录阶段失败的任务自然被拒。
- `ai_process` 返回后增加哨兵检查（镜像 `main.py:803`）：
  ```python
  if isinstance(result_text, str) and result_text.lstrip().startswith('[处理失败'):
      raise Exception(result_text.strip().strip('[]'))
  ```
  异常落入现有 `except` → `update_job(status='failed', error=...)` + HTTP 500，绝不把错误串当结果写盘。

### 前端 ResultView
- 结果卡片顶部新增失败横幅（`job.status === 'failed'` 时渲染，不依赖 tab）：显示 `job.error`，按钮「重新总结」（`openResummarize()`）+「到工作台重做」（`goToWorkbench()`），文案注明「只重跑 AI 总结，无需重新下载/转录」。
- 「重新总结」按钮可用条件：`!isStreaming && (status === 'completed' || status === 'failed') && files.txt 存在`。
- 流式监听/提交路径复用现有 `onResummarizeSuccess`，无需新协议。

## D3 新增模型模板优化（ModelsView 前端为主）

- 服务商预设常量表 `PROVIDER_PRESETS`：DeepSeek / 阿里云百炼（兼容模式 dashscope）/ OpenAI / 智谱 GLM / 硅基流动 / Ollama(本地)。点击只预填 `endpoint`（+`model_id` 占位提示），key/API Key 仍需用户填（安全）。
- 拉取列表弹窗「添加」改**一步保存**：直接 `addModel({...form, key: deriveKey(id), name: id, model_id: id})`，复用已填 endpoint/api_key；endpoint 为空则提示。不再弹回表单二次提交。
- 自动派生：`handleAdd` 前 `key = form.key || deriveKey(model_id || name)`；`name = form.name || model_id || key`。`deriveKey` 沿用现有 `modelId.replace(/[^a-zA-Z0-9_-]/g, '-').toLowerCase()`。
- 端点补全（幂等）：`normalizeEndpoint(url)`：trim、去尾 `/`，非空且不含 `/chat/completions` 时补全。前端提交前 + 后端 `routers/models.py` add/update 各做一次（防直接调 API 踩坑）。

## 兼容性 / 风险
- `app_settings` 表 IF NOT EXISTS 自动建表，无迁移。
- 失败任务允许重总结是**放宽**校验，completed 路径行为不变。
- 默认模型初始化只在未手选时生效；rerun/restore 恢复任务仍用任务模型。
- 静态资源需重新构建（`npm run build` 输出到 `backend/static/`，项目惯例随源码一起提交）。

## 测试计划
- `test_model_repo.py`：fixture 增加 app_settings 表；测 get/set 默认模型、模型被删后回退、未设置回退 config。
- `test_ai_processor.py`：流式 mock 一个 `choices: [] + usage` 的 chunk，断言 `_call_ai` 不抛异常、内容完整、usage 被读取。
- `test_routers.py`：resummarize failed 任务成功路径（mock ai_process/generate_tags/update_job/save_summary_version + 真实 txt 文件）；哨兵串不写盘路径；`POST /models/default` 成功/404。
