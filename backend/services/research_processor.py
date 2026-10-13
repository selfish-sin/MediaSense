"""
YouTube Tool V2 - 深度研究模式处理器
转录→提取主题→联网检索逐主题扩展→合并博文
触发：summary_type='deep_research' 或 dimensions.depth='research'

搜索策略：统一走 `services/web_search.py`（Ollama → Tavily → Firecrawl → DDGS 逐级回退）。
本模块**不再自建搜索实现**——2026-09-16 收敛前，这里内联了一份智谱 chat+tools 调用，用的是
只读环境变量的 `config.ZHIPU_API_KEY`，未配 Key 时整条链路静默失效（表现为"联网模式没用"）；
且该内联解析把数组形态的 `message.web_search` 当 dict 取值，即使配了 Key 也拿不到来源。
研究段落由用户自己选的 AI 模型（_call_ai）撰写，与搜索后端解耦。
"""
import logging
from typing import Dict, List, Optional, Tuple

from config import DEFAULT_MODEL
from db.model_repo import get_model_by_key
from services import web_search as web_search_service
from services.ai_processor import _call_ai, _record_token_usage

logger = logging.getLogger(__name__)

# 每个研究主题检索条数（比追问多取一些，便于展开 800-1500 字）
RESEARCH_SEARCH_RESULTS = 6
# 检索摘要截断上限：沿用 web_search 的 strict 500 / broad 900 既有口径（routers/chat.py 同源）——
# 深度研究要把网页正文喂给模型写 800-1500 字的论证段落，属 broad 档，故取 900。
# （旧代码传的是签名里不存在的 content_size="high"，每主题必抛 TypeError。）
RESEARCH_SNIPPET_LIMIT = 900


def _add_usage(usage: Optional[Dict[str, int]], in_tok: int, out_tok: int) -> None:
    """把一次 `_call_ai` 的真实 usage 累加进累计器（可选出参，调用方不传则只统计不回写）。"""
    if usage is None:
        return
    usage['input'] = usage.get('input', 0) + (in_tok or 0)
    usage['output'] = usage.get('output', 0) + (out_tok or 0)


# ============ 主题提取 ============

async def _extract_topics(
    text: str, title: str, model_cfg: Dict,
    sio=None, job_id: str = '', bypass_cache: bool = False,
    usage: Optional[Dict[str, int]] = None,
) -> List[str]:
    """从转录文本提取 3-8 个研究主题

    ★ usage（可选出参，2026-10-13）：真实 usage 累加进去——本函数此前丢弃 `_call_ai`
      的 token，导致深度研究整条最贵路径在统计里恒记 0。失败路径（哨兵串）同样计入。
    """
    if sio:
        from socket_handlers import emit_log
        await emit_log(sio, job_id, '🔬 深度研究：正在提取研究主题...')

    snippet = text[:20000]
    prompt = f"""你是一位学术研究助理。请从以下视频转录内容中提取 3-8 个值得深入研究的关键主题。

视频标题：{title or '未命名'}

转录文本（前 20000 字）：
{snippet}

请直接输出主题列表，每行一个：
- 主题名称（一句话描述）
- 主题名称（一句话描述）

要求：
1. 主题要有研究价值
2. 覆盖：核心概念、背景知识、争议点、应用场景、相关领域
3. 每个主题简短精准，不超过 15 个字
4. 只输出列表，不要解释"""

    content, in_tok, out_tok = await _call_ai(
        model_cfg, prompt, timeout=120.0,
        sio=sio, job_id=job_id, round_label='提取主题',
        bypass_cache=bypass_cache,
    )
    _add_usage(usage, in_tok, out_tok)
    if content.startswith('[处理失败'):
        return ['核心内容分析', '背景知识扩展', '关键概念解析']

    topics = []
    for line in content.split('\n'):
        line = line.strip().lstrip('-').lstrip('*').lstrip('0123456789.').strip()
        if line and len(line) > 2:
            topics.append(line)
    return topics[:8] if topics else ['核心内容分析', '背景知识扩展', '关键概念解析']


# ============ 逐主题扩展（联网检索 → AI 撰写段落） ============

