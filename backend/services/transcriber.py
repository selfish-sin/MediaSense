"""
YouTube Tool V2 - Whisper 转录服务
使用 faster-whisper（CTranslate2加速），本地模型优先，自动降级
"""
import asyncio
import logging
import os
import re
import time
import shutil
import subprocess
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional, Tuple

from config import LOCAL_MODEL_DIRS, DEFAULT_WHISPER_MODEL

logger = logging.getLogger(__name__)


class TranscriptionCancelledError(Exception):
    """转录进行中检测到用户取消（由调用方注入的 cancel_check 触发）。

    转录在 worker 线程里跑，主流程的 check_cancelled 只能在阶段边界生效；
    不传入循环内部的话，取消后仍要白烧几分钟 GPU/CPU 直到当前音频跑完。
    """


# ============ Whisper 模型进程级缓存 ============
# 模型加载耗时数秒到数十秒且占用大块内存/显存；任务并发上限 2、
# B站字幕抽查也会单独转写，每次重新 WhisperModel(...) 会造成重复加载
# 与内存抖动。同一 (model_path, device, compute_type) 只实例化一次。
# LRU 上限 2：用户可切换 tiny/base/small/medium 四档，GPU→CPU 降级路径
# 还会各留一份，无淘汰会常驻多个大模型（medium GPU 版约 3GB 显存）。
_model_cache: "OrderedDict[tuple, object]" = OrderedDict()
_model_cache_lock = threading.Lock()
_MODEL_CACHE_MAX = 2


def _get_whisper_model(model_path: str, device: str, compute_type: str,
                       local_only: bool, cpu_threads: int, num_workers: int):
    """按 (model_path, device, compute_type) 缓存 WhisperModel（线程安全，LRU 淘汰）。

    淘汰只从缓存移除引用；正在转录的任务仍持有实例引用，用完自然释放。
    """
    key = (model_path, device, compute_type)
    with _model_cache_lock:
        model = _model_cache.get(key)
        if model is not None:
            _model_cache.move_to_end(key)  # LRU touch
            return model
        from faster_whisper import WhisperModel
        model = WhisperModel(
            model_path, device=device, compute_type=compute_type,
            local_files_only=local_only, cpu_threads=cpu_threads, num_workers=num_workers,
        )
        _model_cache[key] = model
        while len(_model_cache) > _MODEL_CACHE_MAX:
            _model_cache.popitem(last=False)  # 淘汰最久未用
    return model

# 本地 ffmpeg/ffprobe 路径（项目自带，无需系统 PATH）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_FFMPEG_BIN = str(_PROJECT_ROOT / "bin" / "ffmpeg.exe")
_FFPROBE_BIN = str(_PROJECT_ROOT / "bin" / "ffprobe.exe")


def _model_complete(model_dir: Path) -> bool:
    """检查模型是否完整（model.bin存在且>10MB，且词表 tokenizer.json 存在）

    历史坑：只查 model.bin 会导致「仅下载了 model.bin、漏了 tokenizer.json」的
    残缺模型被误判为可用，加载时才报
    'Cannot load the vocabulary from the model directory'。
    这里把词表也纳入检查，残缺模型会正确降级而非静默选坏。
    """
    mb = model_dir / 'model.bin'
    tok = model_dir / 'tokenizer.json'
    return (
        model_dir.exists()
        and mb.exists() and mb.stat().st_size > 10_000_000
        and tok.exists()
    )


def _select_model(model_size: str, platform: str = '') -> Tuple[str, str, bool]:
    """
    选择可用的Whisper模型，自动降级
    返回：(model_path, actual_model_size, is_local_only)
    """
    local_dir = LOCAL_MODEL_DIRS.get(model_size)
    if local_dir and _model_complete(local_dir):
        return str(local_dir), model_size, True

    # 本地没有 → 自动降级
    if model_size == 'small':
        fallback_order = ['base', 'tiny']
    else:
        fallback_order = ['base', 'tiny', 'medium']

    for fb in fallback_order:
        if fb != model_size and _model_complete(LOCAL_MODEL_DIRS.get(fb, Path('.'))):
            logger.warning(f'模型 {model_size} 未就绪，自动降级为 {fb}')
            return str(LOCAL_MODEL_DIRS[fb]), fb, True

    available = [k for k, v in LOCAL_MODEL_DIRS.items() if _model_complete(v)]
    raise Exception(
        f'模型 {model_size} 尚未下载到本地，且无可用降级模型。'
        f'本地可用模型: {", ".join(available) if available else "无"}'
    )


def _format_srt_time(seconds: float) -> str:
    """格式化SRT时间戳"""
    if seconds is None:
        return "00:00:00,000"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_mmss(seconds: float) -> str:
    """格式化 MM:SS"""
    if seconds is None:
        return "00:00"
    total_s = int(seconds + 0.5)
    return f"{total_s // 60:02d}:{total_s % 60:02d}"


def _format_duration(seconds: float) -> str:
    """格式化时长为人性化字符串"""
    if seconds is None:
        return ""
    s = max(0, int(seconds + 0.5))
    if s < 60:
        return f"{s}秒"
    elif s < 3600:
        return f"{s//60}分{s%60}秒"
    else:
        h = s // 3600
        m = (s % 3600) // 60
        sec = s % 60
        return f"{h}小时{m}分{sec}秒"


