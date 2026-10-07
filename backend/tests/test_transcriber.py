"""
transcriber 纯逻辑函数测试
"""
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from services.transcriber import (
    _model_complete, _format_srt_time, _format_mmss,
    _format_duration, _is_cuda_runtime_missing, _get_audio_duration_seconds,
    _resolve_device, _transcribe_sync, _fallback_chain,
    ENGINE_VALUES, engine_label,
    TranscriptionCancelledError,
)


@pytest.fixture(autouse=True)
def _config_only_providers(monkeypatch):
    """引擎路由测试固定槽位 → provider 映射，不依赖本机 DB/config 里的 provider 启停状态。"""
    monkeypatch.setattr('db.asr_repo.get_provider', lambda key: None)
    monkeypatch.setattr('db.asr_repo.get_provider_for_engine',
                        lambda engine: {'cloud': 'mimo', 'cloud_free': 'xingchen'}.get(engine))


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


class TestEngineRouting:
    """_transcribe_sync 引擎路由（回退链）：cloud→cloud_free→firered→local / 取消不回退 / local 直达"""

    def _patch_cloud(self, monkeypatch, configured=True, cloud_result=None, cloud_exc=None,
                     free_result=None, free_exc=None):
        """注入云端替身：返回 cloud_asr.transcribe_audio_cloud 的 mock。

        configured: bool（所有 provider 同值）或 {'mimo': bool, 'xingchen': bool}；
        cloud_* / free_* 分别控制 mimo / xingchen 的返回或异常。"""
        import services.cloud_asr as ca
        if isinstance(configured, dict):
            monkeypatch.setattr(ca, 'cloud_asr_configured',
                                lambda provider='mimo': configured.get(provider, False))
        else:
            monkeypatch.setattr(ca, 'cloud_asr_configured', lambda provider='mimo': configured)

        def _dispatch(audio_path, provider='mimo', **kwargs):
            if provider == 'xingchen':
                if free_exc is not None:
                    raise free_exc
                return free_result or ('free.srt', 'free.txt', 3)
            if cloud_exc is not None:
                raise cloud_exc
            return cloud_result or ('fake.srt', 'fake.txt', 3)

        m = MagicMock(side_effect=_dispatch)
        monkeypatch.setattr(ca, 'transcribe_audio_cloud', m)
        return m

    def _patch_firered_ready(self, monkeypatch, ready=False):
        """firered 就绪态 + 转录替身。

        就绪态走注册表单点 `engine_ready`（`firered_model_ready` 只是它的兼容别名）——
        回退链是否收录 firered 由它决定，不再由 firered_model_ready 决定。
        """
        import services.local_asr as la
        monkeypatch.setattr(la, 'engine_ready', lambda key: ready if key == 'firered' else False)
        monkeypatch.setattr(la, 'firered_model_ready', lambda: ready)
        m = MagicMock()
        m.return_value = ('f.srt', 'f.txt', 4)
        monkeypatch.setattr(la, 'transcribe_audio_firered', m)
        return m

    def test_cloud_success_short_circuits_whisper(self, monkeypatch):
        m = self._patch_cloud(monkeypatch, configured=True,
                              cloud_result=('out.srt', 'out.txt', 5))
        self._patch_firered_ready(monkeypatch, ready=True)
        with patch('services.transcriber._transcribe_sync_single') as w:
            result = _transcribe_sync('a.wav', engine='cloud')
        assert result == ('out.srt', 'out.txt', 5)
        m.assert_called_once()
        w.assert_not_called()

    def test_auto_uses_cloud_when_configured(self, monkeypatch):
        m = self._patch_cloud(monkeypatch, configured=True)
        _transcribe_sync('a.wav', engine='auto')
        m.assert_called_once()

    def test_local_never_touches_cloud(self, monkeypatch):
        m = self._patch_cloud(monkeypatch, configured=True)
        with patch('services.transcriber._get_audio_duration_seconds', return_value=None), \
             patch('services.transcriber._transcribe_sync_single', return_value=('l.srt', 'l.txt', 1)) as w:
            result = _transcribe_sync('a.wav', engine='local')
        m.assert_not_called()
        w.assert_called_once()
        assert result == ('l.srt', 'l.txt', 1)

    def test_cloud_failure_falls_back_to_whisper(self, monkeypatch):
        """mimo 失败且免费档未配置、firered 未就绪时，直接回退 Whisper（不触发模型下载）。"""
        self._patch_cloud(monkeypatch, configured={'mimo': True, 'xingchen': False},
                          cloud_exc=Exception('云端 ASR 重试 3 次后仍失败: HTTP 503'))
        self._patch_firered_ready(monkeypatch, ready=False)
        progress = []
        with patch('services.transcriber._get_audio_duration_seconds', return_value=None), \
             patch('services.transcriber._transcribe_sync_single', return_value=('l.srt', 'l.txt', 2)) as w:
            result = _transcribe_sync(
                'a.wav', engine='cloud',
                progress_callback=lambda pct, msg: progress.append((pct, msg)))
        w.assert_called_once()
        assert result == ('l.srt', 'l.txt', 2)
        # 回退原因在进度里可见（错误必须可行动）
        assert any('云端 MiMo 转录失败' in msg and '503' in msg for _pct, msg in progress)
        assert any('回退 本地 Whisper' in msg for _pct, msg in progress)

    def test_cloud_failure_falls_back_to_free_cloud(self, monkeypatch):
        """mimo（付费）失败 → 先落免费档 XingChen，成功即短路，不碰本地。"""
        m = self._patch_cloud(monkeypatch, configured={'mimo': True, 'xingchen': True},
                              cloud_exc=Exception('HTTP 503'),
                              free_result=('free.srt', 'free.txt', 3))
        self._patch_firered_ready(monkeypatch, ready=False)
        progress = []
        with patch('services.transcriber._transcribe_sync_single') as w:
            result = _transcribe_sync(
                'a.wav', engine='cloud',
                progress_callback=lambda pct, msg: progress.append((pct, msg)))
        assert result == ('free.srt', 'free.txt', 3)
        w.assert_not_called()
        # 免费档是被主动回退到的（成本只降不升），且回退原因可见
        assert any(call.kwargs.get('provider') == 'xingchen' for call in m.call_args_list)
        assert any('云端 MiMo 转录失败' in msg for _pct, msg in progress)

    def test_cloud_free_never_falls_back_to_paid(self, monkeypatch):
        """用户选免费档失败 → firered(未就绪跳过) → Whisper；绝不转付费 MiMo。"""
        m = self._patch_cloud(monkeypatch, configured={'mimo': True, 'xingchen': True},
                              cloud_exc=Exception('HTTP 500'),
                              free_exc=Exception('云端 ASR 重试 3 次后仍失败: HTTP 503'))
        self._patch_firered_ready(monkeypatch, ready=False)
        with patch('services.transcriber._get_audio_duration_seconds', return_value=None), \
             patch('services.transcriber._transcribe_sync_single', return_value=('l.srt', 'l.txt', 1)) as w:
            result = _transcribe_sync('a.wav', engine='cloud_free')
        assert result == ('l.srt', 'l.txt', 1)
        w.assert_called_once()
        assert all(call.kwargs.get('provider') != 'mimo' for call in m.call_args_list)

    def test_cloud_failure_falls_back_to_firered_when_ready(self, monkeypatch):
        """mimo 与免费档都失败、firered 模型就绪时，进 firered；成功即短路。"""
        self._patch_cloud(monkeypatch, configured={'mimo': True, 'xingchen': True},
                          cloud_exc=Exception('HTTP 503'), free_exc=Exception('HTTP 503'))
        f = self._patch_firered_ready(monkeypatch, ready=True)
        progress = []
        with patch('services.transcriber._transcribe_sync_single') as w:
            result = _transcribe_sync(
                'a.wav', engine='cloud',
                progress_callback=lambda pct, msg: progress.append((pct, msg)))
        assert result == ('f.srt', 'f.txt', 4)
        f.assert_called_once()  # firered 参与了回退并成功
        w.assert_not_called()
        # 每一级回退原因可见（MiMo→XingChen→FireRedASR）
        assert any('云端 MiMo 转录失败' in msg for _pct, msg in progress)
        assert any('回退 本地 FireRedASR' in msg for _pct, msg in progress)

    def test_firered_fallback_never_downloads_model(self, monkeypatch):
        """模型未就绪时回退链跳过 firered：绝不把 1.1GB 下载当兜底副作用。"""
        self._patch_cloud(monkeypatch, configured={'mimo': True, 'xingchen': False},
                          cloud_exc=Exception('503'))
        m = self._patch_firered_ready(monkeypatch, ready=False)
        with patch('services.transcriber._get_audio_duration_seconds', return_value=None), \
             patch('services.transcriber._transcribe_sync_single', return_value=('l.srt', 'l.txt', 1)):
            _transcribe_sync('a.wav', engine='cloud')
        m.assert_not_called()

    def test_cloud_not_configured_warns_and_uses_local(self, monkeypatch):
        self._patch_cloud(monkeypatch, configured=False)
        self._patch_firered_ready(monkeypatch, ready=False)
        progress = []
        with patch('services.transcriber._get_audio_duration_seconds', return_value=None), \
             patch('services.transcriber._transcribe_sync_single', return_value=('l.srt', 'l.txt', 1)) as w:
            _transcribe_sync('a.wav', engine='cloud',
                             progress_callback=lambda pct, msg: progress.append((pct, msg)))
        w.assert_called_once()
        assert any('未配置' in msg for _pct, msg in progress)

    def test_cancel_not_swallowed_by_fallback(self, monkeypatch):
        # 用户取消必须向上传播，不能被「回退本地」吃掉
        self._patch_cloud(monkeypatch, configured=True,
                          cloud_exc=TranscriptionCancelledError('用户取消，转录中止'))
        self._patch_firered_ready(monkeypatch, ready=True)
        with patch('services.transcriber._transcribe_sync_single') as w:
            with pytest.raises(TranscriptionCancelledError):
                _transcribe_sync('a.wav', engine='cloud')
        w.assert_not_called()

    def test_invalid_engine_treated_as_auto(self, monkeypatch):
        m = self._patch_cloud(monkeypatch, configured=True)
        _transcribe_sync('a.wav', engine='bogus')
        m.assert_called_once()

    def test_cloud_free_routes_to_xingchen_provider(self, monkeypatch):
        """cloud_free（免费档）走 xingchen provider，成功即短路。"""
        import services.cloud_asr as ca
        monkeypatch.setattr(ca, 'cloud_asr_configured',
                            lambda provider='mimo': provider == 'xingchen')
        m = MagicMock()
        m.return_value = ('free.srt', 'free.txt', 2)
        monkeypatch.setattr(ca, 'transcribe_audio_cloud', m)
        with patch('services.transcriber._transcribe_sync_single') as w:
            result = _transcribe_sync('a.wav', engine='cloud_free')
        assert result == ('free.srt', 'free.txt', 2)
        m.assert_called_once()
        assert m.call_args.kwargs.get('provider') == 'xingchen'
        w.assert_not_called()

    # ── firered（本地 FireRedASR2-AED）路由 ──

    def test_firered_success_short_circuits_whisper(self, monkeypatch):
        self._patch_cloud(monkeypatch, configured=True)
        m = self._patch_firered_ready(monkeypatch, ready=True)
        m.return_value = ('f.srt', 'f.txt', 4)
        with patch('services.transcriber._transcribe_sync_single') as w:
            result = _transcribe_sync('a.wav', engine='firered')
        assert result == ('f.srt', 'f.txt', 4)
        m.assert_called_once()
        w.assert_not_called()

    def test_firered_failure_falls_back_to_whisper(self, monkeypatch):
        self._patch_cloud(monkeypatch, configured=True)
        f = self._patch_firered_ready(monkeypatch, ready=True)
        f.side_effect = Exception('下载 FireRedASR 模型失败')
        progress = []
        with patch('services.transcriber._get_audio_duration_seconds', return_value=None), \
             patch('services.transcriber._transcribe_sync_single', return_value=('l.srt', 'l.txt', 2)) as w:
            result = _transcribe_sync(
                'a.wav', engine='firered',
                progress_callback=lambda pct, msg: progress.append((pct, msg)))
        w.assert_called_once()
        assert result == ('l.srt', 'l.txt', 2)
        # 回退原因在进度里可见（错误必须可行动）
        assert any('FireRedASR 转录失败' in msg for _pct, msg in progress)

    def test_firered_cancel_not_swallowed_by_fallback(self, monkeypatch):
        f = self._patch_firered_ready(monkeypatch, ready=True)
        f.side_effect = TranscriptionCancelledError('用户取消，转录中止')
        with patch('services.transcriber._transcribe_sync_single') as w:
            with pytest.raises(TranscriptionCancelledError):
                _transcribe_sync('a.wav', engine='firered')
        w.assert_not_called()


