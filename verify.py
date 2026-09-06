"""
MediaSense 一键验证：后端 pytest + 前端 vitest + 前端构建
Usage:
  python verify.py          # 全部
  python verify.py backend  # 仅后端 pytest
  python verify.py frontend # 仅前端 vitest + build
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
VENV_PY = ROOT / "backend" / "venv" / "Scripts" / "python.exe"


def run(cmd, cwd):
    print(f"\n> {cmd}  (cwd: {cwd})")
    return subprocess.run(cmd, shell=True, cwd=str(cwd)).returncode


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    failures = []

    if target in ("backend", "all"):
        py = VENV_PY if VENV_PY.exists() else sys.executable
        if run(f'"{py}" -m pytest tests/', ROOT / "backend") != 0:
            failures.append("backend pytest")

    if target in ("frontend", "all"):
        if run("npm test", ROOT / "frontend") != 0:
            failures.append("frontend vitest")
        if run("npx vite build", ROOT / "frontend") != 0:
            failures.append("frontend build")

    if failures:
        print(f"\n✗ FAILED: {', '.join(failures)}")
        sys.exit(1)
    print("\n✓ ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
