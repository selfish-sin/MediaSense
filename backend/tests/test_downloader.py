"""
downloader 下载服务纯逻辑函数测试
"""
import asyncio
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
    _pick_zh_subtitle,
    _bili_headers,
    _ensure_buvid,
    _acquire_bilibili_subtitle,
    _dedupe_urls,
    _is_subtitle_body_incomplete,
    _is_subtitle_ready,
    _min_expected_coverage,
    _normalize_subtitle_url,
    _refresh_subtitle_urls,
    _resolve_video_target,
    _subtitle_body_stats,
    _urls_for_lan,
    _validate_subtitle_body,
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


class TestPickZhSubtitle:
    """B站字幕选择：CC（创作者上传）优先于 AI 字幕"""

    def test_cc_before_ai(self):
        subs = [
            {"lan": "ai-zh", "lan_doc": "AI字幕"},
            {"lan": "zh-CN", "lan_doc": "CC字幕"},
        ]
        picked = _pick_zh_subtitle(subs)
        assert picked[0]["lan"] == "zh-CN"  # CC 永远排 AI 前面

    def test_keeps_ai_when_no_cc(self):
        subs = [{"lan": "ai-zh"}, {"lan": "ai-zh-Hans"}]
        picked = _pick_zh_subtitle(subs)
        assert [s["lan"] for s in picked] == ["ai-zh", "ai-zh-Hans"]

    def test_never_picks_foreign_ai_as_zh(self):
        subs = [
            {"lan": "ai-en"},
            {"lan": "ai-jp"},
            {"lan": "zh-Hans"},
        ]
        picked = _pick_zh_subtitle(subs)
        assert picked[0]["lan"] == "zh-Hans"
        assert "ai-en" not in [s["lan"] for s in picked]

    def test_fallback_to_any_ai_for_foreign_video(self):
        subs = [{"lan": "ai-en"}, {"lan": "ai-jp"}]
        picked = _pick_zh_subtitle(subs)
        assert picked == subs  # 无中文字幕时，外文视频兜底任意 AI 字幕

    def test_empty_input_returns_empty(self):
        assert _pick_zh_subtitle([]) == []

    def test_simplified_preferred_over_traditional(self):
        """同类内简体优先：默认输出语言是简体中文，别优先拿繁体字幕"""
        subs = [
            {"lan": "ai-zh"},
            {"lan": "zh-TW"},
            {"lan": "ai-zh-Hans"},
            {"lan": "zh-CN"},
        ]
        picked = _pick_zh_subtitle(subs)
        assert [s["lan"] for s in picked] == ["zh-CN", "zh-TW", "ai-zh", "ai-zh-Hans"]

    def test_drops_ai_subtitle_still_generating(self):
        """AI 字幕生成中（ai_status!=2）时 body 为空/半成品，必须剔除"""
        subs = [
            {"lan": "ai-zh", "ai_status": 1},
            {"lan": "zh-CN"},
        ]
        assert [s["lan"] for s in _pick_zh_subtitle(subs)] == ["zh-CN"]

    def test_drops_generating_ai_when_it_is_the_only_track(self):
        assert _pick_zh_subtitle([{"lan": "ai-zh", "ai_status": 0}]) == []

    def test_keeps_ready_ai_and_cc_without_ai_status(self):
        subs = [{"lan": "ai-zh", "ai_status": 2}, {"lan": "zh-Hans"}]
        assert [s["lan"] for s in _pick_zh_subtitle(subs)] == ["zh-Hans", "ai-zh"]



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
        """云端可用且成功 → 直接用云端结果，本地 Whisper 不参与"""
        txt = tmp_path / "sub.txt"
        txt.write_text("[00:00 - 00:05] 芯片简史第一集，我们开讲\n", encoding="utf-8")
        sample = tmp_path / "s.mp3"
        sample.write_bytes(b"x")
        used = []
        monkeypatch.setattr("services.cloud_asr.preferred_provider", lambda: "xingchen")
        monkeypatch.setattr("services.cloud_asr.extract_sample",
                            lambda p, m: str(sample))
        monkeypatch.setattr(
            "services.cloud_asr.transcribe_file",
            lambda p, **kw: (used.append(kw.get("provider")), "芯片简史第一集，我们开讲")[1])
        local_calls = []

        def fake_local(path, max_seconds, language, prompt):
            local_calls.append(1)
            return "本地不应被调用"

        monkeypatch.setattr("services.transcriber.transcribe_audio_sample", fake_local)

        ok, detail = await verify_subtitle_against_audio(txt, "audio.m4a")
        assert ok is True
        assert "云端" in detail
        assert local_calls == []
        assert used == ["xingchen"]  # 抽查用槽位选择单点给的 provider（免费档优先）
        assert not sample.exists()  # 云端抽查临时片段已清理

    @pytest.mark.asyncio
    async def test_verify_cloud_failure_falls_back_local(self, tmp_path, monkeypatch):
        """云端抽查失败 → 回退本地 Whisper base"""
        txt = tmp_path / "sub.txt"
        txt.write_text("[00:00 - 00:05] 芯片简史第一集，我们开讲\n", encoding="utf-8")
        monkeypatch.setattr("services.cloud_asr.preferred_provider", lambda: "xingchen")
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
        """云端无可用 provider → 本地抽查，日志说明走本地"""
        txt = tmp_path / "sub.txt"
        txt.write_text("[00:00 - 00:05] 芯片简史第一集，我们开讲\n", encoding="utf-8")
        monkeypatch.setattr("services.cloud_asr.preferred_provider", lambda: None)
        monkeypatch.setattr(
            "services.transcriber.transcribe_audio_sample",
            lambda path, max_seconds, language, prompt: "芯片简史第一集，我们开讲")

        ok, detail = await verify_subtitle_against_audio(txt, "audio.m4a")
        assert ok is True
        assert "本地" in detail

    @pytest.mark.asyncio
    async def test_verify_cloud_failure_reason_visible_to_user(self, tmp_path, monkeypatch):
        """抽查失败必须把失败原因推给前端（此前只给异常类名，402 余额不足用户看不到）"""
        from services.cloud_asr import CloudASRConfigError

        txt = tmp_path / "sub.txt"
        txt.write_text("[00:00 - 00:05] 芯片简史第一集，我们开讲\n", encoding="utf-8")
        logs = []

        async def fake_emit_log(_sio, _job_id, message):
            logs.append(message)

        monkeypatch.setattr("services.cloud_asr.preferred_provider", lambda: "mimo")
        monkeypatch.setattr(
            "services.cloud_asr.extract_sample",
            lambda p, m: (_ for _ in ()).throw(
                CloudASRConfigError("云端 ASR 402: MiMo-ASR 账户余额不足/欠费（计费类错误，重试无意义）")))
        monkeypatch.setattr("socket_handlers.emit_log", fake_emit_log)
        monkeypatch.setattr(
            "services.transcriber.transcribe_audio_sample",
            lambda path, max_seconds, language, prompt: "芯片简史第一集，我们开讲")

        ok, _detail = await verify_subtitle_against_audio(
            txt, "audio.m4a", sio=object(), job_id="j1")

        assert ok is True                      # 抽查本身失败不拖垮任务（保留字幕）
        failure_log = next(m for m in logs if "云端抽查失败" in m)
        assert "CloudASRConfigError" in failure_log and "余额不足" in failure_log


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


