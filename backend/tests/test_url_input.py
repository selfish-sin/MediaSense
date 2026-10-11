"""_handle_url_input 无字幕/字幕优先链路测试（此前零覆盖）。

覆盖 2026-09-12 修复：
- 抽查失败复用预下载音频 → files['audio'] 登记（旧代码漏登记）
- 抽查通过 → 已下载音频登记为素材（不闲置）
- 无字幕 → _acquire_audio 下载 → files['audio'] 登记
- _retry 断点续传复用 → 按扩展名登记 audio/video，且不重复下载
"""
from contextlib import ExitStack
from unittest.mock import patch, AsyncMock, MagicMock

BILI_URL = 'https://www.bilibili.com/video/BV1xx411c7mD'


def _opts(**kw):
    return {'input_type': 'url', **kw}


def _enter_bili_common(stack: ExitStack):
    """B站路径的公共 mock：平台识别、BV 提取、cookie、取消检查、日志与任务更新。"""
    stack.enter_context(
        patch('services.downloader.detect_platform', return_value=('哔哩哔哩', False)))
    stack.enter_context(
        patch('main._extract_bvid', new_callable=AsyncMock, return_value='BV1xx411c7mD'))
    stack.enter_context(
        patch('services.bilibili_cookies.load_cookies', return_value={}))
    stack.enter_context(
        patch('socket_handlers.check_cancelled', return_value=False))
    stack.enter_context(patch('main.emit_log', new_callable=AsyncMock))
    stack.enter_context(patch('main.update_job'))


