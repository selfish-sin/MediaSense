"""抖音解析模块单测（游客链路：URL 识别 / id 提取 / VTT 转换 / detail 重试 / 契约）。

网络请求全部 mock，不发真网请求；签名函数（douyin_abogus）只做冒烟。
"""
import httpx
import pytest
from pathlib import Path
from unittest.mock import patch

from services.douyin import (
    DouyinError,
    _caption_to_srt_txt,
    _pick_caption,
    _parse_vtt,
    extract_aweme_id_from_text,
    extract_aweme_id_sync,
    fetch_detail_sync,
    is_douyin_url,
)
from services.douyin_abogus import ABogus, BrowserFingerprintGenerator
from services.downloader import detect_platform, sanitize_input_url
from routers.jobs import normalize_url


# ── URL 识别 ──
class TestIsDouyinUrl:
    @pytest.mark.parametrize("url", [
        "https://www.douyin.com/video/7345493142857338146",
        "https://v.douyin.com/L4NpDJ6/",
        "https://www.iesdouyin.com/share/video/6914948781100338440/?region=CN",
        "https://www.douyin.com/user/self?from_tab_name=main&modal_id=7689342659723673201&showTab=recommend",
    ])
    def test_douyin_forms(self, url):
        assert is_douyin_url(url) is True

    @pytest.mark.parametrize("url", [
        "https://www.bilibili.com/video/BV1RGN462EBs",
        "https://b23.tv/abc",
        "https://www.youtube.com/watch?v=abc",
        "not a url",
        "",
    ])
    def test_non_douyin(self, url):
        assert is_douyin_url(url) is False


# ── id 提取（无网络）──
class TestExtractAwemeId:
    def test_video_url(self):
        assert extract_aweme_id_from_text("https://www.douyin.com/video/7345493142857338146") \
            == "7345493142857338146"

    def test_modal_id(self):
        assert extract_aweme_id_from_text(
            "https://www.douyin.com/user/self?from_tab_name=main&modal_id=7689342659723673201&showTab=recommend"
        ) == "7689342659723673201"

    def test_iesdouyin_share(self):
        assert extract_aweme_id_from_text(
            "https://www.iesdouyin.com/share/video/6914948781100338440/?region=CN"
        ) == "6914948781100338440"

    def test_short_link_needs_network(self):
        # 裸短链无法离线提取（extract_aweme_id_sync 会走 302 展开，这里不打网）
        assert extract_aweme_id_from_text("https://v.douyin.com/L4NpDJ6/") == ""

    def test_extract_sync_short_link_expands(self):
        with patch("services.douyin._expand_short_link_sync",
                   return_value="https://www.iesdouyin.com/share/video/6914948781100338440/?region=CN"):
            assert extract_aweme_id_sync("https://v.douyin.com/L4NpDJ6/") == "6914948781100338440"


# ── sanitize / 平台识别 / 去重 ──
class TestInputLayers:
    def test_sanitize_bare_short_link(self):
        assert sanitize_input_url("看看这个 v.douyin.com/L4NpDJ6 好玩") \
            == "https://v.douyin.com/L4NpDJ6"

    def test_sanitize_full_url_untouched(self):
        u = "https://www.douyin.com/video/7345493142857338146"
        assert sanitize_input_url(u) == u

    def test_detect_platform_iesdouyin(self):
        name, need_proxy = detect_platform("https://www.iesdouyin.com/share/video/6914948781100338440/")
        assert name == "抖音"
        assert need_proxy is False

    def test_detect_platform_douyin_short_host(self):
        name, _ = detect_platform("https://v.douyin.com/L4NpDJ6/")
        assert name == "抖音"

    def test_normalize_douyin_video_id(self):
        assert normalize_url("https://www.douyin.com/video/7345493142857338146") \
            == "douyin:7345493142857338146"

    def test_normalize_douyin_modal_id_not_merged(self):
        """★ 回归：user 页 modal_id 不能被剥 query 合并（同用户页不同视频）"""
        a = normalize_url("https://www.douyin.com/user/self?modal_id=1111111111111111111")
        b = normalize_url("https://www.douyin.com/user/self?modal_id=2222222222222222222")
        assert a == "douyin:1111111111111111111"
        assert b == "douyin:2222222222222222222"
        assert a != b

    def test_normalize_douyin_short_link(self):
        assert normalize_url("https://v.douyin.com/L4NpDJ6/") == "douyin:short:L4NpDJ6"

    def test_normalize_bilibili_regression(self):
        assert normalize_url("https://www.bilibili.com/video/BV1RGN462EBs") == "bilibili:BV1RGN462EBs"


