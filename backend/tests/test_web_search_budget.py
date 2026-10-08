"""联网检索的**时间预算**契约（services/web_search.py，2026-10-08 新增）。

背景：四级串行 await（Ollama 20s → Tavily 20s → Firecrawl 25s → DDGS 25s）各自吃满超时时，
用户要在「正在联网检索…」里干等最坏 ~90s。现在整条链共享 SEARCH_TOTAL_BUDGET 秒：
每级只拿「剩余时间」，剩余不足 SEARCH_MIN_SLICE 就不再启动下一级，并如实写进 error。

本文件不触网：所有 provider 用替身，只观察「被传了多长时间」「有没有被跳过」。
"""
import asyncio
import time

import pytest

from services import web_search


def _rows(title="结果"):
    return [{"title": title, "url": "https://x.com", "snippet": "s", "media": "t", "date": ""}]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """屏蔽本机真实 Key（config.py 已写入 Tavily/Firecrawl）与 ddgs，避免用例误触网"""
    for name in ("OLLAMA_SEARCH_API_KEY", "TAVILY_API_KEY", "FIRECRAWL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("config.OLLAMA_SEARCH_API_KEY", "", raising=False)
    monkeypatch.setattr("config.TAVILY_API_KEY", "", raising=False)
    monkeypatch.setattr("config.FIRECRAWL_API_KEY", "", raising=False)
    monkeypatch.setattr(web_search, "ddgs_available", lambda: False)


@pytest.fixture
def tiny_slice(monkeypatch):
    """把「最小可用切片」压小：这样预算用例不用真等 3s 就能走到跳过分支"""
    monkeypatch.setattr(web_search, "SEARCH_MIN_SLICE", 0.02)


class TestSearchTotalBudget:
    @pytest.mark.asyncio
    async def test_remaining_budget_caps_provider_timeout(self, monkeypatch, tiny_slice):
        """★ 核心：给某一级的超时必须是「剩余预算」而不是它自己的默认超时"""
        monkeypatch.setattr(web_search, "ollama_configured", lambda: True)
        seen = {}

        async def _ok(query, **kw):
            seen["timeout"] = kw.get("timeout")
            return _rows()

        monkeypatch.setattr(web_search, "ollama_search", _ok)

        outcome = await web_search.web_search_collect("q", total_budget=1.0)

        assert outcome.ok is True and outcome.provider == "ollama"
        assert outcome.error == ""
        assert seen["timeout"] is not None
        assert seen["timeout"] < web_search.OLLAMA_TIMEOUT     # 被预算压过
        assert seen["timeout"] <= 1.0

    @pytest.mark.asyncio
    async def test_budget_disabled_keeps_provider_default(self, monkeypatch):
        """total_budget<=0 = 关闭预算：逐级回到各自的默认超时（旧行为，可回退）"""
        monkeypatch.setattr(web_search, "ollama_configured", lambda: True)
        seen = {}

        async def _ok(query, **kw):
            seen["timeout"] = kw.get("timeout")
            return _rows()

        monkeypatch.setattr(web_search, "ollama_search", _ok)

        await web_search.web_search_collect("q", total_budget=0)

        assert seen["timeout"] == web_search.OLLAMA_TIMEOUT

    @pytest.mark.asyncio
    async def test_sliced_failure_says_budget_limited_it(self, monkeypatch, tiny_slice):
        """被预算压过超时的失败，要能看出「是预算掐的」而不是后端本身就慢"""
        monkeypatch.setattr(web_search, "ollama_configured", lambda: True)
        monkeypatch.setattr(web_search, "tavily_configured", lambda: True)

        async def _timeout_like_real(query, **kw):
            await asyncio.sleep(kw.get("timeout", 0))    # 真实后端会在自己的超时上断掉
            raise web_search.SearchError("请求超时（模拟）")

        async def _never(query, **kw):
            raise AssertionError("预算已被吃满，Tavily 不该被调用")

        monkeypatch.setattr(web_search, "ollama_search", _timeout_like_real)
        monkeypatch.setattr(web_search, "tavily_search", _never)

        outcome = await web_search.web_search_collect("q", total_budget=0.15)

        assert outcome.ok is False
        assert "Ollama 联网搜索失败" in outcome.error
        assert "受联网检索总预算限制" in outcome.error

    @pytest.mark.asyncio
    async def test_exhausted_budget_skips_remaining_levels(self, monkeypatch, tiny_slice):
        """★ 回归锚点：预算耗尽后后续级别不再启动，error 说明「哪级没试、为什么」"""
        monkeypatch.setattr(web_search, "ollama_configured", lambda: True)
        monkeypatch.setattr(web_search, "tavily_configured", lambda: True)
        monkeypatch.setattr(web_search, "ddgs_available", lambda: True)

        async def _timeout_like_real(query, **kw):
            await asyncio.sleep(kw.get("timeout", 0))
            raise web_search.SearchError("请求超时（模拟）")

        async def _never(*a, **kw):
            raise AssertionError("预算耗尽后不该再启动这一级")

        monkeypatch.setattr(web_search, "ollama_search", _timeout_like_real)
        monkeypatch.setattr(web_search, "tavily_search", _never)
        monkeypatch.setattr(web_search, "ddgs_search", _never)

        started = time.monotonic()
        outcome = await web_search.web_search_collect("q", total_budget=0.2)
        elapsed = time.monotonic() - started

        assert outcome.ok is False
        assert elapsed < web_search.OLLAMA_TIMEOUT        # 不等于第一级的默认 20s
        assert "Tavily：未尝试（联网检索总预算" in outcome.error
        assert "DuckDuckGo 兜底：未尝试（联网检索总预算" in outcome.error

    @pytest.mark.asyncio
    async def test_ddgs_fallback_runs_under_budget(self, monkeypatch, tiny_slice):
        """DDGS 兜底在预算内正常接棒（不被预算误杀）"""
        monkeypatch.setattr(web_search, "ddgs_available", lambda: True)

        async def _ok_ddgs(query, max_results=5):
            return _rows("DDG 结果")

        monkeypatch.setattr(web_search, "ddgs_search", _ok_ddgs)

        outcome = await web_search.web_search_collect("q", total_budget=2.0)

        assert outcome.ok is True and outcome.provider == "ddgs"
        assert outcome.sources[0]["title"] == "DDG 结果"

    @pytest.mark.asyncio
    async def test_ddgs_cut_off_by_total_budget(self, monkeypatch, tiny_slice):
        """DDGS 自带 25s 总超时，但总预算更小 → 按总预算掐断且文案点明"""
        monkeypatch.setattr(web_search, "ddgs_available", lambda: True)

        async def _hang_ddgs(query, max_results=5):
            await asyncio.sleep(1.0)
            return _rows()

        monkeypatch.setattr(web_search, "ddgs_search", _hang_ddgs)

        started = time.monotonic()
        outcome = await web_search.web_search_collect("q", total_budget=0.2)
        elapsed = time.monotonic() - started

        assert outcome.ok is False
        assert elapsed < 0.9                                       # 被总预算掐断，而非等满 1.0s
        assert "DuckDuckGo 兜底超时" in outcome.error
        assert "受联网检索总预算限制" in outcome.error
