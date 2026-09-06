"""
定向 bug 修复回归测试（纯本地：临时 SQLite + mock，不联网、不起服务）

覆盖：
- 修复1：翻译接口 files 合并逻辑（原 files 不丢、不再双重 JSON 编码）
         + _row_to_dict 对历史双层编码脏数据的防御性修复
- 修复4：finish_job/update_job 终态保护（cancelled 不被 completed/failed 覆盖）
- 修复6：fail_interrupted_jobs 把重启遗留的非终态任务标记为 failed
"""
import json
import sqlite3

import pytest
from unittest.mock import patch

from db.job_repo import (
    create_job, get_job, update_job, finish_job,
    _row_to_dict, fail_interrupted_jobs,
)

# 与 test_job_repo.py 相同的最小 schema + 连接 patch
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


def _raw_files_value(db_path, job_id):
    """直接读 DB 原始 files 列（不经过 _row_to_dict）"""
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT files FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return row[0]


# ============ 修复1：翻译接口 files 合并 ============

class TestTranslateFilesMerge:
    """bilingual_translate 保存译文到 job.files 的行为"""

    async def _call_route(self, monkeypatch, job_id):
        """mock 掉 AI 调用和模型查询后调用翻译路由（不联网）"""
        import services.ai_processor as aip
        import routers.translate as tr

        async def fake_call_ai(model_cfg, prompt, timeout=120.0, **kwargs):
            return "模拟译文", 11, 22

        monkeypatch.setattr(aip, "_call_ai", fake_call_ai)
        monkeypatch.setattr(
            tr, "get_model_by_key",
            lambda key: {"key": key, "endpoint": "http://fake.local", "temperature": 0.3},
        )
        return await tr.bilingual_translate(tr.BilingualRequest(
            text="hello world", target_lang="zh", job_id=job_id,
        ))

    async def test_merge_keeps_original_files(self, monkeypatch, db_path):
        """① 原 files（txt/srt/audio）不丢 + 译文合并 + DB 单层编码"""
        jid = create_job(input_type="url", input_url="https://x.com/v")
        update_job(jid, files={"txt": "/d/x.txt", "srt": "/d/x.srt", "audio": "/d/x.m4a"})

        resp = await self._call_route(monkeypatch, jid)
        assert resp["success"] is True

        files = get_job(jid)["files"]
        assert isinstance(files, dict)
        # 原文件路径保留
        assert files["txt"] == "/d/x.txt"
        assert files["srt"] == "/d/x.srt"
        assert files["audio"] == "/d/x.m4a"
        # 译文已合并
        assert files["translation_text"] == "模拟译文"
        assert files["translation_lang"] == "zh"

        # DB 原始值是单层 JSON：loads 一次即得 dict（旧 bug 下是 str）
        raw = _raw_files_value(db_path, jid)
        once = json.loads(raw)
        assert isinstance(once, dict)
        assert once["translation_text"] == "模拟译文"

    async def test_merge_on_legacy_double_encoded_row(self, monkeypatch, db_path):
        """② 历史脏数据（DB 里双层编码、读回是 str）也能正确合并并不再污染"""
        jid = create_job(input_type="url", input_url="https://x.com/v2")
        inner = json.dumps({"txt": "/d/old.txt", "srt": "/d/old.srt"}, ensure_ascii=False)
        double = json.dumps(inner, ensure_ascii=False)  # 模拟旧 bug 写入的脏值
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE jobs SET files = ? WHERE id = ?", (double, jid))
        conn.commit()
        conn.close()

        # _row_to_dict 防御性修复：读回即 dict
        job = get_job(jid)
        assert isinstance(job["files"], dict)
        assert job["files"]["txt"] == "/d/old.txt"

        resp = await self._call_route(monkeypatch, jid)
        assert resp["success"] is True

        files = get_job(jid)["files"]
        assert files["txt"] == "/d/old.txt"          # 脏数据里的原路径仍在
        assert files["translation_text"] == "模拟译文"
        # 写回后已是干净单层编码
        assert isinstance(json.loads(_raw_files_value(db_path, jid)), dict)

    def test_row_to_dict_repairs_double_and_triple_encoding(self, db_path):
        """③ _row_to_dict 对双层/三层编码的防御性解码"""
        payload = {"txt": "/d/a.txt"}
        double = json.dumps(json.dumps(payload, ensure_ascii=False), ensure_ascii=False)
        triple = json.dumps(double, ensure_ascii=False)
        jid = create_job(input_type="url", input_url="https://x.com/v3")
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE jobs SET files = ?, tags = ? WHERE id = ?", (double, triple, jid))
        conn.commit()
        conn.close()

        job = get_job(jid)
        assert job["files"] == payload
        assert job["tags"] == payload  # 三层也能解出来

    def test_job_list_response_validates_after_repair(self, db_path):
        """④ 脏数据修复后能通过 JobResponse 校验（旧 bug 下 GET /api/jobs 整体 500）

        列表查询已瘦身（不再 SELECT files/result_text 等大字段），脏 files 只
        会走详情接口的修复路径（上面用例已覆盖）；列表行不含 files，
        JobResponse 以默认 {} 校验通过。
        """
        from models.schemas import JobListResponse

        jid = create_job(input_type="url", input_url="https://x.com/v4")
        inner = json.dumps({"txt": "/d/b.txt"}, ensure_ascii=False)
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE jobs SET files = ? WHERE id = ?",
                     (json.dumps(inner, ensure_ascii=False), jid))
        conn.commit()
        conn.close()

        from db.job_repo import get_jobs, get_job
        jobs, total = get_jobs()
        # 瘦身列表不含大字段
        assert "files" not in jobs[0]
        assert "result_text" not in jobs[0]
        resp = JobListResponse(jobs=jobs, total=total)  # 不抛异常即通过
        assert resp.jobs[0].files == {}
        # 脏数据修复路径仍由详情接口兜底
        assert get_job(jid)["files"]["txt"] == "/d/b.txt"