# ── VTT → SRT/TXT ──
SAMPLE_VTT = """WEBVTT
Kind: captions

1
00:00:00.500 --> 00:00:02.000
阳虚气虚同源

2
00:00:02.000 --> 00:00:04.500 line:0%
<v 讲者>扶阳不是补阳
"""


class TestVtt:
    def test_parse_cues(self):
        cues = _parse_vtt(SAMPLE_VTT)
        assert len(cues) == 2
        assert cues[0] == ("00:00:00,500", "00:00:02,000", "阳虚气虚同源")

    def test_srt_format(self):
        srt, _ = _caption_to_srt_txt(SAMPLE_VTT)
        assert "1\n00:00:00,500 --> 00:00:02,000\n阳虚气虚同源" in srt

    def test_txt_strips_markup(self):
        _, txt = _caption_to_srt_txt(SAMPLE_VTT)
        assert "<v 讲者>" not in txt
        assert "扶阳不是补阳" in txt
        assert "-->" not in txt

    def test_empty_vtt(self):
        srt, txt = _caption_to_srt_txt("")
        assert srt == "" and txt == ""


# ── 选轨 ──
class TestPickCaption:
    def test_zh_preferred(self):
        caps = [{"lang": "en", "url_list": ["en"]}, {"lang": "zh-Hans", "url_list": ["zh"]}]
        assert _pick_caption(caps)["url_list"] == ["zh"]

    def test_fallback_first(self):
        caps = [{"lang": "en", "url_list": ["en"]}]
        assert _pick_caption(caps)["lang"] == "en"

    def test_empty(self):
        assert _pick_caption([]) is None


# ── detail 拉取（mock 网络）──
def _resp(json_data):
    class _R:
        status_code = 200

        def json(self):
            return json_data
    return _R()


class TestFetchDetail:
    def test_success_first_try(self):
        with patch("services.douyin.httpx.get", return_value=_resp({"aweme_detail": {"desc": "x"}})), \
             patch("services.douyin._get_ttwid", return_value="t"):
            d = fetch_detail_sync("123")
            assert d == {"desc": "x"}

    def test_empty_then_retry_then_raise(self):
        empty = {"status_code": 0, "filter_detail": {"filter_reason": "core_dep"}}
        with patch("services.douyin.httpx.get", return_value=_resp(empty)), \
             patch("services.douyin._get_ttwid", return_value="t"), \
             patch("services.douyin._mint_ttwid", return_value="t2") as mint:
            with pytest.raises(DouyinError):
                fetch_detail_sync("123")
            assert mint.call_count == 1  # 第一次空响应后换新游客身份

    def test_network_error_raise_actionable(self):
        with patch("services.douyin.httpx.get", side_effect=OSError("conn refused")), \
             patch("services.douyin._get_ttwid", return_value="t"):
            with pytest.raises(DouyinError) as ei:
                fetch_detail_sync("123")
            assert "conn refused" in str(ei.value)


# ── 签名冒烟 ──
class TestAbogusVendor:
    def test_sign_smoke(self):
        fp = BrowserFingerprintGenerator.generate_fingerprint("Edge")
        ab = ABogus(fp=fp, user_agent="Mozilla/5.0 test").generate_abogus("a=1&b=2", "")
        value = ab[0] if isinstance(ab, (list, tuple)) else ab
        assert value and isinstance(value, str)


