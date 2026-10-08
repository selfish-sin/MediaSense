"""
YouTube Tool V2 - 网页搜索服务（联网检索的**唯一实现**，追问与深度研究共用）

四条后端（按优先级，前三级配 Key 才参与，未配自动跳过）：
1. Ollama Web Search（免费主路径，需要 API Key）
   POST {base}/api/web_search   headers: Authorization: Bearer <key>
   body: {query, max_results(≤10)}   200 : {results: [{title, url, content}]}
   Key 申请：ollama.com/settings/keys
   2026-10-07 实测：免费可用，代理/直连均通，中文结果正文长（最长 ~3700 字符）。
2. Tavily（配 Key 即启用；免费 1000 credits/月，basic=1 credit/次）
   POST {base}/search   headers: Authorization: Bearer tvly-…
   body: {query, search_depth:"basic", max_results(≤20), include_answer:false, include_usage:true}
   200 : {results: [{title, url, content, score}]}；错误码 432/433=额度、401=Key 无效、429=限流
   Key 申请：app.tavily.com
3. Firecrawl（配 Key 即启用；免费 1000 credits/月，country=CN 中文定向）
   POST {base}/v2/search   body: {query, limit(≤20), country:"CN", sources:[{type:"web"}]}
   200 : {data: {web: [{title, url, description}]}}；402=额度、401=Key 无效
   注意：免 Key 本机实测 403，故必须配 Key 才参与。Key 申请：firecrawl.dev
4. DuckDuckGo（ddgs 包，免 Key 殿后兜底）

Key 解析两级兜底：环境变量 → config.py（gitignored，不入库）。
（智谱 Web Search 已于 2026-10-07 整体移除：账号套餐过期欠费，用户决定弃用。）

设计约定：搜索只负责取回原始链接与摘要，**不调用任何 LLM 做总结**；
总结交给用户自己选的回答模型（提示词里带上编号资料）。
失败不静默：web_search_collect 返回的 SearchOutcome.error 承载可行动原因。
**时间预算**：整条链共享 SEARCH_TOTAL_BUDGET（默认 25s），每级只拿剩余时间，
耗尽即停并说明原因——不做"每级各自吃满超时"的串行白等。
"""
import asyncio
import importlib.util
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import List, Dict, NamedTuple, Optional

import httpx

from services.httpx_errors import describe_http_error, describe_http_response

logger = logging.getLogger(__name__)

# 后端 base（可环境变量覆盖）
OLLAMA_SEARCH_BASE = os.getenv("OLLAMA_SEARCH_BASE", "https://ollama.com")
TAVILY_SEARCH_BASE = os.getenv("TAVILY_SEARCH_BASE", "https://api.tavily.com")
FIRECRAWL_SEARCH_BASE = os.getenv("FIRECRAWL_SEARCH_BASE", "https://api.firecrawl.dev")

MAX_RESULTS = 50
DEFAULT_RESULTS = 5
# ollama 单次最多 10 条；tavily/firecrawl 适配层限 20
OLLAMA_MAX_RESULTS = 10
ADAPTER_MAX_RESULTS = 20

# 单次搜索超时（秒）
OLLAMA_TIMEOUT = 20.0
TAVILY_TIMEOUT = 20.0
FIRECRAWL_TIMEOUT = 25.0
# DDGS 实测（2026-09-16）：内部多引擎轮询，单次耗时 2~26s 波动很大，且 duckduckgo 直连
# 经代理会 TLS 握手失败（auto 聚合可用）。故拆成「单请求超时」+「整体预算」两个阈值。
DDGS_REQUEST_TIMEOUT = 10.0
DDGS_TOTAL_TIMEOUT = 25.0

# 整条链的**总预算**（秒）：四级串行 await，若每级都吃满自己的超时，最坏要 ~90s 用户干等。
# 现在四级共享这一个预算——每级只拿到「剩余时间」，剩余不足 SEARCH_MIN_SLICE 就不再启动下一级，
# 被跳过的级会写进 error（哪级没试、为什么），不静默。设 <=0 关闭预算（退回各级独立超时）。
SEARCH_TOTAL_BUDGET = float(os.getenv("SEARCH_TOTAL_BUDGET_SECONDS", "25"))
SEARCH_MIN_SLICE = float(os.getenv("SEARCH_MIN_SLICE_SECONDS", "3"))


