"""文件命名单点 + 导出/下载文件名契约测试。

验收口径：导出与下载的文件名必须「视频名优先、保留可读标点」，不再出现
`AI总结_<job_id>`（零信息量）或磁盘名（audio.m4a / bilibili_subtitle.srt）。
"""
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.naming import export_basename, extract_video_id, safe_basename, derived_sibling


# ============ 纯函数 ============

class TestSafeBasename:
    def test_keeps_chinese_punctuation(self):
        """《》、，、-、· 是读标题的关键，不得删（旧实现会把它们全删掉）。"""
        name = '刘景源讲《温病学》第34集，春温-热灼真阴·上'
        assert safe_basename(name) == name

    def test_strips_windows_illegal_chars(self):
        assert safe_basename('a/b\\c:d*e?f"g<h>i|j') == 'abcdefghij'

    def test_collapses_whitespace(self):
        assert safe_basename('  标题   带\n多个\t空白 ') == '标题 带 多个 空白'

    def test_truncates_to_max_len(self):
        assert len(safe_basename('长' * 200)) == 80

    def test_strips_trailing_dots_and_spaces(self):
        assert safe_basename('标题... ') == '标题'

    def test_empty_returns_fallback(self):
        assert safe_basename('   ', fallback='result') == 'result'
        assert safe_basename(None) == ''


class TestExtractVideoId:
    def test_bilibili_bv(self):
        assert extract_video_id('https://www.bilibili.com/video/BV1SZ421T7A1/?p=34') == 'BV1SZ421T7A1'

    def test_youtube_watch(self):
        assert extract_video_id('https://www.youtube.com/watch?v=dQw4w9WgXcQ') == 'dQw4w9WgXcQ'

    def test_youtube_short_link(self):
        assert extract_video_id('https://youtu.be/dQw4w9WgXcQ') == 'dQw4w9WgXcQ'

    def test_unknown_or_empty(self):
        assert extract_video_id('https://example.com/video/123') == ''
        assert extract_video_id('') == ''
        assert extract_video_id(None) == ''


class TestExportBasename:
    def test_title_wins(self):
        job = {'id': 'j1', 'title': '刘景源讲《温病学》',
               'input_url': 'https://www.bilibili.com/video/BV1SZ421T7A1/'}
        assert export_basename(job) == '刘景源讲《温病学》'

    def test_falls_back_to_video_id(self):
        job = {'id': 'j1', 'title': '', 'input_url': 'https://www.bilibili.com/video/BV1SZ421T7A1/'}
        assert export_basename(job) == 'BV1SZ421T7A1'

    def test_falls_back_to_job_id(self):
        assert export_basename({'id': 'e923c16b'}) == 'job_e923c16b'

    def test_never_returns_empty(self):
        assert export_basename({}) == 'result'
        assert export_basename(None) == 'result'
        assert export_basename({'title': '///'}) == 'result'


# ============ 端点级（真实路由 + 真实响应头） ============

@pytest.fixture
def app():
    fastapi_app = FastAPI()
    from routers.export import router as export_router
    from routers.download import router as download_router
    fastapi_app.include_router(export_router, prefix="/api/export")
    fastapi_app.include_router(download_router, prefix="/api/download")
    return fastapi_app


@pytest.fixture
def client(app):
    return TestClient(app, client=('127.0.0.1', 50000))


