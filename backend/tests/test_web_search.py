"""
联网检索链路测试（services/web_search.py 单一实现）

背景（2026-09-16 首修，2026-10-07 换引擎）：追问面板的「联网」开关曾是死按钮——
主路径用未配置的 Key、旧代码走已被官方独立端点取代的 chat+tools 姿势、ddgs 包未安装，
三层全部失效却静默返回空列表。2026-10-07 起引擎链换为：
  Ollama（免费主路径）→ Tavily → Firecrawl（后两级配 Key 才参与）→ DDGS（免 Key 兜底）
智谱 Web Search 整体移除（账号套餐过期欠费，用户决定弃用）。
本文件锁定契约：
  - 各后端的请求姿势与结果解析
  - Key 两级兜底（env → config）与「配 Key 才参与」的链路顺序
  - 全部失败时**必须带可行动原因**（不静默）
不触网：httpx.AsyncClient / ddgs 全部替换为替身。
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import web_search


# ── 替身 ──

def _fake_client(status=200, payload=None, text=None):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text if text is not None else json.dumps(payload or {}, ensure_ascii=False)
    resp.json = lambda: payload or {}
    client = AsyncMock()
    client.post = AsyncMock(return_value=resp)
    # ★ async with 必须返回同一个 client，否则 post 返回裸协程（同 test_routers 的教训）
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    return client


OLLAMA_OK = {
    "results": [
        {"title": "标题A", "content": "正文A", "url": "https://a.com"},
        {"title": "标题B", "content": "正文B", "url": "https://b.com"},
    ],
}
TAVILY_OK = {
    "results": [
        {"title": "标题A", "content": "正文A", "url": "https://a.com", "score": 0.9},
    ],
}
FIRECRAWL_OK = {
    "success": True,
    "data": {"web": [{"title": "标题A", "description": "正文A", "url": "https://a.com"}]},
}


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch):
    """屏蔽开发机上的搜索 Key 环境变量与 config 属性，避免用例受本地配置影响"""
    for name in ("OLLAMA_SEARCH_API_KEY", "TAVILY_API_KEY", "FIRECRAWL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("config.OLLAMA_SEARCH_API_KEY", "", raising=False)
    monkeypatch.setattr("config.TAVILY_API_KEY", "", raising=False)
    monkeypatch.setattr("config.FIRECRAWL_API_KEY", "", raising=False)


@pytest.fixture
def ddgs_off(monkeypatch):
    """默认关掉 DDGS，用例按需单独打开（否则真机装了 ddgs 会触发真实网络请求）"""
    monkeypatch.setattr(web_search, "ddgs_available", lambda: False)


# ── Key 两级兜底（env → config） ──

class TestResolveKeys:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_SEARCH_API_KEY", "sk-env")
        assert web_search.resolve_ollama_key() == "sk-env"
        assert web_search.ollama_configured() is True

    def test_falls_back_to_config(self, monkeypatch):
        monkeypatch.setattr("config.OLLAMA_SEARCH_API_KEY", "sk-cfg", raising=False)
        monkeypatch.delenv("OLLAMA_SEARCH_API_KEY", raising=False)
        assert web_search.resolve_ollama_key() == "sk-cfg"

    def test_nothing_configured(self):
        assert web_search.resolve_ollama_key() == ""
        assert web_search.ollama_configured() is False
        assert web_search.tavily_configured() is False
        assert web_search.firecrawl_configured() is False


# ── Ollama Web Search（免费主路径） ──

class TestOllamaSearch:
    @pytest.mark.asyncio
    async def test_parses_results_and_request_shape(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_SEARCH_API_KEY", "sk-x")
        fake = _fake_client(200, OLLAMA_OK)
        monkeypatch.setattr(web_search.httpx, "AsyncClient", lambda **kw: fake)

        results = await web_search.ollama_search("测试问题", max_results=5)

        assert [r["title"] for r in results] == ["标题A", "标题B"]
        assert results[0]["url"] == "https://a.com"
        assert results[0]["snippet"] == "正文A"          # content → snippet
        assert results[0]["media"] == "Ollama"
        # 请求姿势：POST {base}/api/web_search + Bearer 头 + query/max_results
        assert fake.post.call_args.args[0] == "https://ollama.com/api/web_search"
        assert fake.post.call_args.kwargs["json"]["query"] == "测试问题"
        assert fake.post.call_args.kwargs["json"]["max_results"] == 5
        headers = fake.post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer sk-x"

    @pytest.mark.asyncio
    async def test_max_results_capped_to_10(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_SEARCH_API_KEY", "sk-x")
        fake = _fake_client(200, {"results": []})
        monkeypatch.setattr(web_search.httpx, "AsyncClient", lambda **kw: fake)

        await web_search.ollama_search("q", max_results=50)

        assert fake.post.call_args.kwargs["json"]["max_results"] == web_search.OLLAMA_MAX_RESULTS

    @pytest.mark.asyncio
    async def test_snippet_limit_respected(self, monkeypatch):
        """广泛模式需要更长正文：snippet_limit 参数决定截断上限"""
        monkeypatch.setenv("OLLAMA_SEARCH_API_KEY", "sk-x")
        payload = {"results": [{"title": "T", "content": "长" * 1000, "url": "https://a.com"}]}
        monkeypatch.setattr(web_search.httpx, "AsyncClient", lambda **kw: _fake_client(200, payload))

        strict = await web_search.ollama_search("q", snippet_limit=500)
        broad = await web_search.ollama_search("q", snippet_limit=900)

        assert len(strict[0]["snippet"]) == 500
        assert len(broad[0]["snippet"]) == 900

    @pytest.mark.asyncio
    async def test_non_200_raises_with_actionable_hint(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_SEARCH_API_KEY", "sk-x")
        monkeypatch.setattr(web_search.httpx, "AsyncClient",
                            lambda **kw: _fake_client(401, {"error": "unauthorized"}))

        with pytest.raises(web_search.SearchError) as exc:
            await web_search.ollama_search("q")

        assert "401" in str(exc.value)
        assert "ollama.com/settings/keys" in str(exc.value)   # 可行动提示

    @pytest.mark.asyncio
    async def test_no_key_raises_unavailable(self):
        with pytest.raises(web_search.SearchUnavailable) as exc:
            await web_search.ollama_search("q")
        assert "未配置 Ollama" in str(exc.value)


# ── Tavily ──

class TestTavilySearch:
    @pytest.mark.asyncio
    async def test_parses_results_and_request_shape(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-x")
        fake = _fake_client(200, TAVILY_OK)
        monkeypatch.setattr(web_search.httpx, "AsyncClient", lambda **kw: fake)

        results = await web_search.tavily_search("测试问题", max_results=8)

        assert results[0]["url"] == "https://a.com"
        assert results[0]["snippet"] == "正文A"
        assert results[0]["media"] == "Tavily"
        body = fake.post.call_args.kwargs["json"]
        # 固定 basic 档（1 credit/次）：advanced 不开放
        assert body["search_depth"] == "basic"
        assert body["include_answer"] is False
        assert body["max_results"] == 8

    @pytest.mark.asyncio
    async def test_quota_error_hint(self, monkeypatch):
        """432 是 Tavily 的额度耗尽码（区别于常规 402）"""
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-x")
        monkeypatch.setattr(web_search.httpx, "AsyncClient",
                            lambda **kw: _fake_client(432, {"detail": "quota exceeded"}))

        with pytest.raises(web_search.SearchError) as exc:
            await web_search.tavily_search("q")

        assert "432" in str(exc.value)
        assert "额度" in str(exc.value)

    @pytest.mark.asyncio
    async def test_no_key_raises_unavailable(self):
        with pytest.raises(web_search.SearchUnavailable) as exc:
            await web_search.tavily_search("q")
        assert "未配置 Tavily" in str(exc.value)


# ── Firecrawl ──

class TestFirecrawlSearch:
    @pytest.mark.asyncio
    async def test_parses_results_and_request_shape(self, monkeypatch):
        monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-x")
        fake = _fake_client(200, FIRECRAWL_OK)
        monkeypatch.setattr(web_search.httpx, "AsyncClient", lambda **kw: fake)

        results = await web_search.firecrawl_search("测试问题", max_results=6)

        assert results[0]["url"] == "https://a.com"
        assert results[0]["snippet"] == "正文A"          # description → snippet
        assert results[0]["media"] == "Firecrawl"
        body = fake.post.call_args.kwargs["json"]
        assert body["country"] == "CN"                    # 中文地区定向
        assert body["sources"] == [{"type": "web"}]
        assert body["limit"] == 6

    @pytest.mark.asyncio
    async def test_quota_error_hint(self, monkeypatch):
        """402 是 Firecrawl 的额度耗尽码"""
        monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-x")
        monkeypatch.setattr(web_search.httpx, "AsyncClient",
                            lambda **kw: _fake_client(402, {"error": "insufficient credits"}))

        with pytest.raises(web_search.SearchError) as exc:
            await web_search.firecrawl_search("q")

        assert "402" in str(exc.value)
        assert "firecrawl.dev" in str(exc.value)

    @pytest.mark.asyncio
    async def test_no_key_raises_unavailable(self):
        with pytest.raises(web_search.SearchUnavailable) as exc:
            await web_search.firecrawl_search("q")
        assert "未配置 Firecrawl" in str(exc.value)


# ── 统一入口 web_search_collect ──

class TestWebSearchCollect:
    @pytest.mark.asyncio
    async def test_ollama_success(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_SEARCH_API_KEY", "sk-x")
        monkeypatch.setattr(web_search.httpx, "AsyncClient",
                            lambda **kw: _fake_client(200, OLLAMA_OK))

        outcome = await web_search.web_search_collect("问题")

        assert outcome.ok is True
        assert outcome.provider == "ollama"
        assert outcome.error == ""
        assert len(outcome.sources) == 2
        assert "[1] 标题A" in outcome.text          # 注入模型的编号资料
        assert "https://a.com" in outcome.text

    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self, ddgs_off):
        outcome = await web_search.web_search_collect("   ")
        assert outcome.ok is False
        assert "关键词为空" in outcome.error

    @pytest.mark.asyncio
    async def test_no_backend_configured_reports_actionable_reason(self, ddgs_off):
        """★ 回归锚点：全链无 Key 且无 ddgs 时，必须给出可行动原因（修复前是静默空列表）"""
        outcome = await web_search.web_search_collect("问题")

        assert outcome.ok is False
        assert outcome.sources == []
        assert outcome.provider == ""
        assert "未配置 Ollama" in outcome.error
        assert "未安装 ddgs" in outcome.error

    @pytest.mark.asyncio
    async def test_falls_back_to_ddgs_when_ollama_fails(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_SEARCH_API_KEY", "sk-x")
        monkeypatch.setattr(web_search.httpx, "AsyncClient",
                            lambda **kw: _fake_client(500, {"error": "internal"}))
        monkeypatch.setattr(web_search, "ddgs_available", lambda: True)

        async def _fake_ddgs(query, max_results=5):
            return [{"title": "DDG 结果", "url": "https://ddg.com", "snippet": "s", "media": "DuckDuckGo", "date": ""}]

        monkeypatch.setattr(web_search, "ddgs_search", _fake_ddgs)

        outcome = await web_search.web_search_collect("问题")

        assert outcome.provider == "ddgs"
        assert outcome.sources[0]["title"] == "DDG 结果"

    @pytest.mark.asyncio
    async def test_ollama_fails_tavily_takes_over(self, monkeypatch):
        """链路顺序：Ollama 失败 → 配了 Key 的 Tavily 接棒（不到 DDGS）"""
        monkeypatch.setenv("OLLAMA_SEARCH_API_KEY", "sk-x")
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-x")

        def _client_factory(**kw):
            return _fake_client(500, {"error": "internal"})

        async def _fake_tavily(query, **kw):
            return [{"title": "Tavily 结果", "url": "https://t.com", "snippet": "s", "media": "Tavily", "date": ""}]

        monkeypatch.setattr(web_search.httpx, "AsyncClient", _client_factory)
        monkeypatch.setattr(web_search, "tavily_search", _fake_tavily)
        monkeypatch.setattr(web_search, "ddgs_available", lambda: True)

        async def _fail_ddgs(query, max_results=5):
            raise AssertionError("不应走到 DDGS")

        monkeypatch.setattr(web_search, "ddgs_search", _fail_ddgs)

        outcome = await web_search.web_search_collect("问题")

        assert outcome.provider == "tavily"
        assert outcome.sources[0]["title"] == "Tavily 结果"

    @pytest.mark.asyncio
    async def test_ollama_ok_returns_zero_keeps_reason(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_SEARCH_API_KEY", "sk-x")
        monkeypatch.setattr(web_search.httpx, "AsyncClient",
                            lambda **kw: _fake_client(200, {"results": []}))
        monkeypatch.setattr(web_search, "ddgs_available", lambda: True)

        async def _fake_ddgs(query, max_results=5):
            raise RuntimeError("ddgs 网络不可达")

        monkeypatch.setattr(web_search, "ddgs_search", _fake_ddgs)

        outcome = await web_search.web_search_collect("问题")

        assert outcome.ok is False
        assert "0 条结果" in outcome.error
        assert "DuckDuckGo 兜底失败" in outcome.error

    # 2026-10-08：原 test_search_web_delegates_and_swallows_to_list 随 search_web() 删除
    #   一并移除（它守的正是「失败静默返回空列表」这个被点名的行为）。
    # web_search_collect() 的成功/失败语义由本文件上方的 TestWebSearchCollect 覆盖。


class TestSearchWallClockCap:
    """★ A6（2026-10-13）：总预算必须是**墙钟上限**。

    修复前 `_budget_slice` 只把「剩余预算」当 httpx 标量 timeout 传下去，而 httpx 的
    标量超时是 connect/read/write/pool **各自**的上限——一级实际能跑到约 2× 切片，
    整条链因此能超出 SEARCH_TOTAL_BUDGET（该预算存在的目的正是消掉「最坏 ~90s」），
    超支后 remaining < SEARCH_MIN_SLICE 还会把**免 Key 的 DDGS 兜底**判成「未尝试」
    白白丢掉。修复：每个 provider 外层再套 asyncio.wait_for（与 DDGS 那条同一姿势）。
    """

    @pytest.mark.asyncio
    async def test_hanging_provider_is_capped_by_budget(self, monkeypatch):
        import asyncio as _asyncio
        import time as _time

        monkeypatch.setattr(web_search, "SEARCH_MIN_SLICE", 0.02)
        monkeypatch.setattr(web_search, "ollama_configured", lambda: True)

        async def _hang(query, **kw):
            # 只认 httpx 标量 timeout 的后端在这里（修复前）会一直跑下去
            await _asyncio.sleep(5)
            return [{"title": "T", "url": "https://x.com", "snippet": "s"}]

        monkeypatch.setattr(web_search, "ollama_search", _hang)

        started = _time.monotonic()
        outcome = await web_search.web_search_collect("q", total_budget=0.2)
        elapsed = _time.monotonic() - started

        assert outcome.ok is False
        assert elapsed < 1.0, f"墙钟上限失效：实际跑了 {elapsed:.2f}s"
        # 失败原因可见且可行动（含后端名 + 「被总预算掐断」）
        assert "Ollama 联网搜索失败" in outcome.error
        assert "受联网检索总预算限制" in outcome.error
        assert "超时" in outcome.error

    @pytest.mark.asyncio
    async def test_slow_but_ok_provider_still_succeeds_within_slice(self, monkeypatch):
        """反防：加了墙钟上限不该误杀「在预算内慢慢返回」的后端。"""
        import asyncio as _asyncio

        monkeypatch.setattr(web_search, "SEARCH_MIN_SLICE", 0.02)
        monkeypatch.setattr(web_search, "ollama_configured", lambda: True)

        async def _slow_ok(query, **kw):
            await _asyncio.sleep(0.05)
            return [{"title": "慢但成功", "url": "https://x.com", "snippet": "s"}]

        monkeypatch.setattr(web_search, "ollama_search", _slow_ok)

        outcome = await web_search.web_search_collect("q", total_budget=2.0)
        assert outcome.ok is True
        assert outcome.sources[0]["title"] == "慢但成功"


class TestDescribeSearchBackends:
    def test_reports_availability(self, monkeypatch):
        monkeypatch.setattr(web_search, "ollama_configured", lambda: True)
        monkeypatch.setattr(web_search, "tavily_configured", lambda: False)
        monkeypatch.setattr(web_search, "firecrawl_configured", lambda: False)
        monkeypatch.setattr(web_search, "ddgs_available", lambda: True)
        monkeypatch.setattr(web_search, "resolve_search_proxy", lambda: "")
        assert web_search.describe_search_backends() == {
            "ollama": "configured", "tavily": None, "firecrawl": None,
            "ddgs": "available", "proxy": None}


class TestResolveSearchProxy:
    """★ 坑点回归：ddgs/primp 不读 HTTP_PROXY 环境变量，必须显式传 proxy，
    否则国内网络下免 Key 兜底恒超时（表现为「联网开关没用」）。"""

    @pytest.fixture(autouse=True)
    def _clear_proxy_env(self, monkeypatch):
        for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr("config.PROXY", "", raising=False)

    def test_config_proxy_wins(self, monkeypatch):
        monkeypatch.setattr("config.PROXY", "http://127.0.0.1:7890", raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
        assert web_search.resolve_search_proxy() == "http://127.0.0.1:7890"

    def test_falls_back_to_env_proxy(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
        assert web_search.resolve_search_proxy() == "http://127.0.0.1:7897"

    def test_none_when_absent(self):
        assert web_search.resolve_search_proxy() == ""

    @pytest.mark.asyncio
    async def test_proxy_passed_to_ddgs(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
        seen = {}

        class _FakeDDGS:
            def __init__(self, timeout=None, proxy=None):
                seen["proxy"] = proxy

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def text(self, query, max_results=5):
                return [{"title": "T", "href": "https://x.com", "body": "b"}]

        monkeypatch.setitem(__import__("sys").modules, "ddgs",
                            type("m", (), {"DDGS": _FakeDDGS}))

        result = await web_search.ddgs_search("q", max_results=1)

        assert seen["proxy"] == "http://127.0.0.1:7897"
        assert result[0]["url"] == "https://x.com"

    @pytest.mark.asyncio
    async def test_timeout_message_is_actionable(self, monkeypatch):
        monkeypatch.setattr(web_search, "ollama_configured", lambda: False)
        monkeypatch.setattr(web_search, "tavily_configured", lambda: False)
        monkeypatch.setattr(web_search, "firecrawl_configured", lambda: False)
        monkeypatch.setattr(web_search, "ddgs_available", lambda: True)

        async def _timeout(query, max_results=5):
            raise TimeoutError("超时")

        monkeypatch.setattr(web_search, "ddgs_search", _timeout)

        outcome = await web_search.web_search_collect("q")

        assert "HTTPS_PROXY" in outcome.error


class TestNoDuplicateSearchImplementation:
    def test_research_processor_has_no_inline_search_call(self):
        """回归锚点：深度研究不得再内联第二份搜索调用（单一事实源）"""
        import services.research_processor as rp

        assert not hasattr(rp, "_zhipu_web_search")
        assert not hasattr(rp, "web_search_collect")
        assert hasattr(rp, "_expand_topic")
        src = rp.__doc__ or ""
        assert "web_search" in src

    def test_web_search_module_has_no_zhipu_leftover(self):
        """回归锚点：智谱搜索已整体移除（2026-10-07 用户决定弃用），不得残留符号"""
        assert not hasattr(web_search, "zhipu_search")
        assert not hasattr(web_search, "resolve_zhipu_key")
        assert not hasattr(web_search, "zhipu_configured")
        assert not hasattr(web_search, "ZHIPU_SEARCH_ENDPOINT")
        assert not hasattr(web_search, "ENGINE_MAP")
