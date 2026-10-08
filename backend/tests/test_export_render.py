"""
tests/test_export_render.py — services/md_render 渲染契约

覆盖（2026-10-08 导出成品化）：
  - 元信息：UTC → 北京时间 +8、标签/transcript_meta 解析、损坏 JSON 不崩
  - 目录：二/三级标题 ≥3 才插
  - docx 读回：标题层级 / run 级加粗 / 原生列表样式 / 真表格 / 无字面 '**' '|'
  - txt：列表序号保留、表格分列
  - html：表格 + 文档信息 + 目录
  - pdf：weasyprint 与 fpdf2 两条路径都能出合法 PDF
  - 端点 404/400 保持
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import md_render  # noqa: E402

SAMPLE_MD = """# 顶层标题

这是一段**加粗**与*斜体*，还有 `inline_code`。

## 一、结论先行

- 要点甲
- 要点乙

## 二、数据

| 药材 | 用量 |
|---|---|
| 黄芪 | 30g |
| 当归 | 12g |

## 三、编号清单

1. 第一步
2. 第二步

> 引用一段结论。

---

```text
plain code block
```
"""


@pytest.fixture
def job():
    return {
        "id": "abc12345deadbeef",
        "title": "《伤寒论》太阳篇逐条研读",
        "platform": "B站",
        "input_url": "https://www.bilibili.com/video/BV1xx411c7mD",
        "model": "glm-5-2",
        # 本地 11:39 写库，DB 存 UTC 03:39
        "created_at": "2026-10-07 03:39:04",
        "tags": json.dumps(["中医学习", "方证"], ensure_ascii=False),
        "transcript_meta": json.dumps(
            {"source": "transcription", "engine": "cloud_free", "engine_label": "星尘 XingChen（免费）", "seg_count": 128},
            ensure_ascii=False,
        ),
        "result_text": SAMPLE_MD,
    }


# ---------- 元信息 ----------

def test_meta_time_is_shifted_to_beijing(job):
    """★ created_at 是 UTC 无时区标记，导出件必须 +8（03:39 → 11:39）"""
    meta = dict(md_render.doc_meta(job))
    assert meta["任务创建时间"] == "2026-10-07 11:39"


def test_meta_keeps_tz_aware_value(job):
    """已带时区标记的值按自带时区换算，不做二次偏移"""
    job["created_at"] = "2026-10-07T03:39:04+00:00"
    assert dict(md_render.doc_meta(job))["任务创建时间"] == "2026-10-07 11:39"
    job["created_at"] = "2026-10-07T11:39:04+08:00"
    assert dict(md_render.doc_meta(job))["任务创建时间"] == "2026-10-07 11:39"


def test_meta_fields_and_transcript_source(job):
    meta = dict(md_render.doc_meta(job))
    assert meta["来源平台"] == "B站"
    assert meta["模型"] == "glm-5-2"
    assert meta["标签"] == "中医学习、方证"
    assert meta["转录来源"] == "语音识别 / 星尘 XingChen（免费） / 128 段"
    assert meta["正文字数"].endswith(" 字")
    assert int(meta["正文字数"].split()[0]) > 0


def test_meta_survives_corrupt_json(job):
    """tags/transcript_meta 是损坏 JSON 时不能抛异常（写空即可，但必须留日志）"""
    job["tags"] = "{not json"
    job["transcript_meta"] = "<<broken>>"
    meta = dict(md_render.doc_meta(job))
    assert meta["标签"] == ""
    assert meta["转录来源"] == ""


def test_meta_without_transcript_meta(job):
    job["transcript_meta"] = None
    assert dict(md_render.doc_meta(job))["转录来源"] == ""


# ---------- 目录 ----------

def test_toc_threshold():
    """二/三级标题 3 个才插目录，2 个不插"""
    blocks = md_render.parse_blocks("## 甲\n\n正文\n\n## 乙\n\n正文")
    assert md_render.doc_toc(blocks) == []
    blocks3 = md_render.parse_blocks("## 甲\n\n正文\n\n## 乙\n\n正文\n\n## 丙\n\n正文")
    assert [t for _, t in md_render.doc_toc(blocks3)] == ["甲", "乙", "丙"]


def test_toc_skips_level1_and_deeper():
    md = "# 一级\n\n## 甲\n\n### 甲一\n\n### 甲二\n\n#### 甲一零一"
    toc = md_render.doc_toc(md_render.parse_blocks(md))
    assert [t for _, t in toc] == ["甲", "甲一", "甲二"]


# ---------- docx ----------

def test_docx_structure_readback(job, tmp_path):
    docx = pytest.importorskip("docx")
    out = tmp_path / "out.docx"
    md_render.render_docx(job, SAMPLE_MD, out)
    assert out.exists() and out.stat().st_size > 0

    doc = docx.Document(str(out))
    styles = [p.style.name for p in doc.paragraphs]
    texts = [p.text for p in doc.paragraphs]
    joined = "\n".join(texts)

    # 标题层级：Heading 1/2 真实样式
    assert "Heading 2" in styles, styles
    assert any(p.style.name == "List Bullet" for p in doc.paragraphs), styles
    assert any(p.style.name == "List Number" for p in doc.paragraphs), styles

    # 真表格：1 表（2 列 × 3 行：表头 + 2 行数据）
    assert len(doc.tables) == 1
    table = doc.tables[0]
    assert len(table.columns) == 2
    header_bold = [run.bold for cell in table.rows[0].cells for para in cell.paragraphs for run in para.runs]
    assert header_bold and all(header_bold), "表头必须加粗"
    assert "黄芪" in table.rows[1].cells[0].text
    assert "30g" in table.rows[1].cells[1].text

    # run 级加粗（不是被剥掉）
    bold_paras = [p.text for p in doc.paragraphs if any(run.bold for run in p.runs)]
    assert any("加粗" in t for t in bold_paras), bold_paras

    # 文档信息块 + 目录
    assert "文档信息" in joined
    assert "2026-10-07 11:39" in joined, "元信息时间必须是北京时间"
    assert "目录" in joined

    # 不再出现字面标记
    assert "**" not in joined
    assert "|---" not in joined


def test_docx_inline_code_and_quote(job, tmp_path):
    out = tmp_path / "code.docx"
    md_render.render_docx(job, SAMPLE_MD, out)
    doc = pytest.importorskip("docx").Document(str(out))
    code_runs = [run.text for p in doc.paragraphs for run in p.runs if run.font.name == "Consolas"]
    assert "inline_code" in code_runs, code_runs
    assert any(p.style.name == "Intense Quote" for p in doc.paragraphs), "引用应保留"


# ---------- txt ----------

def test_text_keeps_list_numbers_and_bullets(job):
    out = md_render.render_text(job, SAMPLE_MD)
    assert "1. 第一步" in out and "2. 第二步" in out, "有序列表序号不能被删掉"
    assert "• 要点甲" in out
    assert "黄芪 | 30g" in out, "表格应按列分隔保留"
    assert "**" not in out and "`" not in out


def test_text_has_meta_header(job):
    out = md_render.render_text(job, SAMPLE_MD)
    assert out.startswith(job["title"])
    assert "任务创建时间：2026-10-07 11:39" in out


# ---------- html ----------

def test_html_has_table_meta_and_toc(job):
    page = md_render.render_html(job, SAMPLE_MD)
    assert "<table>" in page and "<th>药材</th>" in page
    assert "文档信息" in page and "2026-10-07 11:39" in page
    assert "class=\"toc\"" in page and "一、结论先行" in page
    assert "<strong>加粗</strong>" in page
    assert "<code>inline_code</code>" in page


def test_html_without_toc_when_few_headings(job):
    page = md_render.render_html(job, "# 单标题\n\n正文")
    assert "class=\"toc\"" not in page


def test_html_escapes_injection(job):
    page = md_render.render_html(job, "# <script>alert(1)</script>\n\n正文")
    assert "<script>" not in page
    assert "&lt;script&gt;" in page


# ---------- pdf ----------

def test_pdf_fpdf2_path(job, tmp_path, monkeypatch):
    """fpdf2 兜底路径：伪造 weasyprint ImportError，出合法 PDF"""
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "weasyprint":
            raise ImportError("no weasyprint in this env")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    out = tmp_path / "out.pdf"
    md_render.render_pdf(job, SAMPLE_MD, out)
    assert out.exists()
    raw = out.read_bytes()
    assert raw[:5] == b"%PDF-"
    assert raw.rstrip()[-5:] == b"%%EOF"
    assert len(raw) > 3000


def test_pdf_font_missing_is_explicit(job, tmp_path, monkeypatch):
    """字体缺失必须明确报错，不能静默出乱码 PDF"""
    monkeypatch.setattr(md_render, "_MS_YAHEI", str(tmp_path / "nope.ttc"))
    with pytest.raises(FileNotFoundError) as exc:
        md_render._render_pdf_fpdf(job, md_render.parse_blocks(SAMPLE_MD), tmp_path / "x.pdf")
    assert "EXPORT_PDF_FONT" in str(exc.value)


# ---------- 端点 ----------

@pytest.fixture
def client():
    """只挂 export 路由的最小 app（端点校验行为）"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.export import router as export_router

    app = FastAPI()
    app.include_router(export_router, prefix="/api/export")
    return TestClient(app)