def _is_cuda_runtime_missing(e: Exception) -> bool:
    """判断是否为 CUDA 运行时 DLL 缺失类错误。

    这类错误在模型「加载」阶段不暴露（CT2 初始化能通过），
    直到真正做矩阵运算（model.encode）调用 cublas/cudart 时才崩。
    典型表现：'Library cublas64_12.dll is not found or cannot be loaded'。
    捕获后自动降级 CPU 重跑，避免整任务失败。
    """
    s = str(e).lower()
    return ('cublas' in s or 'cudart' in s or 'cuda' in s) and (
        'not found' in s or 'cannot be loaded' in s or '.dll' in s
    )


def _cuda_runtime_usable() -> bool:
    """Windows 下探测 CUDA 运行时 DLL 是否真的能加载。

    ctranslate2.get_cuda_device_count() 在有 GPU 但缺 cublas/cudnn 时
    仍会报告可用，直到矩阵运算才崩（本机实测 cublas64_12.dll 缺失）。
    提前探测可以直接一开始就走 CPU，避免先白跑一段推理再降级。
    非 Windows 交给 ctranslate2 自身探测。
    """
    if os.name != 'nt':
        return True
    import ctypes

    def _find(prefixes):
        for p in prefixes:
            try:
                ctypes.WinDLL(p)
                return True
            except OSError:
                continue
        return False

    cublas = _find(('cublas64_12.dll', 'cublas64_11.dll'))
    cudnn = _find(('cudnn64_8.dll', 'cudnn64_9.dll', 'cudnn64_7.dll'))
    return bool(cublas and cudnn)


def _resolve_device(requested: str = 'auto', progress_callback=None) -> Tuple[str, str, Optional[str]]:
    """按用户选择解析 Whisper 运行设备（单点决策，单段/分片共用）。

    返回 (device, compute_type, fallback_reason)
    requested: 'auto' | 'cuda' | 'cpu'（前端「转录设备」选项，非法值按 auto）
    - 'cpu'：跳过 CUDA 探测直接 CPU（打包 CPU-only 版本时无探测开销）
    - 'cuda'：探测 CUDA 可用性；不可用则回退 CPU 并给可见原因，流程不中断
    - 'auto'：原自动探测行为（有 GPU 且运行时 DLL 齐全才用 GPU）
    WHISPER_FORCE_CPU 环境变量仍是最高优先级（历史逃生门）。
    """
    if os.environ.get('WHISPER_FORCE_CPU', '').lower() in ('1', 'true', 'yes'):
        return 'cpu', 'int8', '环境变量 WHISPER_FORCE_CPU 强制 CPU'

    requested = requested or 'auto'
    if requested not in ('auto', 'cuda', 'cpu'):
        logger.warning(f'未知转录设备选择 {requested!r}，按自动处理')
        requested = 'auto'

    if requested == 'cpu':
        return 'cpu', 'int8', None

    try:
        import ctranslate2
        cuda_usable = ctranslate2.get_cuda_device_count() > 0 and _cuda_runtime_usable()
    except Exception:
        cuda_usable = False

    if requested == 'cuda':
        if cuda_usable:
            return 'cuda', 'int8_float16', None
        reason = 'CUDA 不可用（无 GPU 或缺 cublas/cudnn 运行时 DLL），已自动回退 CPU'
        logger.warning(reason)
        if progress_callback:
            progress_callback(5, f'⚠ {reason}')
        return 'cpu', 'int8', reason

    # auto：与原自动探测行为一致
    return ('cuda', 'int8_float16', None) if cuda_usable else ('cpu', 'int8', None)