class TestResolveVideoTarget:
    """多P 时长陷阱：view.duration 是各分P之和，cid 只对应第 1P，必须按 ?p=N 取分P数据"""

    def test_single_page_uses_view_duration(self):
        info = {'aid': 1, 'cid': 2, 'title': 'T', 'duration': 600,
                'pages': [{'cid': 2, 'page': 1, 'duration': 600, 'part': 'P1'}]}
        assert _resolve_video_target(info, 1, 'BV1') == (1, 2, 'T', 600.0)

    def test_multi_page_first_page_uses_page_duration_not_sum(self):
        """P1：用 view.duration(=137+9550) 会把正常的 P1 字幕误判成覆盖不足"""
        info = {'aid': 1, 'cid': 11, 'title': 'T', 'duration': 9687,
                'pages': [{'cid': 11, 'page': 1, 'duration': 137, 'part': 'P1'},
                          {'cid': 12, 'page': 2, 'duration': 9550, 'part': 'P2'}]}
        assert _resolve_video_target(info, 1, 'BV1') == (1, 11, 'T', 137.0)

    def test_multi_page_nth_page_switches_cid_and_title(self):
        info = {'aid': 1, 'cid': 11, 'title': 'T', 'duration': 9687,
                'pages': [{'cid': 11, 'duration': 137, 'part': 'P1'},
                          {'cid': 12, 'duration': 9550, 'part': '第二讲'}]}
        assert _resolve_video_target(info, 2, 'BV1') == (1, 12, 'T - P2 第二讲', 9550.0)

    def test_page_out_of_range_falls_back_to_first(self):
        info = {'aid': 1, 'cid': 11, 'title': 'T', 'duration': 100,
                'pages': [{'cid': 11, 'duration': 100, 'part': 'P1'}]}
        assert _resolve_video_target(info, 9, 'BV1')[1] == 11

    def test_missing_pages_keeps_view_values(self):
        assert _resolve_video_target({'aid': 1, 'cid': 2, 'duration': '600'}, 1, 'BV1') == \
            (1, 2, 'BV1', 600.0)

    def test_bad_duration_is_zero(self):
        assert _resolve_video_target({'aid': 1, 'cid': 2, 'duration': 'x'}, 1, 'BV1')[3] == 0.0


