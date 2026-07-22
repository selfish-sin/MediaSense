"""
YouTube Tool V2 - 视频下载服务
支持：YouTube/B站/抖音/小红书/Vimeo/Twitter等平台
使用 yt-dlp subprocess + B站字幕API
"""
import asyncio
import hashlib
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

from config import DOWNLOAD_DIR, PROXY
from services.bilibili_cookies import load_cookies, save_cookies, clear_cookies, get_status

logger = logging.getLogger(__name__)

# Wbi 签名用
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 52, 1, 44, 30, 22, 25,
    24, 13, 6, 21, 40, 37, 34, 41, 17, 7, 36, 39, 11, 26,
]
_wbi_key_cache = {"img_key": None, "sub_key": None, "expires": 0}


def _get_wbi_keys() -> Tuple[Optional[str], Optional[str]]:
    """获取 B站 wbi img_key 和 sub_key（带缓存）

    nav API 在未登录时返回 code=-101 但 wbi_img 数据仍然存在，所以不依赖 code==0。
    """
    now = time.time()
    if _wbi_key_cache["expires"] > now and _wbi_key_cache["img_key"]:
        return _wbi_key_cache["img_key"], _wbi_key_cache["sub_key"]
    try:
        cookies = load_cookies()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com",
        }
        if cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
            headers["Cookie"] = cookie_str
        resp = httpx.get("https://api.bilibili.com/x/web-interface/nav", headers=headers, timeout=10.0)
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

    B 站逐步收紧了未签名的 ``player/v2`` 接口。先走当前的 WBI
    接口，再用浏览器常见参数尝试旧接口，避免把“接口返回空字幕”和
    “请求被风控”混为一谈。
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://www.bilibili.com',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    cookies = load_cookies() or {}
    if cookies:
        headers['Cookie'] = '; '.join(f'{k}={v}' for k, v in cookies.items())

    urls = [
        _build_wbi_url(
            'https://api.bilibili.com/x/player/wbi/v2',
            {'aid': aid, 'cid': cid, 'bvid': bvid},
        ),
        'https://api.bilibili.com/x/player/v2?' + urlencode({
            'aid': aid,
            'cid': cid,
            'part': 1,
            'dm_img_str': 'V2ViR0VuR2FyOlxyZXR1cm4gTmV3cyBLZXkgUGFnZSBMaXN0Ig==',
            'dm_cover_img_str': '',
            'dm_img_list': '[]',
            'dm_img_crypto': '',
            'web_location': '1315875',
        }),
    ]
    for url in urls:
        try:
            resp = httpx.get(url, headers=headers, timeout=15.0)
            data = resp.json()
            if data.get('code') != 0:
                logger.warning(
                    f'B站字幕接口返回错误: code={data.get("code")}, '
                    f'msg={data.get("message")}'
                )
                continue
            subtitle = data.get('data', {}).get('subtitle', {})
            if subtitle.get('subtitles'):
                return subtitle
        except Exception as e:
            logger.warning(f'B站字幕接口调用失败: {e}')
    return {}


async def extract_bilibili_subtitles(url: str, output_dir: Path, sio=None, job_id: str = '') -> dict:
    """
    提取B站字幕（AI字幕 / 创作者上传字幕）
    策略：bilibili-api-python库（完整wbi签名+风控处理）→ yt-dlp兜底 → Whisper转录
    返回 dict: {"success": True/False, "srt_path": Path, "txt_path": Path, "title": str, "message": str}
    """
    match = re.search(r'(BV[A-Za-z0-9]+)', url)
    if not match:
        # 短链不一定在 URL 中直接带 BV 号，跟随一次重定向再解析。
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=True,
                headers={'User-Agent': 'Mozilla/5.0'},
            ) as client:
                resolved = await client.head(url)
            match = re.search(r'(BV[A-Za-z0-9]+)', str(resolved.url))
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
        info = bili_sync(v.get_info())
        aid = info.get('aid', 0)
        cid = info.get('cid', 0)
        video_title = info.get('title', bvid)
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
            # 页数不足则用第1P（已在上方获取）

        # Step 2: 获取字幕列表
        # v17.4.2+: get_subtitle 内部用 player/wbi/v2 需要 SESSDATA，无登录时会抛异常
        # 无登录态时改用 player/v2 直接调用（无需 WBI 签名）
        if credential is not None:
            try:
                sub_data = bili_sync(v.get_subtitle(cid=cid))
            except Exception as e:
                logger.warning(f'bilibili-api-python 字幕接口失败: {e}')
                sub_data = {}
        else:
            sub_data = {}

        # 账号 Cookie 不完整、接口版本变化或 WBI 签名要求变化时，
        # 统一回退到直接播放器 API，而不是立刻放弃字幕。
        if not isinstance(sub_data, dict) or not sub_data.get('subtitles'):
            sub_data = _fetch_subtitles_via_player_v2(aid, cid, bvid)
        # sub_data 格式: {"allow_submit":..., "lan":..., "subtitles":[{lan, lan_doc, subtitle_url, ai_status}], ...}
        subtitles = sub_data.get('subtitles', []) if isinstance(sub_data, dict) else []

        # 优先选中文（AI字幕 > 中文自动 > 中文 > 任何中文）
        zh_subs = [s for s in subtitles if 'zh' in s.get('lan', '').lower() or 'ai' in s.get('lan', '').lower()]
        if not zh_subs:
            zh_subs = subtitles

        return aid, cid, video_title, zh_subs

    try:
        aid, cid, video_title, zh_subs = await asyncio.to_thread(_do_extract)
    except Exception as e:
        logger.warning(f'bilibili-api-python 提取字幕失败: {e}')
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, f'⚠ 库提取失败: {e}，转yt-dlp兜底')
        return await _extract_bilibili_subtitles_ytdlp(url, output_dir, sio, job_id)

    if not zh_subs:
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, '⚠ 该视频无可用字幕（B站未生成AI字幕+无CC字幕），将走Whisper转录')
        logger.info(f'B站无字幕 bvid={bvid}，转yt-dlp/Whisper')
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

    # 下载字幕内容
    api_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://www.bilibili.com',
    }
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

    # 写入SRT和TXT
    srt_path = output_dir / f'{video_title}.srt'
    txt_path = output_dir / f'{video_title}.txt'
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


