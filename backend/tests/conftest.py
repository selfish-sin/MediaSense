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

# 哨兵：区分「属性原本不存在」与「属性存在但值为 None」（还原时分别用 delattr / setattr）
_MISSING = object()


@pytest.fixture(scope="session", autouse=True)
def _isolate_data_dir():
    """把 config 的数据目录指向临时目录，避免污染真实 data/"""
    tmp_downloads = TEST_DATA_DIR / "downloads"
    tmp_downloads.mkdir(exist_ok=True)
    # ★ 测试期默认关闭 AI/转录结果缓存（2026-10-06）：测试共用会话级临时 DB，
    #   同 endpoint+model+prompt 的键会跨测试命中（前一测试的结果污染后一测试，
    #   实测 TestCallAiEmptyChoicesChunk 拿到别的用例缓存的"你好"）。要测缓存的
    #   用例自行用 cache_on fixture 打开（tests/test_ai_cache.py）。
    import config as _config
    _config.AI_CACHE_ENABLED = False
    from db.database import init_db
    init_db()
    yield TEST_DATA_DIR
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _isolate_model_dirs():
    """把所有本地模型目录重定向到临时目录（测试期不允许碰真实 models/）。

    教训（2026-09-15，真实事故）：本地引擎删除端点加了「下载态须属于该引擎」的守卫后，
    旧测试构造的下载态缺 engine 字段 → 守卫被绕过 → 测试**真的执行了删除**，把真实
    `models/` 下的 FireRedASR 三件套 + 标点 + VAD（约 1.5GB）删掉了（当时只表现为
    "DID NOT RAISE"，副作用没被注意）。模型目录必须像数据库一样在测试期整体隔离：
    无论守卫怎么变，删除都只作用于临时目录。
    """
    import config as _config
    import services.local_asr as la
    import services.transcriber as tr

    root = TEST_DATA_DIR / "models"
    root.mkdir(parents=True, exist_ok=True)

    saved = []          # (obj, name, 原值 or _MISSING)

    def _redirect(obj, name, value):
        saved.append((obj, name, getattr(obj, name, _MISSING)))
        setattr(obj, name, value)

    # ⚠️ LOCAL_MODEL_DIRS 必须**在原 dict 上就地改**：routers/asr_admin.py 等模块在 import
    # 时就绑定了 config.LOCAL_MODEL_DIRS；替换成新对象会让它们仍指向真实 models/——
    # 曾出现「测试以为在删 tmp 路径、路由却按真实路径删」的隐患（本地用例会 404 假失败）。
    from routers import asr_admin as _asr_admin
    assert tr.LOCAL_MODEL_DIRS is _asr_admin.LOCAL_MODEL_DIRS, (
        'LOCAL_MODEL_DIRS 不能替换成新对象：路由仍指向真实 models/，测试可能删到真模型')
    original_dirs = dict(tr.LOCAL_MODEL_DIRS)
    tr.LOCAL_MODEL_DIRS.clear()
    tr.LOCAL_MODEL_DIRS.update(
        {size: root / f'whisper_{size}' for size in ('tiny', 'base', 'small', 'medium')})

    # Firered 的三个路径同时改 config 与 local_asr：两个模块各自持有一份绑定
    for mod in (_config, la):
        _redirect(mod, 'FIRERED_MODEL_DIR', root / 'firered')
        _redirect(mod, 'FIRERED_PUNCT_MODEL_DIR', root / 'punct')
        _redirect(mod, 'FIRERED_VAD_MODEL_PATH', root / 'vad' / 'silero_vad.onnx')
    # 自检：任何仍指向真实 models/ 的路径都会在会话开始就暴露，而不是等到某个用例删文件
    for label, path in list(tr.LOCAL_MODEL_DIRS.items()) + [
            ('FIRERED_MODEL_DIR', la.FIRERED_MODEL_DIR),
            ('FIRERED_PUNCT_MODEL_DIR', la.FIRERED_PUNCT_MODEL_DIR),
            ('FIRERED_VAD_MODEL_PATH', la.FIRERED_VAD_MODEL_PATH)] + [
            (key, spec.removable[0]()) for key, spec in la.LOCAL_ENGINES.items()]:
        p = Path(path)
        if TEST_DATA_DIR not in p.parents and p != TEST_DATA_DIR:
            raise AssertionError(f'测试期模型路径未隔离：{label} = {p}')
    try:
        yield
    finally:
        for obj, name, value in saved:
            if value is _MISSING:
                delattr(obj, name)      # 原本没有的字段（用户 config.py 未同步新增项）
            else:
                setattr(obj, name, value)
        tr.LOCAL_MODEL_DIRS.clear()
        tr.LOCAL_MODEL_DIRS.update(original_dirs)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """每个测试独立的临时 SQLite：用真实 init_db() 建全量 schema

    替代各仓库测试文件里手抄的 CREATE_TABLES_SQL（四份 DDL 此前反复漂移）。
    连接层面仍由各测试文件的 _patch_conn 把 db.<repo>.get_connection 指向该文件。
    """
    path = tmp_path / "test.db"
    monkeypatch.setattr("db.database.DB_PATH", path)
    from db.database import init_db
    init_db()
    return path
