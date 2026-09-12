"""
main.py process_job + token API 测试
mock socketio import 前解决副作用
"""
import pytest, sys, asyncio, os
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path


@pytest.fixture(autouse=True)
def _mock_socketio(monkeypatch):
    """mock socketio 模块，测试结束后自动恢复（避免污染后续测试的 sys.modules）"""
    mock_sio = MagicMock()
    mock_sio.AsyncServer = MagicMock()
    mock_sio.ASGIApp = MagicMock()
    monkeypatch.setitem(sys.modules, 'socketio', mock_sio)
    yield


# ── 辅助：正确 mock 服务层函数 ──
SVC = "services."
MOCK_AI = f"{SVC}ai_processor.ai_process"
MOCK_HANDLE = f"{SVC}downloader.handle_text_input"
MOCK_TRANS_SUB = f"{SVC}ai_processor.translate_subtitles"
MOCK_TAGS = f"{SVC}ai_processor.generate_tags"
MOCK_DOWNLOAD = f"{SVC}downloader.download_video"
MOCK_TRANSCRIBE = f"{SVC}transcriber.transcribe_audio"
MOCK_YT_SUB = f"{SVC}downloader.extract_youtube_subtitles"
MOCK_DETECT = f"{SVC}downloader.detect_platform"


class TestProcessJob:
    def _make_job(self, **overrides):
        from db.job_repo import create_job
        return create_job(**{**{"input_type": "text", "input_text": "x"}, **overrides})

    def _base_opts(self, **kw):
        return {**{"input_type":"text","do_summary":False,"do_translate":False,
            "do_subtitle":False,"preview_before_summary":False}, **kw}

    # ── text 路径 ──
    def test_text_no_summary(self):
        from main import process_job
        jid = self._make_job()
        with patch("main.get_job") as mj, patch("main.update_job"), \
             patch("main.finish_job") as mf, \
             patch(MOCK_HANDLE, return_value=(Path("/t.txt"),Path("/t.srt"))), \
             patch("socket_handlers.emit_log", new_callable=AsyncMock), \
             patch("socket_handlers.emit_progress", new_callable=AsyncMock):
            mj.return_value = {"id":jid,"model":"glm","summary_type":"s",
                "input_url":"","input_text":"x","input_file":"",
                "options":self._base_opts()}
            asyncio.run(process_job(jid))
            mf.assert_called()

    def test_text_with_summary(self):
        from main import process_job
        jid = self._make_job()
        with patch("main.get_job") as mj, patch("main.update_job"), \
             patch("main.finish_job") as mf, \
             patch(MOCK_HANDLE, return_value=(Path("/t.txt"),Path("/t.srt"))), \
             patch(MOCK_AI, new_callable=AsyncMock) as mai, \
             patch(MOCK_TAGS, new_callable=AsyncMock) as mtg, \
             patch("socket_handlers.emit_ai_token", new_callable=AsyncMock), \
             patch("socket_handlers.emit_log", new_callable=AsyncMock), \
             patch("socket_handlers.emit_progress", new_callable=AsyncMock), \
             patch("main.open", MagicMock()):
            mj.return_value = {"id":jid,"model":"glm","summary_type":"s",
                "input_url":"","input_text":"x","input_file":"","title":"t",
                "options":self._base_opts(do_summary=True),
                "files":{},"tags":[],"token_usage":{}}
            mai.return_value = ("AI result",10,20)
            mtg.return_value = ["t1"]
            asyncio.run(process_job(jid))
            mai.assert_called_once()
            mf.assert_called()

    def test_text_preview(self):
        from main import process_job
        jid = self._make_job()
        with patch("main.get_job") as mj, patch("main.update_job") as mu, \
             patch("main.finish_job") as mf, \
             patch(MOCK_HANDLE, return_value=(Path("/t.txt"),Path("/t.srt"))), \
             patch(MOCK_AI, new_callable=AsyncMock) as mai, \
             patch(MOCK_TAGS, new_callable=AsyncMock), \
             patch("socket_handlers.emit_preview", new_callable=AsyncMock), \
             patch("socket_handlers.emit_ai_token", new_callable=AsyncMock), \
             patch("socket_handlers.set_preview_event", side_effect=lambda _jid, event: event.set()), \
             patch("socket_handlers.pop_preview_event"), \
             patch("main.emit_log", new_callable=AsyncMock), \
             patch("main.emit_progress", new_callable=AsyncMock), \
             patch("main.open", MagicMock()):
            mj.return_value = {"id":jid,"model":"glm","summary_type":"s",
                "input_url":"","input_text":"x","input_file":"",
                "options":self._base_opts(do_summary=True,preview_before_summary=True),
                "files":{},"tags":[],"token_usage":{}}
            mai.return_value = ("AI result", 10, 20)
            asyncio.run(process_job(jid))
            mu.assert_called()
            mf.assert_called()

    def test_text_with_subtitle_translate(self):
        from main import process_job
        jid = self._make_job()
        with patch("main.get_job") as mj, patch("main.update_job"), \
             patch("main.finish_job") as mf, \
             patch(MOCK_HANDLE, return_value=(Path("/t.txt"),Path("/t.srt"))), \
             patch(MOCK_AI, new_callable=AsyncMock) as mai, \
             patch(MOCK_TRANS_SUB, new_callable=AsyncMock) as mts, \
             patch(MOCK_TAGS, new_callable=AsyncMock), \
             patch("socket_handlers.emit_ai_token", new_callable=AsyncMock), \
             patch("socket_handlers.emit_log", new_callable=AsyncMock), \
             patch("socket_handlers.emit_progress", new_callable=AsyncMock), \
             patch("main.open", MagicMock()):
            mj.return_value = {"id":jid,"model":"glm","summary_type":"s",
                "input_url":"","input_text":"x","input_file":"","title":"t",
                "options":self._base_opts(do_summary=True,do_translate=True,do_subtitle=True,**{"translate_lang":"en"}),
                "files":{},"tags":[],"token_usage":{}}
            mai.return_value = ("AI",10,20)
            mts.return_value = "/tmp/tr.srt"
            asyncio.run(process_job(jid))
            mts.assert_called_once()
            mf.assert_called()

    def test_translate_runs_without_srt_and_prefers_txt_clean(self, tmp_path):
        """全文翻译只依赖 txt：srt 缺失不阻断；输入优先 txt_clean 纯净版（无时间戳/说话人标记）"""
        from main import process_job
        jid = self._make_job()
        txt = tmp_path / "t.txt"
        txt.write_text("[00:00 - 00:05] Hello world[说话人1]\n[00:05 - 00:10] Second line\n",
                       encoding="utf-8")
        with patch("main.get_job") as mj, patch("main.update_job"), \
             patch("main.finish_job") as mf, \
             patch(MOCK_HANDLE, return_value=(txt, "")), \
             patch(MOCK_AI, new_callable=AsyncMock) as mai, \
             patch("socket_handlers.emit_log", new_callable=AsyncMock), \
             patch("socket_handlers.emit_progress", new_callable=AsyncMock):
            mj.return_value = {"id":jid,"model":"glm","summary_type":"s",
                "input_url":"","input_text":"x","input_file":"","title":"t",
                "options":self._base_opts(do_translate=True, **{"translate_target_lang":"en"}),
                "files":{},"tags":[],"token_usage":{}}
            mai.return_value = ("AI 译文",10,20)
            asyncio.run(process_job(jid))
            mai.assert_called_once()  # 无 srt 仍执行全文翻译
            clean_text = mai.call_args[0][0]
            assert "Hello world" in clean_text
            assert "[" not in clean_text  # 时间戳/说话人标记已被剥掉（txt_clean 优先）
            # 统一语言参数生效：译文落 __en.md
            out = tmp_path / "t__en.md"
            assert out.exists()
            assert out.read_text(encoding="utf-8") == "AI 译文"
            mf.assert_called()

    def test_subtitle_translate_uses_unified_target_lang(self):
        """字幕翻译语言统一走 translate_target_lang（新入口）"""
        from main import process_job
        jid = self._make_job()
        with patch("main.get_job") as mj, patch("main.update_job"), \
             patch("main.finish_job") as mf, \
             patch(MOCK_HANDLE, return_value=(Path("/t.txt"), Path("/t.srt"))), \
             patch(MOCK_TRANS_SUB, new_callable=AsyncMock) as mts, \
             patch("socket_handlers.emit_log", new_callable=AsyncMock), \
             patch("socket_handlers.emit_progress", new_callable=AsyncMock):
            mj.return_value = {"id":jid,"model":"glm","summary_type":"s",
                "input_url":"","input_text":"x","input_file":"","title":"t",
                "options":self._base_opts(do_subtitle=True, **{"translate_target_lang":"en"}),
                "files":{},"tags":[],"token_usage":{}}
            mts.return_value = "/tmp/tr.srt"
            asyncio.run(process_job(jid))
            mts.assert_called_once()
            assert mts.call_args.kwargs["target_lang"] == "en"
            mf.assert_called()

    def test_subtitle_translate_legacy_lang_override(self):
        """历史兼容：显式 subtitle_target_lang 仍优先生效（旧任务回放不变）"""
        from main import process_job
        jid = self._make_job()
        with patch("main.get_job") as mj, patch("main.update_job"), \
             patch("main.finish_job") as mf, \
             patch(MOCK_HANDLE, return_value=(Path("/t.txt"), Path("/t.srt"))), \
             patch(MOCK_TRANS_SUB, new_callable=AsyncMock) as mts, \
             patch("socket_handlers.emit_log", new_callable=AsyncMock), \
             patch("socket_handlers.emit_progress", new_callable=AsyncMock):
            mj.return_value = {"id":jid,"model":"glm","summary_type":"s",
                "input_url":"","input_text":"x","input_file":"","title":"t",
                "options":self._base_opts(do_subtitle=True,
                    **{"translate_target_lang":"en","subtitle_target_lang":"ja"}),
                "files":{},"tags":[],"token_usage":{}}
            mts.return_value = "/tmp/tr.srt"
            asyncio.run(process_job(jid))
            mts.assert_called_once()
            assert mts.call_args.kwargs["target_lang"] == "ja"
            mf.assert_called()

    # ── URL 路径 ──
    def test_url_download_transcribe(self):
        from main import process_job
        jid = self._make_job(input_type="url", input_url="https://youtube.com/watch?v=test")
        with patch("main.get_job") as mj, patch("main.update_job"), \
             patch("main.finish_job") as mf, \
             patch(MOCK_DOWNLOAD, new_callable=AsyncMock) as mdl, \
             patch(MOCK_TRANSCRIBE, new_callable=AsyncMock) as mtr, \
             patch(MOCK_AI, new_callable=AsyncMock) as mai, \
             patch(MOCK_TAGS, new_callable=AsyncMock), \
             patch(MOCK_DETECT, return_value=("YouTube",True)), \
             patch(MOCK_YT_SUB, new_callable=AsyncMock) as mys, \
             patch("socket_handlers.emit_ai_token", new_callable=AsyncMock), \
             patch("socket_handlers.emit_log", new_callable=AsyncMock), \
             patch("socket_handlers.emit_progress", new_callable=AsyncMock), \
             patch("main.open", MagicMock()):
            mys.return_value = {"success": False}
            mj.return_value = {"id":jid,"model":"glm","summary_type":"s",
                "input_url":"https://youtube.com/watch?v=test","input_text":"","input_file":"","title":"t",
                "options":self._base_opts(input_type="url",do_summary=True),
                "files":{},"tags":[],"token_usage":{}}
            mdl.return_value = ("/tmp/a.m4a","Test","YouTube")
            mtr.return_value = ("/tmp/o.srt","/tmp/o.txt",5)
            mai.return_value = ("Summary",10,20)
            asyncio.run(process_job(jid))
            mdl.assert_called_once()
            mtr.assert_called_once()
            mf.assert_called()

    def test_url_yt_subtitles(self):
        from main import process_job
        jid = self._make_job(input_type="url", input_url="https://youtube.com/watch?v=test")
        with patch("main.get_job") as mj, patch("main.update_job"), \
             patch("main.finish_job") as mf, \
             patch(MOCK_AI, new_callable=AsyncMock) as mai, \
             patch(MOCK_TAGS, new_callable=AsyncMock), \
             patch(MOCK_DOWNLOAD, new_callable=AsyncMock) as mdl, \
             patch(MOCK_TRANSCRIBE, new_callable=AsyncMock) as mtr, \
             patch(MOCK_DETECT, return_value=("YouTube",True)), \
             patch(MOCK_YT_SUB, new_callable=AsyncMock) as mys, \
             patch("socket_handlers.emit_ai_token", new_callable=AsyncMock), \
             patch("socket_handlers.emit_log", new_callable=AsyncMock), \
             patch("socket_handlers.emit_progress", new_callable=AsyncMock), \
             patch("main.open", MagicMock()):
            mys.return_value = {"success":True,"srt_path":"/tmp/s.srt","txt_path":"/tmp/s.txt","title":"YT"}
            mj.return_value = {"id":jid,"model":"glm","summary_type":"s",
                "input_url":"https://youtube.com/watch?v=test","input_text":"","input_file":"","title":"t",
                "options":self._base_opts(input_type="url",do_summary=True),
                "files":{},"tags":[],"token_usage":{}}
            mai.return_value = ("Summary",10,20)
            asyncio.run(process_job(jid))
            mdl.assert_not_called()  # 字幕成功应跳过下载
            mtr.assert_not_called()
            mf.assert_called()

    def test_cancelled(self):
        from main import process_job
        jid = self._make_job()
        with patch("main.get_job") as mj, patch("main.update_job"), \
             patch("main.finish_job") as mf, \
             patch(MOCK_HANDLE, side_effect=Exception("cancelled")), \
             patch("socket_handlers.emit_log", new_callable=AsyncMock), \
             patch("socket_handlers.emit_progress", new_callable=AsyncMock):
            mj.return_value = {"id":jid,"model":"glm","summary_type":"s",
                "input_url":"","input_text":"x","input_file":"",
                "options":self._base_opts()}
            asyncio.run(process_job(jid))
            mf.assert_called_once()
            assert mf.call_args[0][1] is False


