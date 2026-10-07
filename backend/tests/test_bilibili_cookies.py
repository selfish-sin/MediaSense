"""
bilibili_cookies B站 Cookie 管理测试
"""
import pytest
import json
from unittest.mock import patch
from pathlib import Path
import services.bilibili_cookies as bc


@pytest.fixture(autouse=True)
def _reset_global():
    bc._bilibili_cookies.clear()
    yield
    bc._bilibili_cookies.clear()


class TestLoadCookies:
    def test_empty_no_file(self):
        with patch.object(Path, "exists", return_value=False):
            bc._bilibili_cookies.clear()
            result = bc.load_cookies()
            assert result is None

    def test_from_memory(self):
        bc._bilibili_cookies["test"] = "val"
        result = bc.load_cookies()
        assert result == {"test": "val"}

    def test_from_file(self, tmp_path):
        bc._bilibili_cookies.clear()
        cookie_file = tmp_path / "cookies.json"
        cookie_file.write_text(json.dumps({"a": "b"}), encoding="utf-8")
        with patch("services.bilibili_cookies.BILIBILI_COOKIE_FILE", cookie_file):
            result = bc.load_cookies()
            assert result == {"a": "b"}

    def test_corrupt_file(self, tmp_path):
        bc._bilibili_cookies.clear()
        cookie_file = tmp_path / "cookies.json"
        cookie_file.write_text("not json", encoding="utf-8")
        with patch("services.bilibili_cookies.BILIBILI_COOKIE_FILE", cookie_file):
            result = bc.load_cookies()
            assert result is None


class TestSaveCookies:
    def test_saves_to_file(self, tmp_path):
        bc._bilibili_cookies.clear()
        cookie_file = tmp_path / "cookies.json"
        with patch("services.bilibili_cookies.BILIBILI_COOKIE_FILE", cookie_file):
            bc.save_cookies({"key": "val"})
            assert cookie_file.exists()
            saved = json.loads(cookie_file.read_text("utf-8"))
            assert saved["key"] == "val"


class TestUpdateCookies:
    def test_updates_both(self, tmp_path):
        bc._bilibili_cookies.clear()
        bc._bilibili_cookies["existing"] = "old"
        cookie_file = tmp_path / "cookies.json"
        with patch("services.bilibili_cookies.BILIBILI_COOKIE_FILE", cookie_file):
            bc.update_cookies({"new": "data"}, refresh_token="rft")
        assert bc._bilibili_cookies["new"] == "data"
        assert bc._bilibili_cookies["refresh_token"] == "rft"

    def test_no_refresh_token(self, tmp_path):
        bc._bilibili_cookies.clear()
        cookie_file = tmp_path / "cookies.json"
        with patch("services.bilibili_cookies.BILIBILI_COOKIE_FILE", cookie_file):
            bc.update_cookies({"new": "data"})
        assert bc._bilibili_cookies["new"] == "data"
        assert "refresh_token" not in bc._bilibili_cookies


class TestClearCookies:
    def test_clears_memory_and_file(self, tmp_path):
        bc._bilibili_cookies["a"] = "b"
        cookie_file = tmp_path / "cookies.json"
        cookie_file.write_text(json.dumps({"x": "y"}), encoding="utf-8")
        with patch("services.bilibili_cookies.BILIBILI_COOKIE_FILE", cookie_file):
            bc.clear_cookies()
            assert bc._bilibili_cookies == {}
            assert not cookie_file.exists()


class TestGetStatus:
    def test_logged_in(self):
        bc._bilibili_cookies["a"] = "b"
        status = bc.get_status()
        assert status["logged_in"] is True
        assert status["cookies_count"] == 1

    def test_not_logged_in(self):
        bc._bilibili_cookies.clear()
        status = bc.get_status()
        assert status["logged_in"] is False
        assert status["cookies_count"] == 0
