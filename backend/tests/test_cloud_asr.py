"""
云端 ASR（SiliconFlow SenseVoice）纯逻辑与编排测试

不触网：_do_request / detect_silences / export_chunks / transcribe_file 均可注入替身。
export_chunks 用项目自带 ffmpeg 生成真实音频验证（毫秒级，不触网）。
"""
import shutil
import subprocess

import httpx
import pytest
import time
from pathlib import Path
from unittest.mock import MagicMock

from services import cloud_asr
from services.cloud_asr import (
    CloudASRConfigError,
    CloudASRUnavailableError,
    cloud_asr_configured,
    distribute_times,
    parse_silences,
    plan_cuts,
    preferred_provider,
    split_sentences,
    strip_rich_tags,
    transcribe_file,
    transcribe_audio_cloud,
)
from services.transcriber import TranscriptionCancelledError


@pytest.fixture
def asr_enabled(monkeypatch):
    """把 config 当作已配置云端 ASR（不依赖真实 config.py 的属性存在）。"""
    monkeypatch.setattr('config.ASR_CLOUD_ENABLED', True, raising=False)
    monkeypatch.setattr(
        'config.ASR_CLOUD_ENDPOINT',
        'https://api.siliconflow.cn/v1/audio/transcriptions', raising=False)
    monkeypatch.setattr('config.ASR_CLOUD_API_KEY', 'sk-test-fake-key', raising=False)
    monkeypatch.setattr('config.ASR_CLOUD_MODEL', 'FunAudioLLM/SenseVoiceSmall', raising=False)


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(cloud_asr.time, 'sleep', lambda *_: None)


# provider 配置的读取单点已改为「DB（asr_providers 表）优先 → config.py 兜底」。
# 本模块的用例都是验证 config.py 兜底语义与编排逻辑，因此屏蔽 DB 层，让它们保持
# 改造前的确定行为；DB 优先与槽位解析由 test_asr_providers.py 显式覆盖。
_DEFAULT_SLOTS = {'cloud': 'mimo', 'cloud_free': 'xingchen'}


@pytest.fixture(autouse=True)
def _config_only_providers(monkeypatch):
    monkeypatch.setattr('db.asr_repo.get_provider', lambda key: None)
    monkeypatch.setattr('db.asr_repo.get_provider_for_engine', lambda engine: _DEFAULT_SLOTS.get(engine))
    monkeypatch.setattr('db.asr_repo.get_default_engine', lambda: '')


# ============ parse_silences ============

class TestParseSilences:
    def test_normal_pairs(self):
        text = "[silencedetect @ 0x1] silence_start: 10.0\nsilence_end: 10.6 | silence_duration: 0.6"
        assert parse_silences(text) == [(10.0, 10.6)]

    def test_trailing_silence_without_end(self):
        text = "silence_start: 50.0"
        assert parse_silences(text, total_duration=100.0) == [(50.0, 100.0)]

    def test_empty(self):
        assert parse_silences('', 100.0) == []
        assert parse_silences(None, 100.0) == []

    def test_multiple(self):
        text = ("silence_start: 1.0\nsilence_end: 1.5\n"
                "silence_start: 9.2\nsilence_end: 10.0\n")
        assert parse_silences(text) == [(1.0, 1.5), (9.2, 10.0)]


# ============ plan_cuts ============

class TestPlanCuts:
    def test_zero_duration(self):
        assert plan_cuts(0, [1, 2]) == []

    def test_short_audio_single_chunk(self):
        # 25s 且无静音 → 只有收尾切点
        assert plan_cuts(25.0, []) == [25.0]

    def test_normal_silences(self):
        # 100s，静音中点 10.3/25.25 → 最后一段 74.75s 超上限强制等分成 3 段
        cuts = plan_cuts(100.0, [10.3, 25.25])
        assert cuts[0] == pytest.approx(10.3)
        assert cuts[1] == pytest.approx(25.25)
        # 强制等分：ceil(74.75/30)=3 → 两个内部切点 + 收尾（切点保留 3 位小数）
        assert cuts[2] == pytest.approx(25.25 + 74.75 / 3, abs=0.01)
        assert cuts[3] == pytest.approx(25.25 + 74.75 * 2 / 3, abs=0.01)
        assert cuts[-1] == pytest.approx(100.0)
        # 所有相邻切点间距 ≥ 3 且 ≤ 30
        prev = 0.0
        for c in cuts:
            assert c - prev <= 30.0 + 1e-6
            assert c > prev  # 单调递增
            prev = c

    def test_close_silences_merged(self):
        # 相距 < 3s 的切点只保留第一个
        cuts = plan_cuts(30.0, [10.0, 11.0, 12.0])
        assert 11.0 not in cuts and 12.0 not in cuts
        assert 10.0 in cuts

    def test_no_silence_long_audio_forced_equal_split(self):
        cuts = plan_cuts(90.0, [])
        # ceil(90/30)=3 段 → 切点 30/60/90
        assert cuts == pytest.approx([30.0, 60.0, 90.0])

    def test_cut_points_inside_duration(self):
        # 越界候选（-5/60）被丢弃；末尾切点按设计 = 总时长
        cuts = plan_cuts(50.0, [-5.0, 25.0, 60.0])
        assert all(0 < c <= 50 for c in cuts)
        assert cuts[-1] == 50.0
        assert 25.0 in cuts
        assert -5.0 not in cuts and 60.0 not in cuts

    def test_leading_silence_near_start_skipped(self):
        # 距开头 < 3s 的切点丢弃
        cuts = plan_cuts(20.0, [2.0, 15.0])
        assert 2.0 not in cuts
        assert 15.0 in cuts


