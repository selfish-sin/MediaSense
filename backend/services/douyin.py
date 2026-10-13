"""
抖音 URL 解析与音频获取（游客链路，无登录态）。

链路：v.douyin.com 短链 302 展开 → aweme_id 提取 → ttwid 铸造（bytedance 注册接口）
→ a_bogus 签名（services/douyin_abogus.py，vendor 自 f2）→ detail 接口（元数据/直链/AI字幕）
→ play_addr 直链 httpx 流式下载。

背景（2026-10-05 PoC 真机实测，详见 .trellis/tasks/10-05-10-05-douyin-url-pipeline/）：
- yt-dlp 的 Douyin extractor 需要浏览器完整 cookie 且 2026 年仍频繁 403，不采用；
- detail 接口游客可拿完整元数据与无水印直链（x-tt-argus 网关头发占位值即可，f2 issue #443）；
- AI 字幕（video.cla_info.caption_infos）游客态实测常为空 → 拿不到走转录链，宁缺勿错；
- 签名只借 vendor 的单个纯 Python 文件，零新增第三方依赖（SM3 走已有 cryptography）。

稳性设计（对齐 B站 412 递增惩罚的教训）：
- 所有 detail/短链展开请求共享全局限流（默认 ≥1s，DOUYIN_API_MIN_INTERVAL 可调），批量不齐射；
- 元数据按 aweme_id 做 90s 进程级缓存——同一任务内"字幕探测 + 下载"共用一次 detail 调用；
- play_addr 直链有时效，下载失败自动重新拉取 detail（绕过缓存）换新直链重试一次。
"""
import asyncio
import logging
import os
import random
import re
import string
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from services.douyin_abogus import ABogus, BrowserFingerprintGenerator

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

_DETAIL_ENDPOINT = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
_TTWID_REGISTER = "https://ttwid.bytedance.com/ttwid/union/register/"

# detail 接口的静态 query 基底（复刻浏览器 webapp 请求指纹，f2 PostDetail 同源）
_STATIC_DETAIL_PARAMS: Dict[str, object] = {
    "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
    "pc_client_type": 1, "publish_video_strategy_type": 2, "pc_libra_divert": "Windows",
    "version_code": "290100", "version_name": "29.1.0", "cookie_enabled": "true",
    "screen_width": 1920, "screen_height": 1080, "browser_language": "zh-CN",
    "browser_platform": "Win32", "browser_name": "Edge", "browser_version": "130.0.0.0",
    "browser_online": "true", "engine_name": "Blink", "engine_version": "130.0.0.0",
    "os_name": "Windows", "os_version": "10", "cpu_core_num": 12, "device_memory": 8,
    "platform": "PC", "downlink": 10, "effective_type": "4g", "round_trip_time": 100,
}

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_FFMPEG_BIN = _PROJECT_ROOT / 'bin' / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')

_DETAIL_TIMEOUT = 20          # detail 接口单次超时
_DOWNLOAD_TIMEOUT = 600       # 直链下载总超时（与 yt-dlp 链路一致）
_DOWNLOAD_CHUNK = 256 * 1024
_API_MIN_INTERVAL = float(os.environ.get('DOUYIN_API_MIN_INTERVAL', '1.0'))
_META_CACHE_TTL = 90.0        # 元数据缓存秒数（play_url 直链时效远大于此，下载失败会绕缓存重拉）


class DouyinError(Exception):
    """抖音解析/下载失败（message 面向用户可行动）"""


# ============ 全局限流（detail / 短链展开共享；对齐 B站"风控递增惩罚"教训，批量不齐射） ============
_api_lock = threading.Lock()
_last_api_ts = [0.0]


def _throttle_douyin_api() -> None:
    """进程级最小间隔限流（阻塞调用，跑在线程池，不占事件循环）"""
    with _api_lock:
        wait = _API_MIN_INTERVAL - (time.monotonic() - _last_api_ts[0])
        if wait > 0:
            time.sleep(wait)
        _last_api_ts[0] = time.monotonic()


