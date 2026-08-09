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
    correct_transcript,
    _count_markers,
    _split_by_boundary,
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


# ── 校正辅助函数 ──
class TestCountMarkers:
    def test_timestamps(self):
        assert _count_markers("[00:05 - 00:08] 你好") == 1
        assert _count_markers("[01:23] 甲\n[02:45] 乙") == 2

    def test_speakers(self):
        assert _count_markers("[说话人1] hello [Speaker A] hi") == 2

    def test_mixed(self):
        assert _count_markers("[00:05] [说话人0] 文本") == 2

    def test_none(self):
        assert _count_markers("纯文本无标记") == 0


class TestSplitByBoundary:
    def test_short_returns_single(self):
        assert _split_by_boundary("短文本", 80000) == ["短文本"]

    def test_splits_by_line(self):
        text = "第一行\n第二行\n第三行"
        chunks = _split_by_boundary(text, 4)
        assert len(chunks) >= 2
        # 切点应在换行处，单块不超限（除非单行超长）
        for c in chunks:
            assert len(c) <= 4 or '\n' not in c

    def test_does_not_split_inside_marker(self):
        # 标记不应被劈开：每块要么完整含标记，要么不含
        text = "[00:05 - 00:08] 段落一" * 50
        chunks = _split_by_boundary(text, 30)
        # 不应出现 [00:05 - 00:0 这种残缺
        joined = '\n'.join(chunks)
        assert "[00:05 - 00:08]" in joined  # 至少有一个完整标记
        # 各块内部不应出现未闭合的 [（即 [ 后必跟 ] 闭合）
        import re
        for c in chunks:
            # 不存在 [ 后 10 字符内无 ] 的情况（简单残缺检测）
            for m in re.finditer(r'\[', c):
                tail = c[m.start():m.start() + 20]
                assert ']' in tail, f"标记可能在块边界被劈开: {tail!r}"


# ── correct_transcript 加固测试 ──
@pytest.mark.asyncio
class TestCorrectTranscript:
    mock_cfg = {"name": "test", "key": "glm", "model_id": "glm-4",
                "endpoint": "https://api.example.com/v1/chat/completions",
                "auth_type": "bearer", "api_key": "sk",
                "temperature": 0.3, "max_tokens": 4096}

    async def _run(self, text, fake_outputs):
        """fake_outputs: list of (content, in_tok, out_tok) 依次返回"""
        call_results = list(fake_outputs)
        async def fake_call_ai(cfg, prompt, **kw):
            return call_results.pop(0) if call_results else ("", 0, 0)
        with patch("services.ai_processor.get_model_by_key", return_value=self.mock_cfg), \
             patch("services.ai_processor._call_ai", side_effect=fake_call_ai), \
             patch("services.ai_processor._record_token_usage"):
            return await correct_transcript(text, model="glm")

    async def test_empty_input(self):
        text, in_t, out_t, stats = await self._run("", [])
        assert text == ""
        assert stats['total_chunks'] == 0

    async def test_whitespace_only(self):
        text, in_t, out_t, stats = await self._run("   \n  ", [])
        assert stats['total_chunks'] == 0

    async def test_success_preserves_markers(self):
        # 校正仅改错字，标记数量不变 → 通过完整性校验
        orig = "[00:05] 终南山说道\n[00:10] 第二句"
        corrected = "[00:05] 钟南山说道\n[00:10] 第二句"
        text, _, _, stats = await self._run(orig, [(corrected, 10, 10)])
        assert text == corrected
        assert stats['failed_chunks'] == 0
        assert stats['integrity_warnings'] == 0
        assert stats['total_chunks'] == 1

    async def test_failure_falls_back_to_original(self):
        orig = "[00:05] 文本内容"
        # _call_ai 返回失败哨兵
        text, _, _, stats = await self._run(orig, [("[处理失败: 超时]", 5, 0)])
        assert text == orig  # 回退原文
        assert stats['failed_chunks'] == 1
        assert len(stats['warnings']) >= 1

    async def test_marker_loss_falls_back(self):
        # 校正后丢了一个时间戳 → 完整性告警 + 回退原文
        orig = "[00:05] 第一句\n[00:10] 第二句"
        lost = "[00:05] 第一句\n第二句"
        text, _, _, stats = await self._run(orig, [(lost, 10, 8)])
        assert text == orig  # 回退
        assert stats['integrity_warnings'] == 1
        assert any('完整性' in w for w in stats['warnings'])

    async def test_length_anomaly_warns_but_keeps(self):
        # 标记完整但长度暴增 >30% → 告警但不回退
        orig = "短"
        long_out = "这是非常非常非常非常非常非常非常非常非常非常非常长的校正结果"
        text, _, _, stats = await self._run(orig, [(long_out, 5, 50)])
        assert text == long_out  # 不回退
        assert stats['length_warnings'] == 1
        assert stats['integrity_warnings'] == 0  # 无标记不触发完整性

    async def test_multi_chunk(self):
        # 长文本分两批，两批都成功
        chunk1 = "[00:05] " + "甲" * 20
        chunk2 = "[00:10] " + "乙" * 20
        text = chunk1 + "\n" + chunk2
        # 用小 max_chars 强制分块——通过 monkeypatch
        with patch("services.ai_processor.get_model_by_key", return_value=self.mock_cfg), \
             patch("services.ai_processor._call_ai", side_effect=[
                 (chunk1, 10, 10), (chunk2, 10, 10)
             ]), \
             patch("services.ai_processor._record_token_usage"):
            # 临时把 max_chars 调小：直接 patch _split_by_boundary 返回值
            with patch("services.ai_processor._split_by_boundary",
                       return_value=[chunk1, chunk2]):
                out, _, _, stats = await correct_transcript(text, model="glm")
        assert stats['total_chunks'] == 2
        assert stats['failed_chunks'] == 0
        assert chunk1 in out and chunk2 in out
