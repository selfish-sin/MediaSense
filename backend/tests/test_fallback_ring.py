"""兜底环（fallback ring）测试 —— 2026-10-05

覆盖三块语义：
1. 环的配置读写（model_repo.get/set_fallback_chain）
2. 候选解析单点 resolve_ai_candidates（环=无主档、去重、每模型只试一次）
3. 换模型的触发边界（_call_ai_with_fallback / run_with_fallback）
   核心不变量：**已向前端吐过字就绝不换模型**（否则产出跨模型混血文档）
"""
import json

import pytest

from db import model_repo


# ---------------------------------------------------------------- 环的配置读写

class TestFallbackChainConfig:
    def test_unconfigured_returns_disabled_empty(self, db_path, monkeypatch):
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(db_path))
        cfg = model_repo.get_fallback_chain()
        assert cfg == {'enabled': False, 'chain': []}

    def test_save_then_read_preserves_order(self, db_path, monkeypatch):
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(db_path))
        model_repo.add_model(key='m1', name='M1', endpoint='https://x/v1/chat/completions', api_key='k')
        model_repo.add_model(key='m2', name='M2', endpoint='https://x/v1/chat/completions', api_key='k')
        model_repo.add_model(key='m3', name='M3', endpoint='https://x/v1/chat/completions', api_key='k')
        assert model_repo.set_fallback_chain(['m3', 'm1', 'm2'], enabled=True) is True
        cfg = model_repo.get_fallback_chain()
        assert cfg['enabled'] is True
        assert cfg['chain'] == ['m3', 'm1', 'm2']   # 顺序即优先级

    def test_no_length_cap(self, db_path, monkeypatch):
        """用户明确要求不设上限：10 个候选必须全部存下。"""
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(db_path))
        keys = [f'm{i}' for i in range(10)]
        for k in keys:
            model_repo.add_model(key=k, name=k, endpoint='https://x/v1/chat/completions', api_key='k')
        model_repo.set_fallback_chain(keys, enabled=True)
        assert model_repo.get_fallback_chain()['chain'] == keys

    def test_dedup_and_drop_nonexistent(self, db_path, monkeypatch):
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(db_path))
        model_repo.add_model(key='m1', name='M1', endpoint='https://x/v1/chat/completions', api_key='k')
        model_repo.set_fallback_chain(['m1', 'm1', '不存在的模型', '', None], enabled=True)
        assert model_repo.get_fallback_chain()['chain'] == ['m1']

    def test_corrupt_json_degrades_to_disabled_not_crash(self, db_path, monkeypatch):
        """存储被手改成非法 JSON：按未配置处理并留下痕迹，不让脏数据打挂整页。"""
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(db_path))
        conn = _conn(db_path)
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (model_repo.FALLBACK_CHAIN_SETTING_KEY, '{not json'),
        )
        conn.commit()
        conn.close()
        assert model_repo.get_fallback_chain() == {'enabled': False, 'chain': []}

    def test_ring_excludes_default_model(self, db_path, monkeypatch):
        """环里不能有当前默认模型：它是首选档，重复占一格等于白浪费一次重试。"""
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(db_path))
        model_repo.add_model(key='m1', name='M1', endpoint='https://x/v1/chat/completions', api_key='k')
        model_repo.add_model(key='m2', name='M2', endpoint='https://x/v1/chat/completions', api_key='k')
        model_repo.set_fallback_chain(['m1', 'm2'], enabled=True)
        model_repo.set_default_model('m1')
        assert model_repo.get_fallback_chain()['chain'] == ['m2']

    def test_setting_default_prunes_ring_immediately(self, db_path, monkeypatch):
        """★ 用户场景：先把 m2 排进环，后来把 m2 设为默认 → 必须自动摘除，不留重复。"""
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(db_path))
        for k in ('m1', 'm2', 'm3'):
            model_repo.add_model(key=k, name=k, endpoint='https://x/v1/chat/completions', api_key='k')
        model_repo.set_default_model('m1')
        model_repo.set_fallback_chain(['m2', 'm3'], enabled=True)
        assert model_repo.get_fallback_chain()['chain'] == ['m2', 'm3']

        model_repo.set_default_model('m2')      # ← 用户改默认
        assert model_repo.get_fallback_chain()['chain'] == ['m3']   # m2 已自动摘除
        assert model_repo.get_default_model_key() == 'm2'

    def test_prune_keeps_relative_order_of_others(self, db_path, monkeypatch):
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(db_path))
        for k in ('m1', 'm2', 'm3', 'm4'):
            model_repo.add_model(key=k, name=k, endpoint='https://x/v1/chat/completions', api_key='k')
        model_repo.set_default_model('m1')
        model_repo.set_fallback_chain(['m2', 'm3', 'm4'], enabled=True)
        model_repo.set_default_model('m3')
        assert model_repo.get_fallback_chain()['chain'] == ['m2', 'm4']  # 其余顺序不变


