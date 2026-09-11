"""
云端 ASR 转录服务（SiliconFlow / SenseVoiceSmall）

SiliconFlow /v1/audio/transcriptions 只返回纯文本（无时间戳），
时间轴由本地静音切分边界提供：ffmpeg silencedetect 找静音中点作切点，
分片 6~30s（≤30s/片 ≈ ≤200KB mp3，天然绕开单次调用 ≤1h/≤50MB 限制，
两小时以上的视频也按 ~几百个分片顺序上传）。
契约与本地 Whisper 引擎一致：返回 (srt_path, txt_path, seg_count)。

错误语义（必须可见可行动，禁止静默吞错）：
- CloudASRConfigError：401/403/404 配置类错误，不重试，立即抛给调用方回退本地并给修复指引
- CloudASRUnavailableError：429/5xx/网络错误退避重试耗尽
分片级韧性（长视频数百片，单片抖动不应整体回退本地）：
- 单片 Unavailable 失败 → 跳过该段记日志，其余分片照常转录
- 全部分片失败 → 抛 CloudASRUnavailableError，调用方整体回退本地 Whisper
- 部分失败 → 返回部分结果并在日志/进度条告警失败区间
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
# SiliconFlow SenseVoice 是排队式推理，常态延迟常 >90s（冒烟实测 90s 单次超时屡发
# ReadTimeout，重试后多数即返回 200）：单次超时放宽到 180s，env ASR_CLOUD_TIMEOUT 可调
_TIMEOUT_PER_CALL = 180.0
_MAX_RETRIES = 3
_BACKOFF_CAP = 8.0
# 有界并发：SiliconFlow 限流为账号级 RPM/TPM（L0 免费档 RPM 1000），
# 8 路并发 + 429 退避重试自适应兜底；env ASR_CLOUD_CONCURRENCY 可调（上限 16）
_DEFAULT_CONCURRENCY = 8

# SenseVoice 富文本标签（<|HAPPY|> / <|BGM|> / <|zh|> 等），防御性清洗
_RICH_TAG_RE = re.compile(r'<\|[A-Za-z_]+\|>')
# silencedetect 输出解析
_SILENCE_START_RE = re.compile(r'silence_start:\s*([\d.]+)')
_SILENCE_END_RE = re.compile(r'silence_end:\s*([\d.]+)')

# 句级字幕切分：云端 API 只回纯文本、无时间戳，若把整个分片文本塞进一条字幕，
# SRT 会退化成「一片一行」（3~30s 一坨），与本地 Whisper 的句级体验明显退化。
# 写盘前按句末标点切句、再按字符数比例把分片时长分配给各句——不增加任何
# API 调用/成本，纯本地后处理，即可把字幕粒度拉回句级。
_SUB_SENT_END = '。！？!?…；;'   # 句末标点（含中英）
_SUB_CLOSERS = '”"』」）)】]'      # 句末标点后可能跟随的收尾符号，一并并入前句
_SUB_MIN_CHARS = 4               # 短于该长度的碎片并入前句，避免字幕抖动
_SUB_MAX_CHARS = 40              # 单条字幕硬上限：无标点长句在此强制断开
_SUB_HARD_BREAK = '，,、 '        # 强制断句时优先选择的次级停顿符


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
    """并发上传路数（config/env 可调，钳制 1~16）。

    实测：硅基流动免费档单次请求排队延迟抖动大（同一段音频 3 片分别 2s/26s/90s），
    而账号级 RPM 上限（L0 免费 1000/min）远高于本默认值——长视频提高并发能让
    总耗时贴近「最慢单片」而非分片累加，故上限放宽到 16。
    """
    import config as _config
    raw = os.getenv('ASR_CLOUD_CONCURRENCY') or getattr(_config, 'ASR_CLOUD_CONCURRENCY', None)
    try:
        return max(1, min(int(raw), 16))
    except (TypeError, ValueError):
        return _DEFAULT_CONCURRENCY


def _call_timeout() -> float:
    """单次云端调用超时秒数（env ASR_CLOUD_TIMEOUT 可调）。"""
    raw = os.getenv('ASR_CLOUD_TIMEOUT')
    try:
        value = float(raw)
        return value if value > 0 else _TIMEOUT_PER_CALL
    except (TypeError, ValueError):
        return _TIMEOUT_PER_CALL


def strip_rich_tags(text: str) -> str:
    """清洗 SenseVoice 富文本标签并归一化空白。"""
    if not text:
        return ''
    cleaned = _RICH_TAG_RE.sub(' ', text)
    return re.sub(r'\s+', ' ', cleaned).strip()


def _hard_split_long(sentence: str) -> List[str]:
    """把无标点长句在次级停顿符（逗号/顿号/空格）附近断开（纯函数）。"""
    parts: List[str] = []
    start = 0
    n = len(sentence)
    while n - start > _SUB_MAX_CHARS:
        end = start + _SUB_MAX_CHARS
        window = sentence[start:end]
        cut = -1
        # 从窗口末尾往回找最近的次级停顿符（保证不切得太短）
        for j in range(len(window) - 1, _SUB_MIN_CHARS - 1, -1):
            if window[j] in _SUB_HARD_BREAK:
                cut = start + j + 1
                break
        if cut <= start:
            cut = end
        parts.append(sentence[start:cut].strip())
        start = cut
    if start < n:
        parts.append(sentence[start:].strip())
    return [p for p in parts if p]


def split_sentences(text: str) -> List[str]:
    """把一段纯文本切成句级片段（纯函数，单测覆盖）。

    规则：
    1. 以句末标点（``。！？!?…；;``）或换行断句；英文句点仅在后面是空白/结尾时算句末
       （避免把小数/缩写切开）；句末标点后紧跟的引号括号并入前句
    2. 过短片段（< ``_SUB_MIN_CHARS``）并入前句，避免字幕碎片
    3. 超长无标点句子（> ``_SUB_MAX_CHARS``）在次级停顿符附近强制断开

    返回去除空白后的句列表；空输入返回 ``[]``。无标点时整段 = 1 句（行为不变）。
    """
    if not text:
        return []
    raw = text.strip()
    if not raw:
        return []

    chunks: List[str] = []
    buf: List[str] = []
    n = len(raw)
    i = 0
    while i < n:
        ch = raw[i]
        buf.append(ch)
        is_end = ch in _SUB_SENT_END or ch == '\n'
        if not is_end and ch == '.':
            nxt = raw[i + 1] if i + 1 < n else ''
            is_end = nxt == '' or nxt.isspace()
        if is_end:
            # 吸收句末标点之后的收尾符号（引号/括号等）
            while i + 1 < n and raw[i + 1] in _SUB_CLOSERS:
                i += 1
                buf.append(raw[i])
            chunks.append(''.join(buf).strip())
            buf = []
        i += 1
    if buf:
        chunks.append(''.join(buf).strip())

    # 合并：累积到 _SUB_MIN_CHARS 才落一条（过短碎片并向后，避免出现
    # 「嗯。」这类残缺字幕行；行尾残余若仍过短则并回前一条）
    merged: List[str] = []
    buf = ''
    for s in (c for c in chunks if c):
        buf += s
        if len(buf) >= _SUB_MIN_CHARS:
            merged.append(buf)
            buf = ''
    if buf:
        if merged:
            merged[-1] += buf
        else:
            merged.append(buf)

    out: List[str] = []
    for s in merged:
        out.extend(_hard_split_long(s) if len(s) > _SUB_MAX_CHARS else [s])
    return out


def distribute_times(start: float, end: float, sentences: List[str]) -> List[Tuple[float, float, str]]:
    """把 [start, end] 按句子字符数比例分配给各句（纯函数，单测覆盖）。

    返回 ``[(seg_start, seg_end, text), ...]``。单句或总字数为 0 时原样返回整段。
    保证首句起点 == start、末句终点 == end、各区间单调不重叠。
    """
    if not sentences:
        return []
    total = sum(len(s) for s in sentences)
    span = max(0.0, end - start)
    if total <= 0 or len(sentences) == 1:
        return [(start, end, sentences[0])]
    out: List[Tuple[float, float, str]] = []
    acc = start
    for k, s in enumerate(sentences):
        # 末句强制对齐 end，避免浮点误差累积导致时间轴漂移
        seg_end = end if k == len(sentences) - 1 else round(acc + span * (len(s) / total), 3)
        if seg_end < acc:  # 兜底：极端比例下不让区间倒退
            seg_end = acc
        out.append((acc, seg_end, s))
        acc = seg_end
    return out


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


def _parse_segment_list(list_file: Path) -> Optional[List[float]]:
    """解析 ffmpeg -segment_list csv 输出（每行 "<路径>,<起始秒>,<结束秒>"），返回每片时长。

    时长 = 结束 - 起始（部分构建会带第 4 列 duration，取末两列之差等价）。
    解析失败返回 None，调用方退化为计划切点区间。
    """
    try:
        rows: List[float] = []
        for line in list_file.read_text(encoding='utf-8', errors='ignore').splitlines():
            fields = line.strip().split(',')
            if len(fields) >= 3:
                rows.append(float(fields[-1]) - float(fields[-2]))
        return rows or None
    except Exception as e:
        logger.warning(f'解析分片时长列表失败（{list_file.name}），按等分处理: {e}')
        return None


def export_chunks(audio_path: str, cuts: List[float]) -> Tuple[List[str], Optional[List[float]]]:
    """按切点导出 16k 单声道 mp3 分片，返回 (分片路径有序列表, 每片实际时长秒列表)。

    cuts 含收尾切点（=总时长），但 segment_times 只传内部切点：
    ffprobe 时长与实际音频存在毫秒级偏差，把收尾切点传给 ffmpeg 会在
    EOF 前多切出一个微型尾片（冒烟实测 4 片 vs 计划 3 片）。最后一片
    由 ffmpeg 自动延伸到实际 EOF。单片场景（无内部切点）直接整段转码。
    每片 ≤30s → ≤200KB，远小于 API 单次 ≤50MB 限制。

    第二返回值取 ffmpeg -segment_list 实测的每片时长（帧对齐真实值），
    时间轴以它为准，根治「分片数与计划不一致」；单片/解析失败时为 None。
    """
    src = Path(audio_path)
    out_dir = src.parent
    prefix = src.stem
    pattern = str(out_dir / f'{prefix}_cloud_%03d.mp3')
    list_file = out_dir / f'{prefix}_cloud_list.txt'
    # 清残留：同名音频重复转录时，上次残留分片会被 glob 混入
    for old in out_dir.glob(f'{prefix}_cloud_*.mp3'):
        old.unlink(missing_ok=True)
    list_file.unlink(missing_ok=True)

    internal_cuts = cuts[:-1] if len(cuts) > 1 else []
    if internal_cuts:
        times = ','.join(f'{t:.3f}' for t in internal_cuts)
        cmd = [
            _ffmpeg_bin(), '-y', '-v', 'error',
            '-i', audio_path,
            '-f', 'segment', '-segment_times', times,
            '-segment_list', str(list_file), '-segment_list_type', 'csv',
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
    durations = _parse_segment_list(list_file) if internal_cuts else None
    if durations is not None and len(durations) != len(chunks):
        logger.warning(
            f'分片时长列表与分片数不一致（{len(durations)} vs {len(chunks)}），忽略列表按等分'
        )
        durations = None
    return [str(c) for c in chunks], durations


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


def transcribe_file(path: str, timeout: Optional[float] = None, max_retries: int = _MAX_RETRIES) -> str:
    """单文件一次云端转写，返回清洗后的纯文本。

    401/403/404 配置类错误立即抛 CloudASRConfigError（重试无意义）；
    429/5xx/网络错误指数退避重试，耗尽抛 CloudASRUnavailableError。
    供完整转录与 B站字幕抽查共用。
    """
    cfg = _asr_cfg()
    if not cloud_asr_configured():
        raise CloudASRConfigError('云端 ASR 未配置（config.py 缺 endpoint/api_key 或未启用）')
    if timeout is None:
        timeout = _call_timeout()

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

    chunk_paths, chunk_durations = export_chunks(audio_path, cuts)
    if chunk_durations:
        # 时间轴以 ffmpeg 实测分片时长为准（帧对齐真实时长，分片数==分段数恒成立）
        segments: List[Tuple[float, float]] = []
        acc = 0.0
        for d in chunk_durations:
            segments.append((round(acc, 3), round(acc + d, 3)))
            acc += d
    else:
        # 单片（无内部切点）或列表解析失败：退回计划切点区间
        # 分片区间：[0, c1), [c1, c2), ..., [cN-1, total]（切点数 = 分片数）
        boundaries = [0.0] + cuts
        segments = list(zip(boundaries[:-1], boundaries[1:]))
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
        succeeded = 0
        failures = 0
        failed_ranges: List[str] = []

        def _report() -> None:
            elapsed = time.time() - transcribe_start
            ratio = (completed_audio / duration) if duration > 0 else completed / total
            pct = int(10 + min(ratio, 1.0) * 88)
            speed_x = (completed_audio / elapsed) if elapsed > 0.5 and completed_audio > 0 else 0.0
            msg = f'云端转录 {pct}% | 分片 {completed}/{total} 完成'
            if failures:
                msg += f' | {failures} 片失败'
            msg += f' | 已用 {_format_duration(elapsed)}'
            if speed_x > 0:
                msg += f' | 速度 {speed_x:.1f}x'
            if progress_callback:
                progress_callback(pct, msg)

        for future in as_completed(futures):
            if cancel_check and cancel_check():
                raise TranscriptionCancelledError('用户取消，转录中止')
            _i, seg_start, seg_end, _cp = futures[future]
            try:
                text = future.result()
            except TranscriptionCancelledError:
                raise
            except CloudASRConfigError:
                raise  # 401/403/404：配置必错，立即整体回退本地并展示修复指引
            except Exception as e:
                # 单片 Unavailable（429/5xx/网络重试耗尽）：跳过该段，其余分片照常
                # 转录——长视频几百片里单片抖动不应把整条转录拖回本地 Whisper
                failures += 1
                failed_ranges.append(f'{_format_mmss(seg_start)}-{_format_mmss(seg_end)}')
                logger.error(
                    f'云端分片转录失败已跳过（{Path(_cp).name}: {type(e).__name__}: {e}）'
                )
                completed += 1
                completed_audio += seg_end - seg_start
                _report()
                continue
            completed += 1
            completed_audio += seg_end - seg_start
            succeeded += 1
            if text:
                results.append((seg_start, seg_end, text))
            _report()
    finally:
        # 取消/失败也要停掉排队任务并清理临时分片，不留垃圾占下载目录
        executor.shutdown(wait=False, cancel_futures=True)
        for cp in chunk_paths:
            try:
                Path(cp).unlink(missing_ok=True)
            except OSError:
                pass

    # 全部成功但文本全空（纯音乐/静音）时 seg_count=0 但不算失败；
    # 只有「分片全失败」才整体回退本地（succeeded 统计请求成功的分片）。
    if succeeded == 0:
        raise CloudASRUnavailableError(
            '云端转录全部分片失败（网络/429/5xx 重试耗尽），回退本地 Whisper')
    if failures:
        shown = '、'.join(failed_ranges[:10])
        more = f'…共 {len(failed_ranges)} 段' if len(failed_ranges) > 10 else ''
        logger.error(
            f'云端转录完成但 {failures}/{total} 分片失败已跳过（区间: {shown}{more}），'
            f'输出为部分结果，可手动补齐或重试'
        )

    srt_path = str(src.with_suffix('.srt'))
    txt_path = str(src.with_suffix('.txt'))
    # 并发完成顺序不定，写盘前按音频时间重排（SRT 序号/时间轴必须单调）
    results.sort(key=lambda r: r[0])
    # 句级化：把每个分片的整坨文本拆成句，并按字数比例分配该分片时长。
    # 纯本地后处理，不增加 API 调用；无标点文本退化为原「一片一条」。
    segments_out: List[Tuple[float, float, str]] = []
    for seg_start, seg_end, text in results:
        segments_out.extend(distribute_times(seg_start, seg_end, split_sentences(text)))
    with open(srt_path, 'w', encoding='utf-8') as f_srt, \
         open(txt_path, 'w', encoding='utf-8') as f_txt:
        for idx, (seg_start, seg_end, text) in enumerate(segments_out):
            f_srt.write(f"{idx + 1}\n{_format_srt_time(seg_start)} --> {_format_srt_time(seg_end)}\n{text}\n\n")
            f_txt.write(f"[{_format_mmss(seg_start)} - {_format_mmss(seg_end)}] {text}\n")

    elapsed = time.time() - transcribe_start
    speed_x = (duration / elapsed) if elapsed > 0 and duration > 0 else 0.0
    logger.info(f'云端转录完成: {len(segments_out)} 段（{total} 分片）, 耗时 {_format_duration(elapsed)}')
    if progress_callback:
        done_msg = (
            f'云端转录完成，共 {len(segments_out)} 段 | 音频 {_format_duration(duration)} | '
            f'耗时 {_format_duration(elapsed)}'
        )
        if failures:
            done_msg += f' | {failures} 段失败已跳过'
        if speed_x > 0:
            done_msg += f' | 均速 {speed_x:.1f}x'
        progress_callback(99, done_msg)
        progress_callback(100, '转录完成！')
    return srt_path, txt_path, len(segments_out)
