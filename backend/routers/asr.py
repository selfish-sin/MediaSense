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

    - cloud_configured：endpoint + key 齐全且开关打开（可用）
    - model：云端模型名（透传给前端展示）
    - provider_host：端点主机名（前端展示用，不回完整 URL 避免歧义）
    """
    cfg = _asr_cfg()
    host = ''
    try:
        host = urlparse(cfg['endpoint']).hostname or ''
    except Exception:
        host = ''
    return {
        "cloud_configured": cloud_asr_configured(),
        "model": cfg['model'],
        "provider_host": host,
    }
