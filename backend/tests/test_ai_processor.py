"""
ai_processor 核心AI处理服务测试（可快速覆盖部分）
"""
import asyncio

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import httpx
from services.ai_processor import (
    TaskCancelledError,
    _check_cancelled,
    _build_headers,
    _record_token_usage,
    _call_ai,
    generate_tags,
    generate_mindmap,
    correct_transcript,
    correct_transcript_with_fallback,
    _CORRECTION_PROMPTS,
    _count_markers,
    _split_by_boundary,
)

# 测试用统一模型配置（等价于 models 页面里一个 OpenAI 兼容模型）
MOCK_CFG = {"name": "test", "key": "glm", "model_id": "glm-4",
            "endpoint": "https://api.example.com/v1/chat/completions",
            "auth_type": "bearer", "api_key": "sk",
            "temperature": 0.3, "max_tokens": 4096}


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


@pytest.mark.asyncio
class TestGenerateTags:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.mock_cfg = MOCK_CFG

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

    async def test_tags_failure_returns_none(self):
        """★ 契约 A（2026-10-13）：调用失败返回 None（区别于「成功但没标签」的 []）。
        修复前这里返回 []，调用方分不清"打标签挂了"和"确实没有标签"，
        会清空已有标签并当成功返回。"""
        with patch("services.ai_processor.get_model_by_key", return_value=self.mock_cfg):
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post.side_effect = Exception("API error")
            with patch("httpx.AsyncClient", return_value=mock_client):
                tags = await generate_tags("test")
                assert tags is None

    async def test_tags_unparsable_output_returns_empty_list(self):
        """反防：调用成功但模型没吐出合法标签数组 → []（不是 None），
        否则上层会把"解析不出"误判成"调用失败"。"""
        with patch("services.ai_processor.get_model_by_key", return_value=self.mock_cfg):
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": "抱歉，我无法完成"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 4},
            }
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post.return_value = mock_resp
            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch("services.ai_processor._record_token_usage") as rec:
                tags = await generate_tags("test")
                assert tags == []
                # 即便解析不出标签，这次调用真的花了 token，必须记账
                rec.assert_called_once()

    async def test_tags_records_token_usage_with_actual_model(self):
        """★ 回归锚点：打标签的 token 此前**从不记账**（真实账单高于系统显示）。
        记账用**实际使用**的模型 key，与 generate_mindmap 的 _on_ok 同一口径。"""
        with patch("services.ai_processor.get_model_by_key", return_value=self.mock_cfg), \
             patch("services.ai_processor.run_with_fallback") as mock_rwf:
            async def _fake_rwf(model, step_name, timeout, build_payload, **kw):
                return kw['on_success']('["哲学","历史"]', '实际模型k', 33, 21)
            mock_rwf.side_effect = _fake_rwf
            with patch("services.ai_processor._record_token_usage") as rec:
                tags = await generate_tags("some text", job_id="job-t")
        assert tags == ["哲学", "历史"]
        rec.assert_called_once_with('实际模型k', 33, 21, "job-t")

    async def test_tags_cancel_propagates_not_swallowed(self):
        """★ 回归锚点：TaskCancelledError 是 Exception 子类，修复前被 except Exception
        吞成 []（用户取消的任务照样往下跑完）。"""
        with patch("services.ai_processor.get_model_by_key", return_value=self.mock_cfg), \
             patch("services.ai_processor.run_with_fallback") as mock_rwf:
            async def _cancel(*a, **kw):
                raise TaskCancelledError("用户取消")
            mock_rwf.side_effect = _cancel
            with pytest.raises(TaskCancelledError):
                await generate_tags("some text", job_id="job-c")

    async def test_tags_http_error_emits_real_reason(self):
        # 回归：打标签 4xx 时服务端错误文字必须推到前端（socket 日志），不能只进日志文件
        with patch("services.ai_processor.get_model_by_key", return_value=self.mock_cfg):
            # 构造 403 流式响应体（非流式 post 失败时 describe_http_error 要读到它）
            req = httpx.Request("POST", MOCK_CFG["endpoint"])
            resp = httpx.Response(403, request=req,
                                  content='{"error": {"message": "无权访问 gemini高级普通 分组"}}'.encode('utf-8'))
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post.side_effect = httpx.HTTPStatusError(
                "403 Forbidden", request=req, response=resp)
            with patch("httpx.AsyncClient", return_value=mock_client):
                with patch("socket_handlers.emit_log", new=AsyncMock()) as mock_emit:
                    tags = await generate_tags("test", sio=object(), job_id="job-x")
                    assert tags is None            # 调用失败 = None（契约 A）
                    mock_emit.assert_awaited()
                    msgs = [c.args[2] for c in mock_emit.await_args_list]
                    assert any("无权访问 gemini高级普通 分组" in m for m in msgs)
                    assert all("Attempted to access streaming" not in m for m in msgs)


