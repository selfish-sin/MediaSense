# 10-07 追问联网搜索换引擎 + 双模式 - Design

## 架构

### 1) 引擎链（`backend/services/web_search.py`，唯一实现不变）

```
web_search_collect(query, ...)
  1. ollama_search()      ← 免费主路径（POST {base}/api/web_search，Bearer Key，max_results≤10，正文长）
  2. tavily_search()      ← 配 Key 才走（POST {base}/search，Bearer Key，basic=1 credit/次，max_results≤20）
  3. firecrawl_search()   ← 配 Key 才走（POST {base}/v2/search，country=CN 中文定向；免 Key 实测 403 不参与）
  4. ddgs_search()        ← 保留，免 Key 兜底
逐级 try：未配 Key 跳过并记入 reasons；失败原因收集进 errors[]，全失败返回 SearchOutcome(error=可行动汇总)——失败不静默契约不变。
```

- Key 解析（env → config.py 两级，均 gitignored 不入库）：
  `OLLAMA_SEARCH_API_KEY`（申请：ollama.com/settings/keys）、`TAVILY_API_KEY`（app.tavily.com）、
  `FIRECRAWL_API_KEY`（firecrawl.dev）。
- `OLLAMA_SEARCH_BASE = "https://ollama.com"`、`TAVILY_SEARCH_BASE = "https://api.tavily.com"`、
  `FIRECRAWL_SEARCH_BASE = "https://api.firecrawl.dev"` 默认可 env 覆盖；超时 20~25s；
  代理复用 `resolve_search_proxy()`（ollama 实测直连/代理均通）。
- 结果映射进统一 source 结构 `{title,url,snippet,media,date}`；ollama/tavily 的正文较长，
  snippet 截断上限做成参数（strict 500 / broad 900），`_fmt_sources` 跟随。
- 新增 `ollama_configured()` / `tavily_configured()` / `firecrawl_configured()`；
  `describe_search_backends()` 输出四后端 + proxy。
- 错误文案可行动（Key 来源 ting-ear 适配器实测错误码语义）：
  ollama 401/403 →「Key 无效，到 ollama.com/settings/keys 重新复制」、429 限流、402 额度；
  tavily 401/403 →「到 app.tavily.com 重新复制」、432/433 额度耗尽、429 限流（读 Retry-After）；
  firecrawl 401/403 →「Key 无效，到 firecrawl.dev 检查」、402 额度（区别于 tavily 的 432）。
- **智谱代码整体删除**：`zhipu_search`/`resolve_zhipu_key`/`zhipu_configured`/`ZHIPU_SEARCH_ENDPOINT`/
  `ENGINE_MAP`/`SEARCH_QUERY_MAX` 全删；config.py 保留 `ZHIPU_API_KEY` 字段（seed_models.py LLM 播种仍引用，
  与搜索无关）。

### 2) 追问双模式（`backend/routers/chat.py`）

- 契约：`ChatMessage` 增 `search_mode: Optional[Literal['strict','broad']] = None`；
  生效模式 = `search_mode or ('strict' if enable_search else 'off')`（旧客户端向后兼容）。
- 检索编排：
  - strict：现状不变——`web_search_collect(question)` 单次。
  - broad：`asyncio.gather` 并行两路：
    q1 = question 原文；q2 = `{job 标题} {question}`（去重 URL 后合并，总条数上限 12）。
    单路失败不拖整体（gather(return_exceptions=True)，任一路有结果即用；全失败 error 汇总透出）。
- 提示词分支（`_build_messages` 加 mode 参数）：
  - strict：现文案不变（严格依据背景资料 + 编号引用 + 找不到明说）。
  - broad：改为「围绕用户问题广泛、深入地回答；视频转录原文与已有分析仅作背景参考，不是约束；
    优先综合联网检索资料与你自身知识展开；检索到的资料必须用 [1][2] 编号标注并在文末列『## 参考来源』；
    不编造来源」。
- SSE `search` 事件增 `mode` 字段；ok 事件 provider 值新增 `ollama`。

### 3) 前端（`ChatPanel.vue` + `utils/chatExport.js`）

- 布尔开关 `el-switch` → 三态 `el-select size=small`：`off 关闭 / strict 严格依据材料 / broad 广泛收集`，
  前缀标签「联网」，tooltip 说明两模式差异；payload 从 `enableSearch` 改为 `searchMode`。
- `searchLabel` 支持 provider=ollama（显示 Ollama）与 broad 模式文案（「广泛检索」）。
- 标签纯函数放 `utils/chatExport.js`（或就近纯函数文件）以便 vitest 覆盖。

### 4) 周边同步

- `research_processor.py`：搜索方式标签改按 `describe_search_backends()` 顺序（Ollama→Tavily→Firecrawl→DDGS）。
- `config.example.py`：三个新 Key 占位 + 注释（申请入口）；`config.py`（gitignored）迁入 ting-ear 的 OLLAMA key（Tavily/Firecrawl 留空待用户供号）。

## 契约

`POST /api/jobs/{job_id}/chat/sessions/{session_id}/stream` 请求体增字段：
```json
{ "content": "...", "model": "", "search_mode": "strict|broad", "enable_search": false }
```
`search_mode` 与 `enable_search` 并存时 `search_mode` 优先；均缺省 = off。
SSE 事件：`{type:"search", status:"searching|ok|failed", mode?, provider?, count?, message?}`。

## 数据模型
无表变更。sources JSON 结构不变。

## 回滚形态
三个独立 commit 单元（引擎链 / 追问模式 / 前端+周边），任一出问题单独 revert；
契约向后兼容，前端不发包即回旧行为。

## 协作模式
后端先行（引擎链+契约+测试），前端随后，最后真网联调（重启服务浏览器实测两模式）。