def _transcribe_with_model(model, audio_path, language, progress_callback, speaker_diarization,
                           cancel_check=None):
    """用已加载的模型完成转录并写入 SRT/TXT，返回 (srt_path, txt_path, seg_count)。

    从 _transcribe_sync 抽出，便于 GPU→CPU 降级时整段重跑。
    cancel_check: 可选可调用对象，返回 True 即抛 TranscriptionCancelledError 中止。
    """
    # ★ 阶段3：开始转录
    if progress_callback:
        progress_callback(8, f'模型已加载，开始转录（语言: {language or "自动检测"}）')
    logger.info(f'开始转录: {Path(audio_path).name}, 语言: {language or "自动检测"}')
    segments_gen, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=5,
        vad_filter=True,
    )

    total_duration = info.duration
    detected_lang = info.language
    lang_prob = info.language_probability
    logger.info(f'检测语言: {detected_lang} (置信度: {lang_prob:.2f})')
    logger.info(f'总时长: {_format_duration(total_duration)}')

    if progress_callback:
        progress_callback(10, f'开始转录... 语言: {detected_lang} 时长: {_format_duration(total_duration)}')

    # 写入SRT和TXT
    srt_path = str(Path(audio_path).with_suffix('.srt'))
    txt_path = str(Path(audio_path).with_suffix('.txt'))

    segments = []
    transcribe_start = time.time()
    last_emit_pct = 0
    last_emit_time = 0
    current_speaker = 0
    last_seg_end = 0

    with open(srt_path, 'w', encoding='utf-8') as f_srt, \
         open(txt_path, 'w', encoding='utf-8') as f_txt:

        for i, seg in enumerate(segments_gen):
            if cancel_check and cancel_check():
                raise TranscriptionCancelledError('用户取消，转录中止')
            segments.append(seg)

            # 说话人分离
            speaker_label = ""
            if speaker_diarization:
                gap = seg.start - last_seg_end if last_seg_end > 0 else 0
                if gap > 1.5 and i > 0:
                    current_speaker = (current_speaker % 2) + 1
                if current_speaker == 0:
                    current_speaker = 1
                speaker_label = f" [说话人{current_speaker}]"
                last_seg_end = seg.end

            # 写入文件
            start_str = _format_srt_time(seg.start)
            end_str = _format_srt_time(seg.end)
            f_srt.write(f"{i+1}\n{start_str} --> {end_str}\n{seg.text}\n\n")
            f_txt.write(f"[{_format_mmss(seg.start)} - {_format_mmss(seg.end)}] {seg.text}{speaker_label}\n")

            # 计算进度
            if total_duration > 0:
                time_pct = min(seg.end / total_duration * 100, 99)
                progress_pct = int(10 + time_pct * 0.88)
            else:
                progress_pct = 0

            # 控制发送频率
            now = time.time()
            should_emit = (
                (i + 1) % 5 == 0
                or abs(progress_pct - last_emit_pct) >= 2
                or (now - last_emit_time) > 1.5
            )

            if should_emit or i == 0:
                elapsed = time.time() - transcribe_start
                # 实时倍率：已处理音频秒数 / 墙钟秒数（>1 表示快于实时）
                speed_x = (seg.end / elapsed) if elapsed > 0.5 and seg.end > 0 else 0.0
                eta = None
                if time_pct > 0.5 and elapsed > 2:
                    eta = _format_duration(elapsed / time_pct * (100 - time_pct))

                msg = (
                    f'转录中 {progress_pct}% | '
                    f'{_format_mmss(seg.end)} / {_format_mmss(total_duration)} | '
                    f'第 {i+1} 段 | '
                    f'已用 {_format_duration(elapsed)}'
                )
                if speed_x > 0:
                    msg += f' | 速度 {speed_x:.1f}x'
                if eta:
                    msg += f' | 预计剩余 {eta}'
                if progress_callback:
                    progress_callback(progress_pct, msg)

                last_emit_time = now
                last_emit_pct = progress_pct

    total = len(segments)
    elapsed = time.time() - transcribe_start
    speed_x = (total_duration / elapsed) if elapsed > 0 and total_duration > 0 else 0.0
    logger.info(f'转录完成: {total} 段, 耗时 {_format_duration(elapsed)}')
    logger.info(f'SRT: {Path(srt_path).name}')
    logger.info(f'TXT: {Path(txt_path).name}')

    if progress_callback:
        done_msg = f'转录完成，共 {total} 段 | 音频 {_format_duration(total_duration)} | 耗时 {_format_duration(elapsed)}'
        if speed_x > 0:
            done_msg += f' | 均速 {speed_x:.1f}x'
        progress_callback(99, done_msg + '，正在保存文件...')
        progress_callback(100, '转录完成！')

    return srt_path, txt_path, total


# ============ 长音频分片支持 ============

def _get_audio_duration_seconds(audio_path: str) -> Optional[float]:
    """用 ffprobe 获取音频时长（秒），失败返回 None"""
    ffprobe = shutil.which('ffprobe') or _FFPROBE_BIN
    try:
        result = subprocess.run(
            [ffprobe, '-v', 'quiet', '-show_entries', 'format=duration',
             '-of', 'csv=p=0', audio_path],
            capture_output=True, text=True, timeout=15,
        )
        return float(result.stdout.strip())
    except Exception:
        logger.warning(f'ffprobe 获取音频时长失败，跳过自动分片')
        return None


def _split_audio_chunks(audio_path: str, chunk_seconds: int = 300) -> list:
    """将音频切分为等长 WAV 片段

    返回 [(chunk_path, offset_seconds), ...]。
    如果时长 <= chunk_seconds 或 ffmpeg 不可用，返回 [(audio_path, 0.0)]。
    """
    ffmpeg = shutil.which('ffmpeg') or _FFMPEG_BIN
    duration = _get_audio_duration_seconds(audio_path)
    if not duration or duration <= chunk_seconds:
        return [(audio_path, 0.0)]

    base_dir = Path(audio_path).parent
    stem = Path(audio_path).stem
    chunks = []
    chunk_left = float(duration)
    for i, start_sec in enumerate(range(0, int(duration), chunk_seconds)):
        actual_t = min(chunk_seconds, chunk_left)
        chunk_left -= actual_t
        chunk_path = base_dir / f"{stem}_chunk{i:03d}.wav"
        cmd = [
            ffmpeg, '-y', '-v', 'quiet',
            '-i', audio_path,
            '-ss', str(start_sec), '-t', str(actual_t),
            '-acodec', 'pcm_s16le',
            str(chunk_path),
        ]
        subprocess.run(cmd, capture_output=True, check=False, timeout=120)
        if chunk_path.exists() and chunk_path.stat().st_size > 0:
            chunks.append((str(chunk_path), float(start_sec)))
            logger.info(f'音频分片 [{i}] 偏移 {start_sec:.0f}s → {chunk_path.name} ({chunk_path.stat().st_size} bytes)')
    if not chunks:
        logger.warning('音频分片全部失败，回退单次转录')
        return [(audio_path, 0.0)]
    return chunks