@pytest.mark.asyncio
class TestGenerateMindmap:
    async def test_mindmap_success(self):
        with patch("services.ai_processor.get_model_by_key", return_value=MOCK_CFG):
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
        with patch("services.ai_processor.get_model_by_key", return_value=MOCK_CFG):
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
    mock_cfg = MOCK_CFG

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

    async def test_changed_lines_captured(self):
        """逐句改动明细：只记录内容变化的行，含时间戳与前后文本。"""
        orig = "[00:05] 终南山说道\n[00:10] 第二句不变"
        corrected = "[00:05] 钟南山说道\n[00:10] 第二句不变"
        _, _, _, stats = await self._run(orig, [(corrected, 10, 10)])
        assert stats['changed_count'] == 1
        assert stats['changed_lines'] == [
            {'ts': '[00:05]', 'before': '终南山说道', 'after': '钟南山说道'}
        ]
        assert stats['changed_truncated'] is False

    async def test_zero_changes_reports_none(self):
        """原文无错 → 校对零改动（好文本不被乱改的直接证据）。"""
        orig = "[00:05] 完全正确的一句\n[00:10] 第二句"
        _, _, _, stats = await self._run(orig, [(orig, 10, 10)])
        assert stats['changed_count'] == 0
        assert stats['changed_lines'] == []

    async def test_trailing_newline_diff_still_captured(self):
        """回归：校正模型输出无尾随换行而原文有 → 空行不参与对齐，改动不漏报。"""
        orig = "[00:05] 银马尔可夫\n[00:10] 第二句\n"
        corrected = "[00:05] 隐马尔可夫\n[00:10] 第二句"
        _, _, _, stats = await self._run(orig, [(corrected, 10, 10)])
        assert stats['changed_count'] == 1
        assert stats['changed_lines'][0]['before'] == '银马尔可夫'
        assert stats['changed_lines'][0]['after'] == '隐马尔可夫'

    async def test_reverted_batch_not_counted_as_change(self):
        """标记丢失回退原文的批次不进改动明细（由完整性告警负责）。"""
        orig = "[00:05] 第一句\n[00:10] 第二句"
        lost = "[00:05] 第一句\n第二句"
        _, _, _, stats = await self._run(orig, [(lost, 10, 8)])
        assert stats['changed_count'] == 0
        assert stats['integrity_warnings'] == 1

    async def test_changed_lines_capped_at_100(self):
        """改动超过 100 条截断存储并打标，防 stats JSON 膨胀。"""
        orig = '\n'.join(f'[{i // 60:02d}:{i % 60:02d}] 第{i}句原文内容' for i in range(120))
        corrected = '\n'.join(f'[{i // 60:02d}:{i % 60:02d}] 第{i}句被改过的内容' for i in range(120))
        _, _, _, stats = await self._run(orig, [(corrected, 10, 10)])
        assert stats['changed_count'] == 120
        assert len(stats['changed_lines']) == 100
        assert stats['changed_truncated'] is True

    async def test_domain_prompt_selection(self):
        """领域模板选择：tcm 用中医专项规则，未知领域回退 default。"""
        captured = []

        async def fake_call_ai(cfg, prompt, **kw):
            captured.append(prompt)
            return "[00:05] 文本", 1, 1

        with patch("services.ai_processor.get_model_by_key", return_value=self.mock_cfg), \
             patch("services.ai_processor._call_ai", side_effect=fake_call_ai), \
             patch("services.ai_processor._record_token_usage"):
            await correct_transcript("[00:05] 文本", model="glm", domain='tcm')
            await correct_transcript("[00:05] 文本", model="glm", domain='unknown-xyz')
            await correct_transcript("[00:05] 文本", model="glm", domain='default')
        assert '中医专项规则' in captured[0]
        assert '中医专项规则' not in captured[1]  # 未知领域回退通用
        assert '中医专项规则' not in captured[2]
        assert '直接输出校正后的纯文本' in captured[1]  # 通用骨架仍在

    async def test_default_prompt_restraint_rules(self):
        """默认模板必须带克制原则：只改转写错/拿不准保留/不整句重写（防语义级替换回归）。"""
        assert '只改"转写错"' in _CORRECTION_PROMPTS['default']
        assert '拿不准' in _CORRECTION_PROMPTS['default']
        assert '不整句重写' in _CORRECTION_PROMPTS['default']
        # tcm 模板同样必须保留克制原则
        assert '只改"转写错"' in _CORRECTION_PROMPTS['tcm']

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


