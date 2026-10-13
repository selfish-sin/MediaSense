"""
YouTube Tool V2 - 任务路由（核心域：上传/提交/列表/删除/清理/取消/重试/编辑结果）

★ 按域拆分：AI 域（重新总结/总结历史/标签/思维导图）见 routers/jobs_ai.py，
  批量域（批量提交/批量状态/转录搜索）见 routers/jobs_batch.py。
  共享工具函数（URL 规范化等）保留在本模块，供 db 层与其他路由复用。
"""
import asyncio
import ipaddress
import re
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File, Query, Request
from typing import Optional
from urllib.parse import urlparse

from models.schemas import (
    JobCreate, JobSubmitResponse, JobListResponse,
    SuccessResponse
)
from db.job_repo import create_job, get_job, get_jobs, get_running_jobs, get_queue_snapshot, delete_job, clear_jobs, clear_jobs_by_status, update_job, find_job_by_url, delete_jobs_batch, get_jobs_disk_info, claim_staged_upload, get_cleanup_jobs
from db.model_repo import get_default_model_key
from config import DOWNLOAD_DIR, MAX_CONCURRENT_JOBS

logger = logging.getLogger(__name__)
router = APIRouter()

# ============ URL 规范化（去重用） ============


# URL 提取正则（用于从混合文本中提取干净的 URL）
_URL_RE = re.compile(r'https?://[^\s<>"\'{}|\\^`\[\]（）\u4e00-\u9fff]*[^\s<>"\'{}|\\^`\[\]（）\u4e00-\u9fff.,;:!?\)】」』]', re.IGNORECASE)


def extract_urls(text: str) -> list:
    """从混合文本中智能提取所有 URL。

    能处理：
    - "3. https://www.bilibili.com/video/BV18XXcBnEz6" → ["https://www.bilibili.com/video/BV18XXcBnEz6"]
    - "这是一段文字 https://example.com 还有更多" → ["https://example.com"]
    - "url1\nurl2" → ["url1", "url2"]
    - 纯 URL → 原样返回
    - 无 URL → 返回空列表
    """
    if not isinstance(text, str):
        return []
    urls = _URL_RE.findall(text)
    # 清理尾部残留标点（URL 里不应以标点结尾）
    cleaned = []
    for u in urls:
        u = u.rstrip('.,;:!?')
        if u and len(u) > 10:  # 过滤太短的假匹配
            cleaned.append(u)
    return cleaned


def extract_single_url(text: str) -> str:
    """从混合文本中提取第一个 URL，失败返回原文本（向后兼容）"""
    urls = extract_urls(text)
    return urls[0] if urls else text.strip() if isinstance(text, str) else text
def normalize_url(url: str) -> str:
    """提取平台唯一标识，用于 URL 去重。
    B站 → BV号；YouTube → 视频ID；youtu.be → 视频ID；其他 → 剥 query 参数
    不提取任何标识的用完整 URL（保守策略，避免误合并不同内容）
    """
    if not isinstance(url, str):
        return url
    u = url.strip()
    # B站: 提取 BV 号 (b23.tv 等短链走服务端重定向，这里只处理标准链接)
    m = re.search(r'(BV[a-zA-Z0-9]{10})', u)
    if m:
        return f"bilibili:{m.group(1)}"
    # YouTube: youtu.be 短链
    m = re.search(r'youtu\.be/([a-zA-Z0-9_-]{11})', u)
    if m:
        return f"youtube:{m.group(1)}"
    # YouTube: watch?v= 或 embed/ 标准链接
    m = re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', u)
    if m:
        return f"youtube:{m.group(1)}"
    # YouTube: /shorts/ 短链接
    m = re.search(r'/shorts/([a-zA-Z0-9_-]{11})', u)
    if m:
        return f"youtube:{m.group(1)}"
    # 抖音: video/<id>、modal_id=<id>、share/video/<id>（★ 不能走"其他"的剥 query 兜底——
    # user 页 modal_id 会被剥掉，同用户页不同视频被误合并；裸 v.douyin.com 短链用短码本身作键）
    from services.douyin import extract_aweme_id_from_text
    aweme_id = extract_aweme_id_from_text(u)
    if aweme_id:
        return f"douyin:{aweme_id}"
    m = re.search(r'v\.douyin\.com/([A-Za-z0-9]+)', u)
    if m:
        return f"douyin:short:{m.group(1)}"
    # 其他: 剥 query 参数（兜底，对未知平台保守）
    return u.split('?')[0]

