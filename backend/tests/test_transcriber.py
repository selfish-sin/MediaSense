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


# ============ 结果可解释性：info 出参（2026-10-07） ============

class TestTranscribeInfoOutParam:
    """transcribe_audio 的 info 出参：实际引擎/缓存命中/降级标记回填。"""

    async def test_info_records_actual_engine_and_cache_miss(self, monkeypatch):
        from services import transcriber
        monkeypatch.setattr(transcriber, '_transcribe_sync', lambda **kw: ('a.srt', 'a.txt', 1))
        info = {}
        await transcriber.transcribe_audio('a.wav', engine='cloud_free', info=info)

        assert info['engine'] == 'cloud_free'
        assert info['requested_engine'] == 'cloud_free'
        assert info['engine_label'] == '云端 XingChen（免费）'
        assert info['cache'] == 'miss'
        assert info['degraded'] is False
        assert info['model_size'] is None

    async def test_info_records_degradation_and_local_model_size(self, monkeypatch):
        from services import transcriber

        def fake_sync(**kw):
            kw['execution_state']['engine'] = 'local'
            return ('a.srt', 'a.txt', 1)

        monkeypatch.setattr(transcriber, '_transcribe_sync', fake_sync)
        info = {}
        await transcriber.transcribe_audio('a.wav', engine='cloud_free', model_size='medium', info=info)

        assert info['engine'] == 'local'
        assert info['requested_engine'] == 'cloud_free'
        assert info['degraded'] is True
        assert info['model_size'] == 'medium'

    async def test_info_records_cache_hit(self, monkeypatch, tmp_path):
        from services import transcriber, ai_cache
        # conftest 会话级 fixture 默认关缓存（防跨用例污染），本用例显式打开
        monkeypatch.setattr('config.AI_CACHE_ENABLED', True)
        audio = tmp_path / 'a.wav'
        audio.write_bytes(b'x' * 2048)
        monkeypatch.setattr(ai_cache, 'lookup_transcription', lambda *a, **kw: {
            'srt_path': 'c.srt', 'txt_path': 'c.txt', 'seg_count': 5,
            'engine': 'cloud_free', 'created_at': '2026-10-07 10:00:00',
        })
        info = {}
        result = await transcriber.transcribe_audio(str(audio), engine='cloud_free', info=info)

        assert result == ('c.srt', 'c.txt', 5)
        assert info['cache'] == 'hit'
        assert info['engine'] == 'cloud_free'
        assert info['degraded'] is False

    async def test_info_records_bypassed_cache(self, monkeypatch):
        from services import transcriber, ai_cache
        monkeypatch.setattr(transcriber, '_transcribe_sync', lambda **kw: ('a.srt', 'a.txt', 1))
        monkeypatch.setattr(ai_cache, 'store_transcription', lambda *a, **kw: True)
        info = {}
        await transcriber.transcribe_audio('a.wav', engine='cloud_free', bypass_cache=True, info=info)

        assert info['cache'] == 'bypassed'

    async def test_no_info_arg_keeps_old_behavior(self, monkeypatch):
        """不传 info 时行为与旧版完全一致（返回三元组，无副作用）。"""
        from services import transcriber
        monkeypatch.setattr(transcriber, '_transcribe_sync', lambda **kw: ('a.srt', 'a.txt', 1))
        result = await transcriber.transcribe_audio('a.wav', engine='cloud_free')

        assert result == ('a.srt', 'a.txt', 1)


# ============ 长音频分片检查点（2026-10-07 断点恢复） ============

class _FakeSeg:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


