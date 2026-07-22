"""
MediaSense - 模块化提示词覆盖 CRUD
用户通过 TemplatesView 编辑器覆盖任意维度模块的 template 文本
"""
import logging
from typing import Dict, List, Optional
from db.database import get_connection

logger = logging.getLogger(__name__)


def list_overrides() -> List[Dict]:
    """列出所有模块覆盖"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, dimension_key, module_key, custom_text, created_at, updated_at "
        "FROM prompt_module_overrides ORDER BY dimension_key, module_key"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_override(dimension_key: str, module_key: str) -> Optional[Dict]:
    """获取单个模块覆盖"""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM prompt_module_overrides WHERE dimension_key = ? AND module_key = ?",
        (dimension_key, module_key)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_override(dimension_key: str, module_key: str, custom_text: str) -> Dict:
    """新增或更新模块覆盖（UPSERT）"""
    conn = get_connection()
    conn.execute("""
        INSERT INTO prompt_module_overrides (dimension_key, module_key, custom_text, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(dimension_key, module_key)
        DO UPDATE SET custom_text = excluded.custom_text, updated_at = CURRENT_TIMESTAMP
    """, (dimension_key, module_key, custom_text))
    conn.commit()
    conn.close()
    logger.info(f"模块覆盖已保存: {dimension_key}.{module_key} ({len(custom_text)} 字符)")
    return {"dimension_key": dimension_key, "module_key": module_key, "custom_text": custom_text}


def delete_override(dimension_key: str, module_key: str) -> bool:
    """删除模块覆盖（恢复代码默认）"""
    conn = get_connection()
    cursor = conn.execute(
        "DELETE FROM prompt_module_overrides WHERE dimension_key = ? AND module_key = ?",
        (dimension_key, module_key)
    )
    conn.commit()
    conn.close()
    deleted = cursor.rowcount > 0
    if deleted:
        logger.info(f"模块覆盖已删除（恢复默认）: {dimension_key}.{module_key}")
    return deleted


def reset_all_overrides() -> int:
    """清空所有覆盖（恢复全部默认）"""
    conn = get_connection()
    cursor = conn.execute("DELETE FROM prompt_module_overrides")
    conn.commit()
    conn.close()
    count = cursor.rowcount
    logger.info(f"已清空 {count} 个模块覆盖")
    return count
