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

import routers.chat as routers_chat


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

    def _file_retry_job(self, input_file):
        return {
            "id": "j1",
            "status": "failed",
            "options": {"input_type": "file"},
            "input_file": input_file,
        }

    def test_force_retry_aborts_when_input_backup_fails(self, client, tmp_path):
        job_dir = tmp_path / "j1"
        job_dir.mkdir()
        video = job_dir / "video.mp4"
        video.write_bytes(b"0" * 1024)
        (job_dir / "old.srt").write_text("old", encoding="utf-8")

        with patch("routers.jobs.get_job", return_value=self._file_retry_job(str(video))), \
             patch("routers.jobs.update_job") as mock_update, \
             patch("routers.jobs.DOWNLOAD_DIR", tmp_path), \
             patch("routers.jobs.UPLOAD_DIR", tmp_path / "uploads"), \
             patch("shutil.copy2", side_effect=OSError("disk full")), \
             patch("main.start_job") as mock_start:
            resp = client.post("/api/jobs/j1/retry?force=true")

        assert resp.status_code == 500
        assert "备份失败" in resp.json()["detail"]
        assert "未删除任何文件" in resp.json()["detail"]
        # 素材与其余文件原封不动，任务未被重新提交
        assert video.read_bytes() == b"0" * 1024
        assert (job_dir / "old.srt").exists()
        mock_update.assert_not_called()
        mock_start.assert_not_called()

    def test_force_retry_aborts_when_backup_fails_verification(self, client, tmp_path):
        job_dir = tmp_path / "j1"
        job_dir.mkdir()
        video = job_dir / "video.mp4"
        video.write_bytes(b"0" * 1024)

        def bad_copy2(src, dst):
            Path(dst).write_bytes(b"truncated")

        with patch("routers.jobs.get_job", return_value=self._file_retry_job(str(video))), \
             patch("routers.jobs.update_job") as mock_update, \
             patch("routers.jobs.DOWNLOAD_DIR", tmp_path), \
             patch("routers.jobs.UPLOAD_DIR", tmp_path / "uploads"), \
             patch("shutil.copy2", side_effect=bad_copy2), \
             patch("main.start_job") as mock_start:
            resp = client.post("/api/jobs/j1/retry?force=true")

        assert resp.status_code == 500
        assert "备份校验" in resp.json()["detail"]
        assert video.read_bytes() == b"0" * 1024
        mock_update.assert_not_called()
        mock_start.assert_not_called()

    def test_force_retry_restores_input_from_inside_job_dir(self, client, tmp_path):
        job_dir = tmp_path / "j1"
        job_dir.mkdir()
        video = job_dir / "video.mp4"
        video.write_bytes(b"media-bytes")
        (job_dir / "old.srt").write_text("old", encoding="utf-8")

        with patch("routers.jobs.get_job", return_value=self._file_retry_job(str(video))), \
             patch("routers.jobs.update_job") as mock_update, \
             patch("routers.jobs.DOWNLOAD_DIR", tmp_path), \
             patch("routers.jobs.UPLOAD_DIR", tmp_path / "uploads"), \
             patch("main.start_job") as mock_start, \
             patch("socket_handlers.clear_cancel_flag"):
            resp = client.post("/api/jobs/j1/retry?force=true")

        assert resp.status_code == 200
        # 输入文件恢复到 job_dir 且字节一致，旧产物已清理
        restored = job_dir / "video.mp4"
        assert restored.read_bytes() == b"media-bytes"
        assert not (job_dir / "old.srt").exists()
        assert mock_update.call_count == 2
        assert mock_update.call_args_list[0].kwargs["input_file"] == str(restored.resolve())
        assert mock_update.call_args_list[1].kwargs["options"]["_retry"] is True
        mock_start.assert_called_once_with("j1")

    def test_force_retry_takes_ownership_of_staged_input(self, client, tmp_path):
        job_dir = tmp_path / "j1"
        job_dir.mkdir()
        uploads_dir = tmp_path / "uploads"
        uploads_dir.mkdir()
        staged = uploads_dir / "abcd1234_video.mp4"
        staged.write_bytes(b"staged-bytes")

        with patch("routers.jobs.get_job", return_value=self._file_retry_job(str(staged))), \
             patch("routers.jobs.update_job") as mock_update, \
             patch("routers.jobs.DOWNLOAD_DIR", tmp_path), \
             patch("routers.jobs.UPLOAD_DIR", uploads_dir), \
             patch("main.start_job"), \
             patch("socket_handlers.clear_cancel_flag"):
            resp = client.post("/api/jobs/j1/retry?force=true")

        assert resp.status_code == 200
        restored = job_dir / "abcd1234_video.mp4"
        assert restored.read_bytes() == b"staged-bytes"
        # 暂存原件按既有复制语义保留在 uploads，由暂存 TTL 清理；input_file 已指向任务目录副本
        assert staged.read_bytes() == b"staged-bytes"
        assert not (uploads_dir / "retry_j1_abcd1234_video.mp4").exists()
        assert mock_update.call_args_list[0].kwargs["input_file"] == str(restored.resolve())

    def test_force_retry_proceeds_when_input_file_missing(self, client, tmp_path):
        job_dir = tmp_path / "j1"
        job_dir.mkdir()
        (job_dir / "old.srt").write_text("old", encoding="utf-8")
        missing = job_dir / "ghost.mp4"

        with patch("routers.jobs.get_job", return_value=self._file_retry_job(str(missing))), \
             patch("routers.jobs.update_job") as mock_update, \
             patch("routers.jobs.DOWNLOAD_DIR", tmp_path), \
             patch("routers.jobs.UPLOAD_DIR", tmp_path / "uploads"), \
             patch("main.start_job") as mock_start:
            resp = client.post("/api/jobs/j1/retry?force=true")

        assert resp.status_code == 200
        assert not (job_dir / "old.srt").exists()
        mock_update.assert_called_once()
        mock_start.assert_called_once_with("j1")

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
    def test_batch_force_new_ignored_never_dedups(self, mock_start, mock_create, client):
        """force_new 键已废弃：批量永不查历史去重（去重只属于单任务路径）"""
        resp = client.post("/api/jobs/batch", json={
            "urls": ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
            "force_new": True,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["skipped"] == 0
        mock_create.assert_called_once()
        mock_start.assert_called_once_with("job-1")

    @patch("routers.jobs_batch.create_job", return_value="job-2")
    @patch("main.start_job")
    def test_batch_never_dedups_multiform_urls(self, mock_start, mock_create, client):
        """批量=排队：同一 BV 的分P链接/重复URL 全部建任务，不做规范化去重
        （2026-09-13 用户场景：6 行分P链接被 BV 号去重后只剩 1 个任务）"""
        urls = [
            "https://www.bilibili.com/video/BV1rtXvBvE1W/?spm_id_from=a",
            "https://www.bilibili.com/video/BV1rtXvBvE1W/?p=2&spm_id_from=a",
            "https://www.bilibili.com/video/BV1rtXvBvE1W/?p=3&spm_id_from=a",
        ]
        resp = client.post("/api/jobs/batch", json={"urls": urls})
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 3
        assert body["skipped"] == 0
        assert mock_create.call_count == 3

    @patch("routers.jobs_batch.create_job", side_effect=lambda **kw: f"job-{kw['input_url'][:10]}")
    @patch("main.start_job")
    def test_batch_invalid_lines_reported(self, mock_start, mock_create, client):
        """非 URL 行不建任务但不静默吞：行号随响应返回，其余行照常提交"""
        resp = client.post("/api/jobs/batch", json={
            "urls": [
                "https://www.bilibili.com/video/BV1RGN462EBs/",
                "这是一行文字不是链接",
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            ],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert body["skipped"] == 1
        assert body["invalid_lines"] == [{"line": 2, "text": "这是一行文字不是链接"}]
        assert "第 2 行" in body["message"]
        assert mock_create.call_count == 2

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
        assert len(resp.json()["dimensions"]) == 10
        # 口误纠错维度：单模块 + 默认启用
        sf = resp.json()["dimensions"]["speech_fix"]
        assert [m["key"] for m in sf["modules"]] == ["balanced"]
        assert sf["default"] == "balanced"

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

    def _stub_stream(self, monkeypatch, outcome):
        """追问流式端点的公共替身：DB / 搜索 / 模型调用全部短路，只观察 SSE 事件"""
        monkeypatch.setattr("routers.chat.get_job", lambda job_id: {
            "id": job_id, "status": "completed", "result_text": "已有分析",
            "files": {}, "model": "m1",
        })
        monkeypatch.setattr("routers.chat.get_model_by_key", lambda key: {
            "key": "m1", "model_id": "m1",
            "endpoint": "https://x.example.com/v1/chat/completions",
            "api_key": "sk-x", "auth_type": "bearer",
        })
        monkeypatch.setattr("routers.chat._ensure_session", lambda *a, **k: None)
        monkeypatch.setattr("routers.chat._get_session_history", lambda *a, **k: [])
        monkeypatch.setattr("routers.chat._insert_chat", lambda *a, **k: 1)
        monkeypatch.setattr("routers.chat._maybe_rename_session", lambda *a, **k: None)

        async def _fake_collect(query, **kwargs):
            return outcome

        monkeypatch.setattr("routers.chat.web_search_collect", _fake_collect)

        # 模型调用短路（测试只关心 search 事件，不必构造流式响应）
        def _boom(*a, **k):
            raise RuntimeError("模型调用被测试短路")

        monkeypatch.setattr("routers.chat.httpx.AsyncClient", _boom)

        # 断连探测固定为 False，避免 TestClient 的 receive 通道差异导致用例不稳定
        async def _not_disconnected(self):
            return False

        monkeypatch.setattr("starlette.requests.Request.is_disconnected", _not_disconnected)

    def test_chat_stream_reports_search_failure(self, client, monkeypatch):
        """★ 回归锚点：联网检索失败必须经 SSE 显式透出（此前静默 → 开关形同虚设）"""
        from services.web_search import SearchOutcome

        self._stub_stream(monkeypatch, SearchOutcome(
            error="未配置 Ollama 搜索 Key（…）；DuckDuckGo 兜底不可用（未安装 ddgs 包）"))
        resp = client.post("/api/jobs/j1/chat/sessions/s1/stream",
                           json={"content": "问题", "enable_search": True})

        assert resp.status_code == 200
        assert '"type": "search", "status": "searching"' in resp.text
        assert '"type": "search", "status": "failed"' in resp.text
        assert "未配置 Ollama 搜索 Key" in resp.text

    def test_chat_stream_reports_search_hits(self, client, monkeypatch):
        from services.web_search import SearchOutcome

        self._stub_stream(monkeypatch, SearchOutcome(
            sources=[{"title": "A", "url": "https://a.com", "snippet": "s"}],
            text="[1] A", provider="ollama"))
        resp = client.post("/api/jobs/j1/chat/sessions/s1/stream",
                           json={"content": "问题", "enable_search": True})

        assert resp.status_code == 200
        assert '"status": "ok"' in resp.text
        assert '"provider": "ollama"' in resp.text
        assert '"count": 1' in resp.text

    def test_chat_stream_without_search_emits_no_search_event(self, client, monkeypatch):
        from services.web_search import SearchOutcome

        self._stub_stream(monkeypatch, SearchOutcome(provider="ollama"))
        resp = client.post("/api/jobs/j1/chat/sessions/s1/stream",
                           json={"content": "问题", "enable_search": False})

        assert resp.status_code == 200
        assert '"type": "search"' not in resp.text

    def test_chat_stream_legacy_enable_search_maps_to_strict_mode(self, client, monkeypatch):
        """向后兼容：旧客户端只发 enable_search=True → 生效模式 strict 且事件带 mode"""
        from services.web_search import SearchOutcome

        self._stub_stream(monkeypatch, SearchOutcome(
            sources=[{"title": "A", "url": "https://a.com", "snippet": "s"}],
            text="[1] A", provider="ollama"))
        resp = client.post("/api/jobs/j1/chat/sessions/s1/stream",
                           json={"content": "问题", "enable_search": True})

        assert '"type": "search", "status": "searching", "mode": "strict"' in resp.text

    def test_chat_stream_broad_mode_emits_mode_field(self, client, monkeypatch):
        """广泛模式：search_mode=broad → SSE 事件带 mode，且检索走 _run_search 的双路编排"""
        from services.web_search import SearchOutcome

        seen_queries = []

        async def _fake_collect(query, **kwargs):
            seen_queries.append(query)
            return SearchOutcome(
                sources=[{"title": f"S-{len(seen_queries)}", "url": f"https://s{len(seen_queries)}.com",
                          "snippet": "s"}],
                text=f"[{len(seen_queries)}] S", provider="ollama")

        self._stub_stream(monkeypatch, SearchOutcome())  # 占位，会被下面的覆盖
        monkeypatch.setattr("routers.chat.web_search_collect", _fake_collect)
        # job 带 title：广泛模式第二路用「标题+问题」组合
        monkeypatch.setattr("routers.chat.get_job", lambda job_id: {
            "id": job_id, "status": "completed", "result_text": "已有分析",
            "files": {}, "model": "m1", "title": "中医讲堂第12讲",
        })

        resp = client.post("/api/jobs/j1/chat/sessions/s1/stream",
                           json={"content": "附子的用法", "search_mode": "broad"})

        assert resp.status_code == 200
        assert '"type": "search", "status": "searching", "mode": "broad"' in resp.text
        assert '"status": "ok"' in resp.text
        # 两路检索：问题本身 + 标题组合
        assert "附子的用法" in seen_queries[0]
        assert "中医讲堂第12讲" in seen_queries[1] and "附子的用法" in seen_queries[1]

    def test_run_search_broad_merges_and_dedupes(self, monkeypatch):
        """广泛模式合并逻辑：两路来源按 URL 去重，单路失败不拖整体"""
        import asyncio
        from services.web_search import SearchOutcome

        async def _fake_collect(query, **kwargs):
            if "标题" in query:
                return SearchOutcome(error="第二路挂了")
            return SearchOutcome(
                sources=[{"title": "A", "url": "https://a.com", "snippet": "s"},
                         {"title": "B", "url": "https://a.com/", "snippet": "s2"}],  # 同 URL（尾斜杠差异）
                text="x", provider="ollama")

        monkeypatch.setattr("routers.chat.web_search_collect", _fake_collect)

        outcome = asyncio.run(routers_chat._run_search("broad", "问题", "视频标题"))

        assert outcome.ok is True
        assert outcome.provider == "ollama"
        assert len(outcome.sources) == 1          # 重复 URL 只留一条
        assert outcome.sources[0]["title"] == "A"

    def test_run_search_broad_all_failed_carries_reasons(self, monkeypatch):
        import asyncio
        from services.web_search import SearchOutcome

        async def _fake_collect(query, **kwargs):
            return SearchOutcome(error="未配置 Ollama 搜索 Key")

        monkeypatch.setattr("routers.chat.web_search_collect", _fake_collect)

        outcome = asyncio.run(routers_chat._run_search("broad", "问题", ""))

        assert outcome.ok is False
        assert "未配置 Ollama 搜索 Key" in outcome.error

    def test_build_messages_strict_vs_broad_prompts(self):
        """严格模式锁材料约束；广泛模式放开约束并保留编号引用要求"""
        strict = routers_chat._build_messages("原文", "结果", [], "问题", "[1] 来源", False,
                                              search_mode="strict")
        broad = routers_chat._build_messages("原文", "结果", [], "问题", "[1] 来源", False,
                                             search_mode="broad")
        broad_no_search = routers_chat._build_messages("原文", "结果", [], "问题", "", False,
                                                       search_mode="broad")

        s_sys = strict[0]["content"]
        b_sys = broad[0]["content"]
        assert "不要编造背景资料中不存在的信息" in s_sys
        assert "仅作背景参考，不是约束" in b_sys
        assert "广泛、深入" in b_sys
        assert "[1]、[2]" in b_sys                    # 广泛模式仍要求编号引用
        assert "自身的知识" in broad_no_search[0]["content"]
        assert "联网搜索资料" not in broad_no_search[0]["content"]  # 无检索结果时不留空资料块

    def test_search_mode_invalid_value_rejected(self, client, monkeypatch):
        """search_mode 白名单校验：非法值 422（契约显式，不静默落 off）"""
        from services.web_search import SearchOutcome

        self._stub_stream(monkeypatch, SearchOutcome())
        resp = client.post("/api/jobs/j1/chat/sessions/s1/stream",
                           json={"content": "问题", "search_mode": "wide"})
        assert resp.status_code == 422


# ── 重新生成与历史回复版本（variant_group/is_active） ──
class TestChatVariants:
    """重新生成保留历史版本：分组落库、activate 切换、上下文过滤、删除语义。"""

    @pytest.fixture(autouse=True)
    def _seed(self, client):
        """一组两版回答 + 后续一轮提问（模拟重新生成一次后的会话）。
        ★ 每测唯一 job/session：DB 是会话级共享的，同 id 会跨测试串数据。"""
        import uuid
        from routers.chat import _insert_chat, _gen_variant_group, _ensure_session
        from db.database import get_connection
        self.JID = "jv-" + uuid.uuid4().hex[:8]
        self.SID = "sv-" + uuid.uuid4().hex[:8]
        # chat_sessions/job_chats 对 jobs 有外键：补一条最小任务行
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO jobs (id, title, status, result_text) VALUES (?, '变体测试', 'completed', '结果')",
                (self.JID,),
            )
            conn.commit()
        finally:
            conn.close()
        _ensure_session(self.JID, self.SID)
        self.group = _gen_variant_group()
        self.u1 = _insert_chat(self.JID, self.SID, "user", "问题一")
        self.a1 = _insert_chat(self.JID, self.SID, "assistant", "答案一v1", variant_group=self.group)
        self.a2 = _insert_chat(self.JID, self.SID, "assistant", "答案一v2", variant_group=self.group)
        from routers.chat import _activate_variant
        _activate_variant(self.JID, self.SID, self.group, self.a2)  # 生效 = v2
        self.u2 = _insert_chat(self.JID, self.SID, "user", "问题二")
        self.a3 = _insert_chat(self.JID, self.SID, "assistant", "答案二")  # 独立单变体

    def test_history_exposes_variant_metadata(self, client):
        resp = client.get(f"/api/jobs/{self.JID}/chat/sessions/{self.SID}/history")
        assert resp.status_code == 200
        msgs = resp.json()["messages"]
        a1 = next(m for m in msgs if m["id"] == self.a1)
        a2 = next(m for m in msgs if m["id"] == self.a2)
        a3 = next(m for m in msgs if m["id"] == self.a3)
        assert a1["variant_group"] == self.group and a2["variant_group"] == self.group
        assert a1["is_active"] is False and a2["is_active"] is True
        assert [v["id"] for v in a2["variants"]] == [self.a1, self.a2]
        # 存量单变体行：无组无切换器
        assert a3["variant_group"] == "" and a3["variants"] == []

    def test_context_excludes_inactive_variants(self, client):
        """★ 回归锚点：模型上下文只含生效版本（v1 不进上下文，否则切换无意义）"""
        from routers.chat import _get_session_history
        hist = _get_session_history(self.JID, self.SID)
        contents = [h["content"] for h in hist]
        assert "答案一v1" not in contents
        assert "答案一v2" in contents and "答案二" in contents

    def test_activate_endpoint_switches_and_persists(self, client):
        resp = client.post(f"/api/jobs/{self.JID}/chat/variants/{self.a1}/activate")
        assert resp.status_code == 200
        assert resp.json() == {"id": self.a1, "variant_count": 2}
        from routers.chat import _get_session_history
        contents = [h["content"] for h in _get_session_history(self.JID, self.SID)]
        assert "答案一v1" in contents and "答案一v2" not in contents
        # 非组内 id → 404
        assert client.post(f"/api/jobs/{self.JID}/chat/variants/999999/activate").status_code == 404

    def test_regenerate_stream_appends_variant_not_delete(self, client, monkeypatch):
        """★ 回归锚点：重新生成不再删除旧回答——对最后一轮（独立行 a3）生成变体入组并带计数"""
        from services.web_search import SearchOutcome
        from routers.chat import _insert_chat as _real_insert
        TestChatRouter()._stub_stream(monkeypatch, SearchOutcome())  # httpx 短路 → 走异常兜底插入
        # _stub_stream 把 _insert_chat 换成了固定返回 1 的桩；本用例要验证真实落库，恢复真函数
        monkeypatch.setattr("routers.chat._insert_chat", _real_insert)
        resp = client.post(f"/api/jobs/{self.JID}/chat/sessions/{self.SID}/stream",
                           json={"content": "", "regenerate": True})
        assert resp.status_code == 200
        # a3 原是独立行（group=''）→ 重生成时从本代起新建组；组内 = a3 + 新变体 = 2
        import re as _re
        m = _re.search(r'"variant_group": "([^"]+)", "variant_count": (\d+)', resp.text)
        assert m, resp.text
        new_group, count = m.group(1), int(m.group(2))
        assert count == 2
        from routers.chat import _variant_rows
        rows = _variant_rows(self.JID, self.SID, new_group)
        assert len(rows) == 2
        assert rows[-1]["is_active"] == 1 and rows[0]["is_active"] == 0
        # 旧回答 a3 仍在（不删除），新变体内容 = 异常兜底文案（模型被短路）
        from db.database import get_connection
        conn = get_connection()
        try:
            content = conn.execute("SELECT content FROM job_chats WHERE id = ?", (rows[-1]["id"],)).fetchone()["content"]
            a3 = conn.execute("SELECT content FROM job_chats WHERE id = ?", (self.a3,)).fetchone()
        finally:
            conn.close()
        assert content == "(生成出错)"
        assert a3 is not None and a3["content"] == "答案二"

    def test_regenerate_without_assistant_rejected(self, client, monkeypatch):
        """没有任何可重生的回答 → 显式 error 事件（不静默）"""
        from services.web_search import SearchOutcome
        TestChatRouter()._stub_stream(monkeypatch, SearchOutcome())
        # content 必填（契约）；正常新提问不受变体分支影响
        resp = client.post(f"/api/jobs/{self.JID}/chat/sessions/{self.SID}/stream",
                           json={"content": "新问题"})
        assert resp.status_code == 200
        # 空会话重生成 → 无可重生对象
        import uuid
        from routers.chat import _ensure_session
        from db.database import get_connection
        jid2 = "jv-" + uuid.uuid4().hex[:8]
        conn = get_connection()
        try:
            conn.execute("INSERT OR IGNORE INTO jobs (id, title, status, result_text) VALUES (?, 't', 'completed', 'r')", (jid2,))
            conn.commit()
        finally:
            conn.close()
        _ensure_session(jid2, "sv2")
        resp2 = client.post(f"/api/jobs/{jid2}/chat/sessions/sv2/stream",
                            json={"content": "", "regenerate": True})
        assert '"没有可重新生成的回答"' in resp2.text

    def test_delete_active_variant_hands_over_to_latest(self, client):
        """删生效版本 → 组内最新接管激活；旧版本保留"""
        resp = client.delete(f"/api/jobs/{self.JID}/chat/{self.a2}")
        assert resp.status_code == 200
        from db.database import get_connection
        conn = get_connection()
        try:
            a1 = conn.execute("SELECT is_active FROM job_chats WHERE id = ?", (self.a1,)).fetchone()
            u2 = conn.execute("SELECT id FROM job_chats WHERE id = ?", (self.u2,)).fetchone()
        finally:
            conn.close()
        assert a1["is_active"] == 1      # 接管
        assert u2 is not None            # 后续轮次不受影响

    def test_delete_last_variant_removes_orphan_question(self, client):
        """组删空 → 连带删除所属提问行（防孤儿）"""
        client.delete(f"/api/jobs/{self.JID}/chat/{self.a2}")
        client.delete(f"/api/jobs/{self.JID}/chat/{self.a1}")
        from db.database import get_connection
        conn = get_connection()
        try:
            u1 = conn.execute("SELECT id FROM job_chats WHERE id = ?", (self.u1,)).fetchone()
        finally:
            conn.close()
        assert u1 is None

    def test_delete_user_message_deletes_whole_group(self, client):
        """删 user 行 = 整轮：全组变体 + 后续全删（编辑回填沿用此语义）"""
        client.delete(f"/api/jobs/{self.JID}/chat/{self.u1}")
        from db.database import get_connection
        conn = get_connection()
        try:
            left = conn.execute(
                "SELECT id FROM job_chats WHERE job_id = ? AND id IN (?, ?, ?)",
                (self.JID, self.a1, self.a2, self.u1),
            ).fetchall()
        finally:
            conn.close()
        assert left == []


class TestChatBatchDelete:
    """多选批量删除：提问行=整轮（不牵连后续轮次）、回答行=该回答全部版本 + 孤儿提问清理。"""

    @pytest.fixture(autouse=True)
    def _seed(self, client):
        """三轮会话：第 1 轮两条变体（生效 v2），第 2/3 轮各一条单变体回答。
        ★ 每测唯一 job/session：DB 是会话级共享的，同 id 会跨测试串数据。"""
        import uuid
        from routers.chat import _insert_chat, _gen_variant_group, _ensure_session, _activate_variant
        from db.database import get_connection
        self.JID = "jb-" + uuid.uuid4().hex[:8]
        self.SID = "sb-" + uuid.uuid4().hex[:8]
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO jobs (id, title, status, result_text) VALUES (?, '批量删除测试', 'completed', '结果')",
                (self.JID,),
            )
            conn.commit()
        finally:
            conn.close()
        _ensure_session(self.JID, self.SID)
        self.group = _gen_variant_group()
        self.u1 = _insert_chat(self.JID, self.SID, "user", "问题一")
        self.a1 = _insert_chat(self.JID, self.SID, "assistant", "答案一v1", variant_group=self.group)
        self.a2 = _insert_chat(self.JID, self.SID, "assistant", "答案一v2", variant_group=self.group)
        _activate_variant(self.JID, self.SID, self.group, self.a2)
        self.u2 = _insert_chat(self.JID, self.SID, "user", "问题二")
        self.a3 = _insert_chat(self.JID, self.SID, "assistant", "答案二")
        self.u3 = _insert_chat(self.JID, self.SID, "user", "问题三")
        self.a4 = _insert_chat(self.JID, self.SID, "assistant", "答案三")

    def _left(self):
        from db.database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, role, content FROM job_chats WHERE job_id = ? ORDER BY id", (self.JID,)
            ).fetchall()
            return [(r["id"], r["role"], r["content"]) for r in rows]
        finally:
            conn.close()

    def _post(self, client, ids):
        return client.post(f"/api/jobs/{self.JID}/chat/messages/delete", json={"ids": ids})

    def test_delete_middle_turn_keeps_later_turns(self, client):
        """★ 回归锚点：勾选中间一轮提问 → 只删该轮（提问+变体组），后续轮次不受影响"""
        resp = self._post(client, [self.u1])
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 3          # u1 + a1 + a2
        left = self._left()
        assert [i for i, _, _ in left] == [self.u2, self.a3, self.u3, self.a4]

    def test_delete_answer_removes_all_versions_and_orphan_question(self, client):
        """勾选回答行（生效 v2）→ 该回答全部版本一起删；提问成孤儿一并删除"""
        resp = self._post(client, [self.a2])
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 3          # a1 + a2 + 所属提问 u1
        left = self._left()
        assert [i for i, _, _ in left] == [self.u2, self.a3, self.u3, self.a4]

    def test_delete_ungrouped_answer_removes_orphan_question(self, client):
        """单变体回答：删除该行后该轮无回答 → 提问一并删除（不留孤儿）"""
        resp = self._post(client, [self.a3])
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 2          # a3 + 所属提问 u2
        left = self._left()
        assert [i for i, _, _ in left] == [self.u1, self.a1, self.a2, self.u3, self.a4]

    def test_delete_mixed_question_and_answer(self, client):
        """混合勾选：提问轮 + 另一轮的末条回答，各自按语义删除"""
        resp = self._post(client, [self.u1, self.u3, self.a4])
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 5          # (u1+a1+a2) + a4 + 孤儿提问 u3
        left = self._left()
        assert [i for i, _, _ in left] == [self.u2, self.a3]

    def test_delete_question_keeps_variants_of_other_turn(self, client):
        """勾选提问 + 同一轮的回答：整轮删一次，不重复计数、不误删后续"""
        resp = self._post(client, [self.u1, self.a2])
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 3
        left = self._left()
        assert [i for i, _, _ in left] == [self.u2, self.a3, self.u3, self.a4]

    def test_delete_empty_ids_rejected(self, client):
        assert self._post(client, []).status_code == 400

    def test_delete_unknown_ids_noop(self, client):
        """不存在的 id 静默跳过（幂等），不报错"""
        resp = self._post(client, [999999, 999998])
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 0
        assert len(self._left()) == 7

    def test_delete_other_job_ids_not_touched(self, client):
        """id 属于其它任务 → 不受影响（job 作用域）"""
        import uuid
        from routers.chat import _insert_chat, _ensure_session
        from db.database import get_connection
        other_jid = "jb-" + uuid.uuid4().hex[:8]
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO jobs (id, title, status, result_text) VALUES (?, '其它任务', 'completed', '结果')",
                (other_jid,),
            )
            conn.commit()
        finally:
            conn.close()
        _ensure_session(other_jid, "sb2")
        other_u = _insert_chat(other_jid, "sb2", "user", "别的问题")
        resp = self._post(client, [other_u])
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 0


