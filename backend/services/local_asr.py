"""
本地 ASR 引擎（sherpa-onnx / onnxruntime，CPU，不依赖 torch）

## 新增通道（本模块的设计核心）

**加一个本地引擎 = 在 `LOCAL_ENGINES` 加一条声明 + 写一个 `transcribe_audio_<key>()`**。
就绪判定 / 文件明细 / 磁盘占用 / 后台下载（含取消与进度）/ 删除 / 运行互斥 全部由
通用生命周期按 key 分发，不需要改路由、前端或其它服务。

## 已注册引擎

| 引擎值 | 模型 | 说明 |
|---|---|---|
| `firered` | FireRedASR2-AED int8（~1.5GB） | 中英最准，实测 RTF≈0.21；**语义冻结** |

> 曾评估过两条 LLM 类路线（通义 Fun-ASR-Nano-2512、Qwen3-ASR ONNX），2026-09-15 实机横评后
> **放弃**：同一段 90s 中文素材上，CPU int8 自回归解码 RTF 0.5~1.0（FireRed 仅 0.27，慢 2~4 倍），
> 且出现成段重复循环幻觉（"those are the ones that are…"）、中英混出，调短 VAD 段长与显式
> `language=zh` 都未能消除。结论：本地档继续只保留 FireRed，不再引入 LLM 解码类引擎。
> 详见 task 09-15-local-asr-engine-registry 的横评记录。

## 通用契约

- 所有引擎返回 `(srt_path, txt_path, seg_count)`，与云端/Whisper 完全一致
- 管线：ffmpeg 16k 单声道 wav → silero VAD 分段（+0.15s padding）→ 段间并行解码 → 句级写盘
  （`cloud_asr.split_sentences/distribute_times`），再跑 CT-Transformer 标点
- 错误语义（禁止静默吞错）：模型缺失 → 按需下载（进度可见，失败文案含手动指引）；
  sherpa-onnx 未安装 → 可行动错误；识别异常 → 上抛由 transcriber 回退；用户取消 → 不回退
- 占位不变量：**兜底链路绝不触发模型下载**（只有用户显式选择该引擎且模型缺失才下载）

## 并发与内存

实测 FireRed 峰值 ~1.7GB，`MAX_CONCURRENT_JOBS=2` 下并发会翻倍——按引擎模块级锁串行化
（Whisper 不受影响）。

## sherpa-onnx API 坑点（实测踩出来的，勿"顺手重构"）

- VAD 必须 `VadModel.create(config)`，直接 `VadModel(...)` 报 "No constructor defined"
- `vad.window_size` 是【方法】不是属性；`vad.front` 是方法不是属性
- `OfflinePunctuation` 没有 `from_*` 工厂，直接 `OfflinePunctuation(cfg)`
"""
import importlib.util
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
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

# ============ 模型下载源（2026-09-15 实测后定型，勿随手改顺序） ============
# 实测结论：本机 **GitHub release 全部被 TLS 重置**（含 hf-mirror / ghproxy 类加速站），
# 而 **ModelScope 稳定可用**。故一律 **ModelScope 优先、GitHub 兜底**：
# 镜像里的 tar 与官方字节数一致（firered 838,589,068 / nano 750,635,863），
# 逐文件源的清单也用 Range 请求核对过（punct model.onnx 294,372,519 = 本地原文件大小）。
_MS = 'https://modelscope.cn/models/{repo}/resolve/master/{path}'
_GH = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/{tag}/{file}'

_MS_ASR_MODELS = 'csukuangfj/asr-models'
_MS_PUNCT_REPO = 'csukuangfj/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12'
# k2-fsa 版 silero_vad.onnx（629KB）没有国内镜像；manyeyes 的 snakers4 官方 v4 导出
# （1.72MB，支持 16k/8k）国内可取，sherpa-onnx 官方文档列为受支持版本
_MS_VAD_REPO = 'manyeyes/silero-vad-onnx'


def _ms(repo: str, path: str) -> str:
    return _MS.format(repo=repo, path=path)


def _gh(tag: str, file: str) -> str:
    return _GH.format(tag=tag, file=file)


