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


@router.get("/status")
async def asr_status():
    """云端 ASR 配置状态（不含密钥）。

    - cloud_configured：MiMo（默认云端 provider）endpoint + key 齐全且开关打开
    - model / provider_host：MiMo 模型名与主机名（展示用）
    - free_configured / free_model：免费档 XingChen Ultra（cloud_free 引擎）可用状态
    - firered_ready：本地 FireRedASR 模型三件套+标点+VAD 是否就位（前端提示是否需下载）
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
    return {
        "cloud_configured": cloud_asr_configured(),
        "model": cfg['model'],
        "provider_host": host,
        "free_configured": cloud_asr_configured('xingchen'),
        "free_model": free_cfg['model'],
        "firered_ready": firered_model_ready(),
    }
