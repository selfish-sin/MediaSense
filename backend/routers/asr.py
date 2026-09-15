"""
YouTube Tool V2 - ASR（转录）状态路由（只读）

只读端点：工作台据此决定引擎可用性与默认值；「模型管理 → ASR 模型」页据此渲染总览。
写操作在 `routers/asr_admin.py`（同挂 /api/asr 前缀），两文件共享本模块的
`mask_provider` / `recommended_engine` / `_settings`（单一事实源，勿各写一份）。

不回原始密钥（只回脱敏串与"是否已配置"）。
"""
import asyncio
import logging
from urllib.parse import urlparse

from fastapi import APIRouter

from db import asr_repo
from services.cloud_asr import _asr_cfg, _provider_cfg, cloud_asr_configured, provider_for_engine

logger = logging.getLogger(__name__)
router = APIRouter()


def mask_provider(row: dict) -> dict:
    """provider 脱敏 + 派生状态（前端只消费这些字段，不再自行判断可用性）。

    - api_key：前 4 + **** + 后 4（长度 ≤8 全遮蔽）
    - key_broken：库里存的就是脱敏串（历史污染，需重新粘贴真密钥）
    - configured：启用 + 端点 + 密钥齐全（即能否真正发起调用）
    - runtime：该 provider 是否为所在槽位当前实际使用者
      （槽位内 enabled 且 sort_order 最小者，见 asr_repo.get_provider_for_engine）
    """
    out = dict(row)
    raw = out.get('api_key') or ''
    out['has_key'] = bool(raw)
    out['key_broken'] = '****' in raw
    if len(raw) > 8:
        out['api_key'] = raw[:4] + '****' + raw[-4:]
    elif raw:
        out['api_key'] = '****'
    out['enabled'] = bool(out.get('enabled'))
    out['is_builtin'] = bool(out.get('is_builtin'))
    out['configured'] = out['enabled'] and bool(out.get('endpoint')) and bool(raw)
    out['runtime'] = provider_for_engine(out.get('engine', '')) == out.get('key')
    # 收费标识单点在这里给出（前端只显示不推断）：cloud=收费 / cloud_free=免费
    out['fee'] = '免费' if out.get('engine') == 'cloud_free' else '收费'
    return out


def recommended_engine() -> str:
    """默认转录引擎推荐（前端默认值单点；**不改变**运行时回退链 _FALLBACK_CHAINS）。

    优先级：
    ① 用户在管理页显式设置的 `asr_default_engine`——但可用性约束下**只降不升**
       （设了免费档而它不可用 → 落 local，绝不自动转付费，与回退链成本不变量一致）
    ② 未设置 → 免费档已配置走 `cloud_free`（"免费使用"是默认路径）
    ③ 否则付费档已配置走 `cloud`
    ④ 都不可用走 `local`（Whisper 保底；`firered` 不参与**自动**推荐，避免默认触发 1.1GB 下载）
    """
    cloud_key = provider_for_engine('cloud')
    free_key = provider_for_engine('cloud_free')
    cloud_ok = bool(cloud_key) and cloud_asr_configured(cloud_key)
    free_ok = bool(free_key) and cloud_asr_configured(free_key)

    user_default = asr_repo.get_default_engine()
    if user_default in ('firered', 'local'):
        return user_default
    if user_default == 'cloud_free':
        return 'cloud_free' if free_ok else 'local'
    if user_default == 'cloud':
        if cloud_ok:
            return 'cloud'
        return 'cloud_free' if free_ok else 'local'
    if free_ok:
        return 'cloud_free'
    if cloud_ok:
        return 'cloud'
    return 'local'


def _settings() -> dict:
    """转录默认设置（app_settings 三项）"""
    return {
        'default_engine': asr_repo.get_default_engine(),
        'whisper_model': asr_repo.get_whisper_model(),
        'whisper_device': asr_repo.get_whisper_device(),
    }


def _local_engines_brief() -> list:
    """工作台引擎下拉用：本地引擎的 值 / 显示名 / 是否就绪（**不遍历磁盘大小**）。"""
    from services import local_asr
    from services.transcriber import any_whisper_ready

    out = [{'key': 'local', 'name': '本地 Whisper', 'ready': any_whisper_ready()}]
    for key, spec in local_asr.LOCAL_ENGINES.items():
        out.append({'key': key, 'name': spec.name, 'ready': local_asr.engine_ready(key)})
    return out


