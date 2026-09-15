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


def _bili_headers(bvid: str = '') -> dict:
    """构造尽量接近浏览器的 B站 API 请求头：完整 UA + buvid 指纹 + 真实视频页 Referer"""
    headers = {
        'User-Agent': FULL_CHROME_UA,
        'Referer': f'https://www.bilibili.com/video/{bvid}' if bvid else 'https://www.bilibili.com',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    cookies = load_cookies() or {}
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
def _fetch_subtitles_via_player_v2(aid, cid, bvid):
    """同步调用 B 站播放器 API 获取字幕列表。

    接口顺序：先走实测更稳的旧版 ``player/v2``（带浏览器常参数），
    wbi/v2 降级为备用；wbi 若被风控(412)则强制刷新签名密钥重试一次。
    返回 (subtitle_dict, timelength_ms, api_ok)：
    - timelength 为视频时长（毫秒），用于字幕时长校验（防旧/错误字幕），取不到为 0
    - api_ok 表示是否至少有一个接口正常响应（code==0，即使字幕为空）——
      用于区分“B站确认无字幕”（可跳过 yt-dlp 兜底）和“接口被风控/异常”（必须兜底）
    """
    headers = _bili_headers(bvid)

    def _try(url: str):
        _throttle_bili_api()
        try:
            resp = httpx.get(url, headers=headers, timeout=15.0)
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
    return {}, 0, api_ok


async def extract_bilibili_subtitles(url: str, output_dir: Path, sio=None, job_id: str = '') -> dict:
    """
    提取B站字幕（AI字幕 / 创作者上传字幕）
    策略：bilibili-api-python库（完整wbi签名+风控处理）→ yt-dlp兜底 → 转录流程
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
        from socket_handlers import emit_log, emit_progress
        await emit_log(sio, job_id, f'尝试提取B站AI字幕 (bvid={bvid}, p={page})')

    # 加载登录态 Cookie 构造 Credential（bilibili-api-python 需要）
    cookies_dict = load_cookies()
    credential = None
    if cookies_dict and cookies_dict.get('SESSDATA') and cookies_dict.get('bili_jct') and cookies_dict.get('DedeUserID'):
        try:
            from bilibili_api import Credential
            credential = Credential(
                sessdata=cookies_dict['SESSDATA'],
                bili_jct=cookies_dict['bili_jct'],
                dedeuserid=cookies_dict['DedeUserID'],
            )
            if sio:
                from socket_handlers import emit_log
                await emit_log(sio, job_id, '✅ 已加载B站登录态Credential')
        except ImportError:
            if sio:
                from socket_handlers import emit_log
                await emit_log(sio, job_id, '⚠ bilibili-api-python未安装，转yt-dlp兜底')
            return await _extract_bilibili_subtitles_ytdlp(url, output_dir, sio, job_id)
    else:
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, '⚠ 未登录B站，AI字幕可能无法获取（仅CC字幕可用）')

    # bilibili-api-python 库的同步IO需要在线程池里跑，避免阻塞事件循环
    try:
        from bilibili_api import video as bili_video, sync as bili_sync
    except ImportError:
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, '⚠ bilibili-api-python未安装，转yt-dlp兜底')
        return await _extract_bilibili_subtitles_ytdlp(url, output_dir, sio, job_id)

    def _do_extract():
        """同步函数：在线程池中执行，使用 bilibili-api-python 提取字幕"""
        v = bili_video.Video(bvid=bvid, credential=credential)
        # Step 1: 获取视频信息（aid/cid/标题/分P列表）
        _throttle_bili_api()
        info = bili_sync(v.get_info())
        aid = info.get('aid', 0)
        cid = info.get('cid', 0)
        video_title = info.get('title', bvid)
        # 视频时长（秒），供字幕时长校验（防旧/错误字幕）
        try:
            video_duration = float(info.get('duration') or 0)
        except (TypeError, ValueError):
            video_duration = 0.0
        pages = info.get('pages', []) or []

        # 多P：根据 ?p=N 从 pages 取对应分P的 cid
        if page > 1 and pages:
            if len(pages) >= page:
                page_info = pages[page - 1]
                page_cid = page_info.get('cid')
                page_title = page_info.get('part', '')
                if page_cid:
                    cid = page_cid
                    video_title = f'{video_title} - P{page} {page_title}'
                try:
                    page_dur = float(page_info.get('duration') or 0)
                except (TypeError, ValueError):
                    page_dur = 0.0
                if page_dur:
                    video_duration = page_dur
            # 页数不足则用第1P（已在上方获取）

        # Step 2: 获取字幕列表
        # v17.4.2+: get_subtitle 内部用 player/wbi/v2 需要 SESSDATA，无登录时会抛异常
        # 无登录态时改用 player/v2 直接调用（无需 WBI 签名）
        api_ok = False
        if credential is not None:
            try:
                _throttle_bili_api()
                sub_data = bili_sync(v.get_subtitle(cid=cid))
                # 库返回了字幕列表结构（即使为空）即视为接口正常响应
                if isinstance(sub_data, dict) and 'subtitles' in sub_data:
                    api_ok = True
            except Exception as e:
                logger.warning(f'bilibili-api-python 字幕接口失败: {e}')
                sub_data = {}
        else:
            sub_data = {}

        # 账号 Cookie 不完整、接口版本变化或 WBI 签名要求变化时，
        # 统一回退到直接播放器 API，而不是立刻放弃字幕。
        if not isinstance(sub_data, dict) or not sub_data.get('subtitles'):
            fb_data, tl_ms, fb_ok = _fetch_subtitles_via_player_v2(aid, cid, bvid)
            sub_data = fb_data if fb_data else sub_data
            api_ok = api_ok or fb_ok
            if tl_ms:
                video_duration = tl_ms / 1000.0
        # sub_data 格式: {"allow_submit":..., "lan":..., "subtitles":[{lan, lan_doc, subtitle_url, ai_status}], ...}
        subtitles = sub_data.get('subtitles', []) if isinstance(sub_data, dict) else []

        # 优先选中文：中文AI字幕(ai-zh) / 中文CC字幕(zh-*) 都算；
        # 绝不把 ai-en / ai-jp 之类的非中文字幕误当中文用
        zh_subs = [
            s for s in subtitles
            if (s.get('lan') or '').lower().startswith('zh')
            or (s.get('lan') or '').lower().startswith('ai-zh')
        ]
        if not zh_subs:
            # 退而求其次：任意 AI 字幕（视频本身可能是外文）
            zh_subs = [s for s in subtitles if (s.get('lan') or '').lower().startswith('ai')]
        if not zh_subs:
            zh_subs = subtitles

        return aid, cid, video_title, zh_subs, video_duration, api_ok

    try:
        aid, cid, video_title, zh_subs, video_duration, api_ok = await asyncio.to_thread(_do_extract)
    except Exception as e:
        logger.warning(f'bilibili-api-python 提取字幕失败: {e}')
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, f'⚠ 库提取失败: {e}，转yt-dlp兜底')
        return await _extract_bilibili_subtitles_ytdlp(url, output_dir, sio, job_id)

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
        return await _extract_bilibili_subtitles_ytdlp(url, output_dir, sio, job_id)

    if sio:
        from socket_handlers import emit_log
        sub_info = [(s.get('lan', ''), s.get('lan_doc', '')) for s in zh_subs]
        await emit_log(sio, job_id, f'找到 {len(zh_subs)} 条字幕: {sub_info}')

    # 下载字幕JSON（库返回的 subtitle_url 可直接下载，库已确保URL正确）
    chosen = zh_subs[0]
    sub_url = chosen.get('subtitle_url', '')
    if not sub_url:
        return {"success": False, "message": "字幕URL为空"}

    # 处理协议相对URL: //aisubtitle.hdslb.com/...
    if sub_url.startswith('//'):
        sub_url = 'https:' + sub_url
    elif sub_url.startswith('/'):
        sub_url = 'https://api.bilibili.com' + sub_url
    elif not sub_url.startswith('http'):
        sub_url = 'https://' + sub_url

    logger.info(f'字幕下载: bvid={bvid}, aid={aid}, cid={cid}, lan={chosen.get("lan","")}')

    # 下载字幕内容（带上完整浏览器指纹，避免 aisubtitle 也被风控）
    api_headers = _bili_headers(bvid)
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=api_headers) as sub_client:
            resp = await sub_client.get(sub_url)
            if resp.status_code != 200:
                return {"success": False, "message": f"字幕下载失败: HTTP {resp.status_code}"}
            sub_json = resp.json()
    except Exception as e:
        logger.warning(f'字幕下载失败: {e}')
        return {"success": False, "message": f"字幕下载异常: {e}"}

    # 校验字幕内容非空
    if isinstance(sub_json, dict):
        body = sub_json.get('body', [])
    elif isinstance(sub_json, list):
        body = sub_json
    else:
        body = []
    if not body:
        return {"success": False, "message": "字幕JSON内容为空"}

    # ★ 字幕时长校验：B站 AI 字幕在换源/重传后可能仍是旧内容（相当于“历史数据”），
    # 与当前视频对不上却会被当成转录结果一路送到分段/总结。字幕时间覆盖范围
    # 明显偏离视频时长时判定可疑，回退本地转录而不是导出错误内容。
    sub_duration = 0.0
    try:
        ends = [
            float(item['to']) for item in body
            if isinstance(item, dict) and item.get('to') is not None
        ]
        if ends:
            sub_duration = max(ends)
    except (TypeError, ValueError):
        pass
    if video_duration and sub_duration:
        if sub_duration < video_duration * 0.5 - 30 or sub_duration > video_duration + 120:
            msg = (f'B站字幕时长校验不通过：字幕覆盖 {sub_duration:.0f}s，'
                   f'视频 {video_duration:.0f}s，疑似旧/错误字幕，转本地转录')
            logger.warning(msg)
            if sio:
                from socket_handlers import emit_log
                await emit_log(sio, job_id, f'⚠ {msg}')
            return {"success": False, "message": "字幕时长与视频时长不匹配，疑似旧字幕", "reason": "duration_mismatch"}

    # 写入SRT和TXT（固定文件名，避免 Windows 非法字符 / ADS 旁路）
    # 视频标题只存返回字段，不进路径
    srt_path = output_dir / 'bilibili_subtitle.srt'
    txt_path = output_dir / 'bilibili_subtitle.txt'
    _write_subtitle_files(sub_json, srt_path, txt_path)

    if sio:
        from socket_handlers import emit_log, emit_progress
        await emit_log(sio, job_id, f'✅ B站AI字幕提取成功: {video_title} ({len(body)}条)')
        await emit_progress(sio, job_id, 'transcribe', 100, 'B站AI字幕已获取，跳过本地转录')

    return {
        "success": True,
        "srt_path": srt_path,
        "txt_path": txt_path,
        "title": video_title,
        "message": f"字幕提取成功: {video_title}"
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
    （免费槽位优先，免费档不可用才用付费档），云端失败回退本地 Whisper base，
    双失败按现行语义保留字幕（不让校验本身拖垮任务）。
    字幕与音频是两个独立来源，内容对不上即说明字幕不是当前视频的。
    返回 (是否保留字幕, 说明)。
    """
    try:
        from services.transcriber import transcribe_audio_sample
        sub_head = _subtitle_head_text(txt_path, max_seconds)
        asr_text: Optional[str] = None
        engine_used = 'local'

        # ★ 云端抽查（provider 已配置才走）：90s 片段一次 API 调用
        #   provider 选择单点 = cloud_asr.preferred_provider()（免费槽位优先，与转录默认
        #   引擎的「免费优先」同口径）——抽查只是校验手段，不该烧付费额度。此前硬编码默认
        #   provider（mimo 付费档）导致付费档欠费（402）时每个任务白重试 3 次退避。
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


async def _extract_bilibili_subtitles_ytdlp(url: str, output_dir: Path, sio=None, job_id: str = '') -> dict:
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

            # 选最新的新增 srt
            new_srt_paths = [output_dir / name for name in new_srt_names]
            srt_path = max(new_srt_paths, key=lambda f: f.stat().st_mtime)

            # ★ 校验 srt 非空（避免拿到空字幕或半成品）
            srt_size = srt_path.stat().st_size
            if srt_size < 50:
                logger.warning(f'新增srt过小({srt_size}字节)，疑似空字幕: {srt_path.name}')
                if sio:
                    from socket_handlers import emit_log
                    await emit_log(sio, job_id, f'⚠ 字幕文件过小({srt_size}字节)，丢弃')
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
