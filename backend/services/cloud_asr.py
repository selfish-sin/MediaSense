"""
云端 ASR 转录服务（双 provider 共存，2026-09-12）

- mimo（默认，付费主力）：小米 MiMo-v2.5-ASR，chat/completions 音频多模态姿势。
  messages 只放音频 {"type":"input_audio","input_audio":{"data":"data:audio/mpeg;base64,..."}}，
  **禁带 text 部分**（网关注入提示词，带文本 400）；文本取 choices[0].message.content。
  ¥0.5/小时，10min 素材实测 9.4s，术语最准。
- xingchen（免费档，简单视频用）：硅基流动 XingChenASR-V3.2-Ultra，OpenAI multipart
  /audio/transcriptions 姿势（同旧 SenseVoice 接口），响应 {'text': ...}。
  实测单片 1.7~4.7s 稳定无抖动（SenseVoice 同基建 46~83s，已退役）。

本地仍做静音切分：分片 3~30s；时间轴由分片边界 + 句级后处理提供。
契约与本地 Whisper 引擎一致：返回 (srt_path, txt_path, seg_count)。

错误语义（必须可见可行动，禁止静默吞错）：
- CloudASRConfigError：401/402/403/404 配置与计费类错误（401 密钥无效、402 余额不足、
  403 无权限、404 模型不存在），重试无意义，立即抛给调用方回退并给修复指引
- CloudASRUnavailableError：400/429/5xx/网络错误退避重试耗尽（400 可能是单片内容问题，
  重试耗尽后跳过交给本地补录，不能上升为整体配置失败）
- **付费档只能显式选择**（2026-09-15 收紧）：自动路径（B站字幕抽查、未显式指定 provider 的调用）
  的 provider 走 `preferred_provider()` 单点，**只返回免费槽位**；免费档不可用返回 None
  （调用方回退本地），绝不静默落到付费档（历史泄漏：抽查漏传 provider → 默认 mimo 付费档，
  付费额度在用户不知情时被逐任务消耗）。付费档由用户显式选引擎 `cloud` 时经
  `transcriber._transcribe_by_engine` 传 `provider_for_engine('cloud')` 进入。
分片级韧性（长视频数百片，单片抖动不应整体回退本地）：
- 单片 Unavailable 失败 → 备用引擎补录（XingChen 免费 → firered → Whisper，
  _rescue_failed_chunks），补不回才留空洞
- 全部分片失败且补录无收获 → 抛 CloudASRUnavailableError，调用方整体回退本地 Whisper
- 部分失败 → 返回补录后的结果并在日志/进度条告警
"""
import base64
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
# 旧 SenseVoice 是排队式推理（单片延迟 1s~360s 随机抖动）；mimo 中位 ~4s，
# ReadTimeout，重试后多数即返回 200）：单次超时放宽到 180s，env ASR_CLOUD_TIMEOUT 可调
_TIMEOUT_PER_CALL = 180.0
_MAX_RETRIES = 3
_BACKOFF_CAP = 8.0
# 有界并发：SiliconFlow 限流为账号级 RPM/TPM（L0 免费档 RPM 1000），
# 8 路并发 + 429 退避重试自适应兜底；env ASR_CLOUD_CONCURRENCY 可调（上限 16）
_DEFAULT_CONCURRENCY = 8

# 富文本标签（<|HAPPY|> / <|BGM|> / <|zh|> 等）防御性清洗（多 provider 通用）
_RICH_TAG_RE = re.compile(r'<\|[A-Za-z_]+\|>')
# silencedetect 输出解析
_SILENCE_START_RE = re.compile(r'silence_start:\s*([\d.]+)')
_SILENCE_END_RE = re.compile(r'silence_end:\s*([\d.]+)')
# 分片补录用 Whisper 提示词（与字幕抽查同语义：锁简体防中文幻觉英文）
_ZH_RESCUE_PROMPT = '以下是简体中文。'

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


_DEFAULT_FREE_ENDPOINT = 'https://api.siliconflow.cn/v1/audio/transcriptions'
_DEFAULT_FREE_MODEL = 'XingChenAGI/XingChenASR-V3.2-Ultra'
_DEFAULT_PAID_ENDPOINT = 'https://api.xiaomimimo.com/v1/chat/completions'
_DEFAULT_PAID_MODEL = 'mimo-v2.5-asr'

