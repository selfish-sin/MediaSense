"""
MediaSense - 模块化提示词路由
9 维度可组合提示词系统：dimensions + recipes + prompt-preview + 模块覆盖 CRUD
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from services.prompt_builder import (
    PROMPT_DIMENSIONS,
    RECIPES,
    get_default_dimensions,
    get_dimensions_with_overrides,
    build_modular_prompt,
)
from db.prompt_override_repo import (
    list_overrides, get_override, upsert_override, delete_override, reset_all_overrides,
    restore_override_backup,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ============ 维度/配方/预览 ============

@router.get("/dimensions")
async def get_dimensions():
    """获取所有维度的模块定义（含 DB 覆盖，供前端渲染选择器）"""
    return {"dimensions": get_dimensions_with_overrides()}


@router.get("/recipes")
async def get_recipes():
    """获取预设配方列表"""
    return {"recipes": RECIPES}


@router.get("/defaults")
async def get_defaults():
    """获取默认维度选择"""
    return {"defaults": get_default_dimensions()}


@router.post("/prompt-preview")
async def get_prompt_preview(data: dict):
    """
    根据当前 dimensions 构建完整的提示词预览（不含实际内容）
    请求体: {dimensions: {...}, custom_prompt?: "...", full_override?: "..."}
    """
    dimensions = data.get("dimensions", {})
    custom_prompt = data.get("custom_prompt", "")
    full_override = data.get("full_override", "")

    prompt = build_modular_prompt(
        dimensions=dimensions,
        content="（此处将替换为转录/输入文本）",
        custom_prompt=custom_prompt,
        full_override=full_override,
    )

    return {
        "dimensions": dimensions,
        "prompt": prompt.strip(),
    }


@router.get("/deep-config")
async def get_deep_config():
    """获取全部配置（dimensions含覆盖 + recipes + defaults，供前端一次性加载）"""
    return {
        "dimensions": get_dimensions_with_overrides(),
        "recipes": RECIPES,
        "defaults": get_default_dimensions(),
    }


# ============ 模块覆盖 CRUD（供 TemplatesView 编辑器） ============

class ModuleOverrideUpsert(BaseModel):
    dimension_key: str
    module_key: str
    custom_text: str
    display_name: Optional[str] = None  # 自定义模块显示名；None/空=保留原值（普通编辑保存不改名）


class OverrideRestoreBody(BaseModel):
    version: str  # 'original'=最初版本 / 'previous'=上一版本


@router.get("/overrides")
async def api_list_overrides():
    """列出所有模块覆盖"""
    return {"overrides": list_overrides()}


@router.get("/overrides/{dimension_key}/{module_key}")
async def api_get_override(dimension_key: str, module_key: str):
    """获取单个模块覆盖（不存在则返回 default template；自定义模块无代码默认文本）"""
    ov = get_override(dimension_key, module_key)
    # 从 PROMPT_DIMENSIONS 取默认文本作为参考
    default_text = ""
    is_custom_module = True
    dim = PROMPT_DIMENSIONS.get(dimension_key, {})
    for m in dim.get("modules", []):
        if m["key"] == module_key:
            default_text = m.get("template", "")
            is_custom_module = False
            break
    return {
        "dimension_key": dimension_key,
        "module_key": module_key,
        "custom_text": ov["custom_text"] if ov else None,
        "default_text": default_text,
        "is_customized": ov is not None,
        "is_custom_module": ov is not None and is_custom_module,
        "display_name": (ov["display_name"] if ov else "") or "",
        "original_text": (ov["original_text"] if ov else "") or "",
        "previous_text": (ov["previous_text"] if ov else "") or "",
    }


@router.put("/overrides")
async def api_upsert_override(body: ModuleOverrideUpsert):
    """新增/更新模块覆盖（文本变更时旧文本自动轮转进 previous_text 备份）"""
    if not body.custom_text.strip():
        raise HTTPException(status_code=400, detail="custom_text 不能为空（如需恢复默认请用 DELETE）")
    # 校验维度和模块存在
    dim = PROMPT_DIMENSIONS.get(body.dimension_key)
    if not dim:
        raise HTTPException(status_code=400, detail=f"维度 {body.dimension_key} 不存在")
    # 允许覆盖未在代码中定义的 module_key（支持维度级自定义模块）
    result = upsert_override(body.dimension_key, body.module_key, body.custom_text, body.display_name)
    return {"success": True, "override": result}


@router.post("/overrides/{dimension_key}/{module_key}/restore")
async def api_restore_override(dimension_key: str, module_key: str, body: OverrideRestoreBody):
    """一键恢复备份（original=最初版本 / previous=上一版本）；恢复本身也走备份轮转"""
    if not PROMPT_DIMENSIONS.get(dimension_key):
        raise HTTPException(status_code=400, detail=f"维度 {dimension_key} 不存在")
    try:
        result = restore_override_backup(dimension_key, module_key, body.version)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="没有可恢复的备份")
    return {"success": True, "override": result}


@router.delete("/overrides/{dimension_key}/{module_key}")
async def api_delete_override(dimension_key: str, module_key: str):
    """删除模块覆盖（恢复代码默认）"""
    deleted = delete_override(dimension_key, module_key)
    return {"success": deleted, "message": "已恢复默认" if deleted else "无覆盖记录"}


@router.delete("/overrides")
async def api_reset_all_overrides():
    """清空所有覆盖（恢复全部默认）"""
    count = reset_all_overrides()
    return {"success": True, "deleted_count": count}
