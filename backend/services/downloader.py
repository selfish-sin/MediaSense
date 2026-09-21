"""
YouTube Tool V2 - 视频下载服务
支持：YouTube/B站/抖音/小红书/Vimeo/Twitter等平台
使用 yt-dlp subprocess + B站字幕API
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

from config import DOWNLOAD_DIR, PROXY, DATA_DIR
from services.bilibili_cookies import load_cookies, save_cookies, clear_cookies, get_status

logger = logging.getLogger(__name__)

# Wbi 签名用
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 52, 1, 44, 30, 22, 25,
    24, 13, 6, 21, 40, 37, 34, 41, 17, 7, 36, 39, 11, 26,
]
_wbi_key_cache = {"img_key": None, "sub_key": None, "expires": 0}

# 完整 Chrome UA：之前截断的 UA 没有浏览器版本号，配合无 buvid 指纹，
# 是 B站 风控判定“脚本”的高权重特征
FULL_CHROME_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
)

# 进程级 buvid 指纹缓存（B站 风控核心维度之一，会话内保持一致，不每次现造）
_BUVID_CACHE = {}


def _ensure_buvid() -> dict:
    """生成/缓存 buvid3/buvid4 指纹 cookie，返回 {"buvid3": ..., "buvid4": ...}"""
    global _BUVID_CACHE
    if _BUVID_CACHE:
        return _BUVID_CACHE
    import uuid
    _BUVID_CACHE = {
        'buvid3': uuid.uuid4().hex.upper() + 'infoc',
        'buvid4': uuid.uuid4().hex.upper() + 'infoc',
    }
    logger.info(f'[buvid] 已生成会话指纹 buvid3={_BUVID_CACHE["buvid3"][:8]}...')
    return _BUVID_CACHE


def _bili_headers(bvid: str = '', with_cookies: bool = True) -> dict:
    """构造尽量接近浏览器的 B站 API 请求头：完整 UA + buvid 指纹 + 真实视频页 Referer

    with_cookies=False：只带 buvid 指纹（匿名请求）——登录态被风控 412 时用于兜底重试。
    """
    headers = {
        'User-Agent': FULL_CHROME_UA,
        'Referer': f'https://www.bilibili.com/video/{bvid}' if bvid else 'https://www.bilibili.com',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    cookies = (load_cookies() or {}) if with_cookies else {}
    cookies = dict(cookies)
    cookies.update(_ensure_buvid())
    headers['Cookie'] = '; '.join(f'{k}={v}' for k, v in cookies.items())
    return headers


# B站 API 限流：进程级锁 + 最小请求间隔。批量任务几乎同时启动时会“齐射”
# 大量同源请求（今天 4 个任务 4 秒内打了 8+ 次），这是触发风控的最大诱因。
_BILI_API_LOCK = threading.Lock()
_BILI_API_LAST_TS = 0.0
_BILI_API_MIN_INTERVAL = float(os.environ.get('BILI_API_MIN_INTERVAL', '1.0'))


def _throttle_bili_api():
    """串行化 + 限速 B站 API 调用；可被测试 monkeypatch 为 no-op"""
    global _BILI_API_LAST_TS
    with _BILI_API_LOCK:
        wait = _BILI_API_MIN_INTERVAL - (time.time() - _BILI_API_LAST_TS)
        if wait > 0:
            time.sleep(wait)
        _BILI_API_LAST_TS = time.time()


def _get_wbi_keys() -> Tuple[Optional[str], Optional[str]]:
    """获取 B站 wbi img_key 和 sub_key（带缓存）

    nav API 在未登录时返回 code=-101 但 wbi_img 数据仍然存在，所以不依赖 code==0。
    """
    now = time.time()
    if _wbi_key_cache["expires"] > now and _wbi_key_cache["img_key"]:
        return _wbi_key_cache["img_key"], _wbi_key_cache["sub_key"]
    try:
        _throttle_bili_api()
        resp = httpx.get("https://api.bilibili.com/x/web-interface/nav", headers=_bili_headers(), timeout=10.0)
        data = resp.json()
        # wbi_img 在未登录时也会返回（即使 code=-101），所以不检查 code
        wbi = data.get("data", {}).get("wbi_img", {})
        if wbi and wbi.get("img_url") and wbi.get("sub_url"):
            img_key = wbi["img_url"].rsplit("/", 1)[-1].split(".")[0]
            sub_key = wbi["sub_url"].rsplit("/", 1)[-1].split(".")[0]
            _wbi_key_cache["img_key"] = img_key
            _wbi_key_cache["sub_key"] = sub_key
            _wbi_key_cache["expires"] = now + 1800
            logger.info(f"[wbi] 密钥已缓存 (img={img_key[:8]}..., sub={sub_key[:8]}...)")
            return img_key, sub_key
        else:
            logger.warning(f"[wbi] nav API 未返回 wbi_img (code={data.get('code')})")
    except Exception as e:
        logger.warning(f"[wbi] 获取密钥失败: {e}")
    return None, None


def _compute_wbi_sign(params: Dict) -> Dict:
    """对参数字典计算 wbi 签名，返回带 w_rid 和 wts 的参数 dict"""
    img_key, sub_key = _get_wbi_keys()
    if not img_key or not sub_key:
        return params
    combined = img_key + sub_key
    mixin_key = "".join(combined[i] for i in MIXIN_KEY_ENC_TAB if i < len(combined))
    params = dict(params)
    params["wts"] = int(time.time())
    sorted_params = dict(sorted(params.items()))
    filtered = {
        k: "".join(c for c in str(v) if c not in "!'()*")
        for k, v in sorted_params.items()
    }
    plain = "&".join(f"{k}={v}" for k, v in filtered.items())
    w_rid = hashlib.md5((plain + mixin_key).encode("utf-8")).hexdigest()
    filtered["w_rid"] = w_rid
    return filtered


def _build_wbi_url(base_url: str, params: Dict) -> str:
    """构建带 wbi 签名的完整 URL"""
    signed = _compute_wbi_sign(params)
    return f"{base_url}?{urlencode(signed)}"


# 本地 ffmpeg 路径（项目自带，无需系统 PATH）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FFMPEG_ARGS = ['--ffmpeg-location', str(PROJECT_ROOT / 'bin')]
_YTDLP_STDIO_LOCK = threading.Lock()


def _run_ytdlp(args: list, timeout: int = 120):
    """运行 yt-dlp，兼容 PyInstaller frozen 模式。
    args: 不含 [sys.executable, '-m', 'yt_dlp'] 前缀的参数列表。
    返回对象含 .returncode / .stdout(bytes) / .stderr(bytes)，与 subprocess.run 兼容。
    """
    full_args = FFMPEG_ARGS + args
    if getattr(sys, 'frozen', False):
        import io as _io
        import yt_dlp as _yt_dlp

        class _R:
            def __init__(self, rc, out, err):
                self.returncode = rc
                self.stdout = out
                self.stderr = err

        # yt-dlp.main() writes to process-global stdio. Serialise this narrow
        # frozen-mode section so concurrent jobs cannot steal each other's logs.
        with _YTDLP_STDIO_LOCK:
            old_out, old_err = sys.stdout, sys.stderr
            buf_out, buf_err = _io.BytesIO(), _io.BytesIO()
            try:
                sys.stdout = _io.TextIOWrapper(buf_out, encoding='utf-8', errors='replace')
                sys.stderr = _io.TextIOWrapper(buf_err, encoding='utf-8', errors='replace')
                rc = 0
                try:
                    _yt_dlp.main(full_args)
                except SystemExit as e:
                    rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
                finally:
                    sys.stdout.flush()
                    sys.stderr.flush()
            finally:
                sys.stdout, sys.stderr = old_out, old_err
        return _R(rc, buf_out.getvalue(), buf_err.getvalue())
    else:
        cmd = [sys.executable, '-m', 'yt_dlp'] + full_args
        return subprocess.run(cmd, capture_output=True, timeout=timeout)


# ============ 平台配置 ============
PLATFORM_CONFIG = {
    "youtube.com": {"name": "YouTube", "need_proxy": True},
    "youtu.be": {"name": "YouTube", "need_proxy": True},
    "bilibili.com": {"name": "哔哩哔哩", "need_proxy": False},
    "b23.tv": {"name": "哔哩哔哩", "need_proxy": False},
    "douyin.com": {"name": "抖音", "need_proxy": False},
    "xiaohongshu.com": {"name": "小红书", "need_proxy": False},
    "vimeo.com": {"name": "Vimeo", "need_proxy": True},
    "twitter.com": {"name": "Twitter/X", "need_proxy": True},
    "x.com": {"name": "Twitter/X", "need_proxy": True},
}


def sanitize_input_url(raw: str) -> str:
    """从用户输入中提取有效视频URL。

    处理场景：
    - 剪贴板截断BV片段拼接: "BV1RGhttps://www.bilibili.com/video/BV1RGN462EBs/..."
    - 列表编号前缀: "3. https://..."
    - 外层包裹符号: <url>、"url"、《url》
    - 尾部中文/标点: "https://... 好看" / "https://...。"
    - 多URL拼接: 优先取已识别平台的URL，否则取最后一个
    - 裸BV号: "BV1RGN462EBs" → 补全为完整链接
    - 裸短链: "b23.tv/xxx" → 补 https://
    """
    if not raw:
        return ''
    text = raw.strip()
    # 剥外层包裹（中英文引号、尖括号、书名号）
    text = text.strip('\'"“”‘’《》〈〉<>「」『』')

    # 提取所有 URL 候选（遇到空白、中日韩字符、或下一个 https?:// 截断）
    candidates = re.findall(
        r'https?://(?:(?!https?://)[^\s\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e])+',
        text,
    )

    if candidates:
        # 剥尾部残留标点（。，！？等不属于URL）
        cleaned = [c.rstrip('.,;:!?\'"）)>》」』】') for c in candidates]
        if len(cleaned) == 1:
            url = cleaned[0]
        else:
            # 多URL：优先选已识别平台中最长的（垃圾片段总是更短）
            url = max(cleaned, key=len)
            platform_hits = [
                c for c in cleaned
                if any(
                    (urlparse(c).hostname or '') == d
                    or (urlparse(c).hostname or '').endswith('.' + d)
                    for d in PLATFORM_CONFIG
                )
            ]
            if platform_hits:
                url = max(platform_hits, key=len)
    else:
        # 无完整URL → 尝试裸BV号 / 裸短链
        bv = re.search(r'(BV[A-Za-z0-9]{10})', text)
        if bv:
            return f'https://www.bilibili.com/video/{bv.group(1)}'
        b23 = re.search(r'(b23\.tv/[A-Za-z0-9]+)', text)
        if b23:
            return f'https://{b23.group(1)}'
        # 实在提取不到，原样返回让下游报错
        return text.strip()

    return url


def detect_platform(url: str) -> Tuple[str, bool]:
    """检测视频平台（hostname匹配，避免子串误判）"""
    try:
        hostname = urlparse(url).hostname or ''
    except Exception:
        hostname = ''
    for domain, cfg in PLATFORM_CONFIG.items():
        if hostname == domain or hostname.endswith('.' + domain):
            return cfg["name"], cfg["need_proxy"]
    return "未知平台", True


# ============ B站字幕提取 ============
# 字幕体获取的重试策略（对抗"假残缺"/auth_key 过期，2026-09-21）：
# ①subtitle_url 带有时效 auth_key，过期即 403 → 重试时重新请求 player/v2 换新链接；
# ②风控降级时"轨道存在但 body 只有几条"（同一视频稍后复查能拿到完整内容）→ 退避重试；
# ③首选轨彻底不可用时依次尝试后面的候选轨，而不是直接放弃整条字幕。
_BILI_SUBTITLE_MAX_ATTEMPTS = int(os.environ.get('BILI_SUBTITLE_MAX_ATTEMPTS', '4'))
_BILI_SUBTITLE_RETRY_BASE = float(os.environ.get('BILI_SUBTITLE_RETRY_BASE', '3'))

# AI 字幕的时间覆盖下限（占视频时长比例）。AI 字幕几乎覆盖全部语音内容，
# 覆盖率腰斩基本等价于旧/错字幕；CC 允许创作者只传片段，故阈值宽松得多。
# 纯请求侧校验：只用"视频时长 + 字幕时间轴"两个接口数据即可判断，不需要音频/ASR。
_AI_SUBTITLE_MIN_COVERAGE = float(os.environ.get('BILI_SUBTITLE_MIN_COVERAGE', '0.75'))


async def _subtitle_retry_wait(seconds: float):
    """字幕重试间隔（独立函数：便于测试 monkeypatch，也便于将来换成指数退避）。"""
    await asyncio.sleep(seconds)


def _is_subtitle_ready(sub: dict) -> bool:
    """字幕轨是否"已生成可用"。

    B站 AI 字幕生成中（``ai_status`` != 2）时 subtitle_url 会返回空/半成品内容，
    必须剔除，否则会把"还没生成完"的字幕当成成品送进分段/总结。
    CC（创作者上传）没有 ai_status 或为 0；老接口不返回该字段 → 一律按可用处理。
    """
    is_ai = (sub.get('lan') or '').lower().startswith('ai')
    if not is_ai:
        return True
    status = sub.get('ai_status')
    if status is None or status == '':
        return True
    try:
        return int(status) == 2
    except (TypeError, ValueError):
        return True


def _zh_variant_rank(lan: str) -> int:
    """中文变体排序：简体优先（默认输出语言是简体中文），繁体其次，其他最后。"""
    if lan in ('zh', 'zh-hans', 'zh-cn', 'zh-sg', 'zh-my'):
        return 0
    if lan in ('zh-tw', 'zh-hant', 'zh-hk', 'zh-mo'):
        return 1
    return 2


def _pick_zh_subtitle(subtitles: list) -> list:
    """从中文字幕列表里按优先级挑可用字幕，返回新的排序后列表。

    优先级（CC 人工上传永不旧，必须排 AI 之前）：
    1. 中文 CC 字幕（lan 如 zh-CN/zh-Hans，无 ai- 前缀），简体优先于繁体
    2. 中文 AI 字幕（ai-zh）
    3. 兜底：任意 AI 字幕（视频本身可能是外文）
    4. 空列表（调用方走转录流程）
    绝不把 ai-en / ai-jp 之类的非中文字幕误当中文用；
    生成中（ai_status != 2）的 AI 字幕直接剔除，不参与排序。
    """
    def _lan(s):
        return (s.get('lan') or '').lower()

    ready = [s for s in subtitles if _is_subtitle_ready(s)]
    zh_subs = [
        s for s in ready
        if _lan(s).startswith('zh') or _lan(s).startswith('ai-zh')
    ]
    # CC（非 ai- 前缀）排前面，AI 字幕排后面；同类内简体优先
    zh_subs.sort(key=lambda s: (_lan(s).startswith('ai-'), _zh_variant_rank(_lan(s))))
    if not zh_subs:
        zh_subs = [s for s in ready if _lan(s).startswith('ai')]
    return zh_subs


def _fetch_subtitles_via_player_v2(aid, cid, bvid):
    """同步调用 B 站播放器 API 获取字幕列表。

    接口顺序：先走实测更稳的旧版 ``player/v2``（带浏览器常参数），
    wbi/v2 降级为备用；wbi 若被风控(412)则强制刷新签名密钥重试一次；
    仍被风控且本机有登录态时，最后用匿名身份再试一次（登录态可能比匿名更严）。
    返回 (subtitle_dict, timelength_ms, api_ok)：
    - timelength 为视频时长（毫秒），用于字幕时长校验（防旧/错误字幕），取不到为 0
    - api_ok 表示是否至少有一个接口正常响应（code==0，即使字幕为空）——
      用于区分“B站确认无字幕”（可跳过 yt-dlp 兜底）和“接口被风控/异常”（必须兜底）
    """
    legacy_url = 'https://api.bilibili.com/x/player/v2?' + urlencode({
        'aid': aid,
        'cid': cid,
        'part': 1,
        'dm_img_str': 'V2ViR0VuR2FyOlxyZXR1cm4gTmV3cyBLZXkgUGFnZSBMaXN0Ig==',
        'dm_cover_img_str': '',
        'dm_img_list': '[]',
        'dm_img_crypto': '',
        'web_location': '1315875',
    })

    def _try(url: str, with_cookies: bool = True):
        _throttle_bili_api()
        try:
            resp = httpx.get(url, headers=_bili_headers(bvid, with_cookies), timeout=15.0)
            data = resp.json()
        except Exception as e:
            logger.warning(f'B站字幕接口调用失败: {e}')
            return {}, 0, False
        code = data.get('code')
        if code != 0:
            if code == -412:
                logger.warning('B站风控拦截(412 request was banned)，自动回退备用接口')
            else:
                logger.warning(
                    f'B站字幕接口返回错误: code={code}, msg={data.get("message")}'
                )
            return {}, 0, False
        sub = data.get('data', {}) or {}
        subtitle = sub.get('subtitle', {})
        return subtitle, sub.get('timelength', 0), True

    # 1) 旧版 player/v2 优先（今天实测未被风控，先拿字幕再考虑更严的签名接口）
    subtitle, tl, _ = _try(legacy_url)
    if subtitle.get('subtitles'):
        return subtitle, tl, True

    # 2) wbi/v2 备用
    wbi_url = _build_wbi_url(
        'https://api.bilibili.com/x/player/wbi/v2',
        {'aid': aid, 'cid': cid, 'bvid': bvid},
    )
    subtitle, tl, api_ok = _try(wbi_url)
    if subtitle.get('subtitles'):
        return subtitle, tl, True

    # 3) wbi 被风控(412) → 强制刷新签名密钥重试一次（换一批 img/sub 密钥常常能过）
    if not api_ok:
        _wbi_key_cache['expires'] = 0  # 强制下次重建时重新拉取 nav 密钥
        wbi_url2 = _build_wbi_url(
            'https://api.bilibili.com/x/player/wbi/v2',
            {'aid': aid, 'cid': cid, 'bvid': bvid},
        )
        if wbi_url2 != wbi_url:
            subtitle, tl, api_ok2 = _try(wbi_url2)
            if subtitle.get('subtitles'):
                return subtitle, tl, True
            api_ok = api_ok or api_ok2

    # 4) 登录态反而比匿名更严（真实踩坑：带 SESSDATA 时 412，匿名能过）→ 匿名再试一次。
    #    匿名看不到 AI 字幕，所以只认"命中字幕列表"；空列表不能改判 api_ok，
    #    否则会把"登录被风控"误判成"B站确认无字幕"而跳过 yt-dlp 兜底。
    if not api_ok and load_cookies():
        subtitle, tl, _ = _try(legacy_url, with_cookies=False)
        if subtitle.get('subtitles'):
            logger.info('登录态被风控，匿名请求命中字幕列表')
            return subtitle, tl, True
    return {}, 0, api_ok


def _resolve_video_target(info: dict, page: int = 1, fallback_title: str = ''):
    """从视频信息里解析 (aid, cid, 标题, 时长秒)。

    **多P 陷阱**：`x/web-interface/view` 的 `duration` 是**各分P时长之和**，而 `cid` 只对应
    第 1P。若拿整篇时长去校验某个分P的字幕覆盖范围，会把正常的分P字幕误判成"覆盖不足"
    （反之也会放宽）。故只要 `pages` 存在，cid 与时长一律按 `?p=N` 从 pages 取。
    `page` 越界时退回第 1P（与旧行为一致）。
    """
    aid = info.get('aid', 0)
    cid = info.get('cid', 0)
    title = info.get('title') or fallback_title
    try:
        duration = float(info.get('duration') or 0)
    except (TypeError, ValueError):
        duration = 0.0

    pages = info.get('pages') or []
    if pages:
        index = page - 1 if 0 < page <= len(pages) else 0
        page_info = pages[index] or {}
        if page_info.get('cid'):
            cid = page_info['cid']
        if page > 1:
            title = f'{title} - P{page} {page_info.get("part", "")}'
        try:
            page_duration = float(page_info.get('duration') or 0)
        except (TypeError, ValueError):
            page_duration = 0.0
        if page_duration:
            duration = page_duration
    return aid, cid, title, duration


def _fetch_video_info_http(bvid: str) -> dict:
    """取视频信息（aid/cid/标题/时长/pages）——纯 httpx，不依赖 bilibili-api-python。

    先走 wbi 签名版（字段最全，与第三方库内部用的是同一族接口），失败退未签名版；
    两者共用同一套浏览器指纹（完整 UA + buvid + 真实 Referer）与进程级限流——
    避免"第三方库/yt-dlp/自研 HTTP"三套指纹并存、风控表现无法归因。
    全失败返回 {}，由调用方决定兜底（**不再因为"某个可选依赖没装"就整条链路降级**）。
    """
    candidates = [
        _build_wbi_url('https://api.bilibili.com/x/web-interface/wbi/view', {'bvid': bvid}),
        'https://api.bilibili.com/x/web-interface/view?' + urlencode({'bvid': bvid}),
    ]
    for url in candidates:
        _throttle_bili_api()
        try:
            data = httpx.get(url, headers=_bili_headers(bvid), timeout=15.0).json()
        except Exception as e:
            logger.warning(f'B站视频信息接口调用失败: {e}')
            continue
        if data.get('code') != 0:
            logger.warning(
                f'B站视频信息接口返回错误: code={data.get("code")}, msg={data.get("message")}')
            continue
        info = data.get('data') or {}
        if info.get('aid') and info.get('cid'):
            return info
    return {}


async def extract_bilibili_subtitles(url: str, output_dir: Path, sio=None, job_id: str = '') -> dict:
    """
    提取B站字幕（AI字幕 / 创作者上传字幕）

    策略（2026-09-21 起收敛为单一路径 + 单校验出口，**不再依赖 bilibili-api-python**）：
      ①视频信息 `_fetch_video_info_http`（wbi/view → view）→ ②轨列表
      `_fetch_subtitles_via_player_v2`（legacy player/v2 → wbi player/v2 → 匿名兜底）
      → ③选轨 `_pick_zh_subtitle`（CC 优先/简体优先/剔除生成中）
      → ④取体+校验+换链接重试 `_acquire_bilibili_subtitle`
      → ⑤yt-dlp 仅作最后兜底（且必须过同一套覆盖校验，见 `_extract_bilibili_subtitles_ytdlp`）
    返回 dict: {"success": True/False, "srt_path": Path, "txt_path": Path, "title": str, "message": str}
    """
    match = re.search(r'(BV[A-Za-z0-9]{10})', url)
    if not match:
        # 短链不一定在 URL 中直接带 BV 号，跟随一次重定向再解析。
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=True,
                headers={'User-Agent': 'Mozilla/5.0'},
            ) as client:
                resolved = await client.head(url)
            match = re.search(r'(BV[A-Za-z0-9]{10})', str(resolved.url))
            if match:
                url = str(resolved.url)
        except Exception as e:
            logger.warning(f'B站短链解析失败: {e}')
    if not match:
        return {"success": False, "message": "无法解析B站视频BV号，请使用完整视频链接"}
    bvid = match.group(1)

    # 解析分P参数 ?p=N（默认第1P）
    page = 1
    p_match = re.search(r'[?&]p=(\d+)', url)
    if p_match:
        page = int(p_match.group(1))
        if page < 1:
            page = 1

    if sio:
        from socket_handlers import emit_log
        await emit_log(sio, job_id, f'尝试提取B站AI字幕 (bvid={bvid}, p={page})')

    if not load_cookies():
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, '⚠ 未登录B站，AI字幕可能无法获取（仅CC字幕可用）')

    # Step 1: 视频信息（aid/cid/标题/时长/pages）——纯 httpx，与本模块其余请求共用指纹与限流
    info = await asyncio.to_thread(_fetch_video_info_http, bvid)
    if not info:
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, '⚠ 视频信息接口不可用（可能被风控），转yt-dlp兜底')
        return await _extract_bilibili_subtitles_ytdlp(url, output_dir, sio, job_id)
    aid, cid, video_title, video_duration = _resolve_video_target(info, page, bvid)

    # Step 2: 字幕轨列表（唯一轨道来源：legacy player/v2 → wbi player/v2 → 匿名兜底）
    try:
        sub_data, tl_ms, api_ok = await asyncio.to_thread(
            _fetch_subtitles_via_player_v2, aid, cid, bvid)
    except Exception as e:
        logger.warning(f'B站字幕轨接口异常: {e}')
        sub_data, tl_ms, api_ok = {}, 0, False
    if tl_ms:
        video_duration = tl_ms / 1000.0
    # sub_data 格式: {"subtitles":[{lan, lan_doc, subtitle_url, ai_status}], ...}
    all_subtitles = (sub_data or {}).get('subtitles') or [] if isinstance(sub_data, dict) else []
    zh_subs = _pick_zh_subtitle(all_subtitles)

    if not zh_subs:
        if api_ok:
            # B站确认无字幕（接口正常响应且字幕列表为空）→ 跳过 yt-dlp 兜底
            # （yt-dlp 读的是同一个接口，只会白等 412 或重复确认无字幕），直接走 Whisper
            if sio:
                from socket_handlers import emit_log
                await emit_log(sio, job_id, '⚠ B站确认无可用字幕，跳过yt-dlp兜底，进入转录流程')
            logger.info(f'B站确认无字幕 bvid={bvid}，跳过yt-dlp，进入转录流程')
            return {"success": False, "message": "B站确认无可用字幕", "reason": "no_subtitle"}
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, '⚠ 字幕接口状态未知（可能被风控/异常），转yt-dlp兜底')
        logger.info(f'B站字幕接口状态未知 bvid={bvid}，转yt-dlp兜底')
        return await _extract_bilibili_subtitles_ytdlp(
            url, output_dir, sio, job_id, video_duration=video_duration)

    if sio:
        from socket_handlers import emit_log
        sub_info = [(s.get('lan', ''), s.get('lan_doc', '')) for s in zh_subs]
        await emit_log(sio, job_id, f'找到 {len(zh_subs)} 条可用字幕: {sub_info}')
        pending = [
            (s.get('lan', ''), s.get('ai_status')) for s in (all_subtitles or [])
            if not _is_subtitle_ready(s)
        ]
        if pending:
            await emit_log(sio, job_id, f'⚠ 跳过 {len(pending)} 条生成未完成的AI字幕: {pending}')

    logger.info(f'字幕下载: bvid={bvid}, aid={aid}, cid={cid}, 候选轨={[s.get("lan") for s in zh_subs]}')

    # ★ "下载 → 校验 → 重试"循环：首选轨 auth_key 过期(403)或返回"假残缺"（轨道在但 body
    #   只有几条）时不直接放弃——重试时重新请求 player/v2 换新链接，并依次尝试后续候选轨。
    #   全部候选与重试都拿不到完整内容时返回 success=False，由调用方走转录（宁缺勿错）。
    result = await _acquire_bilibili_subtitle(
        zh_subs, aid, cid, bvid, video_duration, output_dir, sio, job_id
    )
    if not result.get('success'):
        return result

    if sio:
        from socket_handlers import emit_log, emit_progress
        await emit_log(sio, job_id, f'✅ B站字幕提取成功: {video_title} ({result.get("body_count", 0)}条)')
        await emit_progress(sio, job_id, 'transcribe', 100, 'B站字幕已获取，跳过本地转录')

    return {
        "success": True,
        "srt_path": result['srt_path'],
        "txt_path": result['txt_path'],
        "title": video_title,
        "message": f"字幕提取成功: {video_title}"
    }


def _normalize_subtitle_url(url: str) -> str:
    """补全字幕下载地址协议（B站常返回 //aisubtitle.hdslb.com/... 或 /x/... 形式）。"""
    url = (url or '').strip()
    if not url:
        return ''
    if url.startswith('//'):
        return 'https:' + url
    if url.startswith('/'):
        return 'https://api.bilibili.com' + url
    if not url.startswith('http'):
        return 'https://' + url
    return url