class TestMainUtils:
    def test_get_semaphore(self):
        import main as m
        m._job_semaphore = None
        s = m.get_job_semaphore()
        assert s is not None

    @pytest.mark.asyncio
    async def test_start_job_is_idempotent_for_live_task(self):
        import main as m
        gate = asyncio.Event()

        async def fake_process(_job_id):
            await gate.wait()

        m.RUNNING_JOBS.clear()
        try:
            with patch("main.process_job", side_effect=fake_process) as mock_process:
                first = m.start_job("j1")
                second = m.start_job("j1")
                assert second is first
                assert mock_process.call_count == 1
                gate.set()
                await first
        finally:
            m.RUNNING_JOBS.clear()

    @pytest.mark.asyncio
    async def test_cancelled_queued_job_exits_without_acquiring_slot(self):
        import main as m
        from socket_handlers import check_cancelled, request_cancel

        m.RUNNING_JOBS.clear()
        m._job_semaphore = asyncio.Semaphore(0)
        request_cancel("queued")
        try:
            with patch("main.update_job") as mock_update:
                await m.process_job("queued")

            mock_update.assert_called_once_with("queued", status="cancelled", error="用户取消")
            assert m._job_semaphore._value == 0
            assert check_cancelled("queued") is False
            assert "queued" not in m.RUNNING_JOBS
        finally:
            m.RUNNING_JOBS.clear()
            m._job_semaphore = None


