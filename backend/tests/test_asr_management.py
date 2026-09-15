"""
ASR 管理模块测试（云端 provider / 本地模型 / 默认设置）

覆盖三层：
1. db.asr_repo —— provider CRUD、槽位解析（sort_order/启停）、内置播种、设置读写与非法值
2. services.cloud_asr —— 「DB 优先 → config 兜底」的读取单点、按请求姿势解析响应
3. routers/asr + routers/asr_admin —— 脱敏契约、CRUD 状态码、防脱敏串回写、默认引擎生效链、
   本地模型生命周期（下载/取消/删除，含下载中禁删）

不触网：测试连接用替身 transcribe_file；本地模型文件用临时目录替身。
"""
import sqlite3
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import asr_repo


@pytest.fixture(autouse=True)
def _patch_conn(db_path):
    """asr_repo 的连接指向每个用例独立的临时库（init_db 已建全量 schema 并播种内置 provider）。"""
    def _get_conn():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    with patch("db.asr_repo.get_connection", side_effect=_get_conn):
        yield


@pytest.fixture
def client():
    app = FastAPI()
    from routers.asr import router as asr_router
    from routers.asr_admin import router as asr_admin_router
    app.include_router(asr_router, prefix="/api/asr")
    app.include_router(asr_admin_router, prefix="/api/asr")
    return TestClient(app)


def _clear_providers():
    conn = asr_repo.get_connection()
    try:
        conn.execute("DELETE FROM asr_providers")
        conn.commit()
    finally:
        conn.close()


# ============ 1. asr_repo ============

class TestAsrRepoCrud:
    def test_add_and_get(self):
        _clear_providers()
        assert asr_repo.add_provider(
            key='my-relay', name='自建中转', engine='cloud', api='chat',
            endpoint='https://relay.example.com/v1/chat/completions',
            api_key='sk-test', model='mimo-v2.5-asr', sort_order=5, remark='备线')
        row = asr_repo.get_provider('my-relay')
        assert row['name'] == '自建中转'
        assert row['engine'] == 'cloud'
        assert row['api'] == 'chat'
        assert row['sort_order'] == 5
        assert row['enabled'] == 1
        assert row['is_builtin'] == 0

    def test_add_duplicate_returns_false(self):
        _clear_providers()
        assert asr_repo.add_provider(key='k1', name='K1', engine='cloud', endpoint='https://a.com')
        assert asr_repo.add_provider(key='k1', name='K1x', engine='cloud', endpoint='https://a.com') is False

    def test_list_sorted_by_engine_then_sort_order(self):
        _clear_providers()
        asr_repo.add_provider(key='free-b', name='FB', engine='cloud_free', endpoint='https://f.com', sort_order=30)
        asr_repo.add_provider(key='paid-b', name='PB', engine='cloud', endpoint='https://p.com', sort_order=90)
        asr_repo.add_provider(key='paid-a', name='PA', engine='cloud', endpoint='https://p.com', sort_order=10)
        assert [p['key'] for p in asr_repo.list_providers()] == ['paid-a', 'paid-b', 'free-b']

    def test_update_whitelists_columns(self):
        _clear_providers()
        asr_repo.add_provider(key='k', name='N', engine='cloud', endpoint='https://a.com')
        assert asr_repo.update_provider('k', name='新名', enabled=False, sort_order=7)
        row = asr_repo.get_provider('k')
        assert row['name'] == '新名'
        assert row['enabled'] == 0
        assert row['sort_order'] == 7
        # 非白名单列被忽略（防动态 SQL 注入列名）
        assert asr_repo.update_provider('k', is_builtin=1) is False
        assert asr_repo.get_provider('k')['is_builtin'] == 0
        # None 值表示"不修改"
        assert asr_repo.update_provider('k', name=None, engine='cloud_free')
        assert asr_repo.get_provider('k')['name'] == '新名'

    def test_update_nonexistent(self):
        _clear_providers()
        assert asr_repo.update_provider('ghost', name='x') is False

    def test_delete_custom_ok(self):
        _clear_providers()
        asr_repo.add_provider(key='k', name='N', engine='cloud', endpoint='https://a.com')
        assert asr_repo.delete_provider('k') is True
        assert asr_repo.get_provider('k') is None

    def test_delete_builtin_refused(self):
        _clear_providers()
        asr_repo.add_provider(key='b', name='B', engine='cloud', endpoint='https://a.com', is_builtin=True)
        assert asr_repo.delete_provider('b') is False
        assert asr_repo.get_provider('b') is not None

    def test_delete_nonexistent(self):
        _clear_providers()
        assert asr_repo.delete_provider('ghost') is False