def _subtitle_body_stats(body) -> Tuple[int, float]:
    """字幕 body → (条目数, 时间覆盖终点秒数)；非 dict 条目跳过。"""
    count = 0
    coverage = 0.0
    for item in body or []:
        if not isinstance(item, dict):
            continue
        count += 1
        try:
            end = float(item.get('to'))
        except (TypeError, ValueError):
            continue
        if end > coverage:
            coverage = end
    return count, coverage


def _min_expected_coverage(video_duration: float, is_ai: bool) -> float:
    """字幕时间覆盖的期望下限（秒）——纯请求侧判据（视频时长 + 字幕时间轴，无需音频）。

    AI 字幕几乎覆盖全部语音内容，覆盖率腰斩基本等价于旧/错字幕，故阈值比 CC 严得多；
    CC 允许创作者只上传片段（历史任务里常见），阈值保持宽松，避免误杀。
    """
    ratio = _AI_SUBTITLE_MIN_COVERAGE if is_ai else 0.5
    return video_duration * ratio - 30


def _validate_subtitle_body(body, video_duration: float, is_ai: bool = False) -> Tuple[bool, str]:
    """字幕内容硬校验（决定"能不能用"）。返回 (是否可用, 不可用原因)。

    覆盖范围明显偏离视频时长 → 判定旧/错字幕（B站 AI 字幕换源/重传后可能仍是旧内容，
    与当前视频对不上却会被一路送进分段/总结）。校验只用请求侧数据，不依赖抽查。
    """
    count, coverage = _subtitle_body_stats(body)
    if count == 0:
        return False, '内容为空'
    if video_duration and coverage:
        if coverage > video_duration + 120:
            return False, f'覆盖 {coverage:.0f}s 超出视频 {video_duration:.0f}s，疑似非本视频字幕'
        expected = _min_expected_coverage(video_duration, is_ai)
        if coverage < expected:
            return False, (f'覆盖 {coverage:.0f}s 低于视频 {video_duration:.0f}s 的期望下限 '
                           f'{expected:.0f}s（{"AI" if is_ai else "CC"} 字幕），疑似旧/错误字幕')
    return True, ''


