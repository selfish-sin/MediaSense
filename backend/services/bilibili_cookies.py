"""
YouTube Tool V2 - B站 Cookie 统一管理
bilibili.py 和 downloader.py 共用同一份 cookie 存储
避免双份全局变量不一致的问题
"""
import json
import logging
from pathlib import Path
from typing import Dict, Optional, Any

from config import BILIBILI_COOKIE_FILE

logger = logging.getLogger(__name__)

# 全局唯一 cookies 存储（整个进程共享）
_bilibili_cookies: Dict[str, str] = {}


def load_cookies() -> Optional[Dict[str, str]]:
    """加载B站cookies：优先内存 → 文件"""
    if _bilibili_cookies:
        logger.info(f'使用内存中的 {len(_bilibili_cookies)} 个B站cookie')
        return dict(_bilibili_cookies)
    if BILIBILI_COOKIE_FILE.exists():
        try:
            with open(BILIBILI_COOKIE_FILE, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            if cookies:
                _bilibili_cookies.update(cookies)
                logger.info(f'从文件加载了 {len(cookies)} 个B站cookie')
                return dict(_bilibili_cookies)
        except Exception as e:
            logger.error(f'Cookie文件读取失败: {e}')
    return None


def save_cookies(cookies: Dict[str, str]):
    """保存cookies到内存+文件"""
    _bilibili_cookies.update(cookies)
    with open(BILIBILI_COOKIE_FILE, 'w', encoding='utf-8') as f:
        json.dump(_bilibili_cookies, f, ensure_ascii=False, indent=2)
    logger.info(f'保存了 {len(_bilibili_cookies)} 个B站cookie')


def update_cookies(cookies_data: Dict[str, str], refresh_token: str = ''):
    """扫码登录后更新cookies"""
    _bilibili_cookies.update(cookies_data)
    if refresh_token:
        _bilibili_cookies['refresh_token'] = refresh_token
    with open(BILIBILI_COOKIE_FILE, 'w', encoding='utf-8') as f:
        json.dump(_bilibili_cookies, f, ensure_ascii=False, indent=2)
    logger.info(f'扫码登录更新了 {len(_bilibili_cookies)} 个B站cookie')


def clear_cookies():
    """清除所有B站cookies"""
    _bilibili_cookies.clear()
    if BILIBILI_COOKIE_FILE.exists():
        try:
            BILIBILI_COOKIE_FILE.unlink()
        except OSError:
            pass
    logger.info('B站cookies已清除')


def get_status() -> Dict[str, Any]:
    """查询B站登录状态"""
    return {
        "logged_in": bool(_bilibili_cookies),
        "cookies_count": len(_bilibili_cookies),
    }


# 启动时自动加载
load_cookies()
