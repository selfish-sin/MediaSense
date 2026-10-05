"""抖音解析模块单测（游客链路：URL 识别 / id 提取 / VTT 转换 / detail 重试 / 契约）。

网络请求全部 mock，不发真网请求；签名函数（douyin_abogus）只做冒烟。
"""
import pytest
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