class TestExportFilename:
    def test_txt_export_uses_video_title(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr('routers.export.EXPORT_DIR', tmp_path)
        job = {
            'id': 'e923c16b',
            'title': '刘景源讲《温病学》第34集，春温-热灼真阴',
            'result_text': '# 总结\n内容',
            'summary_type': 'summary',
            'options': {},
        }
        monkeypatch.setattr('routers.export.get_job', lambda jid: job)

        resp = client.post('/api/export/txt', json={'job_id': 'e923c16b', 'format': 'txt'})

        assert resp.status_code == 200
        assert '刘景源讲《温病学》第34集，春温-热灼真阴.txt' in unquote(resp.headers['content-disposition'])
        # 磁盘名保持唯一（附 job_id 短码），可读名只体现在响应头
        assert (tmp_path / '刘景源讲《温病学》第34集，春温-热灼真阴_e923c16b.txt').exists()

    def test_txt_export_without_title_uses_video_id(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr('routers.export.EXPORT_DIR', tmp_path)
        monkeypatch.setattr('routers.export.get_job', lambda jid: {
            'id': 'j1',
            'title': None,
            'input_url': 'https://www.bilibili.com/video/BV1SZ421T7A1/',
            'result_text': '内容',
        })

        resp = client.post('/api/export/txt', json={'job_id': 'j1', 'format': 'txt'})

        assert resp.status_code == 200
        disposition = unquote(resp.headers['content-disposition'])
        assert 'BV1SZ421T7A1.txt' in disposition
        assert 'AI总结_j1' not in disposition


class TestDownloadFilename:
    def _prepare(self, tmp_path, monkeypatch):
        data_dir = tmp_path / 'data'
        job_dir = data_dir / 'downloads' / 'j1'
        job_dir.mkdir(parents=True)
        target = job_dir / 'bilibili_subtitle.srt'
        target.write_text('1\n00:00:00,000 --> 00:00:01,000\n内容\n', encoding='utf-8')

        from routers import download as download_mod
        monkeypatch.setattr(download_mod, '_DATA_DIR_RESOLVED', data_dir.resolve())
        monkeypatch.setattr(download_mod, 'DOWNLOAD_DIR', data_dir / 'downloads')
        monkeypatch.setattr(download_mod, 'get_job',
                            lambda jid: {'id': 'j1', 'title': '刘景源讲《温病学》'})
        return target

    def test_readable_name_with_job_and_label(self, client, monkeypatch, tmp_path):
        target = self._prepare(tmp_path, monkeypatch)

        resp = client.get('/api/download/file', params={
            'path': str(target), 'job_id': 'j1', 'label': '字幕文件'})

        assert resp.status_code == 200
        assert '刘景源讲《温病学》_字幕文件.srt' in unquote(resp.headers['content-disposition'])

    def test_falls_back_to_disk_name_without_params(self, client, monkeypatch, tmp_path):
        """老调用方（只传 path）行为零变化。"""
        target = self._prepare(tmp_path, monkeypatch)

        resp = client.get('/api/download/file', params={'path': str(target)})

        assert resp.status_code == 200
        assert 'bilibili_subtitle.srt' in unquote(resp.headers['content-disposition'])

    def test_unknown_job_falls_back_to_disk_name(self, client, monkeypatch, tmp_path):
        target = self._prepare(tmp_path, monkeypatch)
        monkeypatch.setattr('routers.download.get_job', lambda jid: None)

        resp = client.get('/api/download/file', params={
            'path': str(target), 'job_id': 'missing', 'label': '字幕文件'})

        assert resp.status_code == 200
        assert 'bilibili_subtitle.srt' in unquote(resp.headers['content-disposition'])




# ============ derived_sibling（2026-10-13）============
# 回归背景：main.py / jobs_ai.py 曾用 txt_path.replace('.txt', ...) 派生文件名。
# str.replace 是**全串替换**，而媒体文件名来自 yt-dlp 的 %(title)s，
# 标题里只要含 ".txt"（如「Python.txt 教程」）每一处都会被替换掉，产出错乱名，
# 再长一点还会撞 Windows ENAMETOOLONG 让整个任务失败。
# 用 tmp_path 构造真实路径，避免硬编码分隔符（Windows 下 Path 会归一化 / -> \）。
class TestDerivedSibling:
    def test_stem_suffix_and_extension(self, tmp_path):
        got = derived_sibling(str(tmp_path / "a.txt"),
                              stem_suffix="__summary", extension=".md")
        assert got == str(tmp_path / "a__summary.md")

    def test_suffix_before_extension(self, tmp_path):
        got = derived_sibling(str(tmp_path / "a.txt"), suffix=".raw")
        assert got == str(tmp_path / "a.raw.txt")

    def test_title_containing_dot_txt_is_not_mangled(self, tmp_path):
        """★ 核心回归：标题里本来就含 .txt 时，只能改末尾那个扩展名。"""
        src = tmp_path / "Python.txt 教程.txt"
        got = derived_sibling(str(src), stem_suffix="__summary", extension=".md")
        assert got == str(tmp_path / "Python.txt 教程__summary.md")
        assert got.endswith(".md")
        assert got.count(".md") == 1

    def test_title_containing_multiple_dot_txt(self, tmp_path):
        got = derived_sibling(str(tmp_path / "a.txt.b.txt"),
                              stem_suffix="__s", extension=".md")
        assert got == str(tmp_path / "a.txt.b__s.md")

    def test_str_replace_would_have_been_wrong(self, tmp_path):
        """把被替换掉的旧写法钉在测试里，说明为什么不能用 replace。"""
        src = str(tmp_path / "Python.txt 教程.txt")
        broken = src.replace(".txt", "__summary.md")
        correct = derived_sibling(src, stem_suffix="__summary", extension=".md")
        assert broken != correct

    def test_directory_part_untouched(self, tmp_path):
        sub = tmp_path / "data.txt"
        sub.mkdir()
        got = derived_sibling(str(sub / "b.txt"), extension=".md")
        assert got == str(sub / "b.md")
        assert got.startswith(str(tmp_path / "data.txt"))

    def test_no_args_returns_same_path(self, tmp_path):
        src = str(tmp_path / "a.txt")
        assert derived_sibling(src) == src

    def test_extensionless_path(self, tmp_path):
        got = derived_sibling(str(tmp_path / "audio"), extension=".md")
        assert got == str(tmp_path / "audio.md")

    def test_result_is_writable_filename(self, tmp_path):
        """派生名要能真的落盘（旧的错乱名在 Windows 上会 ENAMETOOLONG/非法字符）。"""
        src = tmp_path / "Python.txt 教程.txt"
        src.write_text("原文", encoding="utf-8")
        out = Path(derived_sibling(str(src), stem_suffix="__summary", extension=".md"))
        out.write_text("正文", encoding="utf-8")
        assert out.is_file()
        assert src.exists(), "原文件必须保持不动"
        assert src.read_text(encoding="utf-8") == "原文"
