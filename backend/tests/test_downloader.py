"""
downloader 下载服务纯逻辑函数测试
"""
import pytest
from types import SimpleNamespace
from pathlib import Path
from services.downloader import (
    detect_platform,
    _format_srt_time,
    _format_mmss,
    _srt_to_txt,
    _write_subtitle_files,
    extract_youtube_subtitles,
    handle_subtitle_file,
    _extract_bilibili_subtitles_ytdlp,
    _fetch_subtitles_via_player_v2,
    _subtitle_head_text,
    _text_similarity,
    verify_subtitle_against_audio,
    _bili_headers,
    _ensure_buvid,
    PLATFORM_CONFIG,
)


class TestDetectPlatform:
    def test_youtube_com(self):
        name, need_proxy = detect_platform("https://www.youtube.com/watch?v=abc")
        assert name == "YouTube"
        assert need_proxy is True

    def test_youtu_be(self):
        name, _ = detect_platform("https://youtu.be/abc123")
        assert name == "YouTube"

    def test_bilibili(self):
        name, need_proxy = detect_platform("https://www.bilibili.com/video/BVxxx")
        assert name == "哔哩哔哩"
        assert need_proxy is False

    def test_b23_tv(self):
        name, _ = detect_platform("https://b23.tv/abc")
        assert name == "哔哩哔哩"

    def test_twitter(self):
        name, _ = detect_platform("https://twitter.com/user/status/123")
        assert name == "Twitter/X"

    def test_x_com(self):
        name, _ = detect_platform("https://x.com/user/status/123")
        assert name == "Twitter/X"

    def test_unknown(self):
        name, need_proxy = detect_platform("https://example.com/video")
        assert name == "未知平台"
        assert need_proxy is True

    def test_invalid_url(self):
        name, _ = detect_platform("not a url")
        assert name == "未知平台"

    def test_subdomain(self):
        name, _ = detect_platform("https://sub.youtube.com/watch")
        assert name == "YouTube"

    def test_platform_config_coverage(self):
        for domain, cfg in PLATFORM_CONFIG.items():
            assert "name" in cfg
            assert "need_proxy" in cfg


class TestFormatSrtTime:
    def test_zero(self):
        assert _format_srt_time(0) == "00:00:00,000"

    def test_one_hour(self):
        assert _format_srt_time(3661.5) == "01:01:01,500"

    def test_milliseconds(self):
        result = _format_srt_time(1.234)
        assert result.startswith("00:00:01,")
        assert "234" in result

    def test_large_time(self):
        result = _format_srt_time(7321.999)
        assert result.startswith("02:")


class TestFormatMmss:
    def test_zero(self):
        assert _format_mmss(0) == "00:00"

    def test_one_minute(self):
        assert _format_mmss(60) == "01:00"

    def test_rounding(self):
        assert _format_mmss(59.4) == "00:59"
        assert _format_mmss(59.6) == "01:00"


class TestSrtToTxt:
    def test_converts_basic(self, tmp_path):
        srt_path = tmp_path / "test.srt"
        txt_path = tmp_path / "test.txt"
        srt_path.write_text("""1
00:01:00,000 --> 00:01:05,000
Hello world

2
00:01:06,000 --> 00:01:10,000
Second line
""", encoding="utf-8")
        _srt_to_txt(srt_path, txt_path)
        content = txt_path.read_text("utf-8")
        assert "[00:01:00]" in content
        assert "Hello world" in content
        assert "[00:01:06]" in content
        assert "Second line" in content

    def test_skips_short_blocks(self, tmp_path):
        srt_path = tmp_path / "test.srt"
        txt_path = tmp_path / "test.txt"
        srt_path.write_text("""1
00:01:00,000
""", encoding="utf-8")
        _srt_to_txt(srt_path, txt_path)
        content = txt_path.read_text("utf-8")
        assert content == "" or content == "\n"


class TestWriteSubtitleFiles:
    def test_writes_srt_and_txt(self, tmp_path):
        srt_path = tmp_path / "test.srt"
        txt_path = tmp_path / "test.txt"
        sub_json = {
            "body": [
                {"from": 0, "to": 5, "content": "hello"},
                {"from": 5, "to": 10, "content": "world"},
            ]
        }
        _write_subtitle_files(sub_json, srt_path, txt_path)
        assert srt_path.exists()
        assert txt_path.exists()
        srt_content = srt_path.read_text("utf-8")
        assert "1" in srt_content
        assert "hello" in srt_content
        assert "world" in srt_content


