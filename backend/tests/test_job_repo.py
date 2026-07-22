"""
job_repo 数据库操作测试
使用 :memory: SQLite 隔离测试
"""
import pytest
import sqlite3
import json
from unittest.mock import patch, MagicMock

from db.job_repo import (
    create_job, get_job, get_jobs, get_running_jobs,
    get_jobs_by_batch, get_batch_summary,
    update_job, delete_job, finish_job,
    _row_to_dict, find_job_by_url, clear_jobs, clear_jobs_by_status,
    save_summary_version, get_summary_history, get_summary_version,
    get_latest_summary_version,
    _now_utc_iso, delete_jobs_batch, get_jobs_disk_info,
    claim_staged_upload, update_job_input_file_if_matches,
)

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, title TEXT, platform TEXT,
    input_url TEXT, input_text TEXT, input_file TEXT,
    status TEXT DEFAULT 'pending', model TEXT, summary_type TEXT,
    options TEXT DEFAULT '{}', result_text TEXT, files TEXT DEFAULT '{}',
    tags TEXT DEFAULT '[]', token_usage TEXT DEFAULT '{}',
    progress_msg TEXT, preview_text TEXT, edited_preview_text TEXT,
    error TEXT, batch_id TEXT, summary_version INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS summary_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
    summary_type TEXT, model TEXT, result_text TEXT,
    tags TEXT DEFAULT '[]', token_usage TEXT DEFAULT '{}',
    files TEXT DEFAULT '{}', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(job_id, version)
);
CREATE TABLE IF NOT EXISTS job_chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT, role TEXT NOT NULL, content TEXT NOT NULL,
    token_usage TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT, model_key TEXT,
    input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
    cost REAL DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(CREATE_TABLES_SQL)
    conn.close()
    return path


@pytest.fixture(autouse=True)
def _patch_conn(db_path):
    def _get_conn():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    with patch("db.job_repo.get_connection", side_effect=_get_conn):
        yield


# ── create_job ──
class TestCreateJob:
    def test_create_basic_url(self):
        jid = create_job(input_type="url", input_url="https://example.com")
        job = get_job(jid)
        assert job["status"] == "pending"
        assert job["input_url"] == "https://example.com"
        assert job["model"] == "glm-4.5-air"

    def test_create_text(self):
        jid = create_job(input_type="text", input_text="hello world")
        job = get_job(jid)
        assert job["input_text"] == "hello world"

    def test_create_with_custom_model(self):
        jid = create_job(input_type="url", input_url="url", model="custom-model")
        job = get_job(jid)
        assert job["model"] == "custom-model"

    def test_create_with_summary_type(self):
        jid = create_job(input_type="url", input_url="url", summary_type="chapter")
        job = get_job(jid)
        assert job["summary_type"] == "chapter"

    def test_create_options_has_input_type(self):
        jid = create_job(input_type="text", input_text="x")
        job = get_job(jid)
        assert job["options"]["input_type"] == "text"

    def test_create_options_has_boolean_flags(self):
        jid = create_job(input_type="url", input_url="x",
                         do_summary=True, do_translate=True,
                         do_subtitle=False, preview_before_summary=True)
        job = get_job(jid)
        assert job["options"]["do_summary"] is True
        assert job["options"]["do_translate"] is True
        assert job["options"]["do_subtitle"] is False

    def test_create_with_dimensions(self):
        jid = create_job(input_type="url", input_url="x",
                         dimensions={"role": "academic"})
        job = get_job(jid)
        assert job["options"]["dimensions"]["role"] == "academic"

    def test_create_with_batch_id(self):
        jid = create_job(input_type="url", input_url="x", batch_id="batch-1")
        job = get_job(jid)
        assert job["batch_id"] == "batch-1"


# ── get_job ──
class TestGetJob:
    def test_existing(self):
        jid = create_job(input_type="url", input_url="test")
        assert get_job(jid) is not None

    def test_nonexistent(self):
        assert get_job("nonexistent") is None