class TestProviderForEngine:
    def test_picks_lowest_sort_order(self):
        _clear_providers()
        asr_repo.add_provider(key='slow', name='S', engine='cloud', endpoint='https://a.com', sort_order=50)
        asr_repo.add_provider(key='fast', name='F', engine='cloud', endpoint='https://b.com', sort_order=5)
        assert asr_repo.get_provider_for_engine('cloud') == 'fast'

    def test_skips_disabled(self):
        _clear_providers()
        asr_repo.add_provider(key='off', name='O', engine='cloud', endpoint='https://a.com',
                              sort_order=1, enabled=False)
        asr_repo.add_provider(key='on', name='N', engine='cloud', endpoint='https://b.com', sort_order=9)
        assert asr_repo.get_provider_for_engine('cloud') == 'on'

    def test_none_when_slot_empty(self):
        _clear_providers()
        assert asr_repo.get_provider_for_engine('cloud') is None
        assert asr_repo.get_provider_for_engine('cloud_free') is None

    def test_invalid_engine_returns_none(self):
        _clear_providers()
        asr_repo.add_provider(key='k', name='K', engine='cloud', endpoint='https://a.com')
        assert asr_repo.get_provider_for_engine('local') is None
        assert asr_repo.get_provider_for_engine('') is None


class TestSeedBuiltin:
    def test_seeds_two_builtins_when_empty(self):
        _clear_providers()
        assert asr_repo.seed_builtin_providers() == 2
        rows = {p['key']: p for p in asr_repo.list_providers()}
        assert set(rows) == {'mimo', 'xingchen'}
        assert rows['mimo']['engine'] == 'cloud' and rows['mimo']['is_builtin'] == 1
        assert rows['xingchen']['engine'] == 'cloud_free' and rows['xingchen']['api'] == 'multipart'

    def test_does_not_reseed_when_not_empty(self):
        _clear_providers()
        asr_repo.add_provider(key='mine', name='M', engine='cloud', endpoint='https://a.com')
        assert asr_repo.seed_builtin_providers() == 0
        assert [p['key'] for p in asr_repo.list_providers()] == ['mine']

    def test_seed_migrates_config_key_into_db(self):
        _clear_providers()
        with patch('config.ASR_CLOUD_API_KEY', 'sk-from-config', create=True), \
             patch('config.ASR_CLOUD_ENDPOINT', 'https://mimo.example.com/v1/chat/completions', create=True):
            asr_repo.seed_builtin_providers()
        row = asr_repo.get_provider('mimo')
        assert row['api_key'] == 'sk-from-config'
        assert row['endpoint'] == 'https://mimo.example.com/v1/chat/completions'


class TestSettings:
    def test_defaults(self):
        from config import DEFAULT_WHISPER_MODEL
        assert asr_repo.get_default_engine() == ''
        assert asr_repo.get_whisper_model() == DEFAULT_WHISPER_MODEL
        assert asr_repo.get_whisper_device() == 'auto'

    def test_set_and_get(self):
        assert asr_repo.set_default_engine('cloud_free')
        assert asr_repo.set_whisper_model('small')
        assert asr_repo.set_whisper_device('cpu')
        assert asr_repo.get_default_engine() == 'cloud_free'
        assert asr_repo.get_whisper_model() == 'small'
        assert asr_repo.get_whisper_device() == 'cpu'

    def test_invalid_values_rejected(self):
        assert asr_repo.set_default_engine('auto') is False
        assert asr_repo.set_whisper_model('huge') is False
        assert asr_repo.set_whisper_device('tpu') is False
        assert asr_repo.get_default_engine() == ''
        assert asr_repo.get_whisper_device() == 'auto'

    def test_empty_engine_clears_to_auto_recommend(self):
        """空串 = 清除用户设置，恢复后端"免费优先"自动推荐。"""
        assert asr_repo.set_default_engine('local')
        assert asr_repo.get_default_engine() == 'local'
        assert asr_repo.set_default_engine('') is True
        assert asr_repo.get_default_engine() == ''


# ============ 2. cloud_asr 读取单点（DB 优先 → config 兜底） ============

