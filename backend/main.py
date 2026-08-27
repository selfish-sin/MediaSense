"""
YouTube Tool V2 - FastAPI 入口 + 任务处理主流程
启动 uvicorn + Socket.IO + SPA 路由 + process_job 编排
"""
import asyncio
import sys
import inspect

# Windows 必须用 ProactorEventLoop 才能支持 asyncio.create_subprocess_exec
# 否则 yt-dlp 等子进程调用会抛 NotImplementedError
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import logging
import time
from pathlib import Path
from typing import Dict, Any

import socketio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.cors import CORSMiddleware

from config import SERVER_HOST, SERVER_PORT, STATIC_DIR, DOWNLOAD_DIR, DEFAULT_MODEL, DATA_DIR
from db.database import init_db, get_connection
from db.job_repo import create_job, get_job, update_job, finish_job, fail_interrupted_jobs
from db.model_repo import get_default_model_key
from socket_handlers import emit_log, emit_progress

# ============ 日志配置 ============
from logging.handlers import RotatingFileHandler
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "backend.log"

file_handler = RotatingFileHandler(
    str(LOG_FILE), maxBytes=5*1024*1024, backupCount=3, encoding="utf-8"
)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)s] %(name)s: %(message)s', datefmt='%H:%M:%S'
))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler(), file_handler],
)
logger = logging.getLogger(__name__)

# ============ 运行中的任务追踪 ============
RUNNING_JOBS: Dict[str, Dict[str, Any]] = {}

# ============ 全局并发限制 ============
# ★ 批量任务或用户快速连续提交时，避免同时跑太多任务把 CPU/网络/磁盘 IO 拖垮
#   RTX 3060 + 6900HX 同时跑 2-3 个 Whisper 转录已接近上限
#   超过此数的任务会在 pending 状态排队，等信号量释放
MAX_CONCURRENT_JOBS = 2
_job_semaphore = None  # 在 startup 里创建（需要 event loop）


def get_job_semaphore():
    """懒加载获取信号量（必须在 event loop 内创建）"""
    global _job_semaphore
    if _job_semaphore is None:
        _job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
    return _job_semaphore

# ============ Socket.IO (async mode) ============
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins='*',
    logger=False,
    engineio_logger=False,
)


async def _safe_emit(event: str, *args, **kwargs):
    """Emit through Socket.IO while tolerating sync test doubles."""
    result = sio.emit(event, *args, **kwargs)
    if inspect.isawaitable(result):
        await result


async def _emit_batch_progress(job_id: str):
    """批量任务子任务到终态（完成/失败/取消）后，推送该批次整体进度。

    负载与 GET /api/jobs/batch/{id} 的 summary 同构（单一事实源：
    get_batch_summary）。非批量任务直接跳过；前端据此实时更新批量面板，
    替代高频 HTTP 轮询。
    """
    try:
        from db.job_repo import get_job, get_batch_summary
        job = get_job(job_id)
        if not job or not job.get('batch_id'):
            return
        summary = get_batch_summary(job['batch_id'])
        if summary:
            await _safe_emit('batch_progress', summary)
    except Exception as e:
        logger.warning(f'batch_progress 推送失败 (job={job_id}): {e}')

# ============ FastAPI 应用 ============
app = FastAPI(
    title="YouTube Tool V2",
    description="本地视频/音频智能处理工作站",
    version="2.0.0",
)

# ★ 安全：本应用生产/局域网访问均为同源（前端由后端 static 提供），
#   dev 环境 vite 走 proxy 也是同源 —— 三种正常路径都不触发 CORS。
#   故无需放开任意来源，仅白名单 localhost/127.0.0.1（任意端口），
#   杜绝局域网内恶意网页跨域读取本机 API。
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============ 挂载 Socket.IO（路径与前端 path:'/ws/socket.io' 一致） ============
socket_app = socketio.ASGIApp(sio, app, socketio_path='/ws/socket.io')

# ============ 注册路由 ============
from routers.jobs import router as jobs_router
from routers.jobs_ai import router as jobs_ai_router
from routers.jobs_batch import router as jobs_batch_router
from routers.models import router as models_router
from routers.summary import router as summary_router
from routers.export import router as export_router
from routers.translate import router as translate_router
from routers.bilibili import router as bilibili_router
from routers.download import router as download_router
from routers.templates import router as templates_router
from routers.chat import router as chat_router
from routers.search import router as search_router
from routers.storage_cleanup import router as storage_cleanup_router
# ★ 格式转换已外包到独立工具，路由已注释
# from routers.converter import router as converter_router

app.include_router(jobs_router,   prefix="/api/jobs",      tags=["jobs"])
# ★ jobs_ai 先于 jobs_batch 注册：保持原单路由内 /{job_id}/summary-history
#   先于 /batch/{batch_id} 的匹配顺序（/batch/summary-history 这类边缘路径行为不变）
app.include_router(jobs_ai_router, prefix="/api/jobs",     tags=["jobs-ai"])
app.include_router(jobs_batch_router, prefix="/api/jobs",  tags=["jobs-batch"])
app.include_router(models_router,  prefix="/api/models",    tags=["models"])
app.include_router(summary_router, prefix="/api/summary",   tags=["summary"])
app.include_router(export_router,  prefix="/api/export",    tags=["export"])
app.include_router(translate_router, prefix="/api/translate", tags=["translate"])
app.include_router(bilibili_router, prefix="/api/bilibili", tags=["bilibili"])
app.include_router(download_router, prefix="/api/download", tags=["download"])
app.include_router(templates_router, prefix="/api/templates", tags=["templates"])
app.include_router(chat_router,    prefix="/api/jobs",     tags=["chat"])


app.include_router(
    storage_cleanup_router,
    prefix='/api/storage-cleanup',
    tags=['storage-cleanup'],
)