# ============ 游客身份（ttwid 进程级缓存） ============
_TTWID_CACHE = {'value': '', 'ts': 0.0}
_TTWID_TTL = 6 * 3600


def _mint_ttwid() -> str:
    """向字节游客注册接口铸造 ttwid（公开无鉴权接口，开源方案通用配方）"""
    body = {
        "region": "cn", "aid": 1768, "needFid": False,
        "service": "www.ixigua.com",
        "migrate_info": {"ticket": "", "source": "node"},
        "cbUrlProtocol": "https", "union": True,
    }
    r = httpx.post(_TTWID_REGISTER, json=body, timeout=15)
    ttwid = r.cookies.get("ttwid", "")
    if not ttwid:
        raise DouyinError("无法获取抖音游客身份（ttwid 注册接口无响应），请检查网络后重试")
    _TTWID_CACHE.update(value=ttwid, ts=time.time())
    return ttwid


def _get_ttwid() -> str:
    if _TTWID_CACHE['value'] and time.time() - _TTWID_CACHE['ts'] < _TTWID_TTL:
        return _TTWID_CACHE['value']
    return _mint_ttwid()


def _false_mstoken() -> str:
    """msToken 用随机假值即可（detail 接口实测不校验其内容，f2 同款姿势）"""
    base = ''.join(random.choices(string.ascii_letters + string.digits + '_-', k=116))
    return base + '='


def _detail_headers(ttwid: str) -> Dict[str, str]:
    return {
        "User-Agent": _UA,
        "Referer": "https://www.douyin.com/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cookie": f"ttwid={ttwid};",
        # 2026-08 起网关校验该头存在性（ArgusSecurityPlugin），占位值即可（f2 #443）
        "x-tt-argus": "1",
    }


def _sign_endpoint(params: Dict) -> str:
    """用 vendor 的 ABogus 对 query 串签名，拼出完整 detail 端点"""
    param_str = "&".join(f"{k}={v}" for k, v in params.items())
    fp = BrowserFingerprintGenerator.generate_fingerprint("Edge")
    ab = ABogus(fp=fp, user_agent=_UA).generate_abogus(param_str, "")
    a_bogus = ab[0] if isinstance(ab, (list, tuple)) else ab
    return f"{_DETAIL_ENDPOINT}?{param_str}&a_bogus={a_bogus}"


def fetch_detail_sync(aweme_id: str) -> dict:
    """拉取单个作品 detail（aweme_detail）；空响应时换新 ttwid 重试一次"""
    last_err = ''
    for attempt in (1, 2):
        try:
            _throttle_douyin_api()
            ttwid = _get_ttwid()
            params = {**_STATIC_DETAIL_PARAMS, "msToken": _false_mstoken(), "aweme_id": aweme_id}
            r = httpx.get(_sign_endpoint(params), headers=_detail_headers(ttwid),
                          timeout=_DETAIL_TIMEOUT, follow_redirects=True)
            j = r.json()
        except Exception as e:
            last_err = f'{type(e).__name__}: {e}'
            logger.warning(f'抖音 detail 请求异常（第{attempt}次）: {last_err}')
            continue
        d = j.get("aweme_detail") or {}
        if d:
            return d
        last_err = (f'status_code={j.get("status_code")} '
                    f'filter_reason={(j.get("filter_detail") or {}).get("filter_reason", "")}')
        logger.warning(f'抖音 detail 空响应（第{attempt}次）: {last_err}，换新游客身份重试')
        if attempt < 2:
            _mint_ttwid()
    raise DouyinError(f"抖音接口未返回视频数据（可能已删除/设为私密/触发风控），可稍后重试。详情: {last_err}")


# ============ URL 识别与 aweme_id 提取 ============
_DY_HOSTS = ("douyin.com", "iesdouyin.com")