def _is_subtitle_body_incomplete(body, video_duration: float, is_ai: bool = True) -> Tuple[bool, str]:
    """字幕是否疑似"假残缺"（硬校验能过，但内容明显被降级截断）。

    表现：风控降级返回时轨道存在、body 只有零星几条；单独复查同一视频能拿到完整内容。
    密度判断只对 AI 字幕生效——CC（创作者上传）本身可以很稀疏（歌词/章节式），不能误杀。
    返回 (是否残缺, 原因)。
    """
    count, coverage = _subtitle_body_stats(body)
    if count == 0:
        return True, '内容为空'
    if video_duration and coverage:
        # 比硬校验下限再高一档：够用但不满时也值得换源重试一次
        expected = _min_expected_coverage(video_duration, is_ai) + video_duration * 0.1
        if coverage < expected:
            return True, f'覆盖 {coverage:.0f}s 明显短于视频 {video_duration:.0f}s'
    if is_ai and coverage >= 60 and coverage / 30 > count:
        # 平均超过 30s 才一条（B站 AI 字幕常态约 5s/条）→ 明显低于正常密度
        return True, f'仅 {count} 条覆盖 {coverage:.0f}s，密度异常'
    return False, ''


async def _fetch_subtitle_body(sub_url: str, bvid: str = '') -> list:
    """下载字幕 JSON 并返回 body 列表（非 200 抛异常，交给调用方决定是否重试）。

    带完整浏览器指纹，避免 aisubtitle CDN 侧也被风控。
    """
    async with httpx.AsyncClient(timeout=15.0, headers=_bili_headers(bvid)) as client:
        resp = await client.get(sub_url)
    if resp.status_code != 200:
        raise RuntimeError(f'HTTP {resp.status_code}')
    data = resp.json()
    if isinstance(data, dict):
        return data.get('body') or []
    if isinstance(data, list):
        return data
    return []


