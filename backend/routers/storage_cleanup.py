'''Local-only storage cleanup preview, execution, progress, and audit API.'''
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field

from config import DATA_DIR, DOWNLOAD_DIR
from db.job_repo import count_jobs_with_status
from services.storage_cleanup import CleanupPlan, CleanupPolicy, StorageCleanup

logger = logging.getLogger(__name__)
router = APIRouter()

PLAN_TTL_SECONDS = 15 * 60
ACTIVE_JOB_STATUSES = {'pending', 'downloading', 'transcribing', 'preview', 'summarizing'}
MAX_LOG_ENTRIES = 500
MAX_AUDIT_ENTRIES = 100


class PreviewRequest(BaseModel):
    staged_ttl_hours: int = Field(default=72, ge=0, le=24 * 365)
    orphan_ttl_days: int = Field(default=14, ge=0, le=3650)
    export_ttl_days: int = Field(default=7, ge=0, le=3650)
    media_ttl_days: int = Field(default=7, ge=0, le=3650)
    quarantine_ttl_days: int = Field(default=30, ge=0, le=3650)
    prune_url_audio: bool = False
    max_items: int = Field(default=1000, ge=1, le=100000)
    max_bytes: int = Field(default=20 * 1024**3, ge=1)


class ApplyRequest(BaseModel):
    plan_id: str
    confirm: str


