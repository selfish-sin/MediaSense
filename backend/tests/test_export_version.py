"""历史版本导出/思维导图跟随当前查看版本（单测，2026-10-11）

起因：历史详情页可以切到任意总结版本看正文，但**导出与思维导图永远走 jobs.result_text（最新版）**——
用户停在 v1 界面，点导出拿到的却是 v2，文档信息里的模型/标签也是 v2 的，
于是"点了版本、下面看到的内容没跟着变"。

覆盖：
1. `_load_job` 的版本视图——正文/总结类型/模型/标签/summary_version 全部取自 summary_history
2. 不传版本 = 原行为（最新版）；传当前版本号 = 等同不传（两条路径都要能用）
3. 版本不存在 / 该版本正文为空 → 显式 404/400，不静默回落到最新版
4. 下载文件名带 vN 后缀（否则连下两次同名文件，浏览器容易当成同一个）
5. 思维导图按 body.version 取对应版本正文
"""

import pytest

from models.schemas import ExportRequest
from routers import export as export_router


def _fake_job(job_id='j1'):
    return {
        'id': job_id,
        'title': '测试任务',
        'summary_version': 2,
        'summary_type': 'summary',
        'model': 'model-v2',
        'tags': ['tag-v2'],
        'result_text': 'V2 正文',
        'created_at': '2026-10-11 00:00:00',
    }


def _fake_versions(job_id, version):
    return {
        1: {'version': 1, 'summary_type': 'summary', 'model': 'model-v1',
            'result_text': 'V1 正文', 'tags': ['tag-v1'], 'created_at': '2026-10-10 00:00:00'},
    }.get(version)


@pytest.fixture
def job_with_versions(db_path, monkeypatch):
    """造一个「已重新总结一次」的任务：当前 v2，历史 v1"""
    monkeypatch.setattr("routers.export.get_job", lambda jid: _fake_job(jid))
    monkeypatch.setattr("routers.export.get_summary_version", _fake_versions)
    return _fake_job('j1')


class TestExportVersion:
    def test_no_version_returns_latest(self, job_with_versions):
        out = export_router._load_job(ExportRequest(job_id='j1', format='txt'))
        assert out['result_text'] == 'V2 正文'
        assert out['_export_version'] is None

    def test_current_version_equivalent_to_omitted(self, job_with_versions):
        """前端可能传当前版本号（正停在 v2），后端必须等同不传，不能去查历史表"""
        out = export_router._load_job(ExportRequest(job_id='j1', format='txt', version=2))
        assert out['result_text'] == 'V2 正文'
        assert out['_export_version'] is None

    def test_history_version_overrides_everything(self, job_with_versions):
        """核心：切到 v1 后，导出件里的一切都必须来自 v1，不能混进 v2 的模型/标签"""
        out = export_router._load_job(ExportRequest(job_id='j1', format='docx', version=1))
        assert out['result_text'] == 'V1 正文'
        assert out['model'] == 'model-v1'
        assert out['tags'] == ['tag-v1']
        assert out['summary_version'] == 1
        assert out['_export_version'] == 1

    def test_original_job_dict_not_mutated(self, job_with_versions):
        """返回的是新 dict：就地改会污染共享 job 缓存/其它请求"""
        export_router._load_job(ExportRequest(job_id='j1', format='txt', version=1))
        fresh = _fake_job()
        assert fresh['result_text'] == 'V2 正文' and fresh['model'] == 'model-v2'

    def test_missing_version_404(self, job_with_versions):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            export_router._load_job(ExportRequest(job_id='j1', format='txt', version=99))
        assert ei.value.status_code == 404

    def test_empty_history_version_400(self, job_with_versions, monkeypatch):
        from fastapi import HTTPException
        monkeypatch.setattr("routers.export.get_summary_version",
                            lambda jid, v: {'version': v, 'result_text': '   '})
        with pytest.raises(HTTPException) as ei:
            export_router._load_job(ExportRequest(job_id='j1', format='txt', version=1))
        assert ei.value.status_code == 400

    def test_job_without_result_text_400(self, monkeypatch):
        from fastapi import HTTPException
        monkeypatch.setattr("routers.export.get_job", lambda jid: {'id': jid, 'result_text': ''})
        with pytest.raises(HTTPException) as ei:
            export_router._load_job(ExportRequest(job_id='j1', format='txt'))
        assert ei.value.status_code == 400


class TestExportFileName:
    def test_history_version_gets_suffix(self, monkeypatch, tmp_path):
        """v1 / v2 必须落不同文件名，否则两次下载同名文件容易拿到上一次的内容"""
        monkeypatch.setattr(export_router, 'EXPORT_DIR', tmp_path)
        _, n1 = export_router._paths({**_fake_job(), '_export_version': 1}, 'txt')
        p2, n2 = export_router._paths({**_fake_job(), '_export_version': None}, 'txt')
        assert n1.endswith('_v1')
        assert '_v1' not in n2
        assert p2.name == f"{n2}_{'j1'[:8]}.txt"


@pytest.mark.anyio
class TestMindmapVersion:
    async def test_mindmap_uses_requested_version_text(self, monkeypatch):
        """页面停在 v1 时生成的导图必须基于 v1 正文"""
        from routers import jobs_ai
        monkeypatch.setattr("routers.jobs_ai.get_job", lambda jid: _fake_job(jid))
        monkeypatch.setattr("routers.jobs_ai.get_summary_version", _fake_versions)

        seen = {}

        async def fake_generate_mindmap(result_text=None, **kwargs):
            seen['text'] = result_text
            return '# mm'

        import services.ai_processor as ap
        monkeypatch.setattr(ap, 'generate_mindmap', fake_generate_mindmap)

        res = await jobs_ai.generate_job_mindmap('j1', {'version': 1})
        assert seen['text'] == 'V1 正文'
        assert res.success is True

    async def test_mindmap_without_version_keeps_latest(self, monkeypatch):
        from routers import jobs_ai
        monkeypatch.setattr("routers.jobs_ai.get_job", lambda jid: _fake_job(jid))
        monkeypatch.setattr("routers.jobs_ai.get_summary_version", _fake_versions)

        seen = {}

        async def fake_generate_mindmap(result_text=None, **kwargs):
            seen['text'] = result_text
            return '# mm'

        import services.ai_processor as ap
        monkeypatch.setattr(ap, 'generate_mindmap', fake_generate_mindmap)

        await jobs_ai.generate_job_mindmap('j1', {})
        assert seen['text'] == 'V2 正文'

    async def test_mindmap_missing_version_404(self, monkeypatch):
        from fastapi import HTTPException
        from routers import jobs_ai
        monkeypatch.setattr("routers.jobs_ai.get_job", lambda jid: _fake_job(jid))
        monkeypatch.setattr("routers.jobs_ai.get_summary_version", _fake_versions)
        with pytest.raises(HTTPException) as ei:
            await jobs_ai.generate_job_mindmap('j1', {'version': 99})
        assert ei.value.status_code == 404


@pytest.fixture
def anyio_backend():
    return 'asyncio'
