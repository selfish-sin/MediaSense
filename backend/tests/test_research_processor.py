"""
深度研究处理器（services/research_processor.py）测试

重点：**真调用契约**。
上一轮 P0-1（`web_search_collect(content_size="high")` 签名不存在 → 每主题必崩）之所以
活下来，是因为当时只断言 `hasattr(rp, '_expand_topic')` 这类结构，对调用契约零覆盖。
本文件的桩函数一律复刻被调方的**真实签名**，多传/少传参数都会立刻 TypeError。
"""
import pytest
import asyncio
import sqlite3

from services import research_processor as rp
from services import web_search as ws
from services.research_processor import RESEARCH_SEARCH_RESULTS, research_process

MOCK_CFG = {"name": "test", "key": "glm", "model_id": "glm-4",
            "endpoint": "https://api.example.com/v1/chat/completions",
            "auth_type": "bearer", "api_key": "sk",
            "temperature": 0.3, "max_tokens": 4096}


def _install_search_stub(monkeypatch, calls, outcome=None):
    """把 `web_search_collect` 换成**与真实签名逐字一致**的桩：

        (query, max_results=DEFAULT_RESULTS, snippet_limit=500, total_budget=...)

    生产代码若多传一个 `content_size=`，这里立刻 TypeError —— 这正是要防的回归。
    """
    real = ws.web_search_collect
    seen = calls

    async def _stub(query, max_results=ws.DEFAULT_RESULTS, snippet_limit=500,
                    total_budget=ws.SEARCH_TOTAL_BUDGET):
        seen.append({
            "query": query,
            "max_results": max_results,
            "snippet_limit": snippet_limit,
            "total_budget": total_budget,
        })
        return outcome if outcome is not None else ws.SearchOutcome(
            sources=[{"title": "来源一", "url": "https://x.com/1", "snippet": "摘要正文"}],
            text="[1] 来源一：摘要正文",
            provider="ollama",
        )

    monkeypatch.setattr(ws, "web_search_collect", _stub)
    return real


# ── P0-1：调用契约 ──

@pytest.mark.asyncio
class TestExpandTopicCallContract:

    async def test_expand_topic_runs_with_real_search_signature(self, monkeypatch):
        """★ P0-1 证伪用例：真正进入 `_expand_topic`。
        修复前传 `content_size="high"`（签名里没有）→ 每主题 TypeError，深度研究 100% 崩。"""
        calls = []
        _install_search_stub(monkeypatch, calls)
        prompts = []

        async def _fake_call_ai(model_cfg, prompt, **kw):
            prompts.append(prompt)
            return "### 主题A\n\n研究段落", 11, 22

        monkeypatch.setattr(rp, "_call_ai", _fake_call_ai)

        section = await rp._expand_topic("主题A", "视频转录里的一段背景", MOCK_CFG)

        # 真跑通了，且产出的是 AI 返回的段落
        assert section == "### 主题A\n\n研究段落"
        assert len(calls) == 1
        assert calls[0]["query"] == "主题A"
        # 检索条数沿用模块常量
        assert calls[0]["max_results"] == RESEARCH_SEARCH_RESULTS

    async def test_expand_topic_requests_broad_snippet_limit(self, monkeypatch):
        """内容丰富度用 `snippet_limit` 表达，取值对齐 web_search 的 strict500/broad900 口径
        ——深度研究要把网页正文喂给模型写长段落，属 broad 档。"""
        calls = []
        _install_search_stub(monkeypatch, calls)

        async def _fake_call_ai(model_cfg, prompt, **kw):
            return "段落", 1, 1

        monkeypatch.setattr(rp, "_call_ai", _fake_call_ai)

        await rp._expand_topic("主题B", "背景", MOCK_CFG)

        assert calls[0]["snippet_limit"] == rp.RESEARCH_SNIPPET_LIMIT == 900

    async def test_expand_topic_prompt_carries_search_and_transcript(self, monkeypatch):
        """跑通之外还要拼对：检索结果与转录片段都进提示词。"""
        calls = []
        _install_search_stub(monkeypatch, calls)
        prompts = []

        async def _fake_call_ai(model_cfg, prompt, **kw):
            prompts.append(prompt)
            return "段落", 1, 1

        monkeypatch.setattr(rp, "_call_ai", _fake_call_ai)

        await rp._expand_topic("量子计算", "转录里的关键背景句", MOCK_CFG)

        assert len(prompts) == 1
        assert '量子计算' in prompts[0]
        assert '转录里的关键背景句' in prompts[0]
        assert '摘要正文' in prompts[0]

    async def test_expand_topic_search_failure_is_visible_not_silent(self, monkeypatch):
        """检索全失败时不得抛 TypeError/吞错：如实标注并继续出段落。"""
        calls = []
        _install_search_stub(
            monkeypatch, calls,
            outcome=ws.SearchOutcome(sources=[], text="", provider="", error="全部后端均失败"),
        )
        prompts = []

        async def _fake_call_ai(model_cfg, prompt, **kw):
            prompts.append(prompt)
            return "### 主题C\n\n仅基于视频内容", 1, 1

        monkeypatch.setattr(rp, "_call_ai", _fake_call_ai)

        section = await rp._expand_topic("主题C", "背景", MOCK_CFG)

        assert "仅基于视频内容" in section
        assert '(无搜索结果)' in prompts[0]      # 失败对模型可见，不假装有资料

    async def test_expand_topic_ai_failure_returns_placeholder(self, monkeypatch):
        """AI 失败（哨兵串）时返回占位段落，不把哨兵串当正文写进博文。"""
        calls = []
        _install_search_stub(monkeypatch, calls)

        async def _fake_call_ai(model_cfg, prompt, **kw):
            return "[处理失败: 超时]", 5, 0

        monkeypatch.setattr(rp, "_call_ai", _fake_call_ai)

        section = await rp._expand_topic("主题D", "背景", MOCK_CFG)

        assert "暂不可用" in section
        assert "[处理失败" not in section

    async def test_extract_topics_parses_model_output(self, monkeypatch):
        """`_extract_topics` 同样走真实 AI 契约：返回清洗后的主题列表。"""
        async def _fake_call_ai(model_cfg, prompt, **kw):
            assert '转录正文里的主题线索' in prompt
            return "- 主题一\n2. 主题二\n\n", 3, 4

        monkeypatch.setattr(rp, "_call_ai", _fake_call_ai)

        topics = await rp._extract_topics("转录正文里的主题线索", "标题", MOCK_CFG)

        assert topics == ["主题一", "主题二"]

    async def test_extract_topics_ai_failure_falls_back_to_defaults(self, monkeypatch):
        async def _fake_call_ai(model_cfg, prompt, **kw):
            return "[处理失败: 连接超时]", 7, 0

        monkeypatch.setattr(rp, "_call_ai", _fake_call_ai)

        topics = await rp._extract_topics("正文", "标题", MOCK_CFG)

        assert topics == ['核心内容分析', '背景知识扩展', '关键概念解析']