class TestProviderCfgDbFirst:
    def test_db_row_wins_over_config(self):
        from services import cloud_asr
        _clear_providers()
        asr_repo.add_provider(key='mimo', name='自建 MiMo', engine='cloud', api='chat',
                              endpoint='https://db.example.com/v1/chat/completions',
                              api_key='sk-db', model='db-model')
        with patch('config.ASR_CLOUD_ENDPOINT', 'https://config.example.com'), \
             patch('config.ASR_CLOUD_API_KEY', 'sk-config'), \
             patch('config.ASR_CLOUD_MODEL', 'config-model'):
            cfg = cloud_asr._provider_cfg('mimo')
        assert cfg['endpoint'] == 'https://db.example.com/v1/chat/completions'
        assert cfg['api_key'] == 'sk-db'
        assert cfg['model'] == 'db-model'
        assert cfg['name'] == '自建 MiMo'
        assert cloud_asr.cloud_asr_configured('mimo') is True

    def test_falls_back_to_config_when_no_row(self):
        from services import cloud_asr
        _clear_providers()
        with patch('config.ASR_CLOUD_ENABLED', True), \
             patch('config.ASR_CLOUD_ENDPOINT', 'https://config.example.com/v1/chat/completions'), \
             patch('config.ASR_CLOUD_API_KEY', 'sk-config'), \
             patch('config.ASR_CLOUD_MODEL', 'config-model'):
            cfg = cloud_asr._provider_cfg('mimo')
            assert cfg['endpoint'] == 'https://config.example.com/v1/chat/completions'
            assert cfg['api'] == 'chat'
            assert cloud_asr.cloud_asr_configured('mimo') is True

    def test_disabled_row_means_not_configured(self):
        from services import cloud_asr
        _clear_providers()
        asr_repo.add_provider(key='mimo', name='M', engine='cloud', api='chat',
                              endpoint='https://db.example.com', api_key='sk-db', enabled=False)
        assert cloud_asr.cloud_asr_configured('mimo') is False
        assert cloud_asr.provider_for_engine('cloud') is None

    def test_multipart_posture_from_db(self):
        from services import cloud_asr
        _clear_providers()
        asr_repo.add_provider(key='custom-free', name='自建免费档', engine='cloud_free', api='multipart',
                              endpoint='https://sf.example.com/v1/audio/transcriptions',
                              api_key='sk', model='X')
        assert cloud_asr._provider_cfg('custom-free')['api'] == 'multipart'
        assert cloud_asr.provider_for_engine('cloud_free') == 'custom-free'

    def test_provider_label_prefers_builtin_short_label(self):
        from services import cloud_asr
        _clear_providers()
        asr_repo.add_provider(key='mimo', name='很长的显示名称', engine='cloud', endpoint='https://a.com')
        assert cloud_asr.provider_label('mimo') == 'MiMo-ASR'
        asr_repo.add_provider(key='custom', name='自定义引擎', engine='cloud', endpoint='https://b.com')
        assert cloud_asr.provider_label('custom') == '自定义引擎'
        assert cloud_asr.provider_label('ghost') == 'ghost'


class TestExtractAsrTextPosture:
    def test_chat_and_multipart(self):
        from services.cloud_asr import _extract_asr_text
        assert _extract_asr_text('chat', {'choices': [{'message': {'content': 'A'}}]}) == 'A'
        assert _extract_asr_text('multipart', {'text': 'B'}) == 'B'
        assert _extract_asr_text('multipart', {'choices': [{'message': {'content': 'A'}}]}) == ''


# ============ 3. 路由层 ============

def _stub_local(monkeypatch, *, firered_ready=False, download=None, busy=False):
    """本地引擎状态替身：不读真实磁盘 / 不跑真实文件检查，用例与机器上的模型文件无关。"""
    import services.local_asr as la
    import services.transcriber as tr

    idle = {'status': 'idle', 'percent': 0, 'message': '', 'error': ''}
    dl = download or idle
    monkeypatch.setattr(tr, 'list_local_models', lambda: [
        {'size': 'base', 'path': 'X:/models/whisper_base', 'ready': True, 'partial': False, 'bytes': 150_000_000},
        {'size': 'medium', 'path': 'X:/models/whisper_medium', 'ready': False, 'partial': False, 'bytes': 0},
    ])
    monkeypatch.setattr(tr, 'any_whisper_ready', lambda: True)
    monkeypatch.setattr(la, 'sherpa_available', lambda: True)
    monkeypatch.setattr(la, 'engine_ready', lambda k: firered_ready if k == 'firered' else False)
    monkeypatch.setattr(la, 'list_engines_status', lambda: [
        {'key': 'firered', 'name': '本地 FireRedASR', 'fee': '免费', 'languages': '中文 / 英文',
         'note': '中英最准', 'size_hint': '约 1.5GB', 'ready': firered_ready, 'busy': busy,
         'bytes': 1, 'dir': 'X:/m/firered',
         'parts': [{'key': 'encoder', 'label': '声学编码器', 'path': 'X:/m/encoder.int8.onnx',
                    'ready': firered_ready, 'bytes': 1}],
         'manual_hint': '手动放置：X:/m/firered', 'download': dl},
    ])


