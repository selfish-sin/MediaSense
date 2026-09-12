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
            srt, txt, seg, _title, _platform, files = await _handle_url_input(
                BILI_URL, tmp_path, MagicMock(), 'j1', _opts(), 'base', False, 'zh')

        m_dl.assert_called_once()
        m_tr.assert_called_once()
        assert files['audio'] == str(audio)
        assert (srt, txt, seg) == ('t.srt', 't.txt', 3)

    async def test_subtitle_check_fail_reuses_audio_and_registers(self, tmp_path):
        """B站字幕抽查失败：丢弃错位字幕、复用抽查音频转录，files['audio'] 必须登记。"""
        from main import _handle_url_input

        audio = tmp_path / 'video.m4a'
        audio.write_bytes(b'x' * 2048)
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
                      return_value=(str(audio), '', '')))
            m_tr = stack.enter_context(
                patch('services.transcriber.transcribe_audio', new_callable=AsyncMock,
                      return_value=('t.srt', 't.txt', 5)))
            _srt, _txt, _seg, _title, _platform, files = await _handle_url_input(
                BILI_URL, tmp_path, MagicMock(), 'j1', _opts(), 'base', False, 'zh')

        m_dl.assert_called_once()   # 只下载一次：复用抽查音频，无二次下载
        m_tr.assert_called_once()
        assert files['audio'] == str(audio)
        assert not sub_srt.exists() and not sub_txt.exists()   # 错位字幕已丢弃

    async def test_subtitle_check_pass_registers_audio_as_material(self, tmp_path):
        """B站字幕抽查通过：不转录，但抽查已下载的音频登记为素材（不闲置）。"""
        from main import _handle_url_input

        audio = tmp_path / 'video.m4a'
        audio.write_bytes(b'x' * 2048)
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
            stack.enter_context(
                patch('services.downloader.download_video', new_callable=AsyncMock,
                      return_value=(str(audio), '', '')))
            m_tr = stack.enter_context(
                patch('services.transcriber.transcribe_audio', new_callable=AsyncMock))
            srt, _txt, _seg, _title, _platform, files = await _handle_url_input(
                BILI_URL, tmp_path, MagicMock(), 'j1', _opts(), 'base', False, 'zh')

        m_tr.assert_not_called()                     # 字幕可用，不转录
        assert str(srt) == str(sub_srt)
        assert files['audio'] == str(audio)          # 已下载音频登记为素材


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
            _srt, _txt, _seg, _title, _platform, files = await _handle_url_input(
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
            _srt, _txt, _seg, _title, _platform, files = await _handle_url_input(
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
            _srt, _txt, _seg, _title, _platform, files = await _handle_url_input(
                BILI_URL, tmp_path, MagicMock(), 'j1', _opts(_retry=True), 'base', False, 'zh')

        m_dl.assert_not_called()
        assert files['video'] == str(existing)
        assert 'audio' not in files
