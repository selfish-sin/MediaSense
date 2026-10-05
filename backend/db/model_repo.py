"""
YouTube Tool V2 - 模型配置数据库操作
"""
import json
import logging
from typing import Optional, List, Dict, Any
from db.database import get_connection
from config import DEFAULT_MODEL

logger = logging.getLogger(__name__)

DEFAULT_MODEL_SETTING_KEY = 'default_model'
# AI 校正默认模型（校正模型选择器留空时使用；独立于总结主模型默认）
DEFAULT_CORRECT_MODEL_SETTING_KEY = 'default_correct_model'
# AI 调用兜底环（2026-10-05）：用户显式排序的备用模型列表，**不设上限**。
# 存 app_settings 同一张键值表，不新增表/列——与 default_model 同构。
FALLBACK_CHAIN_SETTING_KEY = 'llm_fallback_ring'


def get_default_model_key() -> str:
    """获取用户设置的默认模型 key；未设置或模型已被删除时回退 config.DEFAULT_MODEL"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (DEFAULT_MODEL_SETTING_KEY,)
        ).fetchone()
    finally:
        conn.close()
    if row and row['value']:
        key = row['value']
        # 模型可能已被删除，回退到配置默认
        if get_model_by_key(key):
            return key
    return DEFAULT_MODEL


def set_default_model(key: str) -> bool:
    """设置默认模型；模型必须存在，成功返回 True。

    ★ 2026-10-05：设为默认的模型会自动从兜底环里摘除。
      默认模型是首选档，它留在环里等于同一模型占两格、白占一次重试机会。
      规则落在这里而非调用方——不管从 /models 表格、批量接口还是将来任何入口改默认，
      环都会自动保持无重复。摘除不改变其余模型的相对顺序。
    """
    if not get_model_by_key(key):
        return False
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
            (DEFAULT_MODEL_SETTING_KEY, key),
        )
        conn.commit()
        logger.info(f"设置默认模型: {key}")
    except Exception as e:
        logger.error(f"设置默认模型失败: {e}")
        return False
    finally:
        conn.close()

    # 新默认模型原先若在兜底环里，摘掉（失败不阻断"设默认"本身——默认已落库）
    try:
        cfg = get_fallback_chain()
        if key in cfg['chain']:
            set_fallback_chain([k for k in cfg['chain'] if k != key], enabled=cfg['enabled'])
            logger.info(f"模型 {key} 已设为默认，自动从兜底环摘除")
    except Exception as e:
        logger.warning(f"默认模型变更后清理兜底环失败（不影响设默认）: {e}")
    return True


def get_default_correct_model_key() -> str:
    """获取 AI 校正默认模型 key；未设置或模型已删除时返回空串（由调用方回退主模型）"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (DEFAULT_CORRECT_MODEL_SETTING_KEY,)
        ).fetchone()
    finally:
        conn.close()
    if row and row['value']:
        key = row['value']
        # 模型可能已被删除，视为未设置
        if get_model_by_key(key):
            return key
    return ''


def set_default_correct_model(key: str) -> bool:
    """设置 AI 校正默认模型；模型必须存在，成功返回 True"""
    if not get_model_by_key(key):
        return False
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
            (DEFAULT_CORRECT_MODEL_SETTING_KEY, key),
        )
        conn.commit()
        logger.info(f"设置校正默认模型: {key}")
        return True
    except Exception as e:
        logger.error(f"设置校正默认模型失败: {e}")
        return False
    finally:
        conn.close()