_URL_FIRERED = (
    _ms(_MS_ASR_MODELS, 'sherpa-onnx-fire-red-asr2-zh_en-int8-2026-02-26.tar.bz2'),
    _gh('asr-models', 'sherpa-onnx-fire-red-asr2-zh_en-int8-2026-02-26.tar.bz2'),
)
# 标点模型走「逐文件」：镜像仓库把 model.onnx 单独发布（字节数与本地原文件一致）
_URL_PUNCT_MODEL = (
    _ms(_MS_PUNCT_REPO, 'model.onnx'),
)
_URL_VAD = (
    _ms(_MS_VAD_REPO, 'silero_vad.onnx'),
    _gh('asr-models', 'silero_vad.onnx'),
)

# 识别参数（60s 样本实测扫参：threads=3 + 6 路并行解码 RTF≈0.21，
# 比串行（RTF≈0.40）快 ~1.9x，文本逐字一致；threads 抬高反而劣化（16 线程 RTF 0.82）。
# env FIRERED_NUM_THREADS / FIRERED_DECODE_WORKERS 可调）
_NUM_THREADS = int(os.getenv('FIRERED_NUM_THREADS', '3'))
_DECODE_WORKERS = int(os.getenv('FIRERED_DECODE_WORKERS', '6'))
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


# ============ 引擎注册表（新增通道：加引擎只改这里） ============

@dataclass(frozen=True)
class Resolver:
    """文件解析器：`resolve()` 取实际命中的文件（缺则 None），`primary()` 取"期望路径"（用于展示缺失项）"""
    resolve: Callable[[], Optional[Path]]
    primary: Callable[[], Path]


def _abs(path_fn: Callable[[], Path]) -> Resolver:
    """绝对路径（模块级常量在调用时解析，保证运行时可替换/测试 monkeypatch 生效）"""
    def _resolve() -> Optional[Path]:
        p = path_fn()
        return p if _file_ok(p) else None
    return Resolver(_resolve, path_fn)


@dataclass(frozen=True)
class EnginePart:
    """一个关键模型文件：就绪判定与「文件明细」的最小单元"""
    key: str
    label: str
    resolver: Resolver


@dataclass(frozen=True)
class EngineArchive:
    """下载包；urls 按顺序回退（GitHub 失败自动试 ModelScope 镜像）

    `satisfied` 声明"这个包的内容是否已具备"：已具备则跳过下载；dest 已存在但又未具备
    （残缺目录）时**拒绝自动覆盖**——那多半是用户手动放置的模型，直接删掉太危险。
    """
    urls: Tuple[str, ...]
    label: str
    dest: Callable[[], Path]
    kind: str = 'tar'          # tar = 解包成目录（单一顶层目录改名为 dest）；file = 直接落位
    satisfied: Optional[Callable[[], bool]] = None


@dataclass(frozen=True)
class LocalEngine:
    key: str
    name: str
    languages: str
    note: str
    parts: Tuple[EnginePart, ...]
    removable: Tuple[Callable[[], Path], ...]
    archives: Tuple[EngineArchive, ...] = ()
    size_hint: str = ''
    fee: str = '免费'          # 本地引擎一律免费；单点字段，前端只显示不推断


def _firered_core_ready() -> bool:
    """FireRedASR 主包（三件套）是否已具备——决定主 tar 要不要重新下载"""
    return all(_file_ok(p) for p in (
        FIRERED_MODEL_DIR / 'encoder.int8.onnx',
        FIRERED_MODEL_DIR / 'decoder.int8.onnx',
        FIRERED_MODEL_DIR / 'tokens.txt',
    ))


def _firered_punct_ready() -> bool:
    """FireRedASR 标点模型包是否已具备"""
    return _file_ok(FIRERED_PUNCT_MODEL_DIR / 'model.onnx')