# ---------------------------------------------------------------- 候选解析单点

class TestResolveAiCandidates:
    def test_disabled_ring_returns_only_selected(self, db_path, monkeypatch):
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(db_path))
        model_repo.add_model(key='m1', name='M1', endpoint='https://x/v1/chat/completions', api_key='k')
        model_repo.add_model(key='m2', name='M2', endpoint='https://x/v1/chat/completions', api_key='k')
        model_repo.set_fallback_chain(['m2'], enabled=False)
        assert model_repo.resolve_ai_candidates('m1') == ['m1']

    def test_selected_model_goes_first(self, db_path, monkeypatch):
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(db_path))
        for k in ('m1', 'm2', 'm3'):
            model_repo.add_model(key=k, name=k, endpoint='https://x/v1/chat/completions', api_key='k')
        model_repo.set_default_model('m1')
        model_repo.set_fallback_chain(['m2', 'm3'], enabled=True)
        # 本次选了 m3（不是环里的首选）→ m3 必须排最前，环里其余按优先级跟在后面
        assert model_repo.resolve_ai_candidates('m3') == ['m3', 'm2']

    def test_no_duplicate_when_selected_already_in_ring(self, db_path, monkeypatch):
        """本次选的模型本来就在环里 → 环内那一格必须跳过，不能让它失败后又被重试一次。"""
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(db_path))
        for k in ('m1', 'm2', 'm3'):
            model_repo.add_model(key=k, name=k, endpoint='https://x/v1/chat/completions', api_key='k')
        model_repo.set_default_model('m1')
        model_repo.set_fallback_chain(['m2', 'm3'], enabled=True)
        cands = model_repo.resolve_ai_candidates('m2')
        assert cands == ['m2', 'm3']
        assert len(cands) == len(set(cands))     # 无重复

    def test_each_candidate_tried_once_never_wraps(self, db_path, monkeypatch):
        """★ 环不回头：候选序列里每个 key 只出现一次，执行时"转到末尾回到开头"不会发生。"""
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(db_path))
        for k in ('m1', 'm2', 'm3'):
            model_repo.add_model(key=k, name=k, endpoint='https://x/v1/chat/completions', api_key='k')
        model_repo.set_default_model('m1')
        model_repo.set_fallback_chain(['m2', 'm3'], enabled=True)
        cands = model_repo.resolve_ai_candidates('m1')
        assert cands == ['m1', 'm2', 'm3']
        assert len(cands) == len(set(cands))


# ---------------------------------------------------------------- 换模型触发边界

class TestCallAiWithFallback:
    """_call_ai_with_fallback：流式路径换模型的边界。"""

    @pytest.mark.asyncio
    async def test_zero_output_failure_switches_to_next(self, monkeypatch):
        from services import ai_processor as ap
        tried = []

        async def fake_call_ai(model_cfg, prompt, **kw):
            tried.append(model_cfg['key'])
            if model_cfg['key'] == 'm1':
                return '[处理失败: HTTP 503: All available accounts exhausted]', 0, 0
            return '完整总结', 5, 7

        monkeypatch.setattr(ap, '_call_ai', fake_call_ai)
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(_tmp_db()))
        _seed_two_models()

        chain = []
        content, i, o, used = await ap._call_ai_with_fallback(
            'm1', 'p', call_chain=chain)
        assert used == 'm2'
        assert content == '完整总结'
        assert tried == ['m1', 'm2']          # 换了一次就成功
        assert [c['ok'] for c in chain] == [False, True]

    @pytest.mark.asyncio
    async def test_emitted_output_never_switches(self, monkeypatch):
        """★ 核心不变量：已吐过字就**不换模型**——换了会在前端造成混血文档。"""
        from services import ai_processor as ap
        tried = []

        async def fake_call_ai(model_cfg, prompt, **kw):
            tried.append(model_cfg['key'])
            # ⚠ 不能写 `kw.get('state') or {}`：空 dict 是 falsy，会另建一个丢写的副本
            kw['state']['emitted'] = True          # 模拟已经推过 token
            return '[处理失败: 连接中断]', 0, 0

        monkeypatch.setattr(ap, '_call_ai', fake_call_ai)
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(_tmp_db()))
        _seed_two_models()

        content, _, _, used = await ap._call_ai_with_fallback('m1', 'p')
        assert used == 'm1'
        assert tried == ['m1']               # 没有尝试 m2
        assert ap.is_call_failed(content)

    @pytest.mark.asyncio
    async def test_all_candidates_fail_keeps_real_reasons(self, monkeypatch):
        """全部失败时，错误串里必须带每个候选的**真实原因**，不能只剩状态码。"""
        from services import ai_processor as ap

        async def fake_call_ai(model_cfg, prompt, **kw):
            reason = ('HTTP 503: All available accounts exhausted'
                      if model_cfg['key'] == 'm1' else 'HTTP 401: 无权访问 分组')
            return f'[处理失败: {reason}]', 0, 0

        monkeypatch.setattr(ap, '_call_ai', fake_call_ai)
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(_tmp_db()))
        _seed_two_models()

        content, _, _, _ = await ap._call_ai_with_fallback('m1', 'p')
        assert ap.is_call_failed(content)
        assert 'All available accounts exhausted' in content
        assert '无权访问' in content

    @pytest.mark.asyncio
    async def test_cancel_propagates_without_switching(self, monkeypatch):
        """用户取消必须上抛，绝不换模型重试。"""
        from services import ai_processor as ap
        tried = []

        async def fake_call_ai(model_cfg, prompt, **kw):
            tried.append(model_cfg['key'])
            raise ap.TaskCancelledError()

        monkeypatch.setattr(ap, '_call_ai', fake_call_ai)
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(_tmp_db()))
        _seed_two_models()

        with pytest.raises(ap.TaskCancelledError):
            await ap._call_ai_with_fallback('m1', 'p')
        assert tried == ['m1']