# ============ strip_rich_tags ============

class TestStripRichTags:
    def test_plain_text_untouched(self):
        assert strip_rich_tags('你好世界') == '你好世界'

    def test_tags_removed(self):
        raw = '<|HAPPY|>你好啊！<|LAUGHTER|>今天真开心<|BGM|>'
        assert strip_rich_tags(raw) == '你好啊！ 今天真开心'

    def test_empty_and_none(self):
        assert strip_rich_tags('') == ''
        assert strip_rich_tags(None) == ''

    def test_whitespace_normalized(self):
        assert strip_rich_tags('a\n\n b   c') == 'a b c'


# ============ cloud_asr_configured ============

class TestConfigured:
    def test_not_configured_by_default(self, monkeypatch):
        monkeypatch.setattr('config.ASR_CLOUD_ENABLED', False, raising=False)
        monkeypatch.setattr('config.ASR_CLOUD_API_KEY', '', raising=False)
        assert cloud_asr_configured() is False

    def test_configured(self, asr_enabled):
        assert cloud_asr_configured() is True

    def test_enabled_but_no_key(self, monkeypatch):
        monkeypatch.setattr('config.ASR_CLOUD_ENABLED', True, raising=False)
        monkeypatch.setattr('config.ASR_CLOUD_API_KEY', '', raising=False)
        assert cloud_asr_configured() is False


# ============ preferred_provider（一次性调用的槽位选择单点） ============

class TestPreferredProvider:
    """B站字幕抽查等一次性云端调用的 provider 选择：免费槽位优先，其次付费槽位。"""

    def test_free_slot_first(self, monkeypatch):
        monkeypatch.setattr(cloud_asr, 'cloud_asr_configured', lambda key: True)
        assert preferred_provider() == 'xingchen'

    def test_paid_when_free_unavailable(self, monkeypatch):
        """免费档缺密钥/停用 → 落到付费档（有可用云端就比本地 Whisper 强）"""
        monkeypatch.setattr(cloud_asr, 'cloud_asr_configured', lambda key: key == 'mimo')
        assert preferred_provider() == 'mimo'

    def test_none_when_nothing_configured(self, monkeypatch):
        monkeypatch.setattr(cloud_asr, 'cloud_asr_configured', lambda key: False)
        assert preferred_provider() is None

    def test_none_when_slots_empty(self, monkeypatch):
        monkeypatch.setattr('db.asr_repo.get_provider_for_engine', lambda engine: None)
        assert preferred_provider() is None


# ============ transcribe_file（mock _do_request，MiMo chat 响应形态） ============

def _resp(status, text='', json_body=None):
    r = MagicMock()
    r.status_code = status
    r.text = text
    if json_body is not None:
        r.json = lambda: json_body
    return r


def _mimo_body(content):
    """MiMo chat/completions 响应形态。"""
    return {'choices': [{'message': {'role': 'assistant', 'content': content}}]}