class TestWriteCleanText:
    """纯净文本生成：剥时间戳前缀与说话人标记，空行丢弃，行序保留。"""

    def test_strips_timestamps_and_speakers(self, tmp_path):
        from services.transcriber import write_clean_text
        txt = tmp_path / 'a.txt'
        txt.write_text(
            '[00:00 - 00:06] 大家好，今天讲经络。\n'
            '[00:06 - 00:17] 从银马尔可夫到深度学习。[说话人1]\n'
            '\n'
            '[01:02:03 - 01:02:10] 长音频小时级时间戳。\n',
            encoding='utf-8')
        clean_path = write_clean_text(str(txt))
        assert clean_path is not None
        assert Path(clean_path).read_text(encoding='utf-8') == (
            '大家好，今天讲经络。\n'
            '从银马尔可夫到深度学习。\n'
            '长音频小时级时间戳。\n'
        )

    def test_missing_file_returns_none(self, tmp_path):
        from services.transcriber import write_clean_text
        assert write_clean_text(str(tmp_path / 'nope.txt')) is None

    def test_empty_content_returns_none(self, tmp_path):
        from services.transcriber import write_clean_text
        txt = tmp_path / 'b.txt'
        txt.write_text('[00:00 - 00:06] [说话人1]\n', encoding='utf-8')
        assert write_clean_text(str(txt)) is None


