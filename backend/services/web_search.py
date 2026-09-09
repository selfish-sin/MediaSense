"""
YouTube Tool V2 - 网页搜索服务
智谱 Web Search（通过 chat/completions + tools=web_search 实现）

调用方式：POST /api/paas/v4/chat/completions，tools=[{type:web_search, web_search:{enable,search_result}}]
模型基于搜索结果回答，message.web_search 返回原始链接列表。
"""
import os
import logging
from typing import List, Dict, Optional

from services.httpx_errors import describe_http_error

logger = logging.getLogger(__name__)

# 结果上限
MAX_RESULTS = 10
DEFAULT_RESULTS = 5

# 引擎映射：前端 key → 智谱 search_engine 编码
ENGINE_MAP = {
    "std": "search_std",         # 基础版（0.01元/次）
    "pro": "search_pro",         # 高级版（0.03元/次）
    "sogou": "search_pro_sogou", # 搜狗（0.05元/次）
    "quark": "search_pro_quark", # 夸克（0.05元/次）
}


def _resolve_api_key() -> str:
    """三级兜底拿 ZHIPU_API_KEY：环境变量 → config.py → 数据库模型配置"""
    api_key = os.getenv("ZHIPU_API_KEY", "")
    if api_key:
        return api_key
    try:
        from config import ZHIPU_API_KEY as _cfg_key
        api_key = _cfg_key or ""
    except Exception:
        pass
    if api_key:
        return api_key
    try:
        from db.model_repo import get_all_models
        models = get_all_models() or []
        for m in models:
            key = m.get("api_key", "")
            if key and "bigmodel" in m.get("endpoint", ""):
                return key
    except Exception:
        pass
    return ""


async def search_web(
    query: str,
    max_results: int = DEFAULT_RESULTS,
    engine: str = "pro",
) -> List[Dict[str, str]]:
    """
    网页搜索（智谱 Web Search，走 chat/completions + tools）。

    参数:
        query: 搜索关键词
        max_results: 最大结果数（1-50）
        engine: 搜索引擎 ("std" | "pro" | "sogou" | "quark")

    返回:
        [{"title": "...", "url": "...", "snippet": "...", "media": "", "date": ""}, ...]
        若模型未返回 web_search 数组，则用 content 作为单条综合结果。
    """
    api_key = _resolve_api_key()
    if not api_key:
        logger.warning("未找到 ZHIPU_API_KEY（环境变量/config.py/数据库均无），搜索功能不可用")
        return []

    search_engine = ENGINE_MAP.get(engine, "search_pro")
    max_results = min(max(max_results, 1), 50)

    try:
        from zai import ZhipuAiClient
        client = ZhipuAiClient(api_key=api_key)
        import asyncio
        loop = asyncio.get_running_loop()

        def _search():
            return client.chat.completions.create(
                model="glm-4.5-air",
                messages=[{"role": "user", "content": f"请搜索并总结：{query}"}],
                tools=[{"type": "web_search", "web_search": {
                    "enable": True,
                    "search_result": True,
                    "search_engine": search_engine,
                }}],
                stream=False,
            )
        resp = await loop.run_in_executor(None, _search)
        return _parse_chat_response(resp, max_results, query)

    except ImportError:
        # zai-sdk 未装 → HTTP 直连
        return await _search_http(query, max_results, search_engine, api_key)
    except Exception as e:
        logger.error(f"智谱 SDK 搜索失败: {e}，回退 HTTP")
        return await _search_http(query, max_results, search_engine, api_key)


def _parse_chat_response(resp, max_results: int, query: str) -> List[Dict[str, str]]:
    """解析 chat/completions 响应，提取 web_search 结果或回退到 content"""
    results: List[Dict[str, str]] = []

    # SDK 响应是对象，HTTP 是 dict，统一处理
    if hasattr(resp, 'choices'):
        choices = resp.choices
    else:
        choices = resp.get('choices', [])

    if not choices:
        return results

    msg = choices[0].message if hasattr(choices[0], 'message') else choices[0].get('message', {})
    content = getattr(msg, 'content', None) or (msg.get('content', '') if isinstance(msg, dict) else '')
    web_search = getattr(msg, 'web_search', None)
    if web_search is None and isinstance(msg, dict):
        web_search = msg.get('web_search')

    # 优先用 web_search 数组（原始链接）
    if isinstance(web_search, list) and web_search:
        for r in web_search[:max_results]:
            if not isinstance(r, dict):
                continue
            results.append({
                "title": r.get("title", ""),
                "url": r.get("link", r.get("url", "")),
                "snippet": r.get("content", ""),
                "media": r.get("media", ""),
                "date": r.get("date", r.get("publish_date", "")),
            })

    # web_search 数组为空 → 用 content 作为综合答案（标注来源为智谱AI整合）
    if not results and content:
        results.append({
            "title": f"智谱AI搜索结果：{query}",
            "url": "",
            "snippet": content,
            "media": "智谱AI（基于实时网页搜索整合）",
            "date": "",
        })

    logger.info(f"智谱搜索完成: query={query}, results={len(results)}")
    return results


async def _search_http(query: str, max_results: int, search_engine: str, api_key: str) -> List[Dict[str, str]]:
    """HTTP 直连智谱 chat/completions + web_search tool"""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "glm-4.5-air",
                    "messages": [{"role": "user", "content": f"请搜索并总结：{query}"}],
                    "tools": [{
                        "type": "web_search",
                        "web_search": {
                            "enable": True,
                            "search_result": True,
                            "search_engine": search_engine,
                        }
                    }],
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        results: List[Dict[str, str]] = []
        choices = data.get('choices', [])
        if not choices:
            return results

        msg = choices[0].get('message', {})
        content = msg.get('content', '')
        web_search = msg.get('web_search') or []

        if isinstance(web_search, list) and web_search:
            for r in web_search[:max_results]:
                if not isinstance(r, dict):
                    continue
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("link", r.get("url", "")),
                    "snippet": r.get("content", ""),
                    "media": r.get("media", ""),
                    "date": r.get("date", r.get("publish_date", "")),
                })

        if not results and content:
            results.append({
                "title": f"智谱AI搜索结果：{query}",
                "url": "",
                "snippet": content,
                "media": "智谱AI（基于实时网页搜索整合）",
                "date": "",
            })

        logger.info(f"智谱 HTTP 搜索完成: query={query}, results={len(results)}")
        return results

    except httpx.HTTPStatusError as e:
        logger.error(f"智谱 HTTP 搜索失败: {describe_http_error(e)}")
        return []
    except Exception as e:
        logger.error(f"智谱 HTTP 搜索失败: {e}")
        return []


def format_search_context(results: List[Dict[str, str]]) -> str:
    """将搜索结果格式化为 AI 可用的上下文文本"""
    if not results:
        return "（未搜索到相关结果）"

    lines = ["# 网络搜索结果", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"## {i}. {r['title']}")
        if r['url']:
            lines.append(f"URL: {r['url']}")
        lines.append(f"摘要: {r['snippet']}")
        if r.get('date'):
            lines.append(f"日期: {r['date']}")
        lines.append("")
    return "\n".join(lines)