class TestTranscribeFile:
    def test_not_configured_raises_config_error(self, monkeypatch):
        monkeypatch.setattr('config.ASR_CLOUD_ENABLED', False, raising=False)
        monkeypatch.setattr('config.ASR_CLOUD_API_KEY', '', raising=False)
        with pytest.raises(CloudASRConfigError, match='未配置'):
            transcribe_file('whatever.mp3')

    def test_success(self, asr_enabled, monkeypatch):
        called = []
        monkeypatch.setattr(cloud_asr, '_do_request',
                            lambda cfg, p, t: (called.append(p), _resp(200, json_body=_mimo_body(' 识别结果 ')))[1])
        assert transcribe_file('a.mp3') == '识别结果'
        assert called == ['a.mp3']

    def test_extract_text_handles_empty_choices(self):
        """choices 空数组/缺字段必须安全返回空串（防越界，同 MaaS usage chunk 教训）。

        参数是**请求姿势**（chat/multipart）而非 provider 名——自定义 provider 才不会错分支。
        """
        assert cloud_asr._extract_asr_text('chat', {}) == ''
        assert cloud_asr._extract_asr_text('chat', {'choices': []}) == ''
        assert cloud_asr._extract_asr_text('chat', {'choices': None}) == ''
        assert cloud_asr._extract_asr_text('chat', {'choices': [{'message': {}}]}) == ''
        assert cloud_asr._extract_asr_text('chat', _mimo_body('内容')) == '内容'
        # multipart（OpenAI /audio/transcriptions）响应形态：{'text': ...}
        assert cloud_asr._extract_asr_text('multipart', {'text': '识别内容'}) == '识别内容'
        assert cloud_asr._extract_asr_text('multipart', {}) == ''
        # 未知姿势按 chat 处理（宽松兜底，不抛错）
        assert cloud_asr._extract_asr_text('unknown', _mimo_body('内容')) == '内容'

    def test_xingchen_provider_multipart_parse(self, asr_enabled, monkeypatch):
        """免费档 xingchen（multipart）走 {'text': ...} 解析，模型为 XingChenASR。"""
        monkeypatch.setattr(cloud_asr, '_do_request',
                            lambda cfg, p, t: _resp(200, json_body={'text': '免费档结果'}))
        assert transcribe_file('a.mp3', provider='xingchen') == '免费档结果'

    def test_xingchen_provider_default_model(self, monkeypatch):
        import config as _config
        for attr in ('ASR_FREE_ENABLED', 'ASR_FREE_ENDPOINT', 'ASR_FREE_API_KEY', 'ASR_FREE_MODEL'):
            if hasattr(_config, attr):
                monkeypatch.delattr(_config, attr, raising=False)
        cfg = cloud_asr._provider_cfg('xingchen')
        assert cfg['api'] == 'multipart'
        assert 'XingChenASR-V3.2-Ultra' in cfg['model']
        assert 'siliconflow' in cfg['endpoint']
        # 未配密钥 = 未配置
        assert cloud_asr.cloud_asr_configured('xingchen') is False

    def test_rich_tags_stripped(self, asr_enabled, monkeypatch):
        monkeypatch.setattr(cloud_asr, '_do_request',
                            lambda cfg, p, t: _resp(200, json_body=_mimo_body('<|HAPPY|>开心<|BGM|>')))
        assert transcribe_file('a.mp3') == '开心'

    def test_401_no_retry(self, asr_enabled, monkeypatch):
        calls = []
        def fake(cfg, p, t):
            calls.append(1)
            return _resp(401, 'Invalid token')
        monkeypatch.setattr(cloud_asr, '_do_request', fake)
        with pytest.raises(CloudASRConfigError, match='密钥'):
            transcribe_file('a.mp3')
        assert len(calls) == 1  # 配置类错误不重试

    def test_404_model_hint(self, asr_enabled, monkeypatch):
        monkeypatch.setattr(cloud_asr, '_do_request',
                            lambda cfg, p, t: _resp(404, 'not found'))
        with pytest.raises(CloudASRConfigError, match='模型不存在'):
            transcribe_file('a.mp3')

    def test_400_retried_then_unavailable(self, asr_enabled, monkeypatch, no_sleep):
        """400（可能是单片内容问题）走重试，耗尽后 Unavailable（交给本地补录），不判配置错误。"""
        monkeypatch.setattr(cloud_asr, '_do_request',
                            lambda cfg, p, t: _resp(400, json_body={'message': 'bad request'}))
        with pytest.raises(CloudASRUnavailableError, match='400'):
            transcribe_file('a.mp3', max_retries=2)

    def test_402_balance_is_config_error_no_retry(self, asr_enabled, monkeypatch):
        """402 余额不足是计费类错误：立即抛 ConfigError，不做 3 次无意义重试
        （当日实测付费档欠费时，每个任务白等 3 次退避 ≈11s）。"""
        calls = []

        def fake(cfg, p, t):
            calls.append(p)
            return _resp(402, json_body={'error': {'code': '402',
                                                   'message': 'Insufficient account balance'}})

        monkeypatch.setattr(cloud_asr, '_do_request', fake)
        with pytest.raises(CloudASRConfigError, match='余额不足'):
            transcribe_file('a.mp3')
        assert calls == ['a.mp3']  # 只发一次请求，无重试

    def test_429_then_success(self, asr_enabled, monkeypatch, no_sleep):
        responses = [_resp(429, 'rate limited'), _resp(429, 'rate limited'),
                     _resp(200, json_body=_mimo_body('ok'))]
        monkeypatch.setattr(cloud_asr, '_do_request', lambda cfg, p, t: responses.pop(0))
        assert transcribe_file('a.mp3') == 'ok'

    def test_retries_exhausted(self, asr_enabled, monkeypatch, no_sleep):
        monkeypatch.setattr(cloud_asr, '_do_request',
                            lambda cfg, p, t: _resp(503, 'overloaded'))
        with pytest.raises(CloudASRUnavailableError, match='503'):
            transcribe_file('a.mp3', max_retries=3)

    def test_network_error_retry_then_fail(self, asr_enabled, monkeypatch, no_sleep):
        def fake(cfg, p, t):
            raise httpx.ConnectError('boom')
        monkeypatch.setattr(cloud_asr, '_do_request', fake)
        with pytest.raises(CloudASRUnavailableError, match='ConnectError'):
            transcribe_file('a.mp3')

    def test_bad_json_body(self, asr_enabled, monkeypatch):
        r = _resp(200, 'not json at all')
        r.json = lambda: (_ for _ in ()).throw(ValueError('bad json'))
        monkeypatch.setattr(cloud_asr, '_do_request', lambda cfg, p, t: r)
        with pytest.raises(CloudASRUnavailableError):
            transcribe_file('a.mp3')


# ============ export_chunks（真实 ffmpeg，毫秒级） ============