class TestEngineLabel:
    """首条初始化日志/进度的引擎标签必须来自 _ENGINE_LABELS 单一事实源。

    回归：代码曾用三态临时 dict（缺 cloud_free），选免费档时首条日志误标"云端 MiMo"。
    """

    @pytest.mark.parametrize('engine,expected', [
        ('cloud', '云端 MiMo'),
        ('cloud_free', '云端 XingChen（免费）'),
        ('firered', '本地 FireRedASR'),
        ('local', '本地 Whisper'),
    ])
    async def test_initial_log_matches_engine_labels(self, monkeypatch, engine, expected):
        from services import transcriber
        monkeypatch.setattr(transcriber, '_transcribe_sync', lambda **kw: ('a.srt', 'a.txt', 1))
        with patch('socket_handlers.emit_progress', new_callable=AsyncMock) as mep, \
             patch('socket_handlers.emit_log', new_callable=AsyncMock) as mel:
            await transcriber.transcribe_audio('a.wav', engine=engine, sio=MagicMock(), job_id='j1')
        prog_msgs = ' '.join(str(c.args[4]) for c in mep.call_args_list)
        log_msgs = ' '.join(str(c.args[2]) for c in mel.call_args_list)
        assert expected in prog_msgs
        assert expected in log_msgs


