import os
import time
from pathlib import Path

import pytest

from services.storage_cleanup import (
    CleanupPlan,
    CleanupPolicy,
    CleanupSafetyError,
    StorageCleanup,
    validate_job_input_path,
)
from scripts import cleanup_storage as cleanup_cli


def _layout(tmp_path: Path):
    data = tmp_path / 'data'
    downloads = data / 'downloads'
    uploads = downloads / 'uploads'
    exports = data / 'exports'
    uploads.mkdir(parents=True)
    exports.mkdir()
    return data, downloads, uploads, exports


def _set_old(path: Path, days: int = 20):
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))
    return stamp


def test_dry_run_finds_old_unreferenced_upload_without_writes(tmp_path):
    data, downloads, uploads, _ = _layout(tmp_path)
    staged = uploads / 'old.mp3'
    staged.write_bytes(b'audio')
    _set_old(staged, 4)
    before = sorted(str(path.relative_to(data)) for path in data.rglob('*'))

    plan = StorageCleanup(data, downloads, jobs=[]).scan()

    assert [(item.category, Path(item.path).name) for item in plan.candidates] == [
        ('staged_unreferenced', 'old.mp3')
    ]
    assert staged.exists()
    assert before == sorted(str(path.relative_to(data)) for path in data.rglob('*'))
    assert not (data / '.cleanup.lock').exists()
    assert not (data / 'cleanup_audit.jsonl').exists()


def test_referenced_active_upload_is_not_a_candidate(tmp_path):
    data, downloads, uploads, _ = _layout(tmp_path)
    staged = uploads / 'active.mp3'
    staged.write_bytes(b'audio')
    _set_old(staged, 10)
    jobs = [{
        'id': '1234abcd',
        'status': 'transcribing',
        'input_file': str(staged),
        'options': {'input_type': 'file'},
        'files': {},
    }]

    plan = StorageCleanup(data, downloads, jobs=jobs).scan()

    assert plan.candidates == []
    assert staged.exists()


def test_completed_staged_duplicate_canonicalizes_before_delete(tmp_path, monkeypatch):
    data, downloads, uploads, _ = _layout(tmp_path)
    staged = uploads / 'clip.flv'
    staged.write_bytes(b'same-content')
    job_dir = downloads / '1234abcd'
    job_dir.mkdir()
    canonical = job_dir / staged.name
    canonical.write_bytes(staged.read_bytes())
    jobs = [{
        'id': '1234abcd',
        'status': 'completed',
        'input_file': str(staged),
        'options': {'input_type': 'file'},
        'files': {'audio': str(canonical)},
    }]
    calls = []

    def update(job_id, old_path, new_path):
        calls.append((job_id, old_path, new_path, staged.exists()))
        return True

    monkeypatch.setattr('services.storage_cleanup.update_job_input_file_if_matches', update)
    cleanup = StorageCleanup(data, downloads, jobs=jobs)
    plan = cleanup.scan()
    results = cleanup.apply(plan)

    assert [item.category for item in plan.candidates] == ['staged_completed_duplicate']
    assert results[0].status == 'deleted'
    assert calls == [('1234abcd', str(staged.resolve()), str(canonical.resolve()), True)]
    assert not staged.exists()
    assert canonical.exists()


def test_changed_candidate_is_skipped_at_apply_time(tmp_path):
    data, downloads, uploads, _ = _layout(tmp_path)
    staged = uploads / 'old.wav'
    staged.write_bytes(b'old')
    _set_old(staged, 4)
    cleanup = StorageCleanup(data, downloads, jobs=[])
    plan = cleanup.scan()
    staged.write_bytes(b'changed-after-plan')

    results = cleanup.apply(plan)

    assert results[0].status == 'skipped'
    assert 'changed' in results[0].detail
    assert staged.exists()