class TestSubtitleUrlAndReadiness:
    """字幕下载地址补全 + 字幕轨"已生成"判定"""

    def test_normalize_subtitle_url(self):
        assert _normalize_subtitle_url('//aisubtitle.hdslb.com/x.json') == \
            'https://aisubtitle.hdslb.com/x.json'
        assert _normalize_subtitle_url('/x/player/sub') == 'https://api.bilibili.com/x/player/sub'
        assert _normalize_subtitle_url('aisubtitle.hdslb.com/x') == 'https://aisubtitle.hdslb.com/x'
        assert _normalize_subtitle_url('https://a.com/x') == 'https://a.com/x'
        assert _normalize_subtitle_url('') == ''

    def test_ready_flag_follows_ai_status(self):
        assert _is_subtitle_ready({'lan': 'zh-CN'}) is True            # CC 无 ai_status
        assert _is_subtitle_ready({'lan': 'ai-zh', 'ai_status': 2}) is True
        assert _is_subtitle_ready({'lan': 'ai-zh', 'ai_status': 1}) is False   # 生成中
        assert _is_subtitle_ready({'lan': 'ai-en', 'ai_status': '0'}) is False
        assert _is_subtitle_ready({'lan': 'ai-zh'}) is True            # 老接口无该字段按可用

    def test_anonymous_retry_after_login_banned(self, monkeypatch):
        """登录态被 412 时匿名兜底重试（真实踩坑：带 SESSDATA 反而更严）"""
        seen = []

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

        banned = {'code': -412, 'message': 'request was banned'}
        hit = {'code': 0, 'data': {'subtitle': {'subtitles': [{'lan': 'zh-CN'}]}}}

        monkeypatch.setattr('services.downloader.load_cookies', lambda: {'SESSDATA': 's'})
        monkeypatch.setattr('services.downloader._throttle_bili_api', lambda: None)

        def fake_get(url, headers, timeout):
            logged_in = 'SESSDATA=s' in (headers.get('Cookie') or '')
            seen.append(logged_in)
            return FakeResponse(banned if logged_in else hit)

        monkeypatch.setattr('services.downloader.httpx.get', fake_get)

        subtitle, _tl, api_ok = _fetch_subtitles_via_player_v2(1, 2, 'BV1abc123456')

        assert subtitle['subtitles'][0]['lan'] == 'zh-CN'
        assert api_ok is True
        assert seen[-1] is False    # 最后一次是匿名请求

    def test_no_anonymous_retry_when_interface_healthy(self, monkeypatch):
        """接口正常响应（code=0 空列表）时不额外发匿名请求"""
        seen = []

        class FakeResponse:
            def json(self):
                return {'code': 0, 'data': {'subtitle': {'subtitles': []}}}

        monkeypatch.setattr('services.downloader.load_cookies', lambda: {'SESSDATA': 's'})
        monkeypatch.setattr('services.downloader._throttle_bili_api', lambda: None)

        def fake_get(url, headers, timeout):
            seen.append('SESSDATA=s' in (headers.get('Cookie') or ''))
            return FakeResponse()

        monkeypatch.setattr('services.downloader.httpx.get', fake_get)

        subtitle, _tl, api_ok = _fetch_subtitles_via_player_v2(1, 2, 'BV1abc123456')

        assert subtitle == {} and api_ok is True
        assert seen and all(seen)   # 全部带登录态，没有匿名请求