class TestChunkCheckpoints:
    """分片检查点：失败恢复只重跑缺失片；bypass_cache 不读；完成后清理。"""

    def _patch_chunked_env(self, monkeypatch, tmp_path, chunks):
        """把 _transcribe_chunked 的外部依赖钉到临时目录与可注入的假模型。"""
        import services.transcriber as tr

        audio = tmp_path / 'long_video.m4a'
        audio.write_bytes(b'audio')

        monkeypatch.setattr(tr, '_split_audio_chunks', lambda path, secs: chunks)
        monkeypatch.setattr(tr, '_select_model', lambda size, plat: (str(tmp_path / 'model'), 'base', True))
        monkeypatch.setattr(tr, '_resolve_device', lambda device, cb=None: ('cpu', 'int8', ''))
        monkeypatch.setattr(tr, '_get_audio_duration_seconds', lambda p: 900.0)
        return tr, audio

    def _fake_model(self, transcribed, segments_per_chunk, fail_on=None):
        """假 faster-whisper 模型：记录被转录的 chunk，可在指定 chunk 上抛错。"""

        class FakeModel:
            def transcribe(self, chunk_path, language=None, beam_size=5, vad_filter=True):
                transcribed.append(chunk_path)
                idx = int(chunk_path.split('_chunk')[-1].split('.')[0])
                if fail_on is not None and idx == fail_on:
                    raise RuntimeError(f'chunk {idx} boom')
                segs = [_FakeSeg(j * 2.0, j * 2.0 + 2.0, f'第{idx}片第{j}句')
                        for j in range(segments_per_chunk)]
                return iter(segs), {}

        return FakeModel()

    def test_resume_skips_completed_chunks_and_cleans_up(self, monkeypatch, tmp_path):
        chunk_files = []
        for i in range(3):
            c = tmp_path / f'long_video_chunk{i:03d}.wav'
            c.write_bytes(b'x' * 1024)
            chunk_files.append((str(c), float(i * 300)))

        tr, audio = self._patch_chunked_env(monkeypatch, tmp_path, chunk_files)

        # 第一轮：前两片成功，第三片失败 → 任务失败，但前两片检查点已落盘
        first_calls = []
        model1 = self._fake_model(first_calls, segments_per_chunk=3, fail_on=2)
        monkeypatch.setattr(tr, '_get_whisper_model', lambda *a, **kw: model1)
        with pytest.raises(RuntimeError, match='chunk 2 boom'):
            tr._transcribe_chunked(str(audio), 'base', '', False, 'zh', None)

        cps = sorted(tmp_path.glob('long_video_chunk*.transcript.json'))
        assert len(cps) == 2                      # 失败时已完成片有检查点
        assert len(first_calls) == 3              # 第三片有尝试但未成功（无检查点）

        # 第二轮（恢复）：只重跑第三片，合并结果完整，检查点清理干净
        second_calls = []
        model2 = self._fake_model(second_calls, segments_per_chunk=3)
        monkeypatch.setattr(tr, '_get_whisper_model', lambda *a, **kw: model2)
        srt_path, txt_path, total = tr._transcribe_chunked(str(audio), 'base', '', False, 'zh', None)

        assert len(second_calls) == 1             # 只补跑了缺失片
        assert 'chunk002' in second_calls[0]
        assert total == 9                         # 3 片 × 3 段，序号连续不重不漏
        assert not list(tmp_path.glob('long_video_chunk*.transcript.json'))
        # 合并 SRT 覆盖全部片的全部段，且时间戳带偏移
        srt_text = Path(srt_path).read_text(encoding='utf-8')
        assert '第0片第0句' in srt_text and '第2片第2句' in srt_text
        txt_text = Path(txt_path).read_text(encoding='utf-8')
        assert '[10:00' in txt_text               # 600s 偏移进入第 3 片

    def test_bypass_cache_ignores_checkpoints_but_writes(self, monkeypatch, tmp_path):
        import services.transcriber as tr

        chunk_files = []
        for i in range(2):
            c = tmp_path / f'long_video_chunk{i:03d}.wav'
            c.write_bytes(b'x' * 1024)
            chunk_files.append((str(c), float(i * 300)))
        # 预置全部检查点
        for path, offset in chunk_files:
            tr._store_chunk_checkpoint(path, offset, [{'start': 0.0, 'end': 2.0, 'text': '旧结果'}])

        tr, audio = self._patch_chunked_env(monkeypatch, tmp_path, chunk_files)
        calls = []
        model = self._fake_model(calls, segments_per_chunk=1)
        monkeypatch.setattr(tr, '_get_whisper_model', lambda *a, **kw: model)
        srt_path, _txt, total = tr._transcribe_chunked(
            str(audio), 'base', '', False, 'zh', None, bypass_cache=True)

        assert len(calls) == 2                    # 全部重跑，不读检查点
        assert total == 2
        assert '旧结果' not in Path(srt_path).read_text(encoding='utf-8')
        assert not list(tmp_path.glob('long_video_chunk*.transcript.json'))  # 完成后照常清理

    def test_checkpoint_offset_mismatch_is_ignored(self, monkeypatch, tmp_path):
        import services.transcriber as tr

        c = tmp_path / 'long_video_chunk001.wav'
        c.write_bytes(b'x' * 1024)
        tr._store_chunk_checkpoint(str(c), 300.0, [{'start': 0.0, 'end': 2.0, 'text': '正常结果'}])

        assert tr._load_chunk_checkpoint(str(c), 999.0) is None
        assert tr._load_chunk_checkpoint(str(c), 300.0) is not None
        assert tr._load_chunk_checkpoint(str(c), 300.4) is not None   # ±0.5s 容差内有效

    def test_corrupt_checkpoint_is_ignored(self, monkeypatch, tmp_path):
        import services.transcriber as tr

        c = tmp_path / 'long_video_chunk001.wav'
        c.write_bytes(b'x' * 1024)
        tr._chunk_checkpoint_path(str(c)).write_text('{broken json', encoding='utf-8')
        assert tr._load_chunk_checkpoint(str(c), 300.0) is None

    def test_split_reuses_existing_chunk_files(self, monkeypatch, tmp_path):
        """重试重切时，时长校验通过的有效分片不再过 ffmpeg。"""
        import services.transcriber as tr

        audio = tmp_path / 'long_video.m4a'
        audio.write_bytes(b'audio')
        existing = tmp_path / 'long_video_chunk000.wav'
        existing.write_bytes(b'x' * 4096)

        calls = []

        def fake_run(cmd, capture_output, check, timeout):
            calls.append(cmd)
            out = Path(cmd[-1])
            out.write_bytes(b'new-chunk')

        # 音频 700s → 3 片；遗留 chunk000 ffprobe 实测 300s → 有效复用
        def fake_probe(path):
            return 300.0 if 'chunk000' in str(path) else 700.0

        monkeypatch.setattr(tr, '_get_audio_duration_seconds', fake_probe)
        monkeypatch.setattr(tr, 'subprocess', type('S', (), {
            'run': staticmethod(fake_run), 'TimeoutExpired': Exception})())
        chunks = tr._split_audio_chunks(str(audio), 300)

        assert len(calls) == 2                    # chunk001/002 需要重切，chunk000 复用
        assert chunks[0][0] == str(existing)      # chunk000 复用旧文件（未被覆盖）
        assert Path(chunks[1][0]).read_bytes() == b'new-chunk'

    def test_split_recuts_truncated_leftover_chunk(self, monkeypatch, tmp_path):
        """崩溃遗留的截断分片（size>0 但时长不符）必须重切，不能复用。"""
        import services.transcriber as tr

        audio = tmp_path / 'long_video.m4a'
        audio.write_bytes(b'audio')
        truncated = tmp_path / 'long_video_chunk000.wav'
        truncated.write_bytes(b'x' * 4096)

        calls = []

        def fake_run(cmd, capture_output, check, timeout):
            calls.append(cmd)
            Path(cmd[-1]).write_bytes(b'recut')

        def fake_probe(path):
            # 音频 700s；遗留 chunk000 只有 120s → 与预期 300s 不符
            return 700.0 if 'chunk000' not in str(path) else 120.0

        monkeypatch.setattr(tr, '_get_audio_duration_seconds', fake_probe)
        monkeypatch.setattr(tr, 'subprocess', type('S', (), {
            'run': staticmethod(fake_run), 'TimeoutExpired': Exception})())
        chunks = tr._split_audio_chunks(str(audio), 300)

        assert len(calls) == 3                    # 全部重切（含截断的 chunk000）
        assert Path(chunks[0][0]).read_bytes() == b'recut'

    def test_cleanup_checkpoints_with_glob_special_chars_in_stem(self, monkeypatch, tmp_path):
        """标题含 [ ] 等 glob 特殊字符时清理仍按文件名过滤生效。"""
        import services.transcriber as tr

        audio = tmp_path / '中药[上]讲座.m4a'
        audio.write_bytes(b'audio')
        cp = tmp_path / '中药[上]讲座_chunk000.transcript.json'
        cp.write_text('{}', encoding='utf-8')
        other = tmp_path / '其他任务_chunk000.transcript.json'
        other.write_text('{}', encoding='utf-8')

        tr._cleanup_chunk_checkpoints(str(audio))

        assert not cp.exists()                    # 本任务的检查点被清掉
        assert other.exists()                     # 别的任务的不误删