UPLOAD_DIR = DOWNLOAD_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_UPLOAD_EXTS = {
    '.mp3', '.wav', '.m4a', '.flac', '.ogg', '.wma', '.aac',
    '.mp4', '.mkv', '.webm', '.avi', '.mov', '.wmv', '.flv',
    '.txt', '.md', '.srt', '.vtt',
}
MAX_UPLOAD_SIZE = 8 * 1024**3  # 8GB 上传上限（流式落盘，覆盖长视频；前端 uploadRules.js 同步）
STAGED_UPLOAD_PATTERN = re.compile(r'^[0-9a-f]{8}_.+')
REPARSE_POINT = 0x400
ACTIVE_JOB_STATUSES = {'pending', 'downloading', 'transcribing', 'preview', 'summarizing'}


def _resolve_staged_upload(raw_path: Optional[str]) -> Path:
    '''Resolve a server-created staged upload and reject arbitrary client paths.'''
    if not raw_path:
        raise HTTPException(status_code=400, detail='file input requires input_file_path')
    source = Path(raw_path).expanduser()
    try:
        stat = source.lstat()
        root = UPLOAD_DIR.resolve(strict=True)
        resolved = source.resolve(strict=True)
    except OSError:
        raise HTTPException(status_code=400, detail='staged upload does not exist')
    if source.is_symlink() or bool(getattr(stat, 'st_file_attributes', 0) & REPARSE_POINT):
        raise HTTPException(status_code=400, detail='staged upload cannot be a link')
    if resolved.parent != root or not resolved.is_file():
        raise HTTPException(status_code=400, detail='input_file_path is outside the upload staging directory')
    if not STAGED_UPLOAD_PATTERN.fullmatch(resolved.name):
        raise HTTPException(status_code=400, detail='input_file_path is not a server-created upload')
    if resolved.suffix.lower() not in ALLOWED_UPLOAD_EXTS:
        raise HTTPException(status_code=400, detail='staged upload type is not supported')
    return resolved


# ★ 批量端点的 job_ids 数量上限（2026-10-13）。跨域共享的纯校验放核心 jobs.py，
#   export / chat 等其他路由 `from routers.jobs import assert_batch_size` 复用。
#   为什么需要：/batch 导出与 /batch-disk-info 都会**逐条把完整 result_text 读进内存**
#   再打包、再逐条递归统计磁盘，全程在一次请求内同步完成——无上限时内存峰值随选中
#   数量线性增长，会变成一次长时间阻塞的请求。
#   ★ 取值 2000 是**与前端 `HistoryView.SELECT_ALL_CAP` 对齐**，不是随手拍的：
#     前端「全选当前筛选」刻意以 2000 为安全上限并承诺「可跨页批量操作」，
#     后端若取更小的值，用户全选 500 条后点批量删除就会被这条新限制挡住——
#     凭空多出一个此前不存在的失败路径。真要再收紧，必须**前后端一起改**。
#     若日后确实成为性能问题，正确方向是服务端流式出 ZIP，而不是压低上限。
MAX_BATCH_JOB_IDS = 2000


def assert_batch_size(job_ids: list[str], *, what: str = 'job_ids') -> None:
    """校验批量列表长度，超限显式报错并给出分批建议（不做静默截断）。"""
    if len(job_ids) > MAX_BATCH_JOB_IDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{what} 数量 {len(job_ids)} 超过单次上限 {MAX_BATCH_JOB_IDS}。"
                "请分批操作——单次请求会一次性把全部任务的完整结果读进内存，"
                "数量过大会长时间阻塞并推高内存占用。"
            ),
        )