# 可直接从 URL 提取 id 的形态（无网络）：
#   /video/<id>、/note/<id>、iesdouyin share /video/<id>、user 页 modal_id=<id>
_AWEME_ID_RE = re.compile(r'/(?:video|note)/(\d{15,21})')
_MODAL_ID_RE = re.compile(r'[?&]modal_id=(\d{15,21})')
_SHORT_HOST_RE = re.compile(r'^(?:https?://)?v\.douyin\.com/([A-Za-z0-9]+)/?$')


def is_douyin_url(url: str) -> bool:
    """hostname 精确匹配抖音域（含 v./www. 子域与 iesdouyin 分享域）"""
    try:
        hostname = (urlparse(url).hostname or '').lower()
    except Exception:
        return False
    return any(hostname == h or hostname.endswith('.' + h) for h in _DY_HOSTS)


def extract_aweme_id_from_text(url: str) -> str:
    """无网络版 id 提取（normalize_url 去重用），匹配不到返回空串"""
    m = _AWEME_ID_RE.search(url or '') or _MODAL_ID_RE.search(url or '')
    return m.group(1) if m else ''


def _expand_short_link_sync(url: str) -> str:
    """v.douyin.com 短链 302 展开，返回最终落地 URL"""
    try:
        _throttle_douyin_api()
        r = httpx.get(url, headers={"User-Agent": _UA}, timeout=15, follow_redirects=True)
        return str(r.url)
    except Exception as e:
        logger.warning(f'抖音短链展开失败: {type(e).__name__}: {e}')
        return url


def extract_aweme_id_sync(url: str) -> str:
    """从任意抖音 URL 形态提取 aweme_id（短链自动 302 展开；裸短链无 scheme 也容错）"""
    text = (url or '').strip()
    if text.startswith('v.douyin.com/'):
        text = f'https://{text}'
    m = _AWEME_ID_RE.search(text) or _MODAL_ID_RE.search(text)
    if m:
        return m.group(1)
    if _SHORT_HOST_RE.match(text):
        expanded = _expand_short_link_sync(text)
        m = _AWEME_ID_RE.search(expanded) or _MODAL_ID_RE.search(expanded)
        if m:
            return m.group(1)
    return ''


# ============ 元数据（aweme_id 级 90s 缓存：同一任务内字幕探测+下载共用一次 detail） ============
_META_CACHE: Dict[str, Tuple[float, dict]] = {}
_meta_cache_lock = threading.Lock()


def fetch_video_meta_sync(url: str, refresh: bool = False) -> dict:
    """解析 URL → detail 元数据：{aweme_id, title, author, duration_ms, play_url, captions}。

    refresh=True 绕过缓存强制重拉（下载失败换新直链时用）。
    """
    aweme_id = extract_aweme_id_sync(url)
    if not aweme_id:
        raise DouyinError(
            "无法从链接中解析出抖音视频ID。支持：douyin.com/video/<id>、"
            "v.douyin.com 短链、分享链接（iesdouyin.com）、主页弹层链接（modal_id=）"
        )
    if not refresh:
        with _meta_cache_lock:
            hit = _META_CACHE.get(aweme_id)
        if hit and time.monotonic() - hit[0] < _META_CACHE_TTL:
            return hit[1]
    d = fetch_detail_sync(aweme_id)
    video = d.get("video") or {}
    play_urls = ((video.get("play_addr") or {}).get("url_list") or [])
    author = d.get("author") or {}
    meta = {
        "aweme_id": aweme_id,
        "title": (d.get("desc") or "").strip() or aweme_id,
        "author": author.get("nickname") or "",
        "duration_ms": int(video.get("duration") or 0),
        "play_url": play_urls[0] if play_urls else "",
        "captions": ((video.get("cla_info") or {}).get("caption_infos") or []),
    }
    with _meta_cache_lock:
        _META_CACHE[aweme_id] = (time.monotonic(), meta)
    return meta


async def fetch_video_meta(url: str, refresh: bool = False) -> dict:
    return await asyncio.to_thread(fetch_video_meta_sync, url, refresh)