class TestBilibiliPaths:
    async def test_no_subtitle_downloads_audio_and_registers(self, tmp_path):
        """B站确认无字幕：走 _acquire_audio 下载并登记 files['audio']。"""
        from main import _handle_url_input

        audio = tmp_path / 'video.m4a'
        audio.write_bytes(b'x' * 2048)

        with ExitStack() as stack:
            _enter_bili_common(stack)
            stack.enter_context(
                patch('services.downloader.extract_bilibili_subtitles', new_callable=AsyncMock,
                      return_value={'success': False, 'message': 'B站确认无可用字幕',
                                    'reason': 'no_subtitle'}))
            m_dl = stack.enter_context(
                patch('services.downloader.download_video', new_callable=AsyncMock,
                      return_value=(str(audio), '标题', '哔哩哔哩')))
            m_tr = stack.enter_context(
                patch('services.transcriber.transcribe_audio', new_callable=AsyncMock,
                      return_value=('t.srt', 't.txt', 3)))
            srt, txt, seg, _title, _platform, files, meta = await _handle_url_input(
                BILI_URL, tmp_path, MagicMock(), 'j1', _opts(), 'base', False, 'zh')

        m_dl.assert_called_once()
        m_tr.assert_called_once()
        assert files['audio'] == str(audio)
        assert (srt, txt, seg) == ('t.srt', 't.txt', 3)
        assert meta == {'source': 'transcription'}

    async def test_subtitle_check_fail_downloads_full_audio_for_transcribe(self, tmp_path):
        """B站字幕抽查失败：丢弃错位字幕；抽查只下片段，转录时完整音频重新下载。"""
        from main import _handle_url_input

        sample = tmp_path / 'sample90s.m4a'
        sample.write_bytes(b'x' * 2048)
        full = tmp_path / 'full.m4a'
        full.write_bytes(b'y' * 4096)
        sub_srt = tmp_path / 'bilibili_subtitle.srt'
        sub_srt.write_text('1\n00:00:00,000 --> 00:00:06,000\n旧字幕\n', encoding='utf-8')
        sub_txt = tmp_path / 'bilibili_subtitle.txt'
        sub_txt.write_text('[00:00 - 00:06] 旧字幕\n', encoding='utf-8')

        with ExitStack() as stack:
            _enter_bili_common(stack)
            stack.enter_context(
                patch('services.downloader.extract_bilibili_subtitles', new_callable=AsyncMock,
                      return_value={'success': True, 'srt_path': sub_srt, 'txt_path': sub_txt,
                                    'title': '旧视频标题', 'message': 'ok'}))
            stack.enter_context(
                patch('services.downloader.verify_subtitle_against_audio', new_callable=AsyncMock,
                      return_value=(False, '相似度 0.05（错位）')))
            m_dl = stack.enter_context(
                patch('services.downloader.download_video', new_callable=AsyncMock,
                      side_effect=[(str(sample), '', ''), (str(full), '标题', '哔哩哔哩')]))
            m_tr = stack.enter_context(
                patch('services.transcriber.transcribe_audio', new_callable=AsyncMock,
                      return_value=('t.srt', 't.txt', 5)))
            _srt, _txt, _seg, _title, _platform, files, _meta = await _handle_url_input(
                BILI_URL, tmp_path, MagicMock(), 'j1', _opts(), 'base', False, 'zh')

        # 两次下载：抽查片段（max_seconds=90，子目录）+ 转录完整音频（片段不复用）
        assert m_dl.call_count == 2
        first, second = m_dl.call_args_list
        assert first.kwargs.get('max_seconds') == 90
        assert first.args[1].name == '_subtitle_check'
        assert 'max_seconds' not in second.kwargs
        m_tr.assert_called_once()
        assert files['audio'] == str(full)
        assert not sub_srt.exists() and not sub_txt.exists()   # 错位字幕已丢弃

    async def test_subtitle_check_pass_uses_clip_only_no_audio_material(self, tmp_path):
        """B站字幕抽查通过：只为比对下载开头片段（max_seconds=90），片段不作为素材。"""
        from main import _handle_url_input

        sample = tmp_path / 'sample.m4a'
        sample.write_bytes(b'x' * 2048)
        sub_srt = tmp_path / 'bilibili_subtitle.srt'
        sub_srt.write_text('1\n00:00:00,000 --> 00:00:06,000\n对得上的字幕\n', encoding='utf-8')
        sub_txt = tmp_path / 'bilibili_subtitle.txt'
        sub_txt.write_text('[00:00 - 00:06] 对得上的字幕\n', encoding='utf-8')

        with ExitStack() as stack:
            _enter_bili_common(stack)
            stack.enter_context(
                patch('services.downloader.extract_bilibili_subtitles', new_callable=AsyncMock,
                      return_value={'success': True, 'srt_path': sub_srt, 'txt_path': sub_txt,
                                    'title': '标题', 'message': 'ok'}))
            stack.enter_context(
                patch('services.downloader.verify_subtitle_against_audio', new_callable=AsyncMock,
                      return_value=(True, '相似度 0.90')))
            m_dl = stack.enter_context(
                patch('services.downloader.download_video', new_callable=AsyncMock,
                      return_value=(str(sample), '', '')))
            m_tr = stack.enter_context(
                patch('services.transcriber.transcribe_audio', new_callable=AsyncMock))
            srt, _txt, _seg, _title, _platform, files, meta = await _handle_url_input(
                BILI_URL, tmp_path, MagicMock(), 'j1', _opts(), 'base', False, 'zh')

        m_tr.assert_not_called()                     # 字幕可用，不转录
        assert str(srt) == str(sub_srt)
        m_dl.assert_called_once()                    # 抽查只下载一次
        assert m_dl.call_args.kwargs.get('max_seconds') == 90
        assert 'audio' not in files                  # 片段不作为素材登记
        assert meta == {'source': 'platform_subtitle', 'subtitle_from': 'bilibili',
                        'subtitle_check': 'passed'}