# ── AI 调用 600s 超时（回归：2026-10-05 用户实报的"前端只转圈不报错"）────
# 背景：用户日志里出现"AI调用超时（单轮总超时 600s）"的任务，前端既不报错也不停转。
#       根因有两层——① _call_ai 的超时分支直接 return 哨兵串（不发重试），
#       但哨兵串必须被上层识别为失败才能进兜底环；② 超时是**零输出**失败，
#       换模型安全（与"已吐字不换"的不变量不冲突）。
#       哨兵串若不匹配 is_call_failed，就会被当成正常总结写进 result_text。
class TestTimeoutTriggersFallback:
    @pytest.mark.asyncio
    async def test_timeout_sentinel_is_recognized_as_failure(self):
        """_call_ai 超时分支返回的哨兵串必须被 is_call_failed 认出来。"""
        from services import ai_processor as ap
        assert ap.is_call_failed('[处理失败: 超时]')
        assert ap.is_call_failed(ap._strip_sentinel('[处理失败: 超时]') and '[处理失败: 超时]')
        # 反向：正常总结绝不能被误判成失败
        assert not ap.is_call_failed('这是一份正常的总结正文')
        assert not ap.is_call_failed('')

    @pytest.mark.asyncio
    async def test_timeout_switches_to_next_model(self, monkeypatch):
        """首发模型 600s 超时 → 换环内下一个模型（超时是零输出，换模型安全）。"""
        from services import ai_processor as ap
        tried = []

        async def fake_call_ai(model_cfg, prompt, **kw):
            tried.append(model_cfg['key'])
            if model_cfg['key'] == 'm1':
                return '[处理失败: 超时]', 0, 0
            return '换模型后的总结', 5, 7

        monkeypatch.setattr(ap, '_call_ai', fake_call_ai)
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(_tmp_db()))
        _seed_two_models()

        chain = []
        content, _, _, used = await ap._call_ai_with_fallback('m1', 'p', call_chain=chain)
        assert used == 'm2'
        assert content == '换模型后的总结'
        assert tried == ['m1', 'm2']
        assert [c['ok'] for c in chain] == [False, True]
        assert chain[0]['reason'] == '超时'

    @pytest.mark.asyncio
    async def test_all_timeouts_surface_every_attempt(self, monkeypatch):
        """全部候选都超时 → 错误串点名试过的每个模型，且原因可读。"""
        from services import ai_processor as ap

        async def fake_call_ai(model_cfg, prompt, **kw):
            return '[处理失败: 超时]', 0, 0

        monkeypatch.setattr(ap, '_call_ai', fake_call_ai)
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(_tmp_db()))
        _seed_two_models()

        content, _, _, _ = await ap._call_ai_with_fallback('m1', 'p')
        assert ap.is_call_failed(content)
        assert 'm1' in content and 'm2' in content     # 试过谁要写清楚
        assert '超时' in content

    @pytest.mark.asyncio
    async def test_timeout_after_partial_output_does_not_switch(self, monkeypatch):
        """★ 超时若发生在已吐字之后 → 绝不换模型（与"已吐字不换"同一不变量）。

        这是最容易漏的边界：超时和断连走的是同一条不变量，不是两套规则。
        """
        from services import ai_processor as ap
        tried = []

        async def fake_call_ai(model_cfg, prompt, **kw):
            tried.append(model_cfg['key'])
            kw['state']['emitted'] = True
            return '[处理失败: 超时]', 0, 0

        monkeypatch.setattr(ap, '_call_ai', fake_call_ai)
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(_tmp_db()))
        _seed_two_models()

        content, _, _, used = await ap._call_ai_with_fallback('m1', 'p')
        assert tried == ['m1']
        assert ap.is_call_failed(content)
        assert used == 'm1'