# ── correct_transcript_with_fallback 多模型兜底 ──
@pytest.mark.asyncio
class TestCorrectTranscriptFallback:
    """所选校正模型不可用（异常/整批无效）时自动换下一个候选；
    部分批次失败不算不可用；用户取消绝不重试。"""

    async def _run(self, text, behaviors, model="glm-5-2",
                   fallback_models=("deepseek-v4-flash", "longcat-2-0"),
                   calls=None, **kw):
        """behaviors: dict model -> 行为。值为 (content, in, out) 元组 = 成功返回；
        Exception 实例 = 抛出；'all_dead' = 模拟整批哨兵失败；
        'partial_dead' = 两批一批失败一批成功。
        calls: 可选外部列表，记录实际尝试的模型顺序。"""
        calls = calls if calls is not None else []

        async def fake_correct(text, model="", sio=None, job_id='', domain='default', **kw2):
            calls.append(model)
            b = behaviors[model]
            if isinstance(b, Exception):
                raise b
            if b == 'all_dead':
                return text, 1, 1, {'total_chunks': 1, 'failed_chunks': 1,
                                    'integrity_warnings': 0, 'length_warnings': 0, 'warnings': []}
            if b == 'all_integrity_dead':
                return text, 1, 1, {'total_chunks': 2, 'failed_chunks': 0,
                                    'integrity_warnings': 2, 'length_warnings': 0, 'warnings': []}
            if b == 'partial_dead':
                return text, 1, 1, {'total_chunks': 2, 'failed_chunks': 1,
                                    'integrity_warnings': 0, 'length_warnings': 0, 'warnings': ['x']}
            return (b[0], b[1], b[2],
                    {'total_chunks': 1, 'failed_chunks': 0, 'integrity_warnings': 0,
                     'length_warnings': 0, 'warnings': []})

        with patch("services.ai_processor.correct_transcript", side_effect=fake_correct):
            out = await correct_transcript_with_fallback(
                text, model=model, fallback_models=list(fallback_models), **kw)
        return out, calls

    async def test_first_model_success_no_fallback(self):
        (text, _, _, stats, used), calls = await self._run(
            "[00:05] 文本", {"glm-5-2": ("[00:05] 校正后", 3, 3)})
        assert used == "glm-5-2"
        assert calls == ["glm-5-2"]
        assert text == "[00:05] 校正后"

    async def test_exception_switches_to_next_model(self):
        (text, _, _, _, used), calls = await self._run(
            "[00:05] 文本",
            {"glm-5-2": Exception("401 无效密钥"),
             "deepseek-v4-flash": ("[00:05] 备选校正", 3, 3)})
        assert used == "deepseek-v4-flash"
        assert calls == ["glm-5-2", "deepseek-v4-flash"]
        assert text == "[00:05] 备选校正"

    async def test_all_chunks_dead_switches_model(self):
        (text, _, _, stats, used), calls = await self._run(
            "[00:05] 文本",
            {"glm-5-2": "all_dead",
             "deepseek-v4-flash": ("[00:05] 备选校正", 3, 3)})
        assert used == "deepseek-v4-flash"
        assert stats['failed_chunks'] == 0

    async def test_all_integrity_dead_switches_model(self):
        """整批完整性异常（模型老丢时间戳）同样视为不可用。"""
        (_, _, _, _, used), calls = await self._run(
            "[00:05] 文本",
            {"glm-5-2": "all_integrity_dead",
             "deepseek-v4-flash": ("[00:05] 备选校正", 3, 3)})
        assert used == "deepseek-v4-flash"
        assert calls == ["glm-5-2", "deepseek-v4-flash"]

    async def test_partial_failure_keeps_model(self):
        """部分批次失败：结果仍可用（失败批已回退原文），不换模型。"""
        (_, _, _, stats, used), calls = await self._run(
            "[00:05] 文本", {"glm-5-2": "partial_dead"})
        assert used == "glm-5-2"
        assert calls == ["glm-5-2"]
        assert stats['failed_chunks'] == 1

    async def test_cancel_propagates_without_retry(self):
        """用户取消直接上抛，不换模型重试。"""
        with pytest.raises(TaskCancelledError):
            await self._run("[00:05] 文本", {"glm-5-2": TaskCancelledError("用户取消")})

    async def test_all_candidates_fail_raises(self):
        with pytest.raises(Exception) as ei:
            await self._run(
                "[00:05] 文本",
                {"glm-5-2": Exception("x"), "deepseek-v4-flash": Exception("y"),
                 "longcat-2-0": Exception("z")})
        assert "glm-5-2" in str(ei.value) and "longcat-2-0" in str(ei.value)

    async def test_max_fallbacks_caps_candidates(self):
        """max_fallbacks 限制备选数量，超出部分不尝试。"""
        tried = []
        with pytest.raises(Exception) as ei:
            await self._run(
                "[00:05] 文本",
                {k: Exception(k) for k in ("glm-5-2", "deepseek-v4-flash", "longcat-2-0")},
                calls=tried, max_fallbacks=1)
        assert tried == ["glm-5-2", "deepseek-v4-flash"]
        assert "longcat-2-0" not in str(ei.value)