class TestExportChunksReal:
    """冒烟实测教训：收尾切点传给 segment_times 会在 EOF 前多切微型尾片（4 vs 3）。
    修复后只传内部切点；分片实测时长（-segment_list）随结果一并返回，供时间轴对账。"""

    @pytest.fixture
    def sine_wav(self, tmp_path):
        """ffmpeg 生成 3 秒 440Hz 正弦波（恰好略长于探测时长，模拟毫秒级偏差场景）"""
        wav = tmp_path / 'sine.wav'
        subprocess.run(
            [cloud_asr._ffmpeg_bin(), '-y', '-v', 'error',
             '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3.0',
             '-ac', '1', '-ar', '16000', str(wav)],
            check=True, capture_output=True, timeout=30,
        )
        return str(wav)

    def test_trailing_cut_not_passed_to_segment_times(self, sine_wav, tmp_path):
        # cuts 含收尾切点 3.0：只应按内部切点 1.5 切，产出 2 片而非 3 片
        chunks, durations = cloud_asr.export_chunks(sine_wav, [1.5, 3.0])
        assert len(chunks) == 2
        for c in chunks:
            assert Path(c).stat().st_size > 0
        # 实测时长与计划一致（1.5 + 1.5 ≈ 3s，容差毫秒级帧对齐）
        assert durations is not None and len(durations) == 2
        assert sum(durations) == pytest.approx(3.0, abs=0.1)

    def test_single_chunk_direct_transcode(self, sine_wav, tmp_path):
        # 无内部切点（cuts 只有收尾）→ 整段 1 片；无 segment_list，durations=None
        chunks, durations = cloud_asr.export_chunks(sine_wav, [3.0])
        assert len(chunks) == 1
        assert durations is None
        assert Path(chunks[0]).stat().st_size > 0
        assert Path(chunks[0]).name.endswith('_cloud_000.mp3')

    def test_chunks_are_mp3_16k_mono(self, sine_wav, tmp_path):
        chunks, _ = cloud_asr.export_chunks(sine_wav, [1.0, 2.0, 3.0])
        assert len(chunks) == 3
        # 分片后缀可被 ffprobe 识别为 mp3（16k 单声道 48kbps）
        # which('ffmpeg') 可能返回大写的 ffmpeg.EXE，后缀替换要大小写不敏感
        ffprobe = shutil.which('ffprobe') or str(
            Path(cloud_asr._ffmpeg_bin()).with_name('ffprobe.exe'))
        r = subprocess.run(
            [ffprobe,
             '-v', 'quiet', '-show_entries', 'stream=codec_name,sample_rate,channels',
             '-of', 'csv=p=0', chunks[0]],
            capture_output=True, text=True, timeout=15,
        )
        assert 'mp3,16000,1' in r.stdout, f'ffprobe 输出异常: rc={r.returncode} stderr={r.stderr[:200]}'

    def test_stale_chunks_cleaned_before_export(self, sine_wav, tmp_path):
        # 上次转录残留的高编号分片（本次不会重写）不混入新导出结果
        stale = tmp_path / 'sine_cloud_009.mp3'
        stale.write_bytes(b'stale')
        chunks, durations = cloud_asr.export_chunks(sine_wav, [1.5, 3.0])
        assert len(chunks) == 2
        assert not stale.exists()
        assert 'sine_cloud_009.mp3' not in [Path(c).name for c in chunks]
        assert durations is not None and len(durations) == 2


# ============ transcribe_audio_cloud（编排，全 mock） ============

@pytest.fixture
def fake_audio(tmp_path):
    p = tmp_path / 'fake_audio.wav'
    p.write_bytes(b'\x00' * 16)
    return str(p)


def _setup_orchestration(monkeypatch, tmp_path, duration=100.0, texts=None, silences=None):
    """mock 探测/导出/上传，返回 (chunk_paths, transcribe_calls)。"""
    silences = silences if silences is not None else [(10.0, 10.6), (25.0, 25.5)]
    texts = texts if texts is not None else ['文本一', '文本二', '文本三', '文本四', '文本五']

    monkeypatch.setattr(cloud_asr, '_get_audio_duration_seconds', lambda p: duration)
    monkeypatch.setattr(cloud_asr, 'detect_silences', lambda p, d: silences)

    cuts = plan_cuts(duration, [(s + e) / 2 for s, e in silences])
    n = len(cuts)
    chunk_paths = []
    for i in range(n):
        cp = tmp_path / f'fake_audio_cloud_{i:03d}.mp3'
        cp.write_bytes(b'\x00' * 8)
        chunk_paths.append(str(cp))
    monkeypatch.setattr(cloud_asr, 'export_chunks', lambda ap, c: (list(chunk_paths), None))

    calls = []
    def fake_transcribe(path, **kwargs):
        calls.append(path)
        return texts[len(calls) - 1] if len(calls) <= len(texts) else '溢出'
    monkeypatch.setattr(cloud_asr, 'transcribe_file', fake_transcribe)
    return chunk_paths, calls


