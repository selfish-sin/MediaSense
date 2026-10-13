"""
job_repo 数据库操作测试
使用 :memory: SQLite 隔离测试
"""
import pytest
import sqlite3
from unittest.mock import patch

from config import DEFAULT_MODEL
from db.job_repo import (
    create_job, get_job, get_jobs, get_running_jobs,
    get_jobs_by_batch, get_batch_summary,
    update_job, delete_job, finish_job,
    _row_to_dict, find_job_by_url, clear_jobs, clear_jobs_by_status,
    save_summary_version, get_summary_history, get_summary_version, append_summary_version,
    _now_utc_iso, delete_jobs_batch, claim_staged_upload, update_job_input_file_if_matches,
)


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
        assert job["model"] == DEFAULT_MODEL

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
                         do_summary=True, preview_before_summary=True)
        job = get_job(jid)
        assert job["options"]["do_summary"] is True
        assert job["options"]["preview_before_summary"] is True

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

    def test_list_by_keyword_input_url(self):
        """keyword 按 input_url 模糊匹配"""
        create_job(input_type="url", input_url="https://youtube.com/watch?v=abc123")
        create_job(input_type="url", input_url="https://bilibili.com/video/BVxyz")
        jobs, total = get_jobs(keyword="youtube")
        assert total == 1
        assert "youtube" in jobs[0]["input_url"]

    def test_list_by_keyword_title(self):
        """keyword 按 title 模糊匹配"""
        jid1 = create_job(input_type="url", input_url="https://a.com")
        update_job(jid1, title="深度学习入门教程")
        create_job(input_type="url", input_url="https://b.com")  # 无 title
        jobs, total = get_jobs(keyword="深度学习")
        assert total == 1
        assert jobs[0]["id"] == jid1

    def test_list_by_keyword_like_wildcard_escaped(self):
        """LIKE 通配符 % _ 应被转义为字面量，不当通配符"""
        create_job(input_type="url", input_url="https://a.com/50%_off")
        create_job(input_type="url", input_url="https://b.com/normal")
        # 搜 "%_%" —— 若未转义会匹配所有含任意字符的行；转义后只匹配字面含 % 的
        jobs, total = get_jobs(keyword="%")
        assert total == 1
        assert "%" in jobs[0]["input_url"]

    def test_list_by_keyword_with_status(self):
        """keyword 与 status 可叠加（AND）"""
        create_job(input_type="url", input_url="https://youtube.com/1")
        create_job(input_type="url", input_url="https://youtube.com/2")
        # 把第二个改成 completed
        all_jobs, _ = get_jobs(keyword="youtube")
        update_job(all_jobs[1]["id"], status="completed")
        jobs, total = get_jobs(status="pending", keyword="youtube")
        assert total == 1


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

    def test_update_transcript_meta_roundtrip(self):
        """transcript_meta 必须同时进 update_job 序列化表与 _row_to_dict 反序列化表。"""
        jid = create_job(input_type="url", input_url="x")
        meta = {"source": "transcription", "engine": "cloud_free",
                "requested_engine": "cloud", "cache": "miss", "degraded": True}
        assert update_job(jid, transcript_meta=meta)
        job = get_job(jid)
        assert job["transcript_meta"] == meta
        # 未设置的旧任务读回应是 None（不是字符串 'null'）
        other = create_job(input_type="url", input_url="y")
        assert get_job(other)["transcript_meta"] is None

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


# ── JSON 字段往返（回归：2026-10-05 修）────────────────────────────────
# 背景：update_job 会把 JSON 字段序列化成字符串存库，_row_to_dict 负责反序列化。
#       ai_call_chain / correction_stats 一旦漏在 json_fields 之外，get_job 返回的就是
#       裸字符串；而 JobResponse 也没声明这两个字段，pydantic 会直接丢弃它们。
#       两条链路任一断掉，结果页就是永久空白——首页走 socket 反而看不出来。
class TestJsonFieldRoundTrip:
    def test_ai_call_chain_reads_back_as_list(self):
        jid = create_job(input_type='file', input_file='a.mp3')
        chain = [
            {'model': 'bad-model', 'ok': False, 'reason': '连接断开'},
            {'model': 'good-model', 'ok': True, 'reason': ''},
        ]
        assert update_job(jid, ai_call_chain=chain)
        got = get_job(jid)['ai_call_chain']
        assert isinstance(got, list), 'ai_call_chain 必须反序列化成 list，不能是 JSON 字符串'
        assert got == chain

    def test_correction_stats_reads_back_as_dict(self):
        jid = create_job(input_type='file', input_file='a.mp3')
        stats = {
            'total_chunks': 5, 'changed_count': 2, 'failed_chunks': 0,
            'integrity_warnings': 1, 'length_warnings': 0,
            'changed_lines': [{'ts': '00:01', 'before': 'a', 'after': 'b'}],
        }
        assert update_job(jid, correction_stats=stats)
        got = get_job(jid)['correction_stats']
        assert isinstance(got, dict), 'correction_stats 必须反序列化成 dict'
        assert got['total_chunks'] == 5
        assert got['changed_lines'][0]['after'] == 'b'

    def test_json_fields_survive_multiple_updates(self):
        """二次更新不能把上一轮的调用链冲掉成字符串或 None"""
        jid = create_job(input_type='file', input_file='a.mp3')
        update_job(jid, ai_call_chain=[{'model': 'm1', 'ok': False, 'reason': 'x'}])
        update_job(jid, status='completed')
        got = get_job(jid)['ai_call_chain']
        assert isinstance(got, list) and got[0]['model'] == 'm1'

    def test_null_json_field_stays_none(self):
        jid = create_job(input_type='file', input_file='a.mp3')
        assert get_job(jid)['ai_call_chain'] is None


