"""
YouTube Tool V2 - 任务路由
"""
import asyncio
import re
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File, Query, Request
from typing import Optional
from urllib.parse import urlparse

from models.schemas import (
    JobCreate, JobSubmitResponse, JobListResponse,
    JobResponse, SuccessResponse, JobStatus
)
from db.job_repo import create_job, get_job, get_jobs, get_running_jobs, delete_job, clear_jobs, clear_jobs_by_status, update_job, get_jobs_by_batch, get_batch_summary, find_job_by_url, save_summary_version, get_summary_history, get_summary_version, get_latest_summary_version, delete_jobs_batch, get_jobs_disk_info, claim_staged_upload, get_cleanup_jobs
from config import DOWNLOAD_DIR, DEFAULT_MODEL

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
    # 其他: 剥 query 参数（兜底，对未知平台保守）
    return u.split('?')[0]

UPLOAD_DIR = DOWNLOAD_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_UPLOAD_EXTS = {
    '.mp3', '.wav', '.m4a', '.flac', '.ogg', '.wma', '.aac',
    '.mp4', '.mkv', '.webm', '.avi', '.mov', '.wmv', '.flv',
    '.txt', '.md', '.srt', '.vtt',
}
MAX_UPLOAD_SIZE = 2 * 1024**3  # 2GB 上传上限
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

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传文件（音频/视频/文本），返回路径供提交任务时使用"""
    filename = Path(file.filename or '').name
    ext = Path(filename).suffix.lower() if filename else ''
    if ext not in ALLOWED_UPLOAD_EXTS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}")

    # 保存文件到uploads目录（流式写盘，避免大文件 OOM）
    import uuid
    import shutil
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
        model=job.model or DEFAULT_MODEL,
        summary_type=job.summary_type or "summary",
        options=job.options or {},
        do_summary=job.do_summary,
        do_translate=job.do_translate,
        do_subtitle=job.do_subtitle,
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
):
    """获取任务列表"""
    jobs, total = get_jobs(status=status, limit=limit, offset=offset)
    return JobListResponse(jobs=jobs, total=total)


@router.get("/running")
async def running_jobs():
    """获取运行中的任务"""
    jobs = get_running_jobs()
    return {"jobs": jobs, "count": len(jobs)}


@router.get("/{job_id}")
async def get_job_detail(job_id: str):
    """获取单个任务详情"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    return job


@router.delete("/{job_id}")
async def delete_job_api(job_id: str, delete_files: bool = Query(True)):
    """删除单个任务（默认连带删除磁盘文件）"""
    # A worker owns its job directory until cancellation has fully completed.
    _assert_jobs_deletable([job_id])
    success = delete_job(job_id, delete_files=delete_files)
    if not success:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    action = "已删除（含文件）" if delete_files else "已删除（仅记录）"
    return SuccessResponse(message=f"任务 {job_id} {action}")


@router.post("/clear")
async def clear_all_jobs():
    """清空所有任务"""
    jobs = get_cleanup_jobs()
    _assert_jobs_deletable([job['id'] for job in jobs])
    count = clear_jobs()
    return SuccessResponse(message=f"已清空 {count} 个任务")