LOCAL_ENGINES: dict = {
    'firered': LocalEngine(
        key='firered',
        name='本地 FireRedASR',
        languages='中文 / 英文',
        note='FireRedASR2-AED（int8）中英最准，实测 RTF≈0.21；不支持说话人分离',
        parts=(
            EnginePart('encoder', '声学编码器', _abs(lambda: FIRERED_MODEL_DIR / 'encoder.int8.onnx')),
            EnginePart('decoder', '声学解码器', _abs(lambda: FIRERED_MODEL_DIR / 'decoder.int8.onnx')),
            EnginePart('tokens', '词表', _abs(lambda: FIRERED_MODEL_DIR / 'tokens.txt')),
            EnginePart('punct', '标点模型', _abs(lambda: FIRERED_PUNCT_MODEL_DIR / 'model.onnx')),
            EnginePart('vad', 'VAD 模型（静音检测）', _abs(lambda: FIRERED_VAD_MODEL_PATH)),
        ),
        removable=(
            lambda: FIRERED_MODEL_DIR,
            lambda: FIRERED_PUNCT_MODEL_DIR,
            lambda: FIRERED_VAD_MODEL_PATH,
        ),
        archives=(
            EngineArchive(_URL_FIRERED, 'FireRedASR 模型（838MB）',
                          lambda: FIRERED_MODEL_DIR, satisfied=_firered_core_ready),
            EngineArchive(_URL_PUNCT_MODEL, '标点模型（294MB）',
                          lambda: FIRERED_PUNCT_MODEL_DIR / 'model.onnx',
                          kind='file', satisfied=_firered_punct_ready),
            EngineArchive(_URL_VAD, 'VAD 模型（1.7MB）',
                          lambda: FIRERED_VAD_MODEL_PATH, kind='file'),
        ),
        size_hint='约 1.5GB',
    ),
}

# 引擎值白名单（写进 job options 的值；'local'=Whisper 由 transcriber 管，不在此表）
LOCAL_ENGINE_KEYS: Tuple[str, ...] = tuple(LOCAL_ENGINES)

# 引擎级互斥锁：模型常驻内存且解码吃满 CPU（实测 FireRed 峰值 ~1.7GB），
# MAX_CONCURRENT_JOBS=2 下并发会翻倍——按引擎串行化；Whisper 不受影响
_ENGINE_LOCKS = {key: threading.Lock() for key in LOCAL_ENGINES}


def engine_lock(key: str) -> threading.Lock:
    """取引擎互斥锁（解码串行化 + 删除前忙碌判断共用同一把锁）"""
    lock = _ENGINE_LOCKS.get(key)
    if lock is None:
        raise KeyError(f'未注册的本地引擎: {key}')
    return lock

# FireRed 的互斥锁沿用注册表里的同一把（firered_busy 与解码串行化共用，勿另建）
_FIRERED_LOCK = _ENGINE_LOCKS['firered']

# ── 模型下载状态（进程内单例：同一时刻只允许一个引擎在下载）──
# 记 engine 是因为「本地 ASR」页要显示哪个引擎在下；失败/中断后可重试，重启回 idle
_download_state: dict = {'status': 'idle', 'engine': '', 'percent': 0, 'message': '', 'error': ''}
_download_lock = threading.Lock()
# ★ B5：下载阶段的进程级互斥，跨「模型管理页后台下载」与「转录路径按需下载」共用。
#   两者抢同一个 *.tar.bz2.part 并发写会得到损坏的 tar（838MB 白费）。
_ensure_lock = threading.Lock()
_download_cancel = threading.Event()
_download_thread: Optional[threading.Thread] = None


def _file_ok(p: Path) -> bool:
    return p.is_file() and p.stat().st_size > 0


def _dir_size(path: Path) -> int:
    """文件/目录的字节数（不存在或无权访问按 0 计，不让状态接口整体失败）"""
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for p in path.rglob('*'):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def sherpa_available() -> bool:
    """sherpa-onnx 是否可导入（只查 spec，不真正 import，避免拖慢状态接口）"""
    try:
        return importlib.util.find_spec('sherpa_onnx') is not None
    except (ImportError, ValueError):
        return False


# ============ 通用引擎生命周期（新增通道：路由/前端只按 key 调用） ============
# 就绪判定/下载/删除的唯一入口都在本模块——不要绕过这些函数直接删/建模型文件
# （否则就绪判定会与实际文件漂移）。

