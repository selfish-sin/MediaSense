"""
YouTube Tool V2 - ASR 管理路由（写操作，与 routers/asr.py 同挂 /api/asr 前缀）

- 云端 provider CRUD 与测试连接
- 转录默认设置（默认引擎 / Whisper 档位 / 转录设备）
- 本地模型生命周期（FireRedASR 下载·取消·删除；Whisper 档位删除）

约定（与 AI 模型管理一致，勿破坏）：
- provider 配置落 `asr_providers` 表，运行时经 `cloud_asr._provider_cfg` 读取 → 改配置无需重启服务
- 密钥不回显；编辑时留空**或含 ****** 都表示"不修改"（防脱敏串回写覆盖真密钥）
- 删除/覆盖类操作走显式 HTTP 方法 + 前端二次确认；失败必须给可行动文案
- 长任务（下载、磁盘遍历、真实探测调用）一律放线程池，禁止阻塞事件循环
"""
import asyncio
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from config import LOCAL_MODEL_DIRS
from db import asr_repo
from models.schemas import AsrProviderUpsert, AsrSettingsUpdate
from routers.asr import _settings, recommended_engine

logger = logging.getLogger(__name__)
router = APIRouter()

# provider 标识：小写字母/数字开头，后接小写字母/数字/下划线/短横线（1~40 字符）
_KEY_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,39}$')

# 测试连接探测音频时长（秒）：够短（几乎不计费、快）又够长（避免服务端"音频过短"拒绝）
_PROBE_SECONDS = 2.0
# 测试连接单次超时（秒）：探测链路要快失败，不等生产用的 180s
_PROBE_TIMEOUT = 30.0


def _human_bytes(n: int) -> str:
    size = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size < 1024 or unit == 'TB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} TB'


def _validate(body: AsrProviderUpsert) -> None:
    """入参业务校验（pydantic 只保证类型）"""
    if body.engine not in asr_repo.VALID_ENGINES:
        raise HTTPException(status_code=400, detail=(
            f"引擎槽位非法：{body.engine}（可选 {' / '.join(asr_repo.VALID_ENGINES)}）"))
    if body.api not in asr_repo.VALID_APIS:
        raise HTTPException(status_code=400, detail=(
            f"请求姿势非法：{body.api}（可选 {' / '.join(asr_repo.VALID_APIS)}）"))
    if not (body.name or '').strip():
        raise HTTPException(status_code=400, detail='名称不能为空')
    if not (body.endpoint or '').strip():
        raise HTTPException(
            status_code=400,
            detail='端点不能为空（如 https://api.example.com/v1/chat/completions）')


# ============ 云端 provider CRUD ============

@router.post("/providers")
async def add_provider_api(body: AsrProviderUpsert):
    """新增云端 provider"""
    _validate(body)
    key = (body.key or '').strip().lower()
    if not _KEY_RE.match(key):
        raise HTTPException(
            status_code=400,
            detail='标识只允许小写字母/数字/下划线/短横线，且需以字母或数字开头（1~40 字符）')
    if asr_repo.get_provider(key):
        raise HTTPException(
            status_code=400,
            detail=f"标识「{key}」已存在（如需修改端点/密钥请直接编辑该 provider）")
    ok = asr_repo.add_provider(
        key=key, name=body.name.strip(), engine=body.engine, api=body.api,
        endpoint=body.endpoint.strip(), api_key=(body.api_key or '').strip(),
        model=(body.model or '').strip(), enabled=body.enabled,
        concurrency=body.concurrency, timeout=body.timeout,
        sort_order=body.sort_order, remark=(body.remark or '').strip(),
    )
    if not ok:
        raise HTTPException(status_code=400, detail='添加失败，请查看后端日志（backend.log）确认原因')
    return {"success": True, "message": f"已添加 provider「{body.name.strip()}」"}


