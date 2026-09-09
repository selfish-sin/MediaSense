"""
YouTube Tool V2 - 深度研究模式处理器
转录→提取主题→智谱 web_search 逐主题扩展→合并博文
触发：summary_type='deep_research' 或 dimensions.depth='research'

搜索策略（按优先级）:
1. 独立智谱 web_search（用 config.ZHIPU_API_KEY，不绑定 AI 模型选择）
2. DuckDuckGo 兜底（限流+短超时）
"""
import asyncio
import logging
import re
from typing import Tuple, Dict, List

import httpx

from config import DEFAULT_MODEL, ZHIPU_API_KEY
from db.model_repo import get_model_by_key
from services.ai_processor import _call_ai
from services.httpx_errors import describe_http_error

logger = logging.getLogger(__name__)

# Zhipu API 地址（独立搜索端点，不经过用户配的 AI 模型）
ZHIPU_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
ZHIPU_SEARCH_MODEL = "glm-4-flash"  # 搜索用轻量模型，省 token

# DDGS 搜索超时和引擎限制
DDGS_TIMEOUT = 8.0   # 单次搜索总超时（秒）
DDGS_MAX_RESULTS = 3  # 最多返回条数


# ============ 独立智谱 web_search ============

async def _zhipu_web_search(
    system_prompt: str,
    user_prompt: str,
    timeout: float = 60.0,
) -> str:
    """独立智谱 web_search 调用（使用 config.ZHIPU_API_KEY，不依赖 AI 模型配置）

    即使 AI 处理用的是 DeepSeek，搜索仍走智谱联网。
    返回模型生成的文本（含搜索结果），失败返回空字符串。
    """
    if not ZHIPU_API_KEY:
        logger.warning('ZHIPU_API_KEY 未配置，跳过智谱搜索')
        return ''

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {ZHIPU_API_KEY}',
    }
    payload = {
        "model": ZHIPU_SEARCH_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "tools": [{"type": "web_search", "web_search": {"search_result": True}}],
        "temperature": 0.3,
        "max_tokens": 8000,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(ZHIPU_ENDPOINT, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            choice = data.get('choices', [{}])[0]
            msg = choice.get('message', {})
            content = msg.get('content', '') or ''
            return content.strip()
    except httpx.HTTPStatusError as e:
        logger.warning(f'智谱 web_search 调用失败: {describe_http_error(e)}')
        return ''
    except Exception as e:
        logger.warning(f'智谱 web_search 调用失败: {e}')
        return ''


# ============ DuckDuckGo 兜底 ============

def _ddgs_search(query: str, max_results: int = DDGS_MAX_RESULTS) -> List[Dict[str, str]]:
    """DuckDuckGo 搜索（非智谱模型的兜底方案，限流+短超时）

    注意：DDGS 库内部会轮询多个引擎，每个引擎默认超时很长。
    这里用 asyncio.wait_for 在 Python 层限制总超时（通过 run_in_executor 的单次调用无法限制）。
    该函数在 asyncio.to_thread 中运行，超时由调用方控制。
    """
    try:
        from ddgs import DDGS
        results = []
        # DDGS 初始化参数：限制超时
        with DDGS(timeout=DDGS_TIMEOUT) as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    'title': r.get('title', ''),
                    'url': r.get('href', ''),
                    'snippet': r.get('body', ''),
                })
        return results
    except Exception as e:
        logger.warning(f'DDGS 搜索失败: {e}')
        return []