def engine_ready(key: str) -> bool:
    """该引擎关键文件是否全部就位（缺任一即未就绪，禁止「文件在但残缺」当成可用）"""
    spec = LOCAL_ENGINES.get(key)
    if spec is None:
        return False
    return all(part.resolver.resolve() is not None for part in spec.parts)


def engine_busy(key: str) -> bool:
    """该引擎是否正在解码（删除模型文件前必须检查）"""
    lock = _ENGINE_LOCKS.get(key)
    return bool(lock and lock.locked())


def engine_part_status(key: str) -> List[dict]:
    """文件明细：每个关键文件的期望路径 / 是否命中 / 大小"""
    out: List[dict] = []
    for part in LOCAL_ENGINES[key].parts:
        hit = part.resolver.resolve()
        path = hit if hit is not None else part.resolver.primary()
        try:
            size = path.stat().st_size if hit is not None else 0
        except OSError:
            size = 0
        out.append({
            'key': part.key,
            'label': part.label,
            'path': str(path),
            'ready': hit is not None,
            'bytes': size,
        })
    return out


_IDLE_DOWNLOAD = {'status': 'idle', 'percent': 0, 'message': '', 'error': ''}


def engine_status(key: str) -> dict:
    """单个本地引擎的完整状态（模型管理页「本地 ASR」直接消费，前端不再自行推断）"""
    spec = LOCAL_ENGINES[key]
    removable = [fn() for fn in spec.removable]
    state = get_download_state()
    download = state if state.get('engine') == key else dict(_IDLE_DOWNLOAD, engine=key)
    return {
        'key': key,
        'name': spec.name,
        'fee': spec.fee,
        'languages': spec.languages,
        'note': spec.note,
        'size_hint': spec.size_hint,
        'ready': engine_ready(key),
        'busy': engine_busy(key),
        'bytes': sum(_dir_size(p) for p in removable),
        'dir': str(removable[0]) if removable else '',
        'parts': engine_part_status(key),
        'manual_hint': f'手动放置：{removable[0]}' if removable else '',
        'download': download,
    }


def list_engines_status() -> List[dict]:
    """全部已注册本地引擎的完整状态（含未下载的；含磁盘遍历，较重）"""
    return [engine_status(key) for key in LOCAL_ENGINES]


def engines_ready_map() -> dict:
    """{引擎值: 是否就绪}——只做文件存在性检查，不遍历磁盘大小。

    供工作台轻量状态接口（/api/asr/status）用：那里只要"能不能选"，不要占用统计。
    """
    return {key: engine_ready(key) for key in LOCAL_ENGINES}


def get_download_state() -> dict:
    """当前下载状态快照：{status: idle|downloading|done|error, engine, percent, message, error}"""
    with _download_lock:
        return dict(_download_state)


def _set_download_state(**kwargs) -> None:
    with _download_lock:
        _download_state.update(kwargs)


def start_engine_download(key: str) -> bool:
    """后台线程按需下载指定引擎的模型；已有下载在跑时返回 False。

    进度写入 `_download_state` 供前端轮询（长任务不能占住 HTTP 请求）。
    """
    global _download_thread
    if key not in LOCAL_ENGINES:
        raise KeyError(f'未注册的本地引擎: {key}')
    with _download_lock:
        if _download_state['status'] == 'downloading':
            return False
        _download_state.update({'status': 'downloading', 'engine': key, 'percent': 0,
                                'message': '准备下载...', 'error': ''})
    _download_cancel.clear()

    def _run() -> None:
        try:
            _ensure_engine_ready(
                key,
                progress_callback=lambda pct, msg: _set_download_state(
                    percent=max(0, min(int(pct), 100)), message=str(msg)),
                cancel_check=_download_cancel.is_set,
            )
        except TranscriptionCancelledError:
            logger.info(f'{key} 模型下载被取消')
            _set_download_state(status='idle', percent=0, message='下载已取消')
        except Exception as e:
            logger.error(f'{key} 模型下载失败: {e}')
            _set_download_state(status='error', message='下载失败', error=str(e))
        else:
            _set_download_state(status='done', percent=100, message='模型已就绪')

    _download_thread = threading.Thread(target=_run, name=f'{key}-model-download', daemon=True)
    _download_thread.start()
    return True


