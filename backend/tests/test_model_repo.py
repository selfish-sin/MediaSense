"""
model_repo 模型配置数据库测试
"""
import pytest
import sqlite3
from unittest.mock import patch

from db.model_repo import (
    get_all_models, get_model_by_key, add_model,
    update_model, delete_model,
    get_default_model_key, set_default_model,
    get_default_correct_model_key, set_default_correct_model,
)


@pytest.fixture(autouse=True)
def _patch_conn(db_path):
    def _get_conn():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    with patch("db.model_repo.get_connection", side_effect=_get_conn):
        yield


class TestAddModel:
    def test_add_basic(self):
        assert add_model("m1", "Model 1", "https://api.example.com", api_key="sk-xxx")
        m = get_model_by_key("m1")
        assert m["name"] == "Model 1"
        assert m["endpoint"] == "https://api.example.com"
        assert m["api_key"] == "sk-xxx"

    def test_add_with_all_fields(self):
        assert add_model("m2", "M2", "https://x.com", api_key="k",
                         model_id="gpt-4", auth_type="direct",
                         temperature=0.7, max_tokens=8192)
        m = get_model_by_key("m2")
        assert m["model_id"] == "gpt-4"
        assert m["auth_type"] == "direct"
        assert m["temperature"] == 0.7
        assert m["max_tokens"] == 8192

    def test_add_with_engine(self):
        assert add_model("m3", "M3", "https://x.com", engine="阿里云")
        m = get_model_by_key("m3")
        assert m["engine"] == "阿里云"

    def test_engine_default_empty(self):
        assert add_model("m4", "M4", "https://x.com")
        m = get_model_by_key("m4")
        assert m["engine"] == ""

    def test_engine_in_list(self):
        add_model("m5", "M5", "https://x.com", engine="DeepSeek")
        models = get_all_models()
        assert any(m["engine"] == "DeepSeek" for m in models)

    def test_add_duplicate(self):
        add_model("m1", "M1", "https://a.com")
        assert add_model("m1", "M1", "https://a.com") is False


class TestGetModels:
    def test_get_all_empty(self):
        assert get_all_models() == []

    def test_get_all_with_data(self):
        add_model("a", "A", "https://a.com")
        add_model("b", "B", "https://b.com")
        models = get_all_models()
        assert len(models) == 2
        assert models[0]["key"] == "a"

    def test_get_by_key_exists(self):
        add_model("x", "X", "https://x.com", api_key="sk")
        m = get_model_by_key("x")
        assert m is not None
        assert m["api_key"] == "sk"

    def test_get_by_key_not_exists(self):
        assert get_model_by_key("no") is None


class TestUpdateModel:
    def test_update_name(self):
        add_model("m", "Old", "https://x.com")
        assert update_model("m", name="New")
        m = get_model_by_key("m")
        assert m["name"] == "New"

    def test_update_multiple_fields(self):
        add_model("m", "X", "https://x.com")
        update_model("m", name="Y", temperature=0.9, max_tokens=2048)
        m = get_model_by_key("m")
        assert m["name"] == "Y"
        assert m["temperature"] == 0.9
        assert m["max_tokens"] == 2048

    def test_update_engine(self):
        add_model("m", "X", "https://x.com")
        assert update_model("m", engine="硅基流动")
        assert get_model_by_key("m")["engine"] == "硅基流动"

    def test_update_nonexistent(self):
        assert update_model("no", name="x") is False


class TestDeleteModel:
    def test_delete_regular_model(self):
        add_model("m", "M", "https://x.com")
        assert delete_model("m")
        assert get_model_by_key("m") is None

    def test_delete_nonexistent(self):
        assert delete_model("no") is False

    def test_delete_builtin(self, db_path):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("INSERT INTO model_configs (key,name,endpoint,is_builtin) VALUES (?,?,?,1)",
                     ("builtin", "Builtin", "https://x.com"))
        conn.commit()
        conn.close()
        assert delete_model("builtin") is False


class TestDefaultModel:
    def test_unset_falls_back_to_config_default(self):
        from config import DEFAULT_MODEL
        assert get_default_model_key() == DEFAULT_MODEL

    def test_set_default(self):
        add_model("m1", "Model 1", "https://api.example.com")
        assert set_default_model("m1")
        assert get_default_model_key() == "m1"

    def test_set_default_nonexistent_model(self):
        assert set_default_model("ghost") is False

    def test_default_falls_back_when_model_deleted(self):
        add_model("m1", "Model 1", "https://api.example.com")
        set_default_model("m1")
        delete_model("m1")
        from config import DEFAULT_MODEL
        assert get_default_model_key() == DEFAULT_MODEL

    def test_set_default_overwrites(self):
        add_model("a", "A", "https://a.com")
        add_model("b", "B", "https://b.com")
        set_default_model("a")
        set_default_model("b")
        assert get_default_model_key() == "b"


class TestDefaultCorrectModel:
    def test_unset_returns_empty(self):
        """校正默认未设置时返回空串（调用方回退总结主模型），不借用 config.DEFAULT_MODEL。"""
        assert get_default_correct_model_key() == ""

    def test_set_and_get(self):
        add_model("glm-5-2", "glm-5.2", "https://api.example.com")
        assert set_default_correct_model("glm-5-2")
        assert get_default_correct_model_key() == "glm-5-2"

    def test_set_nonexistent_model(self):
        assert set_default_correct_model("ghost") is False

    def test_falls_back_when_model_deleted(self):
        add_model("m1", "Model 1", "https://api.example.com")
        set_default_correct_model("m1")
        delete_model("m1")
        assert get_default_correct_model_key() == ""

    def test_independent_from_summary_default(self):
        """校正默认与总结主模型默认互不干扰。"""
        add_model("a", "A", "https://a.com")
        add_model("b", "B", "https://b.com")
        set_default_model("a")
        set_default_correct_model("b")
        assert get_default_model_key() == "a"
        assert get_default_correct_model_key() == "b"