@router.put("/providers/{key}")
async def update_provider_api(key: str, body: AsrProviderUpsert):
    """更新云端 provider（api_key 留空/含 **** = 保留原密钥）"""
    existing = asr_repo.get_provider(key)
    if not existing:
        raise HTTPException(status_code=404, detail=f"provider「{key}」不存在")
    _validate(body)

    # ★ 防脱敏串回写污染：编辑态密钥框刻意留空（不回填脱敏串）；若前端误回传脱敏串
    #   （含 ****）同样视为"不修改"，否则会把真密钥覆盖成废串导致全部 401
    new_api_key = (body.api_key or '').strip()
    if not new_api_key or '****' in new_api_key:
        new_api_key = existing.get('api_key') or ''

    ok = asr_repo.update_provider(
        key=key, name=body.name.strip(), engine=body.engine, api=body.api,
        endpoint=body.endpoint.strip(), api_key=new_api_key,
        model=(body.model or '').strip(), enabled=body.enabled,
        concurrency=body.concurrency, timeout=body.timeout,
        sort_order=body.sort_order, remark=(body.remark or '').strip(),
    )
    if not ok:
        raise HTTPException(status_code=400, detail='更新失败，请查看后端日志（backend.log）确认原因')
    return {"success": True, "message": f"provider「{key}」已更新"}


@router.delete("/providers/{key}")
async def delete_provider_api(key: str, request: Request):
    """删除云端 provider（内置不可删，可停用/改配置；破坏性：仅本机可发起）"""
    from routers.jobs import require_local_client
    require_local_client(request, what=f'删除 provider「{key}」')
    existing = asr_repo.get_provider(key)
    if not existing:
        raise HTTPException(status_code=404, detail=f"provider「{key}」不存在")
    if existing.get('is_builtin'):
        raise HTTPException(
            status_code=400,
            detail=f"内置 provider「{existing.get('name') or key}」不可删除"
                   f"（可改配置或停用；停用后该槽位自动改用下一个可用 provider）")
    if not asr_repo.delete_provider(key):
        raise HTTPException(status_code=400, detail='删除失败，请查看后端日志（backend.log）')
    return {"success": True, "message": f"provider「{key}」已删除"}


# ============ 测试连接 ============

