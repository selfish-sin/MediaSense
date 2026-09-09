"""
YouTube Tool V2 - AI模型管理路由
"""
import json
import logging
import os
import ssl
import time
import httpx
from fastapi import APIRouter, HTTPException

from models.schemas import ModelConfigCreate, ModelConfigResponse, ModelTestRequest, ModelTestResponse
from db.model_repo import get_all_models, get_model_by_key, add_model, update_model, delete_model, get_default_model_key, set_default_model
from services.httpx_errors import describe_http_response

logger = logging.getLogger(__name__)
router = APIRouter()


def _extract_api_error(text: str) -> str:
    """从服务端响应里提取人类可读的拒绝原因（OpenAI 兼容错误结构），截断防刷屏"""
    text = (text or '').strip()
    if not text:
        return '空响应'
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return text[:120]
    err = data.get('error') if isinstance(data, dict) else None
    if isinstance(err, dict):
        for k in ('message', 'msg'):
            if err.get(k):
                return str(err[k])[:200]
    if isinstance(data, dict):
        for k in ('message', 'msg'):
            if data.get(k):
                return str(data[k])[:200]
    return text[:120]


def _is_ssl_cert_error(e: Exception) -> bool:
    """自签名证书/证书信任失败（CERTIFICATE_VERIFY_FAILED）——服务器侧信任问题，非用户输入错误"""
    return isinstance(e, ssl.SSLCertVerificationError) or 'CERTIFICATE_VERIFY_FAILED' in str(e)


def _client_verify(model_cfg: dict) -> bool:
    """模型配置勾选 skip_ssl_verify（自签名证书/IP 直连中转）时关闭 httpx 证书校验"""
    return not bool((model_cfg or {}).get('skip_ssl_verify'))


def _normalize_endpoint(endpoint: str) -> str:
    """OpenAI 兼容端点补全：统一规范化为 /v1/chat/completions（幂等）。

    用户手填的格式一般不带 /v1（如 api.deepseek.com/chat/completions），
    自动补全时统一加回 /v1；已含 /v1/chat/completions 的完整端点原样保留。
    """
    url = (endpoint or '').strip().rstrip('/')
    if not url:
        return url
    if '/chat/completions' in url:
        if '/v1/chat/completions' in url:
            return url
        return url.replace('/chat/completions', '/v1/chat/completions')
    url = url[:-3] if url.endswith('/v1') else url
    return url + '/v1/chat/completions'


@router.get("")
async def list_models():
    """获取所有模型配置"""
    models = get_all_models()
    for m in models:
        raw_key = m.get('api_key')
        # ★ 密钥被星号污染标记：必须在脱敏前用原始值判断——历史 bug 曾把脱敏串存进 DB，
        #   含 **** 的 key 必然失效，前端据此提示用户重新粘贴真实密钥
        m['key_broken'] = bool(raw_key) and '****' in raw_key
        if raw_key and len(raw_key) > 8:
            m['api_key'] = raw_key[:4] + '****' + raw_key[-4:]
    return {"models": models, "default": get_default_model_key()}


@router.post("/default")
async def set_default_model_api(body: dict):
    """设置默认模型（校验模型存在）"""
    key = (body or {}).get('key', '')
    if not key:
        raise HTTPException(status_code=400, detail="缺少 key 参数")
    if not set_default_model(key):
        raise HTTPException(status_code=404, detail=f"模型 {key} 不存在")
    return {"success": True, "default": key, "message": f"默认模型已设为 {key}"}


@router.post("")
async def add_model_api(model: ModelConfigCreate):
    """添加新模型"""
    success = add_model(
        key=model.key,
        name=model.name,
        endpoint=_normalize_endpoint(model.endpoint),
        api_key=model.api_key,
        model_id=model.model_id,
        auth_type=model.auth_type,
        temperature=model.temperature,
        max_tokens=model.max_tokens,
        engine=(model.engine or ""),
        skip_ssl_verify=model.skip_ssl_verify,
    )
    if not success:
        # ★ 区分失败原因：重复 key 是高频场景，直接指出冲突项与出路（该编辑而非新增）
        existing = get_model_by_key(model.key)
        if existing:
            raise HTTPException(status_code=400, detail=(
                f"添加失败：key「{model.key}」已存在（模型「{existing['name']}」）。"
                f"如需修改端点/密钥/引擎，请直接「编辑」该模型，或换个新 key 添加"))
        raise HTTPException(status_code=400, detail="添加模型失败，请查看后端日志（backend.log）确认具体原因")
    return {"success": True, "message": f"模型 {model.key} 已添加"}


