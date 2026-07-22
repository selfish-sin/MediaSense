import os
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import storage_cleanup as cleanup_api


@pytest.fixture()
def storage_api(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    downloads = data / 'downloads'
    uploads = downloads / 'uploads'
    uploads.mkdir(parents=True)
    monkeypatch.setattr(cleanup_api, 'DATA_DIR', data)
    monkeypatch.setattr(cleanup_api, 'DOWNLOAD_DIR', downloads)
    monkeypatch.setattr(cleanup_api, '_active_jobs', lambda: [])
    cleanup_api.reset_cleanup_state()
    app = FastAPI()
    app.include_router(cleanup_api.router, prefix='/api/storage-cleanup')
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        yield client, data, downloads, uploads
    cleanup_api.reset_cleanup_state()


def _old_candidate(uploads: Path, name: str = 'old.mp3') -> Path:
    path = uploads / name
    path.write_bytes(b'candidate')
    stamp = time.time() - 4 * 86400
    os.utime(path, (stamp, stamp))
    return path


def test_remote_requests_are_rejected(tmp_path):
    cleanup_api.reset_cleanup_state()
    app = FastAPI()
    app.include_router(cleanup_api.router, prefix='/api/storage-cleanup')
    with TestClient(app, client=('192.168.1.20', 50000)) as client:
        response = client.get('/api/storage-cleanup/status')
    assert response.status_code == 403


def test_preview_is_read_only_and_returns_executable_plan(storage_api):
    client, data, _, uploads = storage_api
    candidate = _old_candidate(uploads)

    response = client.post('/api/storage-cleanup/preview', json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload['phase'] == 'ready'
    assert payload['plan_id']
    assert payload['plan']['candidate_count'] == 1
    assert candidate.exists()
    assert not (data / '.cleanup.lock').exists()
    assert not (data / 'cleanup_audit.jsonl').exists()


def test_apply_requires_literal_confirmation(storage_api):
    client, _, _, uploads = storage_api
    candidate = _old_candidate(uploads)
    plan_id = client.post('/api/storage-cleanup/preview', json={}).json()['plan_id']

    response = client.post('/api/storage-cleanup/apply', json={
        'plan_id': plan_id, 'confirm': 'delete',
    })

    assert response.status_code == 400
    assert candidate.exists()


def test_new_preview_invalidates_previous_plan(storage_api):
    client, _, _, uploads = storage_api
    candidate = _old_candidate(uploads)
    old_plan = client.post('/api/storage-cleanup/preview', json={}).json()['plan_id']
    new_plan = client.post('/api/storage-cleanup/preview', json={}).json()['plan_id']

    response = client.post('/api/storage-cleanup/apply', json={
        'plan_id': old_plan, 'confirm': 'DELETE',
    })

    assert old_plan != new_plan
    assert response.status_code == 409
    assert candidate.exists()


def test_expired_preview_cannot_be_applied(storage_api, monkeypatch):
    client, _, _, uploads = storage_api
    candidate = _old_candidate(uploads)
    preview = client.post('/api/storage-cleanup/preview', json={}).json()
    monkeypatch.setattr(cleanup_api.time, 'time', lambda: preview['plan_expires_at'] + 1)

    response = client.post('/api/storage-cleanup/apply', json={
        'plan_id': preview['plan_id'], 'confirm': 'DELETE',
    })
    status = client.get('/api/storage-cleanup/status').json()

    assert response.status_code == 409
    assert status['phase'] == 'expired'
    assert status['plan_id'] is None
    assert candidate.exists()


def test_preview_is_rejected_while_cleanup_is_running(storage_api):
    client, _, _, uploads = storage_api
    candidate = _old_candidate(uploads)
    with cleanup_api._state_lock:
        cleanup_api._state['phase'] = 'running'

    response = client.post('/api/storage-cleanup/preview', json={})

    assert response.status_code == 409
    assert candidate.exists()


def test_apply_is_blocked_while_jobs_are_active(storage_api, monkeypatch):
    client, _, _, uploads = storage_api
    candidate = _old_candidate(uploads)
    plan_id = client.post('/api/storage-cleanup/preview', json={}).json()['plan_id']
    monkeypatch.setattr(cleanup_api, '_active_jobs', lambda: [{'id': 'active'}])

    response = client.post('/api/storage-cleanup/apply', json={
        'plan_id': plan_id, 'confirm': 'DELETE',
    })

    assert response.status_code == 409
    assert candidate.exists()


def test_status_reports_item_progress_and_completion(storage_api):
    client, data, _, uploads = storage_api
    candidate = _old_candidate(uploads)
    plan_id = client.post('/api/storage-cleanup/preview', json={}).json()['plan_id']

    response = client.post('/api/storage-cleanup/apply', json={
        'plan_id': plan_id, 'confirm': 'DELETE',
    })
    status = client.get('/api/storage-cleanup/status').json()

    assert response.status_code == 202
    assert status['phase'] == 'completed'
    assert status['progress']['current'] == 1
    assert status['progress']['percent'] == 100
    assert status['results'][0]['status'] == 'deleted'
    assert not candidate.exists()
    assert (data / 'cleanup_audit.jsonl').is_file()
