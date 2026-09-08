"""
YouTube Tool V2 - AI模型管理路由
"""
import logging
import time
import httpx
from fastapi import APIRouter, HTTPException

from models.schemas import ModelConfigCreate, ModelConfigResponse, ModelTestRequest, ModelTestResponse
from db.model_repo import get_all_models, get_model_by_key, add_model, update_model, delete_model, get_default_model_key, set_default_model

logger = logging.getLogger(__name__)
router = APIRouter()


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
        if m.get('api_key') and len(m['api_key']) > 8:
            m['api_key'] = m['api_key'][:4] + '****' + m['api_key'][-4:]
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
    )
    if not success:
        raise HTTPException(status_code=400, detail="添加模型失败（可能key已存在）")
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

    success = update_model(
        key=key,
        name=model.name,
        api_key=model.api_key if model.api_key is not None else existing.get('api_key', ''),
        endpoint=_normalize_endpoint(model.endpoint),
        model_id=model.model_id,
        auth_type=model.auth_type,
        temperature=model.temperature,
        max_tokens=model.max_tokens,
        engine=(model.engine or ""),
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
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(model_cfg['endpoint'], headers=headers, json=payload)
            latency_ms = int((time.time() - start_time) * 1000)

            if resp.status_code == 200:
                data = resp.json()
                choices = data.get('choices') or []
                content = choices[0].get('message', {}).get('content', '') if choices else ''
                return ModelTestResponse(success=True, response=content, latency_ms=latency_ms)
            else:
                return ModelTestResponse(success=False, error=f"API返回 {resp.status_code}: {resp.text[:200]}",
                                         latency_ms=latency_ms)
    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        return ModelTestResponse(success=False, error=str(e), latency_ms=latency_ms)


@router.post("/fetch")
async def fetch_available_models(key: str = None, endpoint: str = None, api_key: str = None):
    """从API获取可用模型列表（OpenAI兼容接口）
    支持两种方式：
    1. 传入已保存模型的 key
    2. 直接传入 endpoint + api_key（添加新模型时使用）
    """
    if key:
        model_cfg = get_model_by_key(key)
        if not model_cfg:
            raise HTTPException(status_code=404, detail=f"模型 {key} 不存在")
        endpoint_url = model_cfg['endpoint']
        auth_key = model_cfg.get('api_key', '')
        auth_type = model_cfg.get('auth_type', 'bearer')
    else:
        if not endpoint:
            raise HTTPException(status_code=400, detail="缺少 endpoint 参数")
        endpoint_url = endpoint
        auth_key = api_key or ''
        auth_type = 'bearer'

    headers = {}
    if auth_type == 'bearer' and auth_key:
        headers["Authorization"] = f"Bearer {auth_key}"

    try:
        models_url = endpoint_url.replace('/chat/completions', '/models')
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(models_url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                models_list = data.get('data', [])
                result = []
                for m in models_list:
                    mid = str(m.get("id", ""))
                    result.append({"id": mid, "name": mid})
                return {"models": result}
            return {"models": [], "error": f"获取失败: {resp.status_code}"}
    except Exception as e:
        logger.error(f"获取模型列表失败: {e}", exc_info=True)
        return {"models": [], "error": f"获取失败: {str(e)}"}