def _dedupe_urls(urls) -> list:
    """链接去重（保序），去掉空值。"""
    out = []
    for url in urls or []:
        if url and url not in out:
            out.append(url)
    return out


def _urls_for_lan(tracks, lan: str) -> list:
    """从字幕轨列表里取指定 lan 的下载链接（返回列表：同 lan 理论上可有多条）。"""
    target = (lan or '').lower()
    urls = []
    for track in tracks or []:
        if (track.get('lan') or '').lower() == target:
            url = _normalize_subtitle_url(track.get('subtitle_url', ''))
            if url:
                urls.append(url)
    return urls


def _refresh_subtitle_urls(aid, cid, bvid, lan: str) -> list:
    """重新请求播放器接口，取指定 lan 轨的**新的**下载链接（重试时调用）。

    必须换新链接：subtitle_url 带时效 auth_key，旧链接过期后每次重试都只会 403。
    走 `x/player/v2`（内含 wbi 与"登录被风控则匿名兜底"两条二级重试）。
    返回去重后的链接列表（可能为空，调用方沿用旧链接再试）。

    注：曾尝试把 `x/player/wbi/playurl` 当作第二数据源交叉取链，**2026-09-21 真网实测
    该接口的 `data.subtitle` 恒为 null（fnval=4048/16 均如此，code=0）**——B站已不在
    playurl 返回字幕元数据，故不再引入（避免死代码与多余的 412 风险面）。
    """
    subtitle, _tl, _ok = _fetch_subtitles_via_player_v2(aid, cid, bvid)
    urls = _dedupe_urls(_urls_for_lan((subtitle or {}).get('subtitles'), lan))
    if urls:
        logger.info(f'字幕链接已刷新: lan={lan}, 候选链接 {len(urls)} 条')
    return urls


def _build_subtitle_result(body, output_dir: Path) -> dict:
    """把字幕 body 写成 SRT/TXT（固定文件名，避免 Windows 非法字符 / ADS 旁路）。"""
    srt_path = output_dir / 'bilibili_subtitle.srt'
    txt_path = output_dir / 'bilibili_subtitle.txt'
    _write_subtitle_files({'body': body}, srt_path, txt_path)
    return {
        "success": True,
        "srt_path": srt_path,
        "txt_path": txt_path,
        "body_count": len(body),
    }


