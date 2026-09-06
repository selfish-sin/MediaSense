"""
万能格式转换器 — 核心转换引擎
调度 FFmpeg / Pillow 两引擎（视频/音频/图片）

每个公开函数返回：
  {
    "output_path": str,   # 转换后的文件路径
    "engine": str,        # 使用的引擎名称
    "elapsed": float,     # 耗时（秒）
  }

依赖检测：
  check_engines() → {"ffmpeg": True, "pillow": True}
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from config import DATA_DIR

logger = logging.getLogger(__name__)

# ============ 项目内置 bin 目录（ffmpeg/ffprobe） ============
_BIN_DIR = Path(__file__).parent.parent.parent / "bin"

def _find_exe(name: str) -> str | None:
    """查找可执行文件：先系统 PATH，再项目 bin/ 目录"""
    exe = shutil.which(name)
    if exe:
        return exe

    # Windows: 加 .exe 后缀，检查项目 bin/
    for ext in ("", ".exe"):
        p = _BIN_DIR / f"{name}{ext}"
        if p.is_file():
            return str(p)

    return None


# ============ 格式分类定义 ============

VIDEO_FORMATS = {
    ".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".gif",
    ".ts", ".m4v", ".3gp", ".wmv",
}
AUDIO_FORMATS = {
    ".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a", ".opus", ".wma",
}
IMAGE_FORMATS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff",
}

# 视频编码预设
VIDEO_PRESETS: dict[str, dict[str, str]] = {
    "low":    {"crf": "28", "preset": "ultrafast"},
    "medium": {"crf": "23", "preset": "medium"},
    "high":   {"crf": "18", "preset": "slow"},
}

# 图片质量预设（仅 jpg/webp 有损格式生效）
IMAGE_QUALITY: dict[str, int] = {"low": 60, "medium": 85, "high": 95}

# 音频码率预设
AUDIO_BITRATE: dict[str, str] = {"low": "128k", "medium": "320k", "high": "lossless"}

# 输出格式 → 所属类型（前端用）
OUTPUT_TYPE: dict[str, str] = {}
for _ext, _type in [
    ("mp4", "video"), ("mkv", "video"), ("webm", "video"),
    ("avi", "video"), ("mov", "video"), ("gif", "video"),
    ("mp3", "audio"), ("wav", "audio"), ("aac", "audio"),
    ("ogg", "audio"), ("flac", "audio"), ("m4a", "audio"), ("opus", "audio"),
    ("png", "image"), ("jpg", "image"), ("webp", "image"), ("bmp", "image"),
]:
    OUTPUT_TYPE[_ext] = _type


# ============ 公共 API ============

def check_engines() -> dict[str, bool]:
    """检测各引擎是否可用（同步，方便前端实时查询）"""
    result: dict[str, bool] = {}

    # FFmpeg
    result["ffmpeg"] = _find_exe("ffmpeg") is not None

    # Pillow
    try:
        from PIL import Image  # noqa: F401
        result["pillow"] = True
    except ImportError:
        result["pillow"] = False

    return result


def classify_file(filepath: str) -> str:
    """根据扩展名判断文件类型：video | audio | image | unknown"""
    ext = Path(filepath).suffix.lower()
    if ext in VIDEO_FORMATS:
        return "video"
    if ext in AUDIO_FORMATS:
        return "audio"
    if ext in IMAGE_FORMATS:
        return "image"
    return "unknown"


def available_outputs(input_path: str, engines: dict[str, bool] | None = None) -> dict[str, list[str]]:
    """根据输入文件，返回可用的输出格式列表（仅考虑引擎是否就绪）"""
    if engines is None:
        engines = check_engines()
    file_type = classify_file(input_path)
    result: dict[str, list[str]] = {"video": [], "audio": [], "image": []}

    if engines.get("ffmpeg"):
        result["video"] = ["mp4", "mkv", "webm", "avi", "mov", "gif"]
        result["audio"] = ["mp3", "wav", "aac", "ogg", "flac", "m4a", "opus"]

    if engines.get("pillow"):
        result["image"] = ["png", "jpg", "webp", "bmp"]

    # 按输入文件类型筛掉不合理的输出
    if file_type == "video":
        result["image"] = []
    elif file_type == "audio":
        result["video"] = []
        result["image"] = []
    elif file_type == "image":
        result["video"] = []
        result["audio"] = []

    return result


async def convert(input_path: str, target: str, quality: str = "medium") -> dict:
    """
    统一转换入口，异步安全。

    参数
    ----
    input_path : str  输入文件的完整路径
    target : str      目标格式扩展名（不带点），如 "mp3", "png"
    quality : str     质量预设 "low" | "medium" | "high"

    返回
    ----
    {"output_path": str, "engine": str, "elapsed": float}
    """
    ext = Path(input_path).suffix.lower()
    file_type = classify_file(input_path)
    t0 = time.perf_counter()
    engines = check_engines()

    if file_type == "unknown":
        raise ValueError(f"不支持的文件格式：{ext}")

    if file_type in ("video", "audio"):
        if not engines["ffmpeg"]:
            raise RuntimeError("FFmpeg 未安装，无法处理音视频转换")
        target_type = classify_file(f"dummy.{target}")
        if target_type == "unknown":
            target_type = file_type
        output_path, engine = await asyncio.to_thread(
            _ffmpeg_convert, input_path, target, quality, target_type if target_type in ("video", "audio") else file_type,
        )
    elif file_type == "image":
        if not engines["pillow"]:
            raise RuntimeError("Pillow 未安装，无法处理图片转换")
        output_path, engine = await asyncio.to_thread(
            _pillow_convert, input_path, target, quality,
        )
    else:
        raise ValueError(f"不支持的文件格式：{ext}")

    return {
        "output_path": output_path,
        "engine": engine,
        "elapsed": round(time.perf_counter() - t0, 1),
    }


# ============ 内部实现 ============


def _ffmpeg_convert(input_path: str, target: str, quality: str, media_type: str) -> tuple[str, str]:
    """FFmpeg 同步转换（在线程池中调用），返回 (output_path, engine_name)"""
    ffmpeg = _find_exe("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg 未安装")

    output_path = str(Path(input_path).with_suffix(f".{target}"))
    preset = VIDEO_PRESETS.get(quality, VIDEO_PRESETS["medium"])

    if media_type == "video":
        if target == "gif":
            # GIF 动图（低帧率 + 小尺寸）
            cmd = [
                ffmpeg, "-y", "-i", input_path,
                "-vf", "fps=10,scale=480:-1:flags=lanczos",
                "-loop", "0",
                output_path,
            ]
        else:
            cmd = [
                ffmpeg, "-y", "-i", input_path,
                "-c:v", "libx264", "-crf", preset["crf"],
                "-preset", preset["preset"],
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                output_path,
            ]
    else:  # audio
        bitrate = AUDIO_BITRATE.get(quality, "320k")
        if target in ("flac", "wav") and bitrate == "lossless":
            # 无损格式：flac 用 flac 编码器，wav 直出 PCM
            if target == "flac":
                cmd = [ffmpeg, "-y", "-i", input_path, "-c:a", "flac", output_path]
            else:
                cmd = [ffmpeg, "-y", "-i", input_path, output_path]
        else:
            codec_map = {
                "mp3": "libmp3lame", "aac": "aac", "ogg": "libvorbis",
                "opus": "libopus", "m4a": "aac",
            }
            codec = codec_map.get(target, "libmp3lame")
            # mp3 不支持 lossless 码率，high 时用 320k
            br = bitrate if bitrate != "lossless" else "320k"
            if target == "ogg":
                # libvorbis 用 -q:a (0-10) 而非 -b:a，quality: low=2, medium=5, high=8
                q = {"low": "2", "medium": "5", "high": "8"}.get(quality, "5")
                cmd = [ffmpeg, "-y", "-i", input_path, "-c:a", codec, "-q:a", q, output_path]
            else:
                cmd = [
                    ffmpeg, "-y", "-i", input_path,
                    "-c:a", codec, "-b:a", br,
                    output_path,
                ]

    logger.info(f"[ffmpeg] {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip()
        # FFmpeg 错误可能很长，截取最后几行
        err_short = "\n".join(err.splitlines()[-8:])
        raise RuntimeError(f"FFmpeg 转换失败：{err_short}")

    return output_path, "ffmpeg"


def _pillow_convert(input_path: str, target: str, quality: str) -> tuple[str, str]:
    """Pillow 同步转换（在线程池中调用），返回 (output_path, engine_name)"""
    from PIL import Image

    output_path = str(Path(input_path).with_suffix(f".{target}"))
    q = IMAGE_QUALITY.get(quality, 85)

    img = Image.open(input_path)

    # JPEG 不支持透明度，需要转 RGB
    if target in ("jpg", "jpeg") and img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # WebP 可选有损/无损
    save_kwargs: dict = {}
    if target in ("jpg", "jpeg", "webp"):
        save_kwargs["quality"] = q
    if target == "png":
        save_kwargs["optimize"] = True

    img.save(output_path, **save_kwargs)
    return output_path, "pillow"


# ============ 转换临时目录与过期清理 ============
# 原 converter router 已下线，仅启动时清理任务保留在服务层。
CONVERTER_DIR = DATA_DIR / "converter"
UPLOADS_DIR = CONVERTER_DIR / "uploads"
OUTPUTS_DIR = CONVERTER_DIR / "outputs"
for _d in (CONVERTER_DIR, UPLOADS_DIR, OUTPUTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

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