# ── JobResponse 契约（回归：2026-10-05 修）──────────────────────────────
# pydantic v2 默认丢弃未声明字段。不声明 = 落库再正确也传不到前端。
class TestJobResponseCarriesJsonFields:
    def _resp(self, **over):
        from models.schemas import JobResponse
        base = dict(id='j1', status='failed')
        base.update(over)
        return JobResponse(**base)

    def test_ai_call_chain_survives_schema(self):
        chain = [{'model': 'm1', 'ok': False, 'reason': 'HTTP 402'}]
        assert self._resp(ai_call_chain=chain).ai_call_chain == chain

    def test_correction_stats_survives_schema(self):
        stats = {'total_chunks': 3, 'failed_chunks': 1}
        assert self._resp(correction_stats=stats).correction_stats == stats

    def test_both_default_to_none_when_absent(self):
        r = self._resp()
        assert r.ai_call_chain is None
        assert r.correction_stats is None


# ============ 总结版本号（2026-10-13 P0 修复）============
# 回归背景：main.py 收尾曾硬编码 save_summary_version(version=1)，
# 任务重试成功会用本次结果 INSERT OR REPLACE 覆盖用户归档的 v1，并把
# summary_version 打回 1 —— 已归档版本永久丢失、历史列表与指针自相矛盾。
class TestAppendSummaryVersion:
    def _job(self):
        return create_job(input_type="url", input_url="https://example.com")

    def test_first_append_gets_v1(self):
        jid = self._job()
        v = append_summary_version(jid, "summary", "m", "正文一")
        assert v == 1
        assert get_summary_version(jid, 1)["result_text"] == "正文一"

    def test_never_overwrites_archived_version(self):
        """★ 核心回归：已有 v1..v3 时再次追加必须是 v4，v1..v3 一字不改。"""
        jid = self._job()
        for i in range(1, 4):
            append_summary_version(jid, "summary", "m", f"原文{i}")
        v = append_summary_version(jid, "summary", "m", "重跑结果")
        assert v == 4, f"版本号必须是 4，实际 {v}（硬编码 1 会返回 1 并覆盖 v1）"
        history = get_summary_history(jid)
        assert [h["version"] for h in history] == [1, 2, 3, 4]
        assert get_summary_version(jid, 1)["result_text"] == "原文1"
        assert get_summary_version(jid, 4)["result_text"] == "重跑结果"

    def test_pointer_does_not_drift_from_history(self):
        """存档成功后指针指向的版本必须真实存在于历史表里。"""
        jid = self._job()
        append_summary_version(jid, "summary", "m", "A")
        v2 = append_summary_version(jid, "summary", "m", "B")
        update_job(jid, summary_version=v2)
        job = get_job(jid)
        assert job["summary_version"] == 2
        assert get_summary_version(jid, job["summary_version"])["result_text"] == "B"

    def test_gap_is_filled_from_max_not_from_count(self):
        """历史有洞（如 v1,v2,v4）时必须取 MAX+1=5，而不是 count+1。"""
        jid = self._job()
        for i, text in enumerate(["A", "B", "C", "D"], start=1):
            save_summary_version(jid, i, "summary", "m", text)
        v = append_summary_version(jid, "summary", "m", "E")
        assert v == 5

    def test_empty_text_is_still_archived(self):
        """分批总结 fail-fast 后仍可能落一份空正文？—— 版本记录本身不应被跳过。"""
        jid = self._job()
        v = append_summary_version(jid, "summary", "m", "")
        assert v == 1
        assert get_summary_history(jid)[0]["result_text"] == ""
