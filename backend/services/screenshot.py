"""
YouTube Tool V2 - 视频截图服务
使用 FFmpeg 提取关键帧，支持自定义帧数、时间偏移
"""
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 本地 ffmpeg 路径（项目自带，无需系统 PATH）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FFMPEG_PATH = str(PROJECT_ROOT / "bin" / "ffmpeg.exe")
FFPROBE_PATH = str(PROJECT_ROOT / "bin" / "ffprobe.exe")


def extract_keyframes(
    video_path: str,
    output_dir: str,
    frame_count: int = 10,
    quality: int = 2,
) -> list[dict]:
    """
    从视频提取关键帧截图

    Args:
        video_path: 视频文件路径
        output_dir: 截图输出目录
        frame_count: 提取帧数
        quality: JPEG质量 1-31 (越小越好)

    Returns:
        [{path: "xxx.jpg", time: 12.5, timestamp: "00:00:12.500"}, ...]
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 先获取视频时长
    duration = _get_duration(video_path)
    if not duration:
        logger.warning(f"无法获取视频时长: {video_path}")
        return []

    # 计算间隔，均匀提取
    interval = duration / (frame_count + 1)

    results = []
    for i in range(1, frame_count + 1):
        seek_time = i * interval
        out_file = out_dir / f"frame_{i:03d}.jpg"
        timestamp = _seconds_to_timestamp(seek_time)

        cmd = [
            FFMPEG_PATH,
            "-ss", str(seek_time),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", str(quality),
            "-y",
            str(out_file),
        ]

        try:
            subprocess.run(
                cmd,
                capture_output=True,
                timeout=30,
                check=True,
            )
            if out_file.exists() and out_file.stat().st_size > 100:
                results.append({
                    "path": str(out_file),
                    "filename": out_file.name,
                    "time": round(seek_time, 1),
                    "timestamp": timestamp,
                })
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg 截图失败 frame {i}: {e.stderr.decode()[:200]}")
        except subprocess.TimeoutExpired:
            logger.error(f"FFmpeg 截图超时 frame {i}")

    # 如果均匀提取失败，回退到用 scene 检测提取
    if len(results) < 3:
        logger.info("均匀提取截图不足，改用场景检测回退")
        fallback = _extract_scene_frames(video_path, output_dir, frame_count, quality)
        if fallback:
            # 补充到 results
            existing_files = {r["filename"] for r in results}
            for f in fallback:
                if f["filename"] not in existing_files:
                    results.append(f)
        results = results[:frame_count]

    return results


def _get_duration(video_path: str) -> float | None:
    """获取视频时长（秒）"""
    cmd = [
        FFPROBE_PATH,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError, subprocess.TimeoutExpired):
        return None


def _extract_scene_frames(
    video_path: str,
    output_dir: str,
    frame_count: int = 10,
    quality: int = 2,
) -> list[dict]:
    """使用场景变化检测提取关键帧（回退方案）"""
    out_dir = Path(output_dir)
    cmd = [
        FFMPEG_PATH,
        "-i", video_path,
        "-vf", f"select='gte(scene,0.3)',scale=-1:480",
        "-vsync", "vfr",
        "-vframes", str(frame_count),
        "-q:v", str(quality),
        "-y",
        str(out_dir / "scene_%03d.jpg"),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=60, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.error(f"场景检测截图失败: {e}")
        return []

    # 收集生成的文件
    results = []
    for f in sorted(out_dir.glob("scene_*.jpg")):
        if f.stat().st_size > 100:
            results.append({
                "path": str(f),
                "filename": f.name,
                "time": 0,
                "timestamp": "00:00:00.000",
            })
    return results


def _seconds_to_timestamp(seconds: float) -> str:
    """秒数转 HH:MM:SS.mmm 格式"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"