# ── 流式 chunk 容错（回归：choices 空数组导致 list index out of range）──
@pytest.mark.asyncio
class TestCallAiEmptyChoicesChunk:
    """部分 OpenAI 兼容接口（如阿里云 MaaS）在流式末尾发送 choices=[] 的 usage chunk，
    旧代码 chunk.get('choices', [{}])[0] 会越界，导致已生成的内容全部丢失并返回失败哨兵"""

    def _mock_cfg(self):
        return MOCK_CFG

    async def test_usage_chunk_with_empty_choices_does_not_crash(self):
        async def fake_aiter_lines():
            yield 'data: {"choices": [{"delta": {"content": "你好"}}]}'
            yield 'data: {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}'
            yield 'data: [DONE]'

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.aiter_lines = fake_aiter_lines
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.stream = MagicMock(return_value=stream_cm)
        with patch("httpx.AsyncClient", return_value=mock_client):
            content, in_tok, out_tok = await _call_ai(self._mock_cfg(), "测试提示词")
        # 内容完整保留，usage 被读到，而不是抛 list index out of range
        assert content == "你好"
        assert in_tok == 3
        assert out_tok == 2

    async def test_first_chunk_empty_choices_keeps_streaming(self):
        async def fake_aiter_lines():
            yield 'data: {"choices": []}'
            yield 'data: {"choices": [{"delta": {"content": "后续内容"}}]}'
            yield 'data: [DONE]'

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.aiter_lines = fake_aiter_lines
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.stream = MagicMock(return_value=stream_cm)
        with patch("httpx.AsyncClient", return_value=mock_client):
            content, _, _ = await _call_ai(self._mock_cfg(), "测试提示词")
        assert content == "后续内容"


# ── 流式 4xx/5xx 错误透出真实原因（回归：403 被 ResponseNotRead 掩盖）──
@pytest.mark.asyncio
class TestCallAiStreamHttpError:
    """流式请求返回 4xx/5xx 时，错误处理必须先读完整 body 再访问 response.text，
    否则 httpx 抛 ResponseNotRead（"Attempted to access streaming response content,
    without having called read()"），把服务端真实原因（如无权访问分组）掩盖掉"""

    def _mock_cfg(self):
        return MOCK_CFG

    async def test_http_error_exposes_real_reason(self):
        # 用真实 httpx.Response（流式 body）模拟 403，未读时访问 .text 会抛 ResponseNotRead
        body = '{"error": {"message": "无权访问 gemini高级普通 分组"}}'.encode('utf-8')
        stream_resp = httpx.Response(
            403,
            request=httpx.Request("POST", MOCK_CFG["endpoint"]),
            stream=httpx.ByteStream(body),
        )
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_resp)
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.stream = MagicMock(return_value=stream_cm)
        with patch("httpx.AsyncClient", return_value=mock_client):
            content, _, _ = await _call_ai(self._mock_cfg(), "测试提示词")
        # 必须透出服务端真实原因，而不是 httpx 的 ResponseNotRead 流错误
        assert "无权访问 gemini高级普通 分组" in content
        assert "Attempted to access streaming response content" not in content


