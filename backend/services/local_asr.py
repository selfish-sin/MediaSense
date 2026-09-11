"""
本地 FireRedASR2-AED 转录服务（sherpa-onnx int8 / onnxruntime，CPU，不依赖 torch）

小红书 FireRedASR2-AED 的 ONNX int8 版（AISHELL-1 CER 0.57%，比云端 SenseVoice
~3.0% 更准；实测中文长音频 RTF 0.271 ≈ 3.7x 实时，峰值内存 ~1.7GB）。
本地无时间戳语义与云端相同：silero VAD 给出段级边界（精确到样本），段内文本
复用 cloud_asr.split_sentences/distribute_times 句级化。

契约与云端/Whisper 引擎一致：返回 (srt_path, txt_path, seg_count)。

错误语义（禁止静默吞错）：
- 模型缺失 → 按需下载（进度可见）；下载/解包失败上抛，错误文案含手动下载指引
- sherpa-onnx 未安装 → 上抛可行动错误
- 识别/标点异常 → 上抛，由 transcriber 路由回退本地 Whisper（原因可见）
- 用户取消 → TranscriptionCancelledError（不回退）

并发内存：实测峰值 ~1.7GB，MAX_CONCURRENT_JOBS=2 下两个 firered 任务并发会
翻倍——模块级 _FIRERED_LOCK 串行化本引擎（Whisper 不受影响）。

sherpa-onnx API 坑点（实测踩出来的，勿"顺手重构"）：
- VAD 必须 VadModel.create(config)，直接 VadModel(...) 报 "No constructor defined"
- vad.window_size 是【方法】不是属性，写 vad.window_size 会报 unsupported operand
- OfflinePunctuation 没有 from_* 工厂，直接 OfflinePunctuation(cfg)
"""
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import httpx
import numpy as np

from config import (
    FIRERED_MODEL_DIR,
    FIRERED_PUNCT_MODEL_DIR,
    FIRERED_VAD_MODEL_PATH,
)
from services.cloud_asr import (
    _ffmpeg_bin,
    distribute_times,
    split_sentences,
)
from services.transcriber import (
    TranscriptionCancelledError,
    _format_duration,
    _format_mmss,
    _format_srt_time,
    _get_audio_duration_seconds,
)

logger = logging.getLogger(__name__)

# 模型下载（GitHub release，需 follow_redirects；URL 已实测 200/302 可达）
_URL_FIRERED = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
                "sherpa-onnx-fire-red-asr2-zh_en-int8-2026-02-26.tar.bz2")
_URL_PUNCT = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/"
              "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12.tar.bz2")
_URL_VAD = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
            "silero_vad.onnx")

# 识别参数（实测：3 线程 RTF 0.271，272s 音频识别耗时 73.7s）
_NUM_THREADS = 3
# VAD 参数：threshold/min_silence/min_speech 为实测可用组合；
# max_speech_duration=20 保证单段 ≤20s（超长语音 VAD 内部强制切分）
_VAD_THRESHOLD = 0.5
_VAD_MIN_SILENCE = 0.5
_VAD_MIN_SPEECH = 0.25
_VAD_MAX_SPEECH = 20.0
_VAD_WINDOW = 512
# VAD 段首尾 padding（秒）：段间静音被判为非语音直接丢弃会吞字
# （实测丢过「国家/这是/两」），首尾各扩 0.15s 从全量样本里补回
_VAD_PAD_SECONDS = 0.15

# 进度条分段（与云端引擎同口径：识别段 10~99）
_PROGRESS_MODEL_MAX = 8   # 0~8 模型下载
_PROGRESS_RECOG_BASE = 10

_FIRERED_LOCK = threading.Lock()


def _file_ok(p: Path) -> bool:
    return p.is_file() and p.stat().st_size > 0