async def web_search_collect(query: str, context: str = "") -> Tuple[List[Dict[str, str]], str]:
    """联网搜索收集（追问专用）：智谱 web_search 优先，DDGS 兜底。

    返回 (sources, search_text)：
      sources      -> [{"title","url","snippet"}]，用于前端引用源展示
      search_text  -> 已编号的搜索摘要文本，注入给回答模型作为上下文

    无论用户当前选用的「回答模型」是什么，搜索都先走智谱独立 web_search；
    仅在智谱失败（未配 key / 调用异常 / 无来源）时退回 DDGS。
    """
    sources: List[Dict[str, str]] = []
    search_text = ""

    # ---- 1) 智谱 web_search（独立端点，不绑定回答模型）----
    if ZHIPU_API_KEY:
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {ZHIPU_API_KEY}",
            }
            user_msg = f"请联网搜索并回答用户的问题，附上信息来源链接。\n\n用户问题：{query}"
            if context:
                user_msg += f"\n\n已知背景（已有分析结果节选）：\n{context[:1500]}"
            payload = {
                "model": ZHIPU_SEARCH_MODEL,
                "messages": [
                    {"role": "system", "content": "你是联网搜索助手，请使用 web_search 工具检索信息并附上来源链接。"},
                    {"role": "user", "content": user_msg},
                ],
                "tools": [{"type": "web_search", "web_search": {"search_result": True}}],
                "temperature": 0.1,
                "max_tokens": 2000,
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(ZHIPU_ENDPOINT, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                msg = data.get("choices", [{}])[0].get("message", {})
                content = msg.get("content", "") or ""
                raw = msg.get("search_action") or msg.get("web_search", {}).get("results") or []
                for s in raw[:6]:
                    title = s.get("title") or s.get("name") or "来源"
                    url = s.get("link") or s.get("url") or ""
                    snippet = s.get("content") or s.get("snippet") or ""
                    sources.append({
                        "title": title,
                        "url": url,
                        "snippet": (snippet or "")[:300],
                    })
                if sources:
                    search_text = content or _fmt_sources(sources)
                    logger.info(f"[web_search] 智谱返回 {len(sources)} 条来源")
                    return sources, search_text
        except httpx.HTTPStatusError as e:
            logger.warning(f"[web_search] 智谱联网失败，转 DDGS: {describe_http_error(e)}")
        except Exception as e:
            logger.warning(f"[web_search] 智谱联网失败，转 DDGS: {e}")

    # ---- 2) DDGS 兜底 ----
    try:
        results = await asyncio.wait_for(asyncio.to_thread(_ddgs_search, query), timeout=DDGS_TIMEOUT)
        if results:
            for r in results[:6]:
                sources.append({
                    "title": r.get("title", "来源"),
                    "url": r.get("url", ""),
                    "snippet": (r.get("snippet") or "")[:300],
                })
            search_text = _fmt_sources(sources)
            logger.info(f"[web_search] DDGS 返回 {len(sources)} 条来源")
            return sources, search_text
    except Exception as e:
        logger.warning(f"[web_search] DDGS 兜底也失败: {e}")

    return [], ""


def _fmt_sources(sources: List[Dict[str, str]]) -> str:
    """把 sources 渲染成编号文本，便于注入模型上下文"""
    lines = []
    for i, s in enumerate(sources, 1):
        line = f"[{i}] {s.get('title', '来源')}"
        if s.get("snippet"):
            line += f"\n    {s['snippet']}"
        if s.get("url"):
            line += f"\n    链接: {s['url']}"
        lines.append(line)
    return "\n\n".join(lines)


# ============ 主题提取 ============

async def _extract_topics(
    text: str, title: str, model_cfg: Dict,
    sio=None, job_id: str = '',
) -> List[str]:
    """从转录文本提取 3-8 个研究主题"""
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

    content, _, _ = await _call_ai(
        model_cfg, prompt, timeout=120.0,
        sio=sio, job_id=job_id, round_label='提取主题',
    )
    if content.startswith('[处理失败'):
        return ['核心内容分析', '背景知识扩展', '关键概念解析']

    topics = []
    for line in content.split('\n'):
        line = line.strip().lstrip('-').lstrip('*').lstrip('0123456789.').strip()
        if line and len(line) > 2:
            topics.append(line)
    return topics[:8] if topics else ['核心内容分析', '背景知识扩展', '关键概念解析']


# ============ 逐主题扩展（智谱 web_search） ============

async def _expand_with_zhipu(
    topic: str, transcript_snippet: str,
    sio=None, job_id: str = '',
) -> str:
    """用独立智谱 web_search 对一个主题做深度扩展（不依赖 AI 模型选择）"""
    system = """你是一位深度研究分析师。请对用户指定的主题进行深入扩展研究。

你会自动收到智谱搜索引擎的实时搜索结果。请基于搜索结果和你的知识，写出一个高质量的研究段落。

严格按以下 Markdown 结构输出：

### {主题}

**研究概述**（3-5 句话概括该主题的研究现状和核心问题）

**深入分析**（800-1500字，利用搜索结果中的信息，包含：背景脉络、关键论证、不同观点、数据支持、学术争议）

**延伸思考**（该主题的延伸问题、跨学科联系、实践启示）

**参考来源**（列出搜索结果中出现的来源标题或链接，注明搜索来源）

要求：
1. 必须引用搜索结果中的真实信息
2. 不编造事实，不确定的信息标注"据搜索结果显示"
3. 保留思辨性，不要写成百科词条
4. 用中文"""

    user = f"""研究主题：{topic}

原始视频转录片段（供参考背景）：
{transcript_snippet[:6000]}

请基于搜索引擎的实时结果，对上述主题进行深度扩展研究。"""

    result = await _zhipu_web_search(system, user, timeout=60.0)
    if not result:
        return f'### {topic}\n\n（该主题研究扩展暂不可用）'
    return result


async def _expand_with_ddgs(
    topic: str, transcript_snippet: str,
    model_cfg: Dict, sio=None, job_id: str = '',
) -> str:
    """DDGS 搜索 → LLM 扩展（限流+短超时兜底）"""
    # DDGS 搜索（带 Python 层超时保护）
    try:
        search_results = await asyncio.wait_for(
            asyncio.to_thread(_ddgs_search, topic, DDGS_MAX_RESULTS),
            timeout=DDGS_TIMEOUT + 2.0,
        )
    except asyncio.TimeoutError:
        logger.warning(f'DDGS 搜索超时: {topic}')
        search_results = []
    except Exception as e:
        logger.warning(f'DDGS 搜索异常: {e}')
        search_results = []

    search_text = '\n\n'.join(
        f"[来源 {i+1}] {r['title']}\n{r['snippet'][:300]}\n{r['url']}"
        for i, r in enumerate(search_results[:3])
    ) if search_results else '(无搜索结果)'

    prompt = f"""你是一位深度研究分析师。请对以下主题进行深入扩展。

研究主题：{topic}

原始视频转录片段（供参考）：
{transcript_snippet[:8000]}

网页搜索资料：
{search_text[:2000]}

请输出 Markdown：
### {topic}
**研究概述**（3-5 句话）
**深入分析**（800-1500字）
**延伸思考**
**参考来源**
用中文"""

    content, _, _ = await _call_ai(
        model_cfg, prompt, timeout=120.0,
        sio=sio, job_id=job_id, round_label=f'扩展·{topic[:15]}',
    )
    return content if not content.startswith('[处理失败') else f'### {topic}\n\n（暂不可用）'


# ============ 主流程 ============

async def research_process(
    text: str,
    title: str = '',
    model: str = DEFAULT_MODEL,
    sio=None,
    job_id: str = '',
) -> Tuple[str, int, int]:
    """深度研究模式主流程

    搜索策略（与 AI 模型解耦）：
    1. 优先用独立智谱 web_search（config.ZHIPU_API_KEY）
    2. 智谱不可用时，回退 DDGS（限流 8s 超时）
    """
    model_cfg = get_model_by_key(model) or get_model_by_key(DEFAULT_MODEL)
    if not model_cfg:
        raise Exception(f'模型 {model} 不存在')

    # ★ 搜索方式独立判断（不再绑 AI 模型）
    use_zhipu = bool(ZHIPU_API_KEY)

    if sio:
        from socket_handlers import emit_progress, emit_log
        await emit_progress(sio, job_id, 'ai', 0, '🔬 深度研究模式启动...')
        await emit_log(sio, job_id, f'🔬 搜索方式: {"智谱 web_search (独立)" if use_zhipu else "DuckDuckGo (兜底)"}')
        await emit_log(sio, job_id, f'🔬 AI 模型: {model}')

    # Step 1: 提取主题
    topics = await _extract_topics(text, title, model_cfg, sio, job_id)

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

        if use_zhipu:
            section = await _expand_with_zhipu(
                topic, transcript_snippet, sio=sio, job_id=job_id,
            )
            # 智谱失败时回退 DDGS
            if '暂不可用' in section:
                if sio:
                    await emit_log(sio, job_id, f'🔬 智谱搜索失败，回退 DDGS: {topic}')
                section = await _expand_with_ddgs(
                    topic, transcript_snippet, model_cfg, sio=sio, job_id=job_id,
                )
        else:
            section = await _expand_with_ddgs(
                topic, transcript_snippet, model_cfg, sio=sio, job_id=job_id,
            )

        expanded_sections.append(section)

    # Step 3: 合并
    if sio:
        from socket_handlers import emit_progress
        await emit_progress(sio, job_id, 'ai', 92, '🔬 合并研究文章...')

    article_parts = [
        f"# 🔬 深度研究：{title or '视频内容研究'}\n",
        f"> 本文由 AI 基于视频内容深度扩展生成，使用{'智谱联网搜索' if use_zhipu else '网页搜索'}补充资料。\n",
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

    return article_md, 0, 0
