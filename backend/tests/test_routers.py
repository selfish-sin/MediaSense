"""
路由层集成测试 — FastAPI TestClient + mock 外部依赖
"""
import pytest
import asyncio
import json
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    app = FastAPI()
    from routers.jobs import router as jobs_router
    from routers.models import router as models_router
    from routers.summary import router as summary_router
    from routers.export import router as export_router
    from routers.translate import router as translate_router
    from routers.bilibili import router as bilibili_router
    from routers.download import router as download_router
    from routers.templates import router as templates_router
    from routers.chat import router as chat_router
    from routers.search import router as search_router

    app.include_router(jobs_router, prefix="/api/jobs")
    app.include_router(models_router, prefix="/api/models")
    app.include_router(summary_router, prefix="/api/summary")
    app.include_router(export_router, prefix="/api/export")
    app.include_router(translate_router, prefix="/api/translate")
    app.include_router(bilibili_router, prefix="/api/bilibili")
    app.include_router(download_router, prefix="/api/download")
    app.include_router(templates_router, prefix="/api/templates")
    app.include_router(chat_router, prefix="/api/jobs")
    app.include_router(search_router, prefix="")
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


# ── Jobs ──
class TestJobsRouter:
    def test_upload_srt(self, client, tmp_path):
        srt = b"1\r\n00:00:00,000 --> 00:00:02,000\r\nhello\r\n"
        with patch("routers.jobs.UPLOAD_DIR", tmp_path):
            resp = client.post(
                "/api/jobs/upload",
                files={"file": ("nested\\caption.srt", srt, "application/x-subrip")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["file_name"] == "caption.srt"
        assert (tmp_path / Path(data["file_path"]).name).read_bytes() == srt

    def test_upload_empty_file_rejected(self, client, tmp_path):
        with patch("routers.jobs.UPLOAD_DIR", tmp_path):
            resp = client.post("/api/jobs/upload", files={"file": ("empty.srt", b"")})

        assert resp.status_code == 400
        assert "为空" in resp.json()["detail"]

    def test_submit_file_rejects_arbitrary_client_path(self, client, tmp_path):
        upload_dir = tmp_path / 'uploads'
        upload_dir.mkdir()
        outside = tmp_path / 'secret.mp3'
        outside.write_bytes(b'not an upload')
        with patch('routers.jobs.UPLOAD_DIR', upload_dir), \
             patch('routers.jobs.create_job') as mock_create:
            resp = client.post('/api/jobs', json={
                'input_type': 'file',
                'input_file_path': str(outside),
            })

        assert resp.status_code == 400
        mock_create.assert_not_called()

    def test_submit_file_claims_upload_before_start(self, client, tmp_path):
        downloads = tmp_path / 'downloads'
        upload_dir = downloads / 'uploads'
        upload_dir.mkdir(parents=True)
        staged = upload_dir / '1234abcd_clip.mp3'
        staged.write_bytes(b'audio')
        with patch('routers.jobs.UPLOAD_DIR', upload_dir), \
             patch('routers.jobs.DOWNLOAD_DIR', downloads), \
             patch('routers.jobs.create_job', return_value='1234abcd') as mock_create, \
             patch('routers.jobs.claim_staged_upload', return_value=True) as mock_claim, \
             patch('main.start_job') as mock_start:
            resp = client.post('/api/jobs', json={
                'input_type': 'file',
                'input_file_path': str(staged),
            })

        canonical = downloads / '1234abcd' / staged.name
        assert resp.status_code == 200
        assert not staged.exists()
        assert canonical.read_bytes() == b'audio'
        assert mock_create.call_args.kwargs['input_file'] == str(staged.resolve())
        mock_claim.assert_called_once_with('1234abcd', str(staged.resolve()), str(canonical.resolve()))
        mock_start.assert_called_once_with('1234abcd')

    @patch("routers.jobs.create_job", return_value="test-job-id")
    def test_submit_job(self, mock_create, client):
        resp = client.post("/api/jobs", json={"input_type": "text", "input_text": "hello"})
        assert resp.status_code == 200
        assert resp.json()["job_id"] == "test-job-id"

    @patch("routers.jobs.get_jobs", return_value=([], 0))
    def test_list_jobs(self, mock_list, client):
        resp = client.get("/api/jobs")
        assert resp.status_code == 200

    @patch("routers.jobs.get_jobs", return_value=([], 0))
    def test_list_jobs_keyword_forwarded(self, mock_list, client):
        """keyword 查询参数应透传给 get_jobs"""
        resp = client.get("/api/jobs?keyword=深度学习")
        assert resp.status_code == 200
        mock_list.assert_called_once()
        assert mock_list.call_args.kwargs.get("keyword") == "深度学习"

    @patch("routers.jobs.get_job", return_value=None)
    def test_get_job_404(self, mock_get, client):
        resp = client.get("/api/jobs/no")
        assert resp.status_code == 404

    @patch("routers.jobs.get_job")
    def test_get_job_200(self, mock_get, client):
        mock_get.return_value = {"id": "j1", "status": "completed", "title": "T",
            "options": {}, "files": {}, "tags": [], "token_usage": {}}
        resp = client.get("/api/jobs/j1")
        assert resp.status_code == 200

    @patch("routers.jobs.get_running_jobs", return_value=[])
    def test_running_jobs(self, mock_run, client):
        resp = client.get("/api/jobs/running")
        assert resp.status_code == 200

    @patch("routers.jobs.delete_job", return_value=True)
    def test_delete_job(self, mock_del, client):
        resp = client.delete("/api/jobs/j1")
        assert resp.status_code == 200

    @patch("routers.jobs.get_cleanup_jobs", return_value=[])
    @patch("routers.jobs.clear_jobs", return_value=3)
    def test_clear_jobs(self, mock_clear, mock_jobs, client):
        resp = client.post("/api/jobs/clear")
        assert resp.status_code == 200

    @patch("routers.jobs.get_cleanup_jobs", return_value=[{"id": "j1", "status": "pending"}])
    @patch("routers.jobs.get_job", return_value={"id": "j1", "status": "pending"})
    @patch("routers.jobs.clear_jobs")
    def test_clear_jobs_rejects_active_job(self, mock_clear, mock_get, mock_jobs, client):
        resp = client.post("/api/jobs/clear")
        assert resp.status_code == 409
        mock_clear.assert_not_called()

    @patch("routers.jobs.clear_jobs_by_status", return_value=2)
    def test_clear_by_status(self, mock_clear, client):
        resp = client.post("/api/jobs/clear/completed")
        assert resp.status_code == 200

    @patch("routers.jobs.get_job")
    @patch("routers.jobs.update_job")
    @patch("socket_handlers.request_cancel")
    def test_cancel_job(self, mock_cancel, mock_upd, mock_get, client):
        mock_get.return_value = {"id": "j1", "status": "summarizing"}
        resp = client.post("/api/jobs/j1/cancel")
        assert resp.status_code == 200
        mock_cancel.assert_called_once_with("j1")

    def test_completed_job_cannot_be_cancelled(self, client):
        with patch("routers.jobs.get_job", return_value={"id": "j1", "status": "completed"}), \
             patch("routers.jobs.update_job") as mock_update, \
             patch("socket_handlers.request_cancel") as mock_cancel:
            resp = client.post("/api/jobs/j1/cancel")

        assert resp.status_code == 400
        mock_update.assert_not_called()
        mock_cancel.assert_not_called()

    def test_cancel_wakes_preview_waiter(self):
        from socket_handlers import (
            _preview_events, check_cancelled, clear_cancel_flag,
            pop_preview_event, request_cancel,
        )
        event = asyncio.Event()
        _preview_events["j1"] = event
        try:
            request_cancel("j1")
            assert event.is_set()
            assert check_cancelled("j1") is True
        finally:
            pop_preview_event("j1")
            clear_cancel_flag("j1")

    @patch("routers.jobs.cancel_job")
    def test_batch_cancel(self, mock, client):
        resp = client.post("/api/jobs/batch-cancel", json={"job_ids": ["j1","j2"]})
        assert resp.status_code == 200

    def test_batch_cancel_includes_preview(self, client):
        with patch("routers.jobs.get_job", return_value={"id": "j1", "status": "preview"}), \
             patch("routers.jobs.update_job") as mock_update, \
             patch("socket_handlers.request_cancel") as mock_cancel:
            resp = client.post("/api/jobs/batch-cancel", json={"job_ids": ["j1"]})

        assert resp.status_code == 200
        assert resp.json()["cancelled"] == ["j1"]
        mock_cancel.assert_called_once_with("j1")
        mock_update.assert_called_once()

    def test_force_retry_clears_files_and_restarts(self, client, tmp_path):
        job_dir = tmp_path / "j1"
        job_dir.mkdir()
        (job_dir / "old.srt").write_text("old", encoding="utf-8")
        job = {
            "id": "j1",
            "status": "failed",
            "options": {"input_type": "url"},
            "files": {"srt": "old.srt"},
        }

        with patch("routers.jobs.get_job", return_value=job), \
             patch("routers.jobs.update_job") as mock_update, \
             patch("routers.jobs.DOWNLOAD_DIR", tmp_path), \
             patch("main.start_job") as mock_start, \
             patch("socket_handlers.clear_cancel_flag"):
            resp = client.post("/api/jobs/j1/retry?force=true")

        assert resp.status_code == 200
        assert not (job_dir / "old.srt").exists()
        mock_update.assert_called_once()
        assert mock_update.call_args.kwargs["options"]["_retry"] is True
        assert mock_update.call_args.kwargs["files"] is None
        assert mock_update.call_args.kwargs["result_text"] is None
        mock_start.assert_called_once_with("j1")

    def test_retry_rejected_while_old_task_is_still_running(self, client, tmp_path):
        job_dir = tmp_path / "j1"
        job_dir.mkdir()
        old_file = job_dir / "old.srt"
        old_file.write_text("old", encoding="utf-8")
        job = {"id": "j1", "status": "cancelled", "options": {"input_type": "url"}}

        with patch("routers.jobs.get_job", return_value=job), \
             patch("routers.jobs.DOWNLOAD_DIR", tmp_path), \
             patch("main.is_job_running", return_value=True), \
             patch("routers.jobs.update_job") as mock_update, \
             patch("main.start_job") as mock_start, \
             patch("socket_handlers.clear_cancel_flag") as mock_clear:
            resp = client.post("/api/jobs/j1/retry?force=true")

        assert resp.status_code == 409
        assert "仍在停止" in resp.json()["detail"]
        assert old_file.exists()
        mock_update.assert_not_called()
        mock_start.assert_not_called()
        mock_clear.assert_not_called()

    @patch("routers.jobs.delete_jobs_batch", return_value={"deleted_count":2,"deleted":["j1","j2"],"failed":[]})
    def test_batch_delete(self, mock, client):
        resp = client.post("/api/jobs/batch-delete", json={"job_ids":["j1","j2"]})
        assert resp.status_code == 200

    def test_batch_rejects_non_object_options(self, client):
        resp = client.post("/api/jobs/batch", json={
            "urls": ["https://example.com/video"],
            "options": ["invalid"],
        })

        assert resp.status_code == 400
        assert "options" in resp.json()["detail"]

    @patch("routers.jobs.create_job", return_value="job-1")
    @patch("main.start_job")
    @patch("routers.jobs.find_job_by_url")
    def test_batch_force_new_skips_history_dedup(self, mock_find, mock_start, mock_create, client):
        """force_new=True 时跳过 find_job_by_url 跨历史去重，允许重跑已处理过的 URL"""
        resp = client.post("/api/jobs/batch", json={
            "urls": ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
            "force_new": True,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["skipped"] == 0
        mock_find.assert_not_called()  # force_new=True 不查历史
        mock_create.assert_called_once()
        mock_start.assert_called_once_with("job-1")

    @patch("routers.jobs.create_job", return_value="job-2")
    @patch("main.start_job")
    @patch("routers.jobs.find_job_by_url")
    def test_batch_force_new_false_dedups_existing(self, mock_find, mock_start, mock_create, client):
        """force_new=False 时已有 completed 任务的 URL 被跳过，返回 duplicates"""
        mock_find.return_value = {"id": "old-job", "status": "completed", "title": "旧任务"}
        resp = client.post("/api/jobs/batch", json={
            "urls": ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
            "force_new": False,
        })
        # 全部 URL 都被判重复 → 400
        assert resp.status_code == 400
        assert "重复" in resp.json()["detail"]
        mock_find.assert_called_once()
        mock_create.assert_not_called()

    @patch("routers.jobs.create_job", return_value="job-bv")
    @patch("main.start_job")
    def test_batch_accepts_bare_bv(self, mock_start, mock_create, client):
        """裸 BV 号（无 https:// 前缀）经 sanitize_input_url 补全后能提交"""
        resp = client.post("/api/jobs/batch", json={
            "urls": ["BV1RGN462EBs"],
            "force_new": True,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        # create_job 收到的应是补全后的完整 URL（含 https://）
        submitted_url = mock_create.call_args.kwargs.get("input_url", "")
        assert submitted_url.startswith("https://"), f"裸 BV 应被补全为完整 URL, got: {submitted_url}"


# ── Models ──
class TestModelsRouter:
    @patch("routers.models.get_all_models", return_value=[])
    def test_list_models(self, mock_all, client):
        resp = client.get("/api/models")
        assert resp.status_code == 200

    @patch("routers.models.add_model", return_value=True)
    def test_add_model(self, mock_add, client):
        resp = client.post("/api/models", json={
            "key": "m1", "name": "Test", "endpoint": "https://api.example.com"
        })
        assert resp.status_code == 200

    @patch("routers.models.get_model_by_key")
    @patch("routers.models.update_model", return_value=True)
    def test_update_model(self, mock_upd, mock_get, client):
        mock_get.return_value = {"key":"m1","name":"Test","endpoint":"https://x.com",
            "auth_type":"bearer","temperature":0.3,"max_tokens":4096,"model_id":"gpt-4"}
        resp = client.put("/api/models/m1", json={"name":"Updated","endpoint":"https://x.com",
            "key":"m1"})
        assert resp.status_code == 200

    @patch("routers.models.delete_model", return_value=True)
    def test_delete_model(self, mock_del, client):
        resp = client.delete("/api/models/m1")
        assert resp.status_code == 200


# ── Summary ──
class TestSummaryRouter:
    def test_get_dimensions(self, client):
        resp = client.get("/api/summary/dimensions")
        assert resp.status_code == 200
        assert len(resp.json()["dimensions"]) == 8

    def test_get_recipes(self, client):
        resp = client.get("/api/summary/recipes")
        assert resp.status_code == 200
        assert len(resp.json()) > 0

    def test_get_defaults(self, client):
        resp = client.get("/api/summary/defaults")
        assert resp.status_code == 200


# ── Translate ──
class TestTranslateRouter:
    def test_get_langs(self, client):
        resp = client.get("/api/translate/langs")
        assert resp.status_code == 200


# ── Download ──
class TestDownloadRouter:
    def test_view_file_404(self, client):
        resp = client.get("/api/download/view?path=/nonexistent/file.txt")
        assert resp.status_code == 404

    def test_sibling_data_prefix_is_rejected(self, client, tmp_path):
        data_dir = tmp_path / "data"
        downloads_dir = data_dir / "downloads"
        sibling_dir = tmp_path / "data_backup"
        downloads_dir.mkdir(parents=True)
        sibling_dir.mkdir()
        secret = sibling_dir / "secret.txt"
        secret.write_text("secret", encoding="utf-8")

        with patch("routers.download._DATA_DIR_RESOLVED", data_dir.resolve()), \
             patch("routers.download.DOWNLOAD_DIR", downloads_dir):
            resp = client.get("/api/download/view", params={"path": str(secret)})

        assert resp.status_code == 403

    def test_file_inside_data_dir_is_allowed(self, client, tmp_path):
        data_dir = tmp_path / "data"
        downloads_dir = data_dir / "downloads"
        downloads_dir.mkdir(parents=True)
        allowed = downloads_dir / "result.txt"
        allowed.write_text("ok", encoding="utf-8")

        with patch("routers.download._DATA_DIR_RESOLVED", data_dir.resolve()), \
             patch("routers.download.DOWNLOAD_DIR", downloads_dir):
            resp = client.get("/api/download/view", params={"path": str(allowed)})

        assert resp.status_code == 200
        assert resp.text == "ok"


# ── Templates ──
class TestTemplatesRouter:
    @patch("db.prompt_override_repo.list_overrides", return_value=[])
    @patch("routers.templates.get_connection")
    def test_list_templates(self, mock_conn, mock_list, client):
        mock_conn.return_value.execute.return_value.fetchall.return_value = []
        resp = client.get("/api/templates")
        assert resp.status_code == 200


# ── Search ──
class TestSearchRouter:
    def test_search(self, client):
        mock_result = [{"title": "result", "url": "https://x.com", "snippet": "desc"}]
        with patch("routers.search.search_web", return_value=mock_result):
            with patch("routers.search.format_search_context", return_value="ctx"):
                resp = client.get("/api/search?q=python")
                assert resp.status_code == 200
                assert resp.json()["success"] is True


# ── Export ──
class TestExportRouter:
    @patch("routers.export.get_job", return_value=None)
    def test_export_404(self, mock_get, client):
        resp = client.post("/api/export", json={"job_id": "no", "format": "txt"})
        assert resp.status_code == 404


# ── Bilibili ──
class TestBilibiliRouter:
    @patch("routers.bilibili.get_status", return_value={"logged_in": False, "cookies_count": 0})
    def test_status(self, mock_status, client):
        resp = client.get("/api/bilibili/status")
        assert resp.status_code == 200
        assert resp.json()["logged_in"] is False

    @patch("routers.bilibili.clear_cookies")
    def test_logout(self, mock_clear, client):
        resp = client.post("/api/bilibili/logout")
        assert resp.status_code == 200


# ── Chat ──
class TestChatRouter:
    @patch("routers.chat.get_job", return_value={"id": "j1", "status": "completed"})
    @patch("routers.chat.get_connection")
    def test_chat_history(self, mock_conn, mock_get, client):
        mock_conn.return_value.execute.return_value.fetchall.return_value = []
        resp = client.get("/api/jobs/j1/chat/history")
        assert resp.status_code == 200