@pytest.mark.asyncio
class TestCallAiStreamDisconnect:
    @pytest.mark.parametrize('with_state', [True, False])
    @pytest.mark.parametrize('has_content', [True, False])
    async def test_disconnect_retries_only_before_content(self, monkeypatch, with_state, has_content):
        """跑真实流读取：已推送正文不能重试，无正文断连仍可重试。"""
        from services import ai_processor as ap
        import socket_handlers

        requests = []
        emitted = AsyncMock()
        monkeypatch.setattr(socket_handlers, 'emit_ai_token', emitted)
        monkeypatch.setattr(socket_handlers, 'emit_log', AsyncMock())
        monkeypatch.setattr(ap.asyncio, 'sleep', AsyncMock())

        class BrokenStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                if has_content:
                    for _ in range(10):
                        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
                raise httpx.ReadError('upstream disconnected')

        def respond(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(200, stream=BrokenStream())
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')

        original_client = httpx.AsyncClient
        monkeypatch.setattr(ap.httpx, 'AsyncClient',
                            lambda **kw: original_client(transport=httpx.MockTransport(respond), **kw))
        state = {} if with_state else None
        content, _, _ = await ap._call_ai(MOCK_CFG, '断连回归', sio=object(),
                                         max_retries=1, state=state)
        if has_content:
            assert content == '[处理失败: 连接中断（已输出部分内容）]'
            assert len(requests) == 1
            assert emitted.await_count == 1
            assert emitted.await_args.args[2] == 'partial' * 10
        else:
            assert content == 'ok'
            assert len(requests) == 2
        if with_state:
            assert state['emitted'] is True


# ── 思考期取消（2026-10-06 排查：d5615485 取消后打标签仍烧了一次 API）─────────
# 思考型模型可能连续几分钟只吐 reasoning_content——取消检查不能只挂在正文 token 上。
@pytest.mark.asyncio
class TestCallAiThinkingPhaseCancel:

    def _mock_cfg(self):
        return MOCK_CFG

    async def test_cancel_during_reasoning_only_stream(self, monkeypatch):
        """纯 reasoning 流（零正文 token）也必须能被取消：第 20 片 reasoning 触发检查。"""
        import socket_handlers
        from services.ai_processor import TaskCancelledError

        monkeypatch.setattr(socket_handlers, 'check_cancelled', lambda job_id: True)

        async def fake_aiter_lines():
            for i in range(25):
                yield f'data: {{"choices": [{{"delta": {{"reasoning_content": "思{i}"}}}}]}}'
            yield 'data: {"choices": [{"delta": {"content": "正文"}}]}'
            yield 'data: [DONE]'

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.aiter_lines = fake_aiter_lines
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.stream = MagicMock(return_value=stream_cm)
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(TaskCancelledError):
                await _call_ai(self._mock_cfg(), "提示词", job_id="j1")

    async def test_no_cancel_when_not_cancelled(self, monkeypatch):
        """反防：未取消时纯 reasoning 流正常走完（不因检查本身而中断）。"""
        import socket_handlers
        monkeypatch.setattr(socket_handlers, 'check_cancelled', lambda job_id: False)

        async def fake_aiter_lines():
            for i in range(25):
                yield f'data: {{"choices": [{{"delta": {{"reasoning_content": "思{i}"}}}}]}}'
            yield 'data: {"choices": [{"delta": {"content": "正文"}}]}'
            yield 'data: [DONE]'

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.aiter_lines = fake_aiter_lines
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.stream = MagicMock(return_value=stream_cm)
        with patch("httpx.AsyncClient", return_value=mock_client):
            content, _, _ = await _call_ai(self._mock_cfg(), "提示词", job_id="j1")
        assert content == "正文"


# ── A4：失败路径的 token 不能丢（2026-10-13）──────────────────────────────
# 修复前 _call_ai 的所有失败分支恒 `return '[处理失败: ...]', 0, 0`，而 _stream()
# 在断流/超时前已经累计的 token 随协程一起丢弃 → token_usage 表系统性少记，
# 真实账单高于系统显示。
@pytest.mark.asyncio
class TestCallAiFailureTokenAccounting:

    @staticmethod
    def _patch_transport(monkeypatch, respond):
        from services import ai_processor as ap
        original_client = httpx.AsyncClient
        monkeypatch.setattr(ap.httpx, 'AsyncClient',
                            lambda **kw: original_client(transport=httpx.MockTransport(respond), **kw))

    async def test_disconnect_after_content_returns_real_tokens(self, monkeypatch):
        """已吐正文后断流：失败串照旧，但 token 必须是真实消耗（缺 usage 时按字符粗估）。"""
        import socket_handlers
        monkeypatch.setattr(socket_handlers, 'emit_ai_token', AsyncMock())
        monkeypatch.setattr(socket_handlers, 'emit_log', AsyncMock())

        class BrokenStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                for _ in range(10):
                    yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
                raise httpx.ReadError('upstream disconnected')

        self._patch_transport(
            monkeypatch, lambda request: httpx.Response(200, stream=BrokenStream()))

        state = {}
        usage_state = {}
        content, in_tok, out_tok = await _call_ai(
            MOCK_CFG, '失败也记账', sio=object(), state=state, usage_state=usage_state)

        assert content == '[处理失败: 连接中断（已输出部分内容）]'
        # 70 字符正文 → 与成功路径同一口径的粗估；关键是不能是 0
        assert out_tok == 70 // 2
        assert usage_state['out_tok'] == 70 // 2

    async def test_usage_state_accumulates_across_retries(self, monkeypatch):
        """断连重试会重跑整段：前一次已上报的 usage 必须计入最终返回值。"""
        from services import ai_processor as ap
        monkeypatch.setattr(ap.asyncio, 'sleep', AsyncMock())

        class UsageThenDrop(httpx.AsyncByteStream):
            async def __aiter__(self):
                # 只有 usage、没有正文 → emitted 仍为 False，允许安全重试
                yield b'data: {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}\n\n'
                raise httpx.ReadError('upstream disconnected')

        calls = {'n': 0}

        def respond(request):
            calls['n'] += 1
            if calls['n'] == 1:
                return httpx.Response(200, stream=UsageThenDrop())
            return httpx.Response(200, text=(
                'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                'data: {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 5}}\n\n'
                'data: [DONE]\n\n'))

        self._patch_transport(monkeypatch, respond)

        usage_state = {}
        content, in_tok, out_tok = await _call_ai(
            MOCK_CFG, '重试也记账', max_retries=1, usage_state=usage_state)

        assert content == 'ok'
        assert calls['n'] == 2
        assert (in_tok, out_tok) == (18, 8)      # 7+11, 3+5

    async def test_timeout_branch_returns_accumulated_tokens(self, monkeypatch):
        """单轮超时时已收到的 usage 照实返回，不再恒 0。"""
        class HangStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"choices": [], "usage": {"prompt_tokens": 42, "completion_tokens": 9}}\n\n'
                await asyncio.sleep(5)

        self._patch_transport(
            monkeypatch, lambda request: httpx.Response(200, stream=HangStream()))

        usage_state = {}
        content, in_tok, out_tok = await _call_ai(
            MOCK_CFG, '超时也记账', round_timeout=0.05, usage_state=usage_state)

        assert content == '[处理失败: 超时]'
        assert (in_tok, out_tok) == (42, 9)