async def _acquire_bilibili_subtitle(subtitles, aid, cid, bvid, video_duration,
                                     output_dir: Path, sio=None, job_id: str = '') -> dict:
    """候选字幕轨上「下载 → 校验 → 换链接重试」，直到拿到完整可用的一份。

    重试触发条件：链接过期/网络失败、内容为空、时间覆盖不符、疑似"假残缺"。
    每次重试都重新请求接口换新链接（新 auth_key），优先用尚未试过的链接；
    一条轨重试耗尽后换下一条候选轨。宁缺勿错：全部拿不到完整内容就返回 success=False
    让调用方走转录，而不是把残缺/错位字幕当成品。
    """
    from socket_handlers import emit_log

    async def _log(msg: str):
        logger.info(msg)
        if sio:
            await emit_log(sio, job_id, msg)

    best = None  # (coverage, count, body, lan)：硬校验通过但疑似残缺的最优一份，作兜底
    candidates = list(subtitles)[:4]
    for chosen in candidates:
        lan = chosen.get('lan', '')
        is_ai = lan.lower().startswith('ai')
        urls = _dedupe_urls([_normalize_subtitle_url(chosen.get('subtitle_url', ''))])
        if not urls:
            await _log(f'  ⚠ 字幕轨 {lan} 无下载地址，换下一条候选轨')
            continue
        for attempt in range(1, _BILI_SUBTITLE_MAX_ATTEMPTS + 1):
            if attempt > 1:
                # 刷新/换源是同步 httpx 调用 → 放线程池，避免阻塞事件循环
                fresh = await asyncio.to_thread(_refresh_subtitle_urls, aid, cid, bvid, lan)
                urls = _dedupe_urls(urls + fresh)
                await _subtitle_retry_wait(_BILI_SUBTITLE_RETRY_BASE * (attempt - 1))
            # 优先用还没试过的链接（新 auth_key / 另一个数据源），没有新的就复用最后一条
            sub_url = urls[min(attempt - 1, len(urls) - 1)]
            try:
                body = await _fetch_subtitle_body(sub_url, bvid)
            except Exception as e:
                await _log(f'  ⚠ 字幕 {lan} 第{attempt}次下载失败（{_short_error(e)}）')
                continue
            ok, reason = _validate_subtitle_body(body, video_duration, is_ai)
            if not ok:
                await _log(f'  ⚠ 字幕 {lan} 第{attempt}次内容校验未过（{reason}），换新链接重试')
                continue
            incomplete, why = _is_subtitle_body_incomplete(body, video_duration, is_ai)
            if not incomplete:
                if attempt > 1:
                    await _log(f'  ✅ 字幕 {lan} 第{attempt}次尝试拿到完整内容')
                return _build_subtitle_result(body, output_dir)
            count, coverage = _subtitle_body_stats(body)
            if best is None or coverage > best[0]:
                best = (coverage, count, body, lan)
            await _log(f'  ⚠ 字幕 {lan} 疑似残缺（{why}），换新链接重试（第{attempt}/{_BILI_SUBTITLE_MAX_ATTEMPTS}次）')

    if best is not None:
        _cov, count, body, lan = best
        await _log(f'  ⚠ 多次重试后仍偏稀疏，使用覆盖最全的一份（lan={lan}, {count}条）')
        return _build_subtitle_result(body, output_dir)
    return {
        "success": False,
        "message": "字幕内容不完整或校验未通过（已重试全部候选轨）",
        "reason": "subtitle_incomplete",
    }


def _write_subtitle_files(sub_json, srt_path: Path, txt_path: Path):
    """将B站字幕JSON写入SRT和TXT文件
    sub_json 可能是 {"body": [...]} 也可能是 [...]，统一处理
    """
    # 兼容两种格式：完整JSON对象 vs 直接的body数组
    if isinstance(sub_json, dict):
        body = sub_json.get('body', [])
    elif isinstance(sub_json, list):
        body = sub_json
    else:
        body = []

    with open(srt_path, 'w', encoding='utf-8') as f_srt, \
         open(txt_path, 'w', encoding='utf-8') as f_txt:
        for i, item in enumerate(body):
            if not isinstance(item, dict):
                continue
            start = item.get('from', 0)
            end = item.get('to', 0)
            text = item.get('content', '')
            srt_start = _format_srt_time(start)
            srt_end = _format_srt_time(end)
            f_srt.write(f"{i+1}\n{srt_start} --> {srt_end}\n{text}\n\n")
            mm_start = _format_mmss(start)
            mm_end = _format_mmss(end)
            f_txt.write(f"[{mm_start} - {mm_end}] {text}\n")


# ============ B站字幕内容抽查（防旧/错误字幕） ============
def _subtitle_head_text(txt_path, max_seconds: float = 90.0) -> str:
    """从字幕 TXT（每行 `[MM:SS - MM:SS] 文本`）提取开头到 max_seconds 的文本。

    用于和音频开头做内容比对。无时间戳的纯文本行（兜底格式）只收前几条防止失控。
    """
    head: list = []
    try:
        with open(txt_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                m = re.match(r'\[\s*(\d+):(\d+)\s*-\s*\d+:\d+\s*\]\s*(.*)', stripped)
                if m:
                    start = int(m.group(1)) * 60 + int(m.group(2))
                    if start > max_seconds:
                        break
                    head.append(m.group(3))
                else:
                    if len(head) < 30:
                        head.append(stripped)
    except OSError:
        return ''
    return '\n'.join(head)


def _text_similarity(text_a: str, text_b: str) -> float:
    """两段文本的相似度（0~1）。归一化去标点/空白后按字符序列比对；
    太短（<20 字符）无法可靠判定，按是否完全相同返回。"""
    def _norm(s: str) -> str:
        return re.sub(r'[^0-9a-zA-Z\u4e00-\u9fff]+', '', (s or '').lower())

    na, nb = _norm(text_a), _norm(text_b)
    if not na or not nb:
        return 0.0
    if len(na) < 20 or len(nb) < 20:
        return 1.0 if na == nb else 0.0
    from difflib import SequenceMatcher
    return SequenceMatcher(None, na, nb).ratio()


def _short_error(err: BaseException, limit: int = 160) -> str:
    """异常 → 单行短文案（推给前端的告警用；完整原文仍进后端日志）。"""
    text = re.sub(r'\s+', ' ', str(err)).strip()
    return text if len(text) <= limit else text[:limit] + '…'


async def verify_subtitle_against_audio(txt_path, audio_path, sio=None, job_id: str = '',
                                        max_seconds: float = 90.0, threshold: float = 0.3,
                                        language: Optional[str] = None) -> Tuple[bool, str]:
    """校验字幕内容与音频开头是否一致（防 B站 返回旧/错误字幕）。

    转写引擎云端优先——provider 由 `cloud_asr.preferred_provider()` 单点挑选
    （**只挑免费槽位**；免费档不可用返回 None 直接走本地 Whisper，绝不烧付费额度），
    云端失败回退本地 Whisper base，双失败按现行语义保留字幕（不让校验本身拖垮任务）。
    字幕与音频是两个独立来源，内容对不上即说明字幕不是当前视频的。
    返回 (是否保留字幕, 说明)。
    """
    try:
        from services.transcriber import transcribe_audio_sample
        sub_head = _subtitle_head_text(txt_path, max_seconds)
        asr_text: Optional[str] = None
        engine_used = 'local'

        # ★ 云端抽查（免费档可用才走）：90s 片段一次 API 调用
        #   provider 选择单点 = cloud_asr.preferred_provider()（**只挑免费槽位**）——抽查只是
        #   校验手段，不该烧付费额度，免费档不可用就直接走本地 Whisper。此前硬编码默认
        #   provider（mimo 付费档）导致每个任务静默消耗付费额度（2026-09-15 泄漏）。
        try:
            from services import cloud_asr
            provider = cloud_asr.preferred_provider()
            if provider:
                sample_mp3 = await asyncio.to_thread(
                    cloud_asr.extract_sample, str(audio_path), max_seconds)
                try:
                    asr_text = await asyncio.to_thread(
                        cloud_asr.transcribe_file, sample_mp3, provider=provider)
                    engine_used = 'cloud'
                finally:
                    Path(sample_mp3).unlink(missing_ok=True)
        except Exception as cloud_e:
            reason = _short_error(cloud_e)
            logger.warning(f'云端字幕抽查失败，回退本地 Whisper base: {cloud_e}')
            if sio:
                from socket_handlers import emit_log
                await emit_log(
                    sio, job_id,
                    f'  ⚠ 云端抽查失败（{type(cloud_e).__name__}：{reason}），回退本地转写')

        if asr_text is None:
            zh_prompt = '以下是简体中文。' if language == 'zh' else None
            asr_text = await asyncio.to_thread(
                transcribe_audio_sample, str(audio_path), max_seconds, language, zh_prompt
            )

        ratio = _text_similarity(sub_head, asr_text)
        detail = (
            f'相似度 {ratio:.2f}（{"云端" if engine_used == "cloud" else "本地"}抽查 | '
            f'字幕头 {len(sub_head)} 字 vs 音频转写 {len(asr_text)} 字）'
        )
        logger.info(f'字幕内容抽查: {detail}')
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, f'  字幕-音频相似度 {ratio:.2f}，阈值 {threshold}')
        return ratio >= threshold, detail
    except Exception as e:
        logger.warning(f'字幕内容抽查失败，按保留字幕处理: {e}')
        return True, f'抽查不可用（{type(e).__name__}: {e}）'