@router.delete("/{key}")
async def delete_model_api(key: str):
    """删除模型"""
    success = delete_model(key)
    if not success:
        raise HTTPException(status_code=400, detail="删除失败（内置模型不可删除或key不存在）")
    return {"success": True, "message": f"模型 {key} 已删除"}


@router.put("/{key}")
async def update_model_api(key: str, model: ModelConfigCreate):
    """更新模型配置"""
    existing = get_model_by_key(key)
    if not existing:
        raise HTTPException(status_code=404, detail="模型不存在")

    # ★ 防星号污染：前端编辑态 api_key 留空或含脱敏星号（****）都表示"不修改"，
    #   必须保留 DB 原 key——否则脱敏串会被当成真 key 存回，导致该模型全部请求 401
    new_api_key = model.api_key
    if not new_api_key or '****' in new_api_key:
        new_api_key = existing.get('api_key', '')

    success = update_model(
        key=key,
        name=model.name,
        api_key=new_api_key,
        endpoint=_normalize_endpoint(model.endpoint),
        model_id=model.model_id,
        auth_type=model.auth_type,
        temperature=model.temperature,
        max_tokens=model.max_tokens,
        engine=(model.engine or ""),
        skip_ssl_verify=model.skip_ssl_verify,
    )
    if not success:
        raise HTTPException(status_code=400, detail="更新失败，模型不存在")
    return {"success": True, "message": f"模型 {key} 已更新"}


@router.post("/test")
async def test_model(req: ModelTestRequest):
    """测试模型连接"""
    model_cfg = get_model_by_key(req.key)
    if not model_cfg:
        raise HTTPException(status_code=404, detail=f"模型 {req.key} 不存在")

    if not model_cfg.get('api_key'):
        return ModelTestResponse(success=False, error="未设置 API Key")

    # 构建请求
    headers = {"Content-Type": "application/json"}
    auth_type = model_cfg.get('auth_type', 'bearer')
    if auth_type == 'bearer':
        headers["Authorization"] = f"Bearer {model_cfg['api_key']}"
    elif auth_type == 'direct':
        headers["Authorization"] = model_cfg['api_key']

    payload = {
        "model": model_cfg.get('model_id') or model_cfg['key'],
        "messages": [{"role": "user", "content": req.test_prompt}],
        "max_tokens": 100,
        "temperature": 0.3,
    }

    start_time = time.time()

    async def _do_post(verify: bool) -> ModelTestResponse:
        async with httpx.AsyncClient(timeout=30.0, verify=verify) as client:
            resp = await client.post(model_cfg['endpoint'], headers=headers, json=payload)
        latency_ms = int((time.time() - start_time) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            choices = data.get('choices') or []
            content = choices[0].get('message', {}).get('content', '') if choices else ''
            return ModelTestResponse(success=True, response=content, latency_ms=latency_ms)
        return ModelTestResponse(success=False,
                                 error=f"API返回 {describe_http_response(resp)}",
                                 latency_ms=latency_ms)

    try:
        return await _do_post(_client_verify(model_cfg))
    except Exception as e:
        if not _is_ssl_cert_error(e):
            latency_ms = int((time.time() - start_time) * 1000)
            return ModelTestResponse(success=False, error=str(e), latency_ms=latency_ms)
        # ★ 自签名证书自动放行：关校验重试一次；重试能出结果就持久化标记，
        #   后续 AI 摘要/翻译/追问等调用同步放行，用户无需手动勾选
        try:
            result = await _do_post(False)
        except Exception as e2:
            latency_ms = int((time.time() - start_time) * 1000)
            return ModelTestResponse(success=False, error=str(e2), latency_ms=latency_ms)
        update_model(key=req.key, skip_ssl_verify=True)
        logger.info(f"模型 {req.key} 端点自签名证书，已自动开启跳过 SSL 校验")
        return result


async def _get_with_ssl_bypass(url: str, headers: dict, verify: bool, key: str = None):
    """GET 请求；严格校验遇自签名证书时自动关校验重试一次。

    返回 (resp, ssl_bypassed)。ssl_bypassed=True 表示首次证书失败、关校验后成功，
    按 key 拉取时自动把 skip_ssl_verify 写回 DB（后续测试/AI 调用一起放行）。
    """
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=verify) as client:
            return await client.get(url, headers=headers), False
    except Exception as e:
        if verify and _is_ssl_cert_error(e):
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                resp = await client.get(url, headers=headers)
            if key:
                update_model(key=key, skip_ssl_verify=True)
                logger.info(f"模型 {key} 端点自签名证书，已自动开启跳过 SSL 校验")
            return resp, True
        raise