def _assert_jobs_deletable(job_ids: list[str]) -> None:
    from main import is_job_running
    active = []
    for job_id in job_ids:
        job = get_job(job_id)
        if is_job_running(job_id) or (job and job.get('status') in ACTIVE_JOB_STATUSES):
            active.append(job_id)
    if active:
        raise HTTPException(
            status_code=409,
            detail=f'active jobs must be cancelled before deletion: {", ".join(active)}',
        )

def require_local_client(request: Request, *, what: str = '该操作') -> None:
    """破坏性操作仅允许本机发起（与存储清理 `_require_local` 同一口径）。

    ★ 为什么加（2026-10-13）：`config.SERVER_HOST = "0.0.0.0"` 让整个 API 在局域网
      可达，而本项目**没有认证**。CORS 白名单只挡浏览器发起的跨域读取，挡不住同网段
      其它主机上的 curl / 脚本。存储清理是此前唯一做了本地限制的破坏性端点，
      而 `POST /api/jobs/clear`（无参、无确认）能直接删光全部任务与磁盘文件。
      这里把同一道门槛补到其它无认证的破坏性端点上。

    ★ 为什么不改绑回 127.0.0.1：手机/平板访问是既有能力，改绑等于砍功能。
      加端点级限制既堵住局域网横向调用，又不动正常访问路径。
    """
    host = request.client.host if request.client else ''
    if host == 'localhost':
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise HTTPException(
        status_code=403,
        detail=f'{what}仅允许从本机发起（当前请求来源 {host or "未知"}）。'
               '本服务无认证，破坏性操作不对局域网开放。'
               '如你在本机操作，请改用 http://localhost:7001 打开页面（用局域网 IP 访问时'
               '请求来源会被判为非本机）。',
    )


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传文件（音频/视频/文本），返回路径供提交任务时使用"""
    filename = Path(file.filename or '').name
    ext = Path(filename).suffix.lower() if filename else ''
    if ext not in ALLOWED_UPLOAD_EXTS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}")

    # 保存文件到uploads目录（流式写盘，避免大文件 OOM）
    import uuid
    safe_name = f"{uuid.uuid4().hex[:8]}_{filename}"
    dest_path = UPLOAD_DIR / safe_name

    try:
        written = 0
        with open(str(dest_path), 'xb') as f:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_SIZE:
                    raise HTTPException(status_code=413, detail=f"文件超过 {MAX_UPLOAD_SIZE // (1024**3)}GB 上限")
                f.write(chunk)
    except HTTPException:
        dest_path.unlink(missing_ok=True)
        raise
    except Exception:
        dest_path.unlink(missing_ok=True)
        raise
    file_size = dest_path.stat().st_size
    if file_size == 0:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="上传文件为空")

    logger.info(f"文件上传成功: {safe_name} ({file_size} bytes)")
    return {
        "success": True,
        "file_path": str(dest_path.resolve()),
        "file_name": filename,
        "file_size": file_size,
        "message": f"文件 {filename} 上传成功",
    }


@router.post("", response_model=JobSubmitResponse)
async def submit_job(job: JobCreate):
    """提交处理任务

    ★ 同一 URL 去重：如果已有同 URL 的 completed/running 任务，返回已有任务信息
    ★ force_new=true 时跳过去重，强制创建新任务
    ★ 支持智能 URL 提取（从混合文本中提取干净 URL）
    """
    # ★ 智能 URL 提取：从混合文本中提取干净 URL
    staged_upload = None
    if job.input_type.value == 'file':
        staged_upload = _resolve_staged_upload(job.input_file_path)
    elif job.input_file_path:
        raise HTTPException(status_code=400, detail='input_file_path is only valid for file input')

    if job.input_url:
        job.input_url = extract_single_url(job.input_url)

    if job.input_type.value == 'url':
        parsed = urlparse((job.input_url or '').strip())
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise HTTPException(status_code=400, detail='请输入有效的 http/https 视频 URL')
    elif job.input_type.value == 'text' and not (job.input_text or '').strip():
        raise HTTPException(status_code=400, detail='文本内容不能为空')

    # URL 去重检查（force_new 模式下跳过）
    if job.input_url and not job.force_new:
        existing = find_job_by_url(job.input_url)
        if existing and existing.get('status') in ('pending', 'completed', 'preview', 'downloading', 'transcribing', 'summarizing'):
            # 返回已有任务信息，前端可跳转
            return JobSubmitResponse(
                job_id=existing['id'],
                message=f"该URL已有处理记录（状态: {existing['status']}），跳转到已有任务",
                existing_job=True,
                existing_status=existing['status'],
                existing_title=existing.get('title', ''),
            )

    job_id = create_job(
        input_type=job.input_type,
        input_url=job.input_url,
        input_text=job.input_text,
        input_file=str(staged_upload) if staged_upload else None,
        model=job.model or get_default_model_key(),
        summary_type=job.summary_type or "summary",
        options=job.options or {},
        do_summary=job.do_summary,
        preview_before_summary=job.preview_before_summary,
        dimensions=job.dimensions,
    )
    if staged_upload:
        job_dir = DOWNLOAD_DIR / job_id
        canonical = job_dir / staged_upload.name
        try:
            job_dir.mkdir(parents=True, exist_ok=False)
            staged_upload.rename(canonical)
            if not claim_staged_upload(job_id, str(staged_upload), str(canonical.resolve())):
                raise RuntimeError('job no longer owns the staged upload')
        except Exception as exc:
            try:
                if canonical.exists() and not staged_upload.exists():
                    canonical.rename(staged_upload)
                delete_job(job_id, delete_files=True)
            except Exception:
                logger.exception('failed to roll back staged upload claim for job %s', job_id)
            raise HTTPException(status_code=409, detail=f'failed to claim staged upload: {exc}')

    # 启动后台处理
    from main import start_job
    start_job(job_id)

    logger.info(f"任务已提交并启动: {job_id}")
    return JobSubmitResponse(job_id=job_id, message="任务已提交，开始处理")


@router.get("", response_model=JobListResponse)
async def list_jobs(
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000),
    keyword: Optional[str] = Query(None, description="按标题/URL 关键词过滤"),
):
    """获取任务列表（可按 status + keyword 过滤）"""
    jobs, total = get_jobs(status=status, limit=limit, offset=offset, keyword=keyword)
    return JobListResponse(jobs=jobs, total=total)


@router.get("/running")
async def running_jobs():
    """获取运行中的任务"""
    jobs = get_running_jobs()
    return {"jobs": jobs, "count": len(jobs)}


@router.get("/queue")
async def job_queue():
    """处理队列快照：``running``（占用槽位）+ ``pending``（等待槽位，FIFO）。

    单任务也排队：全局信号量上限（``config.MAX_CONCURRENT_JOBS``，**默认 2**）之外的
    任务停在 pending，前端「任务进度」卡下方展示「正在处理 + 排队中」。

    ★ 数据源是 DB 任务状态（不另造内存队列）——刷新/切页后队列仍在；
      但 running 判定要叠加内存的 `RUNNING_JOBS[jid]['status'] == 'processing'`，
      因为字幕/下载阶段 DB status 还停在 pending（否则正在跑的任务会被误报成排队中）。
    ★ 本路由必须注册在 ``GET /{job_id}`` **之前**：它是单段路径，注册晚了会被
      ``/{job_id}`` 捕获（job_id='queue'）→ 404。与既有的 ``/running`` 同因。
    """
    from main import RUNNING_JOBS

    snapshot = get_queue_snapshot()
    running = list(snapshot["running"])
    pending = []
    for job in snapshot["pending"]:
        # ★ DB status 在字幕/下载阶段**可能仍停在 'pending'**（这些步骤不更新 status），
        #   只按 status 分类会把正在处理的任务误报成「排队中」——实测 4 个任务看着全在排队，
        #   其实第一个已经在跑字幕重试。用内存注册表补正：只有真正拿到信号量的任务才会被
        #   `_process_job_impl` 打上 `status='processing'`（等信号量时该字段不存在）。
        entry = RUNNING_JOBS.get(job["id"]) or {}
        if entry.get("status") == "processing":
            running.append({**job, "live": True})
        else:
            pending.append(job)
    running.sort(key=lambda j: (j.get("created_at") or "", j.get("id") or ""))
    return {
        "concurrency": MAX_CONCURRENT_JOBS,
        "running": running,
        "pending": pending,
        "total": len(running) + len(pending),
    }


@router.get("/{job_id}")
async def get_job_detail(job_id: str):
    """获取单个任务详情"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    return job


