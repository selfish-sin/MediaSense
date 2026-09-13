"""
MediaSense - 模块化提示词覆盖 CRUD
用户通过 TemplatesView 编辑器覆盖任意维度模块的 template 文本
"""
import logging
from typing import Dict, List, Optional
from db.database import get_connection

logger = logging.getLogger(__name__)


def list_overrides() -> List[Dict]:
    """列出所有模块覆盖（含自定义模块命名与备份文本）"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, dimension_key, module_key, custom_text, display_name, original_text, previous_text, "
        "created_at, updated_at "
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


def upsert_override(dimension_key: str, module_key: str, custom_text: str,
                    display_name: Optional[str] = None) -> Dict:
    """新增或更新模块覆盖（UPSERT，安全覆盖语义）。

    - 首次创建：original_text 记为本次文本（最初版本，一经写入不变）。
    - 文本发生变更的更新：旧文本轮转进 previous_text（上一版本）；文本未变则不轮转。
    - display_name 传空串/None 时保留原值（普通编辑保存不该动自定义模块命名）。
    - 迁移前的存量行 original_text 为空：以更新前的旧文本补记为最初版本。
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT custom_text, original_text FROM prompt_module_overrides "
            "WHERE dimension_key = ? AND module_key = ?",
            (dimension_key, module_key)
        ).fetchone()
        name = (display_name or "").strip()
        if row is None:
            conn.execute(
                "INSERT INTO prompt_module_overrides "
                "(dimension_key, module_key, custom_text, display_name, original_text, updated_at) "
                "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (dimension_key, module_key, custom_text, name, custom_text)
            )
        else:
            old_text = row["custom_text"] or ""
            original_text = row["original_text"] or old_text
            previous_text = old_text if custom_text != old_text else None  # None=COALESCE 保持原值
            conn.execute(
                "UPDATE prompt_module_overrides SET custom_text = ?, "
                "display_name = CASE WHEN ? = '' THEN display_name ELSE ? END, "
                "original_text = ?, previous_text = COALESCE(?, previous_text), updated_at = CURRENT_TIMESTAMP "
                "WHERE dimension_key = ? AND module_key = ?",
                (custom_text, name, name, original_text, previous_text, dimension_key, module_key)
            )
        conn.commit()
    finally:
        conn.close()
    logger.info(f"模块覆盖已保存: {dimension_key}.{module_key} ({len(custom_text)} 字符)")
    return {"dimension_key": dimension_key, "module_key": module_key, "custom_text": custom_text}


def restore_override_backup(dimension_key: str, module_key: str, version: str) -> Optional[Dict]:
    """一键恢复备份文本（version: 'original'=最初版本 / 'previous'=上一版本）。

    恢复视作一次普通文本变更：被替换下来的当前文本轮转进 previous_text，
    因此两个备份可反复来回切换。无对应备份时返回 None。
    """
    if version not in ("original", "previous"):
        raise ValueError(f"未知的备份版本: {version}")
    ov = get_override(dimension_key, module_key)
    if not ov:
        return None
    backup_text = (ov["original_text"] if version == "original" else ov["previous_text"]) or ""
    if not backup_text.strip():
        return None
    return upsert_override(dimension_key, module_key, backup_text)


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
