"""
httpx HTTP 错误的人类可读描述工具

背景（2026-09-09）：中转站/大模型 API 的 4xx/5xx 通常把真实原因放在响应体里
（如 model_not_found、密钥无效、余额不足），但 `resp.raise_for_status()` 抛出的
HTTPStatusError 只有 "Client error '404 Not Found' for url ..."，正文原因被丢掉，
用户只能对着裸状态码瞎猜。本模块统一从响应体提取 OpenAI 兼容错误信息
（error.message / message / detail / code），过滤 HTML 错误页、截断长文，
让报错"回音"服务端的真实原因。
"""
import json
import re
from typing import Any

import httpx

_MAX_BODY = 300  # 错误文案截断长度（按字符计）


def _looks_like_html(text: str) -> bool:
    """粗判响应是不是 HTML 错误页/防火墙拦截页（而非 API 错误报文）"""
    head = text[:500].lstrip().lower()
    return head.startswith('<!doctype') or '<html' in head


def _extract_message(data: Any) -> str:
    """从常见错误 JSON 结构提取 message（OpenAI 风格 error.message / 顶层 message / detail / code）"""
    if isinstance(data, dict):
        err = data.get('error')
        if isinstance(err, dict):
            for k in ('message', 'msg', 'detail'):
                v = err.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            # 没有 message 时至少给 code/type（如 model_not_found）
            for k in ('code', 'type'):
                v = err.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        elif isinstance(err, str) and err.strip():
            return err.strip()
        for k in ('message', 'msg'):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        detail = data.get('detail')
        if isinstance(detail, list) and detail and isinstance(detail[0], dict):
            # FastAPI 422 校验错误：{"detail": [{"msg": "..."}]}
            for k in ('msg', 'message'):
                v = detail[0].get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        code = data.get('code')
        if isinstance(code, str) and code.strip():
            return code.strip()
    elif isinstance(data, list) and data and isinstance(data[0], dict):
        for k in ('message', 'msg', 'detail'):
            v = data[0].get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ''


def describe_http_response(resp: httpx.Response, *, max_body: int = _MAX_BODY) -> str:
    """把一次 HTTP 响应描述成含服务端真实原因的单行文本。

    例：'HTTP 404: 当前分组暂不支持模型「gemini-3.8-flash-high」，请核对模型名称…'
    平时只回状态码；HTML 错误页给提示而不是倾倒整页；长文截断。
    """
    status = resp.status_code
    text = (resp.text or '').strip()
    if not text:
        return f'HTTP {status}: 服务端返回空响应'
    msg = ''
    try:
        data = json.loads(text)
        msg = _extract_message(data)
    except (ValueError, TypeError):
        msg = ''
    if not msg and _looks_like_html(text):
        return f'HTTP {status}: 响应是网页而非 API 错误（可能被防火墙拦截或需登录）'
    if not msg:
        msg = text
    msg = re.sub(r'\s+', ' ', msg).strip()
    if len(msg) > max_body:
        msg = msg[:max_body] + '…'
    return f'HTTP {status}: {msg}'


def describe_http_error(exc: httpx.HTTPStatusError, *, max_body: int = _MAX_BODY) -> str:
    """HTTPStatusError 形态的 describe_http_response（raise_for_status 时用）"""
    return describe_http_response(exc.response, max_body=max_body)
