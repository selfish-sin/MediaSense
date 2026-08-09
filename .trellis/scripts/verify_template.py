#!/usr/bin/env python3
"""
verify_template.py — 验证 AI_PROJECT_TEMPLATE 指向的模板项目是否包含
AI_AGENT_WORKFLOW.md §8.1 所需的全部基础设施文件。

用法: python verify_template.py [template_path]
不传参数时读 AI_PROJECT_TEMPLATE 环境变量。
"""
import os
import sys
from pathlib import Path

REQUIRED_FILES = [
    # 治理层
    "AGENTS.md",
    "CONTEXT.md",
    "ideas.md",
    ".trellis/project-foundation.json",
    ".trellis/workflow.md",
    ".trellis/config.yaml",
    ".trellis/spec/guides/index.md",
    ".trellis/spec/guides/slice-contract.md",
    ".trellis/spec/guides/slice-registry.md",
    ".trellis/spec/guides/code-reuse-thinking-guide.md",
    ".trellis/spec/guides/cross-layer-thinking-guide.md",
    ".trellis/spec/backend/index.md",
    ".trellis/spec/frontend/index.md",
    ".trellis/scripts/init_developer.py",
    # 前端脚手架
    "frontend/foundation.json",
    "frontend/vite.config.ts",
    "frontend/tsconfig.json",
    "frontend/tsconfig.app.json",
    "frontend/tsconfig.node.json",
    "frontend/.oxlintrc.json",
    "frontend/index.html",
    "frontend/src/index.css",
    "frontend/playwright.config.ts",
    "frontend/e2e/smoke.spec.ts",
]

# 不应从模板复制的（每项目独立 init）
NOT_COPIED = [".codegraph/", "node_modules/", ".venv/"]


def main():
    template = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("AI_PROJECT_TEMPLATE")
    if not template:
        print("ERROR: 未指定模板路径，且 AI_PROJECT_TEMPLATE 未设置")
        sys.exit(1)

    root = Path(template)
    if not root.is_dir():
        print(f"ERROR: 模板路径不存在: {root}")
        sys.exit(1)

    missing = []
    for f in REQUIRED_FILES:
        if not (root / f).exists():
            missing.append(f)

    if missing:
        print(f"FAIL: 缺少 {len(missing)} 个文件:")
        for m in missing:
            print(f"  MISSING  {m}")
        sys.exit(1)
    else:
        print(f"OK: 全部 {len(REQUIRED_FILES)} 个模板文件齐全 ({root})")


if __name__ == "__main__":
    main()