def _parse_srt_time(value: str) -> Optional[float]:
    """解析 SRT 时间戳（00:01:02,500）→ 秒；解析失败返回 None（容忍尾随的 cue 设置）。"""
    m = re.match(r'(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})', (value or '').strip())
    if not m:
        return None
    hours, minutes, seconds, millis = (int(x) for x in m.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def _srt_to_entries(text: str) -> list:
    """SRT 文本 → [{'from': 秒, 'to': 秒}]，供 `_subtitle_body_stats` 系列复用。

    目的：让 yt-dlp 兜底路径与主路径**共用同一套时间覆盖校验**——该路径此前只判
    "文件 ≥50 字节"，是残缺/旧字幕的漏口。
    """
    entries = []
    for line in (text or '').splitlines():
        if '-->' not in line:
            continue
        left, _, right = line.partition('-->')
        end = _parse_srt_time(right)
        if end is None:
            continue
        entries.append({'from': _parse_srt_time(left) or 0.0, 'to': end})
    return entries


def _subtitle_file_lan(path: Path) -> str:
    """从 yt-dlp 产出的字幕文件名取语言码（`video.zh-Hans.srt` → `zh-hans`）。"""
    stem = path.stem.lower()
    return stem.rsplit('.', 1)[-1] if '.' in stem else ''


def _rank_subtitle_file(path: Path):
    """按**主路径同一套选轨规则**给 yt-dlp 产物排序：CC 优先 → 简中优先 → 新文件优先。

    此前是 `max(mtime)`——多个语言/轨同时落盘时等于随机挑一条，可能挑到繁体或外语轨。
    """
    lan = _subtitle_file_lan(path)
    return (lan.startswith('ai'), _zh_variant_rank(lan), -path.stat().st_mtime)


async def _extract_bilibili_subtitles_ytdlp(url: str, output_dir: Path, sio=None, job_id: str = '',
                                            video_duration: float = 0.0) -> dict:
    """yt-dlp兜底提取B站字幕（带cookie）
    关键防误认：只认"本次调用新增"的srt，避免把旧任务残留当成成功结果
    """
    bilibili_headers = [
        '--add-header', 'Origin:https://www.bilibili.com',
        '--add-header', 'Referer:https://www.bilibili.com',
        '--add-header', f'User-Agent:{FULL_CHROME_UA}',
    ]

    # 把 cookie 写成 Netscape 格式文件给 yt-dlp 用（注入 buvid 指纹，yt-dlp 自己的
    # BiliBili 提取器同样会被风控 412，带上指纹能显著降低拦截概率）
    # ★ 写到 DATA_DIR 而非任务目录：job_dir 下的文件可经 /api/download/file 被
    #   服务出去，SESSDATA 不能落进可下载范围；异常路径残留也不暴露。
    cookies_dict = load_cookies() or {}
    cookies_dict.update(_ensure_buvid())
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cookie_file = DATA_DIR / '.bilibili_cookies.txt'
    cookie_args = []
    if cookies_dict:
        # 写 Netscape cookie 文件
        lines = ['# Netscape HTTP Cookie File']
        for name, value in cookies_dict.items():
            if name == 'refresh_token':
                continue
            lines.append(f'.bilibili.com\tTRUE\t/\tFALSE\t0\t{name}\t{value}')
        cookie_file.write_text('\n'.join(lines), encoding='utf-8')
        cookie_args = ['--cookies', str(cookie_file)]
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, f'yt-dlp字幕提取: 使用 {len(cookies_dict)} 个cookie')

    # ★ 关键：记录调用前 output_dir 里已有的 srt 文件，后面只认"新增"的
    existing_srts = set(p.name for p in output_dir.glob('*.srt'))
    existing_txts = set(p.name for p in output_dir.glob('*.txt'))
    if existing_srts and sio:
        from socket_handlers import emit_log
        await emit_log(sio, job_id, f'⚠ 目录已有 {len(existing_srts)} 个旧srt文件，将只认本次新增')

    for sub_flag in ['--write-auto-sub', '--write-sub']:
        ytdlp_args = bilibili_headers + cookie_args + [
            '--no-check-certificate', '--skip-download',
            sub_flag, '--sub-format', 'srt',
            '--sub-langs', 'zh-Hans,zh,zh-CN,ai-zh,all',
            '-o', str(output_dir / '%(title)s.%(ext)s'),
            url,
        ]
        try:
            def _run(args=ytdlp_args):
                return _run_ytdlp(args, timeout=120)
            result = await asyncio.to_thread(_run)
            # ★ 只认本次调用"新增"的 srt（排除旧任务残留）
            current_srts = set(p.name for p in output_dir.glob('*.srt'))
            new_srt_names = current_srts - existing_srts
            if not new_srt_names:
                stderr = result.stderr.decode('utf-8', errors='ignore')[:200] if result.stderr else ''
                if result.returncode != 0:
                    hint = '（B站风控412拦截，进入转录流程）' if '412' in stderr or 'Precondition Failed' in stderr else ''
                    logger.warning(f'yt-dlp字幕提取失败(returncode={result.returncode}){hint}: {stderr}')
                else:
                    logger.info(f'yt-dlp {sub_flag} 成功但未生成新srt（可能视频无字幕）')
                continue  # 没新增就继续试下一个 flag

            # 选轨：按主路径同一套规则（CC 优先 → 简中优先 → 新文件优先），
            # 不再用 mtime 撞运气（多语言同时落盘时会随机挑到繁体/外语轨）
            new_srt_paths = [output_dir / name for name in new_srt_names]
            srt_path = min(new_srt_paths, key=_rank_subtitle_file)

            # ★ 校验 srt 非空（避免拿到空字幕或半成品）
            srt_size = srt_path.stat().st_size
            if srt_size < 50:
                logger.warning(f'新增srt过小({srt_size}字节)，疑似空字幕: {srt_path.name}')
                if sio:
                    from socket_handlers import emit_log
                    await emit_log(sio, job_id, f'⚠ 字幕文件过小({srt_size}字节)，丢弃')
                continue

            # ★ 时间覆盖校验：与主路径共用判据（此前这里只判文件大小，
            #   导致"覆盖不到视频一半的旧/残字幕"被当成成功结果直接送进分段/总结）
            lan = _subtitle_file_lan(srt_path)
            is_ai = lan.startswith('ai')
            entries = _srt_to_entries(srt_path.read_text(encoding='utf-8', errors='ignore'))
            ok, reason = _validate_subtitle_body(entries, video_duration, is_ai)
            incomplete, why = _is_subtitle_body_incomplete(entries, video_duration, is_ai)
            if not ok or incomplete:
                detail = reason or why
                logger.warning(f'yt-dlp 字幕校验未过（{detail}）: {srt_path.name}')
                if sio:
                    from socket_handlers import emit_log
                    await emit_log(sio, job_id, f'⚠ 字幕 {lan or srt_path.stem} 校验未过（{detail}），丢弃')
                continue

            # 生成对应的TXT
            txt_path = srt_path.with_suffix('.txt')
            _srt_to_txt(srt_path, txt_path)
            # 清理临时 cookie 文件
            try:
                cookie_file.unlink(missing_ok=True)
            except BaseException as e:
                logger.debug(f'清理B站临时cookie失败（忽略）: {e}')

            if sio:
                from socket_handlers import emit_log
                await emit_log(sio, job_id, f'✅ yt-dlp字幕提取成功: {srt_path.name} ({srt_size}字节)')

            return {
                "success": True,
                "srt_path": srt_path,
                "txt_path": txt_path,
                "title": srt_path.stem,
                "message": f"yt-dlp字幕提取成功: {srt_path.stem}"
            }
        except subprocess.TimeoutExpired:
            logger.warning(f'yt-dlp {sub_flag} 超时(120s)')
            if sio:
                from socket_handlers import emit_log
                await emit_log(sio, job_id, f'yt-dlp {sub_flag} 超时')
            continue
        except Exception as e:
            logger.warning(f'yt-dlp字幕提取异常: {e}')
            continue
    # 清理临时 cookie 文件
    try:
        cookie_file.unlink(missing_ok=True)
    except BaseException as e:
        logger.debug(f'清理B站临时cookie失败（忽略）: {e}')
    if sio:
        from socket_handlers import emit_log
        await emit_log(sio, job_id, '❌ B站无可用字幕（AI字幕未生成+无CC字幕），进入转录流程')
    # 返回 success=False，main.py 会自动走 download_video + transcribe_audio 流程
    return {"success": False, "message": "B站无可用字幕，进入转录流程"}