# ============ 引擎值与回退链（新增本地引擎的语义边界） ============

class TestEngineValuesAndChains:
    def test_engine_values_single_source(self):
        assert set(ENGINE_VALUES) == {'cloud', 'cloud_free', 'firered', 'local'}

    def test_every_engine_value_has_label(self):
        for engine in ENGINE_VALUES:
            assert engine_label(engine)
        assert engine_label('bogus') == '云端 MiMo'      # 未知值仍按 cloud（旧行为不变）

    def test_user_selected_local_engine_chain_is_kept_verbatim(self):
        """用户显式选的本地引擎永远在链首（显式选择才允许触发按需下载）。"""
        assert _fallback_chain('firered') == ('firered', 'local')

    def test_cloud_chain_drops_unready_local_engines(self, monkeypatch):
        """本地 ONNX 引擎未就绪 → 从兜底链剔除（下载绝不作为兜底副作用）。"""
        import services.local_asr as la
        monkeypatch.setattr(la, 'engine_ready', lambda key: False)
        assert _fallback_chain('cloud') == ('cloud', 'cloud_free', 'local')
        assert _fallback_chain('cloud_free') == ('cloud_free', 'local')

    def test_cloud_chain_keeps_ready_firered(self, monkeypatch):
        import services.local_asr as la
        monkeypatch.setattr(la, 'engine_ready', lambda key: key == 'firered')
        assert _fallback_chain('cloud') == ('cloud', 'cloud_free', 'firered', 'local')

    def test_local_whisper_is_always_chain_tail(self):
        for engine in ENGINE_VALUES:
            assert _fallback_chain(engine)[-1] == 'local'

    def test_unknown_engine_falls_back_to_free_chain_not_paid(self, monkeypatch):
        """未知引擎值 → 落**免费档**链，绝不回付费档 cloud（历史泄漏回归锁）。"""
        import services.local_asr as la
        monkeypatch.setattr(la, 'engine_ready', lambda key: False)
        assert _fallback_chain('bogus') == _fallback_chain('cloud_free')
        assert 'cloud' not in _fallback_chain('bogus')

    def test_default_engine_argument_is_free_not_paid(self):
        """`_transcribe_sync` / `transcribe_audio` 的 engine 缺省值是免费档（不是付费 cloud）。"""
        import inspect
        from services import transcriber
        for fn in (transcriber._transcribe_sync, transcriber.transcribe_audio):
            assert inspect.signature(fn).parameters['engine'].default == 'cloud_free'