# ── A5：半死连接下取消仍要生效（2026-10-13）───────────────────────────────
@pytest.mark.asyncio
class TestCallAiSilentStreamCancel:
    """端点连上却不吐字时零 token，取消检查挂在 token 计数上永不触发——
    用户点了取消要等满 4 次重试（分钟级）才响应。修复：按**时间**轮询。"""

    def _mock_cfg(self):
        return MOCK_CFG

    async def test_cancel_by_time_poll_in_silent_stream(self, monkeypatch):
        import socket_handlers
        from services import ai_processor as ap

        monkeypatch.setattr(socket_handlers, 'check_cancelled', lambda job_id: True)
        monkeypatch.setattr(ap, 'CANCEL_POLL_INTERVAL', 0.01)

        async def fake_aiter_lines():
            # 只吐空 keep-alive 行：没有正文/reasoning token，靠 token 计数永不触发检查
            for _ in range(6):
                await asyncio.sleep(0.02)
                yield 'data: {"choices": []}'

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.aiter_lines = fake_aiter_lines
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.stream = MagicMock(return_value=stream_cm)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(TaskCancelledError):
                await _call_ai(self._mock_cfg(), "静默流", job_id="j-silent")

    async def test_cancel_checked_before_first_attempt(self, monkeypatch):
        """整轮开始前（含每次重试前）查取消：不查就已经在烧 API 了。"""
        import socket_handlers
        from services import ai_processor as ap
        monkeypatch.setattr(socket_handlers, 'check_cancelled', lambda job_id: True)

        def _boom(**kw):
            raise AssertionError("已取消时不该发起任何 HTTP 请求")

        monkeypatch.setattr(ap.httpx, 'AsyncClient', _boom)

        with pytest.raises(TaskCancelledError):
            await _call_ai(self._mock_cfg(), "取消优先", job_id="j-pre")