def _transcribe_chunked(
    audio_path: str,
    model_size: str,
    platform: str,
    speaker_diarization: bool,
    language: Optional[str],
    progress_callback,
    cancel_check=None,
    device: str = 'auto',
) -> Tuple[str, str, int]:
    """长音频分片转录：切分 → 逐段转录（模型只加载一次）→ 时间戳合并

    返回：(srt_path, txt_path, total_segments)
    """
    CHUNK_SECONDS = 300  # 每段 5 分钟
    chunks = _split_audio_chunks(audio_path, CHUNK_SECONDS)
    total_chunks = len(chunks)
    if total_chunks <= 1:
        # 切分失败或音频本身 ≤5 分钟 → 走正常单次流程
        return _transcribe_sync_single(
            audio_path, model_size, platform, speaker_diarization, language, progress_callback,
            cancel_check=cancel_check, device=device,
        )

    logger.info(f'长音频分片转录启动: {total_chunks} 段, 音频: {Path(audio_path).name}')

    # ── 模型选择（一次） ──
    model_path, actual_size, local_only = _select_model(model_size, platform)
    if actual_size == 'tiny' and '哔哩' in platform:
        base_dir = LOCAL_MODEL_DIRS.get('base')
        if base_dir and _model_complete(base_dir):
            model_path = str(base_dir)
            actual_size = 'base'
            local_only = True

    # ── 模型加载（一次） ──
    resolved_device, compute_type, _fallback_reason = _resolve_device(device, progress_callback)
    use_cuda = (resolved_device == 'cuda')

    device = 'cuda' if use_cuda else 'cpu'
    compute_type = 'int8_float16' if use_cuda else 'int8'
    if progress_callback:
        progress_callback(
            5,
            f'加载模型 {actual_size} | 设备: {device} | 计算: {compute_type} | '
            f'请等待（大模型首次加载可能数十秒）...'
        )
    try:
        model = _get_whisper_model(
            model_path, device, compute_type,
            local_only=local_only, cpu_threads=4 if not use_cuda else 1, num_workers=1,
        )
    except ImportError:
        raise Exception('faster-whisper 未安装')
    except Exception as e:
        if use_cuda:
            logger.warning(f'GPU 加载失败 ({e})，降级到 CPU')
            device = 'cpu'
            compute_type = 'int8'
            model = _get_whisper_model(
                model_path, device, compute_type,
                local_only=local_only, cpu_threads=4, num_workers=1,
            )
        else:
            raise Exception(f'模型加载失败: {e}')

    # ── 逐段转录 ──
    all_srt_entries = []
    all_txt_lines = []
    segment_idx = 0
    transcribe_start = time.time()

    # 估算总音频时长（末片偏移 + 片长），用于整体进度与 ETA
    last_offset = chunks[-1][1] if chunks else 0.0
    estimated_total = last_offset + CHUNK_SECONDS
    try:
        probed = _get_audio_duration_seconds(audio_path)
        if probed and probed > 0:
            estimated_total = probed
    except Exception:
        pass

    for ci, (chunk_path, offset) in enumerate(chunks):
        if cancel_check and cancel_check():
            raise TranscriptionCancelledError('用户取消，转录中止')
        chunk_base_pct = int(10 + ci / total_chunks * 85)
        if progress_callback:
            elapsed0 = time.time() - transcribe_start
            progress_callback(
                chunk_base_pct,
                f'分片转录 {ci+1}/{total_chunks} | 偏移 {_format_mmss(offset)} | '
                f'设备 {device} | 已用 {_format_duration(elapsed0)} | 开始本片...'
            )
        logger.info(f'转录分段 [{ci+1}/{total_chunks}] 偏移 {offset:.0f}s')

        try:
            segs, info = model.transcribe(chunk_path, language=language, beam_size=5, vad_filter=True)
            # ★ 物化生成器：faster-whisper 是惰性编码，CUDA 运行时缺失类错误
            # （如 cublas64_12.dll 找不到）在迭代时才抛出；必须把迭代收进 try
            # 才能触发 GPU→CPU 降级，否则分片任务会整段失败。
            chunk_segments = list(segs)
        except RuntimeError as e:
            if use_cuda and _is_cuda_runtime_missing(e):
                logger.warning('GPU 推理失败，降级 CPU')
                device = 'cpu'
                compute_type = 'int8'
                model = _get_whisper_model(
                    model_path, device, compute_type,
                    local_only=local_only, cpu_threads=4, num_workers=1,
                )
                segs, info = model.transcribe(chunk_path, language=language, beam_size=5, vad_filter=True)
                chunk_segments = list(segs)
            else:
                raise

        current_speaker = 0
        last_seg_end = 0
        last_emit_time = 0.0
        for seg in chunk_segments:
            if cancel_check and cancel_check():
                raise TranscriptionCancelledError('用户取消，转录中止')
            seg_start = seg.start + offset
            seg_end_val = seg.end + offset
            segment_idx += 1

            speaker_label = ""
            if speaker_diarization:
                gap = seg_start - last_seg_end if last_seg_end > 0 else 0
                if gap > 1.5 and segment_idx > 1:
                    current_speaker = (current_speaker % 2) + 1
                if current_speaker == 0:
                    current_speaker = 1
                speaker_label = f" [说话人{current_speaker}]"
                last_seg_end = seg_end_val

            all_srt_entries.append(
                f"{segment_idx}\n{_format_srt_time(seg_start)} --> {_format_srt_time(seg_end_val)}\n{seg.text}\n"
            )
            all_txt_lines.append(
                f"[{_format_mmss(seg_start)} - {_format_mmss(seg_end_val)}] {seg.text}{speaker_label}\n"
            )

            # 片内实时进度（足量信息：进度/位置/速度/ETA）
            now = time.time()
            if segment_idx == 1 or (now - last_emit_time) >= 1.2 or segment_idx % 8 == 0:
                elapsed = now - transcribe_start
                if estimated_total > 0:
                    time_pct = min(seg_end_val / estimated_total * 100, 99)
                    progress_pct = int(10 + time_pct * 0.88)
                else:
                    time_pct = 0
                    progress_pct = chunk_base_pct
                speed_x = (seg_end_val / elapsed) if elapsed > 0.5 and seg_end_val > 0 else 0.0
                eta = None
                if time_pct > 0.5 and elapsed > 2:
                    eta = _format_duration(elapsed / time_pct * (100 - time_pct))
                msg = (
                    f'分片转录 {ci+1}/{total_chunks} | {progress_pct}% | '
                    f'{_format_mmss(seg_end_val)} / {_format_mmss(estimated_total)} | '
                    f'第 {segment_idx} 段 | 已用 {_format_duration(elapsed)}'
                )
                if speed_x > 0:
                    msg += f' | 速度 {speed_x:.1f}x'
                if eta:
                    msg += f' | 预计剩余 {eta}'
                if progress_callback:
                    progress_callback(progress_pct, msg)
                last_emit_time = now

        # 清理临时 chunk 文件
        if chunk_path != audio_path:
            try:
                Path(chunk_path).unlink(missing_ok=True)
            except Exception:
                pass

    # ── 写入合并结果 ──
    srt_path = str(Path(audio_path).with_suffix('.srt'))
    txt_path = str(Path(audio_path).with_suffix('.txt'))
    with open(srt_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(all_srt_entries))
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.writelines(all_txt_lines)

    total = segment_idx
    elapsed = time.time() - transcribe_start
    speed_x = (estimated_total / elapsed) if elapsed > 0 and estimated_total > 0 else 0.0
    logger.info(f'分片转录完成: {total} 段, {total_chunks} 段音频, 耗时 {_format_duration(elapsed)}')
    if progress_callback:
        done_msg = f'转录完成，共 {total} 段 | 耗时 {_format_duration(elapsed)}'
        if speed_x > 0:
            done_msg += f' | 均速 {speed_x:.1f}x'
        progress_callback(99, done_msg)
        progress_callback(100, '转录完成！')
    return srt_path, txt_path, total


