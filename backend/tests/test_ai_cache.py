"""AI/转录结果缓存池测试 —— 2026-10-06

覆盖：
1. make_key 稳定性与区分度
2. cache_put/cache_get 往返、空结果不入池、开关、条数上限淘汰
3. 转录缓存（lookup/store）：同音频不同文件名命中、换引擎/语言/说话人分离 miss
4. _call_ai 缓存命中路径（不发起 HTTP、token 记 0、state 回填）与 bypass_cache
"""
import pytest
from pathlib import Path

import config
from services import ai_cache


@pytest.fixture()
def cache_on(monkeypatch):
    monkeypatch.setattr(config, 'AI_CACHE_ENABLED', True)
    monkeypatch.setattr(config, 'AI_CACHE_MAX_ENTRIES', 500)


# ---------------------------------------------------------------- 键

class TestMakeKey:
    def test_stable_and_distinct(self):
        k1 = ai_cache.make_key('llm', 'https://x', 'm1', 0.3, '', '提示词\n带换行')
        k2 = ai_cache.make_key('llm', 'https://x', 'm1', 0.3, '', '提示词\n带换行')
        assert k1 == k2
        assert len(k1) == 64
        assert ai_cache.make_key('llm', 'https://x', 'm1', 0.3, '', '别的') != k1
        assert ai_cache.make_key('llm', 'https://x', 'm2', 0.3, '', '提示词\n带换行') != k1

    def test_none_treated_as_empty(self):
        assert ai_cache.make_key(None, 'x') == ai_cache.make_key('', 'x')


# ---------------------------------------------------------------- 通用读写

class TestCachePutGet:
    def test_roundtrip(self, cache_on):
        key = ai_cache.make_key('t', 'roundtrip')
        assert ai_cache.cache_get(key) is None
        assert ai_cache.cache_put(key, 'llm', 'm1', '结果文本', meta={'a': 1})
        hit = ai_cache.cache_get(key)
        assert hit['content'] == '结果文本'
        assert hit['model'] == 'm1'
        assert hit['meta'] == {'a': 1}
        assert hit['op'] == 'llm'

    def test_empty_content_not_stored(self, cache_on):
        """空/空白结果不入池：失败与零输出下次必须仍是真重试。"""
        key = ai_cache.make_key('t', 'empty')
        assert not ai_cache.cache_put(key, 'llm', 'm1', '   ')
        assert ai_cache.cache_get(key) is None

    def test_disabled_toggle(self, monkeypatch):
        monkeypatch.setattr(config, 'AI_CACHE_ENABLED', False)
        key = ai_cache.make_key('t', 'disabled')
        assert not ai_cache.cache_put(key, 'llm', 'm1', 'x')
        assert ai_cache.cache_get(key) is None

    def test_prune_keeps_latest(self, cache_on, monkeypatch):
        """超出上限淘汰最旧：本轮插入的 13 条里最新 10 条必须保留。"""
        monkeypatch.setattr(config, 'AI_CACHE_MAX_ENTRIES', 10)
        keys = [ai_cache.make_key('t', f'prune{i}') for i in range(13)]
        for i, k in enumerate(keys):
            ai_cache.cache_put(k, 'llm', 'm', f'v{i}')
        from db.database import get_connection
        conn = get_connection()
        try:
            assert conn.execute("SELECT COUNT(*) c FROM ai_cache").fetchone()['c'] <= 10
        finally:
            conn.close()
        for k in keys[3:]:
            assert ai_cache.cache_get(k) is not None


# ---------------------------------------------------------------- 转录缓存