# ── 优化项：元数据缓存 / 下载重试 / 限流 / 裸短链 ──
class TestMetaCache:
    def setup_method(self):
        from services.douyin import _META_CACHE
        _META_CACHE.clear()

    def test_second_call_hits_cache(self):
        import services.douyin as dy
        with patch.object(dy, "fetch_detail_sync", return_value={"desc": "x", "video": {}}) as fd:
            m1 = dy.fetch_video_meta_sync("https://www.douyin.com/video/7345493142857338146")
            m2 = dy.fetch_video_meta_sync("https://www.douyin.com/video/7345493142857338146")
            assert fd.call_count == 1
            assert m1 is m2

    def test_refresh_bypasses_cache(self):
        import services.douyin as dy
        with patch.object(dy, "fetch_detail_sync", return_value={"desc": "x", "video": {}}) as fd:
            dy.fetch_video_meta_sync("https://www.douyin.com/video/7345493142857338146")
            dy.fetch_video_meta_sync("https://www.douyin.com/video/7345493142857338146", refresh=True)
            assert fd.call_count == 2

    def test_expired_cache_refetches(self):
        import services.douyin as dy
        from services.douyin import _META_CACHE
        _META_CACHE["7345493142857338146"] = (dy.time.monotonic() - 10_000, {"aweme_id": "stale"})
        with patch.object(dy, "fetch_detail_sync", return_value={"desc": "fresh", "video": {}}):
            m = dy.fetch_video_meta_sync("https://www.douyin.com/video/7345493142857338146")
            assert m["title"] == "fresh"


class TestThrottle:
    def test_min_interval_enforced(self):
        import services.douyin as dy
        sleeps = []
        fake_now = [100.0]

        def fake_monotonic():
            return fake_now[0]

        def fake_sleep(s):
            sleeps.append(s)
            fake_now[0] += s

        dy._last_api_ts[0] = 0.0
        with patch.object(dy.time, "monotonic", fake_monotonic), \
             patch.object(dy.time, "sleep", fake_sleep):
            dy._throttle_douyin_api()   # 首次不等待
            dy._throttle_douyin_api()   # 紧接着第二次 → 等 1s
        assert sleeps == [dy._API_MIN_INTERVAL]
        dy._last_api_ts[0] = 0.0


class TestBareShortLink:
    def test_schemeless_short_link(self):
        with patch("services.douyin._expand_short_link_sync",
                   return_value="https://www.iesdouyin.com/share/video/6914948781100338440/?region=CN"):
            assert extract_aweme_id_sync("v.douyin.com/L4NpDJ6") == "6914948781100338440"


@pytest.mark.asyncio
class TestDownloadRetry:
    async def test_stale_url_retried_with_fresh_meta(self, tmp_path):
        """直链过期（首次下载抛 DouyinError）→ 绕缓存重拉 detail 换新直链重试一次"""
        import services.douyin as dy

        meta = {"aweme_id": "123", "title": "测试视频", "author": "跃说中医",
                "duration_ms": 588603, "play_url": "http://cdn/x.mp4", "captions": []}
        calls = {"dl": 0}

        async def fake_meta(url, refresh=False):
            assert refresh is (calls["dl"] == 1)  # 第二次必须绕缓存
            return meta

        def fake_dl(play_url, dest, prog=None):
            calls["dl"] += 1
            if calls["dl"] == 1:
                raise DouyinError("HTTP 403")
            dest.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 2048)

        with patch.object(dy, "fetch_video_meta", side_effect=fake_meta), \
             patch.object(dy, "_stream_download_sync", side_effect=fake_dl):
            path, title, platform = await dy.download_douyin_video(
                "https://www.douyin.com/video/7345493142857338146", tmp_path)
        assert calls["dl"] == 2
        assert platform == "抖音" and title == "测试视频"
        assert path.endswith(".mp4")

    async def test_both_attempts_fail_raises(self, tmp_path):
        import services.douyin as dy

        meta = {"aweme_id": "123", "title": "测试视频", "author": "",
                "duration_ms": 0, "play_url": "http://cdn/x.mp4", "captions": []}

        async def fake_meta(url, refresh=False):
            return meta

        def fake_dl(play_url, dest, prog=None):
            raise DouyinError("HTTP 403")

        with patch.object(dy, "fetch_video_meta", side_effect=fake_meta), \
             patch.object(dy, "_stream_download_sync", side_effect=fake_dl):
            with pytest.raises(Exception) as ei:
                await dy.download_douyin_video(
                    "https://www.douyin.com/video/7345493142857338146", tmp_path)
        assert "抖音下载失败" in str(ei.value)


# ============ B1：直链下载必须先写 .part 再原子改名（2026-10-13） ============
# 回归症状：断流重试 2 次都失败后，job_dir 里留下一个几 MB 的**截断 mp4**；
# main._acquire_audio 的断点续传扫描只按「后缀 + size>1000」捞文件（.mp4 在白名单、
# 截断文件 mtime 最新）→ 当成「已有完整音频」跳过下载 → 云端 ASR 对半截音频跑出
# **静默截断的转录并标 completed**。

