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
    def test_player_api_prefers_wbi_and_returns_subtitles(self, monkeypatch):
        calls = []

        class FakeResponse:
            def json(self):
                return {"code": 0, "data": {"subtitle": {"subtitles": [{"lan": "zh-CN"}]}}}

        monkeypatch.setattr("services.downloader.load_cookies", lambda: {})
        monkeypatch.setattr(
            "services.downloader._build_wbi_url",
            lambda base, params: f"{base}?signed=1",
        )

        def fake_get(url, headers, timeout):
            calls.append(url)
            return FakeResponse()

        monkeypatch.setattr("services.downloader.httpx.get", fake_get)

        result = _fetch_subtitles_via_player_v2(1, 2, "BV1abc123456")

        assert result["subtitles"][0]["lan"] == "zh-CN"
        assert calls[0].startswith("https://api.bilibili.com/x/player/wbi/v2")

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