class TestTranscribeAudioCloud:
    @staticmethod
    def _disable_rescue(monkeypatch):
        """禁用补录路径（编排测试不触碰真实本地模型/免费云端）。"""
        import services.local_asr as la
        monkeypatch.setattr(la, 'firered_model_ready', lambda: False)
        monkeypatch.setattr(
            'services.transcriber.transcribe_audio_sample',
            lambda *a, **kw: (_ for _ in ()).throw(Exception('rescue disabled')))
        monkeypatch.setattr(cloud_asr, 'cloud_asr_configured',
                            lambda provider='mimo': provider != 'xingchen')

    def test_full_flow_srt_txt(self, monkeypatch, tmp_path, fake_audio):
        chunk_paths, calls = _setup_orchestration(monkeypatch, tmp_path)
        progress = []
        srt, txt, seg_count = transcribe_audio_cloud(
            fake_audio, progress_callback=lambda pct, msg: progress.append((pct, msg)))

        assert Path(srt).exists() and Path(txt).exists()
        assert seg_count == len(calls)
        srt_text = Path(srt).read_text(encoding='utf-8')
        txt_text = Path(txt).read_text(encoding='utf-8')
        # SRT 结构：序号 + 时间轴 + 文本；时间轴必须单调（并发完成顺序不定）
        assert '1\n00:00:00,000 --> ' in srt_text
        assert '文本一' in srt_text and '文本五' in srt_text
        assert '[00:00 - ' in txt_text and '文本一' in txt_text
        # 进度：开头/结尾都有推送
        assert progress[0][0] == 2
        assert progress[-1][0] == 100
        # 临时分片已清理
        for cp in chunk_paths:
            assert not Path(cp).exists()

    def test_srt_timeline_monotonic_even_with_reversed_completion(self, monkeypatch, tmp_path, fake_audio):
        """并发乱序完成（倒序返回）时 SRT 时间轴仍单调递增。"""
        monkeypatch.setattr(cloud_asr, '_concurrency', lambda: 1)
        _, calls = _setup_orchestration(monkeypatch, tmp_path)

        texts = ['文本一', '文本二', '文本三', '文本四', '文本五']
        state = {'n': 0}

        def reversed_transcribe(path, **kw):
            # 与提交顺序无关，永远返回队尾文本 → 强制乱序完成效果
            idx = len(texts) - 1 - (state['n'] % len(texts))
            state['n'] += 1
            return texts[idx]

        monkeypatch.setattr(cloud_asr, 'transcribe_file', reversed_transcribe)
        srt, txt, seg_count = transcribe_audio_cloud(fake_audio)
        assert seg_count == 5
        lines = [l for l in Path(srt).read_text(encoding='utf-8').splitlines()
                 if '-->' in l]
        starts = [l.split(' --> ')[0] for l in lines]
        assert starts == sorted(starts)

    def test_empty_chunk_dropped(self, monkeypatch, tmp_path, fake_audio):
        texts = ['文本一', '', '文本三', '文本四', '文本五']
        _setup_orchestration(monkeypatch, tmp_path, texts=texts)
        srt, txt, seg_count = transcribe_audio_cloud(fake_audio)
        assert seg_count == 4
        assert '文本一' in Path(srt).read_text(encoding='utf-8')

    def test_all_empty_returns_zero_segments(self, monkeypatch, tmp_path, fake_audio):
        _setup_orchestration(monkeypatch, tmp_path, texts=[''] * 5)
        srt, txt, seg_count = transcribe_audio_cloud(fake_audio)
        assert seg_count == 0
        assert Path(srt).read_text(encoding='utf-8') == ''

    def test_cancel_between_chunks(self, monkeypatch, tmp_path, fake_audio):
        chunk_paths, _ = _setup_orchestration(monkeypatch, tmp_path)
        monkeypatch.setattr(cloud_asr, '_concurrency', lambda: 1)
        # 串行 + 每片 10ms 真实延迟：取消发生在中途而非全部跑完
        def slow_transcribe(path, **kw):
            time.sleep(0.01)
            return '文本'
        monkeypatch.setattr(cloud_asr, 'transcribe_file', slow_transcribe)
        cancel = MagicMock(side_effect=[False, True])
        with pytest.raises(TranscriptionCancelledError):
            transcribe_audio_cloud(fake_audio, cancel_check=cancel)
        # 取消也要清理分片
        for cp in chunk_paths:
            assert not Path(cp).exists()

    def test_cloud_failure_propagates_for_fallback(self, monkeypatch, tmp_path, fake_audio):
        chunk_paths, _ = _setup_orchestration(monkeypatch, tmp_path)
        monkeypatch.setattr(cloud_asr, 'transcribe_file',
                            lambda p, **kw: (_ for _ in ()).throw(CloudASRConfigError('云端 ASR 401: 密钥无效')))
        with pytest.raises(CloudASRConfigError, match='401'):
            transcribe_audio_cloud(fake_audio)
        for cp in chunk_paths:
            assert not Path(cp).exists()

    def test_timeline_from_actual_chunk_durations(self, monkeypatch, tmp_path, fake_audio):
        """时间轴按 ffmpeg 实测分片时长累计（而非计划切点），根治 4vs3 类偏差。"""
        monkeypatch.setattr(cloud_asr, '_get_audio_duration_seconds', lambda p: 100.0)
        monkeypatch.setattr(cloud_asr, 'detect_silences', lambda p, d: [])
        monkeypatch.setattr(cloud_asr, '_concurrency', lambda: 1)
        chunks = []
        for name in ['fake_audio_cloud_000.mp3', 'fake_audio_cloud_001.mp3']:
            cp = tmp_path / name
            cp.write_bytes(b'\x00' * 8)
            chunks.append(str(cp))
        # 探测 100s，但实际只有 2 片（10s + 90s）——SRT 时间轴必须按实测累计
        monkeypatch.setattr(cloud_asr, 'export_chunks', lambda ap, c: (chunks, [10.0, 90.0]))
        texts = iter(['第一段', '第二段'])
        monkeypatch.setattr(cloud_asr, 'transcribe_file', lambda p, **kw: next(texts))
        srt, txt, seg_count = transcribe_audio_cloud(fake_audio)
        assert seg_count == 2
        lines = Path(srt).read_text(encoding='utf-8').splitlines()
        assert lines[1].startswith('00:00:00,000 --> 00:00:10,000')
        assert lines[5].startswith('00:00:10,000 --> 00:01:40,000')

    def test_single_shard_failure_returns_partial(self, monkeypatch, tmp_path, fake_audio):
        """单片 Unavailable 重试耗尽 → 补录被禁用时跳过该段，其余照常返回部分结果。"""
        _setup_orchestration(monkeypatch, tmp_path)
        self._disable_rescue(monkeypatch)

        def flaky(path, **kw):
            if Path(path).name == 'fake_audio_cloud_002.mp3':
                raise CloudASRUnavailableError('重试耗尽: ReadTimeout')
            return f'OK-{Path(path).name}'
        monkeypatch.setattr(cloud_asr, 'transcribe_file', flaky)
        srt, txt, seg_count = transcribe_audio_cloud(fake_audio)
        assert seg_count == 4
        srt_text = Path(srt).read_text(encoding='utf-8')
        assert 'OK-fake_audio_cloud_000.mp3' in srt_text
        assert 'OK-fake_audio_cloud_004.mp3' in srt_text
        assert 'OK-fake_audio_cloud_002.mp3' not in srt_text

    def test_failed_shard_rescued_by_firered(self, monkeypatch, tmp_path, fake_audio):
        """分片级递归补救：云端单片失败 → firered 补录恢复，不产出空洞。"""
        _setup_orchestration(monkeypatch, tmp_path)
        import services.local_asr as la
        monkeypatch.setattr(cloud_asr, 'cloud_asr_configured',
                            lambda provider='mimo': provider != 'xingchen')
        monkeypatch.setattr(la, 'firered_model_ready', lambda: True)
        monkeypatch.setattr(la, 'transcribe_chunk_text',
                            lambda p, language=None: '本地补录内容。')

        def flaky(path, **kw):
            if Path(path).name == 'fake_audio_cloud_002.mp3':
                raise CloudASRUnavailableError('重试耗尽: ReadTimeout')
            return f'OK-{Path(path).name}'
        monkeypatch.setattr(cloud_asr, 'transcribe_file', flaky)
        srt, txt, seg_count = transcribe_audio_cloud(fake_audio)
        assert seg_count == 5  # 4 片云端成功 + 1 片补录
        srt_text = Path(srt).read_text(encoding='utf-8')
        assert '本地补录内容。' in srt_text

    def test_rescue_prefers_free_cloud_when_primary_mimo(self, monkeypatch, tmp_path, fake_audio):
        """mimo 单片 429 失败 → 补录先用免费档 XingChen（换一家避开限流）。"""
        _setup_orchestration(monkeypatch, tmp_path)
        import services.local_asr as la
        monkeypatch.setattr(cloud_asr, 'cloud_asr_configured',
                            lambda provider='mimo': True)
        monkeypatch.setattr(la, 'firered_model_ready', lambda: False)

        def flaky(path, provider='mimo', **kw):
            if provider == 'mimo' and Path(path).name == 'fake_audio_cloud_002.mp3':
                raise CloudASRUnavailableError('重试耗尽: HTTP 429')
            return f'OK-{provider}-{Path(path).name}'
        monkeypatch.setattr(cloud_asr, 'transcribe_file', flaky)
        srt, _txt, seg_count = transcribe_audio_cloud(fake_audio)
        assert seg_count == 5
        assert 'OK-xingchen-fake_audio_cloud_002.mp3' in Path(srt).read_text(encoding='utf-8')

    def test_rescue_skips_free_cloud_when_primary_is_xingchen(self, monkeypatch, tmp_path, fake_audio):
        """主 provider 就是 xingchen 时，补录不再拿同一家限流接口重试，直接本地。"""
        _setup_orchestration(monkeypatch, tmp_path)
        import services.local_asr as la
        monkeypatch.setattr(cloud_asr, 'cloud_asr_configured',
                            lambda provider='mimo': True)
        monkeypatch.setattr(la, 'firered_model_ready', lambda: False)
        monkeypatch.setattr('services.transcriber.transcribe_audio_sample',
                            lambda *a, **kw: 'Whisper补录内容。')

        calls = []

        def flaky(path, provider='mimo', **kw):
            calls.append((Path(path).name, provider))
            if Path(path).name == 'fake_audio_cloud_002.mp3':
                raise CloudASRUnavailableError('重试耗尽: HTTP 429')
            return f'OK-{Path(path).name}'
        monkeypatch.setattr(cloud_asr, 'transcribe_file', flaky)
        srt, _txt, seg_count = transcribe_audio_cloud(fake_audio, provider='xingchen')
        assert seg_count == 5
        assert 'Whisper补录内容。' in Path(srt).read_text(encoding='utf-8')
        # 失败片只被主 provider 尝试过一次（补录没再调 xingchen）
        assert ('fake_audio_cloud_002.mp3', 'xingchen') in calls
        assert calls.count(('fake_audio_cloud_002.mp3', 'xingchen')) == 1

    def test_rescue_falls_back_to_whisper_when_firered_unavailable(self, monkeypatch, tmp_path, fake_audio):
        """firered 模型未就绪 → 补录走 Whisper base。"""
        _setup_orchestration(monkeypatch, tmp_path)
        import services.local_asr as la
        monkeypatch.setattr(cloud_asr, 'cloud_asr_configured',
                            lambda provider='mimo': provider != 'xingchen')
        monkeypatch.setattr(la, 'firered_model_ready', lambda: False)
        monkeypatch.setattr('services.transcriber.transcribe_audio_sample',
                            lambda *a, **kw: 'Whisper补录内容。')

        def flaky(path, **kw):
            if Path(path).name == 'fake_audio_cloud_002.mp3':
                raise CloudASRUnavailableError('重试耗尽: ReadTimeout')
            return f'OK-{Path(path).name}'
        monkeypatch.setattr(cloud_asr, 'transcribe_file', flaky)
        _srt, _txt, seg_count = transcribe_audio_cloud(fake_audio)
        assert seg_count == 5
        assert 'Whisper补录内容。' in Path(_srt).read_text(encoding='utf-8')

    def test_rescue_failure_keeps_partial_without_raise(self, monkeypatch, tmp_path, fake_audio):
        """补录也失败 → 该段留空但不抛错（部分结果仍交付，日志可见）。"""
        _setup_orchestration(monkeypatch, tmp_path)
        self._disable_rescue(monkeypatch)

        def flaky(path, **kw):
            if Path(path).name == 'fake_audio_cloud_002.mp3':
                raise CloudASRUnavailableError('重试耗尽: ReadTimeout')
            return f'OK-{Path(path).name}'
        monkeypatch.setattr(cloud_asr, 'transcribe_file', flaky)
        srt, txt, seg_count = transcribe_audio_cloud(fake_audio)
        assert seg_count == 4

    def test_all_fail_but_rescue_recovers_returns_result(self, monkeypatch, tmp_path, fake_audio):
        """云端全失败但本地补录全部恢复 → 不抛 Unavailable，直接交付补录结果。"""
        _setup_orchestration(monkeypatch, tmp_path)
        import services.local_asr as la
        monkeypatch.setattr(cloud_asr, 'cloud_asr_configured',
                            lambda provider='mimo': provider != 'xingchen')
        monkeypatch.setattr(la, 'firered_model_ready', lambda: True)
        monkeypatch.setattr(la, 'transcribe_chunk_text',
                            lambda p, language=None: '补录。')
        monkeypatch.setattr(
            cloud_asr, 'transcribe_file',
            lambda p, **kw: (_ for _ in ()).throw(CloudASRUnavailableError('HTTP 500')))
        _srt, _txt, seg_count = transcribe_audio_cloud(fake_audio)
        assert seg_count == 5  # 5 片全部补录恢复

    def test_all_shards_fail_raises_unavailable(self, monkeypatch, tmp_path, fake_audio):
        """全部分片失败（补录被禁用）→ 抛 UnavailableError，由调用方整体回退本地 Whisper。"""
        _setup_orchestration(monkeypatch, tmp_path)
        self._disable_rescue(monkeypatch)
        monkeypatch.setattr(
            cloud_asr, 'transcribe_file',
            lambda p, **kw: (_ for _ in ()).throw(CloudASRUnavailableError('HTTP 500')))
        with pytest.raises(CloudASRUnavailableError, match='全部分片失败'):
            transcribe_audio_cloud(fake_audio)

    def test_no_duration_raises(self, monkeypatch, tmp_path, fake_audio):
        monkeypatch.setattr(cloud_asr, '_get_audio_duration_seconds', lambda p: None)
        with pytest.raises(CloudASRUnavailableError, match='时长'):
            transcribe_audio_cloud(fake_audio)

    def test_concurrency_from_config(self, monkeypatch):
        monkeypatch.delenv('ASR_CLOUD_CONCURRENCY', raising=False)
        monkeypatch.setattr('config.ASR_CLOUD_CONCURRENCY', 6, raising=False)
        assert cloud_asr._concurrency() == 6
        monkeypatch.setattr('config.ASR_CLOUD_CONCURRENCY', 99, raising=False)
        assert cloud_asr._concurrency() == 16  # 钳制上限
        monkeypatch.setattr('config.ASR_CLOUD_CONCURRENCY', 0, raising=False)
        assert cloud_asr._concurrency() == 1  # 下限
        monkeypatch.setattr('config.ASR_CLOUD_CONCURRENCY', 'bad', raising=False)
        assert cloud_asr._concurrency() == cloud_asr._DEFAULT_CONCURRENCY


