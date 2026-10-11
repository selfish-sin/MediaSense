"""
YouTube Tool V2 - AI模型管理路由
"""
import json
import logging
import os
import re
import ssl
import time
import asyncio
import httpx
from fastapi import APIRouter, HTTPException

from models.schemas import ModelConfigCreate, ModelTestRequest, ModelTestResponse
from db.model_repo import get_all_models, get_model_by_key, add_model, update_model, delete_model, get_default_model_key, set_default_model, get_default_correct_model_key, set_default_correct_model, get_fallback_chain, set_fallback_chain
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


# ★ 2026-10-11：失败分类 + 体检批量化。
#   起因：401/403/404/429/网络不通在界面上原本一律显示「连接失败」，
#   用户看不出该改密钥、找客服开权限、改模型 ID 还是关代理，只能逐个猜；
#   且坏模型留在兜底环里会被反复撞，表现成"又连不上了"。
#   现在统一归类成 (kind, hint)，前端按 kind 上色/给修法，体检列表直接列出该怎么修。
#   kind 清单（前端 HEALTH_KIND 文案需与之对齐）：
#     ok / no_key / network / tls / auth / forbidden / forbidden_realname /
#     model / endpoint / rate_limit / quota / server / bad_request / unknown


def _classify_failure(status: int | None, detail: str) -> tuple[str, str]:
    """把一次探测失败归类成 (类别, 可行动修法)。

    status=None 表示传输层就失败了（DNS/代理隧道/证书），此时 detail 是异常文本。
    分类只看服务端给的真实原因（describe_http_response 已提取）＋状态码，
    不做猜测；判不出来就落 unknown 并把原文带上，绝不吞错。
    """
    d = (detail or '').lower()

    if status is None:
        if 'certificate' in d or 'ssl' in d or 'tls' in d:
            return 'tls', '证书校验失败：勾选「跳过 SSL 证书校验」后重试（自签名/IP 直连中转常见）'
        return 'network', '网络/代理不通：确认代理已启动并放行该域名，或临时关闭代理直连重试'

    if status in (400, 422):
        if 'model' in d:
            return 'model', '模型ID不被接受：点「编辑」核对模型ID，或重新拉取该端点的模型列表'
        return 'bad_request', '请求被拒绝：核对端点是否为 OpenAI 兼容地址、模型ID是否正确'

    if status == 401:
        return 'auth', 'API Key 无效或已失效：重新复制完整密钥粘贴（编辑时不要把 **** 脱敏串存回去）'

    if status == 403:
        if any(w in d for w in ('real-name', 'real name', 'realname', '实名', 'face verification')):
            return 'forbidden_realname', '账号未实名：去服务商官网完成实名认证后才允许调用该接口'
        return 'forbidden', '账号无权限：在服务商后台开通该模型/分组权限，或改用已开通的模型'

    if status == 404:
        if 'model' in d:
            return 'model', '模型ID不存在：服务商当前不提供该模型名（点「编辑」用拉取到的列表核对）'
        return 'endpoint', '端点路径不对：应为 <base>/v1/chat/completions（或服务商自带版本段的地址）'

    if status == 402:
        return 'quota', '账户额度/余额不足：充值或更换服务商'

    if status == 429:
        # 先分「令牌并发/每分钟打满」与「账户额度用尽」——两者修法完全不同
        if any(w in d for w in ('tpm', 'rpm', 'rate limit', 'ratelimit', 'too many requests')):
            return 'rate_limit', '被限流：令牌并发或每分钟额度已打满，稍后重试；持续失败建议移出兜底环'
        if any(w in d for w in ('quota', 'insufficient', 'balance', 'billing', '余额', '欠费')):
            return 'quota', '账户额度/余额不足：充值或更换服务商'
        return 'rate_limit', '被限流（HTTP 429）：稍后重试；持续失败建议移出兜底环'

    if 500 <= status < 600:
        return 'server', f'服务端错误（{status}）：对方中转或账号池异常，可换模型或稍后重试'

    return 'unknown', f'HTTP {status}：{(detail or "无详细信息")[:120]}'


def _normalize_endpoint(endpoint: str) -> str:
    """OpenAI 兼容端点补全：规范化为 <base>/chat/completions（幂等）。

    ★ 2026-10-05 修正：原来**无条件**插 /v1，会把自带非 v1 版本段的端点改废——
      火山方舟 https://ark.cn-beijing.volces.com/api/v3        → .../api/v3/v1/chat/completions（404）
      智谱 GLM https://open.bigmodel.cn/api/paas/v4             → .../api/paas/v4/v1/chat/completions（404）
      连 app 自带的「智谱 GLM」快捷模板都是坏的。
      现在规则是：**base 结尾已有版本段（/v1 /v3 /v4 …）就沿用它，不插 /v1**；
      没有版本段时才按 OpenAI 惯例补 /v1（api.deepseek.com → /v1/chat/completions）。
    """
    url = (endpoint or '').strip().rstrip('/')
    if not url:
        return url
    if url.endswith('/chat/completions'):
        base = url[:-len('/chat/completions')].rstrip('/')
    else:
        base = url[:-3].rstrip('/') if url.endswith('/v1') else url
    # base 结尾已是版本段 → 沿用；否则按 OpenAI 惯例补 v1
    if re.search(r'/v\d+[a-z0-9._-]*$', base, re.IGNORECASE):
        return base + '/chat/completions'
    return base + '/v1/chat/completions'


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
    return {"models": models, "default": get_default_model_key(),
            "correct_default": get_default_correct_model_key()}


