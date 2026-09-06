"""
prompt_override_repo 模块覆盖 CRUD 测试
"""
import pytest
import sqlite3
from unittest.mock import patch

from db.prompt_override_repo import (
    list_overrides, get_override, upsert_override,
    delete_override, reset_all_overrides,
)


@pytest.fixture(autouse=True)
def _patch_conn(db_path):
    def _get_conn():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    with patch("db.prompt_override_repo.get_connection", side_effect=_get_conn):
        yield


class TestUpsertOverride:
    def test_insert_new(self):
        result = upsert_override("role", "general", "自定义文本")
        assert result["dimension_key"] == "role"
        assert result["module_key"] == "general"
        assert result["custom_text"] == "自定义文本"

    def test_upsert_update(self):
        upsert_override("role", "general", "v1")
        upsert_override("role", "general", "v2")
        override = get_override("role", "general")
        assert override["custom_text"] == "v2"


class TestGetOverride:
    def test_get_existing(self):
        upsert_override("output", "summary", "text")
        o = get_override("output", "summary")
        assert o is not None
        assert o["dimension_key"] == "output"

    def test_get_nonexistent(self):
        assert get_override("no", "no") is None


class TestListOverrides:
    def test_list_empty(self):
        assert list_overrides() == []

    def test_list_with_data(self):
        upsert_override("role", "general", "t1")
        upsert_override("role", "academic", "t2")
        upsert_override("rules", "deep", "t3")
        overrides = list_overrides()
        assert len(overrides) == 3
        assert overrides[0]["dimension_key"] == "role"


class TestDeleteOverride:
    def test_delete_existing(self):
        upsert_override("role", "general", "text")
        assert delete_override("role", "general") is True
        assert get_override("role", "general") is None

    def test_delete_nonexistent(self):
        assert delete_override("no", "no") is False


class TestResetAllOverrides:
    def test_reset_all(self):
        upsert_override("a", "a", "t")
        upsert_override("b", "b", "t")
        count = reset_all_overrides()
        assert count == 2
        assert list_overrides() == []

    def test_reset_empty(self):
        assert reset_all_overrides() == 0
