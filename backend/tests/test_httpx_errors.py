"""
httpx_errors 描述助手测试：验证报错"回音"服务端响应体里的真实原因
（OpenAI 兼容 error.message / message / detail / code，过滤 HTML，截断长文）
"""
import httpx

from services.httpx_errors import describe_http_error, describe_http_response


def _resp(body: str, status: int = 404) -> httpx.Response:
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    return httpx.Response(status, text=body, request=request)


class TestDescribeHttpResponse:
    def test_openai_error_message(self):
        """OpenAI 风格 {"error":{"message":...}}——本次用户遇见的 model_not_found 场景"""
        body = '{"error":{"message":"当前分组暂不支持模型「gemini-3.8-flash-high」，请核对模型名称，或切换分组/模型后重试","type":"model_not_found"}}'
        out = describe_http_response(_resp(body, 404))
        assert out.startswith("HTTP 404: 当前分组暂不支持模型「gemini-3.8-flash-high」")

    def test_top_level_message_with_code(self):
        """顶层 {"code":..., "message":...}（如随想 401 API_KEY_REQUIRED）"""
        body = '{"code":"API_KEY_REQUIRED","message":"🔑 您的 API Key 无效或已停用，请到控制台核对密钥状态"}'
        out = describe_http_response(_resp(body, 401))
        assert out == "HTTP 401: 🔑 您的 API Key 无效或已停用，请到控制台核对密钥状态"

    def test_error_as_string(self):
        out = describe_http_response(_resp('{"error":"Unauthorized"}', 401))
        assert out == "HTTP 401: Unauthorized"

    def test_error_code_fallback_when_no_message(self):
        """error 对象里没有 message 时回退 type/code"""
        out = describe_http_response(_resp('{"error":{"type":"model_not_found"}}', 404))
        assert out == "HTTP 404: model_not_found"

    def test_fastapi_detail_list(self):
        """FastAPI 422 校验错误 {"detail":[{"msg":...}]}"""
        body = '{"detail":[{"loc":["body","api_key"],"msg":"field required"}]}'
        out = describe_http_response(_resp(body, 422))
        assert out == "HTTP 422: field required"

    def test_plain_text_body(self):
        out = describe_http_response(_resp("Bad Gateway", 502))
        assert out == "HTTP 502: Bad Gateway"

    def test_html_body_flagged(self):
        out = describe_http_response(_resp("<!DOCTYPE html><html><body>Just a moment...</body></html>", 404))
        assert out == "HTTP 404: 响应是网页而非 API 错误（可能被防火墙拦截或需登录）"

    def test_empty_body(self):
        out = describe_http_response(_resp("", 500))
        assert out == "HTTP 500: 服务端返回空响应"

    def test_long_message_truncated(self):
        msg = "A" * 500
        out = describe_http_response(_resp(f'{{"error":{{"message":"{msg}"}}}}', 429))
        assert out.startswith("HTTP 429: ")
        assert len(out) <= 300 + len("HTTP 429: ") + 1  # 300 截断 + “…”占位
        assert out.endswith("…")
        assert out.count("A") == 300


class TestDescribeHttpError:
    def test_from_http_status_error(self):
        """raise_for_status 抛出的 HTTPStatusError 形态（ai_processor 直接透传路径）"""
        request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
        response = _resp('{"error":{"message":"余额不足"}}', 402)
        exc = httpx.HTTPStatusError("Client error '402' for url 'https://api.example.com/v1/chat/completions'",
                                    request=request, response=response)
        assert describe_http_error(exc) == "HTTP 402: 余额不足"