class TestUploadedSubtitleFiles:
    @pytest.mark.asyncio
    async def test_utf16_srt_preserves_timestamps(self, tmp_path):
        source = tmp_path / "字幕.srt"
        source.write_text(
            "1\n00:01:02,000 --> 00:01:05,000\n第一句\n\n",
            encoding="utf-16",
        )

        txt_path, srt_path = await handle_subtitle_file(str(source), tmp_path / "job")

        assert srt_path.read_text("utf-8").startswith("1\n00:01:02,000")
        assert "[00:01:02]" in txt_path.read_text("utf-8")
        assert "第一句" in txt_path.read_text("utf-8")

    @pytest.mark.asyncio
    async def test_invalid_subtitle_is_rejected(self, tmp_path):
        source = tmp_path / "bad.srt"
        source.write_text("这不是字幕", encoding="utf-8")

        with pytest.raises(Exception, match="时间轴"):
            await handle_subtitle_file(str(source), tmp_path / "job")


class TestExtractYoutubeSubtitles:
    @pytest.mark.asyncio
    async def test_accepts_valid_srt_when_ytdlp_returns_nonzero(self, tmp_path, monkeypatch):
        def fake_run(cmd, capture_output, timeout):
            (tmp_path / "video.en.srt").write_text(
                "1\n00:00:00,000 --> 00:00:02,000\nHello from YouTube subtitles\n",
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=1, stderr=b"partial subtitle download failure")

        monkeypatch.setattr("services.downloader.subprocess.run", fake_run)

        result = await extract_youtube_subtitles(
            "https://www.youtube.com/watch?v=abcdefghijk", tmp_path, target_lang="en"
        )

        assert result["success"] is True
        assert result["srt_path"].name == "video.en.srt"
        assert result["txt_path"].exists()

    @pytest.mark.asyncio
    async def test_returns_failure_when_no_valid_srt_is_created(self, tmp_path, monkeypatch):
        calls = []

        def fake_run(cmd, capture_output, timeout):
            calls.append(cmd)
            return SimpleNamespace(returncode=1, stderr=b"no subtitles")

        monkeypatch.setattr("services.downloader.subprocess.run", fake_run)

        result = await extract_youtube_subtitles(
            "https://www.youtube.com/watch?v=abcdefghijk", tmp_path, target_lang="zh"
        )

        assert result["success"] is False
        assert len(calls) == 2
        assert all("--ignore-errors" in cmd for cmd in calls)

    @pytest.mark.asyncio
    async def test_prefers_requested_language_over_newest_file(self, tmp_path, monkeypatch):
        def fake_run(cmd, capture_output, timeout):
            (tmp_path / "video.zh-Hans.srt").write_text(
                "1\n00:00:00,000 --> 00:00:02,000\n中文字幕内容足够长\n",
                encoding="utf-8",
            )
            (tmp_path / "video.en.srt").write_text(
                "1\n00:00:00,000 --> 00:00:02,000\nEnglish subtitles are also available\n",
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0, stderr=b"")

        monkeypatch.setattr("services.downloader.subprocess.run", fake_run)

        result = await extract_youtube_subtitles(
            "https://www.youtube.com/watch?v=abcdefghijk", tmp_path, target_lang="zh"
        )

        assert result["success"] is True
        assert result["srt_path"].name == "video.zh-Hans.srt"


