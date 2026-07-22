'''Policy-driven storage cleanup with dry-run planning and apply-time checks.'''
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from db.job_repo import (
    get_cleanup_jobs,
    remove_job_file_reference_if_matches,
    update_job_input_file_if_matches,
)


JOB_DIR_PATTERN = re.compile(
    r'^(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-'
    r'[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12})$'
)
STAGED_UPLOAD_PATTERN = re.compile(r'^[0-9a-f]{8}_.+')
REPARSE_POINT = 0x400


class CleanupSafetyError(RuntimeError):
    '''Raised when cleanup cannot prove that an operation is safe.'''


@dataclass(frozen=True)
class CleanupPolicy:
    staged_ttl_hours: int = 72
    orphan_ttl_days: int = 14
    export_ttl_days: int = 7
    media_ttl_days: int = 7
    quarantine_ttl_days: int = 30
    prune_url_audio: bool = False
    max_items: int = 1000
    max_bytes: int = 20 * 1024**3

    def __post_init__(self) -> None:
        ttl_values = (
            self.staged_ttl_hours,
            self.orphan_ttl_days,
            self.export_ttl_days,
            self.media_ttl_days,
            self.quarantine_ttl_days,
        )
        if any(value < 0 for value in ttl_values):
            raise ValueError('cleanup retention values cannot be negative')
        if self.max_items <= 0 or self.max_bytes <= 0:
            raise ValueError('cleanup budgets must be positive')


@dataclass(frozen=True)
class CleanupCandidate:
    category: str
    path: str
    root: str
    action: str
    size: int
    mtime_ns: int
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProtectedPath:
    path: str
    reason: str


@dataclass
class CleanupPlan:
    created_at: str
    candidates: list[CleanupCandidate]
    protected: list[ProtectedPath]

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.candidates)

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        bytes_by_category: dict[str, int] = {}
        for item in self.candidates:
            counts[item.category] = counts.get(item.category, 0) + 1
            bytes_by_category[item.category] = bytes_by_category.get(item.category, 0) + item.size
        return {
            'dry_run': True,
            'created_at': self.created_at,
            'candidate_count': len(self.candidates),
            'total_bytes': self.total_bytes,
            'counts': counts,
            'bytes_by_category': bytes_by_category,
            'candidates': [asdict(item) for item in self.candidates],
            'protected': [asdict(item) for item in self.protected],
        }


@dataclass(frozen=True)
class CleanupResult:
    category: str
    path: str
    status: str
    bytes_reclaimed: int = 0
    detail: str = ''


def _is_reparse(path: Path) -> bool:
    try:
        stat = path.lstat()
    except OSError:
        return False
    return path.is_symlink() or bool(getattr(stat, 'st_file_attributes', 0) & REPARSE_POINT)


def _within(path: Path, root: Path) -> bool:
    try:
        return path.resolve(strict=False).is_relative_to(root.resolve(strict=False))
    except (OSError, RuntimeError):
        return False


def validate_job_input_path(raw_path: str, download_dir: Path, job_id: str) -> Path:
    '''Validate a persisted file input before any worker reads or copies it.'''
    if not raw_path:
        raise CleanupSafetyError('file job has no input path')
    if not JOB_DIR_PATTERN.fullmatch(job_id):
        raise CleanupSafetyError('file job id is not a managed directory id')
    source = Path(raw_path).expanduser()
    try:
        stat = source.lstat()
        resolved = source.resolve(strict=True)
        root = Path(download_dir).resolve(strict=True)
    except OSError as exc:
        raise CleanupSafetyError('file job input does not exist') from exc
    if source.is_symlink() or bool(getattr(stat, 'st_file_attributes', 0) & REPARSE_POINT):
        raise CleanupSafetyError('file job input cannot be a link')
    if not resolved.is_file() or not _within(resolved, root):
        raise CleanupSafetyError('file job input is outside managed downloads')
    job_root = (root / job_id).resolve(strict=False)
    staged_root = (root / 'uploads').resolve(strict=False)
    is_staged = resolved.parent == staged_root and STAGED_UPLOAD_PATTERN.fullmatch(resolved.name)
    if not (_within(resolved, job_root) or is_staged):
        raise CleanupSafetyError('file job input is not owned by this job or upload staging')
    return resolved


def _age_seconds(path: Path, now: float) -> float:
    return max(0.0, now - path.stat().st_mtime)


