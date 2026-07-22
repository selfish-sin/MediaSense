"""
YouTube Tool V2 - 下载/文件查看路由
安全修复：resolve()规范化路径，防止路径遍历攻击
"""
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from config import DOWNLOAD_DIR, DATA_DIR

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
async def download_file(path: str):
    """下载指定文件（安全：resolve防路径遍历）"""
    file_path = _resolve_data_file(path)
    return FileResponse(str(file_path), filename=file_path.name)


@router.get("/view")
async def view_file(path: str):
    """在线查看文件（安全：resolve防路径遍历）"""
    file_path = _resolve_data_file(path)
    return FileResponse(str(file_path))
