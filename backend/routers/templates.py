"""
YouTube Tool V2 - 模板管理路由
CRUD 提示词模板，支持内置模板保护
"""
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from db.database import get_connection

router = APIRouter()


class TemplateCreate(BaseModel):
    name: str
    summary_type: Optional[str] = None
    prompt: str
    options: Optional[dict] = None
    template_type: Optional[str] = "analysis"  # analysis（其他旧值 stage2_purpose/stage3_goal 已废弃）


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    summary_type: Optional[str] = None
    prompt: Optional[str] = None
    options: Optional[dict] = None
    template_type: Optional[str] = None


@router.get("")
async def list_templates():
    """获取所有模板列表"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name, summary_type, prompt, options, template_type, is_builtin, created_at, updated_at "
        "FROM templates ORDER BY is_builtin DESC, updated_at DESC"
    ).fetchall()
    conn.close()
    templates = []
    for r in rows:
        try:
            opts = json.loads(r["options"]) if r["options"] else {}
        except (json.JSONDecodeError, TypeError):
            opts = {}
        templates.append({
            "id": r["id"],
            "name": r["name"],
            "summary_type": r["summary_type"],
            "prompt": r["prompt"],
            "options": opts,
            "template_type": r["template_type"] or "analysis",
            "is_builtin": bool(r["is_builtin"]),
            "created_at": str(r["created_at"]) if r["created_at"] else None,
            "updated_at": str(r["updated_at"]) if r["updated_at"] else None,
        })
    return {"templates": templates}


@router.post("")
async def create_template(data: TemplateCreate):
    """创建新模板"""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO templates (name, summary_type, prompt, options, template_type) VALUES (?, ?, ?, ?, ?)",
            (data.name, data.summary_type, data.prompt,
             json.dumps(data.options or {}, ensure_ascii=False),
             data.template_type or "analysis")
        )
        conn.commit()
        tid = cursor.lastrowid
    finally:
        conn.close()
    return {"id": tid, "message": "模板创建成功"}


@router.put("/{template_id}")
async def update_template(template_id: int, data: TemplateUpdate):
    """更新模板"""
    conn = get_connection()
    try:
        existing = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="模板不存在")
        if existing["is_builtin"]:
            raise HTTPException(status_code=403, detail="内置模板不可修改")

        updates = {}
        if data.name is not None:
            updates["name"] = data.name
        if data.summary_type is not None:
            updates["summary_type"] = data.summary_type
        if data.prompt is not None:
            updates["prompt"] = data.prompt
        if data.options is not None:
            updates["options"] = json.dumps(data.options, ensure_ascii=False)
        if data.template_type is not None:
            updates["template_type"] = data.template_type

        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [template_id]
            conn.execute(
                f"UPDATE templates SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                values
            )
            conn.commit()
    finally:
        conn.close()
    return {"message": "模板更新成功"}


@router.delete("/{template_id}")
async def delete_template(template_id: int):
    """删除模板"""
    conn = get_connection()
    existing = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="模板不存在")
    if existing["is_builtin"]:
        conn.close()
        raise HTTPException(status_code=403, detail="内置模板不可删除")
    conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
    conn.commit()
    conn.close()
    return {"message": "模板已删除"}
