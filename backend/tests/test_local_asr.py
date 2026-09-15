"""
本地 FireRedASR 引擎测试（不触网、不依赖真实模型）

覆盖：模型就绪判定、模型缺失时显式失败（禁止静默吞错）、sherpa-onnx 缺失文案、
VAD padding 边界、SRT/TXT 写盘契约（复用云端句级切分，时间轴单调对齐）。
识别主链路（VAD→识别→标点）依赖 1.1GB 本地模型，由真实冒烟覆盖，不在单测内。
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from services import local_asr
from services.transcriber import TranscriptionCancelledError


# ============ firered_model_ready（完整性判定） ============

@pytest.fixture
def fake_model_dirs(tmp_path, monkeypatch):
    """把模型路径指到临时目录，返回各关键文件路径字典。"""
    model_dir = tmp_path / 'firered'
    punct_dir = tmp_path / 'punct'
    vad_path = tmp_path / 'vad.onnx'
    monkeypatch.setattr(local_asr, 'FIRERED_MODEL_DIR', model_dir)
    monkeypatch.setattr(local_asr, 'FIRERED_PUNCT_MODEL_DIR', punct_dir)
    monkeypatch.setattr(local_asr, 'FIRERED_VAD_MODEL_PATH', vad_path)
    return {
        'encoder': model_dir / 'encoder.int8.onnx',
        'decoder': model_dir / 'decoder.int8.onnx',
        'tokens': model_dir / 'tokens.txt',
        'punct': punct_dir / 'model.onnx',
        'vad': vad_path,
    }


def _make_all_ready(paths):
    for p in paths.values():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'x')


def test_model_ready_false_when_all_missing(fake_model_dirs):
    assert local_asr.firered_model_ready() is False


def test_model_ready_false_when_any_part_missing(fake_model_dirs):
    paths = fake_model_dirs
    _make_all_ready(paths)
    paths['decoder'].unlink()
    assert local_asr.firered_model_ready() is False


def test_model_ready_false_when_file_empty(fake_model_dirs):
    paths = fake_model_dirs
    _make_all_ready(paths)
    paths['tokens'].write_bytes(b'')
    assert local_asr.firered_model_ready() is False


def test_model_ready_true_when_all_present(fake_model_dirs):
    _make_all_ready(fake_model_dirs)
    assert local_asr.firered_model_ready() is True


# ============ 模型缺失时的错误语义（可见可行动） ============

def test_transcribe_fails_loudly_when_model_missing_and_download_fails(fake_model_dirs):
    """模型缺失 + 下载失败：必须显式抛错并给可行动信息，不允许静默回落。"""
    _make_all_ready(fake_model_dirs)
    fake_model_dirs['encoder'].unlink()
    with patch.object(local_asr, '_download_file',
                      side_effect=Exception('下载 FireRedASR 模型失败（ConnectError）')):
        with pytest.raises(Exception) as ei:
            local_asr.transcribe_audio_firered('/nonexistent/audio.wav')
    assert '下载' in str(ei.value)


def test_download_failure_message_contains_manual_hint(fake_model_dirs, tmp_path):
    dest = tmp_path / 'm.tar.bz2'
    with patch('services.local_asr.httpx.Client', side_effect=Exception('network down')):
        with pytest.raises(Exception) as ei:
            local_asr._download_file('https://example.com/m.tar.bz2', dest, '测试模型')
    msg = str(ei.value)
    assert '手动下载' in msg and 'https://example.com/m.tar.bz2' in msg
    assert not list(tmp_path.glob('*.part'))


def test_download_cancelled_raises_cancel_error(tmp_path):
    dest = tmp_path / 'vad.onnx'
    calls = {'n': 0}

    def cancel_check():
        calls['n'] += 1
        return calls['n'] > 1  # 首次放行拿到 stream，第二次取消

    class FakeResp:
        headers = {'content-length': '1024'}

        def raise_for_status(self):
            pass

        def iter_bytes(self, n):
            yield b'x' * 512
            yield b'x' * 512

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    class FakeClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def stream(self, method, url):
            return FakeResp()

    with patch('services.local_asr.httpx.Client', FakeClient):
        with pytest.raises(TranscriptionCancelledError):
            local_asr._download_file('https://example.com/vad.onnx', dest, 'VAD',
                                     cancel_check=cancel_check)
    assert not dest.exists()


# ============ sherpa-onnx 缺失文案 ============

def test_missing_sherpa_onnx_gives_actionable_error(fake_model_dirs, monkeypatch):
    _make_all_ready(fake_model_dirs)
    monkeypatch.setitem(sys.modules, 'sherpa_onnx', None)
    with pytest.raises(Exception) as ei:
        local_asr.transcribe_audio_firered('/nonexistent/audio.wav')
    assert 'sherpa-onnx 未安装' in str(ei.value)
    assert 'pip install sherpa-onnx' in str(ei.value)


# ============ VAD padding 边界 ============

def test_pad_vad_boundaries_adds_padding_and_clips():
    sr = 16000
    raw = [(int(0.5 * sr), int(2.0 * sr)), (int(3.0 * sr), int(4.0 * sr))]
    total = int(4.2 * sr)
    out = local_asr._pad_vad_boundaries(raw, total, sr)
    assert out[0][0] == pytest.approx(0.35, abs=1e-3)   # 0.5 - 0.15
    assert out[0][1] == pytest.approx(2.15, abs=1e-3)   # 2.0 + 0.15
    assert out[1][0] == pytest.approx(2.85, abs=1e-3)
    assert out[1][1] == pytest.approx(4.15, abs=1e-3)


def test_pad_vad_boundaries_clips_to_audio_bounds():
    sr = 16000
    raw = [(0, int(0.05 * sr)), (int(4.1 * sr), int(4.2 * sr))]
    total = int(4.2 * sr)
    out = local_asr._pad_vad_boundaries(raw, total, sr)
    assert out[0][0] == 0.0
    assert out[1][1] == pytest.approx(4.2, abs=1e-3)


def test_pad_vad_boundaries_drops_empty_range():
    sr = 16000
    out = local_asr._pad_vad_boundaries([(int(1.0 * sr), int(1.0 * sr))], int(2.0 * sr), sr)
    assert out == []


# ============ SRT/TXT 写盘契约（句级化复用云端切分） ============

def test_write_outputs_sentence_level_monotonic(tmp_path):
    audio = tmp_path / 'a.mp3'
    audio.write_bytes(b'x')
    results = [(0.0, 10.0, '第一句话在这里。第二句也在这里！第三句没有句末标号')]
    srt, txt, count = local_asr._write_outputs(str(audio), results)
    assert count == 3
    content = Path(srt).read_text(encoding='utf-8')
    assert content.count('-->') == 3
    # 时间轴单调：每条 start < end，且逐条递增
    blocks = [b for b in content.strip().split('\n\n') if b.strip()]
    prev_end = -1.0
    for b in blocks:
        lines = b.split('\n')
        assert lines[0].strip().isdigit()
        t = lines[1]
        assert '-->' in t
        start_s = t.split('-->')[0].strip()
        end_s = t.split('-->')[1].strip()

        def to_sec(ts):
            hms, ms = ts.split(',')
            h, m, s = hms.split(':')
            return int(h) * 3600 + int(m) * 60 + float(s) + int(ms) / 1000

        assert to_sec(start_s) < to_sec(end_s)
        assert to_sec(start_s) >= prev_end
        prev_end = to_sec(end_s)
    txt_lines = Path(txt).read_text(encoding='utf-8').strip().split('\n')
    assert len(txt_lines) == 3
    assert all(l.startswith('[') and ' - ' in l for l in txt_lines)


# ============ 引擎注册表（新增通道：通用生命周期） ============

def _fake_spec(root):
    """构造一个挂在临时目录上的假引擎规格，用于验证通用机制（不碰真实模型）"""
    return local_asr.LocalEngine(
        key='fake', name='测试引擎', languages='测试语言', note='测试用',
        parts=(
            local_asr.EnginePart('a', '文件A', local_asr._abs(lambda: root / 'a.onnx')),
            local_asr.EnginePart('b', '嵌套/文件B', local_asr._abs(lambda: root / 'sub/b.onnx')),
        ),
        removable=(lambda: root,),
        archives=(local_asr.EngineArchive(('https://example.com/fake.tar.bz2',),
                                          '假模型包', lambda: root),),
        size_hint='约 1MB',
    )


@pytest.fixture
def fake_engine(tmp_path, monkeypatch):
    """注册一个临时引擎（含锁），用完自动移除"""
    root = tmp_path / 'fake_engine'
    spec = _fake_spec(root)
    monkeypatch.setitem(local_asr.LOCAL_ENGINES, 'fake', spec)
    monkeypatch.setitem(local_asr._ENGINE_LOCKS, 'fake', __import__('threading').Lock())
    monkeypatch.setattr(local_asr, '_download_state',
                        {'status': 'idle', 'engine': '', 'percent': 0, 'message': '', 'error': ''})
    return root


class TestEngineRegistry:
    def test_registry_declares_expected_engines(self):
        """本地档只有 FireRed（LLM 类引擎 2026-09-15 实机横评后放弃，见模块 docstring）"""
        assert set(local_asr.LOCAL_ENGINE_KEYS) == {'firered'}
        for key in local_asr.LOCAL_ENGINE_KEYS:
            spec = local_asr.LOCAL_ENGINES[key]
            assert spec.name and spec.parts and spec.removable
            assert spec.fee == '免费'          # 本地引擎一律免费（前端只显示不推断）
            assert spec.archives               # 每个引擎都要有可下载来源（否则管理页无法补模型）

    def test_engine_lock_unknown_key_raises(self):
        with pytest.raises(KeyError):
            local_asr.engine_lock('ghost')

    def test_ready_requires_every_part_non_empty(self, fake_engine):
        root = fake_engine
        assert local_asr.engine_ready('fake') is False

        root.mkdir(parents=True)
        (root / 'a.onnx').write_bytes(b'x')
        assert local_asr.engine_ready('fake') is False       # 还缺嵌套文件

        (root / 'sub').mkdir()
        (root / 'sub' / 'b.onnx').write_bytes(b'x')
        assert local_asr.engine_ready('fake') is True

        (root / 'a.onnx').write_bytes(b'')                   # 空文件必须判未就绪
        assert local_asr.engine_ready('fake') is False

    def test_engine_status_shape(self, fake_engine):
        status = local_asr.engine_status('fake')
        assert status['key'] == 'fake' and status['fee'] == '免费'
        assert status['ready'] is False and status['busy'] is False
        assert status['bytes'] == 0 and status['dir'] == str(fake_engine)
        assert [p['key'] for p in status['parts']] == ['a', 'b']
        # 缺失时展示「期望路径」（前端要能告诉用户该放哪）
        assert status['parts'][1]['path'].endswith('sub\\b.onnx') or \
            status['parts'][1]['path'].endswith('sub/b.onnx')
        assert '手动放置' in status['manual_hint']
        assert status['download']['status'] == 'idle'

    def test_engine_status_bytes_counts_whole_dir(self, fake_engine):
        fake_engine.mkdir(parents=True)
        (fake_engine / 'a.onnx').write_bytes(b'x' * 100)
        (fake_engine / 'junk.bin').write_bytes(b'y' * 900)   # 非关键文件也计入占用（删了会释放）
        assert local_asr.engine_status('fake')['bytes'] == 1000

    def test_busy_reflects_lock(self, fake_engine):
        lock = local_asr.engine_lock('fake')
        assert local_asr.engine_busy('fake') is False
        with lock:
            assert local_asr.engine_busy('fake') is True
        assert local_asr.engine_busy('fake') is False

    def test_remove_engine_frees_and_resets(self, fake_engine):
        fake_engine.mkdir(parents=True)
        (fake_engine / 'a.onnx').write_bytes(b'x' * 512)
        result = local_asr.remove_engine('fake')
        assert result['freed_bytes'] == 512
        assert result['ready'] is False
        assert not fake_engine.exists()

    def test_remove_engine_refused_while_busy(self, fake_engine):
        lock = local_asr.engine_lock('fake')
        with lock:
            with pytest.raises(Exception) as ei:
                local_asr.remove_engine('fake')
        assert '转录中' in str(ei.value)

    def test_remove_engine_refused_while_downloading(self, fake_engine, monkeypatch):
        monkeypatch.setattr(local_asr, '_download_state',
                            {'status': 'downloading', 'engine': 'fake', 'percent': 5,
                             'message': '', 'error': ''})
        with pytest.raises(Exception) as ei:
            local_asr.remove_engine('fake')
        assert '下载中' in str(ei.value)
        # 别的引擎在下载 → 不拦本引擎的删除
        monkeypatch.setattr(local_asr, '_download_state',
                            {'status': 'downloading', 'engine': 'other', 'percent': 5,
                             'message': '', 'error': ''})
        assert local_asr.remove_engine('fake')['freed_bytes'] == 0

    def test_ensure_ready_skips_when_ready(self, fake_engine):
        fake_engine.mkdir(parents=True)
        (fake_engine / 'a.onnx').write_bytes(b'x')
        (fake_engine / 'sub').mkdir()
        (fake_engine / 'sub' / 'b.onnx').write_bytes(b'x')
        calls = []
        with patch.object(local_asr, '_download_file', side_effect=lambda *a, **k: calls.append(a)):
            local_asr._ensure_engine_ready('fake')
        assert calls == []            # 已就绪 → 零开销直接返回

    def test_ensure_ready_downloads_and_extracts(self, fake_engine, tmp_path):
        def fake_download(urls, dest, *a, **k):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b'tar')

        def fake_extract(archive, expected_dir, label):
            (expected_dir / 'sub').mkdir(parents=True, exist_ok=True)
            (expected_dir / 'a.onnx').write_bytes(b'x')
            (expected_dir / 'sub' / 'b.onnx').write_bytes(b'x')

        progress = []
        with patch.object(local_asr, '_download_file', fake_download), \
             patch.object(local_asr, '_extract_archive', fake_extract):
            local_asr._ensure_engine_ready('fake', progress_callback=lambda p, m: progress.append(m))
        assert local_asr.engine_ready('fake') is True
        assert any('开始按需下载' in m for m in progress)

    def test_ensure_ready_refuses_incomplete_existing_dir(self, fake_engine):
        """目录在但残缺：不擅自覆盖（可能是用户手动放置的大版本/第三方导出）"""
        fake_engine.mkdir(parents=True)
        (fake_engine / 'a.onnx').write_bytes(b'x')      # 只有一部分文件
        with pytest.raises(Exception) as ei:
            local_asr._ensure_engine_ready('fake')
        msg = str(ei.value)
        assert '已存在但关键文件不完整' in msg and '请先删除该目录' in msg

    def test_ensure_ready_reports_missing_parts_on_incomplete_download(self, fake_engine):
        """下载/解包跑完仍不完整 → 抛出缺失清单（禁止假就绪）"""
        with patch.object(local_asr, '_download_file', lambda *a, **k: None), \
             patch.object(local_asr, '_extract_archive', lambda *a, **k: None):
            with pytest.raises(Exception) as ei:
                local_asr._ensure_engine_ready('fake')
        msg = str(ei.value)
        assert '完整性校验未通过' in msg and '文件A' in msg

    def test_start_engine_download_single_flight(self, fake_engine, monkeypatch):
        monkeypatch.setattr(local_asr, '_download_state',
                            {'status': 'downloading', 'engine': 'other', 'percent': 5,
                             'message': '', 'error': ''})
        assert local_asr.start_engine_download('fake') is False   # 同时只允许一个下载

    def test_start_engine_download_unknown_key(self):
        with pytest.raises(KeyError):
            local_asr.start_engine_download('ghost')


# ============ 引擎分发入口 ============

class TestEngineDispatch:
    def test_transcribe_local_routes_firered_to_classic_pipeline(self, monkeypatch):
        calls = []
        monkeypatch.setattr(local_asr, 'transcribe_audio_firered',
                            lambda path, **kw: (calls.append(path), ('a.srt', 'a.txt', 1))[1])
        assert local_asr.transcribe_audio_local('firered', 'x.wav') == ('a.srt', 'a.txt', 1)
        assert calls == ['x.wav']

    def test_transcribe_local_unknown_engine(self):
        with pytest.raises(KeyError):
            local_asr.transcribe_audio_local('ghost', 'x.wav')


def test_write_outputs_empty_results_writes_empty_files(tmp_path):
    audio = tmp_path / 'b.mp3'
    audio.write_bytes(b'x')
    srt, txt, count = local_asr._write_outputs(str(audio), [])
    assert count == 0
    assert Path(srt).read_text(encoding='utf-8') == ''
    assert Path(txt).read_text(encoding='utf-8') == ''