class TestNonBilibiliPaths:
    async def test_youtube_no_subtitle_downloads_and_registers(self, tmp_path):
        """非B站（YouTube）无字幕：抓取失败 → 下载音频 → 转录并登记 files['audio']。"""
        from main import _handle_url_input

        audio = tmp_path / 'yt.m4a'
        audio.write_bytes(b'x' * 2048)

        with ExitStack() as stack:
            stack.enter_context(
                patch('services.downloader.detect_platform', return_value=('YouTube', True)))
            stack.enter_context(
                patch('services.downloader.extract_youtube_subtitles', new_callable=AsyncMock,
                      return_value={'success': False, 'message': '无可用字幕'}))
            m_dl = stack.enter_context(
                patch('services.downloader.download_video', new_callable=AsyncMock,
                      return_value=(str(audio), 'YT 标题', 'YouTube')))
            m_tr = stack.enter_context(
                patch('services.transcriber.transcribe_audio', new_callable=AsyncMock,
                      return_value=('t.srt', 't.txt', 4)))
            stack.enter_context(patch('socket_handlers.check_cancelled', return_value=False))
            stack.enter_context(patch('main.emit_log', new_callable=AsyncMock))
            stack.enter_context(patch('main.update_job'))
            _srt, _txt, _seg, _title, _platform, files, _meta = await _handle_url_input(
                'https://www.youtube.com/watch?v=test', tmp_path, MagicMock(), 'j1',
                _opts(), 'base', False, 'zh')

        m_dl.assert_called_once()
        m_tr.assert_called_once()
        assert files['audio'] == str(audio)


class TestRetryResume:
    async def test_retry_reuses_existing_audio_without_download(self, tmp_path):
        """_retry 断点续传：复用 job_dir 已有音频，不重复下载，且登记素材。"""
        from main import _handle_url_input

        existing = tmp_path / 'prev_audio.m4a'
        existing.write_bytes(b'x' * 4096)

        with ExitStack() as stack:
            _enter_bili_common(stack)
            stack.enter_context(
                patch('services.downloader.extract_bilibili_subtitles', new_callable=AsyncMock,
                      return_value={'success': False, 'message': 'no_subtitle'}))
            m_dl = stack.enter_context(patch('services.downloader.download_video', new_callable=AsyncMock))
            m_tr = stack.enter_context(
                patch('services.transcriber.transcribe_audio', new_callable=AsyncMock,
                      return_value=('t.srt', 't.txt', 2)))
            _srt, _txt, _seg, _title, _platform, files, _meta = await _handle_url_input(
                BILI_URL, tmp_path, MagicMock(), 'j1', _opts(_retry=True), 'base', False, 'zh')

        m_dl.assert_not_called()
        m_tr.assert_called_once()
        assert files['audio'] == str(existing)

    async def test_retry_reuses_existing_video_registers_video(self, tmp_path):
        """_retry 复用视频文件时按扩展名登记 files['video']（而非误标音频）。"""
        from main import _handle_url_input

        existing = tmp_path / 'prev_video.mp4'
        existing.write_bytes(b'x' * 4096)

        with ExitStack() as stack:
            _enter_bili_common(stack)
            stack.enter_context(
                patch('services.downloader.extract_bilibili_subtitles', new_callable=AsyncMock,
                      return_value={'success': False, 'message': 'no_subtitle'}))
            m_dl = stack.enter_context(patch('services.downloader.download_video', new_callable=AsyncMock))
            stack.enter_context(
                patch('services.transcriber.transcribe_audio', new_callable=AsyncMock,
                      return_value=('t.srt', 't.txt', 2)))
            _srt, _txt, _seg, _title, _platform, files, _meta = await _handle_url_input(
                BILI_URL, tmp_path, MagicMock(), 'j1', _opts(_retry=True), 'base', False, 'zh')

        m_dl.assert_not_called()
        assert files['video'] == str(existing)
        assert 'audio' not in files

    async def test_resume_scan_skips_leftover_chunk_wavs(self, tmp_path):
        """分片转录失败遗留的 chunk WAV 不能被断点续传当主音频捡走（mtime 更新也不行）。

        捡走 chunk = 只转录 5 分钟 + 分片检查点恢复链失效。
        """
        from main import _handle_url_input

        leftover_chunk = tmp_path / 'prev_audio_chunk002.wav'
        leftover_chunk.write_bytes(b'x' * 4096)
        import os
        os.utime(leftover_chunk, (2_000_000_000, 2_000_000_000))  # 比原始音频新得多
        original = tmp_path / 'prev_audio.m4a'
        original.write_bytes(b'y' * 4096)

        with ExitStack() as stack:
            _enter_bili_common(stack)
            stack.enter_context(
                patch('services.downloader.extract_bilibili_subtitles', new_callable=AsyncMock,
                      return_value={'success': False, 'message': 'no_subtitle'}))
            m_dl = stack.enter_context(patch('services.downloader.download_video', new_callable=AsyncMock))
            stack.enter_context(
                patch('services.transcriber.transcribe_audio', new_callable=AsyncMock,
                      return_value=('t.srt', 't.txt', 2)))
            _srt, _txt, _seg, _title, _platform, files, _meta = await _handle_url_input(
                BILI_URL, tmp_path, MagicMock(), 'j1', _opts(_retry=True), 'base', False, 'zh')

        m_dl.assert_not_called()
        assert files['audio'] == str(original)    # 选中的是原始音频，不是 chunk