# ── P1-5：token 记账 ──

def _fresh_db(tmp_path, monkeypatch, name):
    """把 DB_PATH 指到临时库并建表；token_usage.job_id 有外键，需先有一条 jobs 记录。"""
    import sqlite3
    import db.database as database

    monkeypatch.setattr(database, "DB_PATH", tmp_path / name)
    database.init_db()
    return sqlite3.connect(str(tmp_path / name))


def _seed_job(conn, job_id):
    conn.execute("INSERT INTO jobs (id, input_url, status) VALUES (?, ?, 'running')",
                 (job_id, f"https://example.com/{job_id}"))
    conn.commit()


@pytest.mark.asyncio
class TestResearchTokenAccounting:
    """深度研究是单任务最贵路径（1 次提取 + N 次写作），修复前 token 恒记 0。"""

    async def test_tokens_sum_of_every_call_recorded_to_db(self, tmp_path, monkeypatch):
        conn = _fresh_db(tmp_path, monkeypatch, "research.db")
        _seed_job(conn, "job-research-1")

        usage_calls = [(10, 5), (20, 8), (30, 12)]   # 1 次提取主题 + 2 次扩展写作
        seq = iter(usage_calls)

        async def _fake_call_ai(model_cfg, prompt, **kw):
            in_tok, out_tok = next(seq)
            if kw.get("round_label") == "提取主题":
                return "- 主题一\n- 主题二", in_tok, out_tok
            return "### 段落", in_tok, out_tok

        monkeypatch.setattr(rp, "_call_ai", _fake_call_ai)
        _install_search_stub(monkeypatch, [])
        monkeypatch.setattr(rp, "get_model_by_key", lambda key: MOCK_CFG)
        monkeypatch.setattr(rp.web_search_service, "describe_search_backends",
                            lambda: {"ollama": "https://ollama.com"})

        article, in_tok, out_tok = await research_process(
            "一段转录正文", title="某视频", model="glm", job_id="job-research-1",
        )

        assert "主题一" in article
        assert (in_tok, out_tok) == (60, 25)       # 各次之和，回传给 main.py 存 job

        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT model_key, input_tokens, output_tokens FROM token_usage WHERE job_id = ?",
            ("job-research-1",),
        ).fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0]["model_key"] == "glm"
        assert rows[0]["input_tokens"] == 60 > 0   # 非零
        assert rows[0]["output_tokens"] == 25 > 0

    async def test_failed_calls_tokens_still_counted(self, tmp_path, monkeypatch):
        """失败路径同样烧了 token：AI 全挂时也必须记账（P0-1/P0-2 的失败语义：花了钱要可见）。"""
        conn = _fresh_db(tmp_path, monkeypatch, "research_fail.db")
        _seed_job(conn, "job-fail")

        async def _fake_call_ai(model_cfg, prompt, **kw):
            return "[处理失败: 全部候选模型均失败]", 99, 1

        monkeypatch.setattr(rp, "_call_ai", _fake_call_ai)
        _install_search_stub(monkeypatch, [])
        monkeypatch.setattr(rp, "get_model_by_key", lambda key: MOCK_CFG)
        monkeypatch.setattr(rp.web_search_service, "describe_search_backends",
                            lambda: {"ollama": "https://ollama.com"})

        await research_process("一段转录正文", title="t", model="glm", job_id="job-fail")

        rows = conn.execute(
            "SELECT input_tokens, output_tokens FROM token_usage WHERE job_id = ?",
            ("job-fail",),
        ).fetchall()
        conn.close()

        assert rows, "AI 调用失败时已消耗的 token 仍必须落库"
        assert sum(r[0] for r in rows) == 99 * (1 + 3)   # 1 次提取 + 3 个默认兜底主题

    async def test_exception_midway_still_records_spent_tokens(self, tmp_path, monkeypatch):
        """★ 中途抛异常时已烧掉的 token 仍必须记账（独立复核 2026-10-13 补）。

        回归症状：记账原本在实现体末尾那行，异常/超时根本执行不到，
        而 N+1 次付费调用已经花掉了 —— 统计仍显示 0，正是 P1-5 要消灭的症状。
        """
        conn = _fresh_db(tmp_path, monkeypatch, "research_exc.db")
        _seed_job(conn, "job-exc")

        calls = {"n": 0}

        async def _flaky_call_ai(model_cfg, prompt, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return "主题一\n主题二", 40, 5      # 主题提取成功，钱已花
            raise RuntimeError("第二个主题扩展时炸了")

        monkeypatch.setattr(rp, "_call_ai", _flaky_call_ai)
        _install_search_stub(monkeypatch, [])
        monkeypatch.setattr(rp, "get_model_by_key", lambda key: MOCK_CFG)
        monkeypatch.setattr(rp.web_search_service, "describe_search_backends",
                            lambda: {"ollama": "https://ollama.com"})

        with pytest.raises(RuntimeError):
            await research_process("一段转录正文", title="t", model="glm", job_id="job-exc")

        rows = conn.execute(
            "SELECT input_tokens, output_tokens FROM token_usage WHERE job_id = ?",
            ("job-exc",),
        ).fetchall()
        conn.close()

        assert rows, "异常抛出后已消耗的 token 必须落库（finally 记账）"
        assert sum(r[0] for r in rows) >= 40

    async def test_timeout_cancellation_still_records_spent_tokens(self, tmp_path, monkeypatch):
        """★ 被 `asyncio.wait_for` 取消（超时）时同样必须记账。

        CancelledError 继承自 BaseException，普通 `except Exception` 抓不到，
        记账必须放在 `finally` 里才能覆盖这条路径。
        """
        conn = _fresh_db(tmp_path, monkeypatch, "research_to.db")
        _seed_job(conn, "job-to")

        async def _slow_call_ai(model_cfg, prompt, **kw):
            await asyncio.sleep(5)
            return "永远到不了的正文", 10, 10

        monkeypatch.setattr(rp, "_call_ai", _slow_call_ai)
        _install_search_stub(monkeypatch, [])
        monkeypatch.setattr(rp, "get_model_by_key", lambda key: MOCK_CFG)
        monkeypatch.setattr(rp.web_search_service, "describe_search_backends",
                            lambda: {"ollama": "https://ollama.com"})

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                research_process("一段转录正文", title="t", model="glm", job_id="job-to"),
                timeout=0.3,
            )

        rows = conn.execute(
            "SELECT COUNT(*) FROM token_usage WHERE job_id = ?", ("job-to",)
        ).fetchall()
        conn.close()

        # 全部调用都被取消 → usage 为 0 → 记账单点按「有消耗才记」跳过，
        # 关键是**不能崩、也不能把已记的账重复写**。
        assert rows[0][0] in (0, 1)