# ── A5：校正路径的整体超时 + 跨候选 token（2026-10-13）────────────────────
@pytest.mark.asyncio
class TestCorrectPathTimeoutShell:

    async def test_overall_timeout_raises_actionable(self, monkeypatch):
        """长转录按块累加可以挂数小时 → 必须有与 ai_process 同级的整体超时外壳。"""
        from services import ai_processor as ap

        async def _slow(text, **kw):
            await asyncio.sleep(5)
            return text, 1, 1, {}

        monkeypatch.setattr(ap, 'correct_transcript', _slow)

        with pytest.raises(Exception) as ei:
            await correct_transcript_with_fallback(
                "长文本", model="glm-5-2", fallback_models=["m2"], total_timeout=0.05)
        msg = str(ei.value)
        assert '整体超时' in msg and '拆小分批校正' in msg   # 可行动，不裸 TimeoutError

    async def test_round_timeout_forwarded(self, monkeypatch):
        """硬编码的单轮 600s 提到参数层，并透传到 correct_transcript。"""
        from services import ai_processor as ap
        seen = {}

        async def _fake(text, model="", sio=None, job_id='', domain='default', **kw):
            seen.update(kw)
            return text, 1, 1, {'total_chunks': 1, 'failed_chunks': 0,
                                'integrity_warnings': 0, 'length_warnings': 0, 'warnings': []}

        monkeypatch.setattr(ap, 'correct_transcript', _fake)

        await correct_transcript_with_fallback(
            "文本", model="glm-5-2", fallback_models=[], round_timeout=123.0)

        assert seen['round_timeout'] == 123.0

    async def test_tokens_accumulate_across_candidates(self, monkeypatch):
        """换候选重跑是真的又烧了一次：失败候选的 token 也要累计进返回值。"""
        from services import ai_processor as ap

        async def _fake(text, model="", **kw):
            if model == "glm-5-2":
                # 整批无效 → 视为该模型不可用，换下一个
                return text, 7, 7, {'total_chunks': 2, 'failed_chunks': 2,
                                    'integrity_warnings': 0, 'length_warnings': 0, 'warnings': []}
            return "[00:05] 校正后", 3, 4, {'total_chunks': 1, 'failed_chunks': 0,
                                            'integrity_warnings': 0, 'length_warnings': 0, 'warnings': []}

        monkeypatch.setattr(ap, 'correct_transcript', _fake)

        _, in_tok, out_tok, _, used = await correct_transcript_with_fallback(
            "文本", model="glm-5-2", fallback_models=["m2"])

        assert used == "m2"
        assert (in_tok, out_tok) == (10, 11)      # 7+3, 7+4


