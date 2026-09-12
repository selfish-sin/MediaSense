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
    from routers.jobs_ai import router as jobs_ai_router
    from routers.jobs_batch import router as jobs_batch_router
    from routers.models import router as models_router
    from routers.asr import router as asr_router
    from routers.summary import router as summary_router
    from routers.export import router as export_router
    from routers.bilibili import router as bilibili_router
    from routers.download import router as download_router
    from routers.templates import router as templates_router
    from routers.chat import router as chat_router
    from routers.search import router as search_router

    app.include_router(jobs_router, prefix="/api/jobs")
    app.include_router(jobs_ai_router, prefix="/api/jobs")
    app.include_router(jobs_batch_router, prefix="/api/jobs")
    app.include_router(models_router, prefix="/api/models")
    app.include_router(asr_router, prefix="/api/asr")
    app.include_router(summary_router, prefix="/api/summary")
    app.include_router(export_router, prefix="/api/export")
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

    @patch("routers.jobs_batch.create_job", return_value="job-1")
    @patch("main.start_job")
    @patch("routers.jobs_batch.find_job_by_url")
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

    @patch("routers.jobs_batch.create_job", return_value="job-2")
    @patch("main.start_job")
    @patch("routers.jobs_batch.find_job_by_url")
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

    @patch("routers.jobs_batch.create_job", return_value="job-bv")
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


