"""
YouTube Tool V2 - 任务数据库操作
"""
import json
import os
import uuid
import shutil
import logging
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from db.database import get_connection, get_readonly_connection
from db.model_repo import get_default_model_key
from models.schemas import JobStatus
from config import DOWNLOAD_DIR

logger = logging.getLogger(__name__)



def _table_names(conn) -> set[str]:
    """Return existing table names for schema-tolerant cleanup paths."""
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r[0] for r in rows}


def _delete_if_table(conn, tables: set[str], table: str, where_sql: str = "", params: tuple = ()) -> None:
    """Delete from an optional table when older/test schemas do not have it."""
    if table not in tables:
        return
    suffix = f" WHERE {where_sql}" if where_sql else ""
    conn.execute(f"DELETE FROM {table}{suffix}", params)


def create_job(
    input_type: str,
    input_url: Optional[str] = None,
    input_text: Optional[str] = None,
    input_file: Optional[str] = None,
    model: Optional[str] = None,
    summary_type: str = "summary",
    options: Optional[Dict[str, Any]] = None,
    do_summary: bool = True,
    do_translate: bool = False,
    do_subtitle: bool = False,
    preview_before_summary: bool = True,
    dimensions: Optional[Dict[str, str]] = None,
    batch_id: Optional[str] = None,
) -> str:
    """创建新任务，返回 job_id"""
    # 未指定模型时用用户设置的默认模型（DB 默认，未设置回退 config）
    model = model or get_default_model_key()
    job_id = str(uuid.uuid4())[:8]
    opts = options or {}
    # 将 boolean options 嵌入 options JSON
    opts.update({
        "do_summary": do_summary,
        "do_translate": do_translate,
        "do_subtitle": do_subtitle,
        "preview_before_summary": preview_before_summary,
        "input_type": input_type,
    })
    # ★ 模块化提示词：把 dimensions 嵌入 options（避免改表结构，向前兼容）
    if dimensions:
        opts["dimensions"] = dimensions

    conn = get_connection()
    conn.execute("""
        INSERT INTO jobs (id, status, model, summary_type, options, input_url, input_text, input_file, batch_id)
        VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?)
    """, (job_id, model, summary_type, json.dumps(opts, ensure_ascii=False),
          input_url, input_text, input_file, batch_id))
    conn.commit()
    conn.close()
    logger.info(f"创建任务 {job_id}: input_type={input_type}, batch_id={batch_id or '-'}")
    return job_id


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """获取单个任务详情"""
    conn = get_connection()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def get_jobs(status: Optional[str] = None, limit: int = 50, offset: int = 0,
             keyword: Optional[str] = None) -> List[Dict[str, Any]]:
    """获取任务列表

    keyword 非空时按 title / input_url 模糊匹配（LIKE 通配符 % _ 已转义）。
    可与 status 叠加（AND）。
    """
    conn = get_connection()
    # 构造 WHERE 子句：status 和 keyword 可叠加
    clauses = []
    params: list = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if keyword:
        # 转义 LIKE 通配符，防 % _ 注入；用 ESCAPE '\' 声明转义字符
        escaped = keyword.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        like = f"%{escaped}%"
        clauses.append("(title LIKE ? ESCAPE '\\' OR input_url LIKE ? ESCAPE '\\')")
        params.extend([like, like])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM jobs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (*params, limit, offset)
    ).fetchall()
    total = conn.execute(f"SELECT COUNT(*) FROM jobs{where}", params).fetchone()[0]
    conn.close()
    return [_row_to_dict(r) for r in rows], total


