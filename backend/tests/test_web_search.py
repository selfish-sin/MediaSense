"""
联网检索链路测试（services/web_search.py 单一实现）

背景（2026-09-16）：追问面板的「联网」开关是死按钮——智谱主路径用只读环境变量的 Key
（未配即跳过）、旧代码走已被官方独立端点取代的 chat+tools 姿势、DDGS 兜底的 ddgs 包未安装，
三层全部失效却静默返回空列表。本文件锁定修复后的契约：
  - 智谱独立 Web Search API 的请求姿势与 search_result 解析
  - Key 三级兜底（env → config → DB 模型）
  - 智谱失败 → DDGS 兜底 → 都失败时**必须带可行动原因**（不静默）
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


ZHIPU_OK = {
    "id": "t1",
    "search_result": [
        {"title": "标题A", "content": "摘要 A", "link": "https://a.com",
         "media": "站点A", "publish_date": "2026-09-01"},
        {"title": "标题B", "content": "摘要 B", "link": "https://b.com", "media": "站点B"},
    ],
}


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch):
    """屏蔽开发机上的 ZHIPU_API_KEY 环境变量，避免用例受本地配置影响"""
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.setattr("config.ZHIPU_API_KEY", "", raising=False)


@pytest.fixture
def ddgs_off(monkeypatch):
    """默认关掉 DDGS，用例按需单独打开（否则真机装了 ddgs 会触发真实网络请求）"""
    monkeypatch.setattr(web_search, "ddgs_available", lambda: False)


# ── Key 三级兜底 ──

class TestResolveZhipuKey:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "sk-env")
        assert web_search.resolve_zhipu_key() == "sk-env"
        assert web_search.zhipu_configured() is True

    def test_falls_back_to_db_model(self, monkeypatch):
        monkeypatch.setattr(
            "db.model_repo.get_all_models",
            lambda: [{"api_key": "sk-db", "endpoint": "https://open.bigmodel.cn/api/paas/v4/chat/completions"}],
        )
        assert web_search.resolve_zhipu_key() == "sk-db"

    def test_db_model_without_zhipu_endpoint_ignored(self, monkeypatch):
        monkeypatch.setattr(
            "db.model_repo.get_all_models",
            lambda: [{"api_key": "sk-other", "endpoint": "https://api.deepseek.com/v1/chat/completions"}],
        )
        assert web_search.resolve_zhipu_key() == ""

    def test_nothing_configured(self, monkeypatch):
        monkeypatch.setattr("db.model_repo.get_all_models", lambda: [])
        assert web_search.resolve_zhipu_key() == ""
        assert web_search.zhipu_configured() is False


# ── 智谱独立 Web Search API ──

class TestZhipuSearch:
    @pytest.mark.asyncio
    async def test_parses_search_result(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "sk-x")
        fake = _fake_client(200, ZHIPU_OK)
        monkeypatch.setattr(web_search.httpx, "AsyncClient", lambda **kw: fake)

        results = await web_search.zhipu_search("测试问题", max_results=5)

        assert [r["title"] for r in results] == ["标题A", "标题B"]
        assert results[0]["url"] == "https://a.com"          # link → url
        assert results[0]["snippet"] == "摘要 A"             # content → snippet
        assert results[0]["date"] == "2026-09-01"            # publish_date → date
        # 请求姿势：独立 web_search 端点 + search_query/search_engine/search_intent
        body = fake.post.call_args.kwargs["json"]
        assert fake.post.call_args.args[0] == web_search.ZHIPU_SEARCH_ENDPOINT
        assert body["search_query"] == "测试问题"
        assert body["search_engine"] == "search_pro"
        assert body["search_intent"] is True
        assert body["count"] == 5

    @pytest.mark.asyncio
    async def test_long_query_truncated_to_70(self, monkeypatch):
        # 官方限制 search_query ≤ 70 字符，超长会导致参数错误
        monkeypatch.setenv("ZHIPU_API_KEY", "sk-x")
        fake = _fake_client(200, {"search_result": []})
        monkeypatch.setattr(web_search.httpx, "AsyncClient", lambda **kw: fake)

        await web_search.zhipu_search("长" * 200)

        assert len(fake.post.call_args.kwargs["json"]["search_query"]) == web_search.SEARCH_QUERY_MAX

    @pytest.mark.asyncio
    async def test_engine_mapping(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "sk-x")
        fake = _fake_client(200, {"search_result": []})
        monkeypatch.setattr(web_search.httpx, "AsyncClient", lambda **kw: fake)

        await web_search.zhipu_search("q", engine="quark")

        assert fake.post.call_args.kwargs["json"]["search_engine"] == "search_pro_quark"

    @pytest.mark.asyncio
    async def test_non_200_raises_with_server_reason(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "sk-x")
        payload = {"error": {"code": "1002", "message": "Authorization Token illegal"}}
        monkeypatch.setattr(web_search.httpx, "AsyncClient",
                            lambda **kw: _fake_client(401, payload))

        with pytest.raises(web_search.SearchError) as exc:
            await web_search.zhipu_search("q")

        assert "401" in str(exc.value)
        assert "Authorization Token illegal" in str(exc.value)

    @pytest.mark.asyncio
    async def test_no_key_raises_unavailable(self, monkeypatch):
        monkeypatch.setattr("db.model_repo.get_all_models", lambda: [])
        with pytest.raises(web_search.SearchUnavailable) as exc:
            await web_search.zhipu_search("q")
        assert "未配置智谱 API Key" in str(exc.value)


# ── 统一入口 web_search_collect ──

class TestWebSearchCollect:
    @pytest.mark.asyncio
    async def test_zhipu_success(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "sk-x")
        monkeypatch.setattr(web_search.httpx, "AsyncClient",
                            lambda **kw: _fake_client(200, ZHIPU_OK))

        outcome = await web_search.web_search_collect("问题")

        assert outcome.ok is True
        assert outcome.provider == "zhipu"
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
    async def test_no_backend_configured_reports_actionable_reason(self, ddgs_off, monkeypatch):
        """★ 回归锚点：未配 Key 且无 ddgs 兜底时，必须给出可行动原因（修复前是静默空列表）"""
        monkeypatch.setattr("db.model_repo.get_all_models", lambda: [])

        outcome = await web_search.web_search_collect("问题")

        assert outcome.ok is False
        assert outcome.sources == []
        assert outcome.provider == ""
        assert "未配置智谱 API Key" in outcome.error
        assert "未安装 ddgs" in outcome.error

    @pytest.mark.asyncio
    async def test_falls_back_to_ddgs_when_zhipu_fails(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "sk-x")
        monkeypatch.setattr(web_search.httpx, "AsyncClient",
                            lambda **kw: _fake_client(500, {"error": {"message": "internal"}}))
        monkeypatch.setattr(web_search, "ddgs_available", lambda: True)

        async def _fake_ddgs(query, max_results=5):
            return [{"title": "DDG 结果", "url": "https://ddg.com", "snippet": "s", "media": "DuckDuckGo", "date": ""}]

        monkeypatch.setattr(web_search, "ddgs_search", _fake_ddgs)

        outcome = await web_search.web_search_collect("问题")

        assert outcome.provider == "ddgs"
        assert outcome.sources[0]["title"] == "DDG 结果"

    @pytest.mark.asyncio
    async def test_zhipu_ok_returns_zero_keeps_reason(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "sk-x")
        monkeypatch.setattr(web_search.httpx, "AsyncClient",
                            lambda **kw: _fake_client(200, {"search_result": []}))
        monkeypatch.setattr(web_search, "ddgs_available", lambda: True)

        async def _fake_ddgs(query, max_results=5):
            raise RuntimeError("ddgs 网络不可达")

        monkeypatch.setattr(web_search, "ddgs_search", _fake_ddgs)

        outcome = await web_search.web_search_collect("问题")

        assert outcome.ok is False
        assert "0 条结果" in outcome.error
        assert "DuckDuckGo 兜底失败" in outcome.error

    @pytest.mark.asyncio
    async def test_search_web_delegates_and_swallows_to_list(self, monkeypatch):
        """search_web 保持既有签名（routers/search.py 复用）：成功给 list，失败给 []"""
        monkeypatch.setenv("ZHIPU_API_KEY", "sk-x")
        monkeypatch.setattr(web_search.httpx, "AsyncClient",
                            lambda **kw: _fake_client(200, ZHIPU_OK))

        assert len(await web_search.search_web("q")) == 2


class TestDescribeSearchBackends:
    def test_reports_availability(self, monkeypatch):
        monkeypatch.setattr(web_search, "zhipu_configured", lambda: False)
        monkeypatch.setattr(web_search, "ddgs_available", lambda: True)
        assert web_search.describe_search_backends() == {"zhipu": None, "ddgs": "available"}


class TestNoDuplicateSearchImplementation:
    def test_research_processor_has_no_inline_zhipu_call(self):
        """回归锚点：深度研究不得再内联第二份智谱调用（单一事实源）"""
        import services.research_processor as rp

        assert not hasattr(rp, "_zhipu_web_search")
        assert not hasattr(rp, "web_search_collect")
        assert not hasattr(rp, "ZHIPU_ENDPOINT")
        assert hasattr(rp, "_expand_topic")
        src = rp.__doc__ or ""
        assert "web_search" in src
