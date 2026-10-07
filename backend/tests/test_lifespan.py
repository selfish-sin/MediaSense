"""真实 ASGI 启动路径必须恢复中断任务，且只接触隔离数据目录。"""
from fastapi.testclient import TestClient

from db.job_repo import create_job, get_job, update_job


def test_socket_app_lifespan_recovers_interrupted_jobs():
    import main

    pending = create_job(input_type="text", input_text="待恢复")
    completed = create_job(input_type="text", input_text="已完成")
    update_job(completed, status="completed")

    with TestClient(main.socket_app) as client:
        job = get_job(pending)
        assert job["status"] == "failed"
        assert "重启" in job["error"]
        assert get_job(completed)["status"] == "completed"
        response = client.get(f'/api/jobs/{pending}')
        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        assert client.get("/api/jobs/queue").status_code == 200
        assert client.get("/openapi.json").status_code == 200
