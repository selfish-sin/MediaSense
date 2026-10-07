"""
/api/export/batch 批量导出契约冒烟：TestClient 打真实 app（真实路由注册/参数校验），
monkeypatch routers.export.get_job 提供假任务数据。

钉死的契约：
- 每个任务一个 .md（内容 = "# {title}\n\n{result_text}"），标题重名时文件加序号
- 无结果 / 任务不存在的条目被跳过，不报错
- 全部无结果 → 400；job_ids 为空 → 400
"""
import io
import zipfile
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    fastapi_app = FastAPI()
    from routers.export import router as export_router
    fastapi_app.include_router(export_router, prefix="/api/export")
    return fastapi_app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def fake_jobs():
    return {
        "j1": {"id": "j1", "title": "第一集 解说", "result_text": "## 总结\n这里是第一集的内容。"},
        # j2 缺 title：文件名回退 AI总结_j2，内容标题也用回退
        "j2": {"id": "j2", "result_text": "## 总结\n第二条结果。\n"},
        "j3": {"id": "j3", "status": "pending", "result_text": None},  # 无结果 → 跳过
        # j4 不返回 → 跳过（任务不存在）
    }


@pytest.fixture
def mock_get_job(monkeypatch, fake_jobs):
    def _get_job(jid):
        return fake_jobs.get(jid)
    monkeypatch.setattr("routers.export.get_job", _get_job)
    return _get_job


class TestBatchExport:
    def test_zip_contains_only_exportable_jobs(self, client, mock_get_job):
        resp = client.post("/api/export/batch", json={"job_ids": ["j1", "j2", "j3", "j4"]})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/zip")
        assert resp.headers["content-disposition"], "缺少 content-disposition（前端依赖它取文件名）"
        assert resp.headers["content-disposition"].endswith(".zip")

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = sorted(zf.namelist())
        # ★ 命名统一后：保留可读标点与空格；无标题任务用「任务 {id}」而非 AI总结_{id}
        assert names == ["任务 j2.md", "第一集 解说.md"]
        assert zf.read("第一集 解说.md").decode("utf-8") == "# 第一集 解说\n\n## 总结\n这里是第一集的内容。\n"
        assert zf.read("任务 j2.md").decode("utf-8") == "# 任务 j2\n\n## 总结\n第二条结果。\n"

    def test_zip_download_name_uses_video_title(self, client, mock_get_job):
        """ZIP 下载名（浏览器看到的）用视频名，不再带随机 hex 段。"""
        resp = client.post("/api/export/batch", json={"job_ids": ["j1", "j2"]})
        assert resp.status_code == 200
        disposition = unquote(resp.headers["content-disposition"])
        assert "第一集 解说等2个.zip" in disposition
        assert "b921e7" not in disposition  # 旧随机段不得再出现

    def test_single_video_zip_name_has_no_count_suffix(self, client, mock_get_job):
        resp = client.post("/api/export/batch", json={"job_ids": ["j1"]})
        disposition = unquote(resp.headers["content-disposition"])
        assert "第一集 解说.zip" in disposition

    def test_duplicate_titles_get_numbered_suffix(self, client, monkeypatch):
        monkeypatch.setattr("routers.export.get_job", lambda jid: {
            "j1": {"id": "j1", "title": "同名", "result_text": "甲"},
            "j2": {"id": "j2", "title": "同名", "result_text": "乙"},
        }.get(jid))
        resp = client.post("/api/export/batch", json={"job_ids": ["j1", "j2"]})
        assert resp.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        assert sorted(zf.namelist()) == ["同名.md", "同名_2.md"]

    def test_all_empty_results_returns_400(self, client, mock_get_job):
        resp = client.post("/api/export/batch", json={"job_ids": ["j3", "j4"]})
        assert resp.status_code == 400
        assert "没有可导出" in resp.json()["detail"]

    def test_empty_job_ids_returns_400(self, client):
        resp = client.post("/api/export/batch", json={"job_ids": []})
        assert resp.status_code == 400

    def test_missing_field_rejected_by_validation(self, client):
        resp = client.post("/api/export/batch", json={})
        assert resp.status_code == 422