def get_fallback_chain() -> Dict[str, Any]:
    """获取 AI 调用兜底环配置。

    返回 {'enabled': bool, 'chain': [...]}：
    - 未配置过时 enabled=False、chain=[]（保持"不自动换模型"的既有行为，不擅自改变语义）。
    - 读出时剔除两类 key：**已不存在的模型**（模型被删后留下悬空 key 会让候选静默失真）
      与**当前默认模型**（它已是首选档，再排进环里等于同一模型占两格、白白浪费一次尝试）。
    - **不设条数上限**：环长由用户自己负责，失败耗时与"环里大半失效"直接相关，
      靠每次切换都推日志让人看得见，而不是用硬上限替用户做决定。
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (FALLBACK_CHAIN_SETTING_KEY,)
        ).fetchone()
    except Exception as e:
        logger.error(f"读取兜底环配置失败: {e}")
        row = None
    finally:
        conn.close()

    if not row or not row['value']:
        return {'enabled': False, 'chain': []}
    try:
        data = json.loads(row['value'])
    except (ValueError, TypeError):
        # 存储被手改成非法 JSON：按未配置处理并留下痕迹，不让一次脏数据打挂整页
        logger.warning(f"兜底环配置 JSON 非法，按未配置处理: {row['value']!r}")
        return {'enabled': False, 'chain': []}

    enabled = bool(data.get('enabled', False)) if isinstance(data, dict) else False
    raw_chain = data.get('chain') if isinstance(data, dict) else None
    if not isinstance(raw_chain, list):
        raw_chain = []

    existing = {m['key'] for m in get_all_models()}
    default_key = get_default_model_key()
    chain, seen = [], set()
    for k in raw_chain:
        k = str(k or '').strip()
        if not k or k in seen or k not in existing or k == default_key:
            continue
        seen.add(k)
        chain.append(k)
    return {'enabled': enabled, 'chain': chain}


def set_fallback_chain(chain: List[str], enabled: bool = True) -> bool:
    """保存 AI 调用兜底环（按传入顺序即优先级）。模型必须存在、去重，并剔除当前默认模型。"""
    existing = {m['key'] for m in get_all_models()}
    default_key = get_default_model_key()
    cleaned, seen = [], set()
    for k in (chain or []):
        k = str(k or '').strip()
        if not k or k in seen or k not in existing or k == default_key:
            continue
        seen.add(k)
        cleaned.append(k)

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
            (FALLBACK_CHAIN_SETTING_KEY, json.dumps(
                {'enabled': bool(enabled), 'chain': cleaned}, ensure_ascii=False)),
        )
        conn.commit()
        logger.info(f"设置兜底环: enabled={enabled} chain={cleaned}")
        return True
    except Exception as e:
        logger.error(f"设置兜底环失败: {e}")
        return False
    finally:
        conn.close()


def resolve_ai_candidates(model: str) -> List[str]:
    """AI 调用的候选顺序（**全项目唯一解析点**）。

    = [本次所选模型] + [兜底环里其余模型（保持用户排的优先级）]

    语义（2026-10-05）：兜底环是**环**——没有固定主档，起点永远是本次选的模型，
    顺次往后走，每个模型只试一次、不回头。终止条件天然成立：环走完即止，
    不需要额外的防死循环开关（回头重试已知失效的中转纯属浪费——
    典型如"账号池耗尽"的 503，30 秒内不会恢复）。

    环未启用时只返回本次所选模型——保持"失败就失败"的既有行为，不擅自改语义。
    """
    primary = str(model or '').strip() or DEFAULT_MODEL
    cfg = get_fallback_chain()
    if not cfg['enabled']:
        return [primary]
    seen = {primary}
    return [primary] + [k for k in cfg['chain'] if not (k in seen or seen.add(k))]


def get_all_models() -> List[Dict[str, Any]]:
    """获取所有模型配置（本地工具，返回完整信息含 api_key）"""
    conn = get_connection()
    rows = conn.execute("SELECT key, name, engine, api_key, endpoint, model_id, auth_type, temperature, max_tokens, skip_ssl_verify, is_builtin FROM model_configs ORDER BY created_at ASC").fetchall()
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
              temperature: float = 0.3, max_tokens: Optional[int] = None,
              engine: str = "", skip_ssl_verify: bool = False) -> bool:
    """添加新模型"""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO model_configs (key, name, engine, api_key, endpoint, model_id, auth_type, temperature, max_tokens, skip_ssl_verify)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (key, name, engine, api_key, endpoint, model_id, auth_type, temperature, max_tokens, int(bool(skip_ssl_verify))))
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
    try:
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
        return affected > 0
    finally:
        conn.close()


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
