"""
transcriber 纯逻辑函数测试
"""
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from services.transcriber import (
    _model_complete, _select_model, _format_srt_time, _format_mmss,
    _format_duration, _is_cuda_runtime_missing, _get_audio_duration_seconds,
    _resolve_device,
)


class TestModelComplete:
    def test_dir_not_exists(self):
        assert _model_complete(Path("/nonexistent/path")) is False

    def test_model_bin_too_small(self, tmp_path):
        d = tmp_path / "model"
        d.mkdir()
        (d / "model.bin").write_bytes(b"x" * 100)  # too small
        assert _model_complete(d) is False

    def test_no_tokenizer(self, tmp_path):
        d = tmp_path / "model"
        d.mkdir()
        (d / "model.bin").write_bytes(b"x" * 11_000_000)
        assert _model_complete(d) is False

    def test_complete(self, tmp_path):
        d = tmp_path / "model"
        d.mkdir()
        (d / "model.bin").write_bytes(b"x" * 11_000_000)
        (d / "tokenizer.json").write_text("{}")
        assert _model_complete(d) is True


class TestFormatSrtTime:
    def test_zero(self):
        assert _format_srt_time(0) == "00:00:00,000"

    def test_none(self):
        assert _format_srt_time(None) == "00:00:00,000"

    def test_one_minute(self):
        r = _format_srt_time(65.5)
        assert r.startswith("00:01:05,500")


class TestFormatMmss:
    def test_none(self):
        assert _format_mmss(None) == "00:00"

    def test_normal(self):
        assert _format_mmss(75) == "01:15"


class TestFormatDuration:
    def test_none(self):
        assert _format_duration(None) == ""

    def test_seconds(self):
        assert "45" in _format_duration(45)

    def test_minutes(self):
        d = _format_duration(150)
        assert "2分" in d

    def test_hours(self):
        d = _format_duration(3661)
        assert "1小时" in d


class TestIsCudaMissing:
    def test_cublas_not_found(self):
        e = Exception("Library cublas64_12.dll is not found or cannot be loaded")
        assert _is_cuda_runtime_missing(e) is True

    def test_cudart(self):
        e = Exception("cudart64_12.dll not found")
        assert _is_cuda_runtime_missing(e) is True

    def test_cuda_but_not_missing(self):
        e = Exception("CUDA out of memory")
        assert _is_cuda_runtime_missing(e) is False

    def test_no_cuda(self):
        e = Exception("some error")
        assert _is_cuda_runtime_missing(e) is False


class TestGetAudioDuration:
    def test_subprocess_failure(self):
        mock_run = MagicMock(side_effect=Exception("fail"))
        with patch("subprocess.run", mock_run):
            d = _get_audio_duration_seconds("test.mp3")
            assert d is None

    def test_bad_json(self):
        mock_run = MagicMock()
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = b"not json"
        with patch("subprocess.run", mock_run):
            d = _get_audio_duration_seconds("test.mp3")
            assert d is None


class TestResolveDevice:
    """转录设备解析：auto/cuda/cpu 自选 + WHISPER_FORCE_CPU 逃生门"""

    @pytest.fixture(autouse=True)
    def _clear_force_cpu(self):
        # 默认清掉 WHISPER_FORCE_CPU，避免宿主环境变量影响测试确定性
        with patch.dict(os.environ, {"WHISPER_FORCE_CPU": ""}):
            yield

    def _inject_ct2(self, count=1):
        fake = MagicMock()
        fake.get_cuda_device_count.return_value = count
        sys.modules["ctranslate2"] = fake
        return fake

    def _clear_ct2(self):
        sys.modules.pop("ctranslate2", None)

    def test_cpu_skips_cuda_probe(self):
        # 显式 CPU：不探测 CUDA（探测函数不应被调用）
        with patch("services.transcriber._cuda_runtime_usable") as m:
            device, compute_type, reason = _resolve_device("cpu")
        assert device == "cpu"
        assert compute_type == "int8"
        assert reason is None
        m.assert_not_called()

    def test_cuda_available(self):
        self._inject_ct2(count=1)
        try:
            with patch("services.transcriber._cuda_runtime_usable", return_value=True):
                device, compute_type, reason = _resolve_device("cuda")
        finally:
            self._clear_ct2()
        assert device == "cuda"
        assert compute_type == "int8_float16"
        assert reason is None

    def test_cuda_unavailable_falls_back_cpu_with_warning(self):
        self._inject_ct2(count=0)
        calls = []
        try:
            with patch("services.transcriber._cuda_runtime_usable", return_value=False):
                device, compute_type, reason = _resolve_device(
                    "cuda", lambda pct, msg: calls.append((pct, msg))
                )
        finally:
            self._clear_ct2()
        assert device == "cpu"
        assert compute_type == "int8"
        assert reason and "回退" in reason
        # 进度回调可见告警
        assert calls and "回退" in calls[0][1]

    def test_auto_cuda_available(self):
        self._inject_ct2(count=1)
        try:
            with patch("services.transcriber._cuda_runtime_usable", return_value=True):
                device, compute_type, _ = _resolve_device("auto")
        finally:
            self._clear_ct2()
        assert device == "cuda"
        assert compute_type == "int8_float16"

    def test_auto_cpu_when_unavailable(self):
        self._inject_ct2(count=0)
        try:
            with patch("services.transcriber._cuda_runtime_usable", return_value=False):
                device, compute_type, reason = _resolve_device("auto")
        finally:
            self._clear_ct2()
        assert device == "cpu"
        assert compute_type == "int8"
        # auto 回退是静默的（非用户显式选择，无需告警）
        assert reason is None

    def test_invalid_request_falls_back_auto(self):
        self._inject_ct2(count=1)
        try:
            with patch("services.transcriber._cuda_runtime_usable", return_value=True):
                device, _, _ = _resolve_device("gpu")
        finally:
            self._clear_ct2()
        assert device == "cuda"

    def test_force_cpu_env_overrides_cuda_request(self):
        with patch.dict(os.environ, {"WHISPER_FORCE_CPU": "1"}):
            device, compute_type, reason = _resolve_device("cuda")
        assert device == "cpu"
        assert compute_type == "int8"
        assert "WHISPER_FORCE_CPU" in reason

    def test_none_request_treated_as_auto(self):
        self._inject_ct2(count=1)
        try:
            with patch("services.transcriber._cuda_runtime_usable", return_value=True):
                device, _, _ = _resolve_device(None)
        finally:
            self._clear_ct2()
        assert device == "cuda"
