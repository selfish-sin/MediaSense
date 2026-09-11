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


def test_write_outputs_empty_results_writes_empty_files(tmp_path):
    audio = tmp_path / 'b.mp3'
    audio.write_bytes(b'x')
    srt, txt, count = local_asr._write_outputs(str(audio), [])
    assert count == 0
    assert Path(srt).read_text(encoding='utf-8') == ''
    assert Path(txt).read_text(encoding='utf-8') == ''
