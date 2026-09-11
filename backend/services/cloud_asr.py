"""
云端 ASR 转录服务（SiliconFlow / SenseVoiceSmall）

SiliconFlow /v1/audio/transcriptions 只返回纯文本（无时间戳），
时间轴由本地静音切分边界提供：ffmpeg silencedetect 找静音中点作切点，
分片 6~30s（≤30s/片 ≈ ≤200KB mp3，天然绕开单次调用 ≤1h/≤50MB 限制，
两小时以上的视频也按 ~几百个分片顺序上传）。
契约与本地 Whisper 引擎一致：返回 (srt_path, txt_path, seg_count)。

错误语义（必须可见可行动，禁止静默吞错）：
- CloudASRConfigError：401/403/404 配置类错误，不重试，调用方回退本地并给修复指引
- CloudASRUnavailableError：429/5xx/网络错误退避重试耗尽，调用方回退本地
"""
import logging
import math
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import httpx

from services.transcriber import (
    TranscriptionCancelledError,
    _format_duration,
    _format_mmss,
    _format_srt_time,
    _get_audio_duration_seconds,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_FFMPEG_BIN = str(_PROJECT_ROOT / "bin" / "ffmpeg.exe")

# 分片参数：句级字幕粒度
_SEG_MIN = 3.0    # 分片下限：短于 3s 的静音间隔不切（并入前段）
_SEG_MAX = 30.0   # 分片上限：无静音可切时强制等分（保证单片远小于 API 限制）
_SILENCE_NOISE_DB = -35  # 静音判定阈值
_SILENCE_MIN_D = 0.5     # 静音最短时长

# API 调用
_TIMEOUT_PER_CALL = 90.0
_MAX_RETRIES = 3
_BACKOFF_CAP = 8.0
# 有界并发：SiliconFlow 限流为账号级 RPM/TPM（L0 免费档 RPM 1000），
# 4 路并发 + 429 退避重试自适应兜底；env 可调
_DEFAULT_CONCURRENCY = 4

# SenseVoice 富文本标签（<|HAPPY|> / <|BGM|> / <|zh|> 等），防御性清洗
_RICH_TAG_RE = re.compile(r'<\|[A-Za-z_]+\|>')
# silencedetect 输出解析
_SILENCE_START_RE = re.compile(r'silence_start:\s*([\d.]+)')
_SILENCE_END_RE = re.compile(r'silence_end:\s*([\d.]+)')


class CloudASRConfigError(Exception):
    """云端 ASR 配置类错误（401/403/404/未配置），修复前重试无意义。"""


class CloudASRUnavailableError(Exception):
    """云端 ASR 暂不可用（429/5xx/网络），重试耗尽后抛出。"""


def _asr_cfg() -> dict:
    """读取云端 ASR 配置（getattr 容错，便于测试 monkeypatch config 属性）。"""
    import config as _config
    return {
        'enabled': bool(getattr(_config, 'ASR_CLOUD_ENABLED', False)),
        'endpoint': getattr(_config, 'ASR_CLOUD_ENDPOINT',
                            'https://api.siliconflow.cn/v1/audio/transcriptions'),
        'api_key': getattr(_config, 'ASR_CLOUD_API_KEY', ''),
        'model': getattr(_config, 'ASR_CLOUD_MODEL', 'FunAudioLLM/SenseVoiceSmall'),
    }


def cloud_asr_configured() -> bool:
    """云端 ASR 是否已配置可用（开关开 + 端点 + 密钥齐全）。"""
    cfg = _asr_cfg()
    return cfg['enabled'] and bool(cfg['endpoint']) and bool(cfg['api_key'])


def _concurrency() -> int:
    """并发上传路数（config/env 可调，钳制 1~8）。"""
    import config as _config
    raw = os.getenv('ASR_CLOUD_CONCURRENCY') or getattr(_config, 'ASR_CLOUD_CONCURRENCY', None)
    try:
        return max(1, min(int(raw), 8))
    except (TypeError, ValueError):
        return _DEFAULT_CONCURRENCY


def strip_rich_tags(text: str) -> str:
    """清洗 SenseVoice 富文本标签并归一化空白。"""
    if not text:
        return ''
    cleaned = _RICH_TAG_RE.sub(' ', text)
    return re.sub(r'\s+', ' ', cleaned).strip()


def parse_silences(stderr_text: str, total_duration: Optional[float] = None) -> List[Tuple[float, float]]:
    """解析 ffmpeg silencedetect 的 stderr 输出，返回 [(silence_start, silence_end), ...]。

    末段静音若延伸到文件结束没有 silence_end，用 total_duration 补齐。
    """
    starts = [float(m) for m in _SILENCE_START_RE.findall(stderr_text or '')]
    ends = [float(m) for m in _SILENCE_END_RE.findall(stderr_text or '')]
    silences: List[Tuple[float, float]] = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else (total_duration if total_duration else s)
        silences.append((s, e))
    return silences


def plan_cuts(total_duration: float, silence_midpoints: List[float]) -> List[float]:
    """把静音中点加工成实际切点列表（纯函数，单测覆盖）。

    规则：
    1. 相邻切点间距 ≥ _SEG_MIN（过近的静音不切，避免字幕碎片）
    2. 相邻切点间距 > _SEG_MAX 时强制等分（无静音的长语音也能保证分片粒度）
    3. 切点严格落在 (0, total_duration) 内
    返回切点（含 total_duration 收尾），调用方按 [prev, next] 组装分片区间。
    """
    if total_duration <= 0:
        return []
    candidates = sorted({round(t, 3) for t in silence_midpoints if 0 < t < total_duration})

    # 规则 1：过滤过近切点
    kept: List[float] = []
    last = 0.0
    for t in candidates:
        if t - last >= _SEG_MIN:
            kept.append(t)
            last = t

    # 规则 2：长区间强制等分
    cuts: List[float] = []
    last = 0.0
    for t in kept + [total_duration]:
        span = t - last
        if span > _SEG_MAX:
            n = math.ceil(span / _SEG_MAX)
            step = span / n
            for k in range(1, n):
                cuts.append(round(last + k * step, 3))
        cuts.append(round(t, 3))
        last = t
    return cuts


def _ffmpeg_bin() -> str:
    return shutil.which('ffmpeg') or _FFMPEG_BIN


def detect_silences(audio_path: str, total_duration: Optional[float]) -> List[Tuple[float, float]]:
    """跑 ffmpeg silencedetect，返回静音区间列表。失败按无静音处理（切点走强制等分）。"""
    cmd = [
        _ffmpeg_bin(), '-v', 'info', '-nostats',
        '-i', audio_path,
        '-af', f'silencedetect=noise={_SILENCE_NOISE_DB}dB:d={_SILENCE_MIN_D}',
        '-f', 'null', '-',
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return parse_silences(result.stderr, total_duration)
    except Exception as e:
        logger.warning(f'静音探测失败（按无静音处理，切点走强制等分）: {e}')
        return []


def export_chunks(audio_path: str, cuts: List[float]) -> List[str]:
    """按切点导出 16k 单声道 mp3 分片，返回分片路径列表（有序）。

    cuts 含收尾切点（=总时长），但 segment_times 只传内部切点：
    ffprobe 时长与实际音频存在毫秒级偏差，把收尾切点传给 ffmpeg 会在
    EOF 前多切出一个微型尾片（冒烟实测 4 片 vs 计划 3 片）。最后一片
    由 ffmpeg 自动延伸到实际 EOF。单片场景（无内部切点）直接整段转码。
    每片 ≤30s → ≤200KB，远小于 API 单次 ≤50MB 限制。
    """
    src = Path(audio_path)
    out_dir = src.parent
    prefix = src.stem
    pattern = str(out_dir / f'{prefix}_cloud_%03d.mp3')
    internal_cuts = cuts[:-1] if len(cuts) > 1 else []
    if internal_cuts:
        times = ','.join(f'{t:.3f}' for t in internal_cuts)
        cmd = [
            _ffmpeg_bin(), '-y', '-v', 'error',
            '-i', audio_path,
            '-f', 'segment', '-segment_times', times,
            '-ac', '1', '-ar', '16000',
            '-c:a', 'libmp3lame', '-b:a', '48k',
            pattern,
        ]
    else:
        cmd = [
            _ffmpeg_bin(), '-y', '-v', 'error',
            '-i', audio_path,
            '-ac', '1', '-ar', '16000',
            '-c:a', 'libmp3lame', '-b:a', '48k',
            pattern.replace('%03d', '000'),
        ]
    subprocess.run(cmd, capture_output=True, check=False, timeout=600)
    chunks = sorted(out_dir.glob(f'{prefix}_cloud_*.mp3'))
    if not chunks:
        raise CloudASRUnavailableError(
            f'ffmpeg 分片导出失败（音频: {src.name}），无法继续云端转录'
        )
    return [str(c) for c in chunks]


def _do_request(cfg: dict, path: str, timeout: float) -> httpx.Response:
    """单次 API 调用（独立函数便于测试 monkeypatch）。"""
    with open(path, 'rb') as f:
        with httpx.Client(timeout=timeout) as client:
            return client.post(
                cfg['endpoint'],
                headers={'Authorization': f"Bearer {cfg['api_key']}"},
                files={'file': (Path(path).name, f, 'audio/mpeg')},
                data={'model': cfg['model']},
            )


def extract_sample(audio_path: str, max_seconds: float = 90.0) -> str:
    """提取音频开头片段为 16k 单声道 mp3（B站字幕抽查云端转写用）。

    与本地 Whisper 抽查（transcribe_audio_sample）互为备份：云端先试，
    失败回本地。调用方负责删除返回的临时文件。
    """
    src = Path(audio_path)
    out = src.with_name(f'{src.stem}_sample_cloud.mp3')
    cmd = [
        _ffmpeg_bin(), '-y', '-v', 'error',
        '-i', audio_path,
        '-t', str(max_seconds),
        '-ac', '1', '-ar', '16000',
        '-c:a', 'libmp3lame', '-b:a', '48k',
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, check=False, timeout=120)
    if not out.exists() or out.stat().st_size <= 0:
        raise CloudASRUnavailableError(
            f'ffmpeg 抽取音频开头片段失败（{src.name}, returncode={result.returncode}）'
        )
    return str(out)


def transcribe_file(path: str, timeout: float = _TIMEOUT_PER_CALL, max_retries: int = _MAX_RETRIES) -> str:
    """单文件一次云端转写，返回清洗后的纯文本。

    401/403/404 配置类错误立即抛 CloudASRConfigError（重试无意义）；
    429/5xx/网络错误指数退避重试，耗尽抛 CloudASRUnavailableError。
    供完整转录与 B站字幕抽查共用。
    """
    cfg = _asr_cfg()
    if not cloud_asr_configured():
        raise CloudASRConfigError('云端 ASR 未配置（config.py 缺 endpoint/api_key 或未启用）')

    last_reason = ''
    for attempt in range(max_retries):
        try:
            resp = _do_request(cfg, path, timeout)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_reason = f'{type(e).__name__}: {e}'
            logger.warning(f'云端 ASR 网络错误（第 {attempt + 1}/{max_retries} 次）: {last_reason}')
        else:
            if resp.status_code == 200:
                try:
                    text = (resp.json() or {}).get('text', '')
                except ValueError:
                    last_reason = f'响应非 JSON: {resp.text[:200]}'
                    logger.warning(f'云端 ASR 响应异常: {last_reason}')
                else:
                    return strip_rich_tags(text)
            elif resp.status_code in (401, 403, 404):
                detail = resp.text[:200]
                if resp.status_code == 401:
                    hint = 'ASR 密钥无效，请检查 config.py 的 ASR_CLOUD_API_KEY'
                elif resp.status_code == 403:
                    hint = 'ASR 密钥无权限（可能未开通语音服务或余额不足）'
                else:
                    hint = f'ASR 模型不存在（model={cfg["model"]}），请检查 ASR_CLOUD_MODEL'
                raise CloudASRConfigError(f'云端 ASR {resp.status_code}: {hint} | 响应: {detail}')
            else:
                last_reason = f'HTTP {resp.status_code}: {resp.text[:200]}'
                logger.warning(f'云端 ASR 服务错误（第 {attempt + 1}/{max_retries} 次）: {last_reason}')

        if attempt < max_retries - 1:
            time.sleep(min(2 ** attempt, _BACKOFF_CAP))
    raise CloudASRUnavailableError(f'云端 ASR 重试 {max_retries} 次后仍失败: {last_reason}')


def transcribe_audio_cloud(
    audio_path: str,
    progress_callback: Optional[Callable] = None,
    cancel_check: Optional[Callable] = None,
    language: Optional[str] = None,
    speaker_diarization: bool = False,
) -> Tuple[str, str, int]:
    """云端完整转录：静音切分 → 分片上传 → 句级时间戳 SRT/TXT。

    与本地 Whisper 引擎同契约，返回 (srt_path, txt_path, seg_count)。
    SenseVoice 自动识别语种（language 参数仅记录）；不支持说话人分离。
    """
    if language:
        logger.info(f'云端 SenseVoice 自动识别语种（忽略指定语言 {language}）')
    if speaker_diarization:
        logger.warning('云端引擎暂不支持说话人分离，已忽略该选项')

    src = Path(audio_path)
    duration = _get_audio_duration_seconds(audio_path) or 0.0
    if progress_callback:
        progress_callback(2, f'云端转录（SenseVoice）| 时长 {_format_duration(duration)} | 静音探测中...')

    silences = detect_silences(audio_path, duration)
    midpoints = [(s + e) / 2 for s, e in silences]
    cuts = plan_cuts(duration, midpoints)
    if not cuts:
        raise CloudASRUnavailableError(f'无法确定音频时长（{src.name}），云端转录中止')

    if progress_callback:
        seg_count_est = len(cuts)
        progress_callback(4, f'静音探测完成：{len(silences)} 处静音 → {seg_count_est} 个分片，导出中...')

    chunk_paths = export_chunks(audio_path, cuts)
    # 分片区间：[0, c1), [c1, c2), ..., [cN-1, total]（切点数 = 分片数）
    boundaries = [0.0] + cuts
    segments: List[Tuple[float, float]] = list(zip(boundaries[:-1], boundaries[1:]))
    if len(chunk_paths) != len(segments):
        planned = len(segments)
        if len(chunk_paths) > planned:
            # 实际分片多于计划（探测时长偏差导致尾部多切）：前段区间保持，
            # 从计划的最后一段起点起全部并入尾段直到总时长
            head = segments[:len(chunk_paths) - 1]
            last_start = head[-1][1] if head else 0.0
            segments = head + [(last_start, duration)]
            logger.warning(f'分片数多于计划（{len(chunk_paths)} vs {planned}），多余分片已并入尾段')
        else:
            # 分片少于计划（探测时长高估，末尾内部切点越过 EOF）：退化为等分
            logger.warning(
                f'分片数少于计划（{len(chunk_paths)} vs {len(segments)}），按等分时长处理'
            )
            step = duration / len(chunk_paths) if len(chunk_paths) else 0.0
            segments = [(i * step, (i + 1) * step) for i in range(len(chunk_paths))]

    if progress_callback:
        progress_callback(8, f'开始云端转录：{len(chunk_paths)} 个分片，{_concurrency()} 路并发上传识别...')

    results: List[Tuple[float, float, str]] = []
    transcribe_start = time.time()
    total = len(chunk_paths)
    completed_audio = 0.0  # 已完成分片覆盖的音频秒数（进度/速度用）

    executor = ThreadPoolExecutor(max_workers=_concurrency(),
                                  thread_name_prefix='cloud-asr')
    try:
        futures = {
            executor.submit(transcribe_file, cp): (i, seg_start, seg_end, cp)
            for i, (cp, (seg_start, seg_end)) in enumerate(zip(chunk_paths, segments))
        }
        completed = 0
        for future in as_completed(futures):
            if cancel_check and cancel_check():
                raise TranscriptionCancelledError('用户取消，转录中止')
            _i, seg_start, seg_end, _cp = futures[future]
            text = future.result()  # 异常（配置/重试耗尽）原样向上抛，由调用方回退本地
            completed += 1
            completed_audio += seg_end - seg_start
            if text:
                results.append((seg_start, seg_end, text))

            elapsed = time.time() - transcribe_start
            ratio = (completed_audio / duration) if duration > 0 else completed / total
            pct = int(10 + min(ratio, 1.0) * 88)
            speed_x = (completed_audio / elapsed) if elapsed > 0.5 and completed_audio > 0 else 0.0
            msg = (
                f'云端转录 {pct}% | 分片 {completed}/{total} 完成 | '
                f'已用 {_format_duration(elapsed)}'
            )
            if speed_x > 0:
                msg += f' | 速度 {speed_x:.1f}x'
            if progress_callback:
                progress_callback(pct, msg)
    finally:
        # 取消/失败也要停掉排队任务并清理临时分片，不留垃圾占下载目录
        executor.shutdown(wait=False, cancel_futures=True)
        for cp in chunk_paths:
            try:
                Path(cp).unlink(missing_ok=True)
            except OSError:
                pass

    srt_path = str(src.with_suffix('.srt'))
    txt_path = str(src.with_suffix('.txt'))
    # 并发完成顺序不定，写盘前按音频时间重排（SRT 序号/时间轴必须单调）
    results.sort(key=lambda r: r[0])
    with open(srt_path, 'w', encoding='utf-8') as f_srt, \
         open(txt_path, 'w', encoding='utf-8') as f_txt:
        for idx, (seg_start, seg_end, text) in enumerate(results):
            f_srt.write(f"{idx + 1}\n{_format_srt_time(seg_start)} --> {_format_srt_time(seg_end)}\n{text}\n\n")
            f_txt.write(f"[{_format_mmss(seg_start)} - {_format_mmss(seg_end)}] {text}\n")

    elapsed = time.time() - transcribe_start
    speed_x = (duration / elapsed) if elapsed > 0 and duration > 0 else 0.0
    logger.info(f'云端转录完成: {len(results)} 段（{total} 分片）, 耗时 {_format_duration(elapsed)}')
    if progress_callback:
        done_msg = (
            f'云端转录完成，共 {len(results)} 段 | 音频 {_format_duration(duration)} | '
            f'耗时 {_format_duration(elapsed)}'
        )
        if speed_x > 0:
            done_msg += f' | 均速 {speed_x:.1f}x'
        progress_callback(99, done_msg)
        progress_callback(100, '转录完成！')
    return srt_path, txt_path, len(results)