class TestOverviewRouter:
    def test_overview_shape_and_masking(self, client, monkeypatch):
        _clear_providers()
        _stub_local(monkeypatch)
        asr_repo.add_provider(key='mimo', name='小米 MiMo', engine='cloud', api='chat',
                              endpoint='https://m.example.com', api_key='sk-abcdefghijkl',
                              model='mimo-v2.5-asr', is_builtin=True)
        resp = client.get('/api/asr/overview')
        assert resp.status_code == 200
        data = resp.json()

        assert len(data['providers']) == 1
        p = data['providers'][0]
        # 密钥脱敏：只留 前4 + **** + 后4，且不回原始串
        assert p['api_key'] == 'sk-a****ijkl'
        assert 'sk-abcdefghijkl' not in resp.text
        assert p['has_key'] is True and p['key_broken'] is False
        assert p['configured'] is True
        assert p['runtime'] is True          # 唯一 provider → 就是槽位当前使用者
        assert p['is_builtin'] is True

        # 本地段 = 引擎列表（含免费标识与就绪态）+ Whisper 档位明细（不混在引擎列表里）
        engines = {e['key']: e for e in data['local']['engines']}
        assert set(engines) == {'local', 'firered'}
        assert engines['local']['name'] == '本地 Whisper'
        assert {e['fee'] for e in engines.values()} == {'免费'}
        assert engines['firered']['ready'] is False
        assert engines['firered']['download']['status'] == 'idle'
        assert [m['size'] for m in data['local']['whisper']['models']] == ['base', 'medium']
        assert data['local']['whisper']['device'] == 'auto'
        assert data['local']['sherpa_available'] is True
        assert data['settings']['whisper_device'] == 'auto'
        assert data['recommended_engine'] in ('cloud', 'cloud_free', 'firered', 'local')

    def test_cloud_provider_fee_label(self, client, monkeypatch):
        """云端 provider 的收费标识单点在后端：cloud=收费 / cloud_free=免费。"""
        _clear_providers()
        _stub_local(monkeypatch)
        asr_repo.add_provider(key='paid', name='付费', engine='cloud', endpoint='https://p.com')
        asr_repo.add_provider(key='free', name='免费', engine='cloud_free', endpoint='https://f.com')
        fees = {p['engine']: p['fee'] for p in client.get('/api/asr/overview').json()['providers']}
        assert fees == {'cloud': '收费', 'cloud_free': '免费'}

    def test_status_lists_local_engines_for_workbench_dropdown(self, client, monkeypatch):
        import services.local_asr as la
        _clear_providers()
        _stub_local(monkeypatch, firered_ready=True)
        engines = {e['key']: e for e in client.get('/api/asr/status').json()['local_engines']}
        # Whisper + 注册表里的全部本地引擎（新增引擎自动出现在下拉里，无需改前端）
        assert set(engines) == {'local', *la.LOCAL_ENGINE_KEYS}
        assert engines['firered']['ready'] is True

    def test_overview_flags_broken_key(self, client, monkeypatch):
        """历史污染的脱敏串（含 ****）必须标 key_broken，让用户重新粘贴真密钥。"""
        _clear_providers()
        _stub_local(monkeypatch)
        asr_repo.add_provider(key='broken', name='污染', engine='cloud',
                              endpoint='https://b.com', api_key='sk-1****9')
        p = client.get('/api/asr/overview').json()['providers'][0]
        assert p['key_broken'] is True
        assert p['configured'] is True  # 有串 → 仍会被调用，但标记提示无效

    def test_status_exposes_default_engine(self, client, monkeypatch):
        _clear_providers()
        _stub_local(monkeypatch)
        assert client.get('/api/asr/status').json()['default_engine'] == ''
        asr_repo.set_default_engine('local')
        assert client.get('/api/asr/status').json()['default_engine'] == 'local'