class TestSubtitleBodyValidation:
    """字幕 body 统计 / 硬校验 / "假残缺"判定（纯逻辑）"""

    @staticmethod
    def _body(count, step=5):
        return [
            {'from': i * step, 'to': i * step + step, 'content': f'第{i}句'}
            for i in range(count)
        ]

    def test_stats_counts_and_coverage(self):
        count, coverage = _subtitle_body_stats(self._body(4))
        assert count == 4 and coverage == 20.0
        assert _subtitle_body_stats(['bad', None]) == (0, 0.0)

    def test_validate_rejects_empty_and_short_coverage(self):
        assert _validate_subtitle_body([], 600)[0] is False
        ok, reason = _validate_subtitle_body(self._body(5), 600)     # 仅覆盖 25s
        assert ok is False and '疑似旧' in reason

    def test_validate_accepts_covering_body(self):
        assert _validate_subtitle_body(self._body(600, step=1), 600)[0] is True

    def test_validate_rejects_overrun_body(self):
        ok, reason = _validate_subtitle_body([{'from': 0, 'to': 900, 'content': 'x'}], 600)
        assert ok is False and '超出' in reason

    def test_incomplete_detects_truncated_coverage(self):
        incomplete, why = _is_subtitle_body_incomplete(self._body(20), 600, is_ai=True)
        assert incomplete is True and '短于' in why

    def test_incomplete_density_only_applies_to_ai(self):
        """覆盖整段但只有 5 条：AI 判残缺，CC 不判（CC 本来就可以很稀疏）"""
        sparse = [{'from': i * 100, 'to': i * 100 + 10, 'content': 'x'} for i in range(5)]
        assert _is_subtitle_body_incomplete(sparse, 500, is_ai=True)[0] is True
        assert _is_subtitle_body_incomplete(sparse, 500, is_ai=False)[0] is False

    def test_complete_body_is_not_flagged(self):
        assert _is_subtitle_body_incomplete(self._body(120), 600, is_ai=True)[0] is False

    def test_ai_coverage_bar_is_stricter_than_cc(self):
        """同一份覆盖 60% 时长的字幕：CC 可用，AI 判旧/错（AI 字幕几乎覆盖全部语音）"""
        body = [{'from': i * 5, 'to': i * 5 + 5, 'content': 'x'} for i in range(72)]  # 覆盖 360s
        assert _validate_subtitle_body(body, 600, is_ai=False)[0] is True
        ok, reason = _validate_subtitle_body(body, 600, is_ai=True)
        assert ok is False and '期望下限' in reason
        assert _min_expected_coverage(600, True) == 420.0
        assert _min_expected_coverage(600, False) == 270.0