# ── 重新总结（失败任务仅重跑 AI 步骤）──
class TestResummarizeRouter:
    def _job(self, tmp_path, status="failed"):
        txt = tmp_path / "job.txt"
        txt.write_text("这是转录文本内容", encoding="utf-8")
        return {
            "id": "j1", "status": status, "model": "m1", "summary_type": "summary",
            "files": {"txt": str(txt)}, "options": {},
            "result_text": None, "summary_version": 1, "tags": [], "token_usage": {},
        }

    @patch("routers.jobs_ai.get_job")
    @patch("routers.jobs_ai.update_job")
    def test_failed_job_can_resummarize(self, mock_update, mock_get, client, tmp_path):
        mock_get.return_value = self._job(tmp_path, status="failed")
        with patch("services.ai_processor.ai_process",
                   new=AsyncMock(return_value=("结果内容", 10, 20))), \
             patch("services.ai_processor.generate_tags", new=AsyncMock(return_value=["tag"])), \
             patch("db.job_repo.save_summary_version", return_value=True), \
             patch("main.sio", None):
            resp = client.post("/api/jobs/j1/summarize", json={"model": "m1"})
        assert resp.status_code == 200
        assert resp.json()["result_text"] == "结果内容"
        # 任务被标记回 completed
        assert any(c.kwargs.get("status") == "completed" for c in mock_update.call_args_list)

    @patch("routers.jobs_ai.get_job")
    @patch("routers.jobs_ai.update_job")
    def test_sentinel_failure_not_written_as_result(self, mock_update, mock_get, client, tmp_path):
        """AI 层返回 [处理失败: ...] 哨兵串时不得写盘并标记 completed"""
        mock_get.return_value = self._job(tmp_path, status="failed")
        with patch("services.ai_processor.ai_process",
                   new=AsyncMock(return_value=("[处理失败: list index out of range]", 0, 0))), \
             patch("services.ai_processor.generate_tags", new=AsyncMock(return_value=[])), \
             patch("db.job_repo.save_summary_version", return_value=True), \
             patch("main.sio", None):
            resp = client.post("/api/jobs/j1/summarize", json={})
        assert resp.status_code == 500
        assert any(c.kwargs.get("status") == "failed" for c in mock_update.call_args_list)
        assert not any(c.kwargs.get("status") == "completed" for c in mock_update.call_args_list)

    @patch("routers.jobs_ai.get_job")
    def test_cancelled_job_rejected(self, mock_get, client, tmp_path):
        mock_get.return_value = self._job(tmp_path, status="cancelled")
        resp = client.post("/api/jobs/j1/summarize", json={})
        assert resp.status_code == 400

    @patch("routers.jobs_ai.get_job")
    def test_failed_job_without_txt_rejected(self, mock_get, client):
        mock_get.return_value = {"id": "j1", "status": "failed", "files": {}, "options": {}}
        resp = client.post("/api/jobs/j1/summarize", json={})
        assert resp.status_code == 400


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
    @patch("routers.models.add_model", return_value=False)
    def test_add_model_duplicate_key_message(self, mock_add, mock_get, client):
        """重复 key 失败时直接指出冲突项与出路（已存在 → 提示编辑而非新增）"""
        mock_get.return_value = {"key": "longcat-2-0", "name": "LongCat-2.0", "endpoint": "https://x.com"}
        resp = client.post("/api/models", json={
            "key": "longcat-2-0", "name": "LongCat-2.0", "endpoint": "https://api.example.com/v1"
        })
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "longcat-2-0" in detail
        assert "编辑" in detail

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

    @patch("routers.models.set_default_model", return_value=True)
    def test_set_default_model(self, mock_set, client):
        resp = client.post("/api/models/default", json={"key": "m1"})
        assert resp.status_code == 200
        mock_set.assert_called_once_with("m1")

    @patch("routers.models.set_default_model", return_value=False)
    def test_set_default_model_missing(self, mock_set, client):
        resp = client.post("/api/models/default", json={"key": "ghost"})
        assert resp.status_code == 404

    @patch("routers.models.set_default_model", return_value=False)
    def test_set_default_model_missing_key(self, mock_set, client):
        resp = client.post("/api/models/default", json={})
        assert resp.status_code == 400

    @patch("routers.models.get_default_model_key", return_value="m1")
    @patch("routers.models.get_all_models", return_value=[])
    def test_list_models_returns_db_default(self, mock_all, mock_default, client):
        resp = client.get("/api/models")
        assert resp.status_code == 200
        assert resp.json()["default"] == "m1"

    @patch("routers.models.set_default_correct_model", return_value=True)
    def test_set_default_model_scope_correct(self, mock_set, client):
        """scope='correct' 设置校正默认模型，不影响总结主模型默认。"""
        resp = client.post("/api/models/default", json={"key": "glm-5-2", "scope": "correct"})
        assert resp.status_code == 200
        mock_set.assert_called_once_with("glm-5-2")
        assert resp.json()["scope"] == "correct"

    @patch("routers.models.get_default_model_key", return_value="m1")
    @patch("routers.models.get_default_correct_model_key", return_value="glm-5-2")
    @patch("routers.models.get_all_models", return_value=[])
    def test_list_models_returns_correct_default(self, mock_all, mock_correct, mock_default, client):
        resp = client.get("/api/models")
        assert resp.status_code == 200
        assert resp.json()["correct_default"] == "glm-5-2"

    @patch("routers.models.add_model", return_value=True)
    def test_add_model_normalizes_endpoint(self, mock_add, client):
        resp = client.post("/api/models", json={
            "key": "m1", "name": "Test", "endpoint": "https://api.example.com/v1"
        })
        assert resp.status_code == 200
        kwargs = mock_add.call_args.kwargs
        # ★ 补全契约：统一补成 /v1/chat/completions（手填格式一般不带 v1，自动加回）
        assert kwargs["endpoint"] == "https://api.example.com/v1/chat/completions"

    @patch("routers.models.add_model", return_value=True)
    def test_add_model_normalizes_bare_host_without_v1(self, mock_add, client):
        resp = client.post("/api/models", json={
            "key": "m1", "name": "Test", "endpoint": "https://api.example.com/chat/completions"
        })
        assert resp.status_code == 200
        # 已含 /chat/completions 但没有 /v1 → 自动插入 /v1
        assert mock_add.call_args.kwargs["endpoint"] == "https://api.example.com/v1/chat/completions"

    @patch("routers.models.add_model", return_value=True)
    def test_add_model_bare_host_gets_v1(self, mock_add, client):
        resp = client.post("/api/models", json={
            "key": "m1", "name": "Test", "endpoint": "https://api.example.com"
        })
        assert resp.status_code == 200
        assert mock_add.call_args.kwargs["endpoint"] == "https://api.example.com/v1/chat/completions"

    @patch("routers.models.add_model", return_value=True)
    def test_add_model_keeps_full_endpoint(self, mock_add, client):
        resp = client.post("/api/models", json={
            "key": "m1", "name": "Test", "endpoint": "https://api.example.com/v1/chat/completions"
        })
        assert resp.status_code == 200
        kwargs = mock_add.call_args.kwargs
        assert kwargs["endpoint"] == "https://api.example.com/v1/chat/completions"

    @patch("routers.models.add_model", return_value=True)
    def test_add_model_with_engine(self, mock_add, client):
        resp = client.post("/api/models", json={
            "key": "ali-qw", "name": "qwen-plus", "engine": "阿里云",
            "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        })
        assert resp.status_code == 200
        assert mock_add.call_args.kwargs["engine"] == "阿里云"

    @patch("routers.models.add_model", return_value=True)
    def test_add_model_engine_defaults_empty(self, mock_add, client):
        resp = client.post("/api/models", json={
            "key": "m1", "name": "Test", "endpoint": "https://api.example.com/v1"
        })
        assert resp.status_code == 200
        assert mock_add.call_args.kwargs["engine"] == ""

    # ── fetch_available_models 错误可读性契约 ──
    def _fake_fetch_client(self, status, text):
        resp = MagicMock()
        resp.status_code = status
        resp.text = text
        client = AsyncMock()
        client.get = AsyncMock(return_value=resp)
        # ★ async with 必须返回同一个 client，否则 get 返回裸协程
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        return client

    @patch("routers.models.httpx.AsyncClient")
    def test_fetch_200_not_json(self, mock_cls, client):
        mock_cls.return_value = self._fake_fetch_client(200, "<html>网关错误页</html>")
        resp = client.post("/api/models/fetch", params={"endpoint": "https://x.com/v1/chat/completions"})
        data = resp.json()
        assert "不是 JSON" in data["error"]
        assert "x.com" in data["error"]

    @patch("routers.models.httpx.AsyncClient")
    def test_fetch_401_shows_server_reason(self, mock_cls, client):
        mock_cls.return_value = self._fake_fetch_client(
            401, '{"error":{"message":"Incorrect API key provided"}}')
        resp = client.post("/api/models/fetch", params={
            "endpoint": "https://x.com/v1/chat/completions", "api_key": "sk-test"})
        assert "Incorrect API key" in resp.json()["error"]

    @patch("routers.models.httpx.AsyncClient")
    def test_fetch_timeout_message(self, mock_cls, client):
        from httpx import TimeoutException
        client_inst = self._fake_fetch_client(200, "")
        client_inst.get = AsyncMock(side_effect=TimeoutException("timeout"))
        client_inst.__aenter__.return_value = client_inst
        mock_cls.return_value = client_inst
        resp = client.post("/api/models/fetch", params={"endpoint": "https://x.com/v1/chat/completions"})
        assert "请求超时" in resp.json()["error"]

    @patch("routers.models.httpx.AsyncClient")
    def test_fetch_ok_returns_models(self, mock_cls, client):
        mock_cls.return_value = self._fake_fetch_client(200, '{"data":[{"id":"m1"},{"id":"m2"}]}')
        resp = client.post("/api/models/fetch", params={"endpoint": "https://x.com/v1/chat/completions"})
        assert resp.json()["models"] == [{"id": "m1", "name": "m1"}, {"id": "m2", "name": "m2"}]

    @patch("routers.models.httpx.AsyncClient")
    def test_fetch_edit_form_falls_back_to_saved_key(self, mock_cls, client):
        # ★ 编辑表单按 key 获取：密钥留空必须回退 DB 原密钥（编辑态不回填密钥），
        #   端点用表单值（可能刚改过）——空密钥直发会被服务端 401 Invalid token
        cfg = {"key": "m1", "name": "M1", "api_key": "sk-saved", "auth_type": "bearer",
               "endpoint": "https://saved.example.com/v1/chat/completions", "skip_ssl_verify": 0}
        mock_cls.return_value = self._fake_fetch_client(200, '{"data":[{"id":"m1"}]}')
        with patch("routers.models.get_model_by_key", return_value=cfg):
            resp = client.post("/api/models/fetch", params={
                "key": "m1", "endpoint": "https://changed.example.com/v1/chat/completions",
                "api_key": ""})
        assert resp.status_code == 200
        assert resp.json()["models"] == [{"id": "m1", "name": "m1"}]
        get_args = mock_cls.return_value.get.call_args
        assert get_args.args[0] == "https://changed.example.com/v1/models"
        assert get_args.kwargs["headers"]["Authorization"] == "Bearer sk-saved"

    @patch("routers.models.httpx.AsyncClient")
    def test_fetch_edit_form_prefers_typed_key(self, mock_cls, client):
        # 表单里刚粘贴了新密钥 → 用表单密钥，不用 DB 旧密钥
        cfg = {"key": "m1", "name": "M1", "api_key": "sk-old", "auth_type": "bearer",
               "endpoint": "https://saved.example.com/v1/chat/completions", "skip_ssl_verify": 0}
        mock_cls.return_value = self._fake_fetch_client(200, '{"data":[]}')
        with patch("routers.models.get_model_by_key", return_value=cfg):
            client.post("/api/models/fetch", params={
                "key": "m1", "endpoint": "https://changed.example.com/v1/chat/completions",
                "api_key": "sk-new"})
        get_args = mock_cls.return_value.get.call_args
        assert get_args.kwargs["headers"]["Authorization"] == "Bearer sk-new"

    def test_fetch_unknown_key_404(self, client):
        with patch("routers.models.get_model_by_key", return_value=None):
            resp = client.post("/api/models/fetch", params={
                "key": "nope", "endpoint": "https://x.com/v1/chat/completions"})
        assert resp.status_code == 404

    @patch("routers.models.get_model_by_key")
    @patch("routers.models.update_model", return_value=True)
    def test_update_model_passes_engine(self, mock_upd, mock_get, client):
        mock_get.return_value = {"key": "m1", "name": "Test", "endpoint": "https://x.com",
            "auth_type": "bearer", "temperature": 0.3, "max_tokens": 4096, "model_id": "gpt-4"}
        resp = client.put("/api/models/m1", json={
            "name": "Updated", "endpoint": "https://x.com", "key": "m1", "engine": "OpenAI"
        })
        assert resp.status_code == 200
        assert mock_upd.call_args.kwargs["engine"] == "OpenAI"

    # ── 防星号污染契约 ──
    @patch("routers.models.get_model_by_key")
    @patch("routers.models.update_model", return_value=True)
    def test_update_model_masked_key_keeps_existing(self, mock_upd, mock_get, client):
        """携带脱敏星号串（前端回写）保存时，必须保留 DB 原 key，不得覆盖"""
        real_key = "sk-REAL-SECRET-VALUE-1234"
        mock_get.return_value = {"key": "m1", "name": "Test", "endpoint": "https://x.com",
            "api_key": real_key, "auth_type": "bearer", "temperature": 0.3,
            "max_tokens": 4096, "model_id": "gpt-4"}
        resp = client.put("/api/models/m1", json={
            "name": "Test", "endpoint": "https://x.com", "key": "m1",
            "api_key": "sk-RE****-1234",
        })
        assert resp.status_code == 200
        assert mock_upd.call_args.kwargs["api_key"] == real_key

    @patch("routers.models.get_model_by_key")
    @patch("routers.models.update_model", return_value=True)
    def test_update_model_empty_key_keeps_existing(self, mock_upd, mock_get, client):
        """编辑态留空 api_key（不修改）时，同样保留 DB 原 key"""
        real_key = "sk-REAL-SECRET-VALUE-1234"
        mock_get.return_value = {"key": "m1", "name": "Test", "endpoint": "https://x.com",
            "api_key": real_key, "auth_type": "bearer", "temperature": 0.3,
            "max_tokens": 4096, "model_id": "gpt-4"}
        resp = client.put("/api/models/m1", json={
            "name": "Updated", "endpoint": "https://x.com", "key": "m1", "api_key": ""
        })
        assert resp.status_code == 200
        assert mock_upd.call_args.kwargs["api_key"] == real_key

    @patch("routers.models.get_all_models")
    @patch("routers.models.get_default_model_key", return_value="m1")
    def test_list_models_marks_only_polluted_key_broken(self, mock_default, mock_all, client):
        """污染的 key（DB 存的脱敏串）标记 key_broken=True；合法 key 脱敏后不得误标"""
        mock_all.return_value = [
            {"key": "bad", "name": "Bad", "api_key": "sk-e****eb56",
             "endpoint": "https://x.com", "model_id": "", "auth_type": "bearer",
             "temperature": 0.3, "max_tokens": 4096, "is_builtin": 0, "engine": ""},
            {"key": "good", "name": "Good", "api_key": "sk-legit-secret-value-5678",
             "endpoint": "https://y.com", "model_id": "", "auth_type": "bearer",
             "temperature": 0.3, "max_tokens": 4096, "is_builtin": 0, "engine": ""},
        ]
        resp = client.get("/api/models")
        data = {m["key"]: m for m in resp.json()["models"]}
        assert data["bad"]["key_broken"] is True
        assert data["good"]["key_broken"] is False
        # 合法 key 仍要脱敏，且不能因为没有 key_broken 而裸奔
        assert "sk-legit" not in data["good"]["api_key"]

    # ── 跳过 SSL 证书校验（自签名证书/IP 直连中转） ──
    @patch("routers.models.add_model", return_value=True)
    def test_add_model_passes_skip_ssl_verify(self, mock_add, client):
        resp = client.post("/api/models", json={
            "key": "relay", "name": "Relay", "endpoint": "https://103.236.67.108/v1",
            "skip_ssl_verify": True,
        })
        assert resp.status_code == 200
        assert mock_add.call_args.kwargs["skip_ssl_verify"] is True

    @patch("routers.models.add_model", return_value=True)
    def test_add_model_skip_ssl_verify_defaults_false(self, mock_add, client):
        resp = client.post("/api/models", json={
            "key": "m1", "name": "Test", "endpoint": "https://api.example.com/v1"
        })
        assert resp.status_code == 200
        assert mock_add.call_args.kwargs["skip_ssl_verify"] is False

    @patch("routers.models.get_model_by_key")
    @patch("routers.models.update_model", return_value=True)
    def test_update_model_passes_skip_ssl_verify(self, mock_upd, mock_get, client):
        mock_get.return_value = {"key": "m1", "name": "Test", "endpoint": "https://x.com",
            "auth_type": "bearer", "temperature": 0.3, "max_tokens": 4096, "model_id": "gpt-4"}
        resp = client.put("/api/models/m1", json={
            "name": "Test", "endpoint": "https://x.com", "key": "m1", "skip_ssl_verify": True
        })
        assert resp.status_code == 200
        assert mock_upd.call_args.kwargs["skip_ssl_verify"] is True

    @patch("routers.models.httpx.AsyncClient")
    def test_fetch_with_skip_ssl_verify_disables_verify(self, mock_cls, client):
        mock_cls.return_value = self._fake_fetch_client(200, '{"data":[{"id":"m1"}]}')
        resp = client.post("/api/models/fetch", params={
            "endpoint": "https://103.236.67.108/v1", "skip_ssl_verify": True})
        assert resp.status_code == 200
        assert mock_cls.call_args.kwargs["verify"] is False

    @patch("routers.models.httpx.AsyncClient")
    def test_fetch_default_keeps_verify(self, mock_cls, client):
        mock_cls.return_value = self._fake_fetch_client(200, '{"data":[{"id":"m1"}]}')
        resp = client.post("/api/models/fetch", params={
            "endpoint": "https://api.example.com/v1"})
        assert resp.status_code == 200
        assert mock_cls.call_args.kwargs["verify"] is True

    # ── 自签名证书自动放行（CERTIFICATE_VERIFY_FAILED → verify=False 重试一次） ──
    def _fake_fetch_client_seq(self, sequence):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=sequence)
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        return client

    def _cert_error(self):
        return Exception("SSL: CERTIFICATE_VERIFY_FAILED certificate verify failed: self-signed")

    @patch("routers.models.httpx.AsyncClient")
    def test_fetch_auto_bypass_self_signed_cert(self, mock_cls, client):
        """严格校验遇自签证书 → 自动关校验重试 → 返回模型并标 ssl_bypassed"""
        ok_resp = MagicMock(status_code=200, text='{"data":[{"id":"m1"},{"id":"m2"}]}')
        mock_cls.return_value = self._fake_fetch_client_seq([self._cert_error(), ok_resp])
        resp = client.post("/api/models/fetch", params={
            "endpoint": "https://103.236.67.108/v1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ssl_bypassed"] is True
        assert data["models"] == [{"id": "m1", "name": "m1"}, {"id": "m2", "name": "m2"}]
        calls = mock_cls.call_args_list
        assert len(calls) == 2
        assert calls[0].kwargs["verify"] is True
        assert calls[1].kwargs["verify"] is False

    @patch("routers.models.get_model_by_key")
    @patch("routers.models.update_model", return_value=True)
    @patch("routers.models.httpx.AsyncClient")
    def test_fetch_key_mode_heals_ssl_flag(self, mock_cls, mock_upd, mock_get, client):
        """按已存 key 拉取遇自签证书 → 重试成功同时把 skip_ssl_verify 写回 DB（后续调用放行）"""
        mock_get.return_value = {"key": "relay", "name": "Relay", "endpoint": "https://103.236.67.108/v1",
            "api_key": "sk-x", "auth_type": "bearer", "temperature": 0.3,
            "max_tokens": 4096, "model_id": "longmao"}
        ok_resp = MagicMock(status_code=200, text='{"data":[{"id":"longmao"}]}')
        mock_cls.return_value = self._fake_fetch_client_seq([self._cert_error(), ok_resp])
        resp = client.post("/api/models/fetch", params={"key": "relay"})
        assert resp.status_code == 200
        assert resp.json()["ssl_bypassed"] is True
        mock_upd.assert_called_once_with(key="relay", skip_ssl_verify=True)

    @patch("routers.models.get_model_by_key")
    @patch("routers.models.update_model", return_value=True)
    @patch("routers.models.httpx.AsyncClient")
    def test_test_model_auto_bypass_self_signed(self, mock_cls, mock_upd, mock_get, client):
        """测试连接遇自签证书 → 关校验重试成功 + 把 skip_ssl_verify 写回 DB"""
        mock_get.return_value = {"key": "relay", "name": "Relay", "endpoint": "https://103.236.67.108/v1",
            "api_key": "sk-x", "auth_type": "bearer", "temperature": 0.3,
            "max_tokens": 4096, "model_id": "longmao"}
        ok_resp = MagicMock(status_code=200)
        ok_resp.json.return_value = {"choices": [{"message": {"content": "你好，我是 longmao。"}}]}
        # test_model 用 client.post，fake 需配 post
        client_inst = AsyncMock()
        client_inst.post = AsyncMock(side_effect=[self._cert_error(), ok_resp])
        client_inst.__aenter__.return_value = client_inst
        client_inst.__aexit__.return_value = False
        mock_cls.return_value = client_inst
        resp = client.post("/api/models/test", json={"key": "relay"})
        data = resp.json()
        assert data["success"] is True
        assert data["response"] == "你好，我是 longmao。"
        mock_upd.assert_called_once_with(key="relay", skip_ssl_verify=True)
        assert mock_cls.call_args_list[0].kwargs["verify"] is True
        assert mock_cls.call_args_list[1].kwargs["verify"] is False

    # ── 裸域名端点：先补全 /v1/chat/completions 再推导 /models，避免请求到网站首页 ──
    @patch("routers.models.httpx.AsyncClient")
    def test_fetch_bare_host_derives_v1_models(self, mock_cls, client):
        fake = self._fake_fetch_client(200, '{"data":[{"id":"m1"}]}')
        mock_cls.return_value = fake
        resp = client.post("/api/models/fetch", params={
            "endpoint": "https://redumbrellacloudlink.himscp.top"})
        assert resp.status_code == 200
        got_url = fake.get.call_args.args[0]
        assert got_url == "https://redumbrellacloudlink.himscp.top/v1/models", got_url

    @patch("routers.models.httpx.AsyncClient")
    def test_fetch_html_response_gives_actionable_error(self, mock_cls, client):
        fake = self._fake_fetch_client(200, "<!doctype html><html><head><link rel='icon' href='/logo.png'></head></html>")
        fake.get.return_value.headers = {"content-type": "text/html"}
        mock_cls.return_value = fake
        resp = client.post("/api/models/fetch", params={
            "endpoint": "https://redumbrellacloudlink.himscp.top"})
        assert resp.status_code == 200
        assert "网页而不是 API" in resp.json()["error"]
        assert "/v1/chat/completions" in resp.json()["error"]


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


# ── ASR 状态 ──
class TestAsrStatusRouter:
    def test_status_configured(self, client, monkeypatch):
        monkeypatch.setattr('config.ASR_CLOUD_ENABLED', True, raising=False)
        monkeypatch.setattr(
            'config.ASR_CLOUD_ENDPOINT',
            'https://api.siliconflow.cn/v1/audio/transcriptions', raising=False)
        monkeypatch.setattr('config.ASR_CLOUD_API_KEY', 'sk-x', raising=False)
        monkeypatch.setattr('config.ASR_CLOUD_MODEL', 'FunAudioLLM/SenseVoiceSmall', raising=False)
        resp = client.get("/api/asr/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cloud_configured"] is True
        assert data["model"] == "FunAudioLLM/SenseVoiceSmall"
        assert data["provider_host"] == "api.siliconflow.cn"
        # firered_ready 契约：布尔（模型未就绪的 CI 环境为 False，本机就位为 True）
        assert isinstance(data["firered_ready"], bool)
        # 契约：绝不回密钥
        assert "sk-" not in json.dumps(data)

    def test_status_not_configured(self, client, monkeypatch):
        monkeypatch.setattr('config.ASR_CLOUD_ENABLED', False, raising=False)
        monkeypatch.setattr('config.ASR_CLOUD_API_KEY', '', raising=False)
        resp = client.get("/api/asr/status")
        assert resp.status_code == 200
        assert resp.json()["cloud_configured"] is False

    # ── recommended_engine：默认引擎策略单点（免费优先） ──
    def _set_free_cfg(self, monkeypatch, configured: bool):
        monkeypatch.setattr('config.ASR_FREE_ENABLED', True, raising=False)
        monkeypatch.setattr(
            'config.ASR_FREE_ENDPOINT',
            'https://api.siliconflow.cn/v1/audio/transcriptions', raising=False)
        monkeypatch.setattr('config.ASR_FREE_API_KEY',
                            'sk-free' if configured else '', raising=False)

    def _set_paid_cfg(self, monkeypatch, configured: bool):
        monkeypatch.setattr('config.ASR_CLOUD_ENABLED', configured, raising=False)
        monkeypatch.setattr(
            'config.ASR_CLOUD_ENDPOINT',
            'https://api.xiaomimimo.com/v1/chat/completions', raising=False)
        monkeypatch.setattr('config.ASR_CLOUD_API_KEY',
                            'sk-paid' if configured else '', raising=False)

    def test_recommended_engine_free_first(self, client, monkeypatch):
        """免费优先：免费档可用时推荐 cloud_free（即使付费档也可用）。"""
        self._set_free_cfg(monkeypatch, True)
        self._set_paid_cfg(monkeypatch, True)
        data = client.get("/api/asr/status").json()
        assert data["recommended_engine"] == "cloud_free"

    def test_recommended_engine_free_only(self, client, monkeypatch):
        """仅配置免费档 → cloud_free（前端不再强制落 local）。"""
        self._set_free_cfg(monkeypatch, True)
        self._set_paid_cfg(monkeypatch, False)
        assert client.get("/api/asr/status").json()["recommended_engine"] == "cloud_free"

    def test_recommended_engine_paid_when_free_missing(self, client, monkeypatch):
        """免费档不可用但付费档可用 → cloud（用户配置付费即接受兜底）。"""
        self._set_free_cfg(monkeypatch, False)
        self._set_paid_cfg(monkeypatch, True)
        assert client.get("/api/asr/status").json()["recommended_engine"] == "cloud"

    def test_recommended_engine_local_when_none(self, client, monkeypatch):
        """云端全不可用 → local 保底（firered 不参与推荐，不默认触发下载）。"""
        self._set_free_cfg(monkeypatch, False)
        self._set_paid_cfg(monkeypatch, False)
        assert client.get("/api/asr/status").json()["recommended_engine"] == "local"