async def _expand_topic(
    topic: str, transcript_snippet: str,
    model_cfg: Dict, sio=None, job_id: str = '', bypass_cache: bool = False,
    usage: Optional[Dict[str, int]] = None,
) -> str:
    """对一个主题做联网检索，并交给用户所选模型写成研究段落。

    搜索后端由 `services/web_search` 统一决定（Ollama → Tavily → Firecrawl → DDGS），本函数只负责拼提示词；
    检索失败时如实告知（日志 + 提示词内注明），不假装有资料。

    ★ usage（可选出参）：同上，累加本次撰写的真实 usage（失败哨兵同样计入）。
    """
    outcome = await web_search_service.web_search_collect(
        topic, max_results=RESEARCH_SEARCH_RESULTS,
        snippet_limit=RESEARCH_SNIPPET_LIMIT,
    )

    if sio:
        from socket_handlers import emit_log
        if outcome.ok:
            provider_label = {'ollama': 'Ollama', 'tavily': 'Tavily', 'firecrawl': 'Firecrawl'}.get(outcome.provider, 'DuckDuckGo')
            await emit_log(sio, job_id, f'🔬 检索到 {len(outcome.sources)} 条资料（{provider_label}）: {topic}')
        else:
            await emit_log(sio, job_id, f'🔬 联网检索不可用（{outcome.error}），仅基于视频内容扩展: {topic}')

    search_text = outcome.text or '(无搜索结果)'
    prompt = f"""你是一位深度研究分析师。请对以下主题进行深入扩展研究。

研究主题：{topic}

原始视频转录片段（供参考背景）：
{transcript_snippet[:8000]}

网页搜索结果（实时联网资料，请优先采用其中的真实信息）：
{search_text[:6000]}

请严格按以下 Markdown 结构输出：

### {topic}

**研究概述**（3-5 句话概括该主题的研究现状和核心问题）

**深入分析**（800-1500字，包含：背景脉络、关键论证、不同观点、数据支持、学术争议）

**延伸思考**（该主题的延伸问题、跨学科联系、实践启示）

**参考来源**（列出上面搜索结果中的来源标题或链接，注明对应编号）

要求：
1. 优先引用搜索结果中的真实信息，不编造事实；不确定的信息标注"据搜索结果显示"
2. 若搜索结果为空，明确说明该部分基于视频内容与模型自身知识
3. 保留思辨性，不要写成百科词条
4. 用中文"""

    content, in_tok, out_tok = await _call_ai(
        model_cfg, prompt, timeout=180.0,
        sio=sio, job_id=job_id, round_label=f'扩展·{topic[:15]}',
        bypass_cache=bypass_cache,
    )
    _add_usage(usage, in_tok, out_tok)
    if not content or content.startswith('[处理失败'):
        return f'### {topic}\n\n（该主题研究扩展暂不可用）'
    return content


# ============ 主流程 ============

async def research_process(
    text: str,
    title: str = '',
    model: str = DEFAULT_MODEL,
    sio=None,
    job_id: str = '',
    bypass_cache: bool = False,
) -> Tuple[str, int, int]:
    """深度研究模式主流程（公开入口：负责**无论成败都记账**）。

    搜索后端：`services/web_search`（Ollama → Tavily → Firecrawl → DDGS 逐级回退），
    与「用户选的 AI 模型」解耦——搜索 Key 走 config.py / 环境变量，不需要配成模型。

    ★ 记账放在这里而不是实现体末尾（2026-10-13 独立复核补）：深度研究要烧
      1 次主题提取 + N 次扩展写作，调用方 `main.py` 又用
      `asyncio.wait_for(..., 3600.0)` 包着它。超时/中途抛错时 N+1 次付费调用
      已经花掉了，实现体末尾那行 `_record_token_usage` 根本执行不到，
      统计仍显示 0——正是本次要消灭的症状。`finally` 对 CancelledError
      （BaseException）同样生效，故超时/取消/异常三种都能记账。
    """
    usage = {'input': 0, 'output': 0}
    try:
        return await _research_process_impl(
            text, title, model, sio=sio, job_id=job_id,
            bypass_cache=bypass_cache, usage=usage,
        )
    finally:
        # 记账口径与 ai_process 相同：按**本次请求的模型 key** 记总账
        #（research 用固定单模型 _call_ai，无换模型信息可用）。
        if usage['input'] or usage['output']:
            _record_token_usage(model, usage['input'], usage['output'], job_id)