class TestBatchProgressEmit:
    """batch_progress 推送：批量子任务终态后经 process_job finally 单一出口触发"""

    @pytest.mark.asyncio
    async def test_emit_batch_progress_for_batch_job(self):
        import main as m

        fake_job = {"id": "j1", "batch_id": "b1"}
        fake_summary = {"batch_id": "b1", "total": 2, "completed": 1}
        emitted = []

        def fake_emit(event, *args, **kwargs):
            emitted.append((event, args))

        with patch("db.job_repo.get_job", return_value=fake_job), \
             patch("db.job_repo.get_batch_summary", return_value=fake_summary), \
             patch.object(m.sio, "emit", side_effect=fake_emit):
            await m._emit_batch_progress("j1")

        assert emitted == [("batch_progress", (fake_summary,))]

    @pytest.mark.asyncio
    async def test_emit_batch_progress_skips_non_batch_job(self):
        import main as m
        with patch("db.job_repo.get_job", return_value={"id": "j2"}), \
             patch.object(m.sio, "emit") as mock_emit:
            await m._emit_batch_progress("j2")
        mock_emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_emit_batch_progress_tolerates_db_error(self):
        import main as m
        # get_job 抛异常不得外抛：finally 单一出口不容失败
        with patch("db.job_repo.get_job", side_effect=RuntimeError("db down")):
            await m._emit_batch_progress("j3")

    @pytest.mark.asyncio
    async def test_process_job_finally_emits_batch_progress(self):
        import main as m
        from socket_handlers import request_cancel

        m.RUNNING_JOBS.clear()
        m._job_semaphore = asyncio.Semaphore(0)
        request_cancel("batchjob")
        try:
            with patch("main.update_job"), \
                 patch("db.job_repo.get_job", return_value={"id": "batchjob", "batch_id": "b1"}), \
                 patch("db.job_repo.get_batch_summary", return_value={"batch_id": "b1"}), \
                 patch.object(m.sio, "emit", return_value=None) as mock_emit:
                await m.process_job("batchjob")
            events = [c.args[0] for c in mock_emit.call_args_list]
            assert "batch_progress" in events
        finally:
            m.RUNNING_JOBS.clear()
            m._job_semaphore = None


