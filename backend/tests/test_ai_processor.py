"""
ai_processor 核心AI处理服务测试（可快速覆盖部分）
"""
import pytest
import json
import os
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path

import httpx
from services.ai_processor import (
    TaskCancelledError,
    _check_cancelled,
    _build_headers,
    _record_token_usage,
    _save_checkpoint,
    _load_checkpoint,
    generate_tags,
    generate_mindmap,
)


class TestTaskCancelledError:
    def test_raises_and_caught(self):
        with pytest.raises(TaskCancelledError):
            raise TaskCancelledError("test")


class TestCheckCancelled:
    def test_not_cancelled(self):
        with patch("socket_handlers.check_cancelled", return_value=False):
            _check_cancelled("job-1")  # 不抛异常

    def test_cancelled_raises(self):
        with patch("socket_handlers.check_cancelled", return_value=True):
            with pytest.raises(TaskCancelledError):
                _check_cancelled("job-2")

    def test_no_job_id(self):
        _check_cancelled("")  # 不抛异常
        _check_cancelled(None)  # 不抛异常


class TestBuildHeaders:
    def test_bearer_auth(self):
        cfg = {"auth_type": "bearer", "api_key": "sk-123"}
        h = _build_headers(cfg)
        assert "Content-Type" in h
        assert h["Authorization"] == "Bearer sk-123"

    def test_direct_auth(self):
        cfg = {"auth_type": "direct", "api_key": "key-456"}
        h = _build_headers(cfg)
        assert h["Authorization"] == "key-456"

    def test_no_api_key(self):
        cfg = {"auth_type": "bearer", "api_key": ""}
        h = _build_headers(cfg)
        assert "Authorization" not in h

    def test_default_auth_type(self):
        cfg = {"api_key": "sk"}
        h = _build_headers(cfg)
        assert h["Authorization"] == "Bearer sk"


class TestRecordTokenUsage:
    def test_writes_to_db(self):
        mock_conn = MagicMock()
        with patch("services.ai_processor.get_connection", return_value=mock_conn):
            _record_token_usage("glm", 100, 200, "job-1")
            mock_conn.execute.assert_called_once()
            mock_conn.commit.assert_called_once()
            mock_conn.close.assert_called_once()

    def test_db_error_swallowed(self):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("db error")
        with patch("services.ai_processor.get_connection", return_value=mock_conn):
            _record_token_usage("glm", 100, 200, "job-1")  # 不抛异常
            mock_conn.close.assert_called_once()


class TestCheckpoint:
    def test_save_and_load(self, tmp_path):
        cp_dir = tmp_path / "checkpoints"
        cp_dir.mkdir()
        _save_checkpoint(cp_dir, "round1", "output text", 50, 100)
        loaded = _load_checkpoint(cp_dir, "round1")
        assert loaded is not None
        assert loaded["round"] == "round1"
        assert loaded["output"] == "output text"
        assert loaded["input_tokens"] == 50
        assert loaded["output_tokens"] == 100

    def test_load_nonexistent(self, tmp_path):
        result = _load_checkpoint(tmp_path / "nosuch", "x")
        assert result is None


@pytest.mark.asyncio
class TestGenerateTags:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.mock_cfg = {"name": "test", "key": "glm", "model_id": "glm-4",
                         "endpoint": "https://api.example.com/v1/chat/completions",
                         "auth_type": "bearer", "api_key": "sk",
                         "temperature": 0.3, "max_tokens": 4096}

    async def test_tags_success(self):
        with patch("services.ai_processor.get_model_by_key", return_value=self.mock_cfg):
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": '["哲学","AI","技术"]'}}]
            }
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_resp
            with patch("httpx.AsyncClient", return_value=mock_client):
                tags = await generate_tags("some text here for testing")
                assert "哲学" in tags
                assert len(tags) <= 5

    async def test_tags_markdown_wrapped(self):
        with patch("services.ai_processor.get_model_by_key", return_value=self.mock_cfg):
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": '```json\n["历史","分析"]\n```'}}]
            }
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_resp
            with patch("httpx.AsyncClient", return_value=mock_client):
                tags = await generate_tags("test")
                assert "历史" in tags

    async def test_tags_failure_returns_empty(self):
        with patch("services.ai_processor.get_model_by_key", return_value=self.mock_cfg):
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post.side_effect = Exception("API error")
            with patch("httpx.AsyncClient", return_value=mock_client):
                tags = await generate_tags("test")
                assert tags == []


@pytest.mark.asyncio
class TestGenerateMindmap:
    async def test_mindmap_success(self):
        mock_cfg = {"name": "test", "key": "glm", "model_id": "glm-4",
                    "endpoint": "https://api.example.com/v1/chat/completions",
                    "auth_type": "bearer", "api_key": "sk",
                    "temperature": 0.3, "max_tokens": 4096}
        with patch("services.ai_processor.get_model_by_key", return_value=mock_cfg):
            with patch("services.ai_processor._record_token_usage"):
                mock_resp = MagicMock()
                mock_resp.raise_for_status = MagicMock()
                mock_resp.json.return_value = {
                    "choices": [{"message": {"content": "# 思维导图\n## 主题"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }
                mock_client = AsyncMock()
                mock_client.__aenter__.return_value = mock_client
                mock_client.post.return_value = mock_resp
                with patch("httpx.AsyncClient", return_value=mock_client):
                    result = await generate_mindmap("result text", title="Test")
                    assert "# 思维导图" in result

    async def test_mindmap_failure_returns_empty(self):
        mock_cfg = {"name": "test", "key": "glm", "model_id": "glm-4",
                    "endpoint": "https://api.example.com/v1/chat/completions",
                    "auth_type": "bearer", "api_key": "sk",
                    "temperature": 0.3, "max_tokens": 4096}
        with patch("services.ai_processor.get_model_by_key", return_value=mock_cfg):
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post.side_effect = Exception("fail")
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await generate_mindmap("x")
                assert result == ""