# ============ split_sentences（句级切分，纯函数） ============

class TestSplitSentences:
    def test_chinese_sentences(self):
        assert split_sentences('今天天气很好。我们去公园吧！') == ['今天天气很好。', '我们去公园吧！']

    def test_english_sentences(self):
        assert split_sentences('Hello world. This is a test.') == ['Hello world.', 'This is a test.']

    def test_question_and_exclamation(self):
        assert split_sentences('真的吗？我表示怀疑！那就算了吧。') == [
            '真的吗？', '我表示怀疑！', '那就算了吧。']

    def test_closing_quote_merged_into_previous(self):
        # 句末标点后的引号并入前句，不另起一条
        assert split_sentences('他说：“你好。”然后走了。') == ['他说：“你好。”', '然后走了。']

    def test_decimal_point_not_split(self):
        # 小数点/缩写后的 '.' 后不是空白 → 不算句末
        assert split_sentences('价格是3.5元，很便宜。') == ['价格是3.5元，很便宜。']

    def test_short_fragment_merged(self):
        # 过短碎片（< _SUB_MIN_CHARS）并入前句，避免字幕抖动
        assert split_sentences('嗯。今天我们来讨论这个问题。') == ['嗯。今天我们来讨论这个问题。']

    def test_no_punctuation_single_sentence(self):
        # 无标点 → 整段 1 句（与修复前「一片一条」行为一致）
        assert split_sentences('文本一') == ['文本一']

    def test_long_sentence_hard_split(self):
        long_text = '啊' * 100  # 无任何标点
        parts = split_sentences(long_text)
        assert len(parts) >= 3
        assert all(len(p) <= cloud_asr._SUB_MAX_CHARS for p in parts)
        assert ''.join(parts) == long_text

    def test_hard_split_prefers_secondary_break(self):
        # 超长句里若有逗号，优先在逗号处断，不硬切字符
        text = '前面内容' * 4 + '，' + '后面内容' * 20
        parts = split_sentences(text)
        assert len(parts) >= 2
        assert parts[0].endswith('，')

    def test_newline_boundary(self):
        assert split_sentences('第一行内容\n第二行内容') == ['第一行内容', '第二行内容']

    def test_empty_and_none(self):
        assert split_sentences('') == []
        assert split_sentences('   ') == []
        assert split_sentences(None) == []