class TestChatRegenAnyTurn:
    """任意一轮重新生成：按 user_message_id 定位目标轮 + 上下文按「轮」归位（不因 id 追加跑位）。"""

    @pytest.fixture(autouse=True)
    def _seed(self, client):
        """三轮会话：第 1 轮两条变体（生效 v2），第 2/3 轮各一条单变体回答。
        ★ 每测唯一 job/session：DB 是会话级共享的，同 id 会跨测试串数据。"""
        import uuid
        from routers.chat import _insert_chat, _gen_variant_group, _ensure_session, _activate_variant
        from db.database import get_connection
        self.JID = "jr-" + uuid.uuid4().hex[:8]
        self.SID = "sr-" + uuid.uuid4().hex[:8]
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO jobs (id, title, status, result_text) VALUES (?, '重生成测试', 'completed', '结果')",
                (self.JID,),
            )
            conn.commit()
        finally:
            conn.close()
        _ensure_session(self.JID, self.SID)
        self.group = _gen_variant_group()
        self.u1 = _insert_chat(self.JID, self.SID, "user", "问题一")
        self.a1 = _insert_chat(self.JID, self.SID, "assistant", "答案一v1", variant_group=self.group)
        self.a2 = _insert_chat(self.JID, self.SID, "assistant", "答案一v2", variant_group=self.group)
        _activate_variant(self.JID, self.SID, self.group, self.a2)
        self.u2 = _insert_chat(self.JID, self.SID, "user", "问题二")
        self.a3 = _insert_chat(self.JID, self.SID, "assistant", "答案二")
        self.u3 = _insert_chat(self.JID, self.SID, "user", "问题三")
        self.a4 = _insert_chat(self.JID, self.SID, "assistant", "答案三")

    def _stub_model(self, monkeypatch):
        """只短路模型调用（httpx）与任务/模型配置；DB 助手保持真实——本类要验证落库与上下文顺序"""
        monkeypatch.setattr("routers.chat.get_job", lambda job_id: {
            "id": job_id, "status": "completed", "result_text": "已有分析", "files": {}, "model": "m1",
        })
        monkeypatch.setattr("routers.chat.get_model_by_key", lambda key: {
            "key": "m1", "model_id": "m1",
            "endpoint": "https://x.example.com/v1/chat/completions", "api_key": "sk-x", "auth_type": "bearer",
        })

        def _boom(*a, **k):
            raise RuntimeError("模型调用被测试短路")

        monkeypatch.setattr("routers.chat.httpx.AsyncClient", _boom)

        async def _not_disconnected(self):
            return False

        monkeypatch.setattr("starlette.requests.Request.is_disconnected", _not_disconnected)

    def _stream(self, client, payload):
        return client.post(f"/api/jobs/{self.JID}/chat/sessions/{self.SID}/stream", json=payload)

    def _rows(self, ids):
        from db.database import get_connection
        conn = get_connection()
        try:
            marks = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT id, role, variant_group, is_active, content FROM job_chats WHERE id IN ({marks}) ORDER BY id",
                tuple(ids),
            ).fetchall()
            return {r["id"]: dict(r) for r in rows}
        finally:
            conn.close()

    def test_regenerate_middle_turn_appends_variant_to_that_turn(self, client, monkeypatch):
        """★ 回归锚点：指定第 1 轮提问 → 新变体入该轮原组且生效，后续轮次原样不动"""
        import re as _re
        self._stub_model(monkeypatch)
        resp = self._stream(client, {"content": "", "regenerate": True, "user_message_id": self.u1})
        assert resp.status_code == 200
        m = _re.search(r'"variant_group": "([^"]+)", "variant_count": (\d+)', resp.text)
        assert m, resp.text
        assert m.group(1) == self.group      # 沿用该轮原组
        assert int(m.group(2)) == 3          # a1 + a2 + 新变体
        from routers.chat import _variant_rows
        rows = _variant_rows(self.JID, self.SID, self.group)
        assert len(rows) == 3
        new_id = rows[-1]["id"]
        marks = self._rows([self.a1, self.a2, new_id, self.a3, self.a4])
        assert marks[new_id]["is_active"] == 1          # 新版本生效
        assert marks[self.a1]["is_active"] == 0 and marks[self.a2]["is_active"] == 0
        assert marks[self.a3]["is_active"] == 1         # 后续轮次不受影响
        assert marks[self.a4]["is_active"] == 1
        assert marks[self.a3]["content"] == "答案二" and marks[self.a4]["content"] == "答案三"

    def test_regenerate_middle_turn_keeps_context_position(self, client, monkeypatch):
        """★ 回归锚点：新变体行 id 在会话末尾，但模型上下文里必须紧跟其提问（不能排到最后一轮之后）"""
        self._stub_model(monkeypatch)
        self._stream(client, {"content": "", "regenerate": True, "user_message_id": self.u1})
        from routers.chat import _get_session_history, _variant_rows
        new_id = _variant_rows(self.JID, self.SID, self.group)[-1]["id"]
        new_content = self._rows([new_id])[new_id]["content"]
        contents = [h["content"] for h in _get_session_history(self.JID, self.SID)]
        assert contents[0] == "问题一"
        assert contents[1] == new_content               # 紧跟其提问
        assert contents[2:] == ["问题二", "答案二", "问题三", "答案三"]
        assert contents[-1] == "答案三"                  # 不是新变体

    def test_regenerate_first_turn_history_excludes_target_question(self, client, monkeypatch):
        """重生成中间轮时 history = 该轮之前（提问由 ask 显式带回，不得重复出现）"""
        from routers.chat import _build_messages as real_build
        self._stub_model(monkeypatch)
        seen = {}

        def _spy(original_ctx, result_ctx, history, question, search_text, regenerate, search_mode="off"):
            seen["history"] = [h["content"] for h in history]
            seen["question"] = question
            return real_build(original_ctx, result_ctx, history, question, search_text, regenerate, search_mode)

        monkeypatch.setattr("routers.chat._build_messages", _spy)
        self._stream(client, {"content": "", "regenerate": True, "user_message_id": self.u2})
        assert seen["history"] == ["问题一", "答案一v2"]   # 第 2 轮之前（含第 1 轮生效版本）
        assert seen["question"] == "问题二"

    def test_regenerate_middle_turn_searches_that_question(self, client, monkeypatch):
        """联网检索词 = 目标轮提问（此前固定取最近一条用户消息 → 中间轮会搜错问题）"""
        from services.web_search import SearchOutcome
        self._stub_model(monkeypatch)
        seen = {}

        async def _fake_search(mode, question, job_title=""):
            seen["question"] = question
            return SearchOutcome(error="测试短路")

        monkeypatch.setattr("routers.chat._run_search", _fake_search)
        resp = self._stream(client, {"content": "", "regenerate": True, "user_message_id": self.u1,
                                     "search_mode": "strict"})
        assert resp.status_code == 200
        assert seen["question"] == "问题一"

    def test_regenerate_unknown_or_assistant_target_rejected(self, client, monkeypatch):
        self._stub_model(monkeypatch)
        resp = self._stream(client, {"content": "", "regenerate": True, "user_message_id": 999999})
        assert "没有可重新生成的回答" in resp.text
        # 指向回答行（非 user 行）同样拒绝
        resp2 = self._stream(client, {"content": "", "regenerate": True, "user_message_id": self.a3})
        assert "没有可重新生成的回答" in resp2.text

    def test_regenerate_target_of_other_session_rejected(self, client, monkeypatch):
        """目标提问行必须属于本会话（防跨会话误操作）"""
        from routers.chat import _insert_chat, _ensure_session
        self._stub_model(monkeypatch)
        _ensure_session(self.JID, "other-sid")
        other_u = _insert_chat(self.JID, "other-sid", "user", "别的问题")
        resp = self._stream(client, {"content": "", "regenerate": True, "user_message_id": other_u})
        assert "没有可重新生成的回答" in resp.text

    def test_regenerate_without_target_still_uses_last_turn(self, client, monkeypatch):
        """向后兼容：不传 user_message_id = 重生成最后一轮（现行为）"""
        import re as _re
        self._stub_model(monkeypatch)
        resp = self._stream(client, {"content": "", "regenerate": True})
        m = _re.search(r'"assistant_id": (\d+), "token_usage"', resp.text)
        assert m, resp.text
        new_id = int(m.group(1))
        marks = self._rows([new_id, self.a4, self.a3])
        assert marks[new_id]["is_active"] == 1 and marks[self.a4]["is_active"] == 0   # a4 归组转非激活
        assert marks[new_id]["variant_group"] and marks[new_id]["variant_group"] == marks[self.a4]["variant_group"]
        assert marks[self.a3]["is_active"] == 1                                       # 其它轮不动