def cancel_model_download() -> bool:
    """请求取消下载（下载线程在下一个数据块边界退出）；未在下载返回 False。"""
    with _download_lock:
        running = _download_state['status'] == 'downloading'
    if running:
        _download_cancel.set()
    return running


def remove_engine(key: str) -> dict:
    """删除指定引擎的模型文件，返回释放字节数（下载中 / 解码中拒绝删除）。"""
    if key not in LOCAL_ENGINES:
        raise KeyError(f'未注册的本地引擎: {key}')
    with _download_lock:
        if _download_state['status'] == 'downloading' and _download_state.get('engine') == key:
            raise Exception('模型正在下载中，请先取消下载或等待完成再删除')
    if engine_busy(key):
        raise Exception(f'{LOCAL_ENGINES[key].name} 正在转录中，请等待当前任务结束再删除模型')
    removable = [fn() for fn in LOCAL_ENGINES[key].removable]
    freed = sum(_dir_size(p) for p in removable)
    for path in removable:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    with _download_lock:
        if _download_state.get('engine') == key:
            _download_state.update({'status': 'idle', 'engine': '', 'percent': 0,
                                    'message': '', 'error': ''})
    logger.info(f'已删除本地引擎 {key} 的模型文件，释放 {freed} 字节')
    return {'freed_bytes': freed, 'ready': engine_ready(key)}


# ── FireRed 兼容包装（旧 API 名；路由/测试/其它服务沿用，语义与改造前一致）──

def firered_model_ready() -> bool:
    """三件套（encoder/decoder/tokens）+ 标点模型 + VAD 模型全部就位。"""
    return engine_ready('firered')


def firered_busy() -> bool:
    """是否有 FireRedASR 转录正在跑（模型文件删除前必须检查）"""
    return engine_busy('firered')


def model_file_status() -> dict:
    """FireRedASR 文件明细 + 占用（兼容旧契约：files/dir/punct_dir/vad_path）"""
    parts = engine_part_status('firered')
    return {
        'ready': engine_ready('firered'),
        'files': parts,
        'parts': parts,
        'bytes': sum(_dir_size(fn()) for fn in LOCAL_ENGINES['firered'].removable),
        'dir': str(FIRERED_MODEL_DIR),
        'punct_dir': str(FIRERED_PUNCT_MODEL_DIR),
        'vad_path': str(FIRERED_VAD_MODEL_PATH),
        'sherpa_available': sherpa_available(),
    }


def start_model_download() -> bool:
    """（兼容旧名）下载 FireRedASR 模型"""
    return start_engine_download('firered')


def remove_model_files() -> dict:
    """（兼容旧名）删除 FireRedASR 模型文件"""
    return remove_engine('firered')


def _manual_hint(url: str, target: Path) -> str:
    return (
        f'也可手动下载（网络不通时）：{url} ，'
        f'解压/放置到 {target} 后重试'
    )


def _download_file(urls, dest: Path, label: str,
                   progress_callback=None, cancel_check=None,
                   pct_base: float = 0.0, pct_span: float = 1.0,
                   hint_target: Optional[Path] = None) -> None:
    """流式下载单文件到 dest（带字节级进度）。

    urls 可以是字符串或地址列表：多地址按顺序回退（ModelScope 优先、GitHub 兜底）。
    全部失败时上抛，文案含每个地址的手动下载指引（`hint_target` 用于 tar 场景——
    提示用户放到"解压后的目录"而不是那个临时 tar 路径）。
    """
    candidates = [urls] if isinstance(urls, str) else list(urls)
    hint_path = hint_target or dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + '.part')
    errors: List[str] = []
    for url in candidates:
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
            return
        except TranscriptionCancelledError:
            tmp.unlink(missing_ok=True)
            raise
        except Exception as e:
            tmp.unlink(missing_ok=True)
            logger.warning(f'下载 {label} 失败（{url}）: {type(e).__name__}: {e}')
            errors.append(f'{type(e).__name__}: {e}')
    hints = ' 或 '.join(_manual_hint(u, hint_path) for u in candidates)
    raise Exception(f'下载 {label} 失败（{"; ".join(errors)}）。{hints}')


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


