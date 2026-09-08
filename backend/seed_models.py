"""
Seed default AI models into the database.
Run: python seed_models.py
Creates glm-4.5-air and deepseek-v4-flash if they don't already exist.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db.model_repo import add_model, get_all_models
from config import ZHIPU_API_KEY

models = [
    {
        "key": "glm-4.5-air",
        "name": "GLM 4.5 Air",
        "engine": "智谱",
        "endpoint": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model_id": "glm-4.5-air",
        "auth_type": "bearer",
        "api_key": ZHIPU_API_KEY,
        "temperature": 0.3,
        "max_tokens": 4096,
    },
    {
        "key": "deepseek-v4-flash",
        "name": "DeepSeek V4 Flash",
        "engine": "DeepSeek",
        "endpoint": "https://api.deepseek.com/v1/chat/completions",
        "model_id": "deepseek-chat",
        "auth_type": "bearer",
        "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "temperature": 0.3,
        "max_tokens": 8192,
    },
]

existing = get_all_models()
existing_keys = {m['key'] for m in existing}

for m in models:
    if m['key'] in existing_keys:
        print(f"[SKIP] {m['key']} already exists")
        continue
    ok = add_model(**m)
    if ok:
        print(f"[OK] {m['key']} ({m['name']}) created")
    else:
        print(f"[FAIL] {m['key']} creation failed")

print("Seed complete.")
print("NOTE: deepseek-v4-flash requires DEEPSEEK_API_KEY env var to be set.")