class TestTranscriptionCache:
    def test_store_then_lookup_hit_across_jobs(self, cache_on, tmp_path):
        """同一音频内容、不同文件名/不同任务目录 → 命中并写回新音频旁的约定路径。"""
        audio = tmp_path / 'a.m4a'
        audio.write_bytes(b'AUDIOBYTES')
        srt = tmp_path / 'a.srt'
        srt.write_text('1\n00:00:00,000 --> 00:00:01,000\n你好\n', encoding='utf-8')
        txt = tmp_path / 'a.txt'
        txt.write_text('你好', encoding='utf-8')
        assert ai_cache.store_transcription(str(audio), 'cloud_free', 'zh', False,
                                            str(srt), str(txt), 1)

        job2 = tmp_path / 'job2'
        job2.mkdir()
        audio2 = job2 / 'b.m4a'
        audio2.write_bytes(b'AUDIOBYTES')  # 同内容不同名
        hit = ai_cache.lookup_transcription(str(audio2), 'cloud_free', 'zh', False)
        assert hit and hit['seg_count'] == 1
        assert Path(hit['srt_path']).read_text(encoding='utf-8').startswith('1\n')
        assert Path(hit['txt_path']).read_text(encoding='utf-8') == '你好'
        assert Path(hit['srt_path']).parent == job2

    def test_engine_or_language_change_misses(self, cache_on, tmp_path):
        audio = tmp_path / 'c.m4a'
        audio.write_bytes(b'X')
        srt = tmp_path / 'c.srt'
        srt.write_text('s', encoding='utf-8')
        txt = tmp_path / 'c.txt'
        txt.write_text('t', encoding='utf-8')
        ai_cache.store_transcription(str(audio), 'cloud_free', 'zh', False,
                                     str(srt), str(txt), 1)
        assert ai_cache.lookup_transcription(str(audio), 'firered', 'zh', False) is None
        assert ai_cache.lookup_transcription(str(audio), 'cloud_free', 'en', False) is None
        assert ai_cache.lookup_transcription(str(audio), 'cloud_free', 'zh', True) is None

    def test_different_audio_misses(self, cache_on, tmp_path):
        audio = tmp_path / 'd.m4a'
        audio.write_bytes(b'CONTENT-1')
        srt = tmp_path / 'd.srt'
        srt.write_text('s', encoding='utf-8')
        txt = tmp_path / 'd.txt'
        txt.write_text('t', encoding='utf-8')
        ai_cache.store_transcription(str(audio), 'cloud_free', 'zh', False,
                                     str(srt), str(txt), 1)
        other = tmp_path / 'e.m4a'
        other.write_bytes(b'CONTENT-2')
        assert ai_cache.lookup_transcription(str(other), 'cloud_free', 'zh', False) is None

    def test_missing_audio_returns_none(self, cache_on, tmp_path):
        assert ai_cache.lookup_transcription(str(tmp_path / 'nope.m4a'),
                                             'cloud_free', 'zh', False) is None


# ---------------------------------------------------------------- _call_ai 缓存路径