async def _research_process_impl(
    text: str,
    title: str = '',
    model: str = DEFAULT_MODEL,
    sio=None,
    job_id: str = '',
    bypass_cache: bool = False,
    usage: Optional[Dict[str, int]] = None,
) -> Tuple[str, int, int]:
    """实现体：usage 由调用方持有并透传，本函数只负责往里累加。"""
    model_cfg = get_model_by_key(model) or get_model_by_key(DEFAULT_MODEL)
    if not model_cfg:
        raise Exception(f'模型 {model} 不存在')

    backends = web_search_service.describe_search_backends()
    if backends.get("ollama"):
        search_label = "Ollama Web Search（免费）"
    elif backends.get("tavily"):
        search_label = "Tavily"
    elif backends.get("firecrawl"):
        search_label = "Firecrawl"
    elif backends.get("ddgs"):
        search_label = "DuckDuckGo（免费兜底）"
    else:
        search_label = "不可用（未配任何搜索 Key 且未安装 ddgs）"

    if sio:
        from socket_handlers import emit_progress, emit_log
        await emit_progress(sio, job_id, 'ai', 0, '🔬 深度研究模式启动...')
        await emit_log(sio, job_id, f'🔬 搜索方式: {search_label}')
        await emit_log(sio, job_id, f'🔬 AI 模型: {model}')

    # ★ token 累计（2026-10-13 修复 P1-5）：深度研究是单任务最贵路径（1 次提取主题 +
    #   N 次扩展写作），此前全程丢弃 `_call_ai` 的 usage 并 `return article_md, 0, 0`，
    #   结果页/统计页显示「零消耗」。失败路径（哨兵串）同样已烧 token，一并计入——
    #   口径与 ai_process 一致：花了钱就要可见。累加器由公开入口 research_process
    #   持有（它负责在 finally 里记账，见该函数 docstring）。
    if usage is None:
        usage = {'input': 0, 'output': 0}

    # Step 1: 提取主题
    topics = await _extract_topics(text, title, model_cfg, sio, job_id,
                                   bypass_cache=bypass_cache, usage=usage)

    if sio:
        from socket_handlers import emit_log
        await emit_log(sio, job_id, f'🔬 识别到 {len(topics)} 个研究主题: {", ".join(topics[:3])}...')

    # Step 2: 逐主题扩展
    transcript_snippet = text[:60000]
    expanded_sections = []

    for i, topic in enumerate(topics):
        pct = int(10 + i / len(topics) * 80)
        if sio:
            from socket_handlers import emit_progress, emit_log
            await emit_progress(sio, job_id, 'ai', pct, f'🔬 [{i+1}/{len(topics)}] 研究: {topic[:20]}')
            await emit_log(sio, job_id, f'🔬 [{i+1}/{len(topics)}] 开始研究: {topic}')

        section = await _expand_topic(
            topic, transcript_snippet, model_cfg, sio=sio, job_id=job_id,
            bypass_cache=bypass_cache, usage=usage,
        )
        expanded_sections.append(section)

    # Step 3: 合并
    if sio:
        from socket_handlers import emit_progress
        await emit_progress(sio, job_id, 'ai', 92, '🔬 合并研究文章...')

    article_parts = [
        f"# 🔬 深度研究：{title or '视频内容研究'}\n",
        f"> 本文由 AI 基于视频内容深度扩展生成，使用{search_label}补充资料。\n",
        f"> 共 {len(topics)} 个研究主题。\n\n",
        "## 研究主题概览\n",
    ]
    for i, t in enumerate(topics):
        article_parts.append(f"{i+1}. {t}\n")
    article_parts.append('\n---\n')

    for section in expanded_sections:
        article_parts.append(section)
        article_parts.append('\n')

    import time
    article_parts.append(f'\n> 生成时间: {time.strftime("%Y-%m-%d %H:%M:%S")}\n')
    article_md = '\n'.join(article_parts)

    if sio:
        from socket_handlers import emit_progress, emit_log
        await emit_progress(sio, job_id, 'ai', 100, '🔬 深度研究完成！')
        await emit_log(sio, job_id, f'🔬 深度研究完成: {len(article_md)} 字符')

    # 记账不在这里做：由公开入口 research_process 在 finally 里统一落盘，
    # 否则超时/异常路径烧掉的 token 会整段丢失（独立复核 2026-10-13 补）。
    return article_md, usage['input'], usage['output']
