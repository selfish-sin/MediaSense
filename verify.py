"""
MediaSense 一键验证：Python ruff + 后端 pytest + 前端 eslint + vitest + 构建
Usage:
  python verify.py          # 全部
  python verify.py backend  # Python lint + 后端 pytest
  python verify.py frontend # 仅前端 lint + vitest + build
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Windows 控制台默认 GBK：结尾的 ✓/✗ 会让 print 抛 UnicodeEncodeError（验证通过也"崩"）
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / "backend" / "venv" / "Scripts" / "python.exe"


def run(cmd, cwd):
    print(f"\n> {subprocess.list2cmdline([str(arg) for arg in cmd])}  (cwd: {cwd})", flush=True)
    try:
        return subprocess.run(cmd, cwd=str(cwd), check=False).returncode
    except OSError as exc:
        print(f"无法执行检查，请确认已安装依赖：{exc}")
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", nargs="?", choices=("all", "backend", "frontend"), default="all")
    target = parser.parse_args(argv).target
    failures = []

    if target in ("backend", "all"):
        py = VENV_PY if VENV_PY.exists() else sys.executable
        if run([str(py), "-m", "ruff", "check", "--config", "backend/pyproject.toml",
                "backend", "verify.py", "build_installer.py"], ROOT) != 0:
            failures.append("Python ruff")
        if run([str(py), "-m", "pytest", "tests/"], ROOT / "backend") != 0:
            failures.append("backend pytest")

    if target in ("frontend", "all"):
        npm = shutil.which("npm") or "npm"
        if run([npm, "run", "lint"], ROOT / "frontend") != 0:
            failures.append("frontend eslint")
        if run([npm, "test"], ROOT / "frontend") != 0:
            failures.append("frontend vitest")
        if run([npm, "run", "build"], ROOT / "frontend") != 0:
            failures.append("frontend build")

    if failures:
        print(f"\n✗ FAILED: {', '.join(failures)}")
        sys.exit(1)
    print("\n✓ ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