class TestProviderCrudRouter:
    def _body(self, **over):
        body = {
            'key': 'my-relay', 'name': '自建中转', 'engine': 'cloud', 'api': 'chat',
            'endpoint': 'https://relay.example.com/v1/chat/completions',
            'api_key': 'sk-1234567890', 'model': 'mimo-v2.5-asr', 'enabled': True, 'sort_order': 5,
        }
        body.update(over)
        return body

    def test_add_success(self, client, monkeypatch):
        _clear_providers()
        _stub_local(monkeypatch)
        resp = client.post('/api/asr/providers', json=self._body())
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        row = asr_repo.get_provider('my-relay')
        assert row['sort_order'] == 5 and row['api_key'] == 'sk-1234567890'

    def test_add_duplicate_key_400(self, client):
        _clear_providers()
        asr_repo.add_provider(key='dup', name='D', engine='cloud', endpoint='https://a.com')
        resp = client.post('/api/asr/providers', json=self._body(key='dup'))
        assert resp.status_code == 400
        assert '已存在' in resp.json()['detail']

    def test_add_invalid_key_400(self, client):
        resp = client.post('/api/asr/providers', json=self._body(key='BAD KEY!'))
        assert resp.status_code == 400
        assert '标识' in resp.json()['detail']

    def test_add_invalid_engine_400(self, client):
        resp = client.post('/api/asr/providers', json=self._body(engine='local'))
        assert resp.status_code == 400
        assert '引擎槽位非法' in resp.json()['detail']

    def test_add_invalid_api_400(self, client):
        resp = client.post('/api/asr/providers', json=self._body(api='grpc'))
        assert resp.status_code == 400
        assert '请求姿势非法' in resp.json()['detail']

    def test_add_empty_endpoint_400(self, client):
        resp = client.post('/api/asr/providers', json=self._body(endpoint='  '))
        assert resp.status_code == 400
        assert '端点' in resp.json()['detail']

    def test_update_keeps_key_when_blank(self, client, monkeypatch):
        """编辑态密钥框留空 = 不修改（防脱敏串回写覆盖真密钥）。"""
        _clear_providers()
        asr_repo.add_provider(key='k', name='K', engine='cloud', endpoint='https://a.com',
                              api_key='sk-real-key')
        resp = client.put('/api/asr/providers/k', json=self._body(key='k', api_key='', name='改名'))
        assert resp.status_code == 200
        row = asr_repo.get_provider('k')
        assert row['api_key'] == 'sk-real-key'
        assert row['name'] == '改名'

    def test_update_keeps_key_when_masked_string_sent(self, client):
        """前端误回传脱敏串（含 ****）也必须保留原密钥。"""
        _clear_providers()
        asr_repo.add_provider(key='k', name='K', engine='cloud', endpoint='https://a.com',
                              api_key='sk-real-key')
        client.put('/api/asr/providers/k', json=self._body(key='k', api_key='sk-r****-key'))
        assert asr_repo.get_provider('k')['api_key'] == 'sk-real-key'

    def test_update_replaces_key_when_real_value_sent(self, client):
        _clear_providers()
        asr_repo.add_provider(key='k', name='K', engine='cloud', endpoint='https://a.com',
                              api_key='sk-old')
        client.put('/api/asr/providers/k', json=self._body(key='k', api_key='sk-new'))
        assert asr_repo.get_provider('k')['api_key'] == 'sk-new'

    def test_update_nonexistent_404(self, client):
        _clear_providers()
        assert client.put('/api/asr/providers/ghost', json=self._body()).status_code == 404

    def test_delete_builtin_400(self, client):
        _clear_providers()
        asr_repo.add_provider(key='b', name='B', engine='cloud', endpoint='https://a.com', is_builtin=True)
        resp = client.delete('/api/asr/providers/b')
        assert resp.status_code == 400
        assert '内置' in resp.json()['detail']

    def test_delete_custom_ok(self, client):
        _clear_providers()
        asr_repo.add_provider(key='c', name='C', engine='cloud', endpoint='https://a.com')
        assert client.delete('/api/asr/providers/c').status_code == 200
        assert asr_repo.get_provider('c') is None

    def test_delete_nonexistent_404(self, client):
        _clear_providers()
        assert client.delete('/api/asr/providers/ghost').status_code == 404

    def test_toggle_enabled_changes_slot_choice(self, client, monkeypatch):
        _clear_providers()
        _stub_local(monkeypatch)
        asr_repo.add_provider(key='a', name='A', engine='cloud', endpoint='https://a.com',
                              api_key='sk-a', sort_order=1)
        asr_repo.add_provider(key='b', name='B', engine='cloud', endpoint='https://b.com',
                              api_key='sk-b', sort_order=2)
        assert asr_repo.get_provider_for_engine('cloud') == 'a'
        client.put('/api/asr/providers/a', json=self._body(key='a', api_key='sk-a',
                                                           name='A', sort_order=1, enabled=False))
        assert asr_repo.get_provider_for_engine('cloud') == 'b'


class TestProviderTestRouter:
    def test_test_success(self, client, monkeypatch, tmp_path):
        from services import cloud_asr
        _clear_providers()
        asr_repo.add_provider(key='k', name='K', engine='cloud', endpoint='https://a.com',
                              api_key='sk', model='m')
        monkeypatch.setattr('routers.asr_admin._make_probe_audio',
                            lambda: str(tmp_path / 'probe.mp3'))
        (tmp_path / 'probe.mp3').write_bytes(b'x')
        monkeypatch.setattr(cloud_asr, 'transcribe_file', lambda *a, **kw: '识别文本')
        data = client.post('/api/asr/providers/k/test').json()
        assert data['success'] is True
        assert data['text'] == '识别文本'
        assert data['model'] == 'm'

    def test_test_empty_text_is_still_success(self, client, monkeypatch, tmp_path):
        """探测音频无人声 → 文本为空但链路正常，仍算成功并给出说明。"""
        from services import cloud_asr
        _clear_providers()
        asr_repo.add_provider(key='k', name='K', engine='cloud', endpoint='https://a.com', api_key='sk')
        monkeypatch.setattr('routers.asr_admin._make_probe_audio', lambda: str(tmp_path / 'p.mp3'))
        (tmp_path / 'p.mp3').write_bytes(b'x')
        monkeypatch.setattr(cloud_asr, 'transcribe_file', lambda *a, **kw: '')
        data = client.post('/api/asr/providers/k/test').json()
        assert data['success'] is True and data['text'] == ''
        assert '未返回文本' in data['note']

    def test_test_config_error_returns_failure(self, client, monkeypatch, tmp_path):
        from services import cloud_asr
        _clear_providers()
        asr_repo.add_provider(key='k', name='K', engine='cloud', endpoint='https://a.com', api_key='bad')
        monkeypatch.setattr('routers.asr_admin._make_probe_audio', lambda: str(tmp_path / 'p.mp3'))
        (tmp_path / 'p.mp3').write_bytes(b'x')

        def _boom(*a, **kw):
            raise cloud_asr.CloudASRConfigError('云端 ASR 401: ASR 密钥无效')
        monkeypatch.setattr(cloud_asr, 'transcribe_file', _boom)
        data = client.post('/api/asr/providers/k/test').json()
        assert data['success'] is False
        assert '401' in data['error']

    def test_test_nonexistent_404(self, client):
        _clear_providers()
        assert client.post('/api/asr/providers/ghost/test').status_code == 404

    def test_probe_audio_generation_failure_is_visible(self, client, monkeypatch):
        """探测音频生成失败必须显式失败（不能静默当成"连接成功"）。"""
        _clear_providers()
        asr_repo.add_provider(key='k', name='K', engine='cloud', endpoint='https://a.com', api_key='sk')

        def _boom():
            raise RuntimeError('ffmpeg 缺失')
        monkeypatch.setattr('routers.asr_admin._make_probe_audio', _boom)
        data = client.post('/api/asr/providers/k/test').json()
        assert data['success'] is False
        assert 'ffmpeg 缺失' in data['error']