def get_running_jobs() -> List[Dict[str, Any]]:
    """获取运行中的任务"""
    conn = get_connection()
    active_statuses = ('downloading', 'transcribing', 'preview', 'summarizing')
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status IN (?, ?, ?, ?) ORDER BY created_at DESC",
        active_statuses
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_cleanup_jobs(read_only: bool = False) -> List[Dict[str, Any]]:
    '''Return one consistent job snapshot for storage cleanup planning.'''
    conn = get_readonly_connection() if read_only else get_connection()
    try:
        rows = conn.execute('SELECT * FROM jobs ORDER BY id').fetchall()
        return [_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def update_job_input_file_if_matches(job_id: str, old_path: str, new_path: str) -> bool:
    '''Atomically canonicalize an input path only when the snapshot is current.'''
    conn = get_connection()
    try:
        cursor = conn.execute(
            'UPDATE jobs SET input_file = ?, updated_at = CURRENT_TIMESTAMP '
            "WHERE id = ? AND input_file = ? AND status = 'completed'",
            (new_path, job_id, old_path),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def claim_staged_upload(job_id: str, old_path: str, new_path: str) -> bool:
    '''Transfer a staged upload to a newly-created pending job.'''
    conn = get_connection()
    try:
        cursor = conn.execute(
            'UPDATE jobs SET input_file = ?, updated_at = CURRENT_TIMESTAMP '
            "WHERE id = ? AND input_file = ? AND status = 'pending'",
            (new_path, job_id, old_path),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def remove_job_file_reference_if_matches(job_id: str, role: str, old_path: str) -> bool:
    '''Remove a files JSON role if it still points at the cleanup candidate.'''
    conn = get_connection()
    try:
        row = conn.execute('SELECT files FROM jobs WHERE id = ?', (job_id,)).fetchone()
        if not row:
            return False
        files = json.loads(row[0] or '{}')
        if files.get(role) != old_path:
            return False
        files.pop(role, None)
        cursor = conn.execute(
            "UPDATE jobs SET files = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND files = ? AND status = 'completed'",
            (json.dumps(files, ensure_ascii=False), job_id, row[0]),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def fail_interrupted_jobs() -> int:
    """服务启动时把被重启中断的非终态任务标记为 failed，返回处理数量

    重启后进程内 RUNNING_JOBS 已清空，pending/downloading/transcribing/
    preview/summarizing 的任务既不会有执行者继续跑，又不是终态，而
    retry 只接受 failed/cancelled —— 不处理就会永久悬挂，用户只能
    手动 cancel 再 retry。这里统一标记为 failed 并注明原因，使其可被重试。
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE jobs SET status = 'failed', error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE status IN ('pending', 'downloading', 'transcribing', 'preview', 'summarizing')
            """,
            ("服务重启中断，请重试",),
        )
        count = cursor.rowcount
        conn.commit()
        return count
    except Exception as e:
        conn.rollback()
        logger.error(f"标记重启中断任务失败: {e}")
        return 0
    finally:
        conn.close()


def get_jobs_by_batch(batch_id: str) -> List[Dict[str, Any]]:
    """获取批量任务的所有子任务（按创建时间排序）"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE batch_id = ? ORDER BY created_at ASC",
        (batch_id,)
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_batch_summary(batch_id: str) -> Optional[Dict[str, Any]]:
    """聚合查询批量任务整体进度"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, status, title, input_url, created_at, updated_at FROM jobs WHERE batch_id = ? ORDER BY created_at ASC",
        (batch_id,)
    ).fetchall()
    conn.close()
    if not rows:
        return None
    jobs = [dict(r) for r in rows]
    total = len(jobs)
    by_status = {}
    for j in jobs:
        s = j['status']
        by_status[s] = by_status.get(s, 0) + 1
    completed = by_status.get('completed', 0)
    failed = by_status.get('failed', 0)
    cancelled = by_status.get('cancelled', 0)
    running = sum(by_status.get(s, 0) for s in ('downloading', 'transcribing', 'preview', 'summarizing', 'pending'))
    return {
        "batch_id": batch_id,
        "total": total,
        "completed": completed,
        "failed": failed,
        "cancelled": cancelled,
        "running": running,
        "by_status": by_status,
        "all_done": (completed + failed + cancelled) == total,
        "jobs": jobs,
    }


def update_job(job_id: str, **kwargs) -> bool:
    """更新任务字段"""
    conn = get_connection()
    # 构建 UPDATE 语句
    fields = []
    values = []
    for key, value in kwargs.items():
        if key in ('options', 'files', 'tags', 'token_usage', 'correction_stats'):
            # JSON 字段需要序列化
            value = json.dumps(value, ensure_ascii=False)
        fields.append(f"{key} = ?")
        values.append(value)
    fields.append("updated_at = CURRENT_TIMESTAMP")
    values.append(job_id)

    sql = f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?"
    # ★ cancelled 保护：用户取消后 cancel_job 已立即写入 cancelled，但后台任务
    #   收尾/进行中仍可能调 update_job 把它覆盖成 completed/failed/transcribing 等，
    #   导致 UI 显示死任务在跑、删除被 409 拦截。
    #   这里在 SQL 层原子拦截：除 pending（retry 正是把 cancelled 改回 pending）
    #   之外的任何状态写入，都不许落在已 cancelled 的行上。
    new_status = kwargs.get('status')
    if new_status is not None and new_status != JobStatus.pending:
        sql += " AND status != 'cancelled'"
    cursor = conn.execute(sql, values)
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def delete_job(job_id: str, delete_files: bool = True) -> bool:
    """删除单个任务（连带清理子表记录和磁盘文件，避免外键约束失败）"""
    conn = get_connection()
    tombstone = None
    job_dir = DOWNLOAD_DIR / job_id
    try:
        if not conn.execute('SELECT 1 FROM jobs WHERE id = ?', (job_id,)).fetchone():
            return False
        if Path(job_id).name != job_id or job_id in {'.', '..'}:
            raise ValueError('job id is not safe for filesystem deletion')
        if delete_files and job_dir.exists():
            quarantine = DOWNLOAD_DIR.parent / '.cleanup_quarantine'
            quarantine.mkdir(parents=True, exist_ok=True)
            tombstone = quarantine / f'delete_{int(datetime.now().timestamp())}_{job_id}_{uuid.uuid4().hex[:8]}'
            job_dir.rename(tombstone)
            os.utime(tombstone, None)
        tables = _table_names(conn)
        # 先清子表（job_chats、token_usage 没有 ON DELETE CASCADE）
        _delete_if_table(conn, tables, "job_chats", "job_id = ?", (job_id,))
        _delete_if_table(conn, tables, "chat_sessions", "job_id = ?", (job_id,))
        _delete_if_table(conn, tables, "token_usage", "job_id = ?", (job_id,))
        _delete_if_table(conn, tables, "summary_history", "job_id = ?", (job_id,))
        cursor = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        affected = cursor.rowcount
        conn.commit()
        if tombstone:
            try:
                shutil.rmtree(str(tombstone))
                logger.info('deleted quarantined job files: %s', tombstone)
            except Exception as exc:
                logger.warning('job record deleted; files remain quarantined at %s: %s', tombstone, exc)
        # 删除磁盘文件（失败不抛异常——沙盒环境可能拦截，但 DB 记录已删）
        if delete_files and affected:
            job_dir = DOWNLOAD_DIR / job_id
            if job_dir.exists():
                try:
                    shutil.rmtree(str(job_dir))
                    logger.info(f"已删除磁盘文件: {job_dir}")
                except Exception as e:
                    logger.warning(f"删除磁盘文件失败（DB记录已删）: {job_dir} - {e}")
        return affected > 0
    except Exception as e:
        conn.rollback()
        if tombstone and tombstone.exists() and not job_dir.exists():
            try:
                tombstone.rename(job_dir)
            except Exception:
                logger.exception('failed to restore quarantined job directory: %s', tombstone)
        logger.error(f"删除任务 {job_id} 失败: {e}")
        raise
    finally:
        conn.close()

def _dir_size(path: Path) -> int:
    """递归计算目录总大小（bytes），不存在返回0"""
    if not path.exists():
        return 0
    total = 0
    for f in path.rglob('*'):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total

def get_jobs_disk_info(job_ids: List[str]) -> List[Dict[str, Any]]:
    """获取每个任务的磁盘文件信息（路径+大小），用于删除前预览弹窗"""
    result = []
    for jid in job_ids:
        job = get_job(jid)
        if not job:
            result.append({"job_id": jid, "exists": False, "error": "任务不存在"})
            continue
        job_dir = DOWNLOAD_DIR / jid
        info = {
            "job_id": jid,
            "title": job.get("title", ""),
            "status": job.get("status", ""),
            "job_dir": str(job_dir),
            "dir_exists": job_dir.exists() and job_dir.is_dir(),
            "size_bytes": _dir_size(job_dir) if job_dir.exists() else 0,
        }
        result.append(info)
    return result

def delete_jobs_batch(job_ids: List[str], delete_files: bool = True) -> Dict[str, Any]:
    """批量删除任务，返回详细统计"""
    deleted = []
    failed = []
    total_freed = 0
    for jid in job_ids:
        try:
            # 先算磁盘大小（删之前算）
            job_dir = DOWNLOAD_DIR / jid
            pending_freed = _dir_size(job_dir) if delete_files and job_dir.exists() else 0
            if delete_job(jid, delete_files=delete_files):
                deleted.append(jid)
                total_freed += pending_freed
            else:
                failed.append({'job_id': jid, 'error': 'job not found'})
        except Exception as e:
            logger.error(f"批量删除 {jid} 失败: {e}")
            failed.append({"job_id": jid, "error": str(e)})
    return {"deleted": deleted, "deleted_count": len(deleted), "failed": failed, "files_freed_bytes": total_freed}


def clear_jobs(delete_files: bool = True) -> int:
    '''Delete every job through the same file-aware path as single deletion.'''
    job_ids = [job['id'] for job in get_cleanup_jobs()]
    return delete_jobs_batch(job_ids, delete_files=delete_files)['deleted_count']


def clear_jobs_by_status(status: str, delete_files: bool = True) -> int:
    '''Delete matching jobs through the same file-aware path as single deletion.'''
    job_ids = [job['id'] for job in get_cleanup_jobs() if job['status'] == status]
    return delete_jobs_batch(job_ids, delete_files=delete_files)['deleted_count']


def finish_job(job_id: str, success: bool, files: Optional[Dict] = None,
               token_usage: Optional[Dict] = None, error: Optional[str] = None) -> bool:
    """完成/失败任务。返回是否成功写入终态（cancelled 保护命中时返回 False）。"""
    status = JobStatus.completed if success else JobStatus.failed
    update_data = {"status": status}
    if files:
        update_data["files"] = files
    if token_usage:
        update_data["token_usage"] = token_usage
    if error is not None:
        update_data["error"] = str(error) if error else error
    elif not success:
        update_data["error"] = "未知错误（无异常信息）"
    updated = update_job(job_id, **update_data)
    if updated:
        logger.info(f"任务 {job_id} 完成: status={status}")
    else:
        # ★ 终态保护命中：任务已被用户取消（cancelled），收尾写入被拦截
        logger.info(f"任务 {job_id} 收尾写入被跳过（已处于 cancelled 终态，不被 {status} 覆盖）")
    return updated


def _row_to_dict(row) -> Dict[str, Any]:
    """将 sqlite3.Row 转为 dict，JSON字段自动反序列化，时间字段转 ISO 8601"""
    d = dict(row)
    # 反序列化 JSON 字段
    json_fields = ('options', 'files', 'tags', 'token_usage', 'correction_stats')
    for field in json_fields:
        value = d.get(field)
        if value and isinstance(value, str):
            # ★ 历史脏数据防御：翻译接口旧 bug 曾把已序列化的 str 再传给
            #   update_job，内部二次 json.dumps → 读回 loads 一层后仍是 str，
            #   导致 JobResponse.files(Dict) 校验失败、GET /api/jobs 整体 500。
            #   这里循环解码直到非 str（正常数据一层即止，脏数据最多两三层）。
            for _ in range(3):
                if not isinstance(value, str):
                    break
                try:
                    value = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    break
            d[field] = value
    # 时间字段标准化为 ISO 8601 UTC (2026-07-03T09:17:00Z)
    for tf in ('created_at', 'updated_at'):
        if d.get(tf) and isinstance(d[tf], str):
            # 已经是 ISO 格式 "2026-07-03T09:17:00Z" → 直接返回
            if 'T' in d[tf] and d[tf].endswith('Z'):
                pass
            # SQLite 原始格式 "2026-07-03 09:17:00" → 加 T + Z
            elif re.match(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', d[tf]):
                d[tf] = d[tf].replace(' ', 'T') + 'Z'
    return d


def _now_utc_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串"""
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def find_job_by_url(normalized_url: str) -> Optional[Dict[str, Any]]:
    """根据规范化URL查找最近的任务（用于去重：同一URL不重复处理）

    查找逻辑：
    1. 先精确匹配完整 URL；
    2. 若平台可提取唯一标识（如 youtube:BV号/视频ID），按该唯一标识模糊匹配
       （不同追踪参数/短链都指向同一视频）；
    3. 兜底：去掉 query 参数后模糊匹配（保守，避免误合并不同内容）。

    ★ 旧逻辑对 watch?v= 链接做了 split('?')[0]，会把所有油管 watch 链接
      都归并到同一前缀 "https://www.youtube.com/watch"，导致不同视频互相撞车。
    """
    from routers.jobs import normalize_url
    conn = get_connection()
    # 1. 先精确匹配
    row = conn.execute(
        "SELECT * FROM jobs WHERE input_url = ? ORDER BY created_at DESC LIMIT 1",
        (normalized_url,)
    ).fetchone()
    if not row:
        # 2. 按平台唯一标识匹配（youtube:视频ID / bilibili:BV号 等）
        norm = normalize_url(normalized_url)
        if ':' in norm:
            _, vid = norm.split(':', 1)
            # vid 可能含 LIKE 通配符（YouTube ID 合法含 _），不转义会误匹配相似视频
            vid_escaped = vid.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            row = conn.execute(
                "SELECT * FROM jobs WHERE input_url LIKE ? ESCAPE '\\' ORDER BY created_at DESC LIMIT 1",
                (f'%{vid_escaped}%',)
            ).fetchone()
        else:
            # 3. 兜底：无法提取平台标识的链接，才去掉 query 参数后模糊匹配
            #    ⚠ 绝不能对 youtube watch 等「ID 在 query 中」的链接用此逻辑，
            #      否则会变成 "https://www.youtube.com/watch%" 把所有油管视频归并到一起
            row = conn.execute(
                "SELECT * FROM jobs WHERE input_url LIKE ? ORDER BY created_at DESC LIMIT 1",
                (normalized_url.split('?')[0] + '%',)
            ).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_dict(row)


# ============ 总结历史版本 ============

def save_summary_version(job_id: str, version: int, summary_type: str, model: str,
                         result_text: str, tags=None, token_usage=None, files=None) -> bool:
    """保存一个总结版本到 summary_history 表"""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO summary_history
            (job_id, version, summary_type, model, result_text, tags, token_usage, files, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id, version, summary_type, model, result_text,
            json.dumps(tags or [], ensure_ascii=False),
            json.dumps(token_usage or {}, ensure_ascii=False),
            json.dumps(files or {}, ensure_ascii=False),
            _now_utc_iso(),
        ))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"保存总结版本失败: {e}")
        return False
    finally:
        conn.close()


def get_summary_history(job_id: str) -> List[Dict[str, Any]]:
    """获取某任务的所有总结历史版本（按版本号升序）"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM summary_history WHERE job_id = ? ORDER BY version ASC",
        (job_id,)
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        for field in ('tags', 'token_usage', 'files'):
            if d.get(field) and isinstance(d[field], str):
                try:
                    d[field] = json.loads(d[field])
                except json.JSONDecodeError:
                    pass
        if d.get('created_at') and isinstance(d['created_at'], str):
            if 'T' not in d['created_at']:
                d['created_at'] = d['created_at'].replace(' ', 'T') + 'Z'
        result.append(d)
    return result


def get_summary_version(job_id: str, version: int) -> Optional[Dict[str, Any]]:
    """获取某任务的指定版本总结全文"""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM summary_history WHERE job_id = ? AND version = ?",
        (job_id, version)
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    json_fields = ('tags', 'token_usage', 'files')
    for field in json_fields:
        if d.get(field) and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except json.JSONDecodeError:
                pass
    if d.get('created_at') and isinstance(d['created_at'], str):
        if 'T' not in d['created_at']:
            d['created_at'] = d['created_at'].replace(' ', 'T') + 'Z'
    return d


def get_latest_summary_version(job_id: str) -> int:
    """获取某任务当前最新版本号（默认1）"""
    conn = get_connection()
    row = conn.execute(
        "SELECT MAX(version) FROM summary_history WHERE job_id = ?",
        (job_id,)
    ).fetchone()
    row2 = conn.execute(
        "SELECT summary_version FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    conn.close()
    history_max = row[0] if row and row[0] else 0
    job_ver = row2[0] if row2 and row2[0] else 1
    return max(history_max, job_ver)