class TestTranscriptMeta:
    """结果可解释性（2026-10-07）：transcript_meta 覆盖来源/引擎/缓存/降级。"""

    async def test_bilibili_subtitle_check_skipped_when_disabled(self, tmp_path):
        """subtitle_check=false：直接采用平台字幕，meta 标记 skipped。"""
        from main import _handle_url_input

        sub_srt = tmp_path / 'bilibili_subtitle.srt'
        sub_srt.write_text('1\n00:00:00,000 --> 00:00:06,000\n字幕\n', encoding='utf-8')
        sub_txt = tmp_path / 'bilibili_subtitle.txt'
        sub_txt.write_text('[00:00 - 00:06] 字幕\n', encoding='utf-8')

        with ExitStack() as stack:
            _enter_bili_common(stack)
            stack.enter_context(
                patch('services.downloader.extract_bilibili_subtitles', new_callable=AsyncMock,
                      return_value={'success': True, 'srt_path': sub_srt, 'txt_path': sub_txt,
                                    'title': '标题', 'message': 'ok'}))
            _srt, _txt, _seg, _title, _platform, _files, meta = await _handle_url_input(
                BILI_URL, tmp_path, MagicMock(), 'j1', _opts(subtitle_check=False),
                'base', False, 'zh')

        assert meta == {'source': 'platform_subtitle', 'subtitle_from': 'bilibili',
                        'subtitle_check': 'skipped'}

    async def test_bilibili_subtitle_check_error_keeps_subtitle_meta(self, tmp_path):
        """抽查异常按原结果使用字幕：meta 标记 unavailable（可解释性优先）。"""
        from main import _handle_url_input

        sub_srt = tmp_path / 'bilibili_subtitle.srt'
        sub_srt.write_text('1\n00:00:00,000 --> 00:00:06,000\n字幕\n', encoding='utf-8')
        sub_txt = tmp_path / 'bilibili_subtitle.txt'
        sub_txt.write_text('[00:00 - 00:06] 字幕\n', encoding='utf-8')

        with ExitStack() as stack:
            _enter_bili_common(stack)
            stack.enter_context(
                patch('services.downloader.extract_bilibili_subtitles', new_callable=AsyncMock,
                      return_value={'success': True, 'srt_path': sub_srt, 'txt_path': sub_txt,
                                    'title': '标题', 'message': 'ok'}))
            stack.enter_context(
                patch('services.downloader.download_video', new_callable=AsyncMock,
                      side_effect=RuntimeError('下载器不可用')))
            _srt, _txt, _seg, _title, _platform, _files, meta = await _handle_url_input(
                BILI_URL, tmp_path, MagicMock(), 'j1', _opts(), 'base', False, 'zh')

        assert meta == {'source': 'platform_subtitle', 'subtitle_from': 'bilibili',
                        'subtitle_check': 'unavailable'}

    async def test_youtube_subtitle_success_records_source(self, tmp_path):
        """YouTube 原文字幕抓取成功：meta 标记 platform_subtitle/youtube（无抽查环节）。"""
        from main import _handle_url_input

        sub_srt = tmp_path / 'yt.srt'
        sub_srt.write_text('1\n00:00:00,000 --> 00:00:06,000\ncc\n', encoding='utf-8')
        sub_txt = tmp_path / 'yt.txt'
        sub_txt.write_text('cc\n', encoding='utf-8')

        with ExitStack() as stack:
            stack.enter_context(
                patch('services.downloader.detect_platform', return_value=('YouTube', True)))
            stack.enter_context(
                patch('services.downloader.extract_youtube_subtitles', new_callable=AsyncMock,
                      return_value={'success': True, 'srt_path': sub_srt, 'txt_path': sub_txt,
                                    'title': 'YT 标题', 'message': 'ok'}))
            _srt, _txt, _seg, _title, _platform, _files, meta = await _handle_url_input(
                'https://www.youtube.com/watch?v=test', tmp_path, MagicMock(), 'j1',
                _opts(), 'base', False, 'zh')

        assert meta == {'source': 'platform_subtitle', 'subtitle_from': 'youtube'}

    async def test_engine_degradation_passthrough_in_meta(self, tmp_path):
        """请求 cloud 实际落 cloud_free：transcribe_audio 的 info 原样并入 meta。"""
        from main import _handle_url_input

        audio = tmp_path / 'v.m4a'
        audio.write_bytes(b'x' * 2048)

        async def fake_transcribe(*args, **kwargs):
            info = kwargs.get('info')
            if info is not None:
                info.update({
                    'engine': 'cloud_free', 'engine_label': '云端 XingChen（免费）',
                    'requested_engine': 'cloud', 'cache': 'miss', 'degraded': True,
                })
            return ('t.srt', 't.txt', 3)

        with ExitStack() as stack:
            _enter_bili_common(stack)
            stack.enter_context(
                patch('services.downloader.extract_bilibili_subtitles', new_callable=AsyncMock,
                      return_value={'success': False, 'message': 'no_subtitle'}))
            stack.enter_context(
                patch('services.downloader.download_video', new_callable=AsyncMock,
                      return_value=(str(audio), '标题', '哔哩哔哩')))
            stack.enter_context(
                patch('services.transcriber.transcribe_audio',
                      new=AsyncMock(side_effect=fake_transcribe)))
            _srt, _txt, _seg, _title, _platform, _files, meta = await _handle_url_input(
                BILI_URL, tmp_path, MagicMock(), 'j1', _opts(transcribe_engine='cloud'),
                'base', False, 'zh')

        assert meta['source'] == 'transcription'
        assert meta['engine'] == 'cloud_free'
        assert meta['requested_engine'] == 'cloud'
        assert meta['degraded'] is True
        assert meta['cache'] == 'miss'

    async def test_resume_reuse_records_resume_meta(self, tmp_path):
        """断点续传复用已有转录文件：meta 标记 resume=true，不再转录。"""
        from main import _handle_url_input

        existing = tmp_path / 'prev_audio.m4a'
        existing.write_bytes(b'x' * 4096)
        (tmp_path / 'prev_audio.srt').write_text(
            '1\n00:00:00,000 --> 00:00:02,000\n内容充实的字幕行\n' * 10, encoding='utf-8')
        (tmp_path / 'prev_audio.txt').write_text('内容充实的字幕行\n' * 20, encoding='utf-8')

        with ExitStack() as stack:
            _enter_bili_common(stack)
            stack.enter_context(
                patch('services.downloader.extract_bilibili_subtitles', new_callable=AsyncMock,
                      return_value={'success': False, 'message': 'no_subtitle'}))
            m_tr = stack.enter_context(
                patch('services.transcriber.transcribe_audio', new_callable=AsyncMock))
            _srt, _txt, _seg, _title, _platform, _files, meta = await _handle_url_input(
                BILI_URL, tmp_path, MagicMock(), 'j1', _opts(_retry=True), 'base', False, 'zh')

        m_tr.assert_not_called()
        assert meta == {'source': 'transcription', 'resume': True}
        assert existing.exists()