# 内置 provider 的简短运行时标签（日志/进度条用；自定义 provider 取 DB 里的名称）
_PROVIDER_LABELS = {'mimo': 'MiMo-ASR', 'xingchen': 'XingChen-ASR'}


def _config_provider_cfg(provider: str = 'mimo') -> dict:
    """纯 config.py 读取（不查库）。

    两个用途：①DB 无该 provider 时的读取兜底；②内置 provider 首次播种的初始值。
    不查库是为了避免 db → services 的循环导入。getattr 容错便于测试 monkeypatch。
    """
    import config as _config
    if provider == 'xingchen':
        return {
            'enabled': bool(getattr(_config, 'ASR_FREE_ENABLED', True)),
            'endpoint': getattr(_config, 'ASR_FREE_ENDPOINT', _DEFAULT_FREE_ENDPOINT),
            'api_key': getattr(_config, 'ASR_FREE_API_KEY', ''),
            'model': getattr(_config, 'ASR_FREE_MODEL', _DEFAULT_FREE_MODEL),
            'api': 'multipart',
        }
    return {
        'enabled': bool(getattr(_config, 'ASR_CLOUD_ENABLED', False)),
        'endpoint': getattr(_config, 'ASR_CLOUD_ENDPOINT', _DEFAULT_PAID_ENDPOINT),
        'api_key': getattr(_config, 'ASR_CLOUD_API_KEY', ''),
        'model': getattr(_config, 'ASR_CLOUD_MODEL', _DEFAULT_PAID_MODEL),
        'api': 'chat',
    }


def _provider_cfg(provider: str = 'mimo') -> dict:
    """云端 provider 配置的**唯一读取点**：DB 优先，未命中回退 config.py。

    - DB（`asr_providers` 表，模型管理页可改）命中即返回，改配置无需重启服务；
    - 未命中（表未初始化/回滚到旧版本）回退 config.py，行为与改造前一致。

    返回结构固定：enabled / endpoint / api_key / model / api / name。
    """
    from db import asr_repo
    row = asr_repo.get_provider(provider)
    if row:
        return {
            'enabled': bool(row['enabled']),
            'endpoint': row['endpoint'] or '',
            'api_key': row['api_key'] or '',
            'model': row['model'] or '',
            # 请求姿势决定请求体构造与响应解析分支（见 _do_request / _extract_asr_text）
            'api': 'multipart' if row['api'] == 'multipart' else 'chat',
            'name': row['name'] or provider,
        }
    cfg = _config_provider_cfg(provider)
    cfg['name'] = _PROVIDER_LABELS.get(provider, provider)
    return cfg


def provider_for_engine(engine: str) -> Optional[str]:
    """引擎槽位（cloud / cloud_free）→ 运行时首选 provider key；槽位无可用 provider 返回 None。

    单点：transcriber 的引擎路由与分片补录链都经此取 provider，不要在别处硬编码 provider 名。
    """
    from db import asr_repo
    return asr_repo.get_provider_for_engine(engine)


def preferred_provider() -> Optional[str]:
    """**自动路径**（B站字幕抽查、未显式指定 provider 的调用）的云端 provider 单点。

    ★ 策略（2026-09-15 收紧，勿放宽回「免费档不可用就用付费档」）：
    **只返回免费槽位（`cloud_free`）的可用 provider**；免费档未配置/停用/缺密钥 → 返回 None，
    调用方直接回退本地 Whisper。

    为什么不再回退付费槽位：抽查是「每个任务都会发生」的隐式调用，付费档作为默认兜底
    曾让付费额度在用户毫不知情时被逐任务消耗（历史泄漏：抽查漏传 provider → 默认 mimo）。
    付费档**只能由用户显式选择引擎 `cloud`** 触发，不在本函数职责内
    （要停用免费档就在「模型管理 → ASR 模型」停用它）。
    """
    key = provider_for_engine('cloud_free')
    if key and cloud_asr_configured(key):
        return key
    return None