class SearchUnavailable(Exception):
    """搜索后端不可用（未配置 Key 等），文案面向用户可行动。"""


class SearchError(Exception):
    """搜索调用失败（HTTP 非 200 / 响应无法解析），消息含服务端真实原因。"""


@dataclass
class SearchOutcome:
    """一次联网检索的结果与诊断信息（失败可见化的载体）。"""

    sources: List[Dict[str, str]] = field(default_factory=list)
    text: str = ""
    provider: str = ""      # "ollama" | "tavily" | "firecrawl" | "ddgs" | ""（均未成功）
    error: str = ""         # 人类可读原因；"" 表示无错误

    @property
    def ok(self) -> bool:
        return bool(self.sources)


# ============ Key 解析（env → config 两级，单点） ============

def _resolve_key(env_name: str, config_attr: str) -> str:
    api_key = os.getenv(env_name, "")
    if api_key:
        return api_key
    try:
        import config as cfg
    except Exception as exc:  # config.py 缺失时不该让搜索整体崩掉
        logger.debug(f"导入 config 失败: {exc}")
        return ""
    return getattr(cfg, config_attr, "") or ""


def resolve_ollama_key() -> str:
    return _resolve_key("OLLAMA_SEARCH_API_KEY", "OLLAMA_SEARCH_API_KEY")


def resolve_tavily_key() -> str:
    return _resolve_key("TAVILY_API_KEY", "TAVILY_API_KEY")


def resolve_firecrawl_key() -> str:
    return _resolve_key("FIRECRAWL_API_KEY", "FIRECRAWL_API_KEY")


def ollama_configured() -> bool:
    return bool(resolve_ollama_key())


def tavily_configured() -> bool:
    return bool(resolve_tavily_key())


def firecrawl_configured() -> bool:
    return bool(resolve_firecrawl_key())


def ddgs_available() -> bool:
    """ddgs 包是否可用（免 Key 兜底后端）"""
    return importlib.util.find_spec("ddgs") is not None


# ============ 统一结构与错误处理 ============

def _short_error(exc: Exception) -> str:
    """单行短错误：httpx 的 HTTPStatusError 透出服务端真实原因"""
    if isinstance(exc, httpx.HTTPStatusError):
        return describe_http_error(exc)
    msg = re.sub(r"\s+", " ", str(exc) or exc.__class__.__name__).strip()
    return msg[:200] or exc.__class__.__name__


def _to_source(raw: Dict, snippet_limit: int = 500, media: str = "") -> Dict[str, str]:
    """各后端结果条目 → 统一 result 结构"""
    snippet = raw.get("content") or raw.get("snippet") or raw.get("description") or ""
    return {
        "title": (raw.get("title") or media or "来源").strip(),
        "url": raw.get("link") or raw.get("url") or raw.get("href") or "",
        "snippet": re.sub(r"\s+", " ", snippet).strip()[:snippet_limit],
        "media": raw.get("media", "") or media,
        "date": raw.get("publish_date") or raw.get("date") or "",
    }


# HTTP 状态 → 可行动提示（错误码语义来自 ting-ear 适配器实测，2026-10）
_STATUS_HINTS = {
    "ollama": {
        401: "Ollama API Key 无效，请到 ollama.com/settings/keys 重新复制",
        403: "Ollama 拒绝访问，请检查 Key 是否有效（ollama.com/settings/keys）",
        402: "Ollama 账户额度不足，请到 ollama.com 检查订阅",
        429: "Ollama 限流，请稍后重试",
    },
    "tavily": {
        401: "Tavily API Key 无效，请到 app.tavily.com 重新复制",
        403: "Tavily API Key 无效，请到 app.tavily.com 重新复制",
        432: "Tavily 免费额度已用完（每月 1 日重置），可到 app.tavily.com 升级",
        433: "Tavily 按量付费上限已用完，请检查控制台账单设置",
        429: "Tavily 限流，请稍后重试",
    },
    "firecrawl": {
        401: "Firecrawl API Key 无效，请到 firecrawl.dev 检查",
        403: "Firecrawl 拒绝访问，请检查 Key 是否有效（firecrawl.dev）",
        402: "Firecrawl 额度已用完（免费档 1000 credits/月），请到 firecrawl.dev 查看",
        429: "Firecrawl 限流（免费档 10 次/分），请稍后重试",
    },
}


