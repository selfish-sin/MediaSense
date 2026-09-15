"""
YouTube Tool V2 - ASR（转录）配置数据层

两块配置：
1. `asr_providers` 表：云端 ASR provider（MiMo 付费档 / XingChen 免费档 / 用户自定义）。
   - `engine` 是它在引擎回退链里的**槽位**（cloud / cloud_free），一个槽位可挂多个 provider，
     运行时取「该槽位 enabled 且 sort_order 最小」的那一个（get_provider_for_engine）。
   - `api` 是请求姿势（chat = chat/completions 音频多模态；multipart = /audio/transcriptions）。
2. `app_settings`（既有 KV 表）：转录默认引擎 / Whisper 档位 / 转录设备。

设计约束（勿破坏）：
- 引擎值 cloud/cloud_free/firered/local 语义冻结（历史 job options 已存这些值）。
- 本模块是 ASR 配置的唯一落库点；provider 配置的**唯一读取点**是
  `services/cloud_asr._provider_cfg()`，其它模块不要直接查 asr_providers。
- 内置 provider 不可删除（可编辑/停用）；DB 为空时 cloud_asr 会回退 config.py 属性，
  即"没有本表也能跑"，保证升级/回滚平滑。
"""
import logging
from typing import Any, Dict, List, Optional

from config import DEFAULT_WHISPER_MODEL
from db.database import get_connection

logger = logging.getLogger(__name__)

# 回退链槽位（与 transcriber._FALLBACK_CHAINS 的云端两档一一对应）
VALID_ENGINES = ('cloud', 'cloud_free')
# 请求姿势：chat（chat/completions + base64 音频）/ multipart（OpenAI /audio/transcriptions）
VALID_APIS = ('chat', 'multipart')

# app_settings 键
SETTING_DEFAULT_ENGINE = 'asr_default_engine'
SETTING_WHISPER_MODEL = 'asr_whisper_model'
SETTING_WHISPER_DEVICE = 'asr_whisper_device'

VALID_TRANSCRIBE_ENGINES = ('cloud', 'cloud_free', 'firered', 'local')
VALID_WHISPER_SIZES = ('tiny', 'base', 'small', 'medium')
VALID_WHISPER_DEVICES = ('auto', 'cuda', 'cpu')

# 内置 provider（首次播种）——key 与 cloud_asr 的 provider 名保持一致，勿改
BUILTIN_PROVIDERS = (
    {'key': 'mimo', 'name': '小米 MiMo-ASR（付费）', 'engine': 'cloud', 'sort_order': 10,
     'remark': 'MiMo-v2.5-ASR，¥0.5/小时，术语最准（https://platform.xiaomimimo.com/）'},
    {'key': 'xingchen', 'name': '硅基流动 XingChen（免费）', 'engine': 'cloud_free', 'sort_order': 20,
     'remark': 'XingChenASR-V3.2-Ultra 限时免费，简单视频够用（https://cloud.siliconflow.cn/account/ak）'},
)

# update_provider 允许写的列（白名单，防动态 SQL 注入到列名）
_WRITABLE_COLUMNS = (
    'name', 'engine', 'api', 'endpoint', 'api_key', 'model',
    'enabled', 'concurrency', 'timeout', 'sort_order', 'remark',
)

_SELECT_COLUMNS = (
    'key, name, engine, api, endpoint, api_key, model, enabled, concurrency, timeout, '
    'is_builtin, sort_order, remark, created_at, updated_at'
)


# ============ 云端 provider ============

def list_providers() -> List[Dict[str, Any]]:
    """全部云端 provider（按槽位与优先级排序；含 api_key，仅内部/路由脱敏后用）"""
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM asr_providers "
            "ORDER BY engine ASC, sort_order ASC, created_at ASC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_provider(key: str) -> Optional[Dict[str, Any]]:
    """单个 provider（含 api_key）"""
    if not key:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM asr_providers WHERE key = ?", (key,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def get_provider_for_engine(engine: str) -> Optional[str]:
    """槽位（cloud/cloud_free）→ 运行时首选 provider key；该槽位无可用 provider 时返回 None。

    排序：sort_order 小者优先，同值按创建时间（先建者优先，内置先于自定义）。
    """
    if engine not in VALID_ENGINES:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT key FROM asr_providers WHERE engine = ? AND enabled = 1 "
            "ORDER BY sort_order ASC, created_at ASC LIMIT 1",
            (engine,),
        ).fetchone()
    finally:
        conn.close()
    return row['key'] if row else None