# 纯净文本行清洗：剥掉行首 [MM:SS - MM:SS] 前缀与行内 [说话人X] 标签
_CLEAN_TS_RE = re.compile(r'^\s*\[\d{1,2}:\d{2}(?::\d{2})?\s*(?:-\s*\d{1,2}:\d{2}(?::\d{2})?)?\]\s*')
_CLEAN_SPEAKER_RE = re.compile(r'\s*\[(?:说话人|Speaker| speaker)\s*\w+\]', re.IGNORECASE)


def write_clean_text(txt_path: str) -> Optional[str]:
    """从转录 txt 生成纯净阅读版（无时间戳/说话人标记），返回 clean 文件路径。

    行格式约定见本模块写盘处：`[MM:SS - MM:SS] 文本[说话人X]`。空行丢弃、行序保留；
    校对开启时应在校正写回后调用，使纯净版反映最终文本。读不到或无有效内容返回 None。
    """
    src = Path(txt_path)
    if not src.exists():
        return None
    out_lines = []
    for line in src.read_text(encoding='utf-8').splitlines():
        t = _CLEAN_TS_RE.sub('', line)
        t = _CLEAN_SPEAKER_RE.sub('', t).strip()
        if t:
            out_lines.append(t)
    if not out_lines:
        return None
    clean_path = src.with_suffix('.clean.txt')
    clean_path.write_text('\n'.join(out_lines) + '\n', encoding='utf-8')
    return str(clean_path)


