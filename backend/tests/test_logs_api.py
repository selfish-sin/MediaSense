"""
/api/logs 真实契约冒烟：TestClient 打真实 FastAPI app（真实路由注册/参数校验/序列化），
不是前端手写 mock 形状。

背景（2026-09-07 复盘）：后端曾把 total 硬编码 0，而前端测试全 mock 非零 total，
日志面板增量静默失效且测试全绿。本测试从后端侧钉死契约：
响应体精确等于 {"lines": [...]}——改响应形状必须 consciously 同步改这里和
前端 useBackendLogs.fetchLogs。
"""
import re
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_socketio(monkeypatch):
    """mock socketio 模块，测试结束后自动恢复（与 test_main.py 同模式）"""
    mock_sio = MagicMock()
    mock_sio.AsyncServer = MagicMock()
    mock_sio.ASGIApp = MagicMock()
    monkeypatch.setitem(sys.modules, 'socketio', mock_sio)
    yield


@pytest.fixture
def client(_mock_socketio):
    from fastapi.testclient import TestClient
    from main import app
    # 不用 with 上下文：跳过 startup/shutdown（DB 恢复、清理任务等副作用），
    # 路由在 import 期已全部注册，契约不受影响
    return TestClient(app, client=('127.0.0.1', 50000))


def _write_log(monkeypatch, tmp_path, content: str):
    import main
    log = tmp_path / "app.log"
    log.write_text(content, encoding="utf-8")
    monkeypatch.setattr(main, "LOG_FILE", str(log))


class TestLogsApiContract:
    def test_small_file_exact_body(self, client, monkeypatch, tmp_path):
        """≤256KB 完整窗口含首行；响应体精确等于 {"lines": [...]}（total 不得回归）"""
        _write_log(monkeypatch, tmp_path, "\n".join(f"line{i}" for i in range(1, 6)))
        r = client.get("/api/logs")
        assert r.status_code == 200
        assert r.json() == {"lines": ["line1", "line2", "line3", "line4", "line5"]}

    def test_lines_param_returns_tail(self, client, monkeypatch, tmp_path):
        """lines 参数截尾（真实 FastAPI query 参数校验链路）"""
        _write_log(monkeypatch, tmp_path, "\n".join(f"l{i}" for i in range(10)))
        r = client.get("/api/logs", params={"lines": 3})
        assert r.json()["lines"] == ["l7", "l8", "l9"]

    def test_missing_file_returns_empty(self, client, monkeypatch, tmp_path):
        """日志文件不存在：200 + 空列表，不 500"""
        import main
        monkeypatch.setattr(main, "LOG_FILE", str(tmp_path / "nonexistent.log"))
        r = client.get("/api/logs")
        assert r.status_code == 200
        assert r.json() == {"lines": []}

    def test_big_file_window_drops_partial_head(self, client, monkeypatch, tmp_path):
        """>256KB 回读窗口：起点切在行中间时首段不完整行被丢，返回行均为完整行"""
        # 5000 行 × ~100B ≈ 500KB，窗口只覆盖尾部 ~256KB
        rows = [f"row{i}_" + "y" * 90 for i in range(1, 5001)]
        _write_log(monkeypatch, tmp_path, "\n".join(rows))
        # 请求量取参数上界 5000（2026-10-13 起 lines 有 le=5000 上界），
        # 仍远大于 256KB 窗口能容纳的行数，窗口语义照常被验证。
        r = client.get("/api/logs", params={"lines": 5000})
        lines = r.json()["lines"]
        # 末行是文件真实最后一行
        assert lines[-1] == rows[-1]
        # 窗口生效：没有返回全部 5000 行
        assert len(lines) < 5000
        # 每行都是完整行（被切在中间的半行必须被丢弃，不能混进响应）
        pat = re.compile(r"^row\d+_y{90}$")
        assert all(pat.match(x) for x in lines), lines[0]

    @pytest.mark.parametrize("bad", [-1, 0, 10_000_000])
    def test_lines_param_rejects_out_of_range(self, client, bad):
        """lines 必须有上下界（2026-10-13 修复 P1-7）。

        此前是裸 `int`：负数会经 `all_lines[-lines:]` 反向切片成
        `all_lines[|lines|:]`，语义悄悄变成「丢弃前 N 行、返回其余全部」。
        """
        r = client.get("/api/logs", params={"lines": bad})
        assert r.status_code == 422, f"lines={bad} 必须被 422 拒绝"

    def test_lines_param_accepts_boundary_values(self, client, monkeypatch, tmp_path):
        """加上界不能把正常调用挡掉（1 与上界都必须放行）。"""
        _write_log(monkeypatch, tmp_path, "\n".join(f"l{i}" for i in range(1, 11)))
        assert client.get("/api/logs", params={"lines": 1}).json()["lines"] == ["l10"]
        assert client.get("/api/logs", params={"lines": 5000}).status_code == 200
