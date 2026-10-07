"""
配置服务 — 统一读取 config.py 的配置项
用法：from services.config_service import get_config
"""


_config_module = None


def _load_config():
    """懒加载 config 模块（避免循环导入）"""
    global _config_module
    if _config_module is not None:
        return _config_module
    # backend/ 目录要在 sys.path 里，main.py 启动时会加
    import config
    _config_module = config
    return _config_module


def get_config(key: str, default=None):
    """
    获取配置项，按优先级：
    1. 大写常量（如 download_dir → DOWNLOAD_DIR）
    2. 原样 key
    3. 返回 default
    """
    cfg = _load_config()
    # 优先找大写常量（config.py 规范）
    val = getattr(cfg, key.upper(), None)
    if val is not None:
        return val
    # 再试原样
    val = getattr(cfg, key, None)
    if val is not None:
        return val
    return default