# ── A1/A3：分批总结的 fail-fast 与 edited_prompt（2026-10-13）────────────
# 修复前分批模式下任一批失败都会把 '[处理失败: ...]' 哨兵串拼进合并正文，
# 前面还加 '# 分批处理结果' 标题 → 两处调用方的 startswith('[处理失败') 防线
# 在分批模式下永远不成立 → 任务标 completed、残缺文档写进 jobs.result_text。
@pytest.mark.asyncio
class TestAiProcessBatchFailFast:

    # 2 万字符/批（word_count=500 映射表），要 2 批就得超过它
    LONG = '甲' * 25000

    async def test_second_batch_failure_raises_with_batch_index(self):
        """★ 契约 C：分批中任一批失败 → 在合并/落库/记账前抛异常，并指名第几批。"""
        calls = {'n': 0}

        async def _fake(model, prompt, **kw):
            calls['n'] += 1
            if calls['n'] == 1:
                return "## 第一批内容", 10, 5, 'glm'
            return "[处理失败: 兜底环内全部模型失败（glm）——401 密钥无效]", 20, 7, 'glm'

        from services import ai_processor as ap
        with patch("services.ai_processor.get_model_by_key", return_value=MOCK_CFG), \
             patch("services.ai_processor._call_ai_with_fallback", side_effect=_fake), \
             patch("services.ai_processor._record_token_usage") as rec, \
             patch("socket_handlers.emit_ai_token", new=AsyncMock()) as emit_tok, \
             patch("socket_handlers.emit_log", new=AsyncMock()), \
             patch("socket_handlers.emit_progress", new=AsyncMock()):
            with pytest.raises(Exception) as ei:
                await ap.ai_process(self.LONG, dimensions={'word_count': '500'})

        msg = str(ei.value)
        assert '第 2/2 批' in msg                     # 批次序号 + 总批数
        assert '401 密钥无效' in msg                   # 哨兵里的真实原因
        assert '换用可用模型' in msg                   # 可行动建议
        # 失败状态不能被推成"AI 完成"（emit_ai_token(done=True)）
        assert not any(c.kwargs.get('done') for c in emit_tok.await_args_list)
        # 但已消耗的 token 仍要记账（且是累计值）
        rec.assert_called_once()
        assert rec.call_args.args[1:3] == (30, 12)

    async def test_call_chain_persisted_on_failure(self):
        ctx = {}

        async def _fake(model, prompt, **kw):
            kw['call_chain'].append({'model': 'glm', 'ok': False, 'reason': '401'})
            return "[处理失败: 401 密钥无效]", 1, 1, 'glm'

        from services import ai_processor as ap
        with patch("services.ai_processor.get_model_by_key", return_value=MOCK_CFG), \
             patch("services.ai_processor._call_ai_with_fallback", side_effect=_fake), \
             patch("services.ai_processor._record_token_usage"), \
             patch("socket_handlers.emit_ai_token", new=AsyncMock()), \
             patch("socket_handlers.emit_log", new=AsyncMock()), \
             patch("socket_handlers.emit_progress", new=AsyncMock()):
            with pytest.raises(Exception):
                await ap.ai_process(self.LONG, dimensions={'word_count': '500'}, ctx=ctx)
        assert ctx['call_chain'] and ctx['call_chain'][0]['reason'] == '401'

    async def test_single_batch_failure_keeps_sentinel(self):
        """反防：单批模式行为不变——仍返回哨兵串（调用方有防线），不抛异常。"""

        async def _fake(model, prompt, **kw):
            return "[处理失败: 超时]", 3, 0, 'glm'

        from services import ai_processor as ap
        with patch("services.ai_processor.get_model_by_key", return_value=MOCK_CFG), \
             patch("services.ai_processor._call_ai_with_fallback", side_effect=_fake), \
             patch("services.ai_processor._record_token_usage"):
            out, in_tok, out_tok = await ap.ai_process(
                "短文本", dimensions={'word_count': '500'})
        assert out == "[处理失败: 超时]"
        assert (in_tok, out_tok) == (3, 0)

    async def test_edited_prompt_applies_in_batch_mode(self):
        """★ A3 回归锚点：用户编辑过的提示词在长视频（分批）上也必须生效。
        修复前分批复用分支只认 dimensions，把 edited_prompt 整个丢了
        （短视频生效、长视频不生效，行为不一致）。"""
        prompts = []

        async def _fake(model, prompt, **kw):
            prompts.append(prompt)
            return "内容", 1, 1, 'glm'

        from services import ai_processor as ap
        with patch("services.ai_processor.get_model_by_key", return_value=MOCK_CFG), \
             patch("services.ai_processor._call_ai_with_fallback", side_effect=_fake), \
             patch("services.ai_processor._record_token_usage"):
            await ap.ai_process(
                self.LONG, dimensions={'word_count': '500'},
                summary_options={'edited_prompt': '【用户自己写的开头】\n{{CONTENT}}\n【用户自己写的结尾】'})

        assert len(prompts) == 2                       # 确实分了两批
        for i, p in enumerate(prompts, 1):
            assert '【用户自己写的开头】' in p and '【用户自己写的结尾】' in p
            assert f'第 {i}/2 部分' in p                # 每批内容都替换进占位符

    async def test_edited_prompt_without_placeholder_keeps_content(self):
        """编辑版提示词不带 {{CONTENT}} 时按同一注入约定把内容附在末尾，
        而不是让整批内容凭空消失。"""
        prompts = []

        async def _fake(model, prompt, **kw):
            prompts.append(prompt)
            return "内容", 1, 1, 'glm'

        from services import ai_processor as ap
        with patch("services.ai_processor.get_model_by_key", return_value=MOCK_CFG), \
             patch("services.ai_processor._call_ai_with_fallback", side_effect=_fake), \
             patch("services.ai_processor._record_token_usage"):
            await ap.ai_process(
                self.LONG, dimensions={'word_count': '500'},
                summary_options={'edited_prompt': '【用户自己写的提示词】'})

        assert len(prompts) == 2
        assert all('【用户自己写的提示词】' in p and '第 ' in p for p in prompts)


# ── ai_process 正常路径不回归 ──
@pytest.mark.asyncio
class TestAiProcessBatchSuccess:

    async def test_multi_chunk_merges_all_batches(self):
        prompts = []

        async def _fake(model, prompt, **kw):
            prompts.append(prompt)
            return f"第{len(prompts)}批结果", 2, 3, 'glm'

        from services import ai_processor as ap
        with patch("services.ai_processor.get_model_by_key", return_value=MOCK_CFG), \
             patch("services.ai_processor._call_ai_with_fallback", side_effect=_fake), \
             patch("services.ai_processor._record_token_usage"):
            out, in_tok, out_tok = await ap.ai_process(
                '甲' * 25000, dimensions={'word_count': '500'})

        assert '第1批结果' in out and '第2批结果' in out
        assert (in_tok, out_tok) == (4, 6)