class TestSettingsRouter:
    def test_update_settings_and_effect_on_recommendation(self, client, monkeypatch):
        _clear_providers()
        _stub_local(monkeypatch)
        # 免费档可用 → 自动推荐 cloud_free
        asr_repo.add_provider(key='xingchen', name='免费', engine='cloud_free', api='multipart',
                              endpoint='https://sf.com/v1/audio/transcriptions', api_key='sk-free')
        assert client.get('/api/asr/status').json()['recommended_engine'] == 'cloud_free'

        # 用户显式设 local → 推荐值必须跟随（用户设置优先）
        resp = client.post('/api/asr/settings', json={'default_engine': 'local',
                                                      'whisper_model': 'small',
                                                      'whisper_device': 'cpu'})
        assert resp.status_code == 200
        body = resp.json()
        assert body['settings'] == {'default_engine': 'local', 'whisper_model': 'small',
                                    'whisper_device': 'cpu'}
        assert body['recommended_engine'] == 'local'
        assert client.get('/api/asr/status').json()['recommended_engine'] == 'local'

    def test_paid_default_never_upgrades_to_paid_when_absent(self, client, monkeypatch):
        """用户设 cloud 但付费档不可用 → 降级只往便宜方向（免费档可用则 cloud_free）。"""
        _clear_providers()
        _stub_local(monkeypatch)
        asr_repo.add_provider(key='xingchen', name='免费', engine='cloud_free', api='multipart',
                              endpoint='https://sf.com/v1/audio/transcriptions', api_key='sk-free')
        assert client.post('/api/asr/settings', json={'default_engine': 'cloud'}).status_code == 200
        assert client.get('/api/asr/status').json()['recommended_engine'] == 'cloud_free'

    def test_free_default_never_upgrades_to_paid(self, client, monkeypatch):
        """用户设 cloud_free 但免费档不可用 → 落 local，绝不自动转付费（成本不变量）。"""
        _clear_providers()
        _stub_local(monkeypatch)
        asr_repo.add_provider(key='mimo', name='付费', engine='cloud', api='chat',
                              endpoint='https://m.com/v1/chat/completions', api_key='sk-paid')
        asr_repo.set_default_engine('cloud_free')
        assert client.get('/api/asr/status').json()['recommended_engine'] == 'local'

    def test_invalid_values_400(self, client):
        resp = client.post('/api/asr/settings', json={'default_engine': 'auto',
                                                      'whisper_model': 'huge',
                                                      'whisper_device': 'tpu'})
        assert resp.status_code == 400
        detail = resp.json()['detail']
        assert '默认引擎非法' in detail and 'Whisper 档位非法' in detail and '转录设备非法' in detail
        assert asr_repo.get_default_engine() == ''


