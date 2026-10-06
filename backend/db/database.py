"""
YouTube Tool V2 - SQLite 数据库层
启动时自动建表，提供数据库连接
"""
import sqlite3
import json
import logging
from pathlib import Path
from config import DB_PATH, DATA_DIR

logger = logging.getLogger(__name__)

# 确保数据目录存在
DATA_DIR.mkdir(exist_ok=True)


def get_connection() -> sqlite3.Connection:
    """获取 SQLite 连接（启用 WAL 模式提升并发性能）"""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row  # 让查询结果可以用列名访问
    conn.execute("PRAGMA journal_mode=WAL")  # WAL模式：读写不互相阻塞
    conn.execute("PRAGMA busy_timeout=5000")  # 并发写锁：等5秒而非立即报错
    conn.execute("PRAGMA foreign_keys=ON")    # 启用外键约束
    return conn


def get_readonly_connection() -> sqlite3.Connection:
    '''Open SQLite in URI read-only mode without changing journal settings.'''
    uri = f'{DB_PATH.resolve().as_uri()}?mode=ro'
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    conn.execute('PRAGMA busy_timeout=5000')
    return conn


def init_db():
    """初始化数据库：创建所有表"""
    conn = get_connection()
    cursor = conn.cursor()

    # 任务表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            title TEXT,
            platform TEXT,
            input_url TEXT,
            input_text TEXT,
            input_file TEXT,
            status TEXT DEFAULT 'pending',
            model TEXT,
            summary_type TEXT,
            options TEXT DEFAULT '{}',
            result_text TEXT,
            files TEXT DEFAULT '{}',
            tags TEXT DEFAULT '[]',
            token_usage TEXT DEFAULT '{}',
            progress_msg TEXT,
            preview_text TEXT,
            edited_preview_text TEXT,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # AI模型配置表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS model_configs (
            key TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            api_key TEXT,
            endpoint TEXT NOT NULL,
            model_id TEXT,
            auth_type TEXT DEFAULT 'bearer',
            temperature REAL DEFAULT 0.3,
            max_tokens INTEGER DEFAULT 4096,
            is_builtin INTEGER DEFAULT 0,
            engine TEXT DEFAULT '',
            skip_ssl_verify INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ★ 引擎名列（迁移：旧 model_configs 表没有 engine；空串=不参与「引擎-模型」显示）
    try:
        cursor.execute("ALTER TABLE model_configs ADD COLUMN engine TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # 字段已存在

    # ★ 跳过 SSL 证书校验（自签名证书/IP 直连的中转端点需勾选，0=严格校验）
    try:
        cursor.execute("ALTER TABLE model_configs ADD COLUMN skip_ssl_verify INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # 字段已存在

    # Token统计表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT REFERENCES jobs(id),
            model_key TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cost REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 提示词模板表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            summary_type TEXT,
            prompt TEXT NOT NULL,
            options TEXT DEFAULT '{}',
            is_builtin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 总结类型表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS summary_types (
            key TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            desc TEXT,
            icon TEXT,
            prompt_template TEXT,
            prompt_vars TEXT DEFAULT '[]',
            default_options TEXT DEFAULT '{}',
            category TEXT DEFAULT 'general',
            is_builtin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 模板类型字段（迁移：v1模板没这字段）
    try:
        cursor.execute("ALTER TABLE templates ADD COLUMN template_type TEXT DEFAULT 'analysis'")
    except sqlite3.OperationalError:
        pass  # 字段已存在

    # ★ 批量任务字段（迁移：旧 jobs 表没 batch_id）
    #   batch_id 为空表示单任务；批量提交时所有子任务共享同一 batch_id
    try:
        cursor.execute("ALTER TABLE jobs ADD COLUMN batch_id TEXT")
    except sqlite3.OperationalError:
        pass  # 字段已存在

    # 任务追问聊天表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS job_chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT REFERENCES jobs(id),
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            token_usage TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ★ 追问表扩展字段迁移（sources 引用源 / thinking 思考过程）
    try:
        cursor.execute("ALTER TABLE job_chats ADD COLUMN sources TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE job_chats ADD COLUMN thinking TEXT")
    except sqlite3.OperationalError:
        pass

    # ★ 追问多会话表（每个视频可有多条独立对话线程）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id TEXT PRIMARY KEY,
            job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
            title TEXT DEFAULT '新对话',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ★ job_chats 增加 session_id 列（多会话支持）
    try:
        cursor.execute("ALTER TABLE job_chats ADD COLUMN session_id TEXT")
    except sqlite3.OperationalError:
        pass

    # ★ 旧数据迁移：为每个有消息但无会话的任务创建默认会话，并把消息归属进去
    try:
        conn.execute("""
            INSERT INTO chat_sessions (id, job_id, title)
            SELECT 'def-' || job_id, job_id, '对话 1'
            FROM (SELECT DISTINCT job_id FROM job_chats WHERE session_id IS NULL) t
        """)
        conn.execute("""
            UPDATE job_chats SET session_id = 'def-' || job_id
            WHERE session_id IS NULL
        """)
        conn.commit()
    except Exception as e:
        logger.warning(f"追问会话迁移失败（可忽略）: {e}")

    # ★ 总结历史版本表（每次重新总结保留旧版）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS summary_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            version INTEGER NOT NULL DEFAULT 1,
            summary_type TEXT,
            model TEXT,
            result_text TEXT,
            tags TEXT DEFAULT '[]',
            token_usage TEXT DEFAULT '{}',
            files TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(job_id, version)
        )
    """)

    # ★ 总结版本号字段（迁移：旧 jobs 表没有）
    try:
        cursor.execute("ALTER TABLE jobs ADD COLUMN summary_version INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass

    # ★ AI 校正统计字段（迁移：旧 jobs 表没有，校正任务的批次/失败/告警统计）
    #   JSON：{total_chunks, failed_chunks, integrity_warnings, length_warnings, warnings:[]}
    try:
        cursor.execute("ALTER TABLE jobs ADD COLUMN correction_stats TEXT")
    except sqlite3.OperationalError:
        pass

    # ★ AI 调用链字段（迁移：旧 jobs 表没有，2026-10-05 兜底环）
    #   JSON：[{model, ok, reason}, ...] —— 每次尝试的模型/成败/真实原因。
    #   兜底环最怕"配了却看不见有没有用"，结果页据此展示实际调用链。
    try:
        cursor.execute("ALTER TABLE jobs ADD COLUMN ai_call_chain TEXT")
    except sqlite3.OperationalError:
        pass

    # ★ 模块化提示词覆盖表（TemplatesView 编辑器写入）
    #   用户可覆盖任意维度任意模块的 template 文本，build_modular_prompt 优先读取
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prompt_module_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dimension_key TEXT NOT NULL,
            module_key TEXT NOT NULL,
            custom_text TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            original_text TEXT DEFAULT '',
            previous_text TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(dimension_key, module_key)
        )
    """)

    # ★ 自定义模块命名 + 覆盖备份列（迁移：旧表没有；
    #   original_text=首次自定义文本一经写入不变，previous_text=最近一次文本变更前的旧文本）
    for _stmt in (
        "ALTER TABLE prompt_module_overrides ADD COLUMN display_name TEXT DEFAULT ''",
        "ALTER TABLE prompt_module_overrides ADD COLUMN original_text TEXT DEFAULT ''",
        "ALTER TABLE prompt_module_overrides ADD COLUMN previous_text TEXT DEFAULT ''",
    ):
        try:
            cursor.execute(_stmt)
        except sqlite3.OperationalError:
            pass  # 字段已存在

    # 应用级设置表（键值对，如用户设置的默认模型）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ★ 云端 ASR provider 表（模型管理页「ASR 模型」域；engine = 回退链槽位 cloud/cloud_free）
    #   api = 请求姿势（chat = chat/completions 音频多模态 / multipart = /audio/transcriptions）
    #   仓储与语义见 db/asr_repo.py；provider 配置的读取单点是 services/cloud_asr._provider_cfg
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS asr_providers (
            key TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            engine TEXT NOT NULL DEFAULT 'cloud',
            api TEXT NOT NULL DEFAULT 'chat',
            endpoint TEXT NOT NULL DEFAULT '',
            api_key TEXT DEFAULT '',
            model TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1,
            concurrency INTEGER,
            timeout INTEGER,
            is_builtin INTEGER DEFAULT 0,
            sort_order INTEGER DEFAULT 100,
            remark TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ★ AI/转录结果缓存池（2026-10-06）：key=sha256(输入要素)，读写单点 services/ai_cache.py。
    #   op='llm'（AI 文本：endpoint+model_id+提示词+内容）/ op='asr'（转录：音频哈希+引擎+语言）。
    #   相同输入直接复用上次结果，不重跑计费调用；只缓存成功且非空的结果。
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ai_cache (
            key TEXT PRIMARY KEY,
            op TEXT NOT NULL,
            model TEXT DEFAULT '',
            content TEXT NOT NULL,
            meta TEXT DEFAULT '',
            size INTEGER DEFAULT 0,
            hits INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # ★ 索引（IF NOT EXISTS，纯增量；加速列表/去重/批量/token汇总/追问历史查询）
    _index_stmts = [
        "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)",
        "CREATE INDEX IF NOT EXISTS idx_jobs_batch_id ON jobs(batch_id)",
        "CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_token_usage_job_id ON token_usage(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_token_usage_created_at ON token_usage(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_job_chats_session_id ON job_chats(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_job_chats_job_id ON job_chats(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_chat_sessions_job_id ON chat_sessions(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_summary_history_job_id ON summary_history(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_asr_providers_engine ON asr_providers(engine, enabled, sort_order)",
        "CREATE INDEX IF NOT EXISTS idx_ai_cache_created ON ai_cache(created_at)",
    ]
    for _stmt in _index_stmts:
        try:
            cursor.execute(_stmt)
        except sqlite3.OperationalError as e:
            logger.warning(f"建立索引失败（可忽略）: {e}")

    conn.commit()
    conn.close()
    logger.info(f"数据库初始化完成: {DB_PATH}")

    # 初始化内置数据
    _seed_builtin_models()
    _seed_summary_types()
    _seed_builtin_asr_providers()


def _seed_builtin_models():
    """内置AI模型配置表（已清空，用户自行在 /models 页面管理模型）"""
    # 不再自动播种模型
    logger.info("无内置模型（请在 /models 页面自行添加）")


def _seed_builtin_asr_providers():
    """播种内置云端 ASR provider（仅当 asr_providers 为空时）

    把用户 config.py 里已填的 endpoint/model/api_key 一次性迁入 DB，
    之后运行时一律以 DB 为准（config.py 只作读取兜底），改配置不再需要重启。
    """
    from db.asr_repo import seed_builtin_providers
    seed_builtin_providers()


def _seed_summary_types():
    """插入预设总结类型"""
    from config import SUMMARY_TYPES
    conn = get_connection()
    cursor = conn.cursor()

    for key, cfg in SUMMARY_TYPES.items():
        cursor.execute("SELECT key FROM summary_types WHERE key = ?", (key,))
        if not cursor.fetchone():
            cursor.execute("""
                INSERT INTO summary_types (key, name, desc, icon, category, is_builtin)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (key, cfg["name"], cfg["desc"], cfg.get("icon", ""), cfg.get("category", "general")))

    conn.commit()
    conn.close()
    logger.info("预设总结类型已加载")


def reset_db():
    """重置数据库（危险操作，仅用于开发）"""
    if DB_PATH.exists():
        try:
            DB_PATH.unlink()
        except OSError:
            pass
        logger.warning("数据库已重置")
    init_db()