@router.post("/clear/{status}")
async def clear_jobs_by_status_endpoint(status: str):
    """清空指定状态的任务（completed/failed/cancelled）"""
    if status not in ("completed", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail=f"不支持的状态: {status}，仅支持 completed/failed/cancelled")
    count = clear_jobs_by_status(status)
    labels = {"completed": "成功", "failed": "失败", "cancelled": "已取消"}
    return SuccessResponse(message=f"已清空 {count} 个{labels.get(status, status)}任务")


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
async def batch_delete_jobs(body: dict):
    """批量删除任务（支持 dry_run 预览磁盘文件）"""
    job_ids: list = body.get("job_ids", [])
    dry_run: bool = body.get("dry_run", False)
    delete_files: bool = body.get("delete_files", True)

    if not job_ids:
        raise HTTPException(status_code=400, detail="job_ids 不能为空")

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
            # ★ 文件任务：备份输入文件，rmtree 后恢复（否则重试必崩）
            input_file_path = job.get('input_file', '')
            backup_path = None
            if input_file_path:
                src = Path(input_file_path)
                if src.exists() and src.is_file():
                    backup_path = UPLOAD_DIR / f"retry_{job_id}_{src.name}"
                    try:
                        shutil.copy2(str(src), str(backup_path))
                    except Exception:
                        backup_path = None
            try:
                shutil.rmtree(job_dir)
            except Exception as e:
                logger.warning(f"清理文件失败（重试继续）: {job_dir} - {e}")
            job_dir.mkdir(parents=True, exist_ok=True)
            if backup_path and backup_path.exists():
                restored = job_dir / backup_path.name.split('_', 2)[-1]
                try:
                    backup_path.rename(restored)
                    update_job(job_id, input_file=str(restored.resolve()))
                except Exception:
                    logger.warning(f"恢复输入文件失败: {backup_path}")
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