# ── get_jobs ──
class TestGetJobs:
    def test_list_all(self):
        for _ in range(3):
            create_job(input_type="url", input_url="a")
        jobs, total = get_jobs()
        assert total == 3
        assert len(jobs) == 3

    def test_list_by_status(self):
        create_job(input_type="url", input_url="a")
        jobs, total = get_jobs(status="pending")
        assert total == 1

    def test_list_limit_offset(self):
        for _ in range(5):
            create_job(input_type="url", input_url="a")
        jobs, total = get_jobs(limit=2, offset=1)
        assert len(jobs) == 2
        assert total == 5

    def test_list_empty(self):
        jobs, total = get_jobs()
        assert total == 0
        assert len(jobs) == 0


# ── get_running_jobs ──
class TestGetRunningJobs:
    def test_returns_active(self):
        jid = create_job(input_type="url", input_url="a")
        update_job(jid, status="summarizing")
        jobs = get_running_jobs()
        assert len(jobs) == 1

    def test_excludes_completed(self):
        jid = create_job(input_type="url", input_url="a")
        update_job(jid, status="completed")
        jobs = get_running_jobs()
        assert len(jobs) == 0

    def test_excludes_failed(self):
        jid = create_job(input_type="url", input_url="a")
        update_job(jid, status="failed")
        jobs = get_running_jobs()
        assert len(jobs) == 0


# ── get_jobs_by_batch ──
class TestGetJobsByBatch:
    def test_returns_batch_jobs(self):
        for _ in range(3):
            create_job(input_type="url", input_url="a", batch_id="BATCH-1")
        jobs = get_jobs_by_batch("BATCH-1")
        assert len(jobs) == 3

    def test_returns_empty_for_unknown(self):
        assert get_jobs_by_batch("no-batch") == []


# ── get_batch_summary ──
class TestGetBatchSummary:
    def test_summary(self):
        create_job(input_type="url", input_url="a", batch_id="BS")
        create_job(input_type="url", input_url="b", batch_id="BS")
        summary = get_batch_summary("BS")
        assert summary["total"] == 2
        assert summary["by_status"]["pending"] == 2
        assert summary["all_done"] is False

    def test_nonexistent_batch(self):
        assert get_batch_summary("no") is None


# ── update_job ──
class TestUpdateJob:
    def test_update_status(self):
        jid = create_job(input_type="url", input_url="x")
        assert update_job(jid, status="downloading")
        job = get_job(jid)
        assert job["status"] == "downloading"

    def test_update_title(self):
        jid = create_job(input_type="url", input_url="x")
        update_job(jid, title="My Title")
        assert get_job(jid)["title"] == "My Title"

    def test_update_json_fields(self):
        jid = create_job(input_type="url", input_url="x")
        update_job(jid, files={"srt": "/tmp/x.srt"}, tags=["tag1"])
        job = get_job(jid)
        assert job["files"]["srt"] == "/tmp/x.srt"
        assert "tag1" in job["tags"]

    def test_update_nonexistent(self):
        assert update_job("no", title="x") is False


# ── delete_job ──
class TestDeleteJob:
    def test_delete_existing(self):
        jid = create_job(input_type="url", input_url="x")
        assert delete_job(jid, delete_files=False)
        assert get_job(jid) is None

    def test_delete_nonexistent(self):
        assert delete_job("no", delete_files=False) is False

    def test_delete_cleans_child_tables(self):
        jid = create_job(input_type="url", input_url="x")
        update_job(jid, status="completed")
        save_summary_version(jid, 1, "summary", "m", "text")
        assert delete_job(jid, delete_files=False)
        assert get_job(jid) is None

    def test_delete_keeps_failed_physical_removal_in_quarantine(self, tmp_path):
        jid = create_job(input_type='url', input_url='x')
        downloads = tmp_path / 'downloads'
        job_dir = downloads / jid
        job_dir.mkdir(parents=True)
        (job_dir / 'audio.mp3').write_bytes(b'audio')
        with patch('db.job_repo.DOWNLOAD_DIR', downloads), \
             patch('db.job_repo.shutil.rmtree', side_effect=OSError('locked')):
            assert delete_job(jid)

        assert get_job(jid) is None
        assert not job_dir.exists()
        quarantined = list((tmp_path / '.cleanup_quarantine').iterdir())
        assert len(quarantined) == 1
        assert (quarantined[0] / 'audio.mp3').exists()

    def test_delete_restores_directory_when_database_delete_fails(self, tmp_path):
        jid = create_job(input_type='url', input_url='x')
        downloads = tmp_path / 'downloads'
        job_dir = downloads / jid
        job_dir.mkdir(parents=True)
        (job_dir / 'audio.mp3').write_bytes(b'audio')
        with patch('db.job_repo.DOWNLOAD_DIR', downloads), \
             patch('db.job_repo._delete_if_table', side_effect=RuntimeError('db failed')):
            with pytest.raises(RuntimeError, match='db failed'):
                delete_job(jid)

        assert get_job(jid) is not None
        assert (job_dir / 'audio.mp3').exists()