def provider_label(provider: str) -> str:
    """运行时显示名：内置用简短标签（进度条/日志），自定义 provider 用其名称。"""
    if provider in _PROVIDER_LABELS:
        return _PROVIDER_LABELS[provider]
    from db import asr_repo
    row = asr_repo.get_provider(provider)
    return (row or {}).get('name') or provider


def _asr_cfg() -> dict:
    """默认云端 provider（mimo 槽位的首选 provider）配置，供 /api/asr/status 等使用。"""
    return _provider_cfg(provider_for_engine('cloud') or 'mimo')


def cloud_asr_configured(provider: str = 'mimo') -> bool:
    """指定云端 provider 是否已配置可用（开关开 + 端点 + 密钥齐全）。"""
    cfg = _provider_cfg(provider)
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
    """清洗富文本标签并归一化空白。"""
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


def export_chunks(
    audio_path: str,
    cuts: List[float],
    progress_callback: Optional[Callable] = None,
) -> Tuple[List[str], Optional[List[float]]]:
    """按切点逐片导出 16k 单声道 mp3 分片，返回 (分片路径有序列表, 每片实测时长秒列表)。

    逐片 `-ss` 导出而非 `-f segment` 一次切完：ffmpeg 8.1 的 segment muxer 带
    上百个切点时病态慢（30s 音频要 8~20s，而同参数普通导出 170 倍速），55min
    音频 111 片必撞导出超时（2026-10-05 两个长任务同时超时回退本地实测）；
    逐片路径每片 <1s。cuts 含收尾切点（=计划总时长）；尾片不传 -t，自动延伸
    到实际 EOF（ffprobe 时长与实际音频存在毫秒级偏差）。单片场景（cuts 只有
    收尾切点）整段转码。每片 ≤30s → ≤200KB，远小于 API 单次 ≤50MB 限制。

    每片时长用 ffprobe 实测（mp3 帧对齐使实测略长于计划值），时间轴以实测为准；
    任一片探测失败返回 None，调用方退化为计划切点区间。单片场景直接返回 None
    （单片区间即 [0, 总时长]，无必要探测）。

    progress_callback 沿用调用方的阶段刻度：导出位于 4%（静音探测完成）与
    8%（开始云端转录）之间，按片推进 4→8，消除导出期间的进度盲区。
    """
    src = Path(audio_path)
    out_dir = src.parent
    prefix = src.stem
    # 清残留：同名音频重复转录时，上次残留分片会被 glob 混入
    for old in out_dir.glob(f'{prefix}_cloud_*.mp3'):
        old.unlink(missing_ok=True)

    # 第 i 片区间 [starts[i], starts[i+1])；尾片开区间到实际 EOF
    starts = [0.0] + list(cuts)
    total = len(starts) - 1
    export_start = time.time()
    for i in range(total):
        out_path = out_dir / f'{prefix}_cloud_{i:03d}.mp3'
        cmd = [_ffmpeg_bin(), '-y', '-v', 'error', '-ss', f'{starts[i]:.3f}']
        if i < total - 1:
            cmd += ['-t', f'{starts[i + 1] - starts[i]:.3f}']
        cmd += ['-i', audio_path, '-ac', '1', '-ar', '16000',
                '-c:a', 'libmp3lame', '-b:a', '48k', str(out_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            raise CloudASRUnavailableError(
                f'ffmpeg 分片导出失败（第 {i + 1}/{total} 片，音频: {src.name}，'
                f'ffmpeg: {(result.stderr or "").strip()[-200:]}），无法继续云端转录'
            )
        if progress_callback:
            progress_callback(4 + int((i + 1) / total * 4), f'导出分片 {i + 1}/{total}...')

    chunks = sorted(out_dir.glob(f'{prefix}_cloud_*.mp3'))
    if not chunks:
        raise CloudASRUnavailableError(
            f'ffmpeg 分片导出失败（音频: {src.name}），无法继续云端转录'
        )
    if total == 1:
        return [str(chunks[0])], None
    durations: List[float] = []
    for c in chunks:
        d = _get_audio_duration_seconds(str(c))
        if not d:
            logger.warning(f'分片时长探测失败（{c.name}），按计划切点区间处理')
            durations = []
            break
        durations.append(d)
    logger.info(f'分片导出完成：{len(chunks)} 片，耗时 {time.time() - export_start:.1f}s')
    return [str(c) for c in chunks], (durations or None)


def _do_request(cfg: dict, path: str, timeout: float) -> httpx.Response:
    """单次云端 ASR 调用（独立函数便于测试 monkeypatch），按 provider 分派请求姿势。"""
    if cfg.get('api') == 'multipart':
        return _request_multipart(cfg, path, timeout)
    return _request_chat(cfg, path, timeout)


def _request_chat(cfg: dict, path: str, timeout: float) -> httpx.Response:
    """MiMo：chat/completions 音频姿势，messages 只放 input_audio（base64 data URI），
    **禁带 text 部分**——mimo 网关对 ASR 请求注入固定提示词，带文本会 400。"""
    audio_b64 = base64.b64encode(Path(path).read_bytes()).decode()
    with httpx.Client(timeout=timeout) as client:
        return client.post(
            cfg['endpoint'],
            headers={'Authorization': f"Bearer {cfg['api_key']}"},
            json={
                'model': cfg['model'],
                'messages': [{'role': 'user', 'content': [
                    {'type': 'input_audio',
                     'input_audio': {'data': f'data:audio/mpeg;base64,{audio_b64}'}}]},
                ],
            },
        )


def _request_multipart(cfg: dict, path: str, timeout: float) -> httpx.Response:
    """XingChen/SiliconFlow：OpenAI multipart /audio/transcriptions 姿势（旧 SenseVoice 同款）。"""
    with open(path, 'rb') as f:
        with httpx.Client(timeout=timeout) as client:
            return client.post(
                cfg['endpoint'],
                headers={'Authorization': f"Bearer {cfg['api_key']}"},
                files={'file': (Path(path).name, f, 'audio/mpeg')},
                data={'model': cfg['model']},
            )


def _extract_asr_text(api: str, payload: dict) -> str:
    """按**请求姿势**从响应取转写文本（勿改回按 provider 名分支——自定义 provider 会错分支）。

    multipart（OpenAI /audio/transcriptions）→ ``{'text': ...}``；
    chat（chat/completions 音频多模态）→ ``choices[0].message.content``
    （choices 可能为空数组/null，取值必须防越界）。
    """
    if api == 'multipart':
        return (payload or {}).get('text', '') or ''
    choices = (payload or {}).get('choices') or []
    if not choices:
        return ''
    msg = choices[0].get('message') or {}
    return msg.get('content') or ''


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


def transcribe_file(path: str, timeout: Optional[float] = None, max_retries: int = _MAX_RETRIES,
                    provider: Optional[str] = None) -> str:
    """单文件一次云端转写，返回清洗后的纯文本。

    provider 省略（None）→ 走 `preferred_provider()`（**只挑免费档**）；免费档不可用则抛
    CloudASRConfigError。**绝不默认付费档**——付费档必须由调用方显式传入
    （用户显式选引擎 `cloud` 的路径），避免"漏传参数 = 悄悄烧钱"。
    401/402/403/404 配置与计费类错误立即抛 CloudASRConfigError（重试无意义）；
    400/429/5xx/网络错误指数退避重试，耗尽抛 CloudASRUnavailableError。
    供完整转录与 B站字幕抽查共用。
    """
    if provider is None:
        provider = preferred_provider()
    if not provider:
        raise CloudASRConfigError(
            '未指定云端 ASR provider，且免费档不可用（未配置/已停用）——'
            '请在「模型管理 → ASR 模型 → 云端 ASR」启用免费档，或显式传入 provider'
        )
    cfg = _provider_cfg(provider)
    if not (cfg['enabled'] and bool(cfg['endpoint']) and bool(cfg['api_key'])):
        raise CloudASRConfigError(
            f'云端 ASR「{cfg.get("name", provider)}」未配置（endpoint/api_key 缺失或已停用）'
            f'——请在「模型管理 → ASR 模型 → 云端 ASR」中补全'
        )
    if timeout is None:
        timeout = _call_timeout()

    label = provider_label(provider)
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
                    text = _extract_asr_text(cfg.get('api', 'chat'), resp.json() or {})
                except ValueError:
                    last_reason = f'响应非 JSON: {resp.text[:200]}'
                    logger.warning(f'云端 ASR 响应异常: {last_reason}')
                else:
                    return strip_rich_tags(text)
            elif resp.status_code in (401, 402, 403, 404):
                detail = resp.text[:200]
                if resp.status_code == 401:
                    hint = f'ASR 密钥无效，请在「模型管理 → ASR 模型」中检查 {label} 的 API Key'
                elif resp.status_code == 402:
                    # 计费类错误：重试永远不可能成功（当日实测每任务白等 3 次退避 ≈11s）
                    hint = (f'{label} 账户余额不足/欠费（计费类错误，重试无意义）——'
                            f'请为该 provider 充值，或在「模型管理 → ASR 模型」中停用它')
                elif resp.status_code == 403:
                    hint = 'ASR 密钥无权限（可能未开通服务或余额不足）'
                else:
                    hint = f'ASR 模型不存在（model={cfg["model"]}），请检查该 provider 的模型名'
                raise CloudASRConfigError(f'云端 ASR {resp.status_code}: {hint} | 响应: {detail}')
            else:
                last_reason = f'HTTP {resp.status_code}: {resp.text[:200]}'
                logger.warning(f'云端 ASR 服务错误（第 {attempt + 1}/{max_retries} 次）: {last_reason}')

        if attempt < max_retries - 1:
            time.sleep(min(2 ** attempt, _BACKOFF_CAP))
    raise CloudASRUnavailableError(f'云端 ASR 重试 {max_retries} 次后仍失败: {last_reason}')


def _rescue_failed_chunks(
    failed_chunks: List[Tuple[str, float, float]],
    language: Optional[str],
    progress_callback: Optional[Callable] = None,
    cancel_check: Optional[Callable] = None,
    primary_provider: str = 'mimo',
) -> Tuple[str, List[Tuple[float, float, str]]]:
    """递归路由的分片级应用：云端失败的单片当场换备用引擎补录，不留空洞。

    备用链：XingChen（免费云端，换一家避开主 provider 限流；主 provider 就是
    xingchen 时跳过，不拿同一家限流的接口重试）→ firered（模型就绪时，快且准，
    不触发下载）→ Whisper base（固定 CPU，与字幕抽查同路径，中文带简体提示词
    防幻觉英文）。单片补录失败不拖垮整体（该段留空 + 日志可见）。
    返回 (实际用到的引擎标签, [(seg_start, seg_end, text)])。
    """
    from services import local_asr
    from services.transcriber import transcribe_audio_sample

    # 免费档 provider 由 cloud_free 槽位解析（勿硬编码 provider 名）：
    # 与主 provider 同一家时跳过这一级——不拿同一家被限流的接口重试
    free_provider = provider_for_engine('cloud_free')
    free_ok = bool(free_provider) and free_provider != primary_provider \
        and cloud_asr_configured(free_provider)
    free_label = f'免费档({provider_label(free_provider)})' if free_provider else '免费档'
    use_firered = local_asr.firered_model_ready()
    chain_label = '、'.join(label for label, ok in (
        (free_label, free_ok), ('FireRedASR', use_firered), ('Whisper(base)', True)) if ok)
    if progress_callback:
        progress_callback(
            90, f'{len(failed_chunks)} 片云端失败，改用备用引擎（{chain_label}）补录...')

    lang = language or 'zh'
    zh_prompt = _ZH_RESCUE_PROMPT if lang == 'zh' else None
    used: List[str] = []
    out: List[Tuple[float, float, str]] = []
    for cp, seg_start, seg_end in failed_chunks:
        if cancel_check and cancel_check():
            raise TranscriptionCancelledError('用户取消，转录中止')
        text = None
        if free_ok:
            try:
                text = transcribe_file(cp, provider=free_provider)
                if text and free_label not in used:
                    used.append(free_label)
            except Exception as e:
                logger.warning(
                    f'{free_label}补录失败（{Path(cp).name}: {type(e).__name__}: {e}），改用本地引擎')
                text = None
        if not text and use_firered:
            try:
                text = local_asr.transcribe_chunk_text(
                    cp, language=lang if lang in ('zh', 'en') else None)
                if text and 'FireRedASR' not in used:
                    used.append('FireRedASR')
            except Exception as e:
                logger.warning(
                    f'本地 FireRedASR 补录失败（{Path(cp).name}: {type(e).__name__}: {e}），改用 Whisper base')
                text = None
        if not text:
            try:
                text = transcribe_audio_sample(
                    cp, max_seconds=90.0, language=lang, initial_prompt=zh_prompt)
                if text and 'Whisper(base)' not in used:
                    used.append('Whisper(base)')
            except Exception as e:
                logger.error(
                    f'本地 Whisper 补录失败（{Path(cp).name}: {type(e).__name__}: {e}），该段留空')
                continue
        if text:
            out.append((seg_start, seg_end, text.strip()))
    return ('、'.join(used) if used else 'Whisper(base)'), out


def transcribe_audio_cloud(
    audio_path: str,
    progress_callback: Optional[Callable] = None,
    cancel_check: Optional[Callable] = None,
    language: Optional[str] = None,
    speaker_diarization: bool = False,
    provider: Optional[str] = None,
) -> Tuple[str, str, int]:
    """云端完整转录：静音切分 → 分片上传（指定 provider）→ 句级时间戳 SRT/TXT。

    与本地 Whisper 引擎同契约，返回 (srt_path, txt_path, seg_count)。
    provider: None → `preferred_provider()`（只挑免费档，不可用则抛 ConfigError）；
    调用方（transcriber 引擎路由）总是显式传槽位 provider——付费档只在用户显式选 `cloud` 时进入。
    两者均按音频内容直接转写（language 参数仅记录）；不支持说话人分离。
    """
    if provider is None:
        provider = preferred_provider()
    if not provider:
        raise CloudASRConfigError(
            '未指定云端 ASR provider，且免费档不可用（未配置/已停用）——'
            '请在「模型管理 → ASR 模型 → 云端 ASR」启用免费档，或显式传入 provider'
        )
    if language:
        logger.info(f'云端引擎按音频内容转写（忽略指定语言 {language}）')
    if speaker_diarization:
        logger.warning('云端引擎暂不支持说话人分离，已忽略该选项')

    src = Path(audio_path)
    duration = _get_audio_duration_seconds(audio_path) or 0.0
    if progress_callback:
        progress_callback(2, f'云端转录（{provider_label(provider)}）| 时长 {_format_duration(duration)} | 静音探测中...')

    silences = detect_silences(audio_path, duration)
    midpoints = [(s + e) / 2 for s, e in silences]
    cuts = plan_cuts(duration, midpoints)
    if not cuts:
        raise CloudASRUnavailableError(f'无法确定音频时长（{src.name}），云端转录中止')

    if progress_callback:
        seg_count_est = len(cuts)
        progress_callback(4, f'静音探测完成：{len(silences)} 处静音 → {seg_count_est} 个分片，导出中...')

    chunk_paths, chunk_durations = export_chunks(audio_path, cuts, progress_callback)
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
    chunk_latencies: List[float] = []  # 每片墙钟耗时（含重试），用于延迟画像
    submit_epoch = transcribe_start

    executor = ThreadPoolExecutor(max_workers=_concurrency(),
                                  thread_name_prefix='cloud-asr')
    try:
        futures = {
            executor.submit(transcribe_file, cp, provider=provider): (i, seg_start, seg_end, cp)
            for i, (cp, (seg_start, seg_end)) in enumerate(zip(chunk_paths, segments))
        }
        completed = 0
        succeeded = 0
        failures = 0
        failed_ranges: List[str] = []
        failed_chunks: List[Tuple[str, float, float]] = []  # (分片文件, seg_start, seg_end)

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
            chunk_latencies.append(time.time() - submit_epoch)
            try:
                text = future.result()
            except TranscriptionCancelledError:
                raise
            except CloudASRConfigError:
                raise  # 401/403/404：配置必错，立即整体回退本地并展示修复指引
            except Exception as e:
                # 单片 Unavailable（429/5xx/网络重试耗尽）：记录待补录，其余分片照常
                # 转录——长视频几百片里单片抖动不应把整条转录拖回本地 Whisper
                failures += 1
                failed_ranges.append(f'{_format_mmss(seg_start)}-{_format_mmss(seg_end)}')
                failed_chunks.append((_cp, seg_start, seg_end))
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

        # ── 分片级递归补救：云端失败的片当场换备用引擎补录（必须发生在
        # finally 清理分片文件之前），失败的片不再是空洞 ──
        rescued = 0
        rescue_engine = ''
        if failed_chunks:
            rescue_engine, rescued_texts = _rescue_failed_chunks(
                failed_chunks, language, progress_callback, cancel_check, primary_provider=provider)
            results.extend(rescued_texts)
            rescued = len(rescued_texts)
    finally:
        # 取消/失败也要停掉排队任务并清理临时分片，不留垃圾占下载目录
        executor.shutdown(wait=False, cancel_futures=True)
        for cp in chunk_paths:
            try:
                Path(cp).unlink(missing_ok=True)
            except OSError:
                pass

    # 延迟画像：单片延迟波动大（旧 SenseVoice 实测 6s~300s+，mimo ~1-10s），
    # 中位/p90 直接决定整条转录快慢；日志留档便于诊断"云端突然变慢"
    if chunk_latencies:
        lat_sorted = sorted(chunk_latencies)
        n = len(lat_sorted)
        logger.info(
            f'[{provider_label(provider)}] 云端分片延迟画像: n={n} '
            f'min={lat_sorted[0]:.1f}s 中位={lat_sorted[n // 2]:.1f}s '
            f'p90={lat_sorted[min(int(n * 0.9), n - 1)]:.1f}s max={lat_sorted[-1]:.1f}s '
            f'（并发 {_concurrency()}，总耗时 {time.time() - transcribe_start:.1f}s）'
        )

    # 全部成功但文本全空（纯音乐/静音）时 seg_count=0 但不算失败；
    # 只有「分片全失败且补录也无收获」才整体回退本地（succeeded 统计请求成功的分片）。
    if succeeded == 0 and rescued == 0:
        raise CloudASRUnavailableError(
            '云端转录全部分片失败（网络/429/5xx 重试耗尽），回退本地 Whisper')
    if failures:
        still_failed = failures - rescued
        # 只列真正没补回来的段——failed_ranges 是全部失败段（含已补录的），照抄会误导
        rescued_starts = {r[0] for r in rescued_texts}
        still_ranges = [rng for rng, (_cp, s, _e) in zip(failed_ranges, failed_chunks)
                        if s not in rescued_starts]
        if still_failed > 0:
            shown = '、'.join(still_ranges[:10])
            more = f'…共 {len(still_ranges)} 段' if len(still_ranges) > 10 else ''
            logger.error(
                f'云端转录完成但 {still_failed}/{total} 分片失败且补录未恢复（区间: {shown}{more}），'
                f'输出为部分结果'
            )
        else:
            logger.info(f'云端 {failures}/{total} 分片失败，已全部由备用引擎（{rescue_engine}）补录')

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
    logger.info(f'云端转录完成: {len(segments_out)} 段（{total} 分片，其中 {rescued} 片本地补录）, '
                f'耗时 {_format_duration(elapsed)}')
    if progress_callback:
        done_msg = (
            f'云端转录完成，共 {len(segments_out)} 段 | 音频 {_format_duration(duration)} | '
            f'耗时 {_format_duration(elapsed)}'
        )
        if rescued:
            done_msg += f' | {rescued} 片由本地引擎（{rescue_engine}）补录'
        still_failed = failures - rescued
        if still_failed:
            done_msg += f' | {still_failed} 段失败未恢复'
        if speed_x > 0:
            done_msg += f' | 均速 {speed_x:.1f}x'
        progress_callback(99, done_msg)
        progress_callback(100, '转录完成！')
    return srt_path, txt_path, len(segments_out)