# ============ distribute_times（时长按字数比例分配，纯函数） ============

class TestDistributeTimes:
    def test_single_sentence_keeps_full_range(self):
        assert distribute_times(10.0, 20.0, ['一句话']) == [(10.0, 20.0, '一句话')]

    def test_empty_sentences(self):
        assert distribute_times(0.0, 5.0, []) == []

    def test_proportional_split(self):
        # 'aa'(2) + 'bbbb'(4) 共 6 字，跨度 6s → 2s / 4s
        out = distribute_times(0.0, 6.0, ['aa', 'bbbb'])
        assert out == [(0.0, 2.0, 'aa'), (2.0, 6.0, 'bbbb')]

    def test_first_start_and_last_end_aligned(self):
        out = distribute_times(3.5, 13.5, ['一二三', '四五六七八', '九'])
        assert out[0][0] == 3.5
        assert out[-1][1] == 13.5
        # 区间单调不重叠
        for (s0, e0, _), (s1, _e1, _) in zip(out, out[1:]):
            assert e0 == s1
            assert s1 >= s0

    def test_zero_length_sentences_fall_back_to_whole(self):
        # 全是空串（理论不会出现）时按整段返回，不崩溃
        assert distribute_times(0.0, 4.0, ['', '']) == [(0.0, 4.0, '')]


