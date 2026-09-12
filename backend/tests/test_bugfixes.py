"""
定向 bug 修复回归测试（纯本地：临时 SQLite + mock，不联网、不起服务）

覆盖：
- 修复1：files 脏数据防御（_row_to_dict 对历史双层/三层编码的解码修复）
         ※ 原 bilingual 翻译端点已于 2026-09-12 删除（前端双语块移除后无调用方）
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


@pytest.fixture(autouse=True)
def _patch_conn(db_path):
    def _get_conn():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    with patch("db.job_repo.get_connection", side_effect=_get_conn):
        yield


# ============ 修复1：files 脏数据防御 ============

class TestFilesDecodeRepair:
    """_row_to_dict 对历史双层/三层编码脏数据的防御性修复
    （原 bilingual_translate 路由用例随端点删除，2026-09-12）"""

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