class TestExtractBilibiliSubtitlesYtdlp:
    def test_player_api_prefers_legacy_v2_then_wbi(self, monkeypatch):
        calls = []

        class FakeResponse:
            def json(self):
                return {"code": 0, "data": {"subtitle": {"subtitles": [{"lan": "zh-CN"}]}}}

        monkeypatch.setattr("services.downloader.load_cookies", lambda: {})
        monkeypatch.setattr("services.downloader._throttle_bili_api", lambda: None)

        def fake_get(url, headers, timeout):
            calls.append(url)
            return FakeResponse()

        monkeypatch.setattr("services.downloader.httpx.get", fake_get)

        result = _fetch_subtitles_via_player_v2(1, 2, "BV1abc123456")

        assert result[0]["subtitles"][0]["lan"] == "zh-CN"
        assert result[2] is True
        # 接口顺序反转：实测更稳的旧版 player/v2 优先，wbi/v2 只作备用
        assert calls[0].startswith("https://api.bilibili.com/x/player/v2")
        assert "wbi" not in calls[0]

    def test_player_api_confirms_no_subtitle_when_code_ok(self, monkeypatch):
        class FakeResponse:
            def json(self):
                return {"code": 0, "data": {"subtitle": {"subtitles": []}}}

        monkeypatch.setattr("services.downloader.load_cookies", lambda: {})
        monkeypatch.setattr("services.downloader._throttle_bili_api", lambda: None)
        monkeypatch.setattr(
            "services.downloader.httpx.get",
            lambda url, headers, timeout: FakeResponse(),
        )

        subtitle, tl, api_ok = _fetch_subtitles_via_player_v2(1, 2, "BV1abc123456")

        assert subtitle == {}
        assert api_ok is True  # B站正常响应但无字幕：跳过 yt-dlp 兜底，直接转 Whisper

    def test_player_api_marks_banned_on_412(self, monkeypatch):
        class FakeResponse:
            def json(self):
                return {"code": -412, "message": "request was banned"}

        monkeypatch.setattr("services.downloader.load_cookies", lambda: {})
        monkeypatch.setattr("services.downloader._throttle_bili_api", lambda: None)
        monkeypatch.setattr(
            "services.downloader.httpx.get",
            lambda url, headers, timeout: FakeResponse(),
        )

        subtitle, tl, api_ok = _fetch_subtitles_via_player_v2(1, 2, "BV1abc123456")

        assert subtitle == {}
        assert api_ok is False  # 接口全部被风控：状态未知，必须走 yt-dlp/Whisper 兜底


class TestBiliFingerprint:
    """浏览器指纹（完整 UA + buvid）与 B站 API 限流纯逻辑测试"""

    def test_headers_include_full_ua_buvid_and_referer(self, monkeypatch):
        monkeypatch.setattr(
            "services.downloader.load_cookies", lambda: {"SESSDATA": "test-sessdata"}
        )
        headers = _bili_headers("BV1abc123456")
        assert "Chrome/126" in headers["User-Agent"]
        assert headers["Referer"] == "https://www.bilibili.com/video/BV1abc123456"
        assert "buvid3=" in headers["Cookie"] and "buvid4=" in headers["Cookie"]
        assert "SESSDATA=test-sessdata" in headers["Cookie"]

    def test_buvid_stable_per_process(self):
        first = _ensure_buvid()
        second = _ensure_buvid()
        assert first is second  # 进程级缓存，会话内保持一致
        assert first["buvid3"].endswith("infoc")
        assert first["buvid4"].endswith("infoc")

    @pytest.mark.asyncio
    async def test_accepts_valid_srt_when_ytdlp_returns_nonzero(self, tmp_path, monkeypatch):
        def fake_run(cmd, capture_output, timeout):
            (tmp_path / "video.zh-CN.srt").write_text(
                "1\n00:00:00,000 --> 00:00:02,000\nB站字幕内容足够长\n",
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=1, stderr=b"partial subtitle download failure")

        monkeypatch.setattr("services.downloader.subprocess.run", fake_run)

        result = await _extract_bilibili_subtitles_ytdlp(
            "https://www.bilibili.com/video/BV1abc123456", tmp_path
        )

        assert result["success"] is True
        assert result["srt_path"].name == "video.zh-CN.srt"
        assert result["txt_path"].exists()