# ============ AI 字幕（cla_info → SRT/TXT；游客态常为空，属已知边界） ============
def _pick_caption(captions: List[dict]) -> Optional[dict]:
    """选轨：中文优先（zh*），其余兜底取第一条"""
    if not captions:
        return None
    zh = [c for c in captions if str(c.get("lang") or "").lower().startswith("zh")]
    return (zh or captions)[0]


def _parse_vtt(vtt_text: str) -> List[Tuple[str, str, str]]:
    """VTT → [(start, end, text)]；时间 00:00:01.000 → SRT 的 00:00:01,000"""
    cues: List[Tuple[str, str, str]] = []
    lines = (vtt_text or "").replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line:
            times = [t.strip().split(" ")[0] for t in line.split("-->")]
            texts: List[str] = []
            i += 1
            while i < len(lines) and lines[i].strip() and "-->" not in lines[i]:
                texts.append(lines[i].strip())
                i += 1
            body = "\n".join(texts).strip()
            if body and len(times) == 2:
                cues.append((times[0].replace(".", ","), times[1].replace(".", ","), body))
        i += 1
    return cues


def _caption_to_srt_txt(vtt_text: str) -> Tuple[str, str]:
    cues = _parse_vtt(vtt_text)
    srt_parts = [f"{idx}\n{start} --> {end}\n{body}\n" for idx, (start, end, body) in enumerate(cues, 1)]
    txt = "\n".join(re.sub(r"<[^>]+>", "", body) for _, _, body in cues)
    return "\n".join(srt_parts), txt


async def _log(sio, job_id: str, msg: str) -> None:
    """sio 缺省安全版日志（无 socket 时静默，进程日志仍由调用方打点）"""
    if sio is not None:
        from socket_handlers import emit_log
        await emit_log(sio, job_id, msg)


async def _progress(sio, job_id: str, step: str, pct: int, msg: str) -> None:
    if sio is not None:
        from socket_handlers import emit_progress
        await emit_progress(sio, job_id, step, pct, msg)


async def extract_douyin_subtitles(url: str, output_dir: Path,
                                   sio=None, job_id: str = '') -> dict:
    """尝试获取抖音 AI 字幕（cla_info），返回与 extract_bilibili_subtitles 同契约。

    拿不到（无字幕/接口风控）返回 success=False，由调用方回退转录，宁缺勿错。
    """
    try:
        meta = await fetch_video_meta(url)
    except DouyinError as e:
        await _log(sio, job_id, f'⚠️ 抖音元数据获取失败：{e}')
        return {'success': False, 'message': str(e)}

    title = meta["title"]
    caption = _pick_caption(meta["captions"])
    if not caption:
        return {'success': False, 'message': '该视频无可用的AI字幕（游客接口），转转录'}
    cap_url = (caption.get("url_list") or [""])[0]
    if not cap_url:
        return {'success': False, 'message': 'AI字幕轨无下载地址，转转录'}

    try:
        def _dl() -> str:
            r = httpx.get(cap_url, headers={"User-Agent": _UA, "Referer": "https://www.douyin.com/"},
                          timeout=30, follow_redirects=True)
            r.raise_for_status()
            return r.text

        vtt_text = await asyncio.to_thread(_dl)
        srt_text, txt_text = _caption_to_srt_txt(vtt_text)
        if len(srt_text) < 50:
            return {'success': False, 'message': 'AI字幕内容为空（疑似降级响应），转转录'}
        from services.naming import safe_basename
        base = safe_basename(title)[:80] or meta["aweme_id"]
        srt_path = output_dir / f'{base}.srt'
        txt_path = output_dir / f'{base}.txt'
        srt_path.write_text(srt_text, encoding='utf-8')
        txt_path.write_text(txt_text, encoding='utf-8')
        await _log(sio, job_id, f'✅ 抖音AI字幕已下载（lang={caption.get("lang")}）')
        return {'success': True, 'srt_path': str(srt_path), 'txt_path': str(txt_path),
                'title': title, 'message': '已获取抖音AI字幕'}
    except Exception as e:
        logger.warning(f'抖音AI字幕下载失败: {type(e).__name__}: {e}')
        return {'success': False, 'message': f'AI字幕下载失败：{e}'}


