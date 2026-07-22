"""
万能格式转换器 — API 路由

端点：
  GET  /api/converter/engines       引擎检测状态
  GET  /api/converter/formats       格式支持矩阵
  POST /api/converter/convert       上传文件并转换
  GET  /api/converter/download/{fid} 下载转换结果
"""
from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from config import DATA_DIR
from services.format_converter import (
    check_engines,
    classify_file,
    available_outputs,
    convert,
    OUTPUT_TYPE,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# ============ 文件存储目录 ============
CONVERTER_DIR = DATA_DIR / "converter"
UPLOADS_DIR = CONVERTER_DIR / "uploads"
OUTPUTS_DIR = CONVERTER_DIR / "outputs"
for d in (CONVERTER_DIR, UPLOADS_DIR, OUTPUTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ============ 过期文件清理（默认24小时） ============
_FILE_TTL = timedelta(hours=24)


def cleanup_expired_files() -> int:
    """清理超过 TTL 的上传/输出文件，返回清理数量"""
    now = datetime.now()
    removed = 0
    for folder in (UPLOADS_DIR, OUTPUTS_DIR):
        for item in folder.iterdir():
            if item.is_dir():
                # 清理整个任务目录
                try:
                    mtime = datetime.fromtimestamp(item.stat().st_mtime)
                    if now - mtime > _FILE_TTL:
                        shutil.rmtree(item, ignore_errors=True)
                        removed += 1
                except OSError:
                    pass
            elif item.is_file():
                try:
                    mtime = datetime.fromtimestamp(item.stat().st_mtime)
                    if now - mtime > _FILE_TTL:
                        item.unlink(missing_ok=True)
                        removed += 1
                except OSError:
                    pass
    return removed


# ============ API 端点 ============

@router.get("/engines")
async def api_engines():
    """返回各引擎安装状态"""
    engines = check_engines()
    return {
        "engines": engines,
        "summary": {
            "ffmpeg": engines["ffmpeg"],
            "pillow": engines["pillow"],
        },
    }


@router.get("/formats")
async def api_formats():
    """返回格式支持矩阵"""
    engines = check_engines()
    return {
        "video": {
            "input": [".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".gif", ".ts", ".m4v", ".3gp"],
            "output": ["mp4", "mkv", "webm", "avi", "mov", "gif"] if engines["ffmpeg"] else [],
        },
        "audio": {
            "input": [".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a", ".opus", ".wma"],
            "output": ["mp3", "wav", "aac", "ogg", "flac", "m4a", "opus"] if engines["ffmpeg"] else [],
        },
        "image": {
            "input": [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"],
            "output": ["png", "jpg", "webp", "bmp"] if engines["pillow"] else [],
        },
    }


@router.post("/convert")
async def api_convert(
    file: UploadFile = File(...),
    target: str = Form(...),
    quality: str = Form("medium"),
):
    """
    上传文件并转换。

    参数
    ----
    file : UploadFile  上传的文件
    target : str       目标格式（不带点），如 "mp3", "docx", "png"
    quality : str      质量预设 "low" | "medium" | "high"
    """
    # 参数校验
    if quality not in ("low", "medium", "high"):
        raise HTTPException(status_code=400, detail="quality 必须为 low/medium/high")

    target = target.lower().lstrip(".")
    if not target:
        raise HTTPException(status_code=400, detail="请指定目标格式")

    # 生成任务 ID 和临时目录
    fid = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    task_dir = UPLOADS_DIR / fid
    task_dir.mkdir(parents=True, exist_ok=True)

    # 安全文件名
    safe_name = Path(file.filename or "input").name
    input_path = task_dir / safe_name

    try:
        # 保存上传文件
        content = await file.read()
        input_path.write_bytes(content)
        logger.info(f"[converter] 收到文件: {safe_name} ({len(content)} bytes) → {target}")

        # 执行转换
        input_ext = input_path.suffix.lower()
        file_type = classify_file(str(input_path))

        if file_type == "unknown":
            raise HTTPException(status_code=400, detail=f"不支持的文件格式：{input_ext}")

        result = await convert(str(input_path), target, quality)

        # 将输出移到 outputs 目录
        out_src = Path(result["output_path"])
        out_dir = OUTPUTS_DIR / fid
        out_dir.mkdir(parents=True, exist_ok=True)
        output_name = Path(safe_name).stem + "." + target
        out_dst = out_dir / output_name
        shutil.move(str(out_src), str(out_dst))

        output_size = out_dst.stat().st_size

        return {
            "success": True,
            "download_id": fid,
            "input_name": safe_name,
            "input_type": file_type,
            "input_format": input_ext.lstrip("."),
            "output_name": output_name,
            "output_format": target,
            "output_size": output_size,
            "engine": result["engine"],
            "elapsed": result["elapsed"],
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception:
        logger.exception(f"[converter] 转换异常: {safe_name} → {target}")
        raise HTTPException(status_code=500, detail="转换过程发生未知错误")
    finally:
        # 清理上传临时文件（任务目录）
        if task_dir.exists():
            try:
                shutil.rmtree(task_dir, ignore_errors=True)
            except OSError:
                pass


@router.get("/download/{fid}")
async def api_download(fid: str):
    """下载转换结果"""
    out_dir = OUTPUTS_DIR / fid
    if not out_dir.exists() or not out_dir.is_dir():
        raise HTTPException(status_code=404, detail="转换结果不存在或已过期")

    # 取目录下第一个文件
    files = list(out_dir.iterdir())
    if not files:
        raise HTTPException(status_code=404, detail="转换结果不存在或已过期")

    file_path = files[0]
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="转换结果不存在或已过期")

    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="application/octet-stream",
    )
