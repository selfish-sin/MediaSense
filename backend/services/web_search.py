"""
YouTube Tool V2 - 网页搜索服务（联网检索的**唯一实现**，追问与深度研究共用）

两条后端（按优先级）：
1. 智谱 Web Search API（独立端点，主路径，需要 API Key）
   POST https://open.bigmodel.cn/api/paas/v4/web_search
   body: {search_query(≤70), search_engine, search_intent, count, content_size}
   200 : {search_result: [{title, content, link, media, icon, refer, publish_date}]}
   文档: https://docs.bigmodel.cn/api-reference/工具-api/网络搜索
   注意：**不是** chat/completions + tools=[web_search]（旧姿势已被官方独立端点取代，
   旧姿势还会因 `message.web_search` 是数组而被误当 dict 解析，见 2026-09-16 修复）。
2. DuckDuckGo（ddgs 包，免 Key 兜底）

Key 解析三级兜底：环境变量 ZHIPU_API_KEY → config.py → 数据库模型（endpoint 含 bigmodel/zhipu）。

设计约定：搜索只负责取回原始链接与摘要，**不调用任何 LLM 做总结**；
总结交给用户自己选的回答模型（提示词里带上编号资料）。
失败不静默：web_search_collect 返回的 SearchOutcome.error 承载可行动原因。
"""
import asyncio
import importlib.util
import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional

import httpx

from services.httpx_errors import describe_http_error, describe_http_response

logger = logging.getLogger(__name__)

# 智谱独立 Web Search 端点
ZHIPU_SEARCH_ENDPOINT = os.getenv(
    "ZHIPU_SEARCH_ENDPOINT", "https://open.bigmodel.cn/api/paas/v4/web_search"
)
# search_query 官方上限 70 字符
SEARCH_QUERY_MAX = 70
# count 官方范围 1-50
MAX_RESULTS = 50
DEFAULT_RESULTS = 5

# 单次搜索超时（秒）
ZHIPU_TIMEOUT = 30.0
# DDGS 实测（2026-09-16）：内部多引擎轮询，单次耗时 2~26s 波动很大，且 duckduckgo 直连
# 经代理会 TLS 握手失败（auto 聚合可用）。故拆成「单请求超时」+「整体预算」两个阈值。
DDGS_REQUEST_TIMEOUT = 10.0
DDGS_TOTAL_TIMEOUT = 25.0

# 引擎映射：前端/调用方 key → 智谱 search_engine 编码
ENGINE_MAP = {
    "std": "search_std",          # 基础版（0.01元/次）
    "pro": "search_pro",          # 高级版（0.03元/次）
    "sogou": "search_pro_sogou",  # 搜狗（0.05元/次）
    "quark": "search_pro_quark",  # 夸克（0.05元/次）
}


class SearchUnavailable(Exception):
    """搜索后端不可用（未配置 Key 等），文案面向用户可行动。"""


class SearchError(Exception):
    """搜索调用失败（HTTP 非 200 / 响应无法解析），消息含服务端真实原因。"""


@dataclass
class SearchOutcome:
    """一次联网检索的结果与诊断信息（失败可见化的载体）。"""

    sources: List[Dict[str, str]] = field(default_factory=list)
    text: str = ""
    provider: str = ""      # "zhipu" | "ddgs" | ""（均未成功）
    error: str = ""         # 人类可读原因；"" 表示无错误

    @property
    def ok(self) -> bool:
        return bool(self.sources)


# ============ Key 解析（三级兜底，单点） ============

def resolve_zhipu_key() -> str:
    """环境变量 → config.py → 数据库模型配置（endpoint 含 bigmodel/zhipu）"""
    api_key = os.getenv("ZHIPU_API_KEY", "")
    if api_key:
        return api_key
    try:
        from config import ZHIPU_API_KEY as _cfg_key
        if _cfg_key:
            return _cfg_key
    except Exception as exc:  # config.py 缺失/字段缺失都不该让搜索整体崩掉
        logger.debug(f"读取 config.ZHIPU_API_KEY 失败: {exc}")
    try:
        from db.model_repo import get_all_models
        for m in get_all_models() or []:
            key = m.get("api_key", "")
            endpoint = (m.get("endpoint") or "").lower()
            if key and ("bigmodel" in endpoint or "zhipu" in endpoint):
                return key
    except Exception as exc:
        logger.debug(f"从数据库模型配置查找智谱 Key 失败: {exc}")
    return ""