class _FakeStream:
    """httpx.stream 的替身：先吐若干块，再按需抛网络错误（模拟中途断流）。"""

    def __init__(self, chunks, error=None, status_code=200, content_length='1024'):
        self._chunks = list(chunks)
        self._error = error
        self.status_code = status_code
        self.headers = {'content-length': content_length}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self, _size=None):
        for c in self._chunks:
            yield c
        if self._error is not None:
            raise self._error


def _meta(aweme_id='7345493142857338146'):
    return {
        'aweme_id': aweme_id,
        'title': '测试标题',
        'author': '作者',
        'play_url': 'https://v.test/play.mp4',
        'duration_ms': 60000,
    }


class TestStreamDownloadPartFile:
    def test_success_renames_part_to_final(self, tmp_path, monkeypatch):
        from services import douyin
        dest = tmp_path / '测试标题.mp4'
        monkeypatch.setattr(
            douyin.httpx, 'stream',
            lambda *a, **k: _FakeStream([b'abc', b'def']))

        prog = {'done': 0, 'total': 0}
        douyin._stream_download_sync('https://v.test/play.mp4', dest, prog)

        assert dest.read_bytes() == b'abcdef'
        assert not (tmp_path / '测试标题.mp4.part').exists(), '半截文件未被清理'
        assert prog['total'] == 1024 and prog['done'] == 6

    def test_broken_stream_leaves_no_mp4(self, tmp_path, monkeypatch):
        from services import douyin
        dest = tmp_path / '测试标题.mp4'
        monkeypatch.setattr(
            douyin.httpx, 'stream',
            lambda *a, **k: _FakeStream([b'x' * 4096], error=httpx.ReadError('boom')))

        with pytest.raises(httpx.ReadError):
            douyin._stream_download_sync('https://v.test/play.mp4', dest, None)

        assert not dest.exists(), '断流后仍留下截断 mp4（会被断点续传误当完整音频）'
        assert not (tmp_path / '测试标题.mp4.part').exists()

    def test_empty_body_leaves_no_mp4(self, tmp_path, monkeypatch):
        from services import douyin
        dest = tmp_path / '空标题.mp4'
        monkeypatch.setattr(douyin.httpx, 'stream', lambda *a, **k: _FakeStream([]))
        with pytest.raises(douyin.DouyinError):
            douyin._stream_download_sync('https://v.test/play.mp4', dest, None)
        assert not dest.exists()


class TestDownloadDouyinVideoNoTruncatedFile:

    @pytest.mark.asyncio
    async def test_both_attempts_fail_leaves_no_mp4(self, tmp_path, monkeypatch):
        """两次直链都失败 → 抛异常，且 job_dir 里既无 .mp4 也无 .mp4.part"""
        from services import douyin

        async def fake_meta(url, refresh=False):
            return _meta()

        monkeypatch.setattr(douyin, 'fetch_video_meta', fake_meta)
        monkeypatch.setattr(douyin, '_FFMPEG_BIN', tmp_path / 'no_ffmpeg')
        monkeypatch.setattr(
            douyin.httpx, 'stream',
            lambda *a, **k: _FakeStream([b'y' * 8192], error=httpx.ReadError('boom')))

        with pytest.raises(Exception, match='抖音下载失败'):
            await douyin.download_douyin_video('https://v.douyin.com/x/', tmp_path)

        assert list(tmp_path.glob('*.mp4')) == [], '失败后仍留下截断 mp4'
        assert list(tmp_path.glob('*.part')) == [], '失败后残留半截文件'

    @pytest.mark.asyncio
    async def test_success_produces_complete_mp4(self, tmp_path, monkeypatch):
        from services import douyin

        async def fake_meta(url, refresh=False):
            return _meta()

        monkeypatch.setattr(douyin, 'fetch_video_meta', fake_meta)
        monkeypatch.setattr(douyin, '_FFMPEG_BIN', tmp_path / 'no_ffmpeg')
        monkeypatch.setattr(
            douyin.httpx, 'stream',
            lambda *a, **k: _FakeStream([b'z' * 100]))

        path, title, platform = await douyin.download_douyin_video(
            'https://v.douyin.com/x/', tmp_path)

        assert platform == '抖音' and title == '测试标题'
        assert path.endswith('.mp4') and not path.endswith('.part')
        assert Path(path).read_bytes() == b'z' * 100
        assert list(tmp_path.glob('*.part')) == []