# ============ 修复4：终态保护 ============

class TestTerminalStateProtection:
    """cancelled 是终态：后台收尾的 completed/failed 不得覆盖"""

    def test_cancelled_not_overwritten_by_completed(self):
        jid = create_job(input_type="url", input_url="a")
        update_job(jid, status="downloading")
        # 模拟 cancel_job：立即写 cancelled
        update_job(jid, status="cancelled", error="用户取消")
        # 模拟后台任务迟到的收尾
        finish_job(jid, success=True, files={"txt": "/d/late.txt"},
                   token_usage={"input": 1, "output": 2})

        job = get_job(jid)
        assert job["status"] == "cancelled"
        assert job["error"] == "用户取消"
        # 收尾数据也不应写入已取消的任务
        assert job["files"].get("txt") != "/d/late.txt"

    def test_cancelled_not_overwritten_by_failed(self):
        jid = create_job(input_type="url", input_url="b")
        update_job(jid, status="summarizing")
        update_job(jid, status="cancelled", error="用户取消")
        finish_job(jid, success=False, error="处理失败: xxx")

        job = get_job(jid)
        assert job["status"] == "cancelled"
        assert job["error"] == "用户取消"  # 不被失败原因覆盖

    def test_normal_finish_unaffected(self):
        """正常流程（非 cancelled）不受保护逻辑影响"""
        jid = create_job(input_type="url", input_url="c")
        finish_job(jid, success=True, files={"srt": "x"})
        assert get_job(jid)["status"] == "completed"

        jid2 = create_job(input_type="url", input_url="d")
        finish_job(jid2, success=False, error="boom")
        assert get_job(jid2)["status"] == "failed"

    def test_retry_transition_cancelled_to_pending_allowed(self):
        """retry 把 cancelled 改回 pending 必须不被拦截"""
        jid = create_job(input_type="url", input_url="e")
        update_job(jid, status="cancelled", error="用户取消")
        assert update_job(jid, status="pending", error=None)
        assert get_job(jid)["status"] == "pending"

    def test_direct_update_to_completed_blocked_on_cancelled(self):
        """绕过 finish_job 直接 update_job(status='completed') 同样被拦截"""
        jid = create_job(input_type="url", input_url="f")
        update_job(jid, status="cancelled", error="用户取消")
        assert update_job(jid, status="completed") is False
        assert get_job(jid)["status"] == "cancelled"


# ============ 修复6：重启遗留任务恢复 ============

class TestFailInterruptedJobs:
    def test_non_terminal_marked_failed(self):
        ids = {}
        for st in ("pending", "downloading", "transcribing", "preview", "summarizing"):
            jid = create_job(input_type="url", input_url=f"u-{st}")
            if st != "pending":
                update_job(jid, status=st)
            ids[st] = jid
        done = create_job(input_type="url", input_url="u-done")
        update_job(done, status="completed")
        cancelled = create_job(input_type="url", input_url="u-cancelled")
        update_job(cancelled, status="cancelled", error="用户取消")

        count = fail_interrupted_jobs()
        assert count == 5

        for jid in ids.values():
            job = get_job(jid)
            assert job["status"] == "failed"
            assert "服务重启中断" in (job["error"] or "")
        # 终态任务不动
        assert get_job(done)["status"] == "completed"
        assert get_job(cancelled)["status"] == "cancelled"
        # 标记为 failed 后可被 retry 接受（retry 只收 failed/cancelled）
        assert get_job(ids["preview"])["status"] in ("failed", "cancelled")
