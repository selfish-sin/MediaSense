"""
YouTube Tool V2 - 下载/文件查看路由
安全修复：resolve()规范化路径，防止路径遍历攻击
"""
import logging
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from config import DOWNLOAD_DIR, DATA_DIR
from db.job_repo import get_job
from services.naming import export_basename, safe_basename

logger = logging.getLogger(__name__)
router = APIRouter()

# 预解析安全根目录
_DATA_DIR_RESOLVED = DATA_DIR.resolve()


def _resolve_data_file(path: str) -> Path:
    """Resolve a requested file and require it to be inside DATA_DIR."""
    file_path = Path(path).resolve()
    if not file_path.exists():
        file_path = (DOWNLOAD_DIR / path).resolve()
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    if not file_path.is_relative_to(_DATA_DIR_RESOLVED):
        raise HTTPException(status_code=403, detail="只能访问data目录内的文件")
    return file_path


@router.get("/file")
async def download_file(path: str, job_id: Optional[str] = None, label: Optional[str] = None):
    """下载指定文件（安全：resolve防路径遍历）

    ★ 可选 ``job_id`` + ``label``（前端「下载素材」传入）：下载名改为可读的
      ``{视频名}_{素材类型}.{ext}``，不再直接暴露磁盘名（audio.m4a / bilibili_subtitle.srt）。
      两参任一缺失时回落磁盘文件名——旧调用方与直接调 URL 的行为零变化。
    """
    file_path = _resolve_data_file(path)
    download_name = file_path.name
    if job_id and label:
        job = get_job(job_id)
        base = export_basename(job) if job else ''
        suffix = safe_basename(label, max_len=20)
        if base and suffix:
            download_name = f"{base}_{suffix}{file_path.suffix}"
        elif base:
            download_name = f"{base}{file_path.suffix}"
    return FileResponse(str(file_path), filename=download_name)


@router.get("/view")
async def view_file(path: str):
    """在线查看文件（安全：resolve防路径遍历）"""
    file_path = _resolve_data_file(path)
    return FileResponse(str(file_path))