_state_lock = threading.RLock()
_state: dict[str, Any] = {
    'phase': 'idle', 'plan_id': None, 'plan_expires_at': None, 'plan': None,
    'policy': None, 'operation_id': None, 'started_at': None, 'finished_at': None,
    'progress': {'current': 0, 'total': 0, 'percent': 0, 'bytes_reclaimed': 0},
    'results': [], 'logs': [], 'error': None,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_local(request: Request) -> None:
    host = request.client.host if request.client else ''
    if host == 'localhost':
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise HTTPException(status_code=403, detail='storage cleanup is available from localhost only')


def _policy_from_request(body: PreviewRequest) -> CleanupPolicy:
    payload = body.model_dump() if hasattr(body, 'model_dump') else body.dict()
    return CleanupPolicy(**payload)


def _append_log(message: str, level: str = 'info') -> None:
    with _state_lock:
        _state['logs'].append({'timestamp': _now_iso(), 'level': level, 'message': message})
        del _state['logs'][:-MAX_LOG_ENTRIES]


def _active_jobs() -> int:
    """活跃任务数（轻量 COUNT，不拉整行——/status 轮询高频调用）"""
    return count_jobs_with_status(ACTIVE_JOB_STATUSES)


def _read_audit(limit: int = MAX_AUDIT_ENTRIES) -> list[dict[str, Any]]:
    audit_path = Path(DATA_DIR) / 'cleanup_audit.jsonl'
    if not audit_path.is_file():
        return []
    try:
        lines = audit_path.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        logger.exception('Unable to read storage cleanup audit log')
        return []
    events: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _public_state(include_audit: bool = True) -> dict[str, Any]:
    with _state_lock:
        plan = _state['plan']
        expires_at = _state['plan_expires_at']
        if _state['phase'] == 'ready' and expires_at and time.time() >= expires_at:
            _state['phase'] = 'expired'
            _state['plan'] = None
            plan = None
            _state['error'] = 'preview plan expired; run preview again'
        payload = {
            'phase': _state['phase'],
            'plan_id': _state['plan_id'] if plan is not None else None,
            'plan_expires_at': _state['plan_expires_at'],
            'operation_id': _state['operation_id'],
            'started_at': _state['started_at'],
            'finished_at': _state['finished_at'],
            'progress': dict(_state['progress']),
            'results': list(_state['results']),
            'logs': list(_state['logs']),
            'error': _state['error'],
            'plan': plan.to_dict() if plan is not None else None,
            'policy': dict(_state['policy']) if _state['policy'] else None,
        }
    payload['active_jobs'] = _active_jobs()
    payload['audit'] = _read_audit(20) if include_audit else []
    return payload


def _handle_progress(event: dict[str, Any]) -> None:
    event_type = event.get('event')
    with _state_lock:
        if event_type == 'started':
            total = int(event.get('candidate_count', 0))
            _state['progress'] = {
                'current': 0, 'total': total, 'percent': 0, 'bytes_reclaimed': 0,
            }
            message = f'Cleanup started with {total} candidate(s)'
        elif event_type == 'item':
            result = dict(event.get('result') or {})
            current = int(event.get('index', 0))
            total = int(event.get('total', 0))
            _state['results'].append(result)
            _state['progress']['current'] = current
            _state['progress']['total'] = total
            _state['progress']['percent'] = round(current * 100 / total) if total else 100
            _state['progress']['bytes_reclaimed'] += int(result.get('bytes_reclaimed', 0))
            name = Path(str(result.get('path', ''))).name
            item_status = result.get('status', 'unknown')
            message = f'{current}/{total} {item_status}: {name}'
        elif event_type == 'finished':
            _state['progress']['percent'] = 100
            deleted = event.get('deleted_count', 0)
            quarantined = event.get('quarantined_count', 0)
            skipped = event.get('skipped_count', 0)
            message = (
                f'Cleanup finished: {deleted} deleted, '
                f'{quarantined} quarantined, {skipped} skipped'
            )
        else:
            return
    _append_log(message)


def _run_apply(plan: CleanupPlan, policy: CleanupPolicy) -> None:
    try:
        cleanup = StorageCleanup(DATA_DIR, DOWNLOAD_DIR, policy=policy)
        cleanup.apply(plan, progress_callback=_handle_progress)
    except Exception as exc:
        logger.exception('Storage cleanup operation failed')
        with _state_lock:
            _state['phase'] = 'failed'
            _state['error'] = str(exc)
            _state['finished_at'] = _now_iso()
        _append_log(f'Cleanup failed: {exc}', 'error')
        return
    with _state_lock:
        _state['phase'] = 'completed'
        _state['plan'] = None
        _state['finished_at'] = _now_iso()
        _state['error'] = None


@router.get('/status')
async def cleanup_status(request: Request):
    _require_local(request)
    return _public_state()


@router.get('/audit')
async def cleanup_audit(request: Request, limit: int = 100):
    _require_local(request)
    return {'events': _read_audit(max(1, min(limit, MAX_AUDIT_ENTRIES)))}


@router.post('/preview')
async def cleanup_preview(body: PreviewRequest, request: Request):
    _require_local(request)
    policy = _policy_from_request(body)
    with _state_lock:
        if _state['phase'] in {'previewing', 'running'}:
            raise HTTPException(status_code=409, detail='a cleanup operation is already active')
        _state.update({
            'phase': 'previewing', 'plan_id': None, 'plan_expires_at': None, 'plan': None,
            'policy': asdict(policy), 'operation_id': None, 'started_at': _now_iso(),
            'finished_at': None,
            'progress': {'current': 0, 'total': 0, 'percent': 0, 'bytes_reclaimed': 0},
            'results': [], 'logs': [], 'error': None,
        })
    _append_log('Scanning managed storage in dry-run mode')
    try:
        plan = await asyncio.to_thread(StorageCleanup(DATA_DIR, DOWNLOAD_DIR, policy=policy).scan)
    except Exception as exc:
        with _state_lock:
            _state['phase'] = 'failed'
            _state['error'] = str(exc)
            _state['finished_at'] = _now_iso()
        _append_log(f'Preview failed: {exc}', 'error')
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    plan_id = uuid.uuid4().hex
    expires_at = time.time() + PLAN_TTL_SECONDS
    with _state_lock:
        _state['phase'] = 'ready'
        _state['plan_id'] = plan_id
        _state['plan_expires_at'] = expires_at
        _state['plan'] = plan
        _state['progress'] = {
            'current': 0, 'total': len(plan.candidates), 'percent': 0, 'bytes_reclaimed': 0,
        }
        _state['finished_at'] = _now_iso()
    _append_log(f'Preview ready: {len(plan.candidates)} candidate(s), {plan.total_bytes} bytes')
    return _public_state()


@router.post('/apply', status_code=202)
async def cleanup_apply(body: ApplyRequest, request: Request, background_tasks: BackgroundTasks):
    _require_local(request)
    if body.confirm != 'DELETE':
        raise HTTPException(status_code=400, detail='confirmation must be exactly DELETE')
    active = _active_jobs()
    if active:
        raise HTTPException(
            status_code=409,
            detail=f'cleanup is blocked while {active} job(s) are active',
        )
    with _state_lock:
        if _state['phase'] != 'ready' or _state['plan'] is None:
            raise HTTPException(status_code=409, detail='no executable preview plan; run preview again')
        if body.plan_id != _state['plan_id']:
            raise HTTPException(status_code=409, detail='preview plan is stale; run preview again')
        if time.time() >= float(_state['plan_expires_at'] or 0):
            _state['phase'] = 'expired'
            _state['plan'] = None
            raise HTTPException(status_code=409, detail='preview plan expired; run preview again')
        plan = _state['plan']
        policy = CleanupPolicy(**_state['policy'])
        operation_id = uuid.uuid4().hex
        _state['phase'] = 'running'
        _state['operation_id'] = operation_id
        _state['started_at'] = _now_iso()
        _state['finished_at'] = None
        _state['results'] = []
        _state['error'] = None
    _append_log('Execution confirmed; applying the latest preview plan')
    background_tasks.add_task(_run_apply, plan, policy)
    return {'accepted': True, 'operation_id': operation_id}


def reset_cleanup_state() -> None:
    '''Reset in-process state for isolated tests; never touches storage or audit files.'''
    with _state_lock:
        _state.update({
            'phase': 'idle', 'plan_id': None, 'plan_expires_at': None, 'plan': None,
            'policy': None, 'operation_id': None, 'started_at': None, 'finished_at': None,
            'progress': {'current': 0, 'total': 0, 'percent': 0, 'bytes_reclaimed': 0},
            'results': [], 'logs': [], 'error': None,
        })