def test_orphan_job_directory_is_quarantined_not_deleted(tmp_path):
    data, downloads, _, _ = _layout(tmp_path)
    orphan = downloads / 'deadbeef'
    orphan.mkdir()
    (orphan / 'result.txt').write_text('keep recoverable', encoding='utf-8')
    _set_old(orphan, 20)
    cleanup = StorageCleanup(data, downloads, jobs=[])

    plan = cleanup.scan()
    results = cleanup.apply(plan)

    assert [item.category for item in plan.candidates] == ['orphan_job_dir']
    assert results[0].status == 'quarantined'
    assert not orphan.exists()
    target = Path(results[0].detail)
    assert (target / 'result.txt').read_text(encoding='utf-8') == 'keep recoverable'
    assert target.is_relative_to(data / '.cleanup_quarantine')


def test_expired_export_is_selected_but_recent_export_is_kept(tmp_path):
    data, downloads, _, exports = _layout(tmp_path)
    old = exports / 'old.pdf'
    recent = exports / 'recent.pdf'
    old.write_bytes(b'old')
    recent.write_bytes(b'recent')
    _set_old(old, 8)

    plan = StorageCleanup(data, downloads, jobs=[]).scan()

    assert [(item.category, Path(item.path).name) for item in plan.candidates] == [
        ('expired_export', 'old.pdf')
    ]
    assert recent.exists()


def test_url_audio_requires_opt_in_and_existing_transcript(tmp_path):
    data, downloads, _, _ = _layout(tmp_path)
    job_dir = downloads / '1234abcd'
    job_dir.mkdir()
    audio = job_dir / 'source.mp3'
    transcript = job_dir / 'source.srt'
    audio.write_bytes(b'audio')
    transcript.write_text('subtitle', encoding='utf-8')
    _set_old(audio, 8)
    jobs = [{
        'id': '1234abcd',
        'status': 'completed',
        'input_file': None,
        'options': {'input_type': 'url'},
        'files': {'audio': str(audio), 'srt': str(transcript)},
    }]

    default_plan = StorageCleanup(data, downloads, jobs=jobs).scan()
    opt_in_plan = StorageCleanup(
        data, downloads, jobs=jobs, policy=CleanupPolicy(prune_url_audio=True)
    ).scan()

    assert default_plan.candidates == []
    assert [item.category for item in opt_in_plan.candidates] == ['expired_url_audio']


def test_apply_aborts_before_writes_when_budget_is_exceeded(tmp_path):
    data, downloads, uploads, _ = _layout(tmp_path)
    staged = uploads / 'old.mp3'
    staged.write_bytes(b'12345')
    _set_old(staged, 4)
    cleanup = StorageCleanup(
        data, downloads, jobs=[], policy=CleanupPolicy(max_bytes=4)
    )
    plan = cleanup.scan()

    with pytest.raises(CleanupSafetyError, match='budget'):
        cleanup.apply(plan)

    assert staged.exists()
    assert not (data / '.cleanup.lock').exists()


def test_worker_path_validation_accepts_job_owned_file(tmp_path):
    _, downloads, _, _ = _layout(tmp_path)
    owned = downloads / '1234abcd' / 'input.mp3'
    owned.parent.mkdir()
    owned.write_bytes(b'audio')

    assert validate_job_input_path(str(owned), downloads, '1234abcd') == owned.resolve()


def test_worker_path_validation_rejects_arbitrary_file(tmp_path):
    _, downloads, _, _ = _layout(tmp_path)
    outside = tmp_path / 'secret.txt'
    outside.write_text('secret', encoding='utf-8')

    with pytest.raises(CleanupSafetyError, match='outside'):
        validate_job_input_path(str(outside), downloads, '1234abcd')


def test_cli_apply_plain_output_is_valid(monkeypatch, capsys):
    class FakeCleanup:
        def scan(self):
            return CleanupPlan('2026-07-23T00:00:00+00:00', [], [])

        def apply(self, plan):
            return []

    monkeypatch.setattr(cleanup_cli, '_backend_is_listening', lambda port: False)
    monkeypatch.setattr(cleanup_cli, 'StorageCleanup', lambda *args, **kwargs: FakeCleanup())

    assert cleanup_cli.main(['--apply', '--confirm', 'DELETE']) == 0
    assert 'applied: 0 items, 0 bytes reclaimed' in capsys.readouterr().out


def test_cli_rejects_negative_retention(capsys):
    assert cleanup_cli.main(['--staged-ttl-hours', '-1']) == 2
    assert 'cannot be negative' in capsys.readouterr().err