# ============ 直链下载（音频/视频共用 play_addr） ============
def _partial_path(dest: Path) -> Path:
    """半截产物路径：`{base}.mp4` → `{base}.mp4.part`（后缀不匹配任何媒体白名单）。

    ★ 治本（B1）：直链下载曾直接写最终名 `{base}.mp4`，中途断流就在 job_dir 里留下
    一个几 MB 的**截断 mp4**。main._acquire_audio 的断点续传扫描只按「后缀 + size>1000」
    捞文件（`.mp4` 在白名单里、截断文件 mtime 最新）→ 被当「已有完整音频」跳过下载 →
    送进 ASR → **产出静默截断的转录并标 completed**。故一律先写 `.part`，
    完整落盘后再 `Path.replace()` 原子改名，失败路径删 `.part`，目录里不留半截 `.mp4`。
    """
    return dest.parent / f'{dest.name}.part'


def _stream_download_sync(play_url: str, dest: Path, prog: Optional[dict] = None) -> None:
    """httpx 流式下载直链到 `{dest}.part`，完整后原子改名为 dest（阻塞，跑在线程池）。

    prog 为共享进度字典 {'done','total'}，由 async 壳轮询汇报（线程里只写数字，不碰 socket）。
    """
    headers = {"User-Agent": _UA, "Referer": "https://www.douyin.com/"}
    part = _partial_path(dest)
    try:
        with httpx.stream("GET", play_url, headers=headers,
                          timeout=httpx.Timeout(_DOWNLOAD_TIMEOUT, read=60), follow_redirects=True) as r:
            if r.status_code != 200:
                raise DouyinError(f"抖音直链下载失败（HTTP {r.status_code}），直链可能已过期，请重试")
            if prog is not None:
                prog['total'] = int(r.headers.get("content-length") or 0)
                prog['done'] = 0
            with open(part, "wb") as f:
                for chunk in r.iter_bytes(_DOWNLOAD_CHUNK):
                    f.write(chunk)
                    if prog is not None:
                        prog['done'] += len(chunk)
        if part.stat().st_size <= 0:
            raise DouyinError(f"抖音直链下载为空（{dest.name}），请重试")
        part.replace(dest)
    finally:
        # 失败/取消路径清掉半截文件：绝不让 .mp4.part 变成续传扫描的候选
        try:
            part.unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f'清理抖音半截下载文件失败（{part.name}: {e}）')


def _download_via_ffmpeg_sync(play_url: str, dest: Path, max_seconds: int) -> None:
    """ffmpeg 直读直链截取开头 N 秒（字幕抽查场景，不付整段下载成本）。

    同样走 `.part` + 原子改名（见 `_partial_path` 的 B1 说明）：ffmpeg 被中断时
    也不会在任务目录留下一个「看起来完整」的 mp4。
    """
    part = _partial_path(dest)
    try:
        cmd = [str(_FFMPEG_BIN), '-y', '-user_agent', _UA,
               '-i', play_url, '-t', str(int(max_seconds)),
               '-c', 'copy', str(part)]
        p = subprocess.run(cmd, capture_output=True, timeout=180)
        if p.returncode != 0 or not part.exists() or part.stat().st_size < 1024:
            raise DouyinError(f'ffmpeg 截取失败: {p.stderr.decode("utf-8", "ignore")[-200:]}')
        part.replace(dest)
    finally:
        try:
            part.unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f'清理抖音半截截取文件失败（{part.name}: {e}）')