async def _extract_bilibili_subtitles_ytdlp(url: str, output_dir: Path, sio=None, job_id: str = '') -> dict:
    """yt-dlp兜底提取B站字幕（带cookie）
    关键防误认：只认"本次调用新增"的srt，避免把旧任务残留当成成功结果
    """
    yt_dlp_cmd = [sys.executable, '-m', 'yt_dlp'] + FFMPEG_ARGS
    bilibili_headers = [
        '--add-header', 'Origin:https://www.bilibili.com',
        '--add-header', 'Referer:https://www.bilibili.com',
    ]

    # 把 cookie 写成 Netscape 格式文件给 yt-dlp 用
    cookies_dict = load_cookies()
    cookie_file = output_dir / '.bilibili_cookies.txt'
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
        cmd = yt_dlp_cmd + bilibili_headers + cookie_args + [
            '--no-check-certificate', '--skip-download',
            sub_flag, '--sub-format', 'srt',
            '--sub-langs', 'zh-Hans,zh,zh-CN,ai-zh,all',
            '-o', str(output_dir / '%(title)s.%(ext)s'),
            url,
        ]
        try:
            def _run(cmd=cmd):
                return subprocess.run(cmd, capture_output=True, timeout=120)
            result = await asyncio.to_thread(_run)
            # ★ 只认本次调用"新增"的 srt（排除旧任务残留）
            current_srts = set(p.name for p in output_dir.glob('*.srt'))
            new_srt_names = current_srts - existing_srts
            if not new_srt_names:
                stderr = result.stderr.decode('utf-8', errors='ignore')[:200] if result.stderr else ''
                if result.returncode != 0:
                    logger.warning(f'yt-dlp字幕提取失败(returncode={result.returncode}): {stderr}')
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
        await emit_log(sio, job_id, '❌ B站无可用字幕（AI字幕未生成+无CC字幕），将下载音频走Whisper转录')
    # 返回 success=False，main.py 会自动走 download_video + transcribe_audio 流程
    return {"success": False, "message": "B站无可用字幕，将走Whisper转录"}