class TestLocalModelRouter:
    """本地引擎生命周期端点（按 engine key 通用：`/local/{engine}/*`）

    路径参数是通用 key，未知 key 一律 404（不再硬编码 firered）。
    """

    def test_engine_download_started(self, client, monkeypatch):
        import services.local_asr as la
        monkeypatch.setattr(la, 'engine_ready', lambda k: False)
        monkeypatch.setattr(la, 'start_engine_download', lambda k: True)
        data = client.post('/api/asr/local/firered/download').json()
        assert data['started'] is True
        assert 'FireRed' in data['message']

    def test_engine_download_skipped_when_ready(self, client, monkeypatch):
        import services.local_asr as la
        monkeypatch.setattr(la, 'engine_ready', lambda k: True)
        data = client.post('/api/asr/local/firered/download').json()
        assert data['started'] is False
        assert '无需下载' in data['message']

    def test_engine_download_conflict_409(self, client, monkeypatch):
        import services.local_asr as la
        monkeypatch.setattr(la, 'engine_ready', lambda k: False)
        monkeypatch.setattr(la, 'start_engine_download', lambda k: False)
        assert client.post('/api/asr/local/firered/download').status_code == 409

    def test_download_state_reports_engine_key(self, client, monkeypatch):
        """轮询态带 engine 字段：管理页据此判断「在下的是不是我看的这个引擎」。"""
        import services.local_asr as la
        monkeypatch.setattr(la, 'engine_ready', lambda k: False)
        monkeypatch.setattr(la, 'start_engine_download', lambda k: True)
        assert client.post('/api/asr/local/firered/download').json()['started'] is True
        state = client.get('/api/asr/local/firered/download').json()
        assert state['engine'] == 'firered'

    def test_unknown_engine_404(self, client):
        resp = client.post('/api/asr/local/ghost_engine/download')
        assert resp.status_code == 404
        assert '未注册的本地引擎' in resp.json()['detail']
        assert client.delete('/api/asr/local/ghost_engine').status_code == 404

    def test_engine_cancel(self, client, monkeypatch):
        import services.local_asr as la
        monkeypatch.setattr(la, 'cancel_model_download', lambda: True)
        data = client.post('/api/asr/local/firered/cancel').json()
        assert data['cancel_requested'] is True

    def test_engine_delete_blocked_while_busy(self, client, monkeypatch):
        import services.local_asr as la
        monkeypatch.setattr(la, 'engine_ready', lambda k: True)
        monkeypatch.setattr(la, 'remove_engine',
                            lambda k: (_ for _ in ()).throw(Exception(f'{k} 正在转录中，请等待')))
        resp = client.delete('/api/asr/local/firered')
        assert resp.status_code == 409
        assert '转录中' in resp.json()['detail']

    def test_engine_delete_ok(self, client, monkeypatch):
        import services.local_asr as la
        monkeypatch.setattr(la, 'remove_engine',
                            lambda k: {'freed_bytes': 1_200_000_000, 'ready': False})
        data = client.delete('/api/asr/local/firered').json()
        assert data['success'] is True and data['freed_bytes'] == 1_200_000_000
        assert 'GB' in data['message']

    def test_engine_delete_conflict_when_downloading(self, client, monkeypatch):
        import services.local_asr as la

        def _boom(k):
            raise Exception('模型正在下载中，请先取消下载或等待完成再删除')
        monkeypatch.setattr(la, 'remove_engine', _boom)
        resp = client.delete('/api/asr/local/firered')
        assert resp.status_code == 409
        assert '下载中' in resp.json()['detail']

    def test_whisper_delete_unknown_size_400(self, client):
        assert client.delete('/api/asr/local/whisper/huge').status_code == 400

    def test_whisper_delete_not_installed_404(self, client, monkeypatch, tmp_path):
        from services import transcriber as tr
        monkeypatch.setitem(tr.LOCAL_MODEL_DIRS, 'base', tmp_path / 'not-there')
        resp = client.delete('/api/asr/local/whisper/base')
        assert resp.status_code == 404
        assert '无需删除' in resp.json()['detail']

    def test_whisper_delete_ok(self, client, monkeypatch, tmp_path):
        from services import transcriber as tr
        target = tmp_path / 'whisper_base'
        (target / 'sub').mkdir(parents=True)
        (target / 'model.bin').write_bytes(b'x' * 2048)
        (target / 'sub' / 'tokenizer.json').write_bytes(b'{}')
        monkeypatch.setitem(tr.LOCAL_MODEL_DIRS, 'base', target)
        data = client.delete('/api/asr/local/whisper/base').json()
        assert data['success'] is True
        assert data['freed_bytes'] > 2000
        assert not target.exists()

    def test_whisper_delete_evicts_model_cache(self, monkeypatch, tmp_path):
        """删除后必须清进程级模型缓存，否则「删了还在跑」被误判为删除无效。"""
        from services import transcriber as tr
        target = tmp_path / 'whisper_base'
        (target).mkdir(parents=True)
        (target / 'model.bin').write_bytes(b'x')
        monkeypatch.setitem(tr.LOCAL_MODEL_DIRS, 'base', target)
        with tr._model_cache_lock:
            tr._model_cache[(str(target), 'cpu', 'int8')] = object()
            tr._model_cache[('other', 'cpu', 'int8')] = object()
        try:
            tr.remove_local_model('base')
            assert (str(target), 'cpu', 'int8') not in tr._model_cache
            assert ('other', 'cpu', 'int8') in tr._model_cache
        finally:
            with tr._model_cache_lock:
                tr._model_cache.clear()


class TestLocalModelHelpers:
    def test_list_local_models_reports_ready_and_partial(self, monkeypatch, tmp_path):
        from services import transcriber as tr
        complete = tmp_path / 'complete'
        complete.mkdir()
        (complete / 'model.bin').write_bytes(b'x' * 11_000_000)
        (complete / 'tokenizer.json').write_text('{}')
        partial = tmp_path / 'partial'
        partial.mkdir()
        (partial / 'model.bin').write_bytes(b'x' * 10)
        monkeypatch.setattr(tr, 'LOCAL_MODEL_DIRS', {'base': complete, 'tiny': partial})
        out = {m['size']: m for m in tr.list_local_models()}
        assert out['base']['ready'] is True and out['base']['bytes'] > 11_000_000
        assert out['tiny']['ready'] is False and out['tiny']['partial'] is True

    def test_remove_local_model_unknown_size(self):
        from services import transcriber as tr
        with pytest.raises(ValueError):
            tr.remove_local_model('huge')