def _raise_provider_error(provider: str, resp: httpx.Response) -> None:
    """非 200 统一抛 SearchError：服务端真实原因 + 可行动提示。"""
    reason = describe_http_response(resp)
    hint = _STATUS_HINTS.get(provider, {}).get(resp.status_code, "")
    raise SearchError(f"{reason}；{hint}" if hint else reason)


# ============ Ollama Web Search（免费主路径） ============

async def ollama_search(
    query: str,
    max_results: int = DEFAULT_RESULTS,
    snippet_limit: int = 500,
    timeout: float = OLLAMA_TIMEOUT,
) -> List[Dict[str, str]]:
    """Ollama Web Search API。

    Raises:
        SearchUnavailable: 未配置 Key
        SearchError: 调用或解析失败（由调用方决定是否降级）
    """
    api_key = resolve_ollama_key()
    if not api_key:
        raise SearchUnavailable(
            "未配置 Ollama 搜索 Key（可设环境变量 OLLAMA_SEARCH_API_KEY 或写入 config.py，"
            "申请入口：ollama.com/settings/keys）"
        )

    count = max(1, min(int(max_results), OLLAMA_MAX_RESULTS))
    base = OLLAMA_SEARCH_BASE.rstrip("/")
    async with httpx.AsyncClient(timeout=timeout, proxy=resolve_search_proxy() or None) as client:
        resp = await client.post(
            f"{base}/api/web_search",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"query": (query or "").strip(), "max_results": count},
        )
        if resp.status_code != 200:
            _raise_provider_error("ollama", resp)
        data = resp.json()

    raw = data.get("results") or []
    results = []
    for r in raw:
        if isinstance(r, dict):
            item = _to_source(r, snippet_limit=snippet_limit, media="Ollama")
            if item["url"] or item["title"]:
                results.append(item)
    logger.info(
        f"Ollama 搜索完成: query={(query or '')[:40]!r}, results={len(results)}"
    )
    return results


# ============ Tavily（配 Key 即启用） ============

async def tavily_search(
    query: str,
    max_results: int = DEFAULT_RESULTS,
    snippet_limit: int = 500,
    timeout: float = TAVILY_TIMEOUT,
) -> List[Dict[str, str]]:
    """Tavily Search API（固定 basic 档：1 credit/次，质量提升有限的高级档不开放）。

    Raises:
        SearchUnavailable: 未配置 Key
        SearchError: 调用或解析失败
    """
    api_key = resolve_tavily_key()
    if not api_key:
        raise SearchUnavailable(
            "未配置 Tavily Key（可设环境变量 TAVILY_API_KEY 或写入 config.py，"
            "申请入口：app.tavily.com）"
        )

    count = max(1, min(int(max_results), ADAPTER_MAX_RESULTS))
    base = TAVILY_SEARCH_BASE.rstrip("/")
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{base}/search",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "query": (query or "").strip(),
                # 固定 basic：1 credit。advanced（2 credits）质量提升有限，不开放
                "search_depth": "basic",
                "max_results": count,
                # 只要结构化结果，不要 LLM 总结（综合交给用户自选的回答模型）
                "include_answer": False,
                "include_usage": True,
            },
        )
        if resp.status_code != 200:
            _raise_provider_error("tavily", resp)
        data = resp.json()

    raw = data.get("results") or []
    results = []
    for r in raw:
        if isinstance(r, dict):
            item = _to_source(r, snippet_limit=snippet_limit, media="Tavily")
            if item["url"] or item["title"]:
                results.append(item)
    logger.info(f"Tavily 搜索完成: query={(query or '')[:40]!r}, results={len(results)}")
    return results


# ============ Firecrawl（配 Key 即启用，中文定向） ============

