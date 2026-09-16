"""
剩余服务纯逻辑函数综合测试：web_search
"""
from services.web_search import format_search_context


# ── web_search ──
class TestFormatSearchContext:
    def test_empty(self):
        assert format_search_context([]).startswith("（未搜索到")

    def test_single_result(self):
        ctx = format_search_context([{
            "title": "Test", "url": "https://x.com", "snippet": "desc"
        }])
        assert "Test" in ctx
        assert "https://x.com" in ctx

    def test_multiple(self):
        results = [
            {"title": "A", "url": "a.com", "snippet": "a desc"},
            {"title": "B", "url": "b.com", "snippet": "b desc", "date": "2026"},
        ]
        ctx = format_search_context(results)
        assert "A" in ctx and "B" in ctx
        assert "2026" in ctx