@router.post("/fetch")
async def fetch_available_models(key: str = None, endpoint: str = None, api_key: str = None,
                                 skip_ssl_verify: bool = False):
    """从API获取可用模型列表（OpenAI兼容接口）
    支持三种方式：
    1. 传入已保存模型的 key（用 DB 完整配置拉取）
    2. 直接传入 endpoint + api_key（添加新模型时使用）
    3. key + endpoint 同时传入（编辑表单获取）：端点用表单值（可能刚改过），
       api_key 留空则回退 DB 原密钥
    """
    model_cfg = get_model_by_key(key) if key else None
    if key and not model_cfg:
        raise HTTPException(status_code=404, detail=f"模型 {key} 不存在")

    if endpoint:
        # ★ 编辑态密钥框刻意留空（防脱敏串回写污染），空密钥直发会被服务端
        #   401 Invalid token——按 key 获取时必须回退 DB 原密钥
        endpoint_url = endpoint
        auth_key = (api_key or '').strip() or (model_cfg or {}).get('api_key', '')
        auth_type = model_cfg.get('auth_type', 'bearer') if model_cfg else 'bearer'
        verify = not skip_ssl_verify
    elif model_cfg:
        endpoint_url = model_cfg['endpoint']
        auth_key = model_cfg.get('api_key', '')
        auth_type = model_cfg.get('auth_type', 'bearer')
        verify = _client_verify(model_cfg)
    else:
        raise HTTPException(status_code=400, detail="缺少 endpoint 参数")

    headers = {}
    if auth_type == 'bearer' and auth_key:
        headers["Authorization"] = f"Bearer {auth_key}"

    try:
        # ★ 端点先统一补全再推导 /models：用户手填一般是裸域名或 /v1，
        #   必须先变成 /v1/chat/completions 才能得到正确的 /v1/models——
        #   否则直接请求域名根路径会拿到网站首页 HTML（200 但非 JSON）
        endpoint_url = _normalize_endpoint(endpoint_url)
        models_url = endpoint_url.replace('/chat/completions', '/models')

        # ★ 自签名证书自动放行：证书是服务器侧信任问题，不是用户操作错误。
        #   严格校验失败（CERTIFICATE_VERIFY_FAILED）自动关校验重试一次；
        #   成功即返回 ssl_bypassed（前端自动勾选「跳过SSL校验」），
        #   按已存 key 拉取时顺手把标记写回 DB，后续 AI 调用同样放行
        resp, ssl_bypassed = await _get_with_ssl_bypass(models_url, headers, verify, key=key)

        text = resp.text
        if resp.status_code == 200:
            try:
                data = json.loads(text)
            except (ValueError, TypeError):
                logger.error(f"模型列表响应不是JSON: url={models_url} status=200 body={text[:200]}")
                if 'text/html' in (resp.headers.get('content-type', '')) or text.lstrip().lower().startswith('<!doctype html'):
                    return {"models": [], "error":
                            f"端点返回的是网页而不是 API（{models_url}）：请填写完整的 API 端点，如 https://主机/v1/chat/completions"}
                return {"models": [], "error": f"接口返回 200 但内容不是 JSON（{models_url}）：{text[:120] or '空响应'}"}
            models_list = data.get('data', []) if isinstance(data, dict) else []
            result = []
            for m in models_list:
                mid = str(m.get("id", ""))
                result.append({"id": mid, "name": mid})
            response = {"models": result}
            if ssl_bypassed:
                response["ssl_bypassed"] = True
            return response
        reason = _extract_api_error(text)
        return {"models": [], "error": f"获取失败: HTTP {resp.status_code}（{models_url}）：{reason}"}
    except httpx.TimeoutException:
        logger.error(f"获取模型列表超时: {models_url}")
        return {"models": [], "error": f"请求超时（{models_url}），请检查网络或代理"}
    except Exception as e:
        logger.error(f"获取模型列表失败: {e} url={models_url}", exc_info=True)
        if isinstance(e, httpx.ConnectError):
            # ★ 空消息 ConnectError 多为「代理隧道连不上目标」（httpcore 会吞掉底层 SSL/连接细节），
            #   给出可行动提示而不是裸错误——最常见是 Clash 类代理未放行该域名或中转服务离线
            proxy = (os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY')
                     or os.environ.get('http_proxy') or os.environ.get('https_proxy') or '')
            hint = (f"当前走代理 {proxy}，请确认代理已启动并放行该域名（或临时关闭代理直连重试）" if proxy
                    else "未走代理，请检查网络连通性或中转服务是否在线")
            return {"models": [], "error": f"网络/连接失败: 无法连接服务器——{hint}（{models_url}）"}
        return {"models": [], "error": f"网络/连接失败: {type(e).__name__} {e}（{models_url}）"}