@router.post("/{job_id}/summarize")
async def resummarize_job(job_id: str, body: dict):
    """用不同参数重新总结（基于已有转录，跳过下载/转录步骤）"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")

    # 检查job状态：必须是completed才能重新总结
    if job['status'] != 'completed':
        raise HTTPException(status_code=400, detail="只能对已完成的任务重新总结")

    # 获取已有转录文本
    files = job.get('files') or {}
    txt_path = files.get('txt', '')
    if not txt_path or not Path(txt_path).exists():
        raise HTTPException(status_code=400, detail="任务没有可用的转录文本")

    # 读取转录文本
    with open(txt_path, 'r', encoding='utf-8') as f:
        text_content = f.read()

    if not text_content.strip():
        raise HTTPException(status_code=400, detail="转录文本为空")

    # 获取新参数
    summary_type = body.get('summary_type', job.get('summary_type', 'summary'))
    model = body.get('model', job.get('model', DEFAULT_MODEL))
    # Long-text auto-routing in resummarize: >=5000 chars -> deepseek
    if model == DEFAULT_MODEL and len(text_content) >= 5000:
        from db.model_repo import get_model_by_key
        if get_model_by_key('deepseek-v4-flash'):
            model = 'deepseek-v4-flash'
            logger.info(f'Resummarize: long text ({len(text_content)} chars), auto-switched model: {model}')
    opts = body.get('options', job.get('options', {}))
    # ★ 模块化提示词：优先使用请求体中的 dimensions，其次原始 job 的 options.dimensions
    dimensions = body.get('dimensions') or opts.get('dimensions')

    summary_options = {
        'word_count': opts.get('word_count', '不限'),
        'tone': opts.get('tone', '中性'),
        'format': opts.get('format', 'markdown'),
        'purpose': opts.get('purpose', '学习'),
        'edited_prompt': opts.get('edited_prompt', ''),
        'custom_prompt': opts.get('custom_prompt', ''),
        'full_override': opts.get('full_override', ''),
        'org_strategy': opts.get('org_strategy', 'argument_progression'),
    }

    # ★ 保存当前版本到 summary_history（覆盖前先存档）
    current_version = job.get('summary_version', 1)
    if job.get('result_text'):
        from db.job_repo import save_summary_version
        saved = save_summary_version(
            job_id=job_id, version=current_version,
            summary_type=job.get('summary_type', 'summary'),
            model=job.get('model', ''),
            result_text=job['result_text'],
            tags=job.get('tags', []),
            token_usage=job.get('token_usage', {}),
            files=job.get('files', {}),
        )
        if saved:
            logger.info(f"任务 {job_id} v{current_version} 已存档到 summary_history")

    # 更新job状态
    update_job(job_id, status='summarizing', progress_msg='重新AI处理中...',
               summary_type=summary_type, model=model)

    # 运行AI处理
    try:
        from services.ai_processor import ai_process, generate_tags
        from main import sio

        result_text, in_tok, out_tok = await ai_process(
            text_content, summary_type=summary_type, model=model,
            sio=sio, job_id=job_id, summary_options=summary_options,
            dimensions=dimensions,
        )

        # 保存新结果文件
        result_md_path = txt_path.replace('.txt', f'__{summary_type}.md')
        with open(result_md_path, 'w', encoding='utf-8') as f:
            f.write(result_text)

        # 自动打标签
        try:
            tags = await generate_tags(text_content, model=model, sio=sio, job_id=job_id)
        except Exception:
            tags = []

        # 更新job
        new_version = current_version + 1
        new_files = dict(files)
        new_files['result_md'] = result_md_path
        update_job(job_id, status='completed',
                   result_text=result_text,
                   summary_type=summary_type,
                   model=model,
                   files=new_files,
                   token_usage={'input': in_tok, 'output': out_tok},
                   tags=tags,
                   summary_version=new_version,
                   error=None)

        # ★ 保存新版本到 summary_history
        from db.job_repo import save_summary_version
        save_summary_version(
            job_id=job_id, version=new_version,
            summary_type=summary_type, model=model,
            result_text=result_text,
            tags=tags,
            token_usage={'input': in_tok, 'output': out_tok},
            files=new_files,
        )

        logger.info(f"任务 {job_id} 重新总结完成 v{new_version}: type={summary_type}, tokens in={in_tok} out={out_tok}")
        return {
            "success": True,
            "job_id": job_id,
            "result_text": result_text,
            "token_usage": {"input": in_tok, "output": out_tok},
            "tags": tags,
            "message": "重新总结完成"
        }

    except Exception as e:
        from services.ai_processor import TaskCancelledError
        if isinstance(e, TaskCancelledError):
            logger.info(f"任务 {job_id} 重新总结被用户取消")
            update_job(job_id, status='cancelled', error='用户取消')
            raise HTTPException(status_code=499, detail="任务已被用户取消")
        logger.error(f"任务 {job_id} 重新总结失败: {e}")
        update_job(job_id, status='failed', error=str(e))
        raise HTTPException(status_code=500, detail=f"重新总结失败: {str(e)}")


# ============ 总结历史版本 API ============

@router.get("/{job_id}/summary-history")
async def get_job_summary_history(job_id: str):
    """获取任务的所有总结历史版本列表"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    history = get_summary_history(job_id)
    # 如果 history 为空但 latest 已有 result_text，把当前结果作为 v1 返回
    if not history and job.get('result_text'):
        history = [{
            'version': job.get('summary_version', 1),
            'summary_type': job.get('summary_type', 'summary'),
            'model': job.get('model', ''),
            'result_text': job['result_text'],
            'tags': job.get('tags', []),
            'token_usage': job.get('token_usage', {}),
            'files': job.get('files', {}),
            'created_at': job.get('created_at', ''),
        }]
    # 精简返回：排除 result_text 全文（太大了），前端点击版本时再加载全文
    summary_list = []
    for h in history:
        text_preview = (h.get('result_text', '') or '')[:100]
        summary_list.append({
            'version': h.get('version', 0),
            'summary_type': h.get('summary_type', ''),
            'model': h.get('model', ''),
            'created_at': h.get('created_at', ''),
            'text_preview': text_preview,
            'char_count': len(h.get('result_text', '') or ''),
        })
    return {
        "success": True,
        "job_id": job_id,
        "current_version": job.get('summary_version', 1),
        "total_versions": len(summary_list),
        "versions": summary_list,
    }


