"""
model_repo 模型配置数据库测试
"""
import pytest
import sqlite3
from unittest.mock import patch

from db.model_repo import (
    get_all_models, get_model_by_key, add_model,
    update_model, delete_model,
)

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS model_configs (
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    api_key TEXT,
    endpoint TEXT NOT NULL,
    model_id TEXT,
    auth_type TEXT DEFAULT 'bearer',
    temperature REAL DEFAULT 0.3,
    max_tokens INTEGER DEFAULT 4096,
    is_builtin INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(CREATE_TABLES_SQL)
    conn.close()
    return path


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

    def test_update_nonexistent(self):
        assert update_model("no", name="x") is False


class TestDeleteModel:
    def test_delete_regular_model(self):
        add_model("m", "M", "https://x.com")
        assert delete_model("m")
        assert get_model_by_key("m") is None

    def test_delete_builtin_refused(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(CREATE_TABLES_SQL)
        conn.execute("INSERT INTO model_configs (key,name,endpoint,is_builtin) VALUES (?,?,?,1)",
                     ("builtin", "Builtin", "https://x.com"))
        conn.commit()
        conn.close()
        with patch("db.model_repo.get_connection", return_value=sqlite3.connect(":memory:")):
            # 需要重新建表... 改变策略，直接在同一个 test_db 里插入 builtin
            pass

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