def firered_model_ready() -> bool:
    """三件套（encoder/decoder/tokens）+ 标点模型 + VAD 模型全部就位。"""
    parts = [
        FIRERED_MODEL_DIR / "encoder.int8.onnx",
        FIRERED_MODEL_DIR / "decoder.int8.onnx",
        FIRERED_MODEL_DIR / "tokens.txt",
        FIRERED_PUNCT_MODEL_DIR / "model.onnx",
        FIRERED_VAD_MODEL_PATH,
    ]
    return all(_file_ok(p) for p in parts)


def _manual_hint(url: str, target: Path) -> str:
    return (
        f'也可手动下载（网络不通时）：{url} ，'
        f'解压/放置到 {target} 后重试'
    )


def _download_file(url: str, dest: Path, label: str,
                   progress_callback=None, cancel_check=None,
                   pct_base: float = 0.0, pct_span: float = 1.0) -> None:
    """流式下载单文件到 dest（带字节级进度），失败上抛并附手动指引。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + '.part')
    try:
        with httpx.Client(follow_redirects=True, timeout=60.0) as client:
            with client.stream('GET', url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get('content-length') or 0)
                done = 0
                with open(tmp, 'wb') as f:
                    for chunk in resp.iter_bytes(1024 * 1024):
                        if cancel_check and cancel_check():
                            raise TranscriptionCancelledError('用户取消，模型下载中止')
                        f.write(chunk)
                        done += len(chunk)
                        if progress_callback and total > 0:
                            pct = pct_base + pct_span * min(done / total, 1.0)
                            progress_callback(
                                int(pct),
                                f'下载 {label} {done // (1024 * 1024)}MB / {total // (1024 * 1024)}MB'
                            )
        tmp.replace(dest)
    except TranscriptionCancelledError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise Exception(
            f'下载 {label} 失败（{type(e).__name__}: {e}）。{_manual_hint(url, dest)}'
        ) from e


def _extract_archive(archive: Path, expected_dir: Path, label: str) -> None:
    """解包 tar.bz2 到 expected_dir（归一化顶层目录名），失败上抛并清理。"""
    tmp_root = expected_dir.parent / f'.extract_{expected_dir.name}'
    try:
        tmp_root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, 'r:bz2') as tf:
            tf.extractall(tmp_root, filter='data')
        if expected_dir.exists():
            raise Exception(f'{label} 解包后目标目录已存在（疑似并发下载），请手动检查 {expected_dir}')
        entries = [p for p in tmp_root.iterdir() if not p.name.startswith('.')]
        if len(entries) == 1 and entries[0].is_dir():
            entries[0].rename(expected_dir)
        else:
            tmp_root.rename(expected_dir)
    except Exception as e:
        raise Exception(
            f'解包 {label} 失败（{e}）。也可手动下载解压后放置到 {expected_dir}'
        ) from e
    finally:
        if tmp_root.exists():
            shutil.rmtree(tmp_root, ignore_errors=True)
        archive.unlink(missing_ok=True)


def _ensure_model_ready(progress_callback=None, cancel_check=None) -> None:
    """模型就绪检查；缺失部分按需下载（大→小），全就位时零开销直接返回。"""
    if firered_model_ready():
        return
    if progress_callback:
        progress_callback(1, '本地 FireRedASR 模型未就绪，开始按需下载（约 1.1GB，仅首次）...')
    logger.info('FireRedASR 模型不完整，开始下载（firered/punct/vad 缺失项）')

    enc = FIRERED_MODEL_DIR / 'encoder.int8.onnx'
    dec = FIRERED_MODEL_DIR / 'decoder.int8.onnx'
    tok = FIRERED_MODEL_DIR / 'tokens.txt'
    punct = FIRERED_PUNCT_MODEL_DIR / 'model.onnx'
    vad = FIRERED_VAD_MODEL_PATH

    # 下载份额： firered 0~6% / punct 6~7.5% / vad 7.5~8%（vad 极小可忽略）
    if not (_file_ok(enc) and _file_ok(dec) and _file_ok(tok)):
        archive = FIRERED_MODEL_DIR.parent / f'{FIRERED_MODEL_DIR.name}.tar.bz2'
        _download_file(_URL_FIRERED, archive, 'FireRedASR 模型（838MB）',
                       progress_callback, cancel_check, 1.0, 5.0)
        _extract_archive(archive, FIRERED_MODEL_DIR, 'FireRedASR 模型')
    if not _file_ok(punct):
        archive = FIRERED_PUNCT_MODEL_DIR.parent / f'{FIRERED_PUNCT_MODEL_DIR.name}.tar.bz2'
        _download_file(_URL_PUNCT, archive, '标点模型（266MB）',
                       progress_callback, cancel_check, 6.0, 1.5)
        _extract_archive(archive, FIRERED_PUNCT_MODEL_DIR, '标点模型')
    if not _file_ok(vad):
        _download_file(_URL_VAD, vad, 'VAD 模型（0.6MB）',
                       progress_callback, cancel_check, 7.5, 0.5)

    if not firered_model_ready():
        raise Exception(
            f'模型下载完成但完整性校验未通过（{FIRERED_MODEL_DIR} / {FIRERED_PUNCT_MODEL_DIR}），'
            f'请检查磁盘空间或手动放置模型'
        )


def _load_sherpa():
    try:
        import sherpa_onnx
        return sherpa_onnx
    except ImportError as e:
        raise Exception(
            f'sherpa-onnx 未安装（{e}），本地 FireRedASR 引擎不可用。'
            f'请运行: pip install sherpa-onnx>=1.13.8'
        ) from e


def _create_recognizer(sherpa):
    return sherpa.OfflineRecognizer.from_fire_red_asr(
        encoder=str(FIRERED_MODEL_DIR / 'encoder.int8.onnx'),
        decoder=str(FIRERED_MODEL_DIR / 'decoder.int8.onnx'),
        tokens=str(FIRERED_MODEL_DIR / 'tokens.txt'),
        num_threads=_NUM_THREADS,
        decoding_method='greedy_search',
        provider='cpu',
    )


def _create_vad(sherpa):
    cfg = sherpa.VadModelConfig()
    cfg.silero_vad.model = str(FIRERED_VAD_MODEL_PATH)
    cfg.silero_vad.threshold = _VAD_THRESHOLD
    cfg.silero_vad.min_silence_duration = _VAD_MIN_SILENCE
    cfg.silero_vad.min_speech_duration = _VAD_MIN_SPEECH
    cfg.silero_vad.max_speech_duration = _VAD_MAX_SPEECH
    cfg.silero_vad.window_size = _VAD_WINDOW
    cfg.sample_rate = 16000
    cfg.num_threads = 1
    cfg.provider = 'cpu'
    # 高层封装 VoiceActivityDetector（front 是属性不是方法）；缓冲取 30s 足够
    # max_speech_duration=20 的段排队
    return sherpa.VoiceActivityDetector(cfg, buffer_size_in_seconds=30)


def _create_punct(sherpa):
    cfg = sherpa.OfflinePunctuationConfig()
    cfg.model.ct_transformer = str(FIRERED_PUNCT_MODEL_DIR / 'model.onnx')
    cfg.model.num_threads = 1
    return sherpa.OfflinePunctuation(cfg)


def _to_wav_16k_mono(audio_path: str, progress_callback=None) -> str:
    """ffmpeg 转 16k 单声道 wav（FireRedASR/VAD 输入要求），返回临时文件路径。"""
    duration = _get_audio_duration_seconds(audio_path)
    if progress_callback:
        progress_callback(_PROGRESS_MODEL_MAX, f'音频预处理（{_format_duration(duration)} → 16k 单声道）...')
    fd, tmp = tempfile.mkstemp(suffix='.wav', prefix='firered_')
    os.close(fd)
    Path(tmp).unlink(missing_ok=True)  # 只要路径名，ffmpeg 自己写
    cmd = [_ffmpeg_bin(), '-y', '-i', audio_path, '-ac', '1', '-ar', '16000', '-f', 'wav', tmp]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not Path(tmp).is_file():
        raise Exception(f'ffmpeg 音频预处理失败（exit {proc.returncode}）: {proc.stderr[-300:]}')
    return tmp


def _read_wav_samples(wav_path: str) -> Tuple[np.ndarray, int]:
    with wave.open(wav_path, 'rb') as f:
        sr = f.getframerate()
        frames = f.readframes(f.getnframes())
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    return samples, sr


def _pad_vad_boundaries(raw_segments: List[Tuple[int, int]], total_samples: int,
                        sample_rate: int) -> List[Tuple[float, float]]:
    """VAD 原始边界 [(start_sample, end_sample)] → 秒区间，首尾各加 padding
    并截到 [0, total]，相邻段重叠保持原样（识别按段独立，重叠只影响各自首尾）。"""
    pad = int(_VAD_PAD_SECONDS * sample_rate)
    out: List[Tuple[float, float]] = []
    for start, end in raw_segments:
        if end <= start:
            continue
        s = max(0, start - pad)
        e = min(total_samples, end + pad)
        if e > s:
            out.append((s / sample_rate, e / sample_rate))
    return out


def _detect_speech_segments(vad, samples: np.ndarray, sample_rate: int) -> List[Tuple[int, int]]:
    """silero VAD 切分 → [(start_sample, end_sample)]（原始边界，不含 padding）。

    ⚠️ 必须按 window_size 小块喂入：一次性 accept_waveform 整段音频会导致 VAD
    状态异常（实测只报出尾部一段），分块喂入结果正常（实测 2 段语音边界吻合 RMS）。
    """
    vad.reset()
    out: List[Tuple[int, int]] = []
    for i in range(0, len(samples), _VAD_WINDOW):
        vad.accept_waveform(samples[i:i + _VAD_WINDOW])
        while not vad.empty():
            seg = vad.front
            out.append((seg.start, seg.start + len(seg.samples)))
            vad.pop()
    vad.flush()
    while not vad.empty():
        seg = vad.front
        out.append((seg.start, seg.start + len(seg.samples)))
        vad.pop()
    return out


def _write_outputs(audio_path: str,
                   results: List[Tuple[float, float, str]]) -> Tuple[str, str, int]:
    """段级结果 → 句级化 SRT/TXT（与云端引擎同一套切分/分配逻辑）。"""
    src = Path(audio_path)
    srt_path = str(src.with_suffix('.srt'))
    txt_path = str(src.with_suffix('.txt'))
    segments_out: List[Tuple[float, float, str]] = []
    for seg_start, seg_end, text in results:
        segments_out.extend(distribute_times(seg_start, seg_end, split_sentences(text)))
    with open(srt_path, 'w', encoding='utf-8') as f_srt, \
         open(txt_path, 'w', encoding='utf-8') as f_txt:
        for idx, (seg_start, seg_end, text) in enumerate(segments_out):
            f_srt.write(f"{idx + 1}\n{_format_srt_time(seg_start)} --> {_format_srt_time(seg_end)}\n{text}\n\n")
            f_txt.write(f"[{_format_mmss(seg_start)} - {_format_mmss(seg_end)}] {text}\n")
    return srt_path, txt_path, len(segments_out)


def transcribe_audio_firered(
    audio_path: str,
    progress_callback: Optional[Callable] = None,
    cancel_check: Optional[Callable] = None,
    language: Optional[str] = None,
    speaker_diarization: bool = False,
) -> Tuple[str, str, int]:
    """本地 FireRedASR2-AED 完整转录：模型就绪 → VAD 分段 → 逐段识别 → 标点 → 句级 SRT/TXT。

    与本地 Whisper/云端引擎同契约，返回 (srt_path, txt_path, seg_count)。
    FireRedASR2 仅支持中/英（language 非 zh/en 时仅记录，模型本身自动识别）；
    不支持说话人分离。
    """
    if language and language not in ('zh', 'en'):
        logger.info(f'FireRedASR2 仅支持中/英（收到 language={language}），模型按中英自动识别')
    if speaker_diarization:
        logger.warning('FireRedASR 引擎暂不支持说话人分离，已忽略该选项')

    def _report(pct: int, msg: str) -> None:
        if progress_callback:
            progress_callback(pct, msg)

    with _FIRERED_LOCK:
        src = Path(audio_path)
        duration = _get_audio_duration_seconds(audio_path) or 0.0
        _report(0, f'本地转录（FireRedASR2）| 时长 {_format_duration(duration)} | 检查模型...')

        _ensure_model_ready(progress_callback, cancel_check)
        sherpa = _load_sherpa()
        _report(_PROGRESS_MODEL_MAX, '模型加载中（首次约 4 秒）...')
        load_start = time.time()
        recognizer = _create_recognizer(sherpa)
        vad = _create_vad(sherpa)
        punct = _create_punct(sherpa)
        logger.info(f'FireRedASR 模型加载完成（{time.time() - load_start:.1f}s）')

        wav_tmp = _to_wav_16k_mono(audio_path, progress_callback)
        try:
            samples, sr = _read_wav_samples(wav_tmp)
            if len(samples) == 0:
                raise Exception(f'音频预处理后为空（{src.name}），无法转录')

            _report(9, 'VAD 语音分段中...')
            vad_start = time.time()
            raw = _detect_speech_segments(vad, samples, sr)
            speech_ranges = _pad_vad_boundaries(raw, len(samples), sr)
            logger.info(f'VAD 分段: {len(speech_ranges)} 段语音 / 全长 {duration:.1f}s '
                        f'（耗时 {time.time() - vad_start:.1f}s）')

            transcribe_start = time.time()
            total_speech = sum(e - s for s, e in speech_ranges)
            results: List[Tuple[float, float, str]] = []
            processed = 0.0
            for i, (seg_start, seg_end) in enumerate(speech_ranges):
                if cancel_check and cancel_check():
                    raise TranscriptionCancelledError('用户取消，转录中止')
                seg_samples = samples[int(seg_start * sr):int(seg_end * sr)]
                stream = recognizer.create_stream()
                stream.accept_waveform(sr, seg_samples)
                recognizer.decode_stream(stream)
                text = (stream.result.text or '').strip()
                if text:
                    text = punct.add_punctuation(text)
                results.append((seg_start, seg_end, text))
                processed += seg_end - seg_start

                ratio = (processed / total_speech) if total_speech > 0 else (i + 1) / max(len(speech_ranges), 1)
                pct = int(_PROGRESS_RECOG_BASE + min(ratio, 1.0) * 88)
                elapsed = time.time() - transcribe_start
                speed_x = (processed / elapsed) if elapsed > 0.5 and processed > 0 else 0.0
                msg = (f'本地转录 {pct}% | 语音段 {i + 1}/{len(speech_ranges)} | '
                       f'已用 {_format_duration(elapsed)}')
                if speed_x > 0:
                    msg += f' | 速度 {speed_x:.1f}x'
                if (i + 1) % 5 == 0 or i == 0 or i == len(speech_ranges) - 1:
                    _report(pct, msg)
        finally:
            Path(wav_tmp).unlink(missing_ok=True)

        srt_path, txt_path, seg_count = _write_outputs(audio_path, results)
        elapsed = time.time() - transcribe_start if speech_ranges else 0.0
        speed_x = (duration / elapsed) if elapsed > 0 and duration > 0 else 0.0
        logger.info(f'FireRedASR 转录完成: {seg_count} 段（{len(speech_ranges)} 语音段）, '
                    f'耗时 {_format_duration(elapsed)}')
        done_msg = (f'本地转录完成，共 {seg_count} 段 | 音频 {_format_duration(duration)} | '
                    f'耗时 {_format_duration(elapsed)}')
        if speed_x > 0:
            done_msg += f' | 均速 {speed_x:.1f}x'
        _report(99, done_msg)
        _report(100, '转录完成！')
        return srt_path, txt_path, seg_count
