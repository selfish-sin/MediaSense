"""
全局测试 fixtures 和公共 mock 设置
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

import pytest

# 确保 backend 目录在 sys.path（pythonpath=. 已配，这里兜底）
BACKEND_DIR = Path(__file__).parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="youtube_tool_test_"))
os.environ["YT_TEST_DATA_DIR"] = str(TEST_DATA_DIR)


@pytest.fixture(scope="session", autouse=True)
def _isolate_data_dir():
    """把 config 的数据目录指向临时目录，避免污染真实 data/"""
    tmp_downloads = TEST_DATA_DIR / "downloads"
    tmp_downloads.mkdir(exist_ok=True)
    from db.database import init_db
    init_db()
    yield TEST_DATA_DIR
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)