async def firecrawl_search(
    query: str,
    max_results: int = DEFAULT_RESULTS,
    snippet_limit: int = 500,
    timeout: float = FIRECRAWL_TIMEOUT,
) -> List[Dict[str, str]]:
    """Firecrawl Search API（country=CN 地区定向，中文命中更好）。

    Raises:
        SearchUnavailable: 未配置 Key
        SearchError: 调用或解析失败
    """
    api_key = resolve_firecrawl_key()
    if not api_key:
        raise SearchUnavailable(
            "未配置 Firecrawl Key（可设环境变量 FIRECRAWL_API_KEY 或写入 config.py，"
            "申请入口：firecrawl.dev）"
        )

    count = max(1, min(int(max_results), ADAPTER_MAX_RESULTS))
    base = FIRECRAWL_SEARCH_BASE.rstrip("/")
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{base}/v2/search",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "query": (query or "").strip(),
                "limit": count,
                "country": "CN",
                "sources": [{"type": "web"}],
            },
        )
        if resp.status_code != 200:
            _raise_provider_error("firecrawl", resp)
        data = resp.json()

    raw = (data.get("data") or {}).get("web") or []
    results = []
    for r in raw:
        if isinstance(r, dict):
            item = _to_source(r, snippet_limit=snippet_limit, media="Firecrawl")
            if item["url"] or item["title"]:
                results.append(item)
    logger.info(f"Firecrawl 搜索完成: query={(query or '')[:40]!r}, results={len(results)}")
    return results


# ============ DuckDuckGo 兜底（ddgs，免 Key） ============

