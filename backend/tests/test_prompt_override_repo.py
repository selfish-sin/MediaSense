"""
prompt_override_repo 模块覆盖 CRUD 测试
"""
import pytest
import sqlite3
from unittest.mock import patch

from db.prompt_override_repo import (
    list_overrides, get_override, upsert_override,
    delete_override, reset_all_overrides, restore_override_backup,
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


class TestUpsertBackupSemantics:
    def test_first_save_records_original(self):
        upsert_override("role", "general", "v1")
        o = get_override("role", "general")
        assert o["original_text"] == "v1"
        assert (o["previous_text"] or "") == ""

    def test_text_change_rotates_previous(self):
        upsert_override("role", "general", "v1")
        upsert_override("role", "general", "v2")
        o = get_override("role", "general")
        assert o["custom_text"] == "v2"
        assert o["previous_text"] == "v1"
        assert o["original_text"] == "v1"  # 最初版本不变

    def test_multi_change_keeps_first_original(self):
        for v in ("v1", "v2", "v3"):
            upsert_override("role", "general", v)
        o = get_override("role", "general")
        assert o["custom_text"] == "v3"
        assert o["previous_text"] == "v2"
        assert o["original_text"] == "v1"

    def test_same_text_no_rotation(self):
        upsert_override("role", "general", "v1")
        upsert_override("role", "general", "v2")
        upsert_override("role", "general", "v2")  # 重复保存不轮转
        o = get_override("role", "general")
        assert o["previous_text"] == "v1"

    def test_display_name_persisted_and_kept(self):
        upsert_override("role", "my_mod", "t", display_name="我的模块")
        assert get_override("role", "my_mod")["display_name"] == "我的模块"
        upsert_override("role", "my_mod", "t2")  # 普通编辑不带名 → 保留
        assert get_override("role", "my_mod")["display_name"] == "我的模块"
        upsert_override("role", "my_mod", "t3", display_name="改名")
        assert get_override("role", "my_mod")["display_name"] == "改名"


class TestRestoreOverrideBackup:
    def test_restore_previous(self):
        upsert_override("role", "general", "v1")
        upsert_override("role", "general", "v2")
        result = restore_override_backup("role", "general", "previous")
        assert result["custom_text"] == "v1"

    def test_restore_original(self):
        upsert_override("role", "general", "v1")
        upsert_override("role", "general", "v2")
        result = restore_override_backup("role", "general", "original")
        assert result["custom_text"] == "v1"

    def test_restore_toggles_between_versions(self):
        upsert_override("role", "general", "v1")
        upsert_override("role", "general", "v2")
        restore_override_backup("role", "general", "previous")  # v1，previous 轮转为 v2
        result = restore_override_backup("role", "general", "previous")  # 回到 v2
        assert result["custom_text"] == "v2"

    def test_restore_no_backup_returns_none(self):
        upsert_override("role", "general", "v1")
        assert restore_override_backup("role", "general", "previous") is None

    def test_restore_nonexistent_returns_none(self):
        assert restore_override_backup("no", "no", "original") is None

    def test_restore_bad_version_raises(self):
        with pytest.raises(ValueError):
            restore_override_backup("role", "general", "bogus")