# 引擎回退链（路由递归的静态视图）：用户选的引擎失败后按序降级，链尾 Whisper 兜底。
# - 'local'（Whisper）语义不变：历史 job options 已存 'local'
# - 'cloud'=MiMo（付费主力），'cloud_free'=XingChen Ultra（免费档，简单视频）
# - cloud（付费）失败先落 cloud_free（免费）：云端之间互为梯队且成本只降不升；
#   反向不成立——用户选免费档失败绝不转付费引擎
# - firered 只在模型就绪时参与回退（兜底绝不触发 1.1GB 下载，见 _fallback_chain）
_FALLBACK_CHAINS = {
    'cloud': ('cloud', 'cloud_free', 'firered', 'local'),
    'cloud_free': ('cloud_free', 'firered', 'local'),
    'firered': ('firered', 'local'),
    'local': ('local',),
}

_ENGINE_LABELS = {
    'cloud': '云端 MiMo',
    'cloud_free': '云端 XingChen（免费）',
    'firered': '本地 FireRedASR',
    'local': '本地 Whisper',
}


def _fallback_chain(engine: str) -> Tuple[str, ...]:
    """用户选择的引擎 → 实际回退链。firered 模型未就绪时从链中剔除（下载只发生在
    用户显式选 firered 的首次使用，不作为兜底的隐性副作用）。"""
    chain = _FALLBACK_CHAINS.get(engine, _FALLBACK_CHAINS['cloud'])
    if engine != 'firered':
        from services.local_asr import firered_model_ready
        chain = tuple(e for e in chain if e != 'firered' or firered_model_ready())
    return chain


def _transcribe_by_engine(
    engine: str,
    audio_path: str,
    model_size: str,
    platform: str,
    speaker_diarization: bool,
    language: Optional[str],
    progress_callback,
    cancel_check,
    device: str,
) -> Tuple[str, str, int]:
    """单引擎尝试（不处理回退）。未知引擎一律按 cloud。"""
    if engine == 'firered':
        # 惰性导入避免循环依赖（local_asr → cloud_asr → 本模块）
        from services import local_asr
        return local_asr.transcribe_audio_firered(
            audio_path,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
            language=language,
            speaker_diarization=speaker_diarization,
        )
    if engine == 'local':
        duration = _get_audio_duration_seconds(audio_path)
        if duration and duration > 1200:
            logger.info(f'检测到长音频 {_format_duration(duration)}，启用分片转录')
            if progress_callback:
                progress_callback(1, f'检测到长音频（{_format_duration(duration)}），启用分片转录（每段5分钟）...')
            return _transcribe_chunked(
                audio_path, model_size, platform, speaker_diarization, language, progress_callback,
                cancel_check=cancel_check, device=device,
            )
        return _transcribe_sync_single(
            audio_path, model_size, platform, speaker_diarization, language, progress_callback,
            cancel_check=cancel_check, device=device,
        )
    # cloud / cloud_free：未配置视为该引擎失败，交给回退链
    from services import cloud_asr
    provider = 'xingchen' if engine == 'cloud_free' else 'mimo'
    if not cloud_asr.cloud_asr_configured(provider):
        raise Exception('云端 ASR 未配置（config.py 缺 ASR_CLOUD_* 配置）')
    return cloud_asr.transcribe_audio_cloud(
        audio_path,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
        language=language,
        speaker_diarization=speaker_diarization,
        provider=provider,
    )


def _transcribe_sync(
    audio_path: str,
    model_size: str = DEFAULT_WHISPER_MODEL,
    platform: str = '',
    speaker_diarization: bool = False,
    language: Optional[str] = None,
    progress_callback=None,
    cancel_check=None,
    device: str = 'auto',
    engine: str = 'cloud',
) -> Tuple[str, str, int]:
    """同步转录入口（引擎路由递归回退 + Whisper 长音频分片）

    engine: 'cloud' | 'cloud_free' | 'firered' | 'local'（非法值按 cloud）
    回退链：cloud → cloud_free → firered(模型就绪时) → local；cloud_free → firered → local；
    firered → local；local 无回退。
    每次回退原因经 progress_callback 可见；用户取消在任何一环都直接上抛不回退。
    """
    chain = _fallback_chain(engine)
    for i, eng in enumerate(chain):
        try:
            return _transcribe_by_engine(
                eng, audio_path,
                model_size=model_size, platform=platform,
                speaker_diarization=speaker_diarization, language=language,
                progress_callback=progress_callback, cancel_check=cancel_check,
                device=device,
            )
        except TranscriptionCancelledError:
            raise  # 用户主动取消不是故障，不回退
        except Exception as e:
            label = _ENGINE_LABELS.get(eng, eng)
            remaining = chain[i + 1:]
            if not remaining:
                raise  # 链尾（Whisper）失败：无路可退，上抛给调用方
            reason = f'{label} 转录失败（{type(e).__name__}: {e}），自动回退 {_ENGINE_LABELS[remaining[0]]}'
            logger.warning(reason)
            if progress_callback:
                progress_callback(2, f'⚠ {reason}')