def zhipu_configured() -> bool:
    return bool(resolve_zhipu_key())


def ddgs_available() -> bool:
    """ddgs 包是否可用（免 Key 兜底后端）"""
    return importlib.util.find_spec("ddgs") is not None


# ============ 智谱独立 Web Search API ============

def _short_error(exc: Exception) -> str:
    """单行短错误：httpx 的 HTTPStatusError 透出服务端真实原因"""
    if isinstance(exc, httpx.HTTPStatusError):
        return describe_http_error(exc)
    msg = re.sub(r"\s+", " ", str(exc) or exc.__class__.__name__).strip()
    return msg[:200] or exc.__class__.__name__


def _to_source(raw: Dict) -> Dict[str, str]:
    """智谱 search_result 条目 → 统一 result 结构"""
    snippet = raw.get("content") or raw.get("snippet") or ""
    return {
        "title": (raw.get("title") or raw.get("media") or "来源").strip(),
        "url": raw.get("link") or raw.get("url") or "",
        "snippet": re.sub(r"\s+", " ", snippet).strip()[:500],
        "media": raw.get("media", ""),
        "date": raw.get("publish_date") or raw.get("date") or "",
    }


async def zhipu_search(
    query: str,
    max_results: int = DEFAULT_RESULTS,
    engine: str = "pro",
    content_size: str = "medium",
    timeout: float = ZHIPU_TIMEOUT,
) -> List[Dict[str, str]]:
    """智谱独立 Web Search API。

    Raises:
        SearchUnavailable: 未配置 Key
        httpx.HTTPStatusError / ValueError: 调用或解析失败（由调用方决定是否降级）
    """
    api_key = resolve_zhipu_key()
    if not api_key:
        raise SearchUnavailable(
            "未配置智谱 API Key（可设环境变量 ZHIPU_API_KEY、写入 config.py，"
            "或把智谱模型加进模型管理页）"
        )

    count = max(1, min(int(max_results), MAX_RESULTS))
    payload = {
        "search_query": (query or "").strip()[:SEARCH_QUERY_MAX],
        "search_engine": ENGINE_MAP.get(engine, "search_pro"),
        "search_intent": True,
        "count": count,
        "content_size": content_size if content_size in ("medium", "high") else "medium",
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            ZHIPU_SEARCH_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if resp.status_code != 200:
            # 非 200 一律抛出（含响应体真实原因），由调用方降级到 DDGS 或如实上报
            raise SearchError(describe_http_response(resp))
        data = resp.json()

    raw = data.get("search_result") or []
    results = [_to_source(r) for r in raw if isinstance(r, dict)]
    logger.info(f"智谱搜索完成: query={payload['search_query']!r}, engine={payload['search_engine']}, results={len(results)}")
    return results


# ============ DuckDuckGo 兜底（ddgs，免 Key） ============

def resolve_search_proxy() -> str:
    """DDGS 走的代理。

    坑点（2026-09-16 实测）：`ddgs`/`primp` **不会**自动读取 HTTP_PROXY/HTTPS_PROXY
    环境变量，不显式传 `proxy=` 时在国内网络直接超时（表现为「联网开关没用」）。
    优先级：config.PROXY（项目显式配置）→ HTTPS_PROXY/HTTP_PROXY（系统级代理）。
    """
    try:
        from config import PROXY
        if PROXY:
            return PROXY
    except Exception as exc:
        logger.debug(f"读取 config.PROXY 失败: {exc}")
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.getenv(name)
        if value:
            return value
    return ""


def _ddgs_search_sync(query: str, max_results: int, proxy: str = "") -> List[Dict[str, str]]:
    from ddgs import DDGS

    results: List[Dict[str, str]] = []
    with DDGS(timeout=DDGS_REQUEST_TIMEOUT, proxy=proxy or None) as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title": (r.get("title") or "来源").strip(),
                "url": r.get("href", ""),
                "snippet": re.sub(r"\s+", " ", r.get("body") or "").strip()[:500],
                "media": "DuckDuckGo",
                "date": "",
            })
    return results


async def ddgs_search(query: str, max_results: int = DEFAULT_RESULTS) -> List[Dict[str, str]]:
    """DuckDuckGo 搜索（在线程中执行并加总超时，DDGS 内部各引擎默认超时过长）"""
    proxy = resolve_search_proxy()
    return await asyncio.wait_for(
        asyncio.to_thread(_ddgs_search_sync, query, max_results, proxy),
        timeout=DDGS_TOTAL_TIMEOUT,
    )


