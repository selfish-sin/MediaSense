"""
YouTube Tool V2 - 模型配置数据库操作
"""
import json
import logging
from typing import Optional, List, Dict, Any
from db.database import get_connection

logger = logging.getLogger(__name__)


def get_all_models() -> List[Dict[str, Any]]:
    """获取所有模型配置（本地工具，返回完整信息含 api_key）"""
    conn = get_connection()
    rows = conn.execute("SELECT key, name, api_key, endpoint, model_id, auth_type, temperature, max_tokens, is_builtin FROM model_configs ORDER BY created_at ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_model_by_key(key: str) -> Optional[Dict[str, Any]]:
    """获取单个模型配置（含api_key，仅内部使用）"""
    conn = get_connection()
    row = conn.execute("SELECT * FROM model_configs WHERE key = ?", (key,)).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def add_model(key: str, name: str, endpoint: str, api_key: Optional[str] = None,
              model_id: Optional[str] = None, auth_type: str = "bearer",
              temperature: float = 0.3, max_tokens: Optional[int] = None) -> bool:
    """添加新模型"""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO model_configs (key, name, api_key, endpoint, model_id, auth_type, temperature, max_tokens)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (key, name, api_key, endpoint, model_id, auth_type, temperature, max_tokens))
        conn.commit()
        logger.info(f"添加模型: {key} ({name})")
        return True
    except Exception as e:
        logger.error(f"添加模型失败: {e}")
        return False
    finally:
        conn.close()


def update_model(key: str, **kwargs) -> bool:
    """更新模型配置"""
    conn = get_connection()
    fields = []
    values = []
    for k, v in kwargs.items():
        fields.append(f"{k} = ?")
        values.append(v)
    values.append(key)
    sql = f"UPDATE model_configs SET {', '.join(fields)} WHERE key = ?"
    cursor = conn.execute(sql, values)
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def delete_model(key: str) -> bool:
    """删除模型（内置模型不可删除）"""
    conn = get_connection()
    # 先检查是否内置
    row = conn.execute("SELECT is_builtin FROM model_configs WHERE key = ?", (key,)).fetchone()
    if row and row['is_builtin']:
        conn.close()
        logger.warning(f"内置模型 {key} 不可删除")
        return False
    cursor = conn.execute("DELETE FROM model_configs WHERE key = ?", (key,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0