@router.delete("/{job_id}")
async def delete_job_api(job_id: str, request: Request, delete_files: bool = Query(True)):
    """删除单个任务（默认连带删除磁盘文件；破坏性：仅本机可发起）"""
    require_local_client(request, what='删除任务')
    # A worker owns its job directory until cancellation has fully completed.
    _assert_jobs_deletable([job_id])
    success = delete_job(job_id, delete_files=delete_files)
    if not success:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    action = "已删除（含文件）" if delete_files else "已删除（仅记录）"
    return SuccessResponse(message=f"任务 {job_id} {action}")


@router.post("/clear")
async def clear_all_jobs(request: Request):
    """清空所有任务（破坏性：仅本机可发起，见 require_local_client）"""
    require_local_client(request, what='清空全部任务')
    jobs = get_cleanup_jobs()
    _assert_jobs_deletable([job['id'] for job in jobs])
    count = clear_jobs()
    return SuccessResponse(message=f"已清空 {count} 个任务")


@router.post("/clear/{status}")
async def clear_jobs_by_status_endpoint(status: str, request: Request):
    """清空指定状态的任务（completed/failed/cancelled；破坏性：仅本机可发起）"""
    require_local_client(request, what=f'清空 {status} 任务')
    if status not in ("completed", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail=f"不支持的状态: {status}，仅支持 completed/failed/cancelled")
    count = clear_jobs_by_status(status)
    labels = {"completed": "成功", "failed": "失败", "cancelled": "已取消"}
    return SuccessResponse(message=f"已清空 {count} 个{labels.get(status, status)}任务", data={"count": count})


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str):
    """取消任务"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    active_statuses = ('pending', 'downloading', 'transcribing', 'preview', 'summarizing')
    if job.get('status') not in active_statuses:
        raise HTTPException(status_code=400, detail="只能取消正在处理或等待确认的任务")
    from socket_handlers import request_cancel
    request_cancel(job_id)
    update_job(job_id, status="cancelled", error="用户取消")
    return SuccessResponse(message=f"任务 {job_id} 已取消")


@router.post("/batch-cancel")
async def batch_cancel_jobs(body: dict):
    """批量取消任务（仅对运行中状态生效）"""
    from socket_handlers import request_cancel

    job_ids: list = body.get("job_ids", [])
    if not job_ids:
        raise HTTPException(status_code=400, detail="job_ids 不能为空")

    cancelled: list = []
    skipped: list = []
    not_found: list = []

    for jid in job_ids:
        job = get_job(jid)
        if not job:
            not_found.append(jid)
            continue
        status = job.get("status", "")
        if status in ("pending", "downloading", "transcribing", "preview", "summarizing"):
            request_cancel(jid)
            update_job(jid, status="cancelled", error="用户批量取消")
            cancelled.append(jid)
        else:
            skipped.append({"job_id": jid, "status": status})

    return {
        "cancelled": cancelled,
        "cancelled_count": len(cancelled),
        "skipped": skipped,
        "not_found": not_found,
        "message": f"已取消 {len(cancelled)} 个任务（跳过 {len(skipped)} 个非运行中任务）",
    }


@router.post("/batch-delete")
async def batch_delete_jobs(body: dict, request: Request):
    """批量删除任务（支持 dry_run 预览磁盘文件；破坏性：仅本机可发起）"""
    require_local_client(request, what='批量删除任务')
    job_ids: list = body.get("job_ids", [])
    dry_run: bool = body.get("dry_run", False)
    delete_files: bool = body.get("delete_files", True)

    if not job_ids:
        raise HTTPException(status_code=400, detail="job_ids 不能为空")
    assert_batch_size(job_ids)

    if dry_run:
        # 预览模式：返回每个任务的磁盘信息
        disk_info = get_jobs_disk_info(job_ids)
        total_bytes = sum(d["size_bytes"] for d in disk_info)
        return {
            "dry_run": True,
            "job_count": len(disk_info),
            "total_size_bytes": total_bytes,
            "total_size_desc": _format_size(total_bytes),
            "jobs": disk_info,
        }

    # 真删模式
    _assert_jobs_deletable(job_ids)
    result = delete_jobs_batch(job_ids, delete_files=delete_files)
    result["message"] = (
        f"已删除 {result['deleted_count']} 个任务"
        + (f"，释放 {_format_size(result['files_freed_bytes'])}" if result.get("files_freed_bytes") else "")
    )
    return result


@router.post("/batch-disk-info")
async def batch_disk_info(body: dict):
    """查询批量任务的磁盘信息（路径+大小），用于删除前确认"""
    job_ids: list = body.get("job_ids", [])
    if not job_ids:
        raise HTTPException(status_code=400, detail="job_ids 不能为空")
    assert_batch_size(job_ids)
    disk_info = get_jobs_disk_info(job_ids)
    total_bytes = sum(d["size_bytes"] for d in disk_info)
    return {
        "job_count": len(disk_info),
        "total_size_bytes": total_bytes,
        "total_size_desc": _format_size(total_bytes),
        "jobs": disk_info,
    }


def _format_size(bytes_val: int) -> str:
    """字节转人类可读"""
    for unit in ("B", "KB", "MB", "GB"):
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} TB"


@router.post("/{job_id}/retry")
async def retry_job(job_id: str, force: bool = Query(False, description="强制重试：删除已有文件从头开始")):
    """
    重试失败/取消的任务。

    默认（force=false）：断点续传模式，保留已有下载/转录文件，从失败步骤继续。
    force=true：删除 job_dir 下已有文件，完全重新开始。
    """
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    if job['status'] not in ('failed', 'cancelled'):
        raise HTTPException(status_code=400, detail="只能重试失败或取消的任务")

    from main import is_job_running
    if is_job_running(job_id):
        raise HTTPException(status_code=409, detail="旧任务仍在停止中，请稍后再重试")

    # force 模式：清理已有文件（保留文件任务的输入文件）
    cleared = []
    if force:
        job_dir = DOWNLOAD_DIR / job_id
        if job_dir.exists():
            import shutil
            # ★ 文件任务：备份输入文件，rmtree 后恢复（否则重试必崩）。
            #   输入文件在 job_dir 内时，备份是删除前的唯一副本——备份失败或校验
            #   不过必须整体中止（不删任何文件、任务状态不变），绝不能"备份失败→照删"
            #   把用户素材一起删掉。
            input_file_path = job.get('input_file', '')
            input_name = ''
            backup_path = None
            if input_file_path:
                src = Path(input_file_path)
                if src.exists():
                    if not src.is_file():
                        raise HTTPException(
                            status_code=500,
                            detail=f"输入路径不是常规文件，无法备份，已取消强制重试（未删除任何文件）: {src}",
                        )
                    input_name = src.name
                    backup_path = UPLOAD_DIR / f"retry_{job_id}_{src.name}"
                    try:
                        backup_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(src), str(backup_path))
                    except Exception as e:
                        raise HTTPException(
                            status_code=500,
                            detail=(
                                f"输入文件备份失败（{e}），已取消强制重试：未删除任何文件。"
                                f"素材保留在 {src}；可先手动备份该文件再重试，或改用普通重试（断点续传）。"
                            ),
                        )
                    try:
                        backup_ok = backup_path.is_file() and backup_path.stat().st_size == src.stat().st_size
                    except OSError:
                        backup_ok = False
                    if not backup_ok:
                        raise HTTPException(
                            status_code=500,
                            detail=f"备份校验未通过，已取消强制重试：未删除任何文件。素材保留在 {src}。",
                        )
            try:
                shutil.rmtree(job_dir)
            except Exception as e:
                logger.warning(f"清理文件失败（重试继续）: {job_dir} - {e}")
            job_dir.mkdir(parents=True, exist_ok=True)
            if backup_path and backup_path.exists():
                restored = job_dir / input_name
                try:
                    backup_path.rename(restored)
                    update_job(job_id, input_file=str(restored.resolve()))
                except Exception:
                    logger.warning(f"恢复输入文件失败，备份保留在: {backup_path}")
            cleared.append(f"已清理 {job_dir}")

    # 读取原任务参数
    opts = job.get('options', {}) or {}
    # ★ 标记 _retry = True → main.py 检测到后启用断点续传逻辑
    opts['_retry'] = True

    # 清除失败状态，保留完整 options 和原 input_url/text/file
    update_kwargs = {
        "status": "pending",
        "error": None,
        "progress_msg": "重试中...",
        "options": opts,
        "files": None,
        "result_text": None,
        # 上一轮的来源说明随结果一并作废（新一轮 Step 1 完成时重新写入；
        # 不清的话重试期间/再度失败时展示的是旧一轮的引擎与缓存信息）
        "transcript_meta": None,
    }

    update_job(job_id, **update_kwargs)

    from main import start_job
    from socket_handlers import clear_cancel_flag
    clear_cancel_flag(job_id)  # 清除旧取消标记
    start_job(job_id)

    mode = "强制重试" if force else "断点续传"
    extra = f"（{'; '.join(cleared)}）" if cleared else ""
    return SuccessResponse(message=f"任务 {job_id} 已重新提交（{mode}）{extra}")


@router.post("/{job_id}/edit-result")
async def edit_job_result(job_id: str, body: dict):
    """保存用户在线编辑后的结果文本"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    new_text = body.get("result_text", "")
    if not new_text:
        raise HTTPException(status_code=400, detail="result_text 不能为空")
    update_job(job_id, result_text=new_text)
    logger.info(f"任务 {job_id} 结果已更新 ({len(new_text)} 字符)")
    return SuccessResponse(message="结果已保存")


@router.get("/{job_id}/screenshots")
async def get_screenshots(
    job_id: str,
    frame_count: int = Query(10, ge=1, le=30),
):
    """提取视频关键帧截图"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")

    # 找视频/音频文件（video字段优先，audio字段兜底）
    files_str = job.get('files')
    if not files_str:
        raise HTTPException(status_code=400, detail="任务没有关联文件")

    import json
    try:
        files = json.loads(files_str) if isinstance(files_str, str) else files_str
    except json.JSONDecodeError:
        files = {}

    # 同时检查 video 和 audio 字段
    video_path = files.get('video') or files.get('audio')
    if not video_path or not Path(video_path).exists():
        raise HTTPException(status_code=404, detail="视频/音频文件不存在或已删除")

    output_dir = Path(DOWNLOAD_DIR) / job_id / "screenshots"
    output_dir.mkdir(parents=True, exist_ok=True)

    from services.screenshot import extract_keyframes
    results = await asyncio.to_thread(
        extract_keyframes, video_path, str(output_dir), frame_count=frame_count
    )

    return {
        "job_id": job_id,
        "frame_count": len(results),
        "frames": results,
    }