class TestAcquireBilibiliSubtitle:
    """候选字幕轨「下载 → 校验 → 重试」行为"""

    @staticmethod
    def _full_body(duration=600, step=5):
        return [
            {'from': i * step, 'to': i * step + step, 'content': f'句{i}'}
            for i in range(int(duration // step))
        ]

    @pytest.mark.asyncio
    async def test_retries_truncated_body_after_refreshing_url(self, tmp_path, monkeypatch):
        """首次返回"假残缺" → 换新 auth_key 链接重试 → 拿到完整内容"""
        calls = {'fetch': 0, 'refresh': 0}
        truncated = [{'from': i * 5, 'to': i * 5 + 5, 'content': 'x'} for i in range(20)]
        full = self._full_body()

        async def fake_fetch(url, bvid=''):
            calls['fetch'] += 1
            return truncated if calls['fetch'] == 1 else full

        async def fake_wait(_seconds):
            return None

        def fake_refresh(aid, cid, bvid, lan):
            calls['refresh'] += 1
            return ['https://cdn/new.json']

        monkeypatch.setattr('services.downloader._fetch_subtitle_body', fake_fetch)
        monkeypatch.setattr('services.downloader._subtitle_retry_wait', fake_wait)
        monkeypatch.setattr('services.downloader._refresh_subtitle_urls', fake_refresh)

        result = await _acquire_bilibili_subtitle(
            [{'lan': 'ai-zh', 'subtitle_url': 'https://cdn/old.json'}],
            1, 2, 'BV1abc123456', 600.0, tmp_path)

        assert result['success'] is True
        assert result['body_count'] == len(full)
        assert calls == {'fetch': 2, 'refresh': 1}
        assert (tmp_path / 'bilibili_subtitle.srt').exists()

    @pytest.mark.asyncio
    async def test_falls_through_to_next_candidate_track(self, tmp_path, monkeypatch):
        """首选轨一直拿不到有效内容 → 自动换下一条候选轨"""
        monkeypatch.setattr('services.downloader._BILI_SUBTITLE_MAX_ATTEMPTS', 2)
        monkeypatch.setattr('services.downloader._refresh_subtitle_urls', lambda *a: [])
        monkeypatch.setattr('services.downloader._subtitle_retry_wait',
                            lambda _s: asyncio.sleep(0))
        full = self._full_body()

        async def fake_fetch(url, bvid=''):
            return [] if 'broken' in url else full

        monkeypatch.setattr('services.downloader._fetch_subtitle_body', fake_fetch)

        result = await _acquire_bilibili_subtitle(
            [{'lan': 'zh-CN', 'subtitle_url': 'https://cdn/broken.json'},
             {'lan': 'ai-zh', 'subtitle_url': 'https://cdn/good.json'}],
            1, 2, 'BV1abc123456', 600.0, tmp_path)

        assert result['success'] is True
        assert result['body_count'] == len(full)

    @pytest.mark.asyncio
    async def test_failure_when_all_candidates_unusable(self, tmp_path, monkeypatch):
        """全部候选轨都拿不到完整内容 → 失败（调用方转转录，宁缺勿错）"""
        monkeypatch.setattr('services.downloader._BILI_SUBTITLE_MAX_ATTEMPTS', 2)
        monkeypatch.setattr('services.downloader._refresh_subtitle_urls', lambda *a: [])
        monkeypatch.setattr('services.downloader._subtitle_retry_wait',
                            lambda _s: asyncio.sleep(0))

        async def fake_fetch(url, bvid=''):
            return []

        monkeypatch.setattr('services.downloader._fetch_subtitle_body', fake_fetch)

        result = await _acquire_bilibili_subtitle(
            [{'lan': 'ai-zh', 'subtitle_url': 'https://cdn/a.json'}],
            1, 2, 'BV1abc123456', 600.0, tmp_path)

        assert result['success'] is False
        assert result['reason'] == 'subtitle_incomplete'

    @pytest.mark.asyncio
    async def test_uses_best_effort_body_after_retries(self, tmp_path, monkeypatch):
        """硬校验通过但持续偏稀疏 → 重试耗尽后用覆盖最全的一份，而非直接放弃"""
        monkeypatch.setattr('services.downloader._BILI_SUBTITLE_MAX_ATTEMPTS', 2)
        monkeypatch.setattr('services.downloader._refresh_subtitle_urls', lambda *a: [])
        monkeypatch.setattr('services.downloader._subtitle_retry_wait',
                            lambda _s: asyncio.sleep(0))
        sparse = [{'from': i * 100, 'to': i * 100 + 10, 'content': 'x'} for i in range(5)]

        async def fake_fetch(url, bvid=''):
            return sparse

        monkeypatch.setattr('services.downloader._fetch_subtitle_body', fake_fetch)

        result = await _acquire_bilibili_subtitle(
            [{'lan': 'ai-zh', 'subtitle_url': 'https://cdn/a.json'}],
            1, 2, 'BV1abc123456', 500.0, tmp_path)

        assert result['success'] is True
        assert result['body_count'] == 5

    @pytest.mark.asyncio
    async def test_download_exception_is_retried(self, tmp_path, monkeypatch):
        """下载异常（链接 403/网络）也要重试，不能一次失败就放弃"""
        calls = {'n': 0}
        full = self._full_body()

        async def fake_fetch(url, bvid=''):
            calls['n'] += 1
            if calls['n'] == 1:
                raise RuntimeError('HTTP 403')
            return full

        monkeypatch.setattr('services.downloader._subtitle_retry_wait',
                            lambda _s: asyncio.sleep(0))
        monkeypatch.setattr('services.downloader._refresh_subtitle_urls', lambda *a: [])
        monkeypatch.setattr('services.downloader._fetch_subtitle_body', fake_fetch)

        result = await _acquire_bilibili_subtitle(
            [{'lan': 'ai-zh', 'subtitle_url': 'https://cdn/a.json'}],
            1, 2, 'BV1abc123456', 600.0, tmp_path)

        assert result['success'] is True
        assert calls['n'] == 2


class TestSubtitleUrlRefresh:
    """重试取链：换新 auth_key + 同 lan 多条链接（B站字幕只有 player/v2 一条真源）"""

    def test_urls_for_lan_filters_and_normalizes(self):
        tracks = [
            {'lan': 'zh-CN', 'subtitle_url': '//cdn/a.json'},
            {'lan': 'ai-zh', 'subtitle_url': 'https://cdn/b.json'},
            {'lan': 'zh-CN', 'subtitle_url': 'https://cdn/c.json'},
        ]
        assert _urls_for_lan(tracks, 'ZH-cn') == ['https://cdn/a.json', 'https://cdn/c.json']
        assert _urls_for_lan(tracks, 'ai-zh') == ['https://cdn/b.json']
        assert _urls_for_lan(None, 'zh-CN') == []
        assert _dedupe_urls(['a', '', 'a', 'b']) == ['a', 'b']

    def test_refresh_returns_fresh_urls_for_lan(self, monkeypatch):
        """刷新只取同 lan 的链接并去重（其它 lan 不带回）"""
        monkeypatch.setattr(
            'services.downloader._fetch_subtitles_via_player_v2',
            lambda aid, cid, bvid: ({'subtitles': [
                {'lan': 'ai-zh', 'subtitle_url': '//cdn/player.json'},
                {'lan': 'zh-CN', 'subtitle_url': 'https://cdn/cc.json'},
                {'lan': 'ai-zh', 'subtitle_url': 'https://cdn/player.json'},
            ]}, 0, True))

        assert _refresh_subtitle_urls(1, 2, 'BV1abc123456', 'ai-zh') == \
            ['https://cdn/player.json']

    def test_refresh_returns_empty_when_source_fails(self, monkeypatch):
        monkeypatch.setattr('services.downloader._fetch_subtitles_via_player_v2',
                            lambda aid, cid, bvid: ({}, 0, False))
        assert _refresh_subtitle_urls(1, 2, 'BV1abc123456', 'ai-zh') == []

    @pytest.mark.asyncio
    async def test_retry_with_fresh_url_gets_correct_body(self, tmp_path, monkeypatch):
        """旧链接的字幕是旧内容（覆盖 44s / 视频 600s）→ 换新链接取回正确字幕"""
        monkeypatch.setattr('services.downloader._BILI_SUBTITLE_MAX_ATTEMPTS', 2)
        monkeypatch.setattr('services.downloader._subtitle_retry_wait',
                            lambda _s: asyncio.sleep(0))
        stale = [{'from': 0, 'to': 44, 'content': '旧字幕'}]
        correct = [{'from': i * 5, 'to': i * 5 + 5, 'content': '对'} for i in range(120)]

        async def fake_fetch(url, bvid=''):
            return stale if 'stale' in url else correct

        monkeypatch.setattr('services.downloader._fetch_subtitle_body', fake_fetch)
        monkeypatch.setattr('services.downloader._refresh_subtitle_urls',
                            lambda *a: ['https://cdn/fresh.json'])

        result = await _acquire_bilibili_subtitle(
            [{'lan': 'zh-CN', 'subtitle_url': 'https://cdn/stale.json'}],
            1, 2, 'BV1abc123456', 600.0, tmp_path)

        assert result['success'] is True
        assert result['body_count'] == 120