@pytest.mark.parametrize("fmt", ["docx", "pdf", "html", "txt"])
def test_export_endpoint_404_for_missing_job(client, fmt):
    """端点校验保持：404 任务不存在（ExportRequest.format 是必填字段）"""
    resp = client.post(f"/api/export/{fmt}", json={"job_id": "deadbeef", "format": fmt})
    assert resp.status_code == 404
    assert "不存在" in resp.json()["detail"]


def test_export_endpoint_400_without_result_text(client):
    """端点校验保持：400 任务没有结果文本"""
    import routers.export as export_router_mod

    with patch.object(export_router_mod, "get_job", return_value={"id": "abc12345", "result_text": None}):
        resp = client.post("/api/export/html", json={"job_id": "abc12345", "format": "html"})
    assert resp.status_code == 400
    assert "没有结果文本" in resp.json()["detail"]


def test_export_html_endpoint_end_to_end(client, tmp_path, monkeypatch):
    """端点 → md_render 全链：真实生成 HTML 文件并含文档信息块"""
    import routers.export as export_router_mod

    job = {
        "id": "abc12345deadbeef",
        "title": "端到端导出",
        "platform": "B站",
        "input_url": "https://www.bilibili.com/video/BV1",
        "model": "glm-5-2",
        "created_at": "2026-10-07 03:39:04",
        "result_text": "## 甲\n\n正文 **加粗**\n\n## 乙\n\n正文\n\n## 丙\n\n正文",
    }
    with patch.object(export_router_mod, "get_job", return_value=job), \
         patch.object(export_router_mod, "EXPORT_DIR", tmp_path):
        resp = client.post("/api/export/html", json={"job_id": job["id"], "format": "html"})
    assert resp.status_code == 200
    written = list(tmp_path.glob("*.html"))
    assert len(written) == 1
    content = written[0].read_text(encoding="utf-8")
    assert "2026-10-07 11:39" in content
    assert "<strong>加粗</strong>" in content
    assert "class=\"toc\"" in content