async def extract_youtube_subtitles(
    url: str, output_dir: Path, sio=None, job_id: str = '', target_lang: str = 'zh'
) -> dict:
    """优先用 yt-dlp 抓取 YouTube / 通用平台官方字幕（CC + 自动生成）。

    相比 Whisper 转录：更快、更准、不耗 GPU 和带宽。
    成功返回 {success, srt_path, txt_path, title, message}；
    失败（无字幕 / 超时 / 异常）返回 success=False，由 main.py 自动回退到 Whisper 转录。

    target_lang: 期望语言代码，默认 'zh'，决定 --sub-langs 优先级。
    """

    # 字幕语言优先级：中文优先，其次英文兜底
    if target_lang and target_lang.lower().startswith('zh'):
        language_priority = ['zh-Hans', 'zh-CN', 'zh', 'zh-TW', 'en']
    else:
        language_priority = [target_lang, 'en', 'zh-Hans', 'zh-CN', 'zh']
    language_priority = list(dict.fromkeys(lang for lang in language_priority if lang))
    sub_langs = ','.join(language_priority)

    # 只认本次调用"新增"的 srt，排除旧任务残留
    existing_srts = set(p.name for p in output_dir.glob('*.srt'))

    # 先试手动 CC 字幕（质量更好），再试自动生成字幕
    for sub_flag in ['--write-sub', '--write-auto-sub']:
        ytdlp_args = [
            '--ignore-errors', '--no-check-certificate', '--skip-download',
            sub_flag, '--sub-format', 'srt/best', '--convert-subs', 'srt',
            '--sub-langs', sub_langs,
            '-o', str(output_dir / '%(title)s.%(ext)s'),
            url,
        ]
        try:
            def _run(args=ytdlp_args):
                return _run_ytdlp(args, timeout=120)
            result = await asyncio.to_thread(_run)
            new_srt_names = set(p.name for p in output_dir.glob('*.srt')) - existing_srts
            valid_srt_paths = [
                output_dir / name
                for name in new_srt_names
                if (output_dir / name).stat().st_size >= 50
            ]
            if not valid_srt_paths:
                stderr = result.stderr.decode('utf-8', errors='ignore')[:200] if result.stderr else ''
                if result.returncode != 0:
                    logger.warning(f'yt-dlp YouTube字幕提取失败(returncode={result.returncode}): {stderr}')
                else:
                    logger.info(f'yt-dlp {sub_flag} 完成但未生成有效srt（可能视频无字幕）')
                if sio:
                    from socket_handlers import emit_log
                    await emit_log(sio, job_id, f'yt-dlp {sub_flag} 未生成有效字幕')
                continue

            def _subtitle_rank(path: Path):
                filename = path.name.lower()
                for index, lang in enumerate(language_priority):
                    if f'.{lang.lower()}.srt' in filename:
                        return index, -path.stat().st_mtime
                return len(language_priority), -path.stat().st_mtime

            srt_path = min(valid_srt_paths, key=_subtitle_rank)
            srt_size = srt_path.stat().st_size
            if result.returncode != 0:
                logger.warning(
                    f'yt-dlp YouTube字幕部分成功(returncode={result.returncode})，'
                    f'使用已生成字幕: {srt_path.name}'
                )

            txt_path = srt_path.with_suffix('.txt')
            _srt_to_txt(srt_path, txt_path)

            if sio:
                from socket_handlers import emit_log
                partial = '（部分语言下载失败，已使用可用字幕）' if result.returncode != 0 else ''
                await emit_log(sio, job_id, f'✅ YouTube原文字幕抓取成功{partial}: {srt_path.name} ({srt_size}字节)')

            return {
                "success": True,
                "srt_path": srt_path,
                "txt_path": txt_path,
                "title": srt_path.stem,
                "message": f"YouTube原文字幕抓取成功: {srt_path.stem}",
            }
        except subprocess.TimeoutExpired:
            logger.warning(f'yt-dlp {sub_flag} 超时(120s)')
            if sio:
                from socket_handlers import emit_log
                await emit_log(sio, job_id, f'yt-dlp {sub_flag} 超时')
            continue
        except Exception as e:
            logger.warning(f'yt-dlp YouTube字幕提取异常: {e}')
            continue

    if sio:
        from socket_handlers import emit_log
        # 本函数对 YouTube 与通用平台通用，文案不写死平台名
        await emit_log(sio, job_id, '❌ 该视频无可用平台字幕（CC+自动字幕均无），进入转录流程')
    return {"success": False, "message": "无可用平台字幕，进入转录流程"}


def _srt_to_txt(srt_path: Path, txt_path: Path):
    """将SRT转换为带时间戳的TXT"""
    with open(srt_path, 'r', encoding='utf-8') as f_srt, \
         open(txt_path, 'w', encoding='utf-8') as f_txt:
        content = f_srt.read()
        blocks = content.strip().split('\n\n')
        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) >= 3:
                time_line = lines[1]
                text = '\n'.join(lines[2:])
                match = re.match(r'(\d+:\d+:\d+)', time_line)
                start = match.group(1) if match else '00:00'
                f_txt.write(f"[{start}] {text}\n")


