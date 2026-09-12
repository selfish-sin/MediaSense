"""
YouTube Tool V2 - 任务批量域路由（批量提交 / 批量状态 / 转录全文搜索）

从 routers/jobs.py 按域拆出，全部挂 /api/jobs 前缀，对外路径零变化。
"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request, Query

from db.job_repo import create_job, get_batch_summary, find_job_by_url
from db.model_repo import get_default_model_key
from services.downloader import sanitize_input_url
from routers.jobs import normalize_url

logger = logging.getLogger(__name__)
router = APIRouter()

# ★ 批量任务严格串行编排：批量提交只 spawn 一个编排任务，按顺序逐个 await
#   前一个任务跑完再启动下一个（任务序列，一次只跑一个）。目的：批量不会同时
#   向平台发起多个字幕/元数据请求——2026-09-08 复盘发现并发 2 时 B站 偶发降级
#   （player/v2 返回 subtitle_url="/" 占位），用户明确要求"批量=任务序列"。
#   批量内串行，不影响单个任务；全局 MAX_CONCURRENT_JOBS 上限仍然生效。
_batch_seq_tasks: set = set()


async def _run_batch_sequential(job_ids):
    """按提交顺序逐个执行批量子任务：启动 → await 完成/失败 → 下一个。"""
    from main import start_job
    for job_id in job_ids:
        try:
            task = start_job(job_id)
            if task is not None:
                await task
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f'批量任务 {job_id} 执行异常，继续下一个: {e}')


@router.post("/batch")
async def batch_submit(request: Request):
    """批量提交URL
    body: {"urls": ["url1","url2",...], "options": {...}}  或直接 ["url1","url2",...]（兼容旧前端缓存）

    ★ 完善：①URL 去重过滤 ②生成 batch_id 聚合 ③传递完整参数（model/summary_type/开关）
    批量任务严格串行：创建全部子任务后只启动一个编排任务（_run_batch_sequential），
    按提交顺序逐个执行，一次只跑一个（用户要求"批量=任务序列"，防平台风控降级）；
    全局信号量（MAX_CONCURRENT_JOBS）仍对跨批量/单个任务生效。
    """
    import uuid
    from typing import List

    # ★ 兼容两种 body 格式：旧前端传 list，新前端传 dict
    #   用 Request 直接拿原始 JSON，避免 FastAPI dict 类型校验拒绝 list
    body = await request.json()

    if not isinstance(body, (list, dict)):
        raise HTTPException(status_code=400, detail='批量请求必须是 URL 数组或对象')

    # 兼容两种格式
    if isinstance(body, list):
        urls: List[str] = body
        opts = {}
        model = get_default_model_key()
        summary_type = "summary"
        do_summary = True
        # ★ 批量固定不做转录预览确认：每任务直通总结，流程统一（用户要求批量不用确认）
        preview_before_summary = False
        dimensions = None
        force_new = False  # 旧前端 list 格式无法表达 force_new，保守不去重
    else:
        urls = body.get("urls", [])
        opts = body.get("options", {}) or {}
        model = body.get("model") or get_default_model_key()
        summary_type = body.get("summary_type", "summary")
        do_summary = body.get("do_summary", True)
        # ★ 批量固定不做转录预览确认：即使前端传了 preview_before_summary 也忽略，
        #   批量串行与「等待人工确认」天然冲突，会卡住后续任务
        preview_before_summary = False
        # ★ 与单 URL 路径对齐：默认 force_new=True（用户主动批量提交即意图处理）
        force_new = body.get("force_new", True)

    if not isinstance(urls, list):
        raise HTTPException(status_code=400, detail='urls 必须是数组')
    if not isinstance(opts, dict):
        raise HTTPException(status_code=400, detail='options 必须是对象')
    if len(urls) > 200:
        raise HTTPException(status_code=413, detail='单次最多提交 200 个 URL')

    if isinstance(body, dict):
        # 模块化提示词：从顶层或 options.dimensions 提取
        dimensions = body.get("dimensions") or opts.get("dimensions")

    # ★ URL 去重 + 清洗（智能提取URL + 去空行 + 去重复，保持顺序）
    seen_norm = set()   # 规范化URL去重（同一视频不同query参数）
    seen_raw = set()    # 原始URL去重（完全相同的字符串）
    clean_urls: List[str] = []
    skipped = 0
    duplicates = []  # 跨历史重复的 URL，返回给前端用于跳转
    for url in urls:
        if not isinstance(url, str):
            skipped += 1
            continue
        # ★ 用 sanitize_input_url 处理：支持裸 BV 号 / 裸短链(b23.tv) / 包裹符号 / 尾部标点
        #   与单 URL 路径(process_job)用同一函数，保证两条路径对裸链接的处理一致
        u = sanitize_input_url(url)
        if not u:
            # 无有效 URL，跳过（可能是纯文字行）
            skipped += 1
            continue
        nu = normalize_url(u)
        if nu in seen_norm or u in seen_raw:
            skipped += 1
            continue
        seen_raw.add(u)
        seen_norm.add(nu)
        # ★ 跨历史任务 URL 去重：force_new=True 时跳过（与单 URL 路径 submit_job 对齐）
        if not force_new:
            existing = find_job_by_url(u)
            if existing and existing.get('status') in ('pending', 'completed', 'preview', 'downloading', 'transcribing', 'summarizing'):
                # 已完成/进行中的任务，跳过并返回已有任务信息
                skipped += 1
                duplicates.append({
                    'url': u,
                    'existing_job_id': existing['id'],
                    'existing_status': existing['status'],
                    'existing_title': existing.get('title', ''),
                })
                continue
        clean_urls.append(u)

    if not clean_urls:
        raise HTTPException(status_code=400, detail="没有有效的URL（全部为空或重复）")

    # 生成 batch_id 聚合所有子任务
    batch_id = str(uuid.uuid4())[:8]

    job_ids = []
    for url in clean_urls:
        job_id = create_job(
            input_type="url",
            input_url=url,
            model=model,
            summary_type=summary_type,
            options=opts,
            do_summary=do_summary,
            preview_before_summary=preview_before_summary,
            dimensions=dimensions,
            batch_id=batch_id,
        )
        job_ids.append(job_id)

    # ★ 批量严格串行：只启动一个编排任务，按顺序逐个执行（任务序列，一次只跑一个），
    #   不再一次性把全部子任务并发丢进全局队列（避免同时向平台发起多个请求触发风控降级）
    _orchestrator = asyncio.create_task(_run_batch_sequential(job_ids))
    _batch_seq_tasks.add(_orchestrator)
    _orchestrator.add_done_callback(_batch_seq_tasks.discard)

    return {
        "batch_id": batch_id,
        "job_ids": job_ids,
        "count": len(job_ids),
        "skipped": skipped,
        "duplicates": duplicates,
        "message": f"已提交 {len(job_ids)} 个批量任务（跳过 {skipped} 个无效/重复URL），按顺序逐个执行",
    }


@router.get("/batch/{batch_id}")
async def get_batch_status(batch_id: str):
    """查询批量任务整体进度"""
    summary = get_batch_summary(batch_id)
    if not summary:
        raise HTTPException(status_code=404, detail=f"批量任务 {batch_id} 不存在")
    return summary


# ============ 转录文本全文搜索 ============
@router.get("/search/transcript")
async def search_transcripts(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    limit: int = Query(50, ge=1, le=200, description="最多返回任务数"),
    max_matches_per_job: int = Query(5, ge=1, le=20, description="每个任务最多返回匹配片段数"),
    context_chars: int = Query(80, ge=20, le=300, description="匹配上下文字符数"),
):
    """
    全文搜索转录文本（跨历史任务）。

    搜索范围：所有有 txt 文件的任务（转录文本/OCR文本）。
    返回每个匹配任务的片段（带上下文 + 时间戳）。

    返回:
    {
      "query": q,
      "total_jobs_searched": N,
      "total_jobs_matched": M,
      "results": [
        {
          "job_id": "...", "title": "...", "platform": "...", "created_at": "...",
          "match_count": K,
          "matches": [
            {"text": "...带高亮上下文...", "timestamp": "MM:SS 或 null", "line_no": N}
          ]
        }
      ]
    }
    """
    import asyncio
    import re
    from pathlib import Path
    from db.database import get_connection

    def _scan_sync():
        conn = get_connection()
        # 只搜有 txt 文件路径的任务（不限状态，失败的任务也可能有部分转录）
        rows = conn.execute("""
            SELECT id, title, platform, status, created_at, files
            FROM jobs
            WHERE files LIKE '%"txt"%'
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit * 3,)).fetchall()  # 多取一些，过滤掉无文件的
        conn.close()

        results = []
        searched = 0
        # 时间戳正则：[MM:SS - MM:SS] 或 [MM:SS]
        ts_pattern = re.compile(r'\[(\d{1,2}:\d{2}(?::\d{2})?)\s*(?:-\s*\d{1,2}:\d{2}(?::\d{2})?)?\]')

        for row in rows:
            import json
            files = row['files']
            try:
                files_dict = json.loads(files) if isinstance(files, str) else (files or {})
            except Exception:
                continue
            txt_path = files_dict.get('txt', '')
            if not txt_path or not Path(txt_path).exists():
                continue
            searched += 1
            try:
                with open(txt_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception:
                continue

            # 按行搜索（转录文本通常一行一段，带时间戳）
            lines = content.split('\n')
            matches = []
            q_lower = q.lower()
            for line_no, line in enumerate(lines, 1):
                if q_lower in line.lower():
                    # 提取该行时间戳
                    ts_match = ts_pattern.search(line)
                    timestamp = ts_match.group(1) if ts_match else None

                    # 构建上下文（取匹配位置前后 context_chars 字符）
                    idx = line.lower().find(q_lower)
                    start = max(0, idx - context_chars)
                    end = min(len(line), idx + len(q) + context_chars)
                    context = line[start:end]
                    if start > 0:
                        context = '...' + context
                    if end < len(line):
                        context = context + '...'

                    matches.append({
                        "text": context,
                        "timestamp": timestamp,
                        "line_no": line_no,
                    })
                    if len(matches) >= max_matches_per_job:
                        break

            if matches:
                results.append({
                    "job_id": row['id'],
                    "title": row['title'] or '(未命名)',
                    "platform": row['platform'] or '',
                    "status": row['status'],
                    "created_at": row['created_at'],
                    "match_count": len(matches),
                    "matches": matches,
                })
                if len(results) >= limit:
                    break
        return results, searched

    # 同步扫描（DB + 逐文件读）放线程池，避免卡住事件循环里的任务进度推送
    results, searched = await asyncio.to_thread(_scan_sync)

    return {
        "query": q,
        "total_jobs_searched": searched,
        "total_jobs_matched": len(results),
        "results": results,
    }