async def download_douyin_video(url: str, output_dir: Path, sio=None, job_id: str = '',
                                max_seconds: Optional[int] = None) -> Tuple[str, str, str]:
    """下载抖音视频（play_addr 直链 mp4），与 download_video 同契约。

    max_seconds>0 时用项目自带 ffmpeg 截取开头 N 秒（字幕抽查场景）。
    直链有时效：下载失败自动重新拉取 detail（绕过元数据缓存）换新直链重试一次。
    返回 (file_path, title, platform)。
    """
    from services.naming import safe_basename

    await _progress(sio, job_id, 'download', 0, '正在解析抖音链接...')
    meta = None
    try:
        meta = await fetch_video_meta(url)
    except DouyinError as e:
        raise Exception(f'抖音解析失败: {e}')

    title = meta["title"]
    duration_s = meta["duration_ms"] / 1000
    await _log(sio, job_id,
               f'平台: 抖音 | 作者: {meta["author"]}' if meta["author"] else '平台: 抖音')
    await _log(sio, job_id, f'⬇️ 开始下载 | 平台: 抖音 | 标题: {title}'
               + (f' | 时长: {int(duration_s // 60):02d}:{int(duration_s % 60):02d}' if duration_s else ''))
    if not meta["play_url"]:
        raise Exception('抖音未返回可下载的视频直链（可能为图文/直播等不支持类型）')

    output_dir.mkdir(parents=True, exist_ok=True)
    base = safe_basename(title)[:80] or meta["aweme_id"]
    dest = output_dir / f'{base}.mp4'

    last_err: Optional[Exception] = None
    for attempt in (1, 2):
        if attempt == 2:
            # 直链可能已过期：绕过缓存重拉 detail 换新直链
            await _log(sio, job_id, '⚠ 抖音直链下载失败，重新解析换新直链重试...')
            try:
                meta = await fetch_video_meta(url, refresh=True)
            except DouyinError as e:
                raise Exception(f'抖音解析失败: {e}')
            if not meta["play_url"]:
                raise Exception('抖音未返回可下载的视频直链（可能为图文/直播等不支持类型）')
        prog: dict = {'done': 0, 'total': 0}
        try:
            if max_seconds and max_seconds > 0 and _FFMPEG_BIN.exists():
                await _log(sio, job_id, f'⬇️ 下载开头 {int(max_seconds)}s 片段（ffmpeg 直读直链）...')
                await asyncio.to_thread(_download_via_ffmpeg_sync, meta["play_url"], dest, int(max_seconds))
            else:
                await _progress(sio, job_id, 'download', 15, '下载中（抖音）... 请稍候')
                dl_task = asyncio.create_task(
                    asyncio.to_thread(_stream_download_sync, meta["play_url"], dest, prog))
                last_pct = 15
                while not dl_task.done():
                    await asyncio.sleep(2)
                    if prog['total']:
                        pct = 15 + int(prog['done'] / prog['total'] * 80)
                        if pct != last_pct:
                            last_pct = pct
                            await _progress(sio, job_id, 'download', pct,
                                            f'下载中（抖音）{prog["done"] / 1048576:.0f}MB'
                                            + (f'/{prog["total"] / 1048576:.0f}MB' if prog['total'] else ''))
                await dl_task  # 传播线程内异常
            last_err = None
            break
        except (DouyinError, httpx.HTTPError, OSError, subprocess.TimeoutExpired) as e:
            last_err = e
            logger.warning(f'抖音直链下载失败（第{attempt}次）: {type(e).__name__}: {e}')

    if last_err is not None:
        # 兜底：清掉本次失败可能留下的半截产物（正常路径已在 _stream_download_sync
        # 的 finally 清理，这里防御协程被取消 / 历史崩溃残留）。
        # 只删 .part，不删 dest——dest 只可能是上一次「完整」下载的产物，删了会毁掉
        # 断点续传复用。
        part = _partial_path(dest)
        try:
            part.unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f'清理抖音半截下载文件失败（{part.name}: {e}）')
        raise Exception(f'抖音下载失败: {last_err}')

    size_mb = dest.stat().st_size / (1024 * 1024)
    await _log(sio, job_id, f'✅ 下载完成 | 文件: {dest.name} | 大小: {size_mb:.1f} MB')
    await _progress(sio, job_id, 'download', 100, f'下载完成！{dest.name} ({size_mb:.1f} MB)')
    return str(dest), title, '抖音'