# ============ 统一入口 ============

def _fmt_sources(sources: List[Dict[str, str]]) -> str:
    """把 sources 渲染成编号文本，便于注入回答模型上下文"""
    lines = []
    for i, s in enumerate(sources, 1):
        line = f"[{i}] {s.get('title', '来源')}"
        if s.get("snippet"):
            line += f"\n    {s['snippet']}"
        if s.get("url"):
            line += f"\n    链接: {s['url']}"
        if s.get("date"):
            line += f"\n    时间: {s['date']}"
        lines.append(line)
    return "\n\n".join(lines)


async def web_search_collect(
    query: str,
    max_results: int = DEFAULT_RESULTS,
    engine: str = "pro",
    content_size: str = "medium",
) -> SearchOutcome:
    """联网检索（追问 / 深度研究共用）：智谱独立 API 优先，DDGS 兜底。

    无论成功失败都返回 SearchOutcome；失败原因写在 `error`（绝不静默返回空）。
    """
    query = (query or "").strip()
    if not query:
        return SearchOutcome(error="搜索关键词为空")

    errors: List[str] = []

    # ---- 1) 智谱独立 Web Search（主路径）----
    if zhipu_configured():
        try:
            results = await zhipu_search(
                query, max_results=max_results, engine=engine, content_size=content_size
            )
            if results:
                text = _fmt_sources(results)
                return SearchOutcome(sources=results, text=text, provider="zhipu")
            errors.append("智谱联网搜索返回 0 条结果")
        except SearchUnavailable as exc:
            errors.append(str(exc))
        except Exception as exc:
            msg = _short_error(exc)
            errors.append(f"智谱联网搜索失败（{msg}）")
            logger.warning(f"[web_search] 智谱失败: {msg}")
    else:
        errors.append(
            "未配置智谱 API Key（可设环境变量 ZHIPU_API_KEY、写入 config.py，"
            "或把智谱模型加进模型管理页）"
        )

    # ---- 2) DDGS 兜底 ----
    if ddgs_available():
        try:
            results = await ddgs_search(query, max_results=max_results)
            if results:
                text = _fmt_sources(results)
                logger.info(f"[web_search] DDGS 兜底返回 {len(results)} 条来源")
                return SearchOutcome(sources=results, text=text, provider="ddgs")
            errors.append("DuckDuckGo 返回 0 条结果")
        except asyncio.TimeoutError:
            errors.append(
                f"DuckDuckGo 兜底超时（{DDGS_TOTAL_TIMEOUT:.0f}s）；若本机需代理，"
                "请在 config.py 设 PROXY 或设置 HTTPS_PROXY 环境变量"
            )
        except Exception as exc:
            msg = _short_error(exc)
            errors.append(f"DuckDuckGo 兜底失败（{msg}）")
            logger.warning(f"[web_search] DDGS 兜底失败: {msg}")
    else:
        errors.append("DuckDuckGo 兜底不可用（未安装 ddgs 包）")

    error = "；".join(errors)
    logger.warning(f"[web_search] 联网检索失败: query={query!r}, 原因={error}")
    return SearchOutcome(error=error)


async def search_web(
    query: str,
    max_results: int = DEFAULT_RESULTS,
    engine: str = "pro",
) -> List[Dict[str, str]]:
    """网页搜索（智谱优先 → DDGS 兜底）。

    返回 [{title,url,snippet,media,date}, ...]；失败返回空列表（原因见日志，
    需要可行动原因的调用方请直接用 web_search_collect）。
    """
    outcome = await web_search_collect(query, max_results=max_results, engine=engine)
    return outcome.sources


def format_search_context(results: List[Dict[str, str]]) -> str:
    """将搜索结果格式化为 AI 可用的上下文文本"""
    if not results:
        return "（未搜索到相关结果）"

    lines = ["# 网络搜索结果", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"## {i}. {r.get('title', '来源')}")
        if r.get("url"):
            lines.append(f"URL: {r['url']}")
        lines.append(f"摘要: {r.get('snippet', '')}")
        if r.get("date"):
            lines.append(f"日期: {r['date']}")
        lines.append("")
    return "\n".join(lines)


def describe_search_backends() -> Dict[str, Optional[str]]:
    """当前可用的搜索后端（供健康检查/排障读取）"""
    return {
        "zhipu": "configured" if zhipu_configured() else None,
        "ddgs": "available" if ddgs_available() else None,
        "proxy": resolve_search_proxy() or None,
    }