def _make_probe_audio() -> str:
    """用项目自带 ffmpeg 生成 2 秒 440Hz 正弦波探测音频。

    不用真实录音：仓库不存媒体；正弦波足以验证「端点可达 + 密钥有效 + 模型存在」
    三段链路，且没有语音内容时返回空文本属正常现象。
    """
    from services.cloud_asr import _ffmpeg_bin
    fd, path = tempfile.mkstemp(suffix='.mp3', prefix='asr_probe_')
    os.close(fd)
    Path(path).unlink(missing_ok=True)  # 只要路径名，ffmpeg 自己写
    cmd = [
        _ffmpeg_bin(), '-y', '-v', 'error',
        '-f', 'lavfi', '-i', f'sine=frequency=440:duration={_PROBE_SECONDS}',
        '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame', '-b:a', '48k', path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0 or not Path(path).is_file():
        raise RuntimeError(
            f'探测音频生成失败（ffmpeg exit {proc.returncode}）：{proc.stderr[-200:]}')
    return path


@router.post("/providers/{key}/test")
async def test_provider_api(key: str):
    """真实调用一次云端 ASR（2 秒探测音频）验证链路。

    与 AI 模型测试连接同契约：HTTP 200 + success/error 字段（调用方据 success 判断）。
    探测音频无人声，`text` 为空属正常；401/403/404 与重试耗尽的原因原样透出。
    """
    from services.cloud_asr import (
        CloudASRConfigError,
        CloudASRUnavailableError,
        provider_label,
        transcribe_file,
    )

    existing = asr_repo.get_provider(key)
    if not existing:
        raise HTTPException(status_code=404, detail=f"provider「{key}」不存在")

    label = provider_label(key)
    start = time.time()
    probe = None
    try:
        probe = await asyncio.to_thread(_make_probe_audio)
        text = await asyncio.to_thread(
            transcribe_file, probe, _PROBE_TIMEOUT, 1, key)
    except (CloudASRConfigError, CloudASRUnavailableError) as e:
        return {"success": False, "error": str(e),
                "latency_ms": int((time.time() - start) * 1000)}
    except Exception as e:
        logger.error(f'测试连接 {key} 失败: {type(e).__name__}: {e}', exc_info=True)
        return {"success": False, "error": f'{type(e).__name__}: {e}',
                "latency_ms": int((time.time() - start) * 1000)}
    finally:
        if probe:
            Path(probe).unlink(missing_ok=True)

    return {
        "success": True,
        "text": text,
        "latency_ms": int((time.time() - start) * 1000),
        "model": existing.get('model') or '',
        "endpoint": existing.get('endpoint') or '',
        "api": existing.get('api') or 'chat',
        "note": f'{label} 链路正常' + ('（探测音频无人声，未返回文本属正常）' if not text else ''),
    }


# ============ 转录默认设置 ============

@router.post("/settings")
async def update_settings_api(body: AsrSettingsUpdate):
    """更新转录默认设置（只提交要改的字段；非法值显式报错，不静默忽略）

    ★ 2026-10-13 修复（原子性）：此前边校验边写入——`default_engine` 已落库、
      `whisper_model` 才判非法 → 接口回 400「保存失败」，但默认引擎其实已经改了并对
      后续转录生效，形成"报错但配置已变"的幽灵状态。
      现在**先集中校验全部字段，全部通过才统一写入**，失败时保证零副作用。
      （写入仍逐项调用 set_* ——它们各自管一条 app_settings 键，语义不变。）
    """
    errors = []
    # ── 第一步：纯校验，不碰数据库 ──
    if body.default_engine is not None and body.default_engine \
            and body.default_engine not in asr_repo.VALID_TRANSCRIBE_ENGINES:
        errors.append(f"默认引擎非法：{body.default_engine}"
                      f"（可选 {' / '.join(asr_repo.VALID_TRANSCRIBE_ENGINES)}；传空串可清除设置）")
    if body.whisper_model is not None and body.whisper_model not in asr_repo.VALID_WHISPER_SIZES:
        errors.append(f"Whisper 档位非法：{body.whisper_model}"
                      f"（可选 {' / '.join(asr_repo.VALID_WHISPER_SIZES)}）")
    if body.whisper_device is not None and body.whisper_device not in asr_repo.VALID_WHISPER_DEVICES:
        errors.append(f"转录设备非法：{body.whisper_device}"
                      f"（可选 {' / '.join(asr_repo.VALID_WHISPER_DEVICES)}）")
    if errors:
        raise HTTPException(status_code=400, detail='；'.join(errors))

    # ── 第二步：全部合法才写入 ──
    if body.default_engine is not None and not asr_repo.set_default_engine(body.default_engine):
        errors.append("默认引擎写入失败")
    if body.whisper_model is not None and not asr_repo.set_whisper_model(body.whisper_model):
        errors.append("Whisper 档位写入失败")
    if body.whisper_device is not None and not asr_repo.set_whisper_device(body.whisper_device):
        errors.append("转录设备写入失败")
    if errors:
        raise HTTPException(status_code=500, detail='；'.join(errors))
    return {
        "success": True,
        "settings": _settings(),
        "recommended_engine": recommended_engine(),
    }


# ============ 本地模型生命周期（按引擎 key 通用；engine ∈ local_asr.LOCAL_ENGINES） ============
# 引擎清单是单一事实源（services/local_asr.LOCAL_ENGINES）：新增本地引擎无需改本文件。
# 旧路径 /local/firered/* 天然被 {engine} 通配命中，前端与外部脚本无需改动。

def _require_engine(engine: str):
    """校验引擎已注册（未注册 → 404 并列出可选值）"""
    from services import local_asr
    spec = local_asr.LOCAL_ENGINES.get(engine)
    if spec is None:
        raise HTTPException(
            status_code=404,
            detail=f"未注册的本地引擎「{engine}」"
                   f"（可选 {' / '.join(local_asr.LOCAL_ENGINE_KEYS)}）")
    return spec


@router.get("/local/{engine}/download")
async def engine_download_state(engine: str):
    """下载状态（轻量轮询端点；总览含磁盘遍历，轮询请用这个）"""
    from services import local_asr
    _require_engine(engine)
    return {
        "engine": engine,
        "download": local_asr.engine_status(engine)['download'],
        "ready": local_asr.engine_ready(engine),
        "busy": local_asr.engine_busy(engine),
    }


@router.post("/local/{engine}/download")
async def start_engine_download_api(engine: str):
    """开始后台下载该引擎缺失的模型（进度轮询上面那个端点；不占 HTTP 长连接）"""
    from services import local_asr
    spec = _require_engine(engine)
    if local_asr.engine_ready(engine):
        return {"started": False, "message": f"{spec.name} 模型已就绪，无需下载"}
    if not local_asr.start_engine_download(engine):
        raise HTTPException(status_code=409, detail='已有模型正在下载中，请等待完成或先取消')
    return {
        "started": True,
        "message": f"已开始后台下载 {spec.name}（{spec.size_hint or '首次下载'}），可在本页查看进度",
    }


@router.post("/local/{engine}/cancel")
async def cancel_engine_download_api(engine: str):
    """请求取消下载（在下一个数据块边界生效）"""
    from services import local_asr
    _require_engine(engine)
    cancelled = local_asr.cancel_model_download()
    return {
        "success": True,
        "cancel_requested": cancelled,
        "message": "已请求取消下载" if cancelled else "当前没有进行中的下载",
    }


@router.delete("/local/{engine}")
async def delete_engine_model(engine: str, request: Request):
    """删除该引擎的模型文件（释放磁盘；下载中 / 转录中一律拒绝；破坏性：仅本机可发起）"""
    from routers.jobs import require_local_client
    require_local_client(request, what=f'删除 {engine} 本地模型文件')
    from services import local_asr
    spec = _require_engine(engine)
    try:
        result = await asyncio.to_thread(local_asr.remove_engine, engine)
    except Exception as e:
        raise HTTPException(status_code=409, detail=f'删除失败：{e}')
    return {
        "success": True,
        "freed_bytes": result['freed_bytes'],
        "message": f"已删除 {spec.name} 模型，释放 {_human_bytes(result['freed_bytes'])}",
    }


@router.delete("/local/whisper/{size}")
async def delete_whisper_model(size: str, request: Request):
    """删除本地 Whisper 指定档位模型目录（释放磁盘；破坏性：仅本机可发起）"""
    from routers.jobs import require_local_client
    require_local_client(request, what=f'删除 Whisper {size} 模型目录')
    from services.transcriber import remove_local_model

    if size not in asr_repo.VALID_WHISPER_SIZES:
        raise HTTPException(
            status_code=400,
            detail=f"未知档位：{size}（可选 {' / '.join(asr_repo.VALID_WHISPER_SIZES)}）")
    path = LOCAL_MODEL_DIRS.get(size)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail=f'本地没有 {size} 档模型（无需删除）')
    try:
        result = await asyncio.to_thread(remove_local_model, size)
    except Exception as e:
        logger.error(f'删除本地 Whisper {size} 失败: {e}', exc_info=True)
        raise HTTPException(status_code=500, detail=f'删除失败：{e}')
    return {
        "success": True,
        "freed_bytes": result['freed_bytes'],
        "message": f"已删除本地 Whisper（{size}），释放 {_human_bytes(result['freed_bytes'])}",
    }
