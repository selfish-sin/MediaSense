"""
YouTube Tool V2 - B站登录路由
使用统一的 bilibili_cookies 模块管理 cookie
"""
import asyncio
import json
import logging
import requests
import httpx
from fastapi import APIRouter, HTTPException

from services.bilibili_cookies import update_cookies, clear_cookies, get_status

logger = logging.getLogger(__name__)
router = APIRouter()

# 最简请求头（对齐v1版，太多头反而触发B站风控）
BILIBILI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


@router.get("/qr")
async def get_bilibili_qr():
    """获取B站登录二维码（完全对齐v1版requests实现）"""
    try:
        # 对齐v1：用同步 requests，包装成异步避免阻塞事件循环
        def _do():
            return requests.get(
                "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
                headers=BILIBILI_HEADERS,
                timeout=10,
            )
        resp = await asyncio.to_thread(_do)

        data = resp.json()
        if data.get('code') != 0:
            return {"error": data.get('message', '生成失败')}
        return {
            "url": data['data'].get('url', ''),
            "qrcode_key": data['data'].get('qrcode_key', ''),
        }

    except Exception as e:
        logger.error(f"获取二维码异常: {e}")
        return {"error": str(e)}


@router.get("/qr/check")
async def check_bilibili_qr(qrcode_key: str):
    """检查B站二维码扫码状态"""
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=BILIBILI_HEADERS) as client:
            resp = await client.get(
                "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
                params={"qrcode_key": qrcode_key}
            )
            if resp.status_code == 200:
                data = resp.json().get('data', {})
                code = data.get('code', -1)

                # 扫码成功时保存 cookies（统一模块）
                if code == 0:
                    refresh_token = data.get('refresh_token', '')
                    cookie_url = data.get('url', '')
                    
                    cookies_dict = {}
                    
                    # B站扫码成功后，cookie 参数在 url 的 query string 里
                    # url 格式: https://passport.biligame.com/.../crossDomain?DedeUserID=xxx&SESSDATA=xxx&bili_jct=xxx&...
                    if cookie_url:
                        from urllib.parse import urlparse, parse_qs
                        parsed = urlparse(cookie_url)
                        qs = parse_qs(parsed.query)
                        # 提取关键 cookie 字段
                        for key in ('SESSDATA', 'bili_jct', 'DedeUserID', 'DedeUserID__ckMd5', 'sid'):
                            if key in qs and qs[key]:
                                cookies_dict[key] = qs[key][0]
                    
                    logger.info(f'从URL提取到 {len(cookies_dict)} 个cookie: {list(cookies_dict.keys())}')
                    
                    if cookies_dict:
                        update_cookies(cookies_dict, refresh_token)
                    else:
                        # 兜底：仅记录字段名供调试（★ 不得打印完整 data，url 字段的 query 中含 SESSDATA/bili_jct 等凭证）
                        logger.warning(f'扫码成功但未提取到cookie，data字段: {list(data.keys())}, url是否存在: {bool(cookie_url)}')

                return {"code": code, "message": data.get('message', ''), "status": data.get('status', '')}
            return {"code": -1, "error": f"检查失败: {resp.status_code}"}
    except Exception as e:
        return {"code": -1, "error": str(e)}


@router.get("/status")
async def bilibili_status():
    """检查B站登录状态"""
    return get_status()


@router.post("/logout")
async def bilibili_logout():
    """B站登出"""
    clear_cookies()
    return {"success": True, "message": "已登出"}