def _whisper_entry() -> dict:
    """Whisper 引擎条目（4 档不属引擎注册表：它是独立模型族，档位明细在 whisper 段单列）"""
    from config import LOCAL_MODEL_DIRS
    from services.transcriber import list_local_models

    models = list_local_models()
    base_dir = LOCAL_MODEL_DIRS.get('base') or next(iter(LOCAL_MODEL_DIRS.values()))
    return {
        'key': 'local',
        'name': '本地 Whisper',
        'fee': '免费',
        'languages': '多语种（99 种）',
        'note': '末位兜底引擎：任何引擎失败都可回退到它；档位与设备在下方单独设置',
        'size_hint': '按档位（base ≈ 150MB / medium ≈ 1.5GB）',
        'ready': any(m['ready'] for m in models),
        'busy': False,                     # Whisper 不串行化，无引擎级锁
        'bytes': sum(m['bytes'] for m in models),
        'dir': str(base_dir.parent),
        'parts': [],                       # 档位明细见 whisper 段
        'manual_hint': '手动放置：models/whisper_<档位>/（需 model.bin + tokenizer.json）',
        'download': {'status': 'idle', 'percent': 0, 'message': '', 'error': ''},
    }


def _local_status() -> dict:
    """本地 ASR 状态：引擎列表（含免费标识/就绪/占用/文件明细/下载态）+ Whisper 档位明细。

    含磁盘遍历，调用方必须放进线程池。
    """
    from services import local_asr
    from services.transcriber import list_local_models

    return {
        'engines': [_whisper_entry()] + local_asr.list_engines_status(),
        'sherpa_available': local_asr.sherpa_available(),
        'whisper': {
            'models': list_local_models(),
            'default_model': asr_repo.get_whisper_model(),
            'device': asr_repo.get_whisper_device(),
        },
    }


@router.get("/status")
async def asr_status():
    """转录引擎配置状态（不含密钥）——工作台可用性与默认值的单点来源。

    - cloud_configured / provider_name / model / provider_host：付费档（cloud 槽位）运行时 provider
    - free_configured / free_provider_name / free_model：免费档（cloud_free 槽位）运行时 provider
    - firered_ready：本地 FireRedASR 模型是否就位（前端提示是否需下载）
    - recommended_engine：默认引擎推荐（用户设置优先，其次免费优先；前端只消费，不自行降级）
    - default_engine：用户显式设置的默认引擎（空串 = 未设置）
    """
    from services import local_asr
    from services.transcriber import any_whisper_ready

    cloud_key = provider_for_engine('cloud')
    free_key = provider_for_engine('cloud_free')
    cfg = _asr_cfg() if cloud_key else _provider_cfg('mimo')
    free_cfg = _provider_cfg(free_key) if free_key else {'model': '', 'name': ''}
    try:
        host = urlparse(cfg.get('endpoint') or '').hostname or ''
    except ValueError:
        host = ''
    return {
        "cloud_configured": bool(cloud_key) and cloud_asr_configured(cloud_key),
        "provider_name": cfg.get('name', ''),
        "model": cfg.get('model', ''),
        "provider_host": host,
        "free_configured": bool(free_key) and cloud_asr_configured(free_key),
        "free_provider_name": free_cfg.get('name', ''),
        "free_model": free_cfg.get('model', ''),
        "firered_ready": local_asr.engine_ready('firered'),
        "recommended_engine": recommended_engine(),
        "default_engine": asr_repo.get_default_engine(),
        # 本地下拉按就绪态展示（工作台引擎下拉用；只做文件存在性检查，不遍历磁盘大小）
        "local_engines": _local_engines_brief(),
    }


@router.get("/overview")
async def asr_overview():
    """ASR 模型域总览：云端 provider 列表 + 本地模型状态 + 设置 + 推荐引擎。

    一次喂满管理页（避免前端多次请求）；磁盘遍历在线程池执行，不阻塞事件循环。
    """
    local = await asyncio.to_thread(_local_status)
    return {
        'providers': [mask_provider(p) for p in asr_repo.list_providers()],
        'local': local,
        'settings': _settings(),
        'recommended_engine': recommended_engine(),
    }