class TestTokenEndpoints:
    @pytest.fixture
    def app(self):
        from fastapi import FastAPI
        app = FastAPI()
        from main import get_token_usage_history, get_token_usage
        app.get("/api/token-usage/history")(get_token_usage_history)
        app.get("/api/token-usage")(get_token_usage)
        return app

    def test_history(self, app):
        from fastapi.testclient import TestClient
        c = TestClient(app)
        mc = MagicMock()
        mc.execute.return_value.fetchall.return_value = []
        with patch("main.get_connection", return_value=mc):
            r = c.get("/api/token-usage/history")
            assert r.status_code == 200

    def test_usage(self, app):
        from fastapi.testclient import TestClient
        c = TestClient(app)
        mc = MagicMock()
        mcur = MagicMock()
        mc.cursor.return_value = mcur
        mcur.fetchone.return_value = [100, 200]
        mcur.fetchall.return_value = []
        with patch("main.get_connection", return_value=mc):
            r = c.get("/api/token-usage")
            assert r.status_code == 200


class TestProxyBypass:
    """PROXY_BYPASS_HOSTS → NO_PROXY 合并（中转域名代理隧道不通时直连）"""

    def test_merge_no_proxy_dedup_keep_order(self):
        import main as m
        assert m.merge_no_proxy('', ['a.com']) == 'a.com'
        assert m.merge_no_proxy('localhost, a.com', ['a.com', 'b.com']) == 'localhost,a.com,b.com'
        assert m.merge_no_proxy('x', ['', '  ', None]) == 'x'
        assert m.merge_no_proxy(None, []) == ''

    def test_apply_proxy_bypass_hosts_merges_env(self, monkeypatch):
        # Windows 的 os.environ 对大小写不敏感（NO_PROXY/no_proxy 同键），勿做跨写法删除断言
        import main as m
        monkeypatch.setenv('NO_PROXY', 'localhost,127.0.0.1')
        monkeypatch.setattr(m.config, 'PROXY_BYPASS_HOSTS', ['relay.example.com', 'relay.example.com'])
        m.apply_proxy_bypass_hosts()
        assert 'localhost,127.0.0.1' in os.environ['NO_PROXY']
        assert 'relay.example.com' in os.environ['NO_PROXY']

    def test_apply_proxy_bypass_hosts_noop_when_empty(self, monkeypatch):
        import main as m
        monkeypatch.setenv('NO_PROXY', 'localhost')
        monkeypatch.setattr(m.config, 'PROXY_BYPASS_HOSTS', [])
        m.apply_proxy_bypass_hosts()
        assert os.environ['NO_PROXY'] == 'localhost'