def _transcribe_sync_single(
    audio_path: str,
    model_size: str = DEFAULT_WHISPER_MODEL,
    platform: str = '',
    speaker_diarization: bool = False,
    language: Optional[str] = None,
    progress_callback=None,
    cancel_check=None,
    device: str = 'auto',
) -> Tuple[str, str, int]:
    """单段音频转录（原有逻辑，供分片流程复用）
    返回：(srt_path, txt_path, segment_count)
    """
    # ★ 阶段1：选择模型
    if progress_callback:
        progress_callback(2, f'选择 Whisper 模型（请求: {model_size}，平台: {platform or "通用"}）...')
    # 选择模型
    model_path, actual_size, local_only = _select_model(model_size, platform)
    logger.info(f'使用模型: {actual_size} (路径: {model_path})')

    # B站中文平台: tiny自动升级到base
    if actual_size == 'tiny' and '哔哩' in platform:
        base_dir = LOCAL_MODEL_DIRS.get('base')
        if base_dir and _model_complete(base_dir):
            logger.info('tiny模型中文转录质量差，自动切换为base')
            model_path = str(base_dir)
            actual_size = 'base'
            local_only = True

    # ★ 阶段2：加载模型（按用户选择解析设备，默认自动探测 CUDA）
    # 自动检测 CUDA，有GPU就用GPU（快3-5倍），没有就CPU；
    # 用户显式选 CPU 时跳过探测，选 CUDA 不可用则回退 CPU（见 _resolve_device）
    resolved_device, compute_type, _fallback_reason = _resolve_device(device, progress_callback)
    use_cuda = (resolved_device == 'cuda')

    device = 'cuda' if use_cuda else 'cpu'
    compute_type = 'int8_float16' if use_cuda else 'int8'
    device_label = f'GPU (CUDA, {compute_type})' if use_cuda else f'CPU ({compute_type})'

    if progress_callback:
        progress_callback(
            5,
            f'加载 Whisper 模型: {actual_size} | 设备: {device_label} | '
            f'计算: {compute_type} | 路径: {Path(model_path).name} | 请等待（大模型首次加载可能数十秒）...'
        )

    logger.info(f'加载模型: {actual_size} | device={device}, compute_type={compute_type}')
    try:
        model = _get_whisper_model(
            model_path, device, compute_type,
            local_only=local_only, cpu_threads=4 if not use_cuda else 1, num_workers=1,
        )
    except ImportError:
        raise Exception('faster-whisper未安装，请运行: pip install faster-whisper')
    except Exception as e:
        # CUDA 加载失败时自动降级到 CPU
        if use_cuda:
            logger.warning(f'GPU加载失败 ({e})，降级到CPU')
            if progress_callback:
                progress_callback(5, f'GPU加载失败，降级到CPU: {actual_size}（稍候...）')
            device = 'cpu'
            compute_type = 'int8'
            model = _get_whisper_model(
                model_path, device, compute_type,
                local_only=local_only, cpu_threads=4, num_workers=1,
            )
        else:
            raise Exception(f'模型加载失败: {e}')

    # ★ 阶段3：开始转录（GPU推理若缺CUDA运行时DLL，自动降级CPU重跑）
    try:
        srt_path, txt_path, total = _transcribe_with_model(
            model, audio_path, language, progress_callback, speaker_diarization,
            cancel_check=cancel_check,
        )
    except RuntimeError as e:
        # 加载期不暴露、推理期才崩的 CUDA 运行时缺失（如 cublas64_12.dll 找不到）
        if use_cuda and _is_cuda_runtime_missing(e):
            logger.warning(f'GPU推理失败 ({e})，降级到CPU重跑')
            if progress_callback:
                progress_callback(8, '⚠ GPU推理失败（缺CUDA运行时），降级到CPU重跑...')
            device = 'cpu'
            compute_type = 'int8'
            model = _get_whisper_model(
                model_path, device, compute_type,
                local_only=local_only, cpu_threads=4, num_workers=1,
            )
            srt_path, txt_path, total = _transcribe_with_model(
                model, audio_path, language, progress_callback, speaker_diarization,
                cancel_check=cancel_check,
            )
        else:
            raise
    return srt_path, txt_path, total


def transcribe_audio_sample(audio_path: str, max_seconds: float = 90.0, language: Optional[str] = None,
                            initial_prompt: Optional[str] = None) -> str:
    """转写音频开头片段（用于字幕内容抽查校验），返回拼接后的纯文本。

    固定走 CPU int8 + 最轻的可用模型：抽查只关心“开头说了什么”，
    不依赖本机 CUDA 环境（避免 cublas 缺失把校验本身搞挂）。
    language 必须与字幕语言一致（中文必须传 'zh'）：base 模型语言自动检测
    会把清晰中文幻觉成英文（“什么是经方”→"What is the old?"），
    导致相似度恒≈0 误杀所有中文字幕（见 2026-09-08 复盘）。
    失败抛异常，由调用方决定是否保留原字幕。
    """
    src = Path(audio_path)
    sample_wav = src.with_name(f'{src.stem}_sample.wav')
    ffmpeg = shutil.which('ffmpeg') or _FFMPEG_BIN
    cmd = [
        ffmpeg, '-y', '-v', 'quiet',
        '-i', str(src),
        '-t', str(max_seconds),
        '-acodec', 'pcm_s16le',
        str(sample_wav),
    ]
    result = subprocess.run(cmd, capture_output=True, check=False, timeout=120)
    if not sample_wav.exists() or sample_wav.stat().st_size <= 0:
        raise Exception(f'ffmpeg 截取音频开头失败（returncode={result.returncode}）')
    try:
        model_path, _actual_size, local_only = _select_model('base')
        model = _get_whisper_model(
            model_path, 'cpu', 'int8',
            local_only=local_only, cpu_threads=4, num_workers=1,
        )
        kwargs = {}
        if initial_prompt:
            # 弹简体中文输出：Whisper 中文默认出繁体，与B站简体字幕比对时
            # 会多损失一段相似度；给一句简中提示词同时锁定语言与字形
            kwargs['initial_prompt'] = initial_prompt
        segs, _info = model.transcribe(
            str(sample_wav), language=language, beam_size=1, vad_filter=False, **kwargs
        )
        parts = [seg.text.strip() for seg in segs]
        return ' '.join(p for p in parts if p)
    finally:
        try:
            sample_wav.unlink(missing_ok=True)
        except OSError:
            pass