def _format_srt_time(seconds: float) -> str:
    """格式化SRT时间戳"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_mmss(seconds: float) -> str:
    """格式化 MM:SS"""
    total_s = int(seconds + 0.5)
    return f"{total_s // 60:02d}:{total_s % 60:02d}"


# ============ 核心下载函数 ============
async def download_video(
    url: str,
    output_dir: Path,
    only_audio: bool = True,
    video_only: bool = False,
    sio=None,
    job_id: str = '',
    max_seconds: Optional[int] = None,
) -> Tuple[str, str, str]:
    """
    用 yt-dlp 下载视频/音频（支持多平台）
    max_seconds：只取开头 N 秒（字幕抽查等"只要片段"场景；失败自动回退完整下载）
    返回：(file_path, title, platform_name)
    """
    # ★ 安全清洗：剥离可能混入的前缀（如列表编号 "3. "、中文逗号、截断的BV片段等）
    url = sanitize_input_url(url)

    platform, need_proxy = detect_platform(url)

    if sio:
        from socket_handlers import emit_progress, emit_log
        await emit_progress(sio, job_id, 'download', 0, f'正在解析 {platform} 链接...')
        await emit_log(sio, job_id, f'平台: {platform}')

    # 确保输出目录存在
    output_dir.mkdir(parents=True, exist_ok=True)

    proxy_args = ['--proxy', PROXY] if need_proxy and PROXY else []

    # B站特殊请求头
    bilibili_headers = []
    if '哔哩哔哩' in platform:
        bilibili_headers = [
            '--add-header', 'Origin:https://www.bilibili.com',
            '--add-header', 'Referer:https://www.bilibili.com',
        ]

    # ===== 第一步：获取视频信息 =====
    info_args = proxy_args + bilibili_headers + [
        '--no-check-certificate', '--dump-json', '--skip-download', url
    ]

    title = url
    duration = 0
    try:
        def _run_info():
            return _run_ytdlp(info_args, timeout=60)
        result = await asyncio.to_thread(_run_info)
        if result.returncode == 0 and result.stdout:
            stdout_text = result.stdout.decode('utf-8', errors='ignore')
            first_line = stdout_text.split('\n', 1)[0]
            info = json.loads(first_line)
            title = info.get('title', url)
            duration = info.get('duration', 0)
    except Exception as e:
        logger.warning(f'获取视频信息失败: {e}')

    # ===== 第二步：下载 =====
    mode_label = '仅音频' if only_audio else ('仅视频' if video_only else '视频+音频')
    if sio:
        from socket_handlers import emit_progress, emit_log
        await emit_progress(sio, job_id, 'download', 10, f'开始下载（{mode_label}）...')
        await emit_log(
            sio, job_id,
            f'⬇️ 开始下载 | 平台: {platform} | 模式: {mode_label} | 标题: {title}'
            + (f' | 时长: {_format_mmss(duration)}' if duration else '')
        )

    # 格式选择
    if only_audio:
        fmt = 'bestaudio[ext=m4a]/bestaudio'
    elif video_only:
        fmt = 'bestvideo[height<=1080]'
    else:
        fmt = 'bestvideo[height<=1080]+bestaudio/best[height<=1080]'

    output_template = str(output_dir / '%(title)s.%(ext)s')
    # ★ 只取开头片段（字幕抽查等场景）：--download-sections 需 ffmpeg 切片（用项目自带）
    section_args = []
    if max_seconds and max_seconds > 0:
        section_args = ['--download-sections', f'*0-{int(max_seconds)}']

    def _build_dl_args(with_section: bool) -> list:
        return proxy_args + bilibili_headers + [
            '--no-check-certificate',
            '-f', fmt,
            *((FFMPEG_ARGS + section_args) if with_section else []),
            '-o', output_template,
            '--newline', url
        ]

    def _run_download(args: list):
        r = _run_ytdlp(args, timeout=600)
        if r.stdout:
            tail = r.stdout.decode('utf-8', errors='ignore')[-800:]
            logger.info(f'yt-dlp输出尾部:\n{tail}')
        return r.returncode

    try:
        if sio:
            from socket_handlers import emit_progress, emit_log
            mode_hint = f'前 {int(max_seconds)}s 片段' if section_args else '完整音频'
            await emit_progress(sio, job_id, 'download', 15, f'下载中（{platform}，{mode_hint}）... 请稍候（最长约10分钟）')
            await emit_log(sio, job_id, f'⬇️ 下载进行中 | yt-dlp 拉流中，完成后汇报文件与大小...')
        returncode = await asyncio.to_thread(_run_download, _build_dl_args(bool(section_args)))
        if returncode != 0 and section_args:
            # 片段下载失败（个别流不支持 section 切割）→ 回退完整下载，保证调用方拿到可用音频
            logger.warning(f'片段下载失败（--download-sections），回退完整下载: {platform}')
            if sio:
                from socket_handlers import emit_log
                await emit_log(sio, job_id, '⚠ 片段下载失败，回退完整音频下载...')
            returncode = await asyncio.to_thread(_run_download, _build_dl_args(False))
    except subprocess.TimeoutExpired:
        raise Exception(f'{platform} 下载超时(600s)')
    except Exception as e:
        raise Exception(f'{platform} 下载失败: {e}')

    if returncode != 0:
        raise Exception(f'yt-dlp下载失败，返回码: {returncode}')

    # 找到下载的文件
    audio_exts = {'.mp3', '.m4a', '.webm', '.mp4', '.wav', '.flac', '.ogg', '.mkv', '.avi'}
    files = [f for f in output_dir.iterdir() if f.suffix.lower() in audio_exts]
    if not files:
        raise Exception('下载完成但未找到音频文件')
    latest = max(files, key=lambda f: f.stat().st_mtime)
    size_mb = latest.stat().st_size / (1024 * 1024)

    if sio:
        from socket_handlers import emit_log, emit_progress
        await emit_log(sio, job_id, f'✅ 下载完成 | 文件: {latest.name} | 大小: {size_mb:.1f} MB')
        await emit_progress(sio, job_id, 'download', 100, f'下载完成！{latest.name} ({size_mb:.1f} MB)')

    return str(latest), title, platform


# ============ 文件上传处理 ============
async def handle_file_upload(file_path: str, output_dir: Path, sio=None, job_id: str = '') -> Tuple[str, str, str]:
    """处理上传的本地音频/视频文件"""
    src = Path(file_path)
    if not src.exists():
        raise Exception(f'文件不存在: {file_path}')

    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / src.name

    # 复制文件到任务目录
    import shutil
    if src.resolve() != dest.resolve():
        shutil.copy2(str(src), str(dest))

    title = src.stem
    platform = "本地文件"

    if sio:
        from socket_handlers import emit_log
        await emit_log(sio, job_id, f'已复制文件: {src.name}')

    return str(dest), title, platform


# ============ 纯文本输入处理 ============
async def handle_text_input(text: str, output_dir: Path, sio=None, job_id: str = '') -> Tuple[Path, Path]:
    """处理直接粘贴的文本，创建虚拟的txt+srt"""
    output_dir.mkdir(parents=True, exist_ok=True)

    txt_path = output_dir / 'input_text.txt'
    srt_path = output_dir / 'input_text.srt'

    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(text)

    # 为纯文本创建简单的SRT（每1000字一段）
    chunk_size = 1000
    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
    with open(srt_path, 'w', encoding='utf-8') as f:
        for i, chunk in enumerate(chunks):
            start = i * 30  # 每1000字假设30秒
            end = (i + 1) * 30
            f.write(f"{i+1}\n{_format_srt_time(start)} --> {_format_srt_time(end)}\n{chunk}\n\n")

    if sio:
        from socket_handlers import emit_log
        await emit_log(sio, job_id, f'文本输入: {len(text)} 字符')

    return txt_path, srt_path


# ============ 上传字幕文件处理 ============
def _read_text_with_fallback(path: Path) -> str:
    """读取常见字幕编码，避免 UTF-16/本地编码 SRT 上传后任务失败。"""
    raw = path.read_bytes()
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        encodings = ('utf-16', 'utf-8-sig', 'gb18030')
    elif raw.startswith(b'\xef\xbb\xbf'):
        encodings = ('utf-8-sig', 'utf-16', 'gb18030')
    else:
        encodings = ('utf-8', 'gb18030', 'utf-16')
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def _vtt_to_srt(text: str) -> str:
    """将常见 WebVTT cue 转成 SRT，保留字幕时间轴。"""
    blocks = re.split(r'\r?\n\r?\n+', text.lstrip('\ufeff'))
    output = []
    index = 1
    for block in blocks:
        lines = [line.strip('\ufeff') for line in block.splitlines()]
        timing_index = next((i for i, line in enumerate(lines) if '-->' in line), None)
        if timing_index is None:
            continue
        start, end = [part.strip().split(' ', 1)[0] for part in lines[timing_index].split('-->', 1)]
        content = [line for line in lines[timing_index + 1:] if line.strip()]
        if not content:
            continue
        content_text = '\n'.join(content)
        output.append(
            f'{index}\n{start.replace(".", ",")} --> {end.replace(".", ",")}\n{content_text}'
        )
        index += 1
    return '\n\n'.join(output) + ('\n\n' if output else '')


async def handle_subtitle_file(
    file_path: str, output_dir: Path, sio=None, job_id: str = ''
) -> Tuple[Path, Path]:
    """读取上传的 SRT/VTT，保留原时间轴并生成统一的 TXT。"""
    src = Path(file_path)
    if not src.exists():
        raise Exception(f'文件不存在: {file_path}')

    output_dir.mkdir(parents=True, exist_ok=True)
    text = _read_text_with_fallback(src)
    srt_text = _vtt_to_srt(text) if src.suffix.lower() == '.vtt' else text.lstrip('\ufeff')
    srt_text = srt_text.replace('\r\n', '\n').replace('\r', '\n')
    if '-->' not in srt_text or not srt_text.strip():
        raise Exception('字幕文件内容无效：未找到有效的时间轴')

    srt_path = output_dir / 'input_text.srt'
    txt_path = output_dir / 'input_text.txt'
    srt_path.write_text(srt_text, encoding='utf-8')
    _srt_to_txt(srt_path, txt_path)

    if sio:
        from socket_handlers import emit_log
        await emit_log(sio, job_id, f'字幕文件已读取: {src.name}')
    return txt_path, srt_path


# ============ Windows 睡眠管理 ============
_sleep_prevent_count = 0

def prevent_sleep():
    """阻止系统睡眠(Windows)"""
    global _sleep_prevent_count
    if sys.platform == 'win32':
        try:
            import ctypes
            _sleep_prevent_count += 1
            if _sleep_prevent_count == 1:
                ES_CONTINUOUS = 0x80000000
                ES_SYSTEM_REQUIRED = 0x00000001
                ES_AWAYMODE_REQUIRED = 0x00000040
                ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED)
        except Exception as e:
            logger.warning(f"prevent_sleep异常: {e}")


def allow_sleep():
    """允许系统睡眠"""
    global _sleep_prevent_count
    if sys.platform == 'win32':
        try:
            import ctypes
            _sleep_prevent_count = max(0, _sleep_prevent_count - 1)
            if _sleep_prevent_count == 0:
                ES_CONTINUOUS = 0x80000000
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception as e:
            logger.warning(f"allow_sleep异常: {e}")