class TestCallAiCache:
    _CFG = {
        'key': 'm1', 'name': 'M1', 'endpoint': 'https://cache-test.invalid/v1/chat/completions',
        'api_key': 'sk-test', 'model_id': 'm1', 'temperature': 0.3,
        'auth_type': 'bearer', 'skip_ssl_verify': False,
    }

    @pytest.mark.asyncio
    async def test_hit_skips_http_and_records_zero_tokens(self, cache_on):
        from services import ai_processor as ap
        cfg = dict(self._CFG)
        prompt = f'缓存命中测试提示词-{self._CFG["endpoint"]}'
        key = ai_cache.make_key('llm', cfg['endpoint'], cfg['model_id'], 0.3, '', prompt)
        assert ai_cache.cache_put(key, 'llm', cfg['model_id'], '上次的结果')

        content, i, o = await ap._call_ai(cfg, prompt)
        assert content == '上次的结果'
        assert (i, o) == (0, 0), '缓存命中不产生 token 消耗'

        state = {}
        content2, _, _ = await ap._call_ai(cfg, prompt, state=state)
        assert content2 == '上次的结果'
        assert state['emitted'] is True
        assert state['content'] == '上次的结果'

    @pytest.mark.asyncio
    async def test_bypass_ignores_cache(self, cache_on, monkeypatch):
        """bypass_cache=True（显式重跑语义）绝不读缓存 → 走 HTTP。"""
        from services import ai_processor as ap
        cfg = dict(self._CFG)
        prompt = 'bypass 提示词'
        key = ai_cache.make_key('llm', cfg['endpoint'], cfg['model_id'], 0.3, '', prompt)
        ai_cache.cache_put(key, 'llm', cfg['model_id'], '缓存内容')

        class _Boom:
            def __init__(self, *a, **k):
                raise RuntimeError('no http in test')

        monkeypatch.setattr(ap.httpx, 'AsyncClient', _Boom)
        content, _, _ = await ap._call_ai(cfg, prompt, bypass_cache=True)
        assert ap.is_call_failed(content), 'bypass 必须真的发请求，而不是返回缓存'

    @pytest.mark.asyncio
    async def test_success_result_is_cached(self, cache_on, monkeypatch):
        """真实 _call_ai 成功后结果入池：同键第二次调用直接命中（不再打 HTTP）。"""
        import asyncio
        from services import ai_processor as ap
        cfg = dict(self._CFG)
        prompt = '成功入池测试'

        async def fake_wait_for(coro, timeout=None):
            coro.close()  # 不真正执行 _stream（会打 HTTP），直接给一次"成功流"的产出
            return ['流式结果'], 11, 22, 1

        monkeypatch.setattr(asyncio, 'wait_for', fake_wait_for)
        content, i, o = await ap._call_ai(cfg, prompt)
        assert content == '流式结果'
        assert (i, o) == (11, 22)

        key = ai_cache.make_key('llm', cfg['endpoint'], cfg['model_id'], 0.3, '', prompt)
        hit = ai_cache.cache_get(key)
        assert hit and hit['content'] == '流式结果', '成功结果必须入池'

        # 第二次调用命中缓存：若走到 HTTP（wait_for 被调用）即失败
        async def guard_wait_for(coro, timeout=None):
            coro.close()
            raise AssertionError('缓存命中时不应发起 HTTP')

        monkeypatch.setattr(asyncio, 'wait_for', guard_wait_for)
        content2, i2, o2 = await ap._call_ai(cfg, prompt)
        assert content2 == '流式结果'
        assert (i2, o2) == (0, 0)


# ---------------------------------------------------------------- transcribe_audio 的 bypass 链路

class TestTranscribeAudioBypass:
    """「忽略缓存」开关（2026-10-06）：bypass_cache=True 跳过 ASR 缓存读取、
    强制重新识别，但重新生成的结果仍回写缓存池修复旧条目。"""

    def _setup(self, monkeypatch, tmp_path, seen):
        import services.transcriber as tr
        audio = tmp_path / 'a.mp3'
        audio.write_bytes(b'AUDIO')
        srt = tmp_path / 'a.srt'
        srt.write_text('1\n00:00:00,000 --> 00:00:01,000\n你\n', encoding='utf-8')
        txt = tmp_path / 'a.txt'
        txt.write_text('你', encoding='utf-8')
        monkeypatch.setattr(ai_cache, 'lookup_transcription',
                            lambda *a, **k: seen.__setitem__('lookup', seen['lookup'] + 1))
        monkeypatch.setattr(ai_cache, 'store_transcription',
                            lambda *a, **k: seen.__setitem__('store', seen['store'] + 1) or True)
        monkeypatch.setattr(tr, '_transcribe_sync',
                            lambda **kw: (str(srt), str(txt), 1))
        return tr

    @pytest.mark.asyncio
    async def test_bypass_skips_lookup_but_still_stores(self, cache_on, monkeypatch, tmp_path):
        seen = {'lookup': 0, 'store': 0}
        tr = self._setup(monkeypatch, tmp_path, seen)
        out = await tr.transcribe_audio(str(tmp_path / 'a.mp3'), engine='cloud_free',
                                        language='zh', bypass_cache=True)
        assert out[2] == 1
        assert seen == {'lookup': 0, 'store': 1}

    @pytest.mark.asyncio
    async def test_default_reads_cache_then_stores(self, cache_on, monkeypatch, tmp_path):
        seen = {'lookup': 0, 'store': 0}
        tr = self._setup(monkeypatch, tmp_path, seen)
        await tr.transcribe_audio(str(tmp_path / 'a.mp3'), engine='cloud_free', language='zh')
        assert seen == {'lookup': 1, 'store': 1}