def _tree_size(path: Path) -> Optional[int]:
    if _is_reparse(path):
        return None
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob('*'):
        if _is_reparse(child):
            return None
        if child.is_file():
            total += child.stat().st_size
    return total


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+b')
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CleanupSafetyError('another cleanup process holds the storage lock') from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


class StorageCleanup:
    def __init__(
        self,
        data_dir: Path,
        download_dir: Path,
        jobs: Optional[list[dict[str, Any]]] = None,
        policy: Optional[CleanupPolicy] = None,
    ) -> None:
        self.data_dir = Path(data_dir).resolve(strict=False)
        self.download_dir = Path(download_dir).resolve(strict=False)
        self.upload_dir = self.download_dir / 'uploads'
        self.export_dir = self.data_dir / 'exports'
        self.quarantine_dir = self.data_dir / '.cleanup_quarantine'
        self.audit_path = self.data_dir / 'cleanup_audit.jsonl'
        self.lock_path = self.data_dir / '.cleanup.lock'
        self.jobs = jobs
        self.policy = policy or CleanupPolicy()

    def _candidate(
        self,
        path: Path,
        root: Path,
        category: str,
        action: str,
        reason: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> CleanupCandidate | ProtectedPath:
        resolved = path.resolve(strict=False)
        root_resolved = root.resolve(strict=False)
        if resolved == root_resolved or not _within(resolved, root_resolved):
            return ProtectedPath(str(path), 'path is outside its allowed root or is the root')
        if not path.exists():
            return ProtectedPath(str(path), 'path disappeared during scan')
        if _is_reparse(path):
            return ProtectedPath(str(path), 'symlinks and reparse points are never cleaned')
        size = _tree_size(path)
        if size is None:
            return ProtectedPath(str(path), 'tree contains a symlink or reparse point')
        stat = path.stat()
        return CleanupCandidate(
            category=category,
            path=str(resolved),
            root=str(root_resolved),
            action=action,
            size=size,
            mtime_ns=stat.st_mtime_ns,
            reason=reason,
            metadata=metadata or {},
        )

    @staticmethod
    def _append(
        item: CleanupCandidate | ProtectedPath,
        candidates: list[CleanupCandidate],
        protected: list[ProtectedPath],
    ) -> None:
        if isinstance(item, CleanupCandidate):
            candidates.append(item)
        else:
            protected.append(item)

    def scan(self, now: Optional[float] = None) -> CleanupPlan:
        current = time.time() if now is None else now
        jobs = self.jobs if self.jobs is not None else get_cleanup_jobs(read_only=True)
        candidates: list[CleanupCandidate] = []
        protected: list[ProtectedPath] = []
        self._scan_uploads(jobs, current, candidates, protected)
        self._scan_orphan_dirs(jobs, current, candidates, protected)
        self._scan_exports(current, candidates, protected)
        self._scan_url_audio(jobs, current, candidates, protected)
        self._scan_quarantine(current, candidates, protected)
        candidates.sort(key=lambda item: (item.category, item.path.casefold()))
        protected.sort(key=lambda item: item.path.casefold())
        created = datetime.fromtimestamp(current, tz=timezone.utc).isoformat()
        return CleanupPlan(created, candidates, protected)

    def _scan_uploads(
        self,
        jobs: list[dict[str, Any]],
        now: float,
        candidates: list[CleanupCandidate],
        protected: list[ProtectedPath],
    ) -> None:
        if not self.upload_dir.is_dir() or _is_reparse(self.upload_dir):
            return
        refs: dict[Path, list[dict[str, Any]]] = {}
        for job in jobs:
            raw = job.get('input_file')
            if raw:
                refs.setdefault(Path(raw).resolve(strict=False), []).append(job)
        ttl = self.policy.staged_ttl_hours * 3600
        for path in self.upload_dir.iterdir():
            if not path.is_file() or _is_reparse(path):
                protected.append(ProtectedPath(str(path), 'only regular staged files are eligible'))
                continue
            owners = refs.get(path.resolve(strict=False), [])
            if not owners:
                if _age_seconds(path, now) >= ttl:
                    item = self._candidate(
                        path, self.upload_dir, 'staged_unreferenced', 'delete',
                        f'unreferenced staged upload older than {self.policy.staged_ttl_hours} hours',
                    )
                    self._append(item, candidates, protected)
                continue
            if len(owners) != 1:
                protected.append(ProtectedPath(str(path), 'staged upload has multiple job owners'))
                continue
            job = owners[0]
            if job.get('status') != 'completed' or (job.get('options') or {}).get('input_type') != 'file':
                continue
            canonical = self.download_dir / str(job['id']) / path.name
            if not canonical.is_file() or _is_reparse(canonical):
                protected.append(ProtectedPath(str(path), 'completed job has no safe canonical copy'))
                continue
            if canonical.stat().st_size != path.stat().st_size:
                protected.append(ProtectedPath(str(path), 'canonical copy size differs from staged upload'))
                continue
            item = self._candidate(
                path, self.upload_dir, 'staged_completed_duplicate', 'delete',
                'completed file job has a same-name and same-size job-local copy',
                {'job_id': job['id'], 'canonical_path': str(canonical.resolve(strict=False))},
            )
            self._append(item, candidates, protected)

    def _scan_orphan_dirs(
        self,
        jobs: list[dict[str, Any]],
        now: float,
        candidates: list[CleanupCandidate],
        protected: list[ProtectedPath],
    ) -> None:
        if not self.download_dir.is_dir() or _is_reparse(self.download_dir):
            return
        known = {str(job['id']) for job in jobs}
        ttl = self.policy.orphan_ttl_days * 86400
        for path in self.download_dir.iterdir():
            if not path.is_dir() or path.name == 'uploads' or not JOB_DIR_PATTERN.fullmatch(path.name):
                continue
            if path.name in known or _age_seconds(path, now) < ttl:
                continue
            item = self._candidate(
                path, self.download_dir, 'orphan_job_dir', 'quarantine',
                f'job-like directory has no database row and is older than {self.policy.orphan_ttl_days} days',
            )
            self._append(item, candidates, protected)

    def _scan_exports(
        self,
        now: float,
        candidates: list[CleanupCandidate],
        protected: list[ProtectedPath],
    ) -> None:
        if not self.export_dir.is_dir() or _is_reparse(self.export_dir):
            return
        ttl = self.policy.export_ttl_days * 86400
        for path in self.export_dir.rglob('*'):
            if not path.is_file():
                continue
            if _is_reparse(path):
                protected.append(ProtectedPath(str(path), 'export is a symlink or reparse point'))
                continue
            if _age_seconds(path, now) < ttl:
                continue
            item = self._candidate(
                path, self.export_dir, 'expired_export', 'delete',
                f'export is older than {self.policy.export_ttl_days} days',
            )
            self._append(item, candidates, protected)

    def _scan_url_audio(
        self,
        jobs: list[dict[str, Any]],
        now: float,
        candidates: list[CleanupCandidate],
        protected: list[ProtectedPath],
    ) -> None:
        if not self.policy.prune_url_audio:
            return
        ttl = self.policy.media_ttl_days * 86400
        for job in jobs:
            options = job.get('options') or {}
            files = job.get('files') or {}
            if job.get('status') != 'completed' or options.get('input_type') != 'url':
                continue
            if options.get('keep_audio') or options.get('download_audio') or options.get('save_audio'):
                continue
            raw_audio = files.get('audio')
            transcripts = [files.get('srt'), files.get('txt')]
            if not raw_audio or not any(raw and Path(raw).is_file() for raw in transcripts):
                continue
            audio = Path(raw_audio)
            job_root = self.download_dir / str(job['id'])
            if not audio.is_file() or _age_seconds(audio, now) < ttl:
                continue
            item = self._candidate(
                audio, job_root, 'expired_url_audio', 'delete',
                f'completed URL media is older than {self.policy.media_ttl_days} days and has a transcript',
                {'job_id': job['id'], 'file_role': 'audio'},
            )
            self._append(item, candidates, protected)

    def _scan_quarantine(
        self,
        now: float,
        candidates: list[CleanupCandidate],
        protected: list[ProtectedPath],
    ) -> None:
        if not self.quarantine_dir.is_dir() or _is_reparse(self.quarantine_dir):
            return
        ttl = self.policy.quarantine_ttl_days * 86400
        for path in self.quarantine_dir.iterdir():
            if _age_seconds(path, now) < ttl:
                continue
            item = self._candidate(
                path, self.quarantine_dir, 'quarantine_expired', 'delete',
                f'quarantine item is older than {self.policy.quarantine_ttl_days} days',
            )
            self._append(item, candidates, protected)

    def _validate_candidate(self, item: CleanupCandidate) -> Path:
        path = Path(item.path)
        root = Path(item.root)
        expected_roots = {
            'staged_unreferenced': self.upload_dir,
            'staged_completed_duplicate': self.upload_dir,
            'orphan_job_dir': self.download_dir,
            'expired_export': self.export_dir,
            'quarantine_expired': self.quarantine_dir,
        }
        expected_root = expected_roots.get(item.category)
        if item.category == 'expired_url_audio':
            expected_root = self.download_dir / str(item.metadata.get('job_id', ''))
        if expected_root is None or root.resolve(strict=False) != expected_root.resolve(strict=False):
            raise CleanupSafetyError('candidate root is not an allowed storage root')
        if path == root or not _within(path, root) or _is_reparse(path):
            raise CleanupSafetyError('candidate is outside its captured root or is a reparse point')
        if not path.exists():
            raise CleanupSafetyError('candidate disappeared after planning')
        current_size = _tree_size(path)
        if current_size is None or current_size != item.size:
            raise CleanupSafetyError('candidate size changed after planning')
        if path.stat().st_mtime_ns != item.mtime_ns:
            raise CleanupSafetyError('candidate mtime changed after planning')
        if item.category == 'staged_completed_duplicate':
            canonical = Path(str(item.metadata.get('canonical_path', '')))
            if not _within(canonical, self.download_dir) or _is_reparse(canonical):
                raise CleanupSafetyError('canonical copy is outside downloads or unsafe')
            if not canonical.is_file() or canonical.stat().st_size != item.size:
                raise CleanupSafetyError('canonical copy changed after planning')
        return path

    def _audit(self, payload: dict[str, Any]) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + '\n')

    def apply(
        self,
        plan: CleanupPlan,
        progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> list[CleanupResult]:
        if len(plan.candidates) > self.policy.max_items:
            raise CleanupSafetyError('cleanup item budget exceeded')
        if plan.total_bytes > self.policy.max_bytes:
            raise CleanupSafetyError('cleanup byte budget exceeded')
        results: list[CleanupResult] = []
        with _exclusive_lock(self.lock_path):
            started_event = {
                'event': 'started',
                'candidate_count': len(plan.candidates),
                'planned_bytes': plan.total_bytes,
            }
            if progress_callback:
                progress_callback(started_event)
            self._audit({
                'event': 'cleanup_started',
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'pid': os.getpid(),
                'host': socket.gethostname(),
                'candidate_count': len(plan.candidates),
                'planned_bytes': plan.total_bytes,
            })
            for index, item in enumerate(plan.candidates, start=1):
                try:
                    path = self._validate_candidate(item)
                    if item.category == 'staged_completed_duplicate':
                        job_id = str(item.metadata['job_id'])
                        canonical = str(Path(item.metadata['canonical_path']).resolve(strict=False))
                        if not update_job_input_file_if_matches(job_id, str(path), canonical):
                            raise CleanupSafetyError('job input path changed or job is no longer completed')
                    if item.action == 'quarantine':
                        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
                        target = self.quarantine_dir / f'{int(time.time())}_{path.name}'
                        if target.exists():
                            raise CleanupSafetyError('quarantine target already exists')
                        path.rename(target)
                        os.utime(target, None)
                        result = CleanupResult(item.category, str(path), 'quarantined', 0, str(target))
                    else:
                        if path.is_dir():
                            shutil.rmtree(path)
                        else:
                            path.unlink()
                        if item.category == 'expired_url_audio':
                            metadata_updated = remove_job_file_reference_if_matches(
                                str(item.metadata['job_id']), str(item.metadata['file_role']), str(path)
                            )
                            detail = '' if metadata_updated else 'file deleted; metadata reference changed concurrently'
                        else:
                            detail = ''
                        result = CleanupResult(item.category, str(path), 'deleted', item.size, detail)
                except Exception as exc:
                    result = CleanupResult(item.category, item.path, 'skipped', 0, str(exc))
                results.append(result)
                if progress_callback:
                    progress_callback({
                        'event': 'item',
                        'index': index,
                        'total': len(plan.candidates),
                        'result': asdict(result),
                    })
                self._audit({
                    'event': 'cleanup_item',
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    **asdict(result),
                })
            summary = {
                'deleted_count': sum(result.status == 'deleted' for result in results),
                'quarantined_count': sum(result.status == 'quarantined' for result in results),
                'skipped_count': sum(result.status == 'skipped' for result in results),
                'bytes_reclaimed': sum(result.bytes_reclaimed for result in results),
            }
            self._audit({
                'event': 'cleanup_finished',
                'timestamp': datetime.now(timezone.utc).isoformat(),
                **summary,
            })
            if progress_callback:
                progress_callback({'event': 'finished', **summary})
        return results