@router.get("/{job_id}/summary-history/{version}")
async def get_job_summary_version(job_id: str, version: int):
    """获取任务指定版本的总结全文"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    # 如果请求的是当前最新版本，直接从 jobs 表返回
    if version == job.get('summary_version', 1) and job.get('result_text'):
        return {
            "success": True,
            "job_id": job_id,
            "version": version,
            "summary_type": job.get('summary_type', ''),
            "model": job.get('model', ''),
            "result_text": job['result_text'],
            "tags": job.get('tags', []),
            "created_at": job.get('created_at', ''),
        }
    # 否则从 summary_history 表查询
    ver = get_summary_version(job_id, version)
    if not ver:
        raise HTTPException(status_code=404, detail=f"版本 {version} 不存在")
    return {
        "success": True,
        "job_id": job_id,
        "version": ver.get('version', version),
        "summary_type": ver.get('summary_type', ''),
        "model": ver.get('model', ''),
        "result_text": ver.get('result_text', ''),
        "tags": ver.get('tags', []),
        "created_at": ver.get('created_at', ''),
    }


@router.post("/batch")
async def batch_submit(request: Request):
    """批量提交URL
    body: {"urls": ["url1","url2",...], "options": {...}}  或直接 ["url1","url2",...]（兼容旧前端缓存）

    ★ 完善：①URL 去重过滤 ②生成 batch_id 聚合 ③传递完整参数（model/summary_type/开关）
    并发控制由 main.py 的全局信号量保证（MAX_CONCURRENT_JOBS），这里只负责创建启动
    """
    import uuid
    from typing import List

    # ★ 兼容两种 body 格式：旧前端传 list，新前端传 dict
    #   用 Request 直接拿原始 JSON，避免 FastAPI dict 类型校验拒绝 list
    body = await request.json()

    if not isinstance(body, (list, dict)):
        raise HTTPException(status_code=400, detail='批量请求必须是 URL 数组或对象')

    # 兼容两种格式
    if isinstance(body, list):
        urls: List[str] = body
        opts = {}
        model = DEFAULT_MODEL
        summary_type = "summary"
        do_summary, do_translate, do_subtitle = True, False, False
        preview_before_summary = True
        dimensions = None
    else:
        urls = body.get("urls", [])
        opts = body.get("options", {}) or {}
        model = body.get("model", DEFAULT_MODEL)
        summary_type = body.get("summary_type", "summary")
        do_summary = body.get("do_summary", True)
        do_translate = body.get("do_translate", False)
        do_subtitle = body.get("do_subtitle", False)
        preview_before_summary = body.get("preview_before_summary", True)

    if not isinstance(urls, list):
        raise HTTPException(status_code=400, detail='urls 必须是数组')
    if not isinstance(opts, dict):
        raise HTTPException(status_code=400, detail='options 必须是对象')
    if len(urls) > 200:
        raise HTTPException(status_code=413, detail='单次最多提交 200 个 URL')

    if isinstance(body, dict):
        # 模块化提示词：从顶层或 options.dimensions 提取
        dimensions = body.get("dimensions") or opts.get("dimensions")

    # ★ URL 去重 + 清洗（智能提取URL + 去空行 + 去重复，保持顺序）
    seen_norm = set()   # 规范化URL去重（同一视频不同query参数）
    seen_raw = set()    # 原始URL去重（完全相同的字符串）
    clean_urls: List[str] = []
    skipped = 0
    duplicates = []  # 跨历史重复的 URL，返回给前端用于跳转
    for url in urls:
        if not isinstance(url, str):
            skipped += 1
            continue
        # ★ 智能提取：从混合文本中提取干净 URL（如 "3. https://..." → "https://..."）
        extracted = extract_urls(url)
        if not extracted:
            # 无有效 URL，跳过（可能是纯文字行）
            skipped += 1
            continue
        # 取第一个 URL（一行一个 URL 的场景）
        u = extracted[0]
        nu = normalize_url(u)
        if nu in seen_norm or u in seen_raw:
            skipped += 1
            continue
        seen_raw.add(u)
        seen_norm.add(nu)
        # ★ 跨历史任务 URL 去重：检查是否已有同 URL 的任务
        existing = find_job_by_url(u)
        if existing and existing.get('status') in ('pending', 'completed', 'preview', 'downloading', 'transcribing', 'summarizing'):
            # 已完成/进行中的任务，跳过并返回已有任务信息
            skipped += 1
            duplicates.append({
                'url': u,
                'existing_job_id': existing['id'],
                'existing_status': existing['status'],
                'existing_title': existing.get('title', ''),
            })
            continue
        clean_urls.append(u)

    if not clean_urls:
        raise HTTPException(status_code=400, detail="没有有效的URL（全部为空或重复）")

    # 生成 batch_id 聚合所有子任务
    batch_id = str(uuid.uuid4())[:8]

    job_ids = []
    for url in clean_urls:
        job_id = create_job(
            input_type="url",
            input_url=url,
            model=model,
            summary_type=summary_type,
            options=opts,
            do_summary=do_summary,
            do_translate=do_translate,
            do_subtitle=do_subtitle,
            preview_before_summary=preview_before_summary,
            dimensions=dimensions,
            batch_id=batch_id,
        )
        from main import start_job
        start_job(job_id)
        job_ids.append(job_id)

    return {
        "batch_id": batch_id,
        "job_ids": job_ids,
        "count": len(job_ids),
        "skipped": skipped,
        "duplicates": duplicates,
        "message": f"已提交 {len(job_ids)} 个批量任务（跳过 {skipped} 个无效/重复URL），并发数受全局限制",
    }


@router.get("/batch/{batch_id}")
async def get_batch_status(batch_id: str):
    """查询批量任务整体进度"""
    summary = get_batch_summary(batch_id)
    if not summary:
        raise HTTPException(status_code=404, detail=f"批量任务 {batch_id} 不存在")
    return summary


# ============ 转录文本全文搜索 ============
@router.get("/search/transcript")
async def search_transcripts(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    limit: int = Query(50, ge=1, le=200, description="最多返回任务数"),
    max_matches_per_job: int = Query(5, ge=1, le=20, description="每个任务最多返回匹配片段数"),
    context_chars: int = Query(80, ge=20, le=300, description="匹配上下文字符数"),
):
    """
    全文搜索转录文本（跨历史任务）。

    搜索范围：所有有 txt 文件的任务（转录文本/OCR文本）。
    返回每个匹配任务的片段（带上下文 + 时间戳）。

    返回:
    {
      "query": q,
      "total_jobs_searched": N,
      "total_jobs_matched": M,
      "results": [
        {
          "job_id": "...", "title": "...", "platform": "...", "created_at": "...",
          "match_count": K,
          "matches": [
            {"text": "...带高亮上下文...", "timestamp": "MM:SS 或 null", "line_no": N}
          ]
        }
      ]
    }
    """
    import re
    from pathlib import Path
    from db.database import get_connection

    conn = get_connection()
    # 只搜有 txt 文件路径的任务（不限状态，失败的任务也可能有部分转录）
    rows = conn.execute("""
        SELECT id, title, platform, status, created_at, files
        FROM jobs
        WHERE files LIKE '%"txt"%'
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit * 3,)).fetchall()  # 多取一些，过滤掉无文件的
    conn.close()

    results = []
    searched = 0
    # 时间戳正则：[MM:SS - MM:SS] 或 [MM:SS]
    ts_pattern = re.compile(r'\[(\d{1,2}:\d{2}(?::\d{2})?)\s*(?:-\s*\d{1,2}:\d{2}(?::\d{2})?)?\]')

    for row in rows:
        import json
        files = row['files']
        try:
            files_dict = json.loads(files) if isinstance(files, str) else (files or {})
        except Exception:
            continue
        txt_path = files_dict.get('txt', '')
        if not txt_path or not Path(txt_path).exists():
            continue
        searched += 1
        try:
            with open(txt_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception:
            continue

        # 按行搜索（转录文本通常一行一段，带时间戳）
        lines = content.split('\n')
        matches = []
        q_lower = q.lower()
        for line_no, line in enumerate(lines, 1):
            if q_lower in line.lower():
                # 提取该行时间戳
                ts_match = ts_pattern.search(line)
                timestamp = ts_match.group(1) if ts_match else None

                # 构建上下文（取匹配位置前后 context_chars 字符）
                idx = line.lower().find(q_lower)
                start = max(0, idx - context_chars)
                end = min(len(line), idx + len(q) + context_chars)
                context = line[start:end]
                if start > 0:
                    context = '...' + context
                if end < len(line):
                    context = context + '...'

                matches.append({
                    "text": context,
                    "timestamp": timestamp,
                    "line_no": line_no,
                })
                if len(matches) >= max_matches_per_job:
                    break

        if matches:
            results.append({
                "job_id": row['id'],
                "title": row['title'] or '(未命名)',
                "platform": row['platform'] or '',
                "status": row['status'],
                "created_at": row['created_at'],
                "match_count": len(matches),
                "matches": matches,
            })
            if len(results) >= limit:
                break

    return {
        "query": q,
        "total_jobs_searched": searched,
        "total_jobs_matched": len(results),
        "results": results,
    }


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


# ============ 标签管理 API ============

@router.post("/{job_id}/generate-tags")
async def generate_job_tags(job_id: str, body: dict = None):
    """对已完成的任务用 AI 生成分类标签"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    if job['status'] != 'completed':
        raise HTTPException(status_code=400, detail="只能对已完成的任务生成标签")

    files = job.get('files') or {}
    txt_path = files.get('txt', '')
    if not txt_path or not Path(txt_path).exists():
        raise HTTPException(status_code=400, detail="任务没有可用的转录文本")

    with open(txt_path, 'r', encoding='utf-8') as f:
        text_content = f.read()

    if not text_content.strip():
        raise HTTPException(status_code=400, detail="转录文本为空")

    model = (body or {}).get('model', job.get('model', DEFAULT_MODEL))

    from services.ai_processor import generate_tags
    from main import sio
    try:
        tags = await generate_tags(text_content, model=model, sio=sio, job_id=job_id)
    except Exception as e:
        logger.error(f"AI 生成标签失败: {e}")
        raise HTTPException(status_code=500, detail=f"AI 生成标签失败: {str(e)}")

    update_job(job_id, tags=tags)
    logger.info(f"任务 {job_id} 标签已生成: {tags}")
    return SuccessResponse(message=f"已生成 {len(tags)} 个标签", data={"tags": tags})


# ============ 思维导图 API ============

@router.post("/{job_id}/mindmap")
async def generate_job_mindmap(job_id: str, body: dict = None):
    """对已完成的任务生成 markmap 思维导图"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    if not job.get('result_text'):
        raise HTTPException(status_code=400, detail="任务没有总结结果，无法生成思维导图")

    model = (body or {}).get('model', job.get('model', DEFAULT_MODEL))
    from services.ai_processor import generate_mindmap
    from main import sio
    try:
        mindmap_md = await generate_mindmap(
            result_text=job['result_text'],
            title=job.get('title', ''),
            model=model,
            sio=sio,
            job_id=job_id,
        )
    except Exception as e:
        logger.error(f"思维导图生成失败: {e}")
        raise HTTPException(status_code=500, detail=f"思维导图生成失败: {str(e)}")

    return SuccessResponse(message="思维导图已生成", data={"mindmap_md": mindmap_md})


@router.put("/{job_id}/tags")
async def update_job_tags(job_id: str, body: dict):
    """手动更新任务的标签列表"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")

    tags = body.get('tags', [])
    if not isinstance(tags, list):
        raise HTTPException(status_code=400, detail="tags 必须是字符串数组")

    # 去重 + 去空格
    tags = list(set(t.strip() for t in tags if t and t.strip()))
    update_job(job_id, tags=tags)
    logger.info(f"任务 {job_id} 标签已更新: {tags}")
    return SuccessResponse(message=f"标签已更新 ({len(tags)} 个)", data={"tags": tags})