@router.post("/default")
async def set_default_model_api(body: dict):
    """设置默认模型（校验模型存在）。

    body.scope 缺省/'summary' = 总结主模型默认；'correct' = AI 校正默认。
    """
    body = body or {}
    key = body.get('key', '')
    scope = body.get('scope', 'summary')
    if not key:
        raise HTTPException(status_code=400, detail="缺少 key 参数")
    if scope == 'correct':
        if not set_default_correct_model(key):
            raise HTTPException(status_code=404, detail=f"模型 {key} 不存在")
        return {"success": True, "scope": scope, "default": key, "message": f"校正默认模型已设为 {key}"}
    if not set_default_model(key):
        raise HTTPException(status_code=404, detail=f"模型 {key} 不存在")
    return {"success": True, "scope": "summary", "default": key, "message": f"默认模型已设为 {key}"}


@router.get("/fallback-chain")
async def get_fallback_chain_api():
    """获取 AI 调用兜底环配置（enabled + 有序候选）。用 GET 而非 /{key} 分支，语义更准。"""
    return get_fallback_chain()


@router.post("/fallback-chain")
async def set_fallback_chain_api(body: dict):
    """保存 AI 调用兜底环。

    body: {chain: [key, ...], enabled: bool}；chain 的**顺序即优先级**。
    故意用 POST 而非 PUT：本路由存在 `PUT /{key}`，子路径会被其吞掉，
    POST 与既有 `POST /default`、`POST /test` 同一约定，天然无此冲突。
    不设条数上限；模型不存在/重复/等于当前默认模型的项在 repo 层剔除。
    """
    body = body or {}
    chain = body.get('chain')
    if not isinstance(chain, list):
        raise HTTPException(status_code=400, detail="chain 必须是模型 key 数组")
    if not set_fallback_chain(chain, enabled=bool(body.get('enabled', True))):
        raise HTTPException(status_code=500, detail="保存兜底环失败，请查看后端日志")
    cfg = get_fallback_chain()
    return {"success": True, **cfg,
            "message": f"兜底环已保存（{len(cfg['chain'])} 个候选）"}


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


