'''Export the FastAPI OpenAPI schema to contracts/openapi.json (contract snapshot for FE/AI checks).'''
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from main import app

OUT_PATH = REPO_ROOT / 'contracts' / 'openapi.json'


def main() -> int:
    spec = app.openapi()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'[ok] wrote {OUT_PATH.relative_to(REPO_ROOT)} (paths={len(spec.get("paths", {}))})')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