# ── 模型上下文预算（字符级裁剪） ──
class TestChatContextBudget:
    """`_get_session_history(limit=30)` 只按行数截断、单条不截：长会话会把 prompt 顶爆。
    本类锁定字符预算契约：按**整轮**丢（不留"有问无答"）、最新轮必留、单条超限打标记、
    裁剪后必须经 SSE `context` 事件如实告知前端。"""

    @staticmethod
    def _rows(*specs):
        return [{"role": role, "content": content} for role, content in specs]

    def test_within_budget_keeps_everything(self):
        rows = self._rows(("user", "问一"), ("assistant", "答一"), ("user", "问二"), ("assistant", "答二"))
        kept, meta = routers_chat._trim_history_by_budget(rows, max_chars=24000)

        assert kept == rows
        assert meta == {"budget": 24000, "omitted_turns": 0, "omitted_chars": 0,
                        "truncated_messages": 0, "kept_messages": 4}

    def test_over_budget_drops_oldest_turns_whole(self):
        """★ 核心：超预算从最旧整轮丢，保留的是完整轮（不出现孤立的 assistant）"""
        rows = self._rows(("user", "u" * 50), ("assistant", "a" * 50),
                          ("user", "v" * 50), ("assistant", "b" * 50))

        kept, meta = routers_chat._trim_history_by_budget(rows, max_chars=120)

        assert [m["content"][0] for m in kept] == ["v", "b"]
        assert [m["role"] for m in kept] == ["user", "assistant"]
        assert meta["omitted_turns"] == 1 and meta["omitted_chars"] == 100
        assert meta["kept_messages"] == 2

    def test_budget_boundary_keeps_multiple_complete_turns(self):
        rows = self._rows(("user", "1" * 40), ("assistant", "2" * 40),
                          ("user", "3" * 40), ("assistant", "4" * 40),
                          ("user", "5" * 40), ("assistant", "6" * 40))

        kept, meta = routers_chat._trim_history_by_budget(rows, max_chars=160)

        assert [m["content"][0] for m in kept] == ["3", "4", "5", "6"]
        assert meta["omitted_turns"] == 1

    def test_latest_turn_always_kept_even_when_alone_over_budget(self):
        """最新一轮就是用户刚问的这轮，丢了等于失忆 → 无论如何保留"""
        rows = self._rows(("user", "老" * 500), ("assistant", "旧" * 500),
                          ("user", "新" * 500), ("assistant", "答" * 500))

        kept, meta = routers_chat._trim_history_by_budget(rows, max_chars=100)

        assert kept == rows[-2:]
        assert meta["omitted_turns"] == 1

    def test_single_huge_message_truncated_with_marker(self):
        rows = self._rows(("user", "问"), ("assistant", "长" * 500))

        kept, meta = routers_chat._trim_history_by_budget(rows, max_chars=10000, message_max=100)

        assert meta["truncated_messages"] == 1
        assert "内容较长，已截断" in kept[-1]["content"]
        assert len(kept[-1]["content"]) < 500
        assert rows[-1]["content"] == "长" * 500          # 不改动入参（调用方可能还在用）

    def test_zero_budget_disables_char_trimming(self):
        """max_chars<=0 = 关闭预算（退回纯行数截断的旧行为，可回退开关）"""
        rows = self._rows(("user", "问" * 10), ("assistant", "答" * 10))

        kept, meta = routers_chat._trim_history_by_budget(rows, max_chars=0)

        assert kept == rows
        assert meta["omitted_turns"] == 0 and meta["truncated_messages"] == 0

    def test_history_for_prompt_wires_budget_and_reports_meta(self, monkeypatch):
        """_history_for_prompt = 取历史 + 预算裁剪（_get_session_history 契约不变）"""
        rows = self._rows(("user", "老" * 100), ("assistant", "旧" * 100),
                          ("user", "新" * 10), ("assistant", "答" * 10))
        monkeypatch.setattr("routers.chat._get_session_history", lambda *a, **k: rows)
        monkeypatch.setattr(routers_chat, "HISTORY_MAX_CHARS", 50)

        kept, meta = routers_chat._history_for_prompt("j1", "s1")

        assert [m["content"][0] for m in kept] == ["新", "答"]
        assert meta["budget"] == 50 and meta["omitted_turns"] == 1

    # ---- SSE：被裁剪时必须如实告知（前端据此显示"已省略较早的 N 轮"提示行） ----

    def _stub_stream(self, monkeypatch, history):
        """短路 DB/模型/搜索，只观察 SSE 事件（同 TestChatRouter._stub_stream 姿势）"""
        monkeypatch.setattr("routers.chat.get_job", lambda job_id: {
            "id": job_id, "status": "completed", "result_text": "已有分析",
            "files": {}, "model": "m1",
        })
        monkeypatch.setattr("routers.chat.get_model_by_key", lambda key: {
            "key": "m1", "model_id": "m1",
            "endpoint": "https://x.example.com/v1/chat/completions",
            "api_key": "sk-x", "auth_type": "bearer",
        })
        monkeypatch.setattr("routers.chat._ensure_session", lambda *a, **k: None)
        monkeypatch.setattr("routers.chat._get_session_history", lambda *a, **k: history)
        monkeypatch.setattr("routers.chat._insert_chat", lambda *a, **k: 1)
        monkeypatch.setattr("routers.chat._maybe_rename_session", lambda *a, **k: None)

        def _boom(*a, **k):
            raise RuntimeError("模型调用被测试短路")

        monkeypatch.setattr("routers.chat.httpx.AsyncClient", _boom)

        async def _not_disconnected(self):
            return False

        monkeypatch.setattr("starlette.requests.Request.is_disconnected", _not_disconnected)

    def test_stream_emits_context_event_when_trimmed(self, client, monkeypatch):
        """★ 回归锚点：历史被裁剪要经 SSE 透出（否则用户不知道模型"没看到"旧对话）"""
        self._stub_stream(monkeypatch, self._rows(
            ("user", "老问题" * 50), ("assistant", "老回答" * 50), ("user", "上一轮提问")))
        monkeypatch.setattr(routers_chat, "HISTORY_MAX_CHARS", 20)

        resp = client.post("/api/jobs/j1/chat/sessions/s1/stream", json={"content": "这次的问题"})

        assert resp.status_code == 200
        assert '"type": "context"' in resp.text
        assert '"omitted_turns": 1' in resp.text
        assert '"budget": 20' in resp.text

    def test_stream_no_context_event_when_within_budget(self, client, monkeypatch):
        self._stub_stream(monkeypatch, self._rows(("user", "上一轮提问"), ("assistant", "上一轮回答")))

        resp = client.post("/api/jobs/j1/chat/sessions/s1/stream", json={"content": "这次的问题"})

        assert resp.status_code == 200
        assert '"type": "context"' not in resp.text

    # ── 模型超时（2026-10-08 并入预算体系） ──

    def test_timeout_defaults_align_with_batch_stream(self):
        """追问默认超时必须与批量流式（ai_processor 的 600s）对齐，不能再是 180s"""
        import services.ai_processor as ai_processor

        assert routers_chat.CHAT_MODEL_TIMEOUT == 600.0
        assert routers_chat.CHAT_MODEL_CONNECT_TIMEOUT == 15.0
        assert routers_chat.HTTPX_TIMEOUT.read == 600.0
        assert routers_chat.HTTPX_TIMEOUT.connect == 15.0
        # 批量侧的 600s 就在同一个仓库里，别让两边悄悄漂开
        assert "timeout=600.0" in Path(ai_processor.__file__).read_text(encoding="utf-8")

    def test_env_float_fallbacks(self, monkeypatch):
        """非法/非正 env 值必须回退默认并 warn，不能让服务起不来"""
        monkeypatch.setenv("CHAT_MODEL_TIMEOUT_SECONDS", "not-a-number")
        assert routers_chat._env_float("CHAT_MODEL_TIMEOUT_SECONDS", 600.0) == 600.0
        monkeypatch.setenv("CHAT_MODEL_TIMEOUT_SECONDS", "0")
        assert routers_chat._env_float("CHAT_MODEL_TIMEOUT_SECONDS", 600.0) == 600.0
        monkeypatch.setenv("CHAT_MODEL_TIMEOUT_SECONDS", "-5")
        assert routers_chat._env_float("CHAT_MODEL_TIMEOUT_SECONDS", 600.0) == 600.0
        monkeypatch.setenv("CHAT_MODEL_TIMEOUT_SECONDS", "900")
        assert routers_chat._env_float("CHAT_MODEL_TIMEOUT_SECONDS", 600.0) == 900.0
        monkeypatch.delenv("CHAT_MODEL_TIMEOUT_SECONDS", raising=False)
        assert routers_chat._env_float("CHAT_MODEL_TIMEOUT_SECONDS", 600.0) == 600.0

    def test_timeout_message_is_actionable(self):
        """超时文案必须点明秒数 + 给可行动建议（不能只回一句 timeout）"""
        import httpx as _httpx

        msg = routers_chat._model_timeout_message(_httpx.ReadTimeout("read timed out"))
        assert "600 秒" in msg
        assert "CHAT_MODEL_TIMEOUT_SECONDS" in msg
        assert "换" in msg

        connect_msg = routers_chat._model_timeout_message(_httpx.ConnectTimeout("connect timed out"))
        assert "连接" in connect_msg and "15" in connect_msg

        pool_msg = routers_chat._model_timeout_message(_httpx.PoolTimeout("pool"))
        assert "排队" in pool_msg

    def test_stream_timeout_emits_actionable_error(self, client, monkeypatch):
        """★ 回归锚点：模型超时时 SSE error 必须是人话，不是 str(ReadTimeout)"""
        import httpx as _httpx

        self._stub_stream(monkeypatch, self._rows(("user", "上一轮提问"), ("assistant", "上一轮回答")))

        def _timeout(*a, **k):
            raise _httpx.ReadTimeout("read timed out")

        monkeypatch.setattr("routers.chat.httpx.AsyncClient", _timeout)

        resp = client.post("/api/jobs/j1/chat/sessions/s1/stream", json={"content": "这次的问题"})

        assert resp.status_code == 200
        assert '"type": "error"' in resp.text
        assert "600 秒内没有返回内容" in resp.text
        assert "read timed out" not in resp.text, "不能再把裸异常串当错误文案"
        # 超时也要收尾（前端据此清 loading 态）
        assert '"type": "done"' in resp.text


# ── ASR 状态 ──
class TestAsrStatusRouter:
    @pytest.fixture(autouse=True)
    def _config_only_providers(self, monkeypatch):
        """本类验证 config.py 兜底语义与默认引擎推荐策略 → 屏蔽 DB provider 层，
        保持确定行为；「DB 优先 / 槽位解析 / 管理页写操作」由 test_asr_management.py 覆盖。"""
        monkeypatch.setattr('db.asr_repo.get_provider', lambda key: None)
        monkeypatch.setattr('db.asr_repo.get_provider_for_engine',
                            lambda engine: {'cloud': 'mimo', 'cloud_free': 'xingchen'}.get(engine))
        monkeypatch.setattr('db.asr_repo.get_default_engine', lambda: '')

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
