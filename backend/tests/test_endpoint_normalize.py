"""端点补全 _normalize_endpoint 回归锁（2026-10-05）

曾经的 bug：无条件插 /v1，把自带非 v1 版本段的端点改废——
火山方舟 /api/v3、智谱 GLM /api/paas/v4 都会变成 .../v1/chat/completions 而 404。
"""
import pytest

from routers.models import _normalize_endpoint


class TestNormalizeEndpoint:
    @pytest.mark.parametrize("raw,expect", [
        # —— 无版本段：按 OpenAI 惯例补 /v1（原有行为不变）——
        ("https://api.deepseek.com", "https://api.deepseek.com/v1/chat/completions"),
        ("https://api.deepseek.com/", "https://api.deepseek.com/v1/chat/completions"),
        ("https://api.deepseek.com/chat/completions",
         "https://api.deepseek.com/v1/chat/completions"),
        ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1/chat/completions"),
        ("https://api.deepseek.com/v1/chat/completions",
         "https://api.deepseek.com/v1/chat/completions"),
        # —— 已带 v1：原样保留（幂等）——
        ("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
         "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"),
        ("https://api.longcat.chat/openai/v1/chat/completions",
         "https://api.longcat.chat/openai/v1/chat/completions"),
        # —— ★ 带非 v1 版本段：绝不能插 /v1（本次修复的真 bug）——
        ("https://ark.cn-beijing.volces.com/api/v3",
         "https://ark.cn-beijing.volces.com/api/v3/chat/completions"),
        ("https://ark.cn-beijing.volces.com/api/v3/chat/completions",
         "https://ark.cn-beijing.volces.com/api/v3/chat/completions"),
        ("https://ark.cn-beijing.volces.com/api/v3/",
         "https://ark.cn-beijing.volces.com/api/v3/chat/completions"),
        ("https://open.bigmodel.cn/api/paas/v4",
         "https://open.bigmodel.cn/api/paas/v4/chat/completions"),
        ("https://open.bigmodel.cn/api/paas/v4/chat/completions",
         "https://open.bigmodel.cn/api/paas/v4/chat/completions"),
        ("https://example.com/api/v2beta",
         "https://example.com/api/v2beta/chat/completions"),
        # —— ★ 方舟 Agent Plan（/api/plan/v3）：与 /api/v3 同类，绝不能插 /v1 ——
        # 2026-10-06 实测：DB 里存的是 /api/plan/v3/v1/chat/completions（旧版无条件插 /v1 的产物），
        # 每次调用 404 后才换模型白耗一个候选位
        ("https://ark.cn-beijing.volces.com/api/plan/v3",
         "https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions"),
        ("https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions",
         "https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions"),
    ])
    def test_normalize(self, raw, expect):
        assert _normalize_endpoint(raw) == expect

    def test_idempotent(self):
        """重复规范化必须稳定——否则每次编辑模型都会把端点改变形。"""
        for raw in ("https://ark.cn-beijing.volces.com/api/v3",
                    "https://api.deepseek.com",
                    "https://open.bigmodel.cn/api/paas/v4"):
            once = _normalize_endpoint(raw)
            assert _normalize_endpoint(once) == once

    def test_empty(self):
        assert _normalize_endpoint('') == ''
        assert _normalize_endpoint(None) == ''