def _archive_satisfied(arc: EngineArchive) -> bool:
    """该下载包的内容是否已具备。

    - 有 `satisfied` 声明用它（可精确到"包内容"级别，多包引擎必需）
    - 单文件包按 size>0 判断
    - 目录包且未声明：一律判「未具备」——目录存在却没通过引擎就绪判定 = 残缺目录，
      交给 `_ensure_engine_ready` 给出「请先删除或手动补全」的可行动提示（不静默覆盖）
    """
    if arc.satisfied is not None:
        return arc.satisfied()
    if arc.kind == 'file':
        return _file_ok(arc.dest())
    return False


def _ensure_engine_ready(key: str, progress_callback=None, cancel_check=None) -> None:
    """引擎模型就绪检查；缺失则按注册表 archives 依次下载（tar 解包 / 单文件落位）。

    全就位时零开销直接返回。**兜底链路绝不调用本函数**——只有用户显式选择该引擎且
    模型缺失时才触发下载（避免"回退"变成隐性 1GB 下载）。

    ★ B5 修复（2026-10-13）：下载阶段加模块级互斥 `_ensure_lock`。此前 `_download_lock`
    只保护后台下载的状态字典，**转录路径完全不查"是否已在下载"**——用户在模型管理页点
    下载（后台线程）与一个显式选 firered 的任务启动同时发生时，两边都判定
    `engine_ready() == False`，两个线程交错写同一个 `FIRERED_MODEL_DIR.tar.bz2.part`，
    `tmp.replace(dest)` 得到**损坏的 tar**，838MB 下载白费。
    转录路径现在会等待在跑的下载完成（可被 cancel_check 中断），而不是并行写。
    """
    if engine_ready(key):
        return
    spec = LOCAL_ENGINES[key]
    if progress_callback:
        progress_callback(1, f'{spec.name} 模型未就绪，开始按需下载（{spec.size_hint or "仅首次"}）...')

    if not _ensure_lock.acquire(blocking=False):
        # 已有下载在跑：等待（而不是并行写同一个 .part）
        if progress_callback:
            progress_callback(1, f'{spec.name} 模型正在下载中，等待完成...')
        logger.info(f'{key} 模型下载已被其它线程占用，等待中')
        while not _ensure_lock.acquire(timeout=0.5):
            if cancel_check and cancel_check():
                raise TranscriptionCancelledError('用户取消，模型下载等待中止')
    try:
        # 等锁期间可能已被另一个线程下完：再判定一次，避免重复下载
        if engine_ready(key):
            logger.info(f'{key} 模型已由其它下载完成，跳过重复下载')
            return
        logger.info(f'{key} 模型不完整，开始下载（{len(spec.archives)} 个包）')
        total_archives = max(len(spec.archives), 1)
        for idx, arc in enumerate(spec.archives):
            dest = arc.dest()
            if _archive_satisfied(arc):
                continue
            if arc.kind == 'tar' and dest.exists():
                # 目录在但内容不齐：不擅自删除（可能是用户手动放置的第三方/更大版本导出）
                raise Exception(
                    f'模型目录已存在但关键文件不完整：{dest}。'
                    f'请先删除该目录（或手动补全所需文件）后再点下载'
                )
            base = _PROGRESS_MODEL_MAX * idx / total_archives
            span = _PROGRESS_MODEL_MAX / total_archives
            if arc.kind == 'file':
                _download_file(arc.urls, dest, arc.label, progress_callback, cancel_check, base, span)
            else:
                archive = dest.parent / f'{dest.name}.tar.bz2'
                _download_file(arc.urls, archive, arc.label,
                               progress_callback, cancel_check, base, span, hint_target=dest)
                _extract_archive(archive, dest, arc.label)

        if not engine_ready(key):
            missing = [p['label'] for p in engine_part_status(key) if not p['ready']]
            raise Exception(
                f'模型下载完成但完整性校验未通过（缺失：{"、".join(missing) or "未知"}），'
                f'请检查磁盘空间或手动放置模型到 {spec.removable[0]()}'
            )
    finally:
        _ensure_lock.release()