# ============ 编排：句级化后 SRT 条目数 ============

class TestSentenceLevelOutput:
    def test_punctuated_chunk_expands_to_multiple_entries(self, monkeypatch, tmp_path, fake_audio):
        """带标点的分片文本被拆成多条 SRT，而非整片一坨。"""
        texts = ['今天天气很好。我们去公园玩耍吧！非常感谢你啊。'] * 5
        _setup_orchestration(monkeypatch, tmp_path, texts=texts)
        srt, txt, seg_count = transcribe_audio_cloud(fake_audio)
        lines = [l for l in Path(srt).read_text(encoding='utf-8').splitlines() if '-->' in l]
        # 每片 3 句 × 5 片 = 15 条，远多于分片数
        assert seg_count == 15
        assert len(lines) == 15
        assert '今天天气很好。' in Path(srt).read_text(encoding='utf-8')

    def test_no_punctuation_keeps_one_entry_per_chunk(self, monkeypatch, tmp_path, fake_audio):
        """无标点分片退化行为不变：1 片 = 1 条。"""
        _setup_orchestration(monkeypatch, tmp_path, texts=['文本一', '文本二', '文本三', '文本四', '文本五'])
        srt, txt, seg_count = transcribe_audio_cloud(fake_audio)
        assert seg_count == 5
