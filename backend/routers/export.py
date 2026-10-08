"""
YouTube Tool V2 - 导出路由 (Word/PDF/HTML/TXT/批量ZIP)

★ 渲染全部委托 `services/md_render`：那里是唯一解析点（markdown-it token → 块结构），
  四个格式各一个渲染器。本模块只做「校验 → 调渲染 → FileResponse」。
  （2026-10-08 之前这里是四套并行解析：docx 按行手工解析、fpdf2 行循环、
  _md_to_plain_text 正则、_md_to_html 另一条路径。）
"""
import asyncio
import logging
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from models.schemas import ExportRequest, BatchExportRequest
from db.job_repo import get_job
from config import DATA_DIR
from services.naming import export_basename, safe_basename
from services import md_render

logger = logging.getLogger(__name__)
router = APIRouter()

EXPORT_DIR = DATA_DIR / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _load_job(req: ExportRequest) -> Dict[str, Any]:
    """取任务并校验可导出（404 任务不存在 / 400 无结果文本）"""
    job = get_job(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {req.job_id} 不存在")
    if not job.get('result_text'):
        raise HTTPException(status_code=400, detail="任务没有结果文本")
    return job


def _paths(job: Dict[str, Any], ext: str) -> Tuple[Path, str]:
    """(磁盘路径, 对外下载名)——磁盘名附 job_id 短码防同标题互相覆盖"""
    download_name = export_basename(job)
    stem = f"{download_name}_{job['id'][:8]}"
    return EXPORT_DIR / f"{stem}.{ext}", download_name


@router.post("/docx")
async def export_docx(req: ExportRequest):
    """导出为 Word 文档（python-docx，渲染见 services/md_render.render_docx）"""
    job = _load_job(req)
    output_path, download_name = _paths(job, "docx")
    try:
        await asyncio.to_thread(md_render.render_docx, job, job['result_text'], output_path)
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="python-docx 未安装，请运行: pip install python-docx") from exc
    except Exception as exc:
        logger.error(f"Word 生成失败 job={job['id']}: {exc}")
        raise HTTPException(status_code=500, detail=f"Word 生成失败: {exc}") from exc
    return FileResponse(path=str(output_path), filename=f"{download_name}.docx", media_type=DOCX_MEDIA)


@router.post("/pdf")
async def export_pdf(req: ExportRequest):
    """导出为 PDF（渲染见 services/md_render.render_pdf：weasyprint 优先 → fpdf2 兜底）"""
    job = _load_job(req)
    output_path, download_name = _paths(job, "pdf")
    try:
        await asyncio.to_thread(md_render.render_pdf, job, job['result_text'], output_path)
    except Exception as exc:
        logger.error(f"PDF 生成失败 job={job['id']}: {exc}")
        raise HTTPException(status_code=500, detail=f"PDF 生成失败: {exc}") from exc
    return FileResponse(path=str(output_path), filename=f"{download_name}.pdf", media_type="application/pdf")


@router.post("/html")
async def export_html(req: ExportRequest):
    """导出为 HTML 页面"""
    job = _load_job(req)
    output_path, download_name = _paths(job, "html")
    content = md_render.render_html(job, job['result_text'])
    await asyncio.to_thread(output_path.write_text, content, 'utf-8')
    return FileResponse(path=str(output_path), filename=f"{download_name}.html", media_type="text/html")


@router.post("/txt")
async def export_txt(req: ExportRequest):
    """导出为纯文本"""
    job = _load_job(req)
    output_path, download_name = _paths(job, "txt")
    content = md_render.render_text(job, job['result_text'])
    await asyncio.to_thread(output_path.write_text, content, 'utf-8')
    return FileResponse(path=str(output_path), filename=f"{download_name}.txt", media_type="text/plain")

@router.post("/batch")
async def export_batch(req: BatchExportRequest):
    """批量导出：把多个任务的结果文本打包成 ZIP（每个任务一个 .md 文件）

    未完成 / 无结果文本的任务自动跳过（不报错，不是每个任务都有结果可导）；
    全部无结果时返回 400。非倒序、按请求的 job_ids 顺序打包。
    """
    job_ids: List[str] = req.job_ids or []
    if not job_ids:
        raise HTTPException(status_code=400, detail="job_ids 不能为空")

    contents: List[tuple] = []  # (展示标题, 文件内容)
    for jid in job_ids:
        job = get_job(jid)
        if not job or not job.get('result_text'):
            continue
        display_title = job.get('title') or f"任务 {jid}"
        content = f"# {display_title}\n\n{job['result_text'].strip()}\n"
        contents.append((display_title, content))

    if not contents:
        raise HTTPException(status_code=400, detail="选中的任务都没有可导出的结果")

    # 重名标题的文件名追加序号，保证 zip 内文件名唯一
    seen: dict = {}
    def _md_name(title: str) -> str:
        safe = safe_basename(title, max_len=80, fallback='未命名')
        if safe not in seen:
            seen[safe] = 1
            return f"{safe}.md"
        seen[safe] += 1
        return f"{safe}_{seen[safe]}.md"

    # ★ 磁盘名保持唯一（时间戳+随机段，同秒重复导出不互相覆盖）；对外 filename=
    #   用可读名（视频名优先）——「唯一性」与「可读性」解耦
    zip_path = EXPORT_DIR / f"AI总结批量导出_{datetime.now():%Y%m%d-%H%M%S}_{uuid.uuid4().hex[:6]}.zip"
    first_name = safe_basename(contents[0][0], max_len=40, fallback='AI总结')
    zip_download_name = (
        f"{first_name}.zip" if len(contents) == 1
        else f"{first_name}等{len(contents)}个.zip"
    )

    def _build_zip():
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for title, content in contents:
                zf.writestr(_md_name(title), content)

    await asyncio.to_thread(_build_zip)

    return FileResponse(
        path=str(zip_path),
        filename=zip_download_name,
        media_type="application/zip",
    )