def _ensure_model_ready(progress_callback=None, cancel_check=None) -> None:
    """（兼容旧名）确保 FireRedASR 模型就绪"""
    _ensure_engine_ready('firered', progress_callback, cancel_check)


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


def transcribe_chunk_text(audio_path: str, language: Optional[str] = None) -> str:
    """单个短音频片段（3~30s）→ 纯文本（云端分片失败补录用，不写 SRT/TXT）。

    与完整转录同一管线（ffmpeg→VAD→识别→标点），模型每次重建（加载 ~5s，
    补录是低频路径，不值得为此常驻 1.3GB 内存）。
    失败上抛，由调用方决定是否放弃该分片。

    ★ B6 修复（2026-10-13）：补录入口现在也取**同一把引擎锁** `_FIRERED_LOCK`。
    此前补录直接 `_create_recognizer`，既不加锁也不更新忙碌状态 →
    `engine_busy('firered')` 在补录期返回 False，`remove_engine` 会在补录进行中
    `shutil.rmtree` 掉模型目录，UI「正在转录中，请等待」这层保护形同虚设；
    同时补录的 1.3GB 模型与完整转录的模型可能同时在内存里（峰值翻倍）。
    锁是非重入的：调用方只有 cloud_asr._rescue_failed_chunks（不持锁），不会自死锁。
    """
    with _FIRERED_LOCK:
        sherpa = _load_sherpa()
        recognizer = _create_recognizer(sherpa)
        vad = _create_vad(sherpa)
        punct = _create_punct(sherpa)

        wav_tmp = _to_wav_16k_mono(audio_path)
        try:
            samples, sr = _read_wav_samples(wav_tmp)
            if len(samples) == 0:
                return ''
            raw = _detect_speech_segments(vad, samples, sr)
            speech_ranges = _pad_vad_boundaries(raw, len(samples), sr)
            parts: List[str] = []
            for seg_start, seg_end in speech_ranges:
                seg_samples = samples[int(seg_start * sr):int(seg_end * sr)]
                stream = recognizer.create_stream()
                stream.accept_waveform(sr, seg_samples)
                recognizer.decode_stream(stream)
                text = (stream.result.text or '').strip()
                if text:
                    parts.append(punct.add_punctuation(text))
            return ''.join(parts)
        finally:
            Path(wav_tmp).unlink(missing_ok=True)


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

            def _decode_segment(seg_start: float, seg_end: float) -> str:
                seg_samples = samples[int(seg_start * sr):int(seg_end * sr)]
                stream = recognizer.create_stream()
                stream.accept_waveform(sr, seg_samples)
                recognizer.decode_stream(stream)
                return (stream.result.text or '').strip()

            # 段间并行解码（ORT Run 释放 GIL，实测近线性）；标点在主线程串行补
            # （0.01s/段可忽略，且不确认 OfflinePunctuation 的并发安全性）
            results: List[Tuple[float, float, str]] = []
            processed = 0.0
            executor = ThreadPoolExecutor(max_workers=max(_DECODE_WORKERS, 1),
                                          thread_name_prefix='firered-decode')
            try:
                futures = [executor.submit(_decode_segment, s, e) for s, e in speech_ranges]
                for i, (seg_start, seg_end) in enumerate(speech_ranges):
                    if cancel_check and cancel_check():
                        raise TranscriptionCancelledError('用户取消，转录中止')
                    text = futures[i].result()
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
                executor.shutdown(wait=False, cancel_futures=True)
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


def transcribe_audio_local(engine: str, audio_path: str, **kwargs) -> Tuple[str, str, int]:
    """本地引擎**统一入口**（transcriber 只调这一个）。

    目前注册表里只有 `firered`（AED 管线：VAD + CT-Transformer 标点）。保留这层签名是为了
    job options 里的引擎值是通用 key，将来加引擎时 transcriber 侧零改动。
    """
    if engine == 'firered':
        return transcribe_audio_firered(audio_path, **kwargs)
    raise KeyError(f'未注册的本地引擎: {engine}')