# ── 日志 API（独立路由，避免被 /api/jobs/{job_id} 通配抓走）──
@app.get("/api/logs", tags=["system"])
async def get_logs(lines: int = 300):
    """返回后端日志文件最近N行"""
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.read().splitlines()
        return {"lines": all_lines[-lines:], "total": len(all_lines)}
    except FileNotFoundError:
        return {"lines": [], "total": 0}
app.include_router(search_router,  prefix="",             tags=["search"])
# ★ 格式转换已外包，路由已禁用
# app.include_router(converter_router, prefix="/api/converter", tags=["converter"])


# ============ Token 统计接口（直接挂在 main，不单独建 router） ============
@app.get("/api/token-usage/history", tags=["token"])
async def get_token_usage_history(limit: int = 30):
    """获取 Token 使用历史（按每次任务记录）"""
    conn = get_connection()
    rows = conn.execute("""
        SELECT tu.id, tu.job_id, tu.model_key, tu.input_tokens, tu.output_tokens,
               tu.created_at, j.title, j.summary_type
        FROM token_usage tu
        LEFT JOIN jobs j ON tu.job_id = j.id
        ORDER BY tu.created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return {
        "records": [
            {
                "id": r["id"],
                "job_id": r["job_id"],
                "model": r["model_key"],
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "total_tokens": (r["input_tokens"] or 0) + (r["output_tokens"] or 0),
                "title": r["title"] or "",
                "summary_type": r["summary_type"] or "",
                "created_at": r["created_at"],
            }
            for r in rows
        ],
        "total": len(rows),
    }


@app.get("/api/token-usage", tags=["token"])
async def get_token_usage():
    """获取 Token 使用统计（总量 + 按模型分列）"""
    conn = get_connection()
    c = conn.cursor()

    # 总量
    c.execute("SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) FROM token_usage")
    row = c.fetchone()
    total_input, total_output = int(row[0]), int(row[1])

    # 按模型分列
    c.execute("""
        SELECT model_key,
               COALESCE(SUM(input_tokens),0) AS input_tokens,
               COALESCE(SUM(output_tokens),0) AS output_tokens
        FROM token_usage
        GROUP BY model_key
        ORDER BY (input_tokens + output_tokens) DESC
    """)
    by_model = {}
    for r in c.fetchall():
        by_model[r[0] or 'unknown'] = {
            "input_tokens": int(r[1]),
            "output_tokens": int(r[2]),
        }

    conn.close()
    return {
        "total_input": total_input,
        "total_output": total_output,
        "by_model": by_model,
    }

# ============ 静态文件 + SPA fallback ============
# 生产模式：Vue build输出到 STATIC_DIR (backend/static/)
# base='/' → JS引用路径为 /assets/xxx.js
# 需要为 /assets/* 和根路径 / 返回静态文件，同时不拦截 /api/* 和 /ws/*
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")
    logger.info(f"静态文件目录: {STATIC_DIR}")


def _serve_index():
    """返回 index.html，强制不缓存（确保浏览器每次拿到最新版本）"""
    from fastapi.responses import Response
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return {"message": "Vue前端尚未构建，请先运行 cd frontend && npm run build"}
    content = index_file.read_bytes()
    return Response(
        content=content,
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )


@app.get("/")
async def serve_vue_root():
    """根路径返回SPA（不缓存）"""
    return _serve_index()


@app.get("/{path:path}")
async def serve_vue_spa(path: str):
    """SPA fallback：非API路径返回index.html（不缓存）"""
    # 尝试返回静态文件（如 favicon.ico 等）
    # ★ 安全：先 resolve() 归一化再用 is_relative_to 校验，防 `..` 路径穿越
    #    （字符串 startswith 无法防御 ../ 逐层上溯）
    try:
        static_file = (STATIC_DIR / path).resolve()
    except (OSError, ValueError):
        return _serve_index()
    if static_file.is_file() and static_file.is_relative_to(STATIC_DIR.resolve()):
        return FileResponse(str(static_file))
    # 否则返回 index.html（SPA路由，不缓存）
    return _serve_index()


# ============ Socket.IO 事件处理 ============
from socket_handlers import register_socket_events
register_socket_events(sio)


# ============ 输入获取策略函数（_process_job_impl Step 1 拆分） ============
# 按输入类型获取转录文本，统一返回 (srt_path, txt_path, seg_count, title, platform, files)。
# ★ emit_* 在函数内 import（与原 _process_job_impl 一致），保证测试能以
#   patch("socket_handlers.emit_xxx") 方式替换。


class JobCancelledError(Exception):
    """策略函数内部检测到用户取消；由 _process_job_impl 统一收尾（信号量交由外层释放）。"""


async def _extract_bvid(input_url: str):
    """从B站 URL 提取 BV 号；短链 b23.tv 通过 HEAD 重定向解析。"""
    if '/BV' in input_url:
        return input_url.split('/BV')[1].split('?')[0].split('/')[0]
    if '@' in input_url:
        try:
            import requests as _req

            def _resolve_redirect():
                resp = _req.head(input_url, allow_redirects=False, timeout=5)
                return resp.headers.get('Location', '')

            real_url = await asyncio.to_thread(_resolve_redirect)
            if '/BV' in real_url:
                return real_url.split('/BV')[1].split('?')[0].split('/')[0]
        except Exception:
            pass
    return None


async def _handle_text_input(text, job_dir, sio, job_id, title, platform, progress_msg):
    """纯文本/文本文件输入：直接生成 txt+srt，跳过下载与转录。"""
    from socket_handlers import emit_progress
    from services.downloader import handle_text_input as _text_input
    txt_path_obj, srt_path_obj = await _text_input(text, job_dir, sio, job_id)
    update_job(job_id, title=title, platform=platform, status='transcribing',
               progress_msg=progress_msg)
    await emit_progress(sio, job_id, 'transcribe', 100, progress_msg)
    return str(srt_path_obj), str(txt_path_obj), len(text) // 1000 + 1, title, platform, {}


async def _handle_subtitle_file(input_file, job_dir, sio, job_id):
    """字幕文件输入（.srt/.vtt）：直接转换，跳过下载与转录。"""
    from socket_handlers import emit_progress
    from services.downloader import handle_subtitle_file
    txt_path_obj, srt_path_obj = await handle_subtitle_file(
        input_file, job_dir, sio=sio, job_id=job_id
    )
    title = Path(input_file).stem
    update_job(job_id, title=title, platform='文本文件', status='transcribing',
               progress_msg='文本文件已读取，跳过转录')
    await emit_progress(sio, job_id, 'transcribe', 100, '文本文件已读取，跳过转录')
    return str(srt_path_obj), str(txt_path_obj), 0, title, '文本文件', {}


async def _handle_media_file(input_file, job_dir, sio, job_id, model_size, speaker_diarization, language):
    """音视频文件上传：提取音频后 Whisper 转录。"""
    from services.downloader import handle_file_upload
    from services.transcriber import transcribe_audio
    audio_path, title, platform = await handle_file_upload(input_file, job_dir, sio, job_id)
    files = {'audio': str(audio_path)}
    update_job(job_id, status='transcribing', progress_msg='开始转录上传文件')
    srt_path, txt_path, seg_count = await transcribe_audio(
        audio_path, model_size=model_size, platform=platform,
        speaker_diarization=speaker_diarization, language=language,
        sio=sio, job_id=job_id
    )
    return srt_path, txt_path, seg_count, title, platform, files


async def _handle_url_input(input_url, job_dir, sio, job_id, options,
                            model_size, speaker_diarization, language):
    """URL 输入：B站字幕（含内容抽查）→ 平台原文字幕 → 下载+Whisper 转录（含断点续传）。"""
    from socket_handlers import check_cancelled
    from services.downloader import (
        download_video, extract_bilibili_subtitles, verify_subtitle_against_audio,
        detect_platform, extract_youtube_subtitles,
    )
    from services.transcriber import transcribe_audio

    platform, _ = detect_platform(input_url)
    title = ''
    files = {}
    srt_path = None
    txt_path = None
    seg_count = 0

    # B站特殊路径：优先尝试提取字幕（CC + AI 双路径）
    # ★ force_transcribe=True 时跳过B站字幕，直接下载音频+Whisper转录
    #   适用于B站AI字幕质量差、需要更高准确度、或不想依赖B站登录态的场景
    force_transcribe = options.get('force_transcribe', False)
    is_bilibili = 'bilibili' in input_url.lower() or 'b23.tv' in input_url.lower()
    # 字幕内容抽查时预下载的音频；校验不过时复用，避免二次下载
    pre_downloaded_audio = None

    if is_bilibili and force_transcribe:
        await emit_log(sio, job_id, '🔧 已开启「强制转录」，跳过B站字幕提取，直接下载音频用Whisper转录（更慢但准确度更高）')
    elif is_bilibili:
        bvid = await _extract_bvid(input_url)
        # 短链不一定在输入中直接包含 BV，仍交给提取器做重定向解析。
        if not bvid:
            bvid = '待解析'
        if bvid:
            await emit_log(sio, job_id, f'📄 检测到B站视频，尝试提取字幕（BV={bvid}）...')

            # 读取登录态 cookie（需要AI字幕时必须）
            from services.bilibili_cookies import load_cookies
            _cookies = load_cookies() or {}
            if not _cookies.get('SESSDATA'):
                await emit_log(sio, job_id, '⚠️ 未登录B站，AI字幕可能无法获取（仅CC字幕可用）')

            # 调用字幕提取（异步，不阻塞事件循环）
            sub_result = await extract_bilibili_subtitles(input_url, job_dir, sio, job_id)

            if sub_result.get('success'):
                srt_path = sub_result['srt_path']
                txt_path = sub_result['txt_path']
                # 用B站API返回的视频标题（不是字幕内容第一行）
                title = sub_result.get('title') or bvid
                await emit_log(sio, job_id, f'✅ 字幕提取成功：{sub_result.get("message", "已获取字幕")}')
                update_job(job_id, title=title, platform=platform, status='transcribing',
                           progress_msg='B站字幕已获取（跳过下载和转录）')

                # ★ B站字幕内容抽查（默认开启，subtitle_check=false 可关）：
                # B站 AI 字幕在换源/重传后可能仍是旧内容（“历史数据”），
                # 与当前视频对不上却会被一路送进分段/总结。用本地音频开头
                # 与字幕开头做相似度比对，对不上就丢弃字幕改走 Whisper 转录。
                if not force_transcribe and options.get('subtitle_check', True):
                    try:
                        await emit_log(sio, job_id, '🧪 B站字幕内容抽查：下载音频并与字幕开头比对（防旧/错误字幕）...')
                        audio_path, _, _ = await download_video(
                            input_url, job_dir, only_audio=True, sio=sio, job_id=job_id
                        )
                        ok, _detail = await verify_subtitle_against_audio(
                            txt_path, str(audio_path), sio=sio, job_id=job_id
                        )
                        if ok:
                            await emit_log(sio, job_id, f'✅ B站字幕内容抽查通过（{_detail}），使用该字幕')
                        else:
                            await emit_log(sio, job_id, f'⚠ B站字幕内容抽查未通过（{_detail}）：疑似旧/错误字幕，丢弃，转本地Whisper转录')
                            for _p in (srt_path, txt_path):
                                try:
                                    Path(_p).unlink(missing_ok=True)
                                except OSError:
                                    pass
                            pre_downloaded_audio = str(audio_path)
                            srt_path = None
                            txt_path = None
                    except Exception as e:
                        logger.warning(f'B站字幕抽查异常（保留原字幕结果）: {e}')
                        await emit_log(sio, job_id, f'⚠ B站字幕抽查不可用（{e}），按原结果使用字幕')
            else:
                await emit_log(sio, job_id, f'⚠️ 字幕提取失败：{sub_result.get("message", "")}')

    # YouTube / 通用平台：优先抓官方字幕（CC + 自动生成），比 Whisper 更快更准更省
    # 抓取失败（无字幕/超时）则自动回退到下面的下载+Whisper 转录
    if not srt_path and not is_bilibili and not force_transcribe:
        await emit_log(sio, job_id, '📄 尝试抓取YouTube/平台原文字幕（CC+自动）...')
        yt_sub = await extract_youtube_subtitles(
            input_url, job_dir, sio, job_id, target_lang=language or 'zh'
        )
        if yt_sub.get('success'):
            srt_path = yt_sub['srt_path']
            txt_path = yt_sub['txt_path']
            title = yt_sub.get('title') or title
            await emit_log(sio, job_id, f'✅ 原文字幕抓取成功：{yt_sub.get("message", "已获取字幕")}')
            update_job(job_id, title=title, platform=platform, status='transcribing',
                       progress_msg='原文字幕已获取（跳过转录）')
        else:
            await emit_log(sio, job_id, f'⚠️ 原文字幕抓取失败：{yt_sub.get("message", "")}，转Whisper转录')

    # 如果没有B站字幕 → 正常下载+转录
    if not srt_path:
        # ★ 断点续传：检测是否已有下载完的文件（重试场景）
        _retry = options.get('_retry', False)
        file_path = None
        skip_download = False
        if pre_downloaded_audio:
            # ★ 字幕内容抽查时已下载音频，校验不过直接复用，避免二次下载
            file_path = Path(pre_downloaded_audio)
            skip_download = True
            await emit_log(sio, job_id, '↩ 复用字幕抽查时下载的音频，跳过重复下载')
        elif _retry:
            # 搜索 job_dir 下已有音频/视频文件
            audio_exts = ('.m4a', '.mp3', '.wav', '.opus', '.aac', '.webm', '.mp4', '.mkv')
            for f in sorted(job_dir.glob('*'), key=lambda x: x.stat().st_mtime, reverse=True):
                if f.suffix.lower() in audio_exts and f.stat().st_size > 1000:
                    file_path = f
                    title = title or f.stem
                    skip_download = True
                    await emit_log(sio, job_id, f'🔁 断点续传：发现已有文件 {f.name}（{f.stat().st_size//1024}KB），跳过下载')
                    break

        if not skip_download:
            update_job(job_id, status='downloading', progress_msg='开始下载')

            # 下载
            if options.get('download_video_only', False):
                file_path, title, platform = await download_video(
                    input_url, job_dir, only_audio=False, video_only=True, sio=sio, job_id=job_id
                )
                files['video'] = str(file_path)
            elif options.get('download_video', False):
                file_path, title, platform = await download_video(
                    input_url, job_dir, only_audio=False, sio=sio, job_id=job_id
                )
                files['video'] = str(file_path)
            else:
                file_path, title, platform = await download_video(
                    input_url, job_dir, only_audio=True, sio=sio, job_id=job_id
                )
                files['audio'] = str(file_path)

        if not file_path:
            raise Exception("下载失败，未获取到音频/视频文件")

        update_job(job_id, title=title, platform=platform)

        if check_cancelled(job_id):
            raise JobCancelledError(job_id)

        # ★ 断点续传：检查是否已有转录文件（重试场景）
        skip_transcribe = False
        if _retry:
            # ★ download_video 返回的是 str，统一成 Path 才能 with_suffix
            file_path = Path(file_path)
            expected_srt = file_path.with_suffix('.srt')
            expected_txt = file_path.with_suffix('.txt')
            if expected_srt.exists() and expected_txt.exists() \
               and expected_srt.stat().st_size > 100 \
               and expected_txt.stat().st_size > 100:
                srt_path = str(expected_srt)
                txt_path = str(expected_txt)
                skip_transcribe = True
                await emit_log(sio, job_id, f'🔁 断点续传：发现已有转录文件，跳过转录（SRT:{expected_srt.stat().st_size}字节 TXT:{expected_txt.stat().st_size}字节）')

        if not skip_transcribe:
            # 转录
            update_job(job_id, status='transcribing', progress_msg='开始转录')
            srt_path, txt_path, seg_count = await transcribe_audio(
                file_path, model_size=model_size, platform=platform,
                speaker_diarization=speaker_diarization, language=language,
                sio=sio, job_id=job_id
            )

    return srt_path, txt_path, seg_count, title, platform, files


async def process_job(job_id: str):
    """
    主处理流程：串联下载→转录→预览→AI总结→翻译→标签
    在后台 asyncio.Task 中运行

    ★ 关键设计：preview 等待期间释放信号量，不阻塞其他任务
    """
    from socket_handlers import check_cancelled, clear_cancel_flag

    sem = get_job_semaphore()
    waited = False
    # ★ 共享持有状态：process_job 与 _process_job_impl 读写同一份真相，
    #   避免 preview 阶段内部释放后，外层用陈旧标志二次释放（许可数 over-release）
    sem_state = {'held': False}
    try:
        while True:
            if check_cancelled(job_id):
                update_job(job_id, status='cancelled', error='用户取消')
                return
            try:
                await asyncio.wait_for(sem.acquire(), timeout=0.25)
                sem_state['held'] = True
                break
            except asyncio.TimeoutError:
                if not waited and sio:
                    from socket_handlers import emit_log
                    await emit_log(sio, job_id, '⏳ 等待处理队列（并发限制，前序任务完成后再开始）...')
                    waited = True

        if waited and sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, '▶ 队列释放，开始处理')
        # _process_job_impl 内部管理信号量：
        # - preview 等待前释放，等待后重新获取
        # - 通过共享的 sem_state['held'] 记录当前是否仍持有信号量
        await _process_job_impl(job_id, sem, sem_state)
        if sem_state['held']:
            sem.release()
            sem_state['held'] = False
    except Exception:
        # 异常时无论处于哪个阶段，只按 sem_state 的真实持有状态释放一次，
        # 杜绝 preview 内部已释放后外层二次释放（over-release）
        if sem_state['held']:
            sem.release()
            sem_state['held'] = False
        raise
    finally:
        # ★ 批量任务子任务终态后推送整体进度（单一出口：所有终态路径都流经此处，
        #   含排队期取消/正常完成/失败/异常，无需在各 return 点散落 emit）
        await _emit_batch_progress(job_id)
        current_task = asyncio.current_task()
        registered_task = RUNNING_JOBS.get(job_id, {}).get('task')
        if registered_task is None or registered_task is current_task:
            RUNNING_JOBS.pop(job_id, None)
            clear_cancel_flag(job_id)


async def _process_job_impl(job_id: str, sem: asyncio.Semaphore = None, sem_state: dict = None):
    """实际的任务处理实现（被 process_job 包装，负责并发控制）

    ★ sem 参数：用于 preview 等待期间释放信号量，不阻塞其他任务
    ★ 返回值：是否仍持有信号量（True=持有需外部释放, False=已内部释放）
    """
    # ★ 用共享 sem_state 记录持有状态；无 sem_state（独立调用）时本地兜底构造
    if sem_state is None:
        sem_state = {'held': sem is not None}
    logger.info(f"process_job 进入: job_id={job_id}")
    from socket_handlers import (
        emit_progress, emit_log, emit_preview,
        emit_ai_token, set_preview_event, pop_preview_event,
        check_cancelled,
    )
    from services.downloader import prevent_sleep, allow_sleep
    from services.ai_processor import (
        ai_process, translate_subtitles, generate_tags, TaskCancelledError,
    )
    from services.textbook_processor import textbook_process
    from services.research_processor import research_process

    # 阻止系统睡眠
    prevent_sleep()
    running_entry = RUNNING_JOBS.setdefault(job_id, {})
    running_entry.update({
        'start_time': running_entry.get('start_time', time.time()),
        'status': 'processing',
    })

    try:
        job = get_job(job_id)
        if not job:
            logger.error(f"任务 {job_id} 不存在")
            return sem_state['held']

        options = job.get('options', {})
        if isinstance(options, str):
            import json
            options = json.loads(options)

        model = job.get('model') or get_default_model_key()
        summary_type = job.get('summary_type', 'summary')
        input_url = job.get('input_url', '')
        # ★ 清洗畸形URL（剪贴板可能带入截断BV片段等前缀垃圾）
        if input_url:
            from services.downloader import sanitize_input_url
            input_url = sanitize_input_url(input_url)
        input_text = job.get('input_text', '')
        input_file = job.get('input_file', '')
        input_type = options.get('input_type', 'url')
        do_summary = options.get('do_summary', True)
        do_translate = options.get('do_translate', False)
        do_subtitle = options.get('do_subtitle', False)
        preview_before_summary = options.get('preview_before_summary', True)
        model_size = options.get('model_size', 'base')
        speaker_diarization = options.get('speaker_diarization', False)
        # ★ 转录语言默认中文（Whisper自动识别会把中文误判为英文）
        #   前端 settingsStore 默认 'zh'，这里做兜底：None → 'zh'
        #   用户在前端选"自动检测"才会传 null，否则一律尊重用户选择
        language = options.get('transcribe_language')
        if language is None or language == '':
            # 仅在用户明确选了"自动检测"时才允许None；其他情况兜底中文
            # 但前端默认是 'zh'，所以走到这里通常意味着前端没传或传了空
            # 为安全起见，兜底中文
            language = 'zh'

        # 每个任务独立目录
        job_dir = DOWNLOAD_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        result = {'files': {}, 'title': '', 'platform': ''}

        await emit_log(sio, job_id, '=' * 60)
        await emit_log(sio, job_id, f'开始处理任务 {job_id}')
        await emit_log(sio, job_id, f'输入类型: {input_type} | 模型: {model} | 总结类型: {summary_type}')

        # ===== Step 1: 获取转录文本（按输入类型分发到策略函数） =====
        if input_type == 'text':
            srt_path, txt_path, seg_count, title, platform, step_files = await _handle_text_input(
                input_text, job_dir, sio, job_id,
                title='文本输入', platform='纯文本', progress_msg='文本已准备',
            )
        elif input_type == 'file':
            from services.storage_cleanup import validate_job_input_path
            input_file = str(validate_job_input_path(input_file, DOWNLOAD_DIR, job_id))
            # 本地文件上传 — 音频/视频/文本类
            file_ext = Path(input_file).suffix.lower()

            if file_ext in ('.txt', '.md'):
                # 文本文件：直接读取 → 跳过转录
                srt_path, txt_path, seg_count, title, platform, step_files = await _handle_text_input(
                    Path(input_file).read_text(encoding='utf-8'), job_dir, sio, job_id,
                    title=Path(input_file).stem, platform='文本文件',
                    progress_msg='文本文件已读取，跳过转录',
                )
            elif file_ext in ('.srt', '.vtt'):
                # 字幕文件：直接转换 → 跳过转录
                srt_path, txt_path, seg_count, title, platform, step_files = await _handle_subtitle_file(
                    input_file, job_dir, sio, job_id
                )
            else:
                # 音频/视频文件：需要转录
                srt_path, txt_path, seg_count, title, platform, step_files = await _handle_media_file(
                    input_file, job_dir, sio, job_id,
                    model_size=model_size, speaker_diarization=speaker_diarization,
                    language=language,
                )
        else:
            # URL输入
            srt_path, txt_path, seg_count, title, platform, step_files = await _handle_url_input(
                input_url, job_dir, sio, job_id, options,
                model_size=model_size, speaker_diarization=speaker_diarization,
                language=language,
            )
        result['files'].update(step_files)

        result['title'] = title
        result['platform'] = platform
        # 统一转 str，后续所有地方都用字符串，避免 Path 不能 JSON 序列化
        srt_path = str(srt_path)
        txt_path = str(txt_path)
        result['files']['srt'] = srt_path
        result['files']['txt'] = txt_path
        update_job(job_id, files=result['files'])

        # ===== Step 1.5: AI 校正转录文本（可选） =====
        # ★ 用云端模型校正语音转写常见错误（同音字、专有名词、标点）
        #   校正后原 txt 备份为 .raw.txt，srt 不动（字数变化会破坏时间戳对齐）
        ai_correct = options.get('ai_correct', False)
        if ai_correct and txt_path and Path(txt_path).exists():
            from services.ai_processor import correct_transcript
            # 校正模型：优先用 ai_correct_model，否则用主模型
            correct_model = options.get('ai_correct_model') or model
            try:
                with open(txt_path, 'r', encoding='utf-8') as f:
                    raw_text = f.read()
                if raw_text.strip():
                    # 备份原文
                    raw_backup = txt_path.replace('.txt', '.raw.txt')
                    with open(raw_backup, 'w', encoding='utf-8') as f:
                        f.write(raw_text)
                    result['files']['txt_raw'] = raw_backup

                    corrected, c_in, c_out, corr_stats = await correct_transcript(
                        raw_text, model=correct_model, sio=sio, job_id=job_id
                    )
                    # 写回 txt（校正后）
                    with open(txt_path, 'w', encoding='utf-8') as f:
                        f.write(corrected)
                    # 记录校正统计，供结果页展示「是否有人工复核提示」
                    result['correction_stats'] = corr_stats
                    update_job(job_id, correction_stats=corr_stats)
                    need_review = (corr_stats['failed_chunks'] or
                                   corr_stats['integrity_warnings'] or
                                   corr_stats['length_warnings'])
                    review_hint = '（⚠ 有批次需人工复核）' if need_review else ''
                    await emit_log(sio, job_id, f'📝 已用 {correct_model} 校正转录文本{review_hint}（原文备份: {Path(raw_backup).name}）')
            except TaskCancelledError:
                raise  # ★ 用户取消不能被"校正失败不影响主流程"的兜底吞掉
            except Exception as e:
                logger.warning(f'AI 校正失败（不影响主流程）: {e}')
                await emit_log(sio, job_id, f'⚠ AI 校正失败: {e}（继续用原转录文本）')
        # ===== Step 2: 转录后预览 =====
        if do_summary and preview_before_summary and srt_path:
            with open(txt_path, 'r', encoding='utf-8') as f:
                preview_text = f.read()

            await emit_log(sio, job_id, f'✅ 转录完成，等待确认后继续AI总结')
            await emit_preview(sio, job_id, preview_text, title=title, platform=platform,
                              srt_path=str(srt_path), txt_path=str(txt_path))
            update_job(job_id, status='preview', progress_msg='等待确认转录文本',
                       preview_text=preview_text)
            await emit_progress(sio, job_id, 'preview', 100, '⏸ 转录完成，请在预览区检查并确认')

            # ★ 等待用户确认前，释放信号量——preview 不占用并发槽位
            # 这样其他任务不会被 preview 中的任务阻塞
            if sem is not None:
                sem.release()
                sem_state['held'] = False  # 标记：不再持有信号量
                await emit_log(sio, job_id, '🔓 已释放处理槽位，其他任务可继续')

            preview_event = asyncio.Event()
            set_preview_event(job_id, preview_event)
            try:
                await asyncio.wait_for(preview_event.wait(), timeout=3600)  # 最多等1小时
            except asyncio.TimeoutError:
                await emit_log(sio, job_id, '⚠ 预览等待超时，自动继续')

            pop_preview_event(job_id)

            # ★ 用户确认后，重新获取信号量才能继续 AI 总结（消耗资源）
            if sem is not None:
                await emit_log(sio, job_id, '⏳ 等待处理队列（重新获取槽位）...')
                await sem.acquire()
                sem_state['held'] = True  # 标记：重新持有信号量
                await emit_log(sio, job_id, '▶ 获得槽位，继续处理')

            # 检查是否取消了
            if check_cancelled(job_id):
                await emit_log(sio, job_id, '⏹ 用户取消处理')
                update_job(job_id, status='cancelled', error='用户取消')
                await _safe_emit('done', {'job_id': job_id, 'success': False, 'error': '用户取消'})
                # 取消时也要释放刚获取的信号量
                if sem is not None and sem_state['held']:
                    sem.release()
                    sem_state['held'] = False
                return sem_state['held']

            # 用户可能编辑了文本
            updated_job = get_job(job_id)
            edited_text = updated_job.get('edited_preview_text', '') if updated_job else ''
            if edited_text:
                # ★ 校正覆盖提示：若启用了 AI 校正且用户编辑与校正后文本不同，
                #   告知用户其编辑已覆盖校正结果（编辑优先，不阻断）
                if ai_correct:
                    try:
                        with open(txt_path, 'r', encoding='utf-8') as f:
                            pre_edit_text = f.read()
                    except Exception:
                        pre_edit_text = ''
                    if edited_text != pre_edit_text:
                        await emit_log(sio, job_id,
                                       'ℹ 检测到用户编辑，已用编辑内容覆盖 AI 校正结果（编辑优先）')
                with open(txt_path, 'w', encoding='utf-8') as f:
                    f.write(edited_text)
                await emit_log(sio, job_id, f'✅ 已应用用户编辑（{len(edited_text)}字符）')

        # ===== Step 3: AI 总结 =====
        if do_summary and txt_path:
            # ★ 开始前再检查一次取消标记
            from socket_handlers import check_cancelled
            if check_cancelled(job_id):
                await emit_log(sio, job_id, '⏹ 用户取消处理')
                update_job(job_id, status='cancelled', error='用户取消')
                await _safe_emit('done', {'job_id': job_id, 'success': False, 'error': '用户取消', 'cancelled': True})
                if sem is not None and sem_state['held']:
                    sem.release()
                    sem_state['held'] = False
                return False

            with open(txt_path, 'r', encoding='utf-8') as f:
                text_content = f.read()

            update_job(job_id, status='summarizing', progress_msg='AI处理中')

            summary_options = {
                'word_count': options.get('word_count', '不限'),
                'tone': options.get('tone', '中性'),
                'format': options.get('format', 'markdown'),
                'purpose': options.get('purpose', '学习'),
                'edited_prompt': options.get('edited_prompt', ''),
                'custom_prompt': options.get('custom_prompt', ''),
                'full_override': options.get('full_override', ''),
                'org_strategy': options.get('org_strategy', 'argument_progression'),
            }

            # ★ 模块化提示词：从 options 中提取 dimensions（由 create_job 存入）
            dimensions = options.get('dimensions')

            # ★ 教科书/深度研究模式检测
            # Long-text auto-routing: if default model and text >=5000 chars, switch to deepseek
            if model == DEFAULT_MODEL and len(text_content) >= 5000:
                from db.model_repo import get_model_by_key
                if get_model_by_key('deepseek-v4-flash'):
                    model = 'deepseek-v4-flash'
                    await emit_log(sio, job_id, f'Long text ({len(text_content)} chars), auto-switched model: {model}')

            is_textbook = (
                summary_type == 'textbook'
                or (dimensions and isinstance(dimensions, dict) and dimensions.get('depth') == 'textbook')
            )
            is_research = (
                summary_type in ('deep_research', 'research')
                or (dimensions and isinstance(dimensions, dict) and dimensions.get('depth') == 'research')
            )

            # ★ 路由：根据模式选择处理管线 + 超时
            if is_textbook:
                result_text, in_tok, out_tok = await asyncio.wait_for(
                    textbook_process(
                        text_content, title=title or options.get('input_url', '未命名'), model=model,
                        sio=sio, job_id=job_id,
                    ),
                    timeout=3600.0,
                )
            elif is_research:
                result_text, in_tok, out_tok = await asyncio.wait_for(
                    research_process(
                        text_content, title=title or options.get('input_url', '未命名'), model=model,
                        sio=sio, job_id=job_id,
                    ),
                    timeout=3600.0,
                )
            else:
                result_text, in_tok, out_tok = await asyncio.wait_for(
                    ai_process(
                        text_content, summary_type=summary_type, model=model,
                        sio=sio, job_id=job_id, summary_options=summary_options,
                        dimensions=dimensions,
                    ),
                    timeout=1800.0,
                )

            # ★ 契约检查：AI 层失败时返回哨兵串（[处理失败: ...]）而非抛异常，
            #   若不拦截会被当作正常结果写盘并标记 success=True，用户看到的“总结”其实是错误。
            #   这里显式失败，路由到下方 except → finish_job(False) + done{success:false, error}
            if isinstance(result_text, str) and result_text.lstrip().startswith('[处理失败'):
                raise Exception(result_text.strip().strip('[]'))

            # 保存结果到文件
            result_md_path = txt_path.replace('.txt', f'__{summary_type}.md')
            with open(result_md_path, 'w', encoding='utf-8') as f:
                f.write(result_text)
            result['files']['result_md'] = str(result_md_path)
            result['result_text'] = result_text
            result['token_usage'] = {'input': in_tok, 'output': out_tok}

            # 自动打标签
            tags = await generate_tags(text_content, model=model, sio=sio, job_id=job_id)
            if tags:
                result['tags'] = tags
                await emit_log(sio, job_id, f'标签: {", ".join(tags)}')

        # ===== Step 4: 翻译（可选） =====
        if do_translate and txt_path and srt_path:
            target_lang = options.get('translate_target_lang', 'zh-CN')
            await emit_log(sio, job_id, f'开始翻译 → {target_lang}')

            # 全文翻译（使用translate总结类型）
            with open(txt_path, 'r', encoding='utf-8') as f:
                text_content = f.read()
            # ★ 整体总超时兜底
            translation, in_tok, out_tok = await asyncio.wait_for(
                ai_process(
                    text_content, summary_type='translate', model=model,
                    sio=sio, job_id=job_id, summary_options=summary_options if do_summary else {}
                ),
                timeout=1800.0,
            )
            lang_suffix = target_lang.replace('-', '_')
            translation_path = txt_path.replace('.txt', f'__{lang_suffix}.md')
            with open(translation_path, 'w', encoding='utf-8') as f:
                f.write(translation)
            result['files']['translation'] = str(translation_path)
            result['translation_text'] = translation

        # ===== Step 5: 字幕翻译（可选） =====
        if do_subtitle and srt_path:
            target_lang = options.get('subtitle_target_lang', 'zh-CN')
            subtitle_category = options.get('subtitle_category', 'auto')
            cn_srt_path = await translate_subtitles(
                srt_path, target_lang=target_lang, model=model,
                category=subtitle_category, sio=sio, job_id=job_id
            )
            result['files']['translated_srt'] = str(cn_srt_path)

        # ===== 完成 =====
        # ★ 取消竞态：收尾前再查一次；若已取消则不写结果、不推 success
        if check_cancelled(job_id):
            await emit_log(sio, job_id, '⏹ 用户取消处理（收尾阶段）')
            update_job(job_id, status='cancelled', error='用户取消')
            await _safe_emit('done', {
                'job_id': job_id,
                'success': False,
                'error': '用户取消',
                'cancelled': True,
            })
            return sem_state['held']

        # 如果 do_summary=False，把文本存到 result_text 供前端显示
        if not do_summary and not result.get('result_text') and txt_path:
            try:
                with open(txt_path, 'r', encoding='utf-8') as f:
                    result['result_text'] = f.read()
            except Exception:
                pass
        finished = finish_job(job_id, success=True, files=result.get('files', {}),
                              token_usage=result.get('token_usage', {}))
        if not finished:
            # 终态保护：期间被取消 → 不写 result_text/历史，推取消 done
            await emit_log(sio, job_id, '⏹ 任务已取消，跳过结果写入')
            await _safe_emit('done', {
                'job_id': job_id,
                'success': False,
                'error': '用户取消',
                'cancelled': True,
            })
            return sem_state['held']

        update_job(job_id, title=result.get('title', title),
                   platform=result.get('platform', platform),
                   tags=result.get('tags', []),
                   result_text=result.get('result_text', ''))

        # ★ 首次总结保存到 summary_history (v1)
        if result.get('result_text'):
            from db.job_repo import save_summary_version
            save_summary_version(
                job_id=job_id, version=1,
                summary_type=summary_type, model=model,
                result_text=result['result_text'],
                tags=result.get('tags', []),
                token_usage=result.get('token_usage', {}),
                files=result.get('files', {}),
            )
            update_job(job_id, summary_version=1)

        # 如果有翻译文本，也存入files的translation_text字段（方便前端展示）
        if result.get('translation_text'):
            files_dict = result.get('files', {})
            files_dict['translation_text'] = result['translation_text']
            update_job(job_id, files=files_dict)

        await emit_log(sio, job_id, '✅ 任务处理完成！')
        await _safe_emit('done', {
            'job_id': job_id,
            'success': True,
            'title': result.get('title', ''),
            'files': result.get('files', {}),
            'token_usage': result.get('token_usage', {}),
        })

        logger.info(f"任务 {job_id} 完成: title={result.get('title', '')}")
        return sem_state['held']

    except JobCancelledError:
        # ★ 策略函数内部（下载完成后）检测到取消：不写结果，交由外层释放信号量
        await emit_log(sio, job_id, '⏹ 任务已取消')
        update_job(job_id, status='cancelled', error='用户取消')
        await _safe_emit('done', {'job_id': job_id, 'success': False, 'error': '用户取消'})
        return sem_state['held']
    except Exception as e:
        # ★ 区分用户取消 vs 真正异常
        from services.ai_processor import TaskCancelledError
        if isinstance(e, TaskCancelledError):
            await emit_log(sio, job_id, '⏹ 用户取消任务')
            update_job(job_id, status='cancelled', error='用户取消')
            await _safe_emit('done', {
                'job_id': job_id,
                'success': False,
                'error': '用户取消',
                'cancelled': True,
            })
            # 取消时释放信号量
            if sem is not None and sem_state['held']:
                sem.release()
                sem_state['held'] = False
            return False
        logger.error(f"任务 {job_id} 处理失败: {e}", exc_info=True)
        finish_job(job_id, False, error=str(e))
        await _safe_emit('done', {
            'job_id': job_id,
            'success': False,
            'error': str(e),
        })
        return sem_state['held']  # 异常时返回当前信号量持有状态

    finally:
        allow_sleep()


def is_job_running(job_id: str) -> bool:
    """Return whether this process already owns a live task for the job."""
    task = RUNNING_JOBS.get(job_id, {}).get('task')
    return task is not None and not task.done()


def start_job(job_id: str):
    """启动后台任务处理；同一 job 在单进程内最多只有一个 live task。"""
    existing_task = RUNNING_JOBS.get(job_id, {}).get('task')
    if existing_task is not None and not existing_task.done():
        logger.warning(f"任务 {job_id} 已在运行，忽略重复启动")
        return existing_task

    task = asyncio.create_task(process_job(job_id))
    RUNNING_JOBS[job_id] = {'task': task, 'start_time': time.time()}
    logger.info(f"后台任务已启动: {job_id}")

    # 捕获任务异常，避免静默失败
    def _on_task_done(t):
        if t.cancelled():
            logger.info(f"后台任务已取消: {job_id}")
            return
        exc = t.exception()
        if exc:
            logger.error(f"❌ 任务 {job_id} 异常退出: {exc}", exc_info=exc)
    task.add_done_callback(_on_task_done)
    return task


# ============ 启动时初始化 ============
@app.on_event("startup")
async def startup():
    init_db()
    logger.info("数据库初始化完成")

    # ★ 重启恢复：上次进程遗留的非终态任务（pending/downloading/transcribing/
    #   preview/summarizing）已无执行者，标记为 failed（注明服务重启中断），
    #   使其可被 retry，避免永久悬挂
    interrupted = fail_interrupted_jobs()
    if interrupted:
        logger.warning(f"检测到 {interrupted} 个被服务重启中断的任务，已标记为 failed（可重试）")

    # 清理格式转换器过期文件（24小时）
    from routers.converter import cleanup_expired_files
    removed = cleanup_expired_files()
    if removed:
        logger.info(f"格式转换器文件清理完成，移除了 {removed} 个过期项")

    logger.info(f"Swagger UI: http://localhost:{SERVER_PORT}/docs")
    logger.info(f"API根路径: http://localhost:{SERVER_PORT}/api")


# ============ 启动方式 ============
# uvicorn main:socket_app --host 0.0.0.0 --port 7001 --reload

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:socket_app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        reload=False,  # 改为 False，避免任务运行时文件变动触发热重启导致任务卡死
        log_level="info",
    )
