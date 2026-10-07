"""
YouTube Tool V2 - AI/转录结果缓存池（2026-10-06）
唯一读写单点：相同输入直接复用上次的返回结果，不重跑计费调用。

两个消费者：
- op='llm'：AI 文本调用（总结/校正/打标签/思维导图；_call_ai 与 _post_chat_once）
    key 要素 = endpoint + model_id + temperature + max_tokens + prompt
    ——提示词或模型任一变化自然 miss；显式重跑（重新总结等）由调用方传 bypass_cache。
- op='asr'：转录结果（transcriber.transcribe_audio）
    key 要素 = asr_v2 + 音频 sha256 + 引擎 + 语言 + 说话人分离 + 模型/配置上下文
    ——同一视频反复提交/失败重试时，转录（分片调用或本地推理）是大头开销。

契约：
- 只缓存"成功且非空"的结果：失败哨兵串/零输出绝不入池，下次重试仍是真重试；
- 命中不产生 token 消耗，token_usage 记 0（统计口径保持真实）；
- 缓存读写失败只告警不阻断主流程（缓存是加速项，不是正确性依赖）；
- 条数上限 config.AI_CACHE_MAX_ENTRIES，超出淘汰最旧（created_at + rowid）；
- 开关 config.AI_CACHE_ENABLED（环境变量 YT_AI_CACHE_ENABLED=0 关闭）。
"""
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import config

logger = logging.getLogger(__name__)

# 单条结果上限：总结批次 ~3 万字符、转录 SRT ~10 万字符都远够；防御异常巨物撑爆 DB
_MAX_CONTENT_CHARS = 2 * 1024 * 1024


def make_key(*parts: Any) -> str:
    """稳定键：各要素以 \x1f 连接后 sha256（要素含换行/中文也稳定， None 当空串）。"""
    raw = '\x1f'.join('' if p is None else str(p) for p in parts)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def file_sha256(path: str) -> str:
    """大文件分块哈希（45MB 音频约 0.1s），供 asr 键使用。"""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def cache_get(key: str) -> Optional[Dict[str, Any]]:
    """按 key 取缓存；命中时 hits+1。未命中/停用/读失败一律返回 None。"""
    if not config.AI_CACHE_ENABLED or not key:
        return None
    from db.database import get_connection
    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT op, model, content, meta, created_at FROM ai_cache WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE ai_cache SET hits = hits + 1 WHERE key = ?", (key,))
        conn.commit()
        try:
            meta = json.loads(row['meta']) if row['meta'] else {}
        except (ValueError, TypeError):
            meta = {}
        return {
            'op': row['op'],
            'model': row['model'],
            'content': row['content'],
            'meta': meta,
            'created_at': row['created_at'],
        }
    except Exception as e:
        logger.warning(f'AI 缓存读取失败（不影响主流程）: {e}')
        return None
    finally:
        if conn is not None:
            conn.close()


def cache_put(key: str, op: str, model: str, content: str, meta: Optional[dict] = None) -> bool:
    """成功结果入池。空结果/超限/写入失败返回 False（主流程不感知）。"""
    if not config.AI_CACHE_ENABLED or not key:
        return False
    content = content or ''
    if not content.strip():
        return False  # 空/失败结果不入池：下次重试仍是真重试
    if len(content) > _MAX_CONTENT_CHARS:
        logger.warning(f'AI 缓存跳过大结果（{len(content)} 字符 > {_MAX_CONTENT_CHARS} 上限）')
        return False
    from db.database import get_connection
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO ai_cache (key, op, model, content, meta, size, hits, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, datetime('now', 'localtime'))",
            (key, op, model or '', content,
             json.dumps(meta or {}, ensure_ascii=False), len(content)),
        )
        conn.commit()
        _prune(conn)
        return True
    except Exception as e:
        logger.warning(f'AI 缓存写入失败（不影响主流程）: {e}')
        return False
    finally:
        if conn is not None:
            conn.close()


def _prune(conn) -> None:
    """条数超上限时淘汰最旧（created_at 秒级同值靠 rowid 决胜）。"""
    try:
        limit = max(int(config.AI_CACHE_MAX_ENTRIES), 10)
        conn.execute(
            "DELETE FROM ai_cache WHERE key NOT IN ("
            "  SELECT key FROM ai_cache ORDER BY created_at DESC, rowid DESC LIMIT ?"
            ")", (limit,))
        conn.commit()
    except Exception as e:
        logger.warning(f'AI 缓存清理失败: {e}')


# ============ 转录（ASR）缓存 ============

def transcription_key(audio_path: str, engine: str, language: Optional[str],
                      speaker_diarization: bool, context: Optional[dict] = None) -> str:
    """v2 身份：完整请求/模型上下文序列化后散列，旧四要素缓存不再读取。"""
    signature = json.dumps(context or {}, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return make_key('asr_v2', file_sha256(audio_path), engine, language or '',
                    int(bool(speaker_diarization)), signature)


def lookup_transcription(audio_path: str, engine: str, language: Optional[str],
                         speaker_diarization: bool, *, cache_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """同音频+同引擎已有识别结果时，把上次 SRT/TXT 写回约定路径。

    返回 {'srt_path','txt_path','seg_count','created_at','engine'}；未命中/异常返回 None。
    写回路径与各引擎的产出约定一致：Path(audio_path).with_suffix('.srt'/'.txt')。
    """
    if not config.AI_CACHE_ENABLED:
        return None
    try:
        key = cache_key or transcription_key(audio_path, engine, language, speaker_diarization)
    except OSError as e:
        logger.warning(f'转录缓存跳过（音频不可读）: {e}')
        return None
    hit = cache_get(key)
    if not hit:
        return None
    txt = (hit.get('meta') or {}).get('txt', '')
    seg_count = (hit.get('meta') or {}).get('seg_count', 0)
    if not txt or not seg_count:
        return None  # 旧格式/残缺条目按未命中处理
    srt_path = str(Path(audio_path).with_suffix('.srt'))
    txt_path = str(Path(audio_path).with_suffix('.txt'))
    try:
        Path(srt_path).write_text(hit['content'], encoding='utf-8')
        Path(txt_path).write_text(txt, encoding='utf-8')
    except OSError as e:
        logger.warning(f'转录缓存命中但写文件失败（按未命中处理）: {e}')
        return None
    return {
        'srt_path': srt_path, 'txt_path': txt_path, 'seg_count': int(seg_count),
        'created_at': hit.get('created_at', ''), 'engine': hit.get('model', ''),
    }


def store_transcription(audio_path: str, engine: str, language: Optional[str],
                        speaker_diarization: bool,
                        srt_path: str, txt_path: str, seg_count: int, *,
                        cache_key: Optional[str] = None, actual_engine: Optional[str] = None) -> bool:
    """转录成功后把 SRT/TXT 入池（键 = 音频内容哈希，换文件名不影响命中）。"""
    if not config.AI_CACHE_ENABLED:
        return False
    try:
        srt = Path(srt_path).read_text(encoding='utf-8')
        txt = Path(txt_path).read_text(encoding='utf-8')
    except OSError as e:
        logger.warning(f'转录缓存跳过（结果文件不可读）: {e}')
        return False
    if not srt.strip() or not txt.strip():
        return False
    try:
        key = cache_key or transcription_key(audio_path, engine, language, speaker_diarization)
    except OSError as e:
        logger.warning(f'转录缓存跳过（音频不可读）: {e}')
        return False
    actual_engine = actual_engine or engine
    return cache_put(key, 'asr', actual_engine, srt,
                     meta={'txt': txt, 'seg_count': int(seg_count), 'actual_engine': actual_engine})