async def extract_youtube_subtitles(
    url: str, output_dir: Path, sio=None, job_id: str = '', target_lang: str = 'zh'
) -> dict:
    """优先用 yt-dlp 抓取 YouTube / 通用平台官方字幕（CC + 自动生成）。

    相比 Whisper 转录：更快、更准、不耗 GPU 和带宽。
    成功返回 {success, srt_path, txt_path, title, message}；
    失败（无字幕 / 超时 / 异常）返回 success=False，由 main.py 自动回退到 Whisper 转录。

    target_lang: 期望语言代码，默认 'zh'，决定 --sub-langs 优先级。
    """
    yt_dlp_cmd = [sys.executable, '-m', 'yt_dlp'] + FFMPEG_ARGS

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
        cmd = yt_dlp_cmd + [
            '--ignore-errors', '--no-check-certificate', '--skip-download',
            sub_flag, '--sub-format', 'srt/best', '--convert-subs', 'srt',
            '--sub-langs', sub_langs,
            '-o', str(output_dir / '%(title)s.%(ext)s'),
            url,
        ]
        try:
            def _run(cmd=cmd):
                return subprocess.run(cmd, capture_output=True, timeout=120)
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
        await emit_log(sio, job_id, '❌ 该视频无可用字幕（CC+自动字幕均无），将下载音频走Whisper转录')
    return {"success": False, "message": "YouTube无可用字幕，将走Whisper转录"}


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
) -> Tuple[str, str, str]:
    """
    用 yt-dlp 下载视频/音频（支持多平台）
    返回：(file_path, title, platform_name)
    """
    # ★ 安全清洗：剥离可能混入的前缀（如列表编号 "3. "、中文逗号等）
    url = url.strip()
    # 剥除开头的非 URL 前缀：编号、中文文字、标点等
    # 匹配模式: "3. https://..." → "https://..."
    m = re.match(r'^(?:[\s\d]+[\.\、\s]+)*(https?://.+)', url)
    if m:
        url = m.group(1)
    elif not url.startswith('http'):
        # 如果连 http 都不是，尝试从中提取
        inner = re.search(r'(https?://\S+)', url)
        if inner:
            url = inner.group(1)

    platform, need_proxy = detect_platform(url)

    if sio:
        from socket_handlers import emit_progress, emit_log
        await emit_progress(sio, job_id, 'download', 0, f'正在解析 {platform} 链接...')
        await emit_log(sio, job_id, f'平台: {platform}')

    # 确保输出目录存在
    output_dir.mkdir(parents=True, exist_ok=True)

    proxy_args = ['--proxy', PROXY] if need_proxy and PROXY else []
    yt_dlp_cmd = [sys.executable, '-m', 'yt_dlp'] + FFMPEG_ARGS

    # B站特殊请求头
    bilibili_headers = []
    if '哔哩哔哩' in platform:
        bilibili_headers = [
            '--add-header', 'Origin:https://www.bilibili.com',
            '--add-header', 'Referer:https://www.bilibili.com',
        ]

    # ===== 第一步：获取视频信息 =====
    cmd_info = yt_dlp_cmd + proxy_args + bilibili_headers + [
        '--no-check-certificate', '--dump-json', '--skip-download', url
    ]

    title = url
    duration = 0
    try:
        def _run_info():
            return subprocess.run(cmd_info, capture_output=True, timeout=60)
        result = await asyncio.to_thread(_run_info)
        if result.returncode == 0 and result.stdout:
            info = json.loads(result.stdout.decode('utf-8', errors='ignore'))
            title = info.get('title', url)
            duration = info.get('duration', 0)
    except Exception as e:
        logger.warning(f'获取视频信息失败: {e}')

    # ===== 第二步：下载 =====
    if sio:
        from socket_handlers import emit_progress
        await emit_progress(sio, job_id, 'download', 10, '开始下载...')

    # 格式选择
    if only_audio:
        fmt = 'bestaudio[ext=m4a]/bestaudio'
    elif video_only:
        fmt = 'bestvideo[height<=1080]'
    else:
        fmt = 'bestvideo[height<=1080]+bestaudio/best[height<=1080]'

    output_template = str(output_dir / '%(title)s.%(ext)s')
    cmd_dl = yt_dlp_cmd + proxy_args + bilibili_headers + [
        '--no-check-certificate',
        '-f', fmt,
        '-o', output_template,
        '--newline', url
    ]

    # 用 subprocess + asyncio.to_thread 避免 asyncio.create_subprocess_exec 在 Windows 上的问题
    # ★ 关键修复：必须用 communicate() 读取 PIPE 输出，否则输出缓冲区满后子进程会死锁，
    #   proc.wait() 永远等不到退出 → 600s timeout → "下载失败"
    def _run_download():
        proc = subprocess.Popen(
            cmd_dl,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            stdout_data, _ = proc.communicate(timeout=600)
            # 保留输出尾部用于排查（yt-dlp 失败时 stderr 会写这里）
            if stdout_data:
                tail = stdout_data.decode('utf-8', errors='ignore')[-800:]
                logger.info(f'yt-dlp输出尾部:\n{tail}')
            return proc.returncode
        except subprocess.TimeoutExpired:
            # 超时先 kill 再 communicate 清理，避免僵尸进程
            proc.kill()
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            raise
        except Exception:
            # 其他异常也确保进程不残留
            try:
                proc.kill()
            except Exception:
                pass
            raise

    try:
        if sio:
            from socket_handlers import emit_progress
            await emit_progress(sio, job_id, 'download', 10, '下载中... 请稍候')
        returncode = await asyncio.to_thread(_run_download)
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

    if sio:
        from socket_handlers import emit_log, emit_progress
        await emit_log(sio, job_id, f'文件: {latest.name}')
        await emit_progress(sio, job_id, 'download', 100, '下载完成！')

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