# ── finish_job ──
class TestFinishJob:
    def test_finish_success(self):
        jid = create_job(input_type="url", input_url="x")
        finish_job(jid, success=True, files={"srt": "x"}, token_usage={"in": 10})
        job = get_job(jid)
        assert job["status"] == "completed"
        assert job["files"]["srt"] == "x"
        assert job["token_usage"]["in"] == 10

    def test_finish_failure(self):
        jid = create_job(input_type="url", input_url="x")
        finish_job(jid, success=False, error="something broke")
        job = get_job(jid)
        assert job["status"] == "failed"
        assert "something broke" in job["error"]

    def test_finish_failure_no_error(self):
        jid = create_job(input_type="url", input_url="x")
        finish_job(jid, success=False)
        job = get_job(jid)
        assert job["status"] == "failed"
        assert "未知错误" in job["error"]


# ── _row_to_dict ──
class TestRowToDict:
    def test_json_deserialization(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE t (id TEXT, options TEXT, files TEXT, tags TEXT, token_usage TEXT,"
            " created_at TEXT, updated_at TEXT)")
        conn.execute("INSERT INTO t VALUES (?, ?, ?, ?, ?, ?, ?)",
                     ("1", '{"a":1}', '{"b":2}', '["x"]', '{"c":3}',
                      "2026-07-08 12:00:00", "2026-07-08T12:00:00Z"))
        row = conn.execute("SELECT * FROM t").fetchone()
        d = _row_to_dict(row)
        assert d["options"] == {"a": 1}
        assert d["files"] == {"b": 2}
        assert d["tags"] == ["x"]
        assert d["token_usage"] == {"c": 3}
        assert d["created_at"] == "2026-07-08T12:00:00Z"
        assert d["updated_at"] == "2026-07-08T12:00:00Z"
        conn.close()

    def test_already_iso_format(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE t (id TEXT, options TEXT, files TEXT, tags TEXT, token_usage TEXT,"
            " created_at TEXT, updated_at TEXT)")
        conn.execute("INSERT INTO t VALUES (?,?,?,?,?,?,?)",
                     ("1", None, None, None, None,
                      "2026-07-08T12:00:00Z", "2026-07-08T12:00:00Z"))
        row = conn.execute("SELECT * FROM t").fetchone()
        d = _row_to_dict(row)
        assert d["created_at"] == "2026-07-08T12:00:00Z"
        conn.close()

    def test_none_json_fields(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE t (id TEXT, options TEXT, files TEXT, tags TEXT, token_usage TEXT,"
            " created_at TEXT, updated_at TEXT)")
        conn.execute("INSERT INTO t VALUES (?,NULL,NULL,NULL,NULL,NULL,NULL)",
                     ("1",))
        row = conn.execute("SELECT * FROM t").fetchone()
        d = _row_to_dict(row)
        assert d["options"] is None
        assert d["files"] is None
        conn.close()


# ── find_job_by_url ──
class TestFindJobByUrl:
    def test_exact_match(self):
        create_job(input_type="url", input_url="https://a.com")
        found = find_job_by_url("https://a.com")
        assert found is not None
        assert found["input_url"] == "https://a.com"

    def test_fuzzy_match(self):
        create_job(input_type="url", input_url="https://a.com?v=1&spm=2")
        found = find_job_by_url("https://a.com?v=1&spm=3")
        assert found is not None

    def test_no_match(self):
        assert find_job_by_url("https://no-match.com") is None

    def test_different_youtube_video_ids_do_not_match(self):
        create_job(
            input_type="url",
            input_url="https://www.youtube.com/watch?v=abcdefghijk",
        )

        assert find_job_by_url("https://www.youtube.com/watch?v=lmnopqrstuv") is None


# ── clear_jobs ──
class TestClearJobs:
    def test_clear_all(self):
        for _ in range(3):
            create_job(input_type="url", input_url="a")
        assert clear_jobs() == 3
        jobs, total = get_jobs()
        assert total == 0

    def test_clear_empty(self):
        assert clear_jobs() == 0

    def test_clear_all_removes_job_directories(self, tmp_path):
        jid = create_job(input_type='url', input_url='a')
        job_dir = tmp_path / jid
        job_dir.mkdir()
        (job_dir / 'audio.mp3').write_bytes(b'audio')
        with patch('db.job_repo.DOWNLOAD_DIR', tmp_path):
            assert clear_jobs() == 1
        assert not job_dir.exists()


# ── clear_jobs_by_status ──
class TestClearJobsByStatus:
    def test_clear_by_status(self):
        j1 = create_job(input_type="url", input_url="a")
        j2 = create_job(input_type="url", input_url="b")
        update_job(j1, status="completed")
        assert clear_jobs_by_status("completed") == 1
        # j2 还在
        assert get_job(j2) is not None


# ── summary_history ──
class TestSummaryHistory:
    def test_save_and_get(self):
        jid = create_job(input_type="url", input_url="x")
        assert save_summary_version(jid, 1, "summary", "m", "text1",
                                    tags=["a"], token_usage={"in": 5},
                                    files={"srt": "f"})
        versions = get_summary_history(jid)
        assert len(versions) == 1
        assert versions[0]["result_text"] == "text1"
        assert versions[0]["tags"] == ["a"]

    def test_save_multiple_versions(self):
        jid = create_job(input_type="url", input_url="x")
        save_summary_version(jid, 1, "s", "m", "v1")
        save_summary_version(jid, 2, "s", "m", "v2")
        versions = get_summary_history(jid)
        assert len(versions) == 2
        assert versions[0]["version"] == 1

    def test_get_specific_version(self):
        jid = create_job(input_type="url", input_url="x")
        save_summary_version(jid, 1, "s", "m", "v1")
        save_summary_version(jid, 2, "s", "m", "v2")
        v = get_summary_version(jid, 2)
        assert v["result_text"] == "v2"

    def test_get_nonexistent_version(self):
        assert get_summary_version("no", 1) is None

    def test_latest_version_no_history(self):
        jid = create_job(input_type="url", input_url="x")
        assert get_latest_summary_version(jid) == 1

    def test_latest_version_with_history(self):
        jid = create_job(input_type="url", input_url="x")
        save_summary_version(jid, 1, "s", "m", "v1")
        save_summary_version(jid, 2, "s", "m", "v2")
        assert get_latest_summary_version(jid) == 2


# ── delete_jobs_batch ──
class TestDeleteJobsBatch:
    def test_batch_delete(self):
        j1 = create_job(input_type="url", input_url="a")
        j2 = create_job(input_type="url", input_url="b")
        result = delete_jobs_batch([j1, j2], delete_files=False)
        assert result["deleted_count"] == 2
        assert j1 in result["deleted"]

    def test_batch_delete_nonexistent(self):
        result = delete_jobs_batch(['no1'], delete_files=False)
        assert result['deleted_count'] == 0
        assert result['failed'] == [{'job_id': 'no1', 'error': 'job not found'}]


class TestInputFileOwnership:
    def test_pending_job_can_claim_staged_upload(self):
        jid = create_job(input_type='file', input_file='staged.mp3')
        assert claim_staged_upload(jid, 'staged.mp3', 'job/input.mp3')
        assert get_job(jid)['input_file'] == 'job/input.mp3'

    def test_completed_cleanup_update_requires_completed_status(self):
        jid = create_job(input_type='file', input_file='staged.mp3')
        assert not update_job_input_file_if_matches(jid, 'staged.mp3', 'job/input.mp3')
        update_job(jid, status='completed')
        assert update_job_input_file_if_matches(jid, 'staged.mp3', 'job/input.mp3')


# ── _now_utc_iso ──
class TestNowUtcIso:
    def test_returns_iso_format(self):
        iso = _now_utc_iso()
        assert "T" in iso
        assert "Z" in iso
        assert len(iso) == 20