class TestRealCallAiTimeoutBranch:
    """★ 跑**真实的** `_call_ai` 超时分支，不 mock 掉它。

    上一组测试全部用 fake 替换了 `_call_ai`，结果把超时分支整个跳过了——
    变异测试（把 `return '[处理失败: 超时]'` 改成 `return ''`）时 21 条全绿，
    证明这组测试根本拦不住"超时被吞成空串"这个真实回归。这里补上真调用。
    """

    _CFG = {
        'key': 'm1', 'name': 'M1', 'endpoint': 'https://example.invalid/v1/chat/completions',
        'api_key': 'sk-test', 'model_id': 'm1', 'temperature': 0.3,
        'auth_type': 'bearer', 'skip_ssl_verify': False,
    }

    @pytest.mark.asyncio
    async def test_timeout_returns_sentinel_not_empty(self, monkeypatch):
        """wait_for 超时 → 必须返回可被识别的哨兵串，不能返回空串被当成正常总结。"""
        import asyncio
        from services import ai_processor as ap

        async def boom(*a, **kw):
            raise asyncio.TimeoutError()

        monkeypatch.setattr(asyncio, 'wait_for', boom)

        content, i, o = await ap._call_ai(dict(self._CFG), 'prompt')
        assert content == '[处理失败: 超时]'
        assert ap.is_call_failed(content), '超时空串会被写进 result_text 当成正常总结'
        assert (i, o) == (0, 0)

    @pytest.mark.asyncio
    async def test_timeout_marks_state_as_not_emitted(self, monkeypatch):
        """超时发生在零输出时，state['emitted'] 必须是 False（= 允许换模型）。"""
        import asyncio
        from services import ai_processor as ap

        async def boom(*a, **kw):
            raise asyncio.TimeoutError()

        monkeypatch.setattr(asyncio, 'wait_for', boom)

        state = {}
        await ap._call_ai(dict(self._CFG), 'prompt', state=state)
        assert state.get('emitted') is False, '超时若被当成已吐字，就永远不会触发兜底'

    @pytest.mark.asyncio
    async def test_timeout_does_not_retry_same_model(self, monkeypatch):
        """★ 超时必须立即返回，**不做指数退避重试**。

        退避重试是给"连接瞬断"设计的；600s 已经等了十分钟，
        再重试三次等于一个批次白等四十分钟。这是超时与断连的语义差别。
        """
        import asyncio
        from services import ai_processor as ap

        calls = []

        async def boom(*a, **kw):
            calls.append(1)
            raise asyncio.TimeoutError()

        monkeypatch.setattr(asyncio, 'wait_for', boom)

        content, _, _ = await ap._call_ai(dict(self._CFG), 'prompt', max_retries=3)
        assert calls == [1], f'超时被重试了 {len(calls)} 次，每次都是 600s 起步'
        assert ap.is_call_failed(content)


class TestIsCallFailed:
    def test_recognises_sentinel(self):
        from services.ai_processor import is_call_failed, _strip_sentinel
        assert is_call_failed('[处理失败: HTTP 503]')
        assert is_call_failed('  [处理失败: xxx]')
        assert not is_call_failed('正常总结内容')
        assert not is_call_failed('')
        assert _strip_sentinel('[处理失败: HTTP 503: 账号池耗尽]') == 'HTTP 503: 账号池耗尽'
        assert _strip_sentinel('[处理失败]') == '未知原因'


# ---------------------------------------------------------------- 辅助

_TMP_DB = None


def _tmp_db():
    global _TMP_DB
    if _TMP_DB is None:
        import tempfile
        from pathlib import Path
        from db.database import init_db
        d = Path(tempfile.mkdtemp()) / 'ring.db'
        import db.database as database
        database.DB_PATH = d
        init_db()
        _TMP_DB = d
    return _TMP_DB


def _conn(path):
    import sqlite3
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    return c


def _seed_two_models():
    model_repo.add_model(key='m1', name='M1', endpoint='https://x/v1/chat/completions', api_key='k')
    model_repo.add_model(key='m2', name='M2', endpoint='https://x/v1/chat/completions', api_key='k')
    model_repo.set_default_model('m1')
    model_repo.set_fallback_chain(['m2'], enabled=True)