async def _probe_model(model_cfg: dict, *, timeout: float = 30.0,
                      prompt: str = "hi", max_tokens: int = 16) -> dict:
    """真实发一次最小 chat 请求探测模型连通性——**全项目唯一探测实现**。

    「更多 → 测试连接」(POST /api/models/test) 与「一键体检」(POST /api/models/health-check)
    共用它，保证两处口径一致（历史上两处各写一套，用户点测试通过、体检说不通最难排查）。

    返回 dict：
      {ok, content, status, kind, hint, reason, latency_ms, ssl_bypassed}
      ok=False 时 kind/hint 给可行动分类，reason 是服务端真实原因原文。
    """
    key = model_cfg.get('key')
    headers = {"Content-Type": "application/json"}
    auth_type = model_cfg.get('auth_type', 'bearer')
    if auth_type == 'bearer':
        headers["Authorization"] = f"Bearer {model_cfg.get('api_key') or ''}"
    elif auth_type == 'direct':
        headers["Authorization"] = model_cfg.get('api_key') or ''

    payload = {
        "model": model_cfg.get('model_id') or key,
        "messages": [{"role": "user", "content": prompt}],
        # 体检默认 16 token 足够——避免推理型模型把额度耗在探测上；
        # 单模型「测试连接」传 64，让用户看到真实回复片段
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    start_time = time.time()

    def _latency() -> int:
        return int((time.time() - start_time) * 1000)

    async def _do_post(verify: bool):
        async with httpx.AsyncClient(timeout=timeout, verify=verify) as client:
            return await client.post(model_cfg['endpoint'], headers=headers, json=payload)

    def _from_response(resp: httpx.Response) -> dict:
        latency_ms = _latency()
        if resp.status_code == 200:
            content = ''
            try:
                data = resp.json()
                choices = data.get('choices') or []
                content = choices[0].get('message', {}).get('content', '') if choices else ''
            except (ValueError, AttributeError, IndexError, TypeError):
                # 200 但结构不认：链路是通的，但不代表真的可用——按 unknown 报出来，不当成功
                kind, hint = _classify_failure(resp.status_code, '返回 200 但响应结构不是 OpenAI 兼容格式')
                return {'ok': False, 'status': 200, 'kind': kind, 'hint': hint,
                        'reason': 'HTTP 200 但响应结构不是 OpenAI 兼容格式', 'latency_ms': latency_ms}
            return {'ok': True, 'content': content, 'status': 200, 'kind': 'ok',
                    'reason': '', 'latency_ms': latency_ms}
        reason = describe_http_response(resp)
        kind, hint = _classify_failure(resp.status_code, reason)
        return {'ok': False, 'status': resp.status_code, 'kind': kind, 'hint': hint,
                'reason': reason, 'latency_ms': latency_ms}

    try:
        return _from_response(await _do_post(_client_verify(model_cfg)))
    except Exception as e:
        if not _is_ssl_cert_error(e):
            kind, hint = _classify_failure(None, str(e))
            return {'ok': False, 'status': None, 'kind': kind, 'hint': hint,
                    'reason': str(e), 'latency_ms': _latency()}

    # ★ 自签名证书自动放行：关校验重试一次；**重试成功才**把标记持久化，
    #   后续 AI 摘要/翻译/追问等调用同步放行，用户无需手动勾选。
    #   （注意：重试仍失败时**不写** skip_ssl_verify——否则一个纯 401 的端点会被
    #     永久标记成"跳过证书校验"，掩盖真正原因。）
    try:
        resp = await _do_post(False)
    except Exception as e2:
        kind, hint = _classify_failure(None, str(e2))
        return {'ok': False, 'status': None, 'kind': kind, 'hint': hint,
                'reason': str(e2), 'latency_ms': _latency()}

    result = _from_response(resp)
    if result['ok']:
        result['ssl_bypassed'] = True
        update_model(key=key, skip_ssl_verify=True)
        logger.info(f"模型 {key} 端点自签名证书，已自动开启跳过 SSL 校验")
    return result


@router.post("/test")
async def test_model(req: ModelTestRequest):
    """测试模型连接（真实发一次最小请求；失败给类别 + 修法，不是一句「连接失败」）"""
    model_cfg = get_model_by_key(req.key)
    if not model_cfg:
        raise HTTPException(status_code=404, detail=f"模型 {req.key} 不存在")

    if not model_cfg.get('api_key'):
        return ModelTestResponse(success=False, error="未设置 API Key", kind='no_key',
                                 hint='点「编辑」补上完整 API Key 后再测')

    r = await _probe_model(model_cfg, prompt=req.test_prompt, max_tokens=64)
    if r['ok']:
        return ModelTestResponse(success=True, response=r['content'], latency_ms=r['latency_ms'])
    return ModelTestResponse(success=False, error=r['reason'], latency_ms=r['latency_ms'],
                             kind=r['kind'], hint=r['hint'])


_HEALTH_CONCURRENCY = 4
_HEALTH_TIMEOUT = 20.0


@router.post("/health-check")
async def health_check_models():
    """一键体检：并发探测**全部** LLM 模型，把每个模型的连通性与失败修法一次列清。

    与 POST /test 共用 `_probe_model`，区别只在批量并发（默认 4 路，单个 20s 超时）。
    故意不放在 GET：会真实消耗各服务商额度，且与 /test 同一 POST 约定。
    密钥为空或已被 **** 污染的模型不发请求，直接标 no_key（与 list_models 的 key_broken 同口径）。
    """
    models = get_all_models()
    sem = asyncio.Semaphore(_HEALTH_CONCURRENCY)

    async def _one(m: dict) -> dict:
        base = {'key': m.get('key'), 'name': m.get('name'),
                'model_id': m.get('model_id') or m.get('key')}
        raw_key = m.get('api_key') or ''
        if not raw_key.strip():
            return {**base, 'ok': False, 'status': None, 'kind': 'no_key',
                    'hint': '未设置 API Key：点「编辑」补上完整密钥',
                    'reason': '未设置 API Key', 'latency_ms': 0}
        if '****' in raw_key:
            return {**base, 'ok': False, 'status': None, 'kind': 'no_key',
                    'hint': '密钥被脱敏串污染（含 ****，必然 401）：点「编辑」重新粘贴完整密钥',
                    'reason': '密钥含脱敏串 ****', 'latency_ms': 0}
        async with sem:
            r = await _probe_model(m, timeout=_HEALTH_TIMEOUT)
        return {**base, 'ok': r['ok'], 'status': r['status'], 'kind': r['kind'],
                'hint': r.get('hint', ''), 'reason': r['reason'], 'latency_ms': r['latency_ms']}

    results = await asyncio.gather(*(_one(m) for m in models))
    failed = [r for r in results if not r['ok']]
    # 顺手标出哪些失败模型还挂在兜底环里——它们会在每次换模型时被反复撞，是"又连不上"的放大器
    ring = {str(k) for k in (get_fallback_chain().get('chain') or [])}
    for r in failed:
        r['in_fallback_ring'] = r['key'] in ring

    return {
        'results': results,
        'summary': {
            'total': len(results),
            'ok': len(results) - len(failed),
            'failed': len(failed),
            # 服务端侧硬失败（密钥/权限/模型不存在/额度）——这些重试无用，该移出兜底环
            'hard_failed': sum(1 for r in failed if r['kind'] in
                               ('auth', 'forbidden', 'forbidden_realname', 'model', 'endpoint', 'no_key', 'quota')),
            'failed_in_ring': sum(1 for r in failed if r.get('in_fallback_ring')),
        },
    }



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