async def transcribe_audio(
    audio_path: str,
    model_size: str = DEFAULT_WHISPER_MODEL,
    platform: str = '',
    speaker_diarization: bool = False,
    language: Optional[str] = None,
    sio=None,
    job_id: str = '',
    cancel_check=None,
    device: str = 'auto',
    engine: str = 'cloud',
) -> Tuple[str, str, int]:
    """
    异步转录音频（在线程池中运行同步引擎，云端/本地由 engine 路由）
    使用 asyncio.Queue 跨线程桥接进度回调
    返回：(srt_path, txt_path, segment_count)
    """
    if sio:
        from socket_handlers import emit_progress, emit_log
        # engine 只有 cloud/firered/local 三态；非法值按 cloud 展示
        engine_label = {'local': '本地 Whisper', 'firered': '本地 FireRedASR'}.get(engine, '云端 MiMo')
        await emit_progress(sio, job_id, 'transcribe', 0, f'正在初始化转录（引擎: {engine_label}, 模型: {model_size}, 语言: {language or "自动检测"}）...')
        await emit_log(sio, job_id, f'🎬 开始转录 | 音频: {Path(audio_path).name} | 引擎: {engine_label} | 模型: {model_size} | 语言: {language or "自动检测"}')

    # 跨线程进度桥接：线程 → Queue → async emit
    # 队列容量放大，避免密集进度回调丢消息
    progress_queue: asyncio.Queue = asyncio.Queue(maxsize=256)

    def progress_callback(pct, msg):
        """同步回调 → 将进度推入asyncio.Queue（满时丢弃最旧再写）"""
        try:
            progress_queue.put_nowait((pct, msg))
        except asyncio.QueueFull:
            try:
                progress_queue.get_nowait()
            except Exception:
                pass
            try:
                progress_queue.put_nowait((pct, msg))
            except Exception:
                pass
        except Exception:
            pass

    # 启动异步进度读取器：Timeout 只 continue，绝不能退出（模型加载常 >0.5s）
    # 工作台日志即进度条：足量推送进度到 emit_log（节流防刷屏，但不抽稀到失真）
    async def _progress_reader():
        last_log_msg = ''
        last_log_time = 0.0
        last_log_pct = -1
        try:
            while True:
                try:
                    item = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    # 模型加载/静默阶段：保持等待，不退出
                    continue
                if item is None:
                    break  # 哨兵：转录线程结束
                pct, msg = item
                if not (sio and job_id):
                    continue
                from socket_handlers import emit_progress, emit_log
                await emit_progress(sio, job_id, 'transcribe', pct, msg)

                now = time.monotonic()
                # 日志策略（日志=进度条）：
                # - 加载/完成阶段：每条都记
                # - 进行中：消息变化 且 (间隔≥1s 或 进度前进≥2%) 就记，保证速度/ETA 实时可见
                msg_changed = msg != last_log_msg
                pct_jump = abs(pct - last_log_pct) >= 2
                time_ok = (now - last_log_time) >= 1.0
                force = pct <= 10 or pct >= 99 or pct == 0
                if msg_changed and (force or time_ok or pct_jump):
                    await emit_log(sio, job_id, f'📊 {msg}')
                    last_log_msg = msg
                    last_log_time = now
                    last_log_pct = pct
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('转录进度读取器异常')

    reader_task = asyncio.create_task(_progress_reader())

    try:
        # 在线程池中运行同步转录
        result = await asyncio.to_thread(
            _transcribe_sync,
            audio_path=audio_path,
            model_size=model_size,
            platform=platform,
            speaker_diarization=speaker_diarization,
            language=language,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
            device=device,
            engine=engine,
        )
    finally:
        # 通知 reader 排空后退出；超时则 cancel 兜底
        try:
            progress_queue.put_nowait(None)
        except Exception:
            pass
        try:
            await asyncio.wait_for(reader_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass

    if sio:
        from socket_handlers import emit_progress, emit_log
        await emit_progress(sio, job_id, 'transcribe', 100, '转录完成！')
        await emit_log(sio, job_id, f'✅ 转录完成: 共 {result[2]} 段')
        await emit_log(sio, job_id, f'📄 SRT: {Path(result[0]).name}')
        await emit_log(sio, job_id, f'📄 TXT: {Path(result[1]).name}')

    return result
