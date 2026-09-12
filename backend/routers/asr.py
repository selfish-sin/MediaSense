"""
YouTube Tool V2 - 云端 ASR 状态路由

只读状态端点：前端据此决定「转录引擎」云端选项是否可用。
不回密钥（只回配置状态与模型名），无写操作。
"""
import logging
from urllib.parse import urlparse

from fastapi import APIRouter

from services.cloud_asr import cloud_asr_configured, _asr_cfg

logger = logging.getLogger(__name__)
router = APIRouter()


def _recommended_engine(free_configured: bool, cloud_configured: bool) -> str:
    """默认转录引擎推荐（策略单点，前端只消费；不改变运行时回退链 _FALLBACK_CHAINS）。

    - 免费档已配置 → cloud_free（"免费使用"是默认路径）
    - 否则付费档已配置 → cloud（用户配置了付费 Key，接受付费兜底）
    - 都不可用 → local（本地 Whisper 保底；firered 不参与推荐——避免默认触发 1.1GB 模型下载）
    """
    if free_configured:
        return 'cloud_free'
    if cloud_configured:
        return 'cloud'
    return 'local'


@router.get("/status")
async def asr_status():
    """云端 ASR 配置状态（不含密钥）。

    - cloud_configured：MiMo（默认云端 provider）endpoint + key 齐全且开关打开
    - model / provider_host：MiMo 模型名与主机名（展示用）
    - free_configured / free_model：免费档 XingChen Ultra（cloud_free 引擎）可用状态
    - firered_ready：本地 FireRedASR 模型三件套+标点+VAD 是否就位（前端提示是否需下载）
    - recommended_engine：默认引擎推荐（免费优先；前端默认值单点，见 _recommended_engine）
    """
    from services.cloud_asr import _provider_cfg
    from services.local_asr import firered_model_ready

    cfg = _asr_cfg()
    free_cfg = _provider_cfg('xingchen')
    host = ''
    try:
        host = urlparse(cfg['endpoint']).hostname or ''
    except Exception:
        host = ''
    cloud_configured = cloud_asr_configured()
    free_configured = cloud_asr_configured('xingchen')
    return {
        "cloud_configured": cloud_configured,
        "model": cfg['model'],
        "provider_host": host,
        "free_configured": free_configured,
        "free_model": free_cfg['model'],
        "firered_ready": firered_model_ready(),
        "recommended_engine": _recommended_engine(free_configured, cloud_configured),
    }
