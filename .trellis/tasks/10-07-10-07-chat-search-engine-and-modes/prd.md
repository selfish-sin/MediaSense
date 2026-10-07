# 10-07 追问联网搜索换引擎 + 双模式 - PRD

## 需求
我要解决什么问题：追问（ChatPanel）的联网搜索主引擎（智谱 Web Search）账号欠费/套餐过期，搜索环节实际已不可用（实测本仓库未配 Key → 每次失败落到不稳定的 DDGS）；同时追问回答姿态只有一种「严格依据材料」，需要支持「围绕问题广泛收集资料」的开放模式。

## 背景（2026-10-07 实测证据）
- `backend/services/web_search.py`：智谱独立 API 优先 → DDGS 兜底；本仓库 env/config/DB 三级均无智谱 Key，实测报「未配置智谱 API Key」。
- DDGS 兜底实测「No results found」（国内网络不稳定，已知坑）。
- 用户提供兄弟项目 `D:\projects\ting-ear` 有可复用资产，实测结论：
  - **Ollama Web Search**（`POST https://ollama.com/api/web_search`，Bearer Key）：**免费**，代理/直连均通，中文结果质量好（维基/专业站，正文最长 3758 字符）。ting-ear 里的 Key 实测可用，用户后续还可再供账号。
  - **Tavily**（`POST https://api.tavily.com/search`，Bearer Key）：免费 1000 credits/月（basic=1 credit/次），中文实测优秀（ting-ear 适配器含 2026-10 实测记录）。当前无 Key，做成配 Key 即启用。
  - **Firecrawl**（`POST https://api.firecrawl.dev/v2/search`，Bearer Key，`country:'CN'` 中文定向）：免费 1000 credits/月；**免 Key 本机实测 403 不可用**，故也做成配 Key 才启用。
  - 智谱 Key 实测仍能返回结果（套餐过期转按量计费），但**用户明确决定弃用智谱**，从搜索链路整体移除。

## 具体例子
- 例1（换引擎）：追问开启联网 → 后端先走 Ollama 免费搜索，返回带正文来源，气泡显示「已联网检索 · Ollama · N 条来源」；Ollama 挂了自动落 Tavily/Firecrawl（配了 Key 时）→ DDGS，失败原因可见。用户拿到新账号后把 Key 贴进 config.py 即启用对应梯队。
- 例2（严格模式）：选「严格」提问「视频里说的经方是指什么」→ 回答严格基于转录原文 + AI结果 + 检索资料，找不到就明说，[1][2] 编号引用。
- 例3（广泛模式）：选「广泛」提问「附子现代临床有哪些用法」→ 后端并行两路检索（问题本身 + 视频标题+问题），合并去重更多来源；回答围绕问题综合检索资料与自身知识展开，材料仅作参考不作约束，检索来源仍编号引用。

## 验收标准
- [ ] 搜索链路改为 Ollama → Tavily → Firecrawl → DDGS：前三级配 Key 才参与，DDGS 免 Key 殿后；每级失败原因可行动、日志可见
- [ ] 智谱搜索从 `web_search.py` 整体移除（含 resolve_zhipu_key/ENGINE_MAP 等），LLM 侧智谱相关（seed_models、端点归一化）不受影响
- [ ] Ollama Key 从 ting-ear 迁入 `backend/config.py`（gitignored），不写入任何入库文档；`config.example.py` 提供三个新 Key 占位与申请入口注释
- [ ] 追问提供三态：关闭 / 严格（材料+检索依据）/ 广泛（围绕问题放开收集），旧 `enable_search` 契约向后兼容（True→strict）
- [ ] 严格模式行为与现状等价（单次检索 + 严格依据提示词 + 编号引用）
- [ ] 广泛模式并行两路检索合并去重，提示词放开材料约束、仍要求引用检索来源编号
- [ ] 前端气泡状态文案支持新 provider（Ollama/Tavily/Firecrawl）与模式
- [ ] research_processor 的搜索标签与新链路一致
- [ ] 后端/前端测试全绿 + verify 通过 + 重启服务真网实测两条模式

## 不做项
- 不做 LLM 生成检索词的查询扩展（用确定性的「标题+问题」组合，省一次模型调用与失败面）
- 不做搜索源配置 UI（Key 落 config.py / 环境变量，贴 Key 即启用；后续有需要再加管理页）
- 不做多 Key 轮换/Key 池（一个引擎一个 Key，用户给新号就替换）
- 不动深度研究的检索策略本身（只同步其标签文案；引擎链升级自动受益）
- 智谱搜索不保留（按用户决定整体移除，不留 retired 死代码）

## 影响范围
- `backend/services/web_search.py`（引擎链）、`backend/routers/chat.py`（契约+提示词+检索编排）、`backend/services/research_processor.py`（标签）、`backend/config.example.py`、`frontend/src/components/ChatPanel.vue`（选择器+文案）、`frontend/src/utils/chatExport.js`（模式文案纯函数）、对应测试。

## 风险与回滚
- 风险等级：M1（跨模块 + API 契约加字段，方案清晰无需切片）
- Ollama 搜索为外部免费服务，限流策略未知 → 链路天然兜底（智谱/DDGS），失败原因透出
- Key 均写 gitignored 的 config.py，不发生密钥入库
- 回滚方式：单 commit 单元，git revert 即可；契约字段向后兼容（旧客户端 enable_search 不受影响）
