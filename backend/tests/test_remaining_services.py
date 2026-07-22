"""
剩余服务纯逻辑函数综合测试：web_search + textbook + research
"""
import pytest
from unittest.mock import patch, MagicMock
from services.web_search import format_search_context, _parse_chat_response
from services.textbook_processor import _fallback_structure, _parse_json, _extract_time_range


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


class TestParseChatResponse:
    def test_dict_choices_with_web_search(self):
        resp = {
            "choices": [{
                "message": {
                    "content": "summary",
                    "web_search": [
                        {"title": "T1", "link": "https://a.com", "content": "c1"}
                    ]
                }
            }]
        }
        results = _parse_chat_response(resp, 5, "query")
        assert len(results) == 1
        assert results[0]["title"] == "T1"

    def test_dict_no_web_search(self):
        resp = {"choices": [{"message": {"content": "fallback content"}}]}
        results = _parse_chat_response(resp, 5, "q")
        assert len(results) == 1
        assert results[0]["title"].startswith("智谱AI")

    def test_empty_choices(self):
        assert _parse_chat_response({"choices": []}, 5, "q") == []


# ── textbook_processor ──
# ── textbook_processor ──
    def test_normal(self):
        t = _extract_time_range("some text 00:05 / 10:15", "00:00", "30:00")
        assert "00:05" in t


class TestParseJson:
    def test_valid_json(self):
        d = _parse_json('{"key": "value"}')
        assert d == {"key": "value"}

    def test_markdown_json_block(self):
        d = _parse_json('```json\n{"a":1}\n```')
        assert d == {"a": 1}

    def test_bad_json(self):
        d = _parse_json("not json")
        assert d == {}