def resolve_search_proxy() -> str:
    """DDGS 走的代理。

    坑点（2026-09-16 实测）：`ddgs`/`primp` **不会**自动读取 HTTP_PROXY/HTTPS_PROXY
    环境变量，不显式传 `proxy=` 时在国内网络直接超时（表现为「联网开关没用」）。
    优先级：config.PROXY（项目显式配置）→ HTTPS_PROXY/HTTP_PROXY（系统级代理）。
    ollama 走 httpx 原生客户端（会读环境变量），但显式传入同一来源也无害。
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

def fmt_sources(sources: List[Dict[str, str]]) -> str:
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


class _HttpProvider(NamedTuple):
    """HTTP 型搜索后端声明（加一级 = 在这张表里加一行）。

    函数以**模块内名字**声明、调用时再解析：导入时捕获函数对象会让测试打桩
    （monkeypatch.setattr(web_search, "tavily_search", fake)）与运行时替换失效。
    """

    name: str          # 展示名；SearchOutcome.provider 取其小写
    configured_name: str
    search_name: str
    timeout: float
    fail_label: str    # 失败文案前缀，如 "Ollama 联网搜索失败"
    zero_label: str    # 0 条结果文案
    missing_note: str = ""  # 未配置 Key 时的可行动提示（空串 = 跳过且不写 error，避免噪音）


_HTTP_PROVIDERS = (
    _HttpProvider(
        "Ollama", "ollama_configured", "ollama_search", OLLAMA_TIMEOUT,
        "Ollama 联网搜索失败", "Ollama 联网搜索返回 0 条结果",
        "未配置 Ollama 搜索 Key（可设环境变量 OLLAMA_SEARCH_API_KEY 或写入 config.py，"
        "申请入口：ollama.com/settings/keys）",
    ),
    _HttpProvider(
        "Tavily", "tavily_configured", "tavily_search", TAVILY_TIMEOUT,
        "Tavily 搜索失败", "Tavily 返回 0 条结果",
    ),
    _HttpProvider(
        "Firecrawl", "firecrawl_configured", "firecrawl_search", FIRECRAWL_TIMEOUT,
        "Firecrawl 搜索失败", "Firecrawl 返回 0 条结果",
    ),
)


async def web_search_collect(
    query: str,
    max_results: int = DEFAULT_RESULTS,
    snippet_limit: int = 500,
    total_budget: float = SEARCH_TOTAL_BUDGET,
) -> SearchOutcome:
    """联网检索（追问 / 深度研究共用）：Ollama → Tavily → Firecrawl → DDGS。

    前三级未配 Key 自动跳过（跳过原因记入 error 汇总）；任何一级成功即返回。
    **整条链共享 `total_budget` 秒**（<=0 关闭）：每级只拿到「剩余时间」作超时，
    剩余不足 `SEARCH_MIN_SLICE` 即停止后续兜底并把原因写进 error——不做「每级各自
    吃满超时」的串行白等（那是修复前最坏 ~90s 的来源）。
    无论成功失败都返回 SearchOutcome；失败原因写在 `error`（绝不静默返回空）。
    """
    query = (query or "").strip()
    if not query:
        return SearchOutcome(error="搜索关键词为空")

    deadline = time.monotonic() + total_budget if total_budget and total_budget > 0 else None
    errors: List[str] = []

    def _budget_block(name: str) -> bool:
        """剩余预算不足以再启动一级 → 记原因并返回 True（调用方跳过该级）"""
        if deadline is None:
            return False
        remaining = deadline - time.monotonic()
        if remaining >= SEARCH_MIN_SLICE:
            return False
        errors.append(
            f"{name}：未尝试（联网检索总预算 {total_budget:.0f}s 已用尽，剩余 {max(remaining, 0):.1f}s）"
        )
        return True

    def _budget_slice(default_timeout: float) -> "tuple[float, bool]":
        """本级可用超时 = min(默认超时, 剩余预算)；第二个返回值表示是否被预算压过"""
        if deadline is None:
            return default_timeout, False
        remaining = max(deadline - time.monotonic(), 0.0)
        if remaining < default_timeout:
            return remaining, True
        return default_timeout, False

    # ---- 1~3) Ollama → Tavily → Firecrawl ----
    for provider in _HTTP_PROVIDERS:
        configured = globals()[provider.configured_name]
        if not configured():
            if provider.missing_note:
                errors.append(provider.missing_note)
            continue
        if _budget_block(provider.name):
            continue
        timeout, sliced = _budget_slice(provider.timeout)
        search = globals()[provider.search_name]
        try:
            results = await search(
                query, max_results=max_results, snippet_limit=snippet_limit, timeout=timeout,
            )
        except SearchUnavailable as exc:
            errors.append(str(exc))
        except Exception as exc:
            msg = _short_error(exc)
            errors.append(f"{provider.fail_label}（{msg}）" + ("；受联网检索总预算限制" if sliced else ""))
            logger.warning(f"[web_search] {provider.name} 失败: {msg}")
        else:
            if results:
                logger.info(f"[web_search] {provider.name} 返回 {len(results)} 条来源")
                return SearchOutcome(
                    sources=results, text=fmt_sources(results), provider=provider.name.lower(),
                )
            errors.append(provider.zero_label)

    # ---- 4) DDGS 兜底 ----
    if ddgs_available():
        if not _budget_block("DuckDuckGo 兜底"):
            timeout, sliced = _budget_slice(DDGS_TOTAL_TIMEOUT)
            try:
                # ddgs_search 内部自带 DDGS_TOTAL_TIMEOUT；这里再套一层是为了让**总预算**说了算
                results = await asyncio.wait_for(
                    ddgs_search(query, max_results=max_results), timeout=timeout,
                )
            except asyncio.TimeoutError:
                errors.append(
                    f"DuckDuckGo 兜底超时（{timeout:.0f}s"
                    + ("，受联网检索总预算限制" if sliced else "")
                    + "）；若本机需代理，请在 config.py 设 PROXY 或设置 HTTPS_PROXY 环境变量"
                )
            except Exception as exc:
                msg = _short_error(exc)
                errors.append(f"DuckDuckGo 兜底失败（{msg}）")
                logger.warning(f"[web_search] DDGS 兜底失败: {msg}")
            else:
                if results:
                    logger.info(f"[web_search] DDGS 兜底返回 {len(results)} 条来源")
                    return SearchOutcome(sources=results, text=fmt_sources(results), provider="ddgs")
                errors.append("DuckDuckGo 返回 0 条结果")
    else:
        errors.append("DuckDuckGo 兜底不可用（未安装 ddgs 包）")

    error = "；".join(errors)
    logger.warning(f"[web_search] 联网检索失败: query={query!r}, 原因={error}")
    return SearchOutcome(error=error)


async def search_web(
    query: str,
    max_results: int = DEFAULT_RESULTS,
) -> List[Dict[str, str]]:
    """网页搜索（Ollama → Tavily → Firecrawl → DDGS）。

    返回 [{title,url,snippet,media,date}, ...]；失败返回空列表（原因见日志，
    需要可行动原因的调用方请直接用 web_search_collect）。
    """
    outcome = await web_search_collect(query, max_results=max_results)
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
    """当前可用的搜索后端（供健康检查/排障读取；按链路优先级排列）"""
    return {
        "ollama": "configured" if ollama_configured() else None,
        "tavily": "configured" if tavily_configured() else None,
        "firecrawl": "configured" if firecrawl_configured() else None,
        "ddgs": "available" if ddgs_available() else None,
        "proxy": resolve_search_proxy() or None,
    }