def add_provider(key: str, name: str, engine: str = 'cloud', api: str = 'chat',
                 endpoint: str = '', api_key: str = '', model: str = '',
                 enabled: bool = True, concurrency: Optional[int] = None,
                 timeout: Optional[int] = None, is_builtin: bool = False,
                 sort_order: int = 100, remark: str = '') -> bool:
    """新增 provider（key 重复返回 False）"""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO asr_providers
                (key, name, engine, api, endpoint, api_key, model, enabled,
                 concurrency, timeout, is_builtin, sort_order, remark)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (key, name, engine, api, endpoint, api_key, model, int(bool(enabled)),
             concurrency, timeout, int(bool(is_builtin)), sort_order, remark),
        )
        conn.commit()
        logger.info(f"添加 ASR provider: {key} ({name}) engine={engine} api={api}")
        return True
    except Exception as e:
        logger.error(f"添加 ASR provider 失败: {e}")
        return False
    finally:
        conn.close()


def update_provider(key: str, **kwargs) -> bool:
    """更新 provider（仅白名单列；无有效列时返回 False）"""
    fields: List[str] = []
    values: List[Any] = []
    for k, v in kwargs.items():
        if k not in _WRITABLE_COLUMNS or v is None:
            continue
        if k == 'enabled':
            v = int(bool(v))
        fields.append(f"{k} = ?")
        values.append(v)
    if not fields:
        return False
    fields.append("updated_at = CURRENT_TIMESTAMP")
    values.append(key)
    conn = get_connection()
    try:
        cursor = conn.execute(
            f"UPDATE asr_providers SET {', '.join(fields)} WHERE key = ?", values
        )
        affected = cursor.rowcount
        conn.commit()
        return affected > 0
    finally:
        conn.close()


def delete_provider(key: str) -> bool:
    """删除 provider（内置不可删，返回 False）"""
    conn = get_connection()
    try:
        row = conn.execute("SELECT is_builtin FROM asr_providers WHERE key = ?", (key,)).fetchone()
        if not row:
            return False
        if row['is_builtin']:
            logger.warning(f"内置 ASR provider {key} 不可删除")
            return False
        cursor = conn.execute("DELETE FROM asr_providers WHERE key = ?", (key,))
        affected = cursor.rowcount
        conn.commit()
        return affected > 0
    finally:
        conn.close()


def seed_builtin_providers() -> int:
    """首次播种内置 provider（表为空时才会写入）；已有配置则原样保留。

    从 config.py 读初始值（endpoint/model/api_key），用户 config.py 里已填的 Key
    一次性迁进 DB——之后一律以 DB 为准，config.py 只作读取兜底。
    返回播种条数。
    """
    conn = get_connection()
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM asr_providers").fetchone()['n']
    finally:
        conn.close()
    if count:
        return 0

    # 惰性导入避免 db → services 的模块级循环依赖
    from services.cloud_asr import _config_provider_cfg

    seeded = 0
    for item in BUILTIN_PROVIDERS:
        cfg = _config_provider_cfg(item['key'])
        ok = add_provider(
            key=item['key'], name=item['name'], engine=item['engine'], api=cfg['api'],
            endpoint=cfg['endpoint'], api_key=cfg['api_key'], model=cfg['model'],
            enabled=bool(cfg['enabled']), is_builtin=True,
            sort_order=item['sort_order'], remark=item['remark'],
        )
        seeded += 1 if ok else 0
    if seeded:
        logger.info(f"已播种 {seeded} 个内置 ASR provider（从 config.py 迁移初始值）")
    return seeded


# ============ 应用级设置（app_settings KV） ============

def _get_setting(key: str) -> str:
    conn = get_connection()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    return (row['value'] or '') if row else ''


def _set_setting(key: str, value: str) -> bool:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
            (key, value),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"写入设置 {key} 失败: {e}")
        return False
    finally:
        conn.close()


def get_default_engine() -> str:
    """用户设置的默认转录引擎；未设置或值非法时返回空串（调用方走自动推荐）"""
    value = _get_setting(SETTING_DEFAULT_ENGINE)
    return value if value in VALID_TRANSCRIBE_ENGINES else ''


def set_default_engine(engine: str) -> bool:
    """设置默认转录引擎；**空串 = 清除设置**（恢复"免费优先"自动推荐）。非法值拒绝返回 False。"""
    if engine and engine not in VALID_TRANSCRIBE_ENGINES:
        return False
    return _set_setting(SETTING_DEFAULT_ENGINE, engine or '')


def get_whisper_model() -> str:
    """Whisper 档位（未设置/非法 → config.DEFAULT_WHISPER_MODEL）"""
    value = _get_setting(SETTING_WHISPER_MODEL)
    return value if value in VALID_WHISPER_SIZES else DEFAULT_WHISPER_MODEL


def set_whisper_model(size: str) -> bool:
    if size not in VALID_WHISPER_SIZES:
        return False
    return _set_setting(SETTING_WHISPER_MODEL, size)


def get_whisper_device() -> str:
    """转录设备（auto/cuda/cpu；未设置 → auto）"""
    value = _get_setting(SETTING_WHISPER_DEVICE)
    return value if value in VALID_WHISPER_DEVICES else 'auto'


def set_whisper_device(device: str) -> bool:
    if device not in VALID_WHISPER_DEVICES:
        return False
    return _set_setting(SETTING_WHISPER_DEVICE, device)