class TestSubtitleContentCheck:
    """B站字幕内容抽查（防旧/错误字幕）纯逻辑测试"""

    def test_head_text_respects_max_seconds(self, tmp_path):
        txt = tmp_path / "bilibili_subtitle.txt"
        txt.write_text(
            "[00:00 - 00:03] 第一句开场白\n"
            "[00:03 - 00:10] 第二句引入主题\n"
            "[01:40 - 01:45] 超过90秒不应被取到\n",
            encoding="utf-8",
        )
        head = _subtitle_head_text(txt, max_seconds=90)
        assert "第一句开场白" in head
        assert "第二句引入主题" in head
        assert "超过90秒" not in head

    def test_head_text_falls_back_for_plain_lines(self, tmp_path):
        txt = tmp_path / "plain.txt"
        txt.write_text("没有时间戳的文本行一\n没有时间戳的文本行二\n", encoding="utf-8")
        head = _subtitle_head_text(txt, max_seconds=90)
        assert "文本行一" in head and "文本行二" in head

    def test_text_similarity_same_content_high(self):
        assert _text_similarity("今天我们讲芯片产业的历史发展脉络", "今天我们讲芯片产业的历史发展脉络") > 0.9

    def test_text_similarity_diff_content_low(self):
        assert _text_similarity("芯片产业为何走向失败的历史分析", "今天天气很好我们出去散步吧") < 0.2

    @pytest.mark.asyncio
    async def test_verify_keeps_subtitle_when_similar(self, tmp_path, monkeypatch):
        txt = tmp_path / "sub.txt"
        txt.write_text(
            "[00:00 - 00:05] 芯片简史第一集，我们开讲\n"
            "[00:05 - 00:10] 今天要聊晶体管的发明与故事\n",
            encoding="utf-8",
        )
        # 模拟本地 Whisper 转写开头，内容与字幕开头一致（个别词/语气稍有出入）
        async def fake_to_thread(fn, *args):
            return "芯片简史第一集我们开讲，今天要聊晶体管的发明还有故事"
        monkeypatch.setattr("services.downloader.asyncio.to_thread", fake_to_thread)

        ok, detail = await verify_subtitle_against_audio(txt, "audio.m4a")
        assert ok is True
        assert "相似度" in detail

    @pytest.mark.asyncio
    async def test_verify_rejects_subtitle_when_content_differs(self, tmp_path, monkeypatch):
        txt = tmp_path / "sub.txt"
        txt.write_text(
            "[00:00 - 00:05] 苏联芯片产业为何走向失败\n[00:05 - 00:10] 巨人之殇的历史回顾\n",
            encoding="utf-8",
        )
        # 模拟本地 Whisper 转写开头是另一段内容（换源后的新音频）
        async def fake_to_thread(fn, *args):
            return "今天天气很好，我们出去散步，顺便买点菜回家做饭"
        monkeypatch.setattr("services.downloader.asyncio.to_thread", fake_to_thread)

        ok, _detail = await verify_subtitle_against_audio(txt, "audio.m4a")
        assert ok is False

    @pytest.mark.asyncio
    async def test_verify_keeps_subtitle_when_sampler_broken(self, tmp_path, monkeypatch):
        """Whisper 抽查不可用时保留原字幕，不让校验本身拖垮任务"""
        txt = tmp_path / "sub.txt"
        txt.write_text("[00:00 - 00:05] 内容\n", encoding="utf-8")

        def boom(path, max_seconds, language):
            raise RuntimeError("model missing")

        monkeypatch.setattr(
            "services.downloader.asyncio.to_thread", lambda fn, *a: boom(*a)
        )

        ok, detail = await verify_subtitle_against_audio(txt, "audio.m4a")
        assert ok is True
        assert "抽查不可用" in detail

    @pytest.mark.asyncio
    async def test_verify_cloud_first_skips_local(self, tmp_path, monkeypatch):
        """云端已配置且成功 → 直接用云端结果，本地 Whisper 不参与"""
        txt = tmp_path / "sub.txt"
        txt.write_text("[00:00 - 00:05] 芯片简史第一集，我们开讲\n", encoding="utf-8")
        sample = tmp_path / "s.mp3"
        sample.write_bytes(b"x")
        monkeypatch.setattr("services.cloud_asr.cloud_asr_configured", lambda: True)
        monkeypatch.setattr("services.cloud_asr.extract_sample",
                            lambda p, m: str(sample))
        monkeypatch.setattr("services.cloud_asr.transcribe_file",
                            lambda p, **kw: "芯片简史第一集，我们开讲")
        local_calls = []

        def fake_local(path, max_seconds, language, prompt):
            local_calls.append(1)
            return "本地不应被调用"

        monkeypatch.setattr("services.transcriber.transcribe_audio_sample", fake_local)

        ok, detail = await verify_subtitle_against_audio(txt, "audio.m4a")
        assert ok is True
        assert "云端" in detail
        assert local_calls == []
        assert not sample.exists()  # 云端抽查临时片段已清理

    @pytest.mark.asyncio
    async def test_verify_cloud_failure_falls_back_local(self, tmp_path, monkeypatch):
        """云端抽查失败 → 回退本地 Whisper base"""
        txt = tmp_path / "sub.txt"
        txt.write_text("[00:00 - 00:05] 芯片简史第一集，我们开讲\n", encoding="utf-8")
        monkeypatch.setattr("services.cloud_asr.cloud_asr_configured", lambda: True)
        monkeypatch.setattr(
            "services.cloud_asr.extract_sample",
            lambda p, m: (_ for _ in ()).throw(RuntimeError("ffmpeg gone")))
        monkeypatch.setattr(
            "services.transcriber.transcribe_audio_sample",
            lambda path, max_seconds, language, prompt: "芯片简史第一集，我们开讲")

        ok, detail = await verify_subtitle_against_audio(txt, "audio.m4a")
        assert ok is True
        assert "本地" in detail

    @pytest.mark.asyncio
    async def test_verify_cloud_not_configured_goes_local(self, tmp_path, monkeypatch):
        """云端未配置 → 本地抽查，日志说明走本地"""
        txt = tmp_path / "sub.txt"
        txt.write_text("[00:00 - 00:05] 芯片简史第一集，我们开讲\n", encoding="utf-8")
        monkeypatch.setattr("services.cloud_asr.cloud_asr_configured", lambda: False)
        monkeypatch.setattr(
            "services.transcriber.transcribe_audio_sample",
            lambda path, max_seconds, language, prompt: "芯片简史第一集，我们开讲")

        ok, detail = await verify_subtitle_against_audio(txt, "audio.m4a")
        assert ok is True
        assert "本地" in detail


