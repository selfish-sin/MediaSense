"""GET /api/jobs/queue 契约测试：队列 = running（升序）+ pending（FIFO 升序）。

钉死的契约：
- pending/running 各自按 created_at ASC（同秒再用 rowid 兜底）→ 界面顺序 = 实际获槽顺序
- `concurrency` 来自 config.MAX_CONCURRENT_JOBS（默认 2，设为 1 才是严格串行）
- 空队列返回 200 + 空数组（不是 404）
- `/queue` 不被 `GET /{job_id}` 捕获（单段路径路由遮蔽回归锁）
- 队列项只含轻量列，不带 result_text 等大文本（轮询成本）
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(db_path):
    app = FastAPI()
    from routers.jobs import router as jobs_router
    app.include_router(jobs_router, prefix="/api/jobs")
    return TestClient(app)


def _insert(job_id, status, created_at, title=None):
    from db.database import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, title, input_url, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, title, "https://example.com/video", status, created_at, created_at),
    )
    conn.commit()
    conn.close()


class TestQueueSnapshot:
    def test_empty_queue_returns_200_with_empty_lists(self, client):
        resp = client.get("/api/jobs/queue")

        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] == []
        assert data["pending"] == []
        assert data["total"] == 0
        assert data["concurrency"] >= 1

    def test_pending_is_fifo_ordered(self, client):
        """同秒提交（SQLite CURRENT_TIMESTAMP 秒级）时靠 rowid 保证 FIFO 稳定。"""
        _insert("j2", "pending", "2026-09-21 12:00:02")
        _insert("j1", "pending", "2026-09-21 12:00:01")
        _insert("j3", "pending", "2026-09-21 12:00:02")

        data = client.get("/api/jobs/queue").json()

        assert [j["id"] for j in data["pending"]] == ["j1", "j2", "j3"]
        assert data["total"] == 3
        assert data["running"] == []

    def test_running_and_pending_are_separated(self, client):
        _insert("run1", "downloading", "2026-09-21 12:00:01")
        _insert("wait1", "pending", "2026-09-21 12:00:02")
        _insert("done1", "completed", "2026-09-21 12:00:03")

        data = client.get("/api/jobs/queue").json()

        assert [j["id"] for j in data["running"]] == ["run1"]
        assert [j["id"] for j in data["pending"]] == ["wait1"]
        assert data["total"] == 2

    def test_terminal_statuses_not_in_queue(self, client):
        _insert("c1", "completed", "2026-09-21 12:00:01")
        _insert("f1", "failed", "2026-09-21 12:00:02")
        _insert("x1", "cancelled", "2026-09-21 12:00:03")

        assert client.get("/api/jobs/queue").json()["total"] == 0

    def test_queue_path_not_shadowed_by_job_detail(self, client):
        """`/queue` 必须命中队列端点，而非 GET /{job_id}（job_id='queue' → 404）。"""
        resp = client.get("/api/jobs/queue")

        assert resp.status_code == 200
        assert "pending" in resp.json()

    def test_live_pending_job_counts_as_running(self, client, monkeypatch):
        """DB status 还停在 pending、但内存已标记 processing 的任务必须算「正在处理」。

        实战（2026-09-21）：字幕/下载阶段不更新 DB status，界面上 4 个任务看着全在排队，
        其实第一个已经在跑字幕——只按 status 分类会误报。
        """
        _insert("live1", "pending", "2026-09-21 12:00:01")
        _insert("wait1", "pending", "2026-09-21 12:00:02")
        import main
        monkeypatch.setitem(main.RUNNING_JOBS, "live1",
                            {"task": object(), "status": "processing"})

        data = client.get("/api/jobs/queue").json()

        assert [j["id"] for j in data["running"]] == ["live1"]
        assert data["running"][0]["live"] is True
        assert [j["id"] for j in data["pending"]] == ["wait1"]

    def test_waiting_slot_job_stays_pending(self, client, monkeypatch):
        """已 start_job 但还在等信号量（无 processing 标记）→ 仍是「排队中」。"""
        _insert("w1", "pending", "2026-09-21 12:00:01")
        import main
        monkeypatch.setitem(main.RUNNING_JOBS, "w1", {"task": object()})

        data = client.get("/api/jobs/queue").json()

        assert data["running"] == []
        assert [j["id"] for j in data["pending"]] == ["w1"]

    def test_queue_items_exclude_heavy_text_columns(self, client):
        _insert("p1", "pending", "2026-09-21 12:00:01")

        item = client.get("/api/jobs/queue").json()["pending"][0]

        assert item["id"] == "p1"
        assert "result_text" not in item
        assert "preview_text" not in item
