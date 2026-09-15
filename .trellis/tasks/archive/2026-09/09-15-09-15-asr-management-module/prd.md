# ASR 管理模块（云端 + 本地） - PRD

## 需求

我要解决什么问题：转录引擎的配置目前全部散落在 gitignored 的 `config.py`（改一个 Key 要改文件 + 重启），
本地模型（Whisper 四档 / FireRedASR ~1.1GB）没有任何可视化状态与生命周期管理入口。
在「模型管理」页（`/models`）新增「云端 ASR」与「本地 ASR」两个管理模块，把转录侧配置补齐成与 AI 模型同级的可管理对象。

## 具体例子

- 给「云端 ASR」页：看到内置的 MiMo（付费）/ XingChen（免费）两个 provider，直接把 API Key 填进去保存即生效（无需改文件/重启）；再新增一个自建中转或百炼 Qwen-Audio provider 并测试连接；把默认转录引擎设为免费档。
- 给「本地 ASR」页：一眼看到 Whisper 四档哪些已就绪、各占多少磁盘、FireRedASR 是否就绪/缺哪个文件；点「下载」后台拉取 FireRedASR 模型并看到进度；点「删除」释放 1.1GB。

## 验收标准

- [ ] `/models` 页一级 Tab 明确区分「LLM 模型」与「ASR 模型」两域（不混排）；ASR 域内再小分「云端 ASR / 本地 ASR」
- [ ] 视觉与现状完全一致：复用既有全局类与 Element Plus 主题，不新增配色/风格；切换 Tab 不丢状态、窄窗口不破版
- [ ] 云端 ASR：列表（名称/槽位/端点/模型/请求姿势/密钥状态/启用态）、新增、编辑、删除、启停、测试连接全部可用
- [ ] 云端 ASR 的 Key 与 AI 模型同样规则：编辑态留空 = 不修改；含 `****` 判定为密钥无效并给出重新粘贴提示
- [ ] 内置 provider（mimo/xingchen）不可删除但可编辑/停用；自定义 provider 可删
- [ ] 新增/修改 provider 后，**无需重启服务**，下次转录即生效（DB 优先于 config.py）
- [ ] 默认转录引擎可在页面设置，设置后工作台默认引擎随之变化（用户手选不被覆盖）
- [ ] 本地 ASR：Whisper 四档的「是否就绪 / 磁盘占用 / 路径」可见，可设为默认模型、可删除
- [ ] FireRedASR：就绪状态 + 五个模型文件逐项状态 + 总占用可见；可一键后台下载（有百分比进度）、可取消、可删除释放空间
- [ ] 任一操作失败都给出可行动错误文案（禁止静默失败/假成功）
- [ ] `backend/venv/Scripts/python.exe -m pytest` 全绿；`npm run test`/`lint`/`build` 全绿
- [ ] 浏览器实测：三个 Tab 走通、console 无未解释报错、暗色主题正常

## 不做项

- 不做转录引擎的运行时热重载之外的高级编排（回退链 `_FALLBACK_CHAINS` 语义冻结，不改）
- 不做 Whisper 模型在线下载（沿用现有「手动放置 models/」约定，只在 UI 里给出路径指引）
- 不做 provider 的并发/超时的分 provider 独立生效（本轮只做存储与展示，运行时仍用全局 env/config 值）
- 不做云端用量/计费统计
- 不动 AI 模型（LLM）管理模块的既有行为

## 影响范围

- 后端：`db/database.py`（新表）、新增 `db/asr_repo.py`、`services/cloud_asr.py`（配置读取改 DB 优先）、
  `services/transcriber.py`（provider 选择）、`services/local_asr.py`（下载状态机/删除/占用）、
  `routers/asr.py`（status 扩展）、新增 `routers/asr_admin.py`、`main.py`（注册路由）
- 前端：`views/ModelsView.vue`（Tab 化）、新增 `components/models/*`、新增 `stores/asrStore.js`、`utils/api.js`
- 契约：`contracts/openapi.json` 需重新导出

## 风险与回滚

- 风险等级：M2
- 不可逆操作：删除 provider（DB 行）、删除本地模型文件（磁盘）、设置默认引擎（可改回）
- 回滚方式：`git revert` 对应提交；DB 新表为纯增量（`CREATE TABLE IF NOT EXISTS`），
  旧版本代码忽略它即可；provider 读取有 config.py 兜底，DB 表为空时行为与改前完全一致。
- 本地模型删除为显式二次确认，且删除后可由下载按钮恢复。