class TestFireredLifecycle:
    def test_model_file_status_reports_missing_parts(self, monkeypatch, tmp_path):
        import services.local_asr as la
        model_dir = tmp_path / 'firered'
        punct_dir = tmp_path / 'punct'
        vad = tmp_path / 'vad.onnx'
        monkeypatch.setattr(la, 'FIRERED_MODEL_DIR', model_dir)
        monkeypatch.setattr(la, 'FIRERED_PUNCT_MODEL_DIR', punct_dir)
        monkeypatch.setattr(la, 'FIRERED_VAD_MODEL_PATH', vad)
        status = la.model_file_status()
        assert status['ready'] is False
        assert {f['key'] for f in status['files']} == {'encoder', 'decoder', 'tokens', 'punct', 'vad'}
        assert all(f['ready'] is False for f in status['files'])
        assert status['bytes'] == 0

    def test_remove_model_files_frees_bytes(self, monkeypatch, tmp_path):
        import services.local_asr as la
        model_dir = tmp_path / 'firered'
        punct_dir = tmp_path / 'punct'
        vad = tmp_path / 'vad.onnx'
        monkeypatch.setattr(la, 'FIRERED_MODEL_DIR', model_dir)
        monkeypatch.setattr(la, 'FIRERED_PUNCT_MODEL_DIR', punct_dir)
        monkeypatch.setattr(la, 'FIRERED_VAD_MODEL_PATH', vad)
        model_dir.mkdir(parents=True)
        (model_dir / 'encoder.int8.onnx').write_bytes(b'x' * 512)
        (model_dir / 'decoder.int8.onnx').write_bytes(b'x' * 256)
        (model_dir / 'tokens.txt').write_bytes(b'x' * 16)
        punct_dir.mkdir(parents=True)
        (punct_dir / 'model.onnx').write_bytes(b'x' * 128)
        vad.write_bytes(b'x' * 8)
        assert la.firered_model_ready() is True

        result = la.remove_model_files()
        assert result['freed_bytes'] == 512 + 256 + 16 + 128 + 8
        assert result['ready'] is False
        assert not model_dir.exists() and not punct_dir.exists() and not vad.exists()

    def test_download_state_starts_idle(self, monkeypatch, tmp_path):
        import services.local_asr as la
        monkeypatch.setattr(la, '_download_state',
                            {'status': 'idle', 'percent': 0, 'message': '', 'error': ''})
        assert la.get_download_state()['status'] == 'idle'

    def test_download_thread_records_success(self, monkeypatch, tmp_path):
        import services.local_asr as la
        monkeypatch.setattr(la, '_download_state',
                            {'status': 'idle', 'percent': 0, 'message': '', 'error': ''})
        monkeypatch.setattr(la, '_download_cancel', __import__('threading').Event())
        monkeypatch.setattr(la, '_ensure_engine_ready',
                            lambda key, progress_callback=None, cancel_check=None:
                            progress_callback and progress_callback(100, '完成'))
        monkeypatch.setattr(la, 'firered_model_ready', lambda: True)
        assert la.start_model_download() is True
        la._download_thread.join(timeout=5)
        state = la.get_download_state()
        assert state['status'] == 'done' and state['percent'] == 100
        # 完成后（非 downloading）可再次触发，不会被 409 拦住
        assert la.start_model_download() is True
        la._download_thread.join(timeout=5)

    def test_download_thread_records_failure(self, monkeypatch):
        import services.local_asr as la
        import threading
        monkeypatch.setattr(la, '_download_state',
                            {'status': 'idle', 'percent': 0, 'message': '', 'error': ''})
        monkeypatch.setattr(la, '_download_cancel', threading.Event())

        def _boom(key, progress_callback=None, cancel_check=None):
            raise Exception('网络不通')
        monkeypatch.setattr(la, '_ensure_engine_ready', _boom)
        assert la.start_model_download() is True
        la._download_thread.join(timeout=5)
        state = la.get_download_state()
        assert state['status'] == 'error'
        assert '网络不通' in state['error']

    def test_start_download_rejected_while_downloading(self, monkeypatch):
        import services.local_asr as la
        monkeypatch.setattr(la, '_download_state',
                            {'status': 'downloading', 'percent': 10, 'message': '', 'error': ''})
        assert la.start_model_download() is False

    def test_remove_model_files_refused_while_downloading(self, monkeypatch):
        import services.local_asr as la
        # 下载态带 engine 字段：只有「正在下载的就是这个引擎」才拒绝删除
        monkeypatch.setattr(la, '_download_state',
                            {'status': 'downloading', 'engine': 'firered',
                             'percent': 10, 'message': '', 'error': ''})
        with pytest.raises(Exception) as ei:
            la.remove_model_files()
        assert '下载' in str(ei.value)