class TestDownloadVideoSections:
    """download_video(max_seconds)：只取开头片段（--download-sections）+ 失败回退完整下载。"""

    @staticmethod
    def _fake_run(download_rcs, out_dir, calls):
        rc_iter = iter(download_rcs)

        def fake_run(cmd, capture_output, timeout):
            args = [str(c) for c in cmd]
            if '--dump-json' in args:            # 视频信息步骤：失败走默认 title
                return SimpleNamespace(returncode=1, stdout=b'', stderr=b'')
            calls.append(args)
            rc = next(rc_iter)
            if rc == 0:
                (out_dir / 'video.m4a').write_bytes(b'x' * 4096)
            return SimpleNamespace(returncode=rc, stdout=b'', stderr=b'boom')

        return fake_run

    async def test_max_seconds_adds_download_sections(self, tmp_path, monkeypatch):
        from services.downloader import download_video

        calls = []
        monkeypatch.setattr('services.downloader.subprocess.run',
                            self._fake_run([0], tmp_path, calls))
        result = await download_video(
            'https://www.bilibili.com/video/BVxxx', tmp_path,
            only_audio=True, max_seconds=90)

        assert result[0].endswith('video.m4a')
        assert len(calls) == 1
        assert '--download-sections' in calls[0]
        assert '*0-90' in calls[0]

    async def test_sections_failure_falls_back_to_full_download(self, tmp_path, monkeypatch):
        from services.downloader import download_video

        calls = []
        monkeypatch.setattr('services.downloader.subprocess.run',
                            self._fake_run([1, 0], tmp_path, calls))
        result = await download_video(
            'https://www.bilibili.com/video/BVxxx', tmp_path,
            only_audio=True, max_seconds=90)

        assert result[0].endswith('video.m4a')
        assert len(calls) == 2
        assert '--download-sections' in calls[0]
        assert '--download-sections' not in calls[1]   # 回退完整下载

    async def test_no_max_seconds_keeps_full_download_args(self, tmp_path, monkeypatch):
        from services.downloader import download_video

        calls = []
        monkeypatch.setattr('services.downloader.subprocess.run',
                            self._fake_run([0], tmp_path, calls))
        await download_video('https://www.bilibili.com/video/BVxxx', tmp_path, only_audio=True)

        assert len(calls) == 1
        assert '--download-sections' not in calls[0]
