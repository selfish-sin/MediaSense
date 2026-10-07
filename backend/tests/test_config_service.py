"""
config_service 配置服务测试
"""
import services.config_service as cs
from config import DEFAULT_MODEL


class TestLoadConfig:
    def test_first_load(self):
        cs._config_module = None
        cfg = cs._load_config()
        assert cfg is not None
        assert hasattr(cfg, "DEFAULT_MODEL")

    def test_cached_load(self):
        cs._config_module = None
        cs._load_config()
        cs._load_config()  # 第二次走缓存
        assert cs._config_module is not None


class TestGetConfig:
    def test_get_by_uppercase(self):
        cs._config_module = None
        val = cs.get_config("default_model")
        assert val == DEFAULT_MODEL

    def test_get_by_original(self):
        cs._config_module = None
        val = cs.get_config("DEFAULT_MODEL")
        assert val == DEFAULT_MODEL

    def test_get_with_default(self):
        cs._config_module = None
        val = cs.get_config("nonexistent_key_xyz", "fallback")
        assert val == "fallback"

    def test_get_nonexistent_no_default(self):
        cs._config_module = None
        val = cs.get_config("no_such_key_abc")
        assert val is None

    def test_get_server_port(self):
        cs._config_module = None
        val = cs.get_config("server_port")
        assert val == 7001