class TestPlatformSubtitleCache:
    """平台字幕缓存（2026-10-11）：命中即跳过探测/抽查；只有已验证正确才写回。

    背景：上次抽查通过的正确字幕若不入池，重新处理时这次平台侧瞬态失败
    （B站 412 / 残字幕）就会白白回退一次全量转录。
    """

    def _sub_files(self, tmp_path):
        sub_srt = tmp_path / 'bilibili_subtitle.srt'
        sub_srt.write_text('1\n00:00:00,000 --> 00:00:06,000\n对得上的字幕\n', encoding='utf-8')
        sub_txt = tmp_path / 'bilibili_subtitle.txt'
        sub_txt.write_text('[00:00 - 00:06] 对得上的字幕\n', encoding='utf-8')
        return sub_srt, sub_txt

    async def test_cache_hit_skips_probe_and_check(self, tmp_path):
        """命中缓存：不调字幕提取、不下载抽查片段、不转录，meta 标记 cached。"""
        from main import _handle_url_input

        cached_srt = tmp_path / 'cached.srt'
        cached_srt.write_text('1\n00:00:00,000 --> 00:00:06,000\n缓存字幕\n', encoding='utf-8')
        cached_txt = tmp_path / 'cached.txt'
        cached_txt.write_text('[00:00 - 00:06] 缓存字幕\n', encoding='utf-8')

        with ExitStack() as stack:
            _enter_bili_common(stack)
            m_extract = stack.enter_context(
                patch('services.downloader.extract_bilibili_subtitles', new_callable=AsyncMock))
            m_hit = stack.enter_context(
                patch('services.ai_cache.lookup_subtitle',
                      return_value={'srt_path': str(cached_srt), 'txt_path': str(cached_txt),
                                    'title': '缓存标题', 'subtitle_from': 'bilibili',
                                    'seg_count': 0, 'created_at': '2026-10-11 10:00:00'}))
            m_dl = stack.enter_context(
                patch('services.downloader.download_video', new_callable=AsyncMock))
            m_tr = stack.enter_context(
                patch('services.transcriber.transcribe_audio', new_callable=AsyncMock))
            srt, txt, _seg, title, _platform, _files, meta = await _handle_url_input(
                BILI_URL, tmp_path, MagicMock(), 'j1', _opts(), 'base', False, 'zh')

        m_hit.assert_called_once()
        assert m_hit.call_args.args[0] == 'bilibili:BV1xx411c7mD'
        assert m_hit.call_args.args[1] == 'zh'
        m_extract.assert_not_called()      # 不重新探测字幕
        m_dl.assert_not_called()           # 不下载 90s 抽查片段
        m_tr.assert_not_called()           # 不转录
        assert srt == str(cached_srt) and txt == str(cached_txt)
        assert title == '缓存标题'
        assert meta == {'source': 'platform_subtitle', 'subtitle_from': 'bilibili',
                        'subtitle_check': 'cached'}

    async def test_check_passed_writes_cache(self, tmp_path):
        """抽查通过 → 以「平台:视频ID + 语言」为键写回缓存。"""
        from main import _handle_url_input

        sample = tmp_path / 'sample.m4a'
        sample.write_bytes(b'x' * 2048)
        sub_srt, sub_txt = self._sub_files(tmp_path)

        with ExitStack() as stack:
            _enter_bili_common(stack)
            stack.enter_context(
                patch('services.downloader.extract_bilibili_subtitles', new_callable=AsyncMock,
                      return_value={'success': True, 'srt_path': sub_srt, 'txt_path': sub_txt,
                                    'title': '标题', 'message': 'ok'}))
            stack.enter_context(
                patch('services.downloader.verify_subtitle_against_audio', new_callable=AsyncMock,
                      return_value=(True, '相似度 0.90')))
            stack.enter_context(
                patch('services.downloader.download_video', new_callable=AsyncMock,
                      return_value=(str(sample), '', '')))
            m_store = stack.enter_context(patch('services.ai_cache.store_subtitle'))
            _srt, _txt, _seg, _title, _platform, _files, meta = await _handle_url_input(
                BILI_URL, tmp_path, MagicMock(), 'j1', _opts(), 'base', False, 'zh')

        assert meta['subtitle_check'] == 'passed'
        m_store.assert_called_once()
        args, kwargs = m_store.call_args
        assert args[0] == 'bilibili:BV1xx411c7mD'
        assert args[1] == 'zh'
        assert kwargs.get('subtitle_from') == 'bilibili'
        assert kwargs.get('title') == '标题'

    async def test_check_unavailable_does_not_store(self, tmp_path):
        """抽查异常（未能验证对错）→ 不写缓存，下次仍会重新抽查（宁缺勿错）。"""
        from main import _handle_url_input

        sub_srt, sub_txt = self._sub_files(tmp_path)

        with ExitStack() as stack:
            _enter_bili_common(stack)
            stack.enter_context(
                patch('services.downloader.extract_bilibili_subtitles', new_callable=AsyncMock,
                      return_value={'success': True, 'srt_path': sub_srt, 'txt_path': sub_txt,
                                    'title': '标题', 'message': 'ok'}))
            stack.enter_context(
                patch('services.downloader.download_video', new_callable=AsyncMock,
                      side_effect=RuntimeError('下载器不可用')))
            m_store = stack.enter_context(patch('services.ai_cache.store_subtitle'))
            _srt, _txt, _seg, _title, _platform, _files, meta = await _handle_url_input(
                BILI_URL, tmp_path, MagicMock(), 'j1', _opts(), 'base', False, 'zh')

        assert meta['subtitle_check'] == 'unavailable'
        m_store.assert_not_called()

    async def test_check_failed_does_not_store(self, tmp_path):
        """抽查未通过（疑似错位）→ 字幕被丢弃，不写缓存。"""
        from main import _handle_url_input

        sample = tmp_path / 'sample.m4a'
        sample.write_bytes(b'x' * 2048)
        full = tmp_path / 'full.m4a'
        full.write_bytes(b'y' * 4096)
        sub_srt, sub_txt = self._sub_files(tmp_path)

        with ExitStack() as stack:
            _enter_bili_common(stack)
            stack.enter_context(
                patch('services.downloader.extract_bilibili_subtitles', new_callable=AsyncMock,
                      return_value={'success': True, 'srt_path': sub_srt, 'txt_path': sub_txt,
                                    'title': '旧视频标题', 'message': 'ok'}))
            stack.enter_context(
                patch('services.downloader.verify_subtitle_against_audio', new_callable=AsyncMock,
                      return_value=(False, '相似度 0.05（错位）')))
            stack.enter_context(
                patch('services.downloader.download_video', new_callable=AsyncMock,
                      side_effect=[(str(sample), '', ''), (str(full), '标题', '哔哩哔哩')]))
            stack.enter_context(
                patch('services.transcriber.transcribe_audio', new_callable=AsyncMock,
                      return_value=('t.srt', 't.txt', 5)))
            m_store = stack.enter_context(patch('services.ai_cache.store_subtitle'))
            await _handle_url_input(BILI_URL, tmp_path, MagicMock(), 'j1', _opts(), 'base', False, 'zh')

        m_store.assert_not_called()

    async def test_bypass_cache_skips_read_but_still_stores(self, tmp_path):
        """忽略缓存 = 只跳过读取：重新获取字幕，验证通过后仍写回（与 ASR 缓存语义一致）。"""
        from main import _handle_url_input

        sample = tmp_path / 'sample.m4a'
        sample.write_bytes(b'x' * 2048)
        sub_srt, sub_txt = self._sub_files(tmp_path)

        with ExitStack() as stack:
            _enter_bili_common(stack)
            m_hit = stack.enter_context(patch('services.ai_cache.lookup_subtitle'))
            stack.enter_context(
                patch('services.downloader.extract_bilibili_subtitles', new_callable=AsyncMock,
                      return_value={'success': True, 'srt_path': sub_srt, 'txt_path': sub_txt,
                                    'title': '标题', 'message': 'ok'}))
            stack.enter_context(
                patch('services.downloader.verify_subtitle_against_audio', new_callable=AsyncMock,
                      return_value=(True, '相似度 0.90')))
            stack.enter_context(
                patch('services.downloader.download_video', new_callable=AsyncMock,
                      return_value=(str(sample), '', '')))
            m_store = stack.enter_context(patch('services.ai_cache.store_subtitle'))
            await _handle_url_input(BILI_URL, tmp_path, MagicMock(), 'j1',
                                    _opts(bypass_cache=True), 'base', False, 'zh')

        m_hit.assert_not_called()
        m_store.assert_called_once()

    async def test_force_transcribe_neither_reads_nor_writes(self, tmp_path):
        """强制转录：用户明确要转录 → 字幕缓存不读也不写。"""
        from main import _handle_url_input

        audio = tmp_path / 'v.m4a'
        audio.write_bytes(b'x' * 4096)

        with ExitStack() as stack:
            _enter_bili_common(stack)
            m_hit = stack.enter_context(patch('services.ai_cache.lookup_subtitle'))
            m_store = stack.enter_context(patch('services.ai_cache.store_subtitle'))
            stack.enter_context(
                patch('services.downloader.download_video', new_callable=AsyncMock,
                      return_value=(str(audio), '标题', '哔哩哔哩')))
            stack.enter_context(
                patch('services.transcriber.transcribe_audio', new_callable=AsyncMock,
                      return_value=('t.srt', 't.txt', 3)))
            _srt, _txt, _seg, _title, _platform, _files, meta = await _handle_url_input(
                BILI_URL, tmp_path, MagicMock(), 'j1',
                _opts(force_transcribe=True), 'base', False, 'zh')

        m_hit.assert_not_called()
        m_store.assert_not_called()
        assert meta == {'source': 'transcription'}

    async def test_youtube_success_writes_cache(self, tmp_path):
        """YouTube 无抽查环节：抓取成功即注入缓存（来源标记 youtube）。"""
        from main import _handle_url_input

        sub_srt = tmp_path / 'yt.srt'
        sub_srt.write_text('1\n00:00:00,000 --> 00:00:06,000\ncc\n', encoding='utf-8')
        sub_txt = tmp_path / 'yt.txt'
        sub_txt.write_text('cc\n', encoding='utf-8')

        with ExitStack() as stack:
            stack.enter_context(
                patch('services.downloader.detect_platform', return_value=('YouTube', True)))
            stack.enter_context(
                patch('services.downloader.extract_youtube_subtitles', new_callable=AsyncMock,
                      return_value={'success': True, 'srt_path': sub_srt, 'txt_path': sub_txt,
                                    'title': 'YT 标题', 'message': 'ok'}))
            stack.enter_context(patch('socket_handlers.check_cancelled', return_value=False))
            stack.enter_context(patch('main.emit_log', new_callable=AsyncMock))
            stack.enter_context(patch('main.update_job'))
            m_store = stack.enter_context(patch('services.ai_cache.store_subtitle'))
            await _handle_url_input('https://www.youtube.com/watch?v=cachetest11', tmp_path,
                                    MagicMock(), 'j1', _opts(), 'base', False, 'zh')

        m_store.assert_called_once()
        assert m_store.call_args.args[0] == 'youtube:cachetest11'
        assert m_store.call_args.kwargs.get('subtitle_from') == 'youtube'
