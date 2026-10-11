"""模型连接体检 + 失败分类（单测，2026-10-11）

覆盖三块：
1. `_classify_failure` 纯函数分类——401/403/404/429/网络/证书各自归到正确的 kind，
   这是"连不上"能被用户看懂的前提（历史上所有失败一律叫「连接失败」）。
2. `_probe_model` 探测契约——成功取正文；失败给 kind/hint；自签名证书只在**重试成功时**
   才持久化 skip_ssl_verify（重试仍失败却写标记，会把一个纯 401 端点永久标成跳过证书校验）。
3. `/api/models/health-check` 批量端点——并发探测、no_key 不发请求、
   失败项标出是否还挂在兜底环里（坏模型留在环里会被反复撞）。

全部打桩 httpx，不发真实网络请求。
"""

import json
import sqlite3

import httpx
import pytest

from db import model_repo
from routers import models as models_router
from routers.models import _classify_failure, _probe_model


def _conn(path):
    """每次调用返回一个新连接到该临时 DB 的连接（与 model_repo 的用法一致）"""
    c = sqlite3.connect(str(path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


class _FakeResp:
    """最小 httpx.Response 替身：只需 status_code / text / json()"""

    def __init__(self, status_code, text=''):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


class _FakeClient:
    """按脚本回放响应/异常，并记录每次请求的 verify 取值"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append({'url': url, 'verify': getattr(self, '_verify', None),
                           'payload': json})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _patch_client(monkeypatch, script):
    """把 httpx.AsyncClient 换成可回放的假客户端；verify 记进 calls"""
    client = _FakeClient(script)

    def _factory(*args, **kwargs):
        client._verify = kwargs.get('verify')
        return client

    monkeypatch.setattr(models_router.httpx, 'AsyncClient', _factory)
    return client


_OK = _FakeResp(200, json.dumps({'choices': [{'message': {'content': 'pong'}}]}))


# ---------------------------------------------------------------- 分类器

class TestClassifyFailure:
    @pytest.mark.parametrize('status,detail,kind', [
        (401, 'HTTP 401: Incorrect API key provided', 'auth'),
        (403, 'HTTP 403: 无权访问 高速5.6 分组', 'forbidden'),
        (403, 'HTTP 403: real-name verification is required for your free step plan',
         'forbidden_realname'),
        (404, 'HTTP 404: model is not found', 'model'),
        (404, 'HTTP 404: Not Found', 'endpoint'),
        (400, 'HTTP 400: invalid model id', 'model'),
        (422, 'HTTP 422: unprocessable', 'bad_request'),
        (429, 'HTTP 429: inference exceeds tpm/rpm limit', 'rate_limit'),
        (429, 'HTTP 429: insufficient balance', 'quota'),
        (402, 'HTTP 402: payment required', 'quota'),
        (500, 'HTTP 500: upstream exploded', 'server'),
        (503, 'HTTP 503: no available account', 'server'),
        (None, 'CERTIFICATE_VERIFY_FAILED', 'tls'),
        (None, 'All connection attempts failed', 'network'),
    ])
    def test_kind_mapping(self, status, detail, kind):
        assert _classify_failure(status, detail)[0] == kind

    def test_hint_is_actionable_not_empty(self):
        """每类都必须给出可行动的修法——只有类别没有出路等于没分类"""
        for status, detail in [
                (401, 'x'), (403, '无权访问'), (404, 'model is not found'),
                (429, 'tpm limit'), (None, 'All connection attempts failed')]:
            kind, hint = _classify_failure(status, detail)
            assert kind and hint
            assert len(hint) > 8

    def test_unknown_keeps_raw_detail(self):
        """判不出来时不能吞错：原文必须带出去"""
        kind, hint = _classify_failure(418, "I'm a teapot")
        assert kind == 'unknown'
        assert "I'm a teapot" in hint


# ---------------------------------------------------------------- 探测契约

@pytest.mark.anyio
class TestProbeModel:
    async def test_success_returns_content(self, monkeypatch):
        _patch_client(monkeypatch, [_OK])
        r = await _probe_model({'key': 'm1', 'api_key': 'sk-x', 'endpoint': 'https://x/v1/chat/completions'})
        assert r['ok'] is True
        assert r['content'] == 'pong'
        assert r['kind'] == 'ok'

    async def test_failure_carries_kind_and_hint(self, monkeypatch):
        _patch_client(monkeypatch, [_FakeResp(403, '无权访问 分组')])
        r = await _probe_model({'key': 'm1', 'api_key': 'sk-x', 'endpoint': 'https://x/v1/chat/completions'})
        assert r['ok'] is False
        assert r['kind'] == 'forbidden'
        assert r['reason'] and r['hint']
        assert r['status'] == 403

    async def test_network_error_is_network_kind(self, monkeypatch):
        _patch_client(monkeypatch, [httpx.ConnectError('All connection attempts failed')])
        r = await _probe_model({'key': 'm1', 'api_key': 'sk-x', 'endpoint': 'https://x/v1/chat/completions'})
        assert r['ok'] is False
        assert r['kind'] == 'network'

    async def test_health_probe_uses_small_max_tokens(self, monkeypatch):
        """体检默认 max_tokens 很小：探测只为验链路，不能把推理型模型的额度烧掉"""
        client = _patch_client(monkeypatch, [_OK])
        await _probe_model({'key': 'm1', 'api_key': 'sk-x', 'endpoint': 'https://x/v1/chat/completions'})
        assert client.calls[0]['payload']['max_tokens'] == 16

    async def test_skip_ssl_verify_persisted_only_when_bypass_succeeds(self, monkeypatch, db_path):
        """自签名：关校验后通了才写标记"""
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(db_path))
        model_repo.add_model(key='m1', name='M1', endpoint='https://x/v1/chat/completions', api_key='sk-x')
        _patch_client(monkeypatch, [httpx.ConnectError('CERTIFICATE_VERIFY_FAILED'), _OK])
        r = await _probe_model({'key': 'm1', 'api_key': 'sk-x', 'endpoint': 'https://x/v1/chat/completions'})
        assert r['ok'] is True
        assert r['ssl_bypassed'] is True
        assert model_repo.get_model_by_key('m1')['skip_ssl_verify'] == 1

    async def test_no_ssl_flag_when_bypass_still_fails(self, monkeypatch, db_path):
        """★ 关校验重试仍失败时**不能**写 skip_ssl_verify：
        否则一个纯 401 的端点被永久标成「跳过证书校验」，真实原因被永久掩盖"""
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(db_path))
        model_repo.add_model(key='m1', name='M1', endpoint='https://x/v1/chat/completions', api_key='sk-x')
        _patch_client(monkeypatch, [
            httpx.ConnectError('CERTIFICATE_VERIFY_FAILED'),
            _FakeResp(401, 'Incorrect API key'),
        ])
        r = await _probe_model({'key': 'm1', 'api_key': 'sk-x', 'endpoint': 'https://x/v1/chat/completions'})
        assert r['ok'] is False
        assert r['kind'] == 'auth'
        assert model_repo.get_model_by_key('m1')['skip_ssl_verify'] == 0


# ---------------------------------------------------------------- 批量体检端点

@pytest.mark.anyio
class TestHealthCheckEndpoint:
    async def test_reports_every_model_with_summary(self, monkeypatch, db_path):
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(db_path))
        model_repo.add_model(key='good', name='G', endpoint='https://x/v1/chat/completions', api_key='sk-x')
        model_repo.add_model(key='bad', name='B', endpoint='https://x/v1/chat/completions', api_key='sk-y')
        _patch_client(monkeypatch, [_OK, _FakeResp(404, 'model is not found')])

        res = await models_router.health_check_models()
        by_key = {r['key']: r for r in res['results']}
        assert set(by_key) == {'good', 'bad'}
        assert by_key['good']['ok'] is True
        assert by_key['bad']['kind'] == 'model'
        assert res['summary'] == {'total': 2, 'ok': 1, 'failed': 1, 'hard_failed': 1,
                                  'failed_in_ring': 0}

    async def test_flag_failed_models_still_in_fallback_ring(self, monkeypatch, db_path):
        """坏模型留在兜底环 = 每次换模型都白撞一次，是"又连不上"的放大器，必须标出来"""
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(db_path))
        model_repo.add_model(key='good', name='G', endpoint='https://x/v1/chat/completions', api_key='sk-x')
        model_repo.add_model(key='bad', name='B', endpoint='https://x/v1/chat/completions', api_key='sk-y')
        model_repo.set_fallback_chain(['good', 'bad'], enabled=True)
        _patch_client(monkeypatch, [_OK, _FakeResp(403, '无权访问 分组')])

        res = await models_router.health_check_models()
        by_key = {r['key']: r for r in res['results']}
        assert by_key['bad']['in_fallback_ring'] is True
        assert res['summary']['failed_in_ring'] == 1

    async def test_missing_key_is_skipped_without_request(self, monkeypatch, db_path):
        """没配密钥的模型不发请求（否则白拿一次 401），直接标 no_key"""
        monkeypatch.setattr("db.model_repo.get_connection", lambda: _conn(db_path))
        model_repo.add_model(key='nokey', name='N', endpoint='https://x/v1/chat/completions', api_key='')
        client = _patch_client(monkeypatch, [])

        res = await models_router.health_check_models()
        assert res['results'][0]['kind'] == 'no_key'
        assert client.calls == []


@pytest.fixture
def anyio_backend():
    return 'asyncio'
