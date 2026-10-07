"""验证入口必须拒绝无效目标，并将检查失败向上返回。"""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def verifier():
    path = Path(__file__).resolve().parents[2] / "verify.py"
    spec = importlib.util.spec_from_file_location("project_verify", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_invalid_target_does_not_report_success(verifier, monkeypatch, capsys):
    monkeypatch.setattr(verifier, "run", lambda *args: pytest.fail("不应运行检查"))
    with pytest.raises(SystemExit) as exc:
        verifier.main(["backedn"])
    assert exc.value.code == 2
    assert "ALL CHECKS PASSED" not in capsys.readouterr().out


def test_lint_failure_fails_backend_verification(verifier, monkeypatch, capsys):
    commands = []

    def fake_run(cmd, cwd):
        commands.append(cmd)
        return 1 if "ruff" in cmd else 0

    monkeypatch.setattr(verifier, "run", fake_run)
    with pytest.raises(SystemExit) as exc:
        verifier.main(["backend"])
    assert exc.value.code == 1
    assert any("pytest" in cmd for cmd in commands)
    assert "Python ruff" in capsys.readouterr().out
