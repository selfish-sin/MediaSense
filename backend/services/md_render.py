"""
Markdown 导出渲染服务（唯一解析点）

设计要点（2026-10-08 导出成品化）：
  - **解析单点**：markdown-it（commonmark + table）产出 token 流，先归一成**块结构**
    （heading/para/blockquote/list/table/code/hr 的嵌套树），四个渲染器只消费块结构，
    不再各写一套正则/行循环。
    历史实现：docx 按行手工解析（`**加粗**` 被 re.sub 剥掉、列表不处理、表格完全不认）、
    fpdf2 按行循环只有标题与项目符号、`_md_to_plain_text` 把有序列表 `1.` 直接删掉、
    `_md_to_html` 又是另一条路径——四种格式四种解析。
  - **元信息**：导出件顶部「文档信息」块，全部来自任务自身字段（不外发内容）。
  - ★ **时间坑**：`jobs.created_at` 是 UTC 无时区标记（本地 11:39 写行存 03:39:04），
    必须 +8 转北京时间再写，否则导出件时间整整差 8 小时。
  - **目录**：仅当二级/三级标题 ≥3 个时插入（静态列出标题，不假装有页码）；docx/HTML 有。

对外契约：
  doc_meta(job) -> List[Tuple[str, str]]      文档信息块（字段名, 值）
  doc_toc(blocks) -> List[Tuple[int, str]]    目录条目（level, text）；不足 3 条返回 []
  parse_blocks(text) -> List[Block]           块结构（渲染器的唯一输入）
  render_docx(job, text, path) -> None
  render_pdf(job, text, path) -> None        （weasyprint 优先，失败降级 fpdf2）
  render_html(job, text) -> str
  render_text(job, text) -> str
"""
import html as html_lib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from markdown_it import MarkdownIt

logger = logging.getLogger(__name__)

# DB 存的是 UTC（CURRENT_TIMESTAMP，无时区标记），展示按北京时间
CN_TZ = timezone(timedelta(hours=8))

# 目录门槛：二/三级标题少于这个数就不插目录（小文档插目录反而是噪音）
TOC_MIN_HEADINGS = 3
TOC_MIN_LEVEL = 2
TOC_MAX_LEVEL = 3

_PARSER: Optional[MarkdownIt] = None


def _parser() -> MarkdownIt:
    """markdown-it 单例：commonmark + table（不新增依赖，markdown-it-py 已在锁文件里）"""
    global _PARSER
    if _PARSER is None:
        _PARSER = MarkdownIt("commonmark", {"html": False, "linkify": True}).enable("table")
    return _PARSER


# ============ 解析：token → 块结构 ============
# Block 形如 {"kind": "heading", "level": 2, "runs": [...]}，
# blockquote / list 各自带嵌套的 blocks（list item 内可含多个块）。

def _runs(children: Optional[Sequence[Any]]) -> List[Dict[str, Any]]:
    """inline token 的子 token → 扁平 run 列表 [{text, bold, italic, code, href}]。

    这是四种格式共同的行内富文本表达：docx 映射成 run 属性、html 映射成标签、
    pdf 在 bold 时切 B 字体、txt 只取 text。强调是栈式的：新 run 按**当前嵌套深度**取样式。
    """
    runs: List[Dict[str, Any]] = []
    bold_depth = 0
    italic_depth = 0
    href = ""

    def _new(text: str = "", code: bool = False) -> Dict[str, Any]:
        run: Dict[str, Any] = {
            "text": text,
            "bold": bold_depth > 0,
            "italic": italic_depth > 0,
            "code": code,
            "href": href or None,
        }
        runs.append(run)
        return run

    for child in children or []:
        ttype = child.type
        if ttype == "text":
            _new(child.content)
        elif ttype == "code_inline":
            _new(child.content, code=True)
        elif ttype in ("softbreak", "hardbreak"):
            _new("\n")
        elif ttype == "strong_open":
            bold_depth += 1
        elif ttype == "strong_close":
            bold_depth = max(0, bold_depth - 1)
        elif ttype == "em_open":
            italic_depth += 1
        elif ttype == "em_close":
            italic_depth = max(0, italic_depth - 1)
        elif ttype == "link_open":
            href = child.attrGet("href") or ""
        elif ttype == "link_close":
            href = ""
        elif ttype == "image":
            alt = child.attrGet("alt") or child.content or ""
            if alt:
                _new(alt)
        else:
            # html_inline 等：commonmark + html=False 下不会出现，忽略
            if child.content and ttype == "html_inline":
                logger.debug("忽略内联 HTML 片段（html=False 已禁用）")
    return [r for r in runs if r["text"]]


def _runs_text(runs: Sequence[Dict[str, Any]]) -> str:
    return "".join(r.get("text", "") for r in runs)


def _parse(token_stream: Sequence[Any], i: int, stops: Tuple[str, ...]) -> Tuple[List[Dict[str, Any]], int]:
    """从 token 下标 i 开始读块，遇到 stops 里的 *_close 返回（块列表, 下一个下标）。

    stops 含自身闭合类型，故嵌套调用时外层的 close 会把控制权交回外层。
    """
    blocks: List[Dict[str, Any]] = []
    n = len(token_stream)
    while i < n:
        token = token_stream[i]
        ttype = token.type

        if ttype in stops or (ttype.endswith("_close") and ttype in stops):
            return blocks, i

        if ttype == "heading_open":
            level = int(token.tag[1:]) if token.tag[1:].isdigit() else 1
            inline = token_stream[i + 1] if i + 1 < n else None
            runs = _runs(list(inline.children or [])) if inline and inline.type == "inline" else []
            if _runs_text(runs).strip():
                blocks.append({"kind": "heading", "level": level, "runs": runs})
            i += 3  # heading_open inline heading_close
            continue

        if ttype == "paragraph_open":
            inline = token_stream[i + 1] if i + 1 < n else None
            runs = _runs(list(inline.children or [])) if inline and inline.type == "inline" else []
            if _runs_text(runs).strip():
                blocks.append({"kind": "para", "runs": runs})
            i += 3  # paragraph_open inline paragraph_close
            continue

        if ttype == "blockquote_open":
            inner, i = _parse(token_stream, i + 1, ("blockquote_close",))
            blocks.append({"kind": "blockquote", "blocks": inner})
            i += 1  # 跳过 blockquote_close
            continue

        if ttype in ("bullet_list_open", "ordered_list_open"):
            ordered = ttype == "ordered_list_open"
            items: List[Dict[str, Any]] = []
            i += 1
            while i < n and token_stream[i].type != ("ordered_list_close" if ordered else "bullet_list_close"):
                if token_stream[i].type == "list_item_open":
                    item_blocks, i = _parse(token_stream, i + 1, ("list_item_close",))
                    items.append({"blocks": item_blocks})
                    i += 1  # 跳过 list_item_close
                    continue
                i += 1
            blocks.append({"kind": "list", "ordered": ordered, "items": items})
            i += 1  # 跳过列表 close
            continue

        if ttype == "table_open":
            header: List[List[Dict[str, Any]]] = []
            rows: List[List[List[Dict[str, Any]]]] = []
            in_header = False
            i += 1
            while i < n and token_stream[i].type != "table_close":
                cur = token_stream[i].type
                if cur == "thead_open":
                    in_header = True
                elif cur == "thead_close":
                    in_header = False
                elif cur == "tr_open":
                    cells: List[List[Dict[str, Any]]] = []
                    i += 1
                    while i < n and token_stream[i].type != "tr_close":
                        if token_stream[i].type in ("th_open", "td_open"):
                            inline = token_stream[i + 1] if i + 1 < n else None
                            cells.append(_runs(list(inline.children or [])) if inline and inline.type == "inline" else [])
                            i += 2  # inline + th_close/td_close
                            continue
                        i += 1
                    if in_header:
                        header = cells
                    else:
                        rows.append(cells)
                    i += 1  # 跳过 tr_close
                    continue
                i += 1
            blocks.append({"kind": "table", "header": header, "rows": rows})
            i += 1  # 跳过 table_close
            continue

        if ttype in ("fence", "code_block"):
            code = (token.content or "").rstrip("\n")
            if code.strip():
                blocks.append({"kind": "code", "text": code, "lang": token.info or ""})
            i += 1
            continue

        if ttype == "hr":
            blocks.append({"kind": "hr"})
            i += 1
            continue

        # 其它（list 内部残留的 hidden token、thead_open 之外的杂项）跳过
        i += 1

    return blocks, i


def parse_blocks(text: str) -> List[Dict[str, Any]]:
    """Markdown 文本 → 块结构（四个渲染器的唯一输入）"""
    tokens = _parser().parse(text or "")
    blocks, _ = _parse(tokens, 0, ())
    return blocks


def _item_runs(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """列表项 → 合并其首段与嵌套段落的行内 run（导出件里列表项视为一段文本）"""
    merged: List[Dict[str, Any]] = []
    for sub in item.get("blocks") or []:
        if sub["kind"] == "para":
            if merged:
                merged.append({"text": " ", "bold": False, "italic": False, "code": False, "href": None})
            merged.extend(sub["runs"])
        elif sub["kind"] == "list":
            for nested in sub["items"]:
                merged.append({"text": " ", "bold": False, "italic": False, "code": False, "href": None})
                merged.extend(_item_runs(nested))
    return merged


def _block_lines(block: Dict[str, Any]) -> List[str]:
    """块 → 纯文本行（表格转 `a | b`，列表保留序号/项目符号）"""
    kind = block["kind"]
    if kind in ("heading", "para"):
        return [_runs_text(block["runs"]).replace("\n", " ")]
    if kind == "blockquote":
        out: List[str] = []
        for sub in block["blocks"]:
            for line in _block_lines(sub):
                out.append(f"> {line}" if line.strip() else ">")
        return out
    if kind == "list":
        out = []
        for order, item in enumerate(block["items"], start=1):
            marker = f"{order}. " if block["ordered"] else "• "
            out.append(marker + _runs_text(_item_runs(item)).replace("\n", " "))
        return out
    if kind == "table":
        out = []
        if block["header"]:
            out.append(" | ".join(_runs_text(c) for c in block["header"]))
        for row in block["rows"]:
            out.append(" | ".join(_runs_text(c) for c in row))
        return out
    if kind == "code":
        return block["text"].split("\n")
    if kind == "hr":
        return ["———"]
    return []


# ============ 元信息 / 目录 ============

def _fmt_local(utc_str: str) -> str:
    """DB 的 UTC 无时区时间串 → 北京时间可读串。

    ★ jobs.created_at 是 UTC（CURRENT_TIMESTAMP），直接展示会比本地早 8 小时。
    已带时区标记的值按自带时区换算，不再二次偏移。
    """
    if not utc_str:
        return ""
    s = str(utc_str).strip()
    if "+" in s[10:] or s.endswith("Z"):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            logger.warning(f"created_at 解析失败，按原样输出: {s!r}")
            return s
        if dt.tzinfo is None:
            return s
        return dt.astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        return dt.replace(tzinfo=timezone.utc).astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M")
    logger.warning(f"created_at 格式未知，按原样输出: {s!r}")
    return s


def _json_field(value: Any, field: str) -> Any:
    """jobs 的 JSON 列可能是 str（未反序列化的旧行）或已解析对象"""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            logger.warning(f"{field} 不是合法 JSON，导出件该字段留空")
            return None
    return value


def _tag_list(job: Dict[str, Any]) -> str:
    tags = _json_field(job.get("tags"), "tags")
    if not isinstance(tags, (list, tuple)) or not tags:
        return ""
    return "、".join(str(t).strip() for t in tags if str(t).strip())


def _transcript_source(job: Dict[str, Any]) -> str:
    """转录来源：transcript_meta 的来源 + 实际引擎（+ 字幕/语音段数，若有）"""
    meta = _json_field(job.get("transcript_meta"), "transcript_meta")
    if not isinstance(meta, dict) or not meta:
        return ""
    label = {"subtitle": "视频字幕", "transcription": "语音识别"}.get(meta.get("source") or "", meta.get("source") or "")
    engine = meta.get("engine_label") or meta.get("engine") or ""
    seg = meta.get("seg_count")
    parts = [p for p in (label, engine) if p]
    if isinstance(seg, int) and seg > 0:
        parts.append(f"{seg} 段")
    return " / ".join(parts)


def doc_meta(job: Dict[str, Any]) -> List[Tuple[str, str]]:
    """导出件顶部的「文档信息」块：(字段名, 值)；值为空串表示调用方跳过该行"""
    body = job.get("result_text") or ""
    return [
        ("标题", job.get("title") or ""),
        ("来源平台", job.get("platform") or ""),
        ("来源链接", job.get("input_url") or ""),
        ("模型", job.get("model") or ""),
        ("任务创建时间", _fmt_local(job.get("created_at") or "")),
        ("正文字数", f"{len(re.sub(chr(92) + 's+', '', body))} 字"),
        ("标签", _tag_list(job)),
        ("转录来源", _transcript_source(job)),
    ]


def doc_toc(blocks: Sequence[Dict[str, Any]]) -> List[Tuple[int, str]]:
    """目录条目 (level, text)；二/三级标题不足 TOC_MIN_HEADINGS 个时返回 []"""
    entries: List[Tuple[int, str]] = []
    for block in blocks:
        if block["kind"] == "heading" and TOC_MIN_LEVEL <= block["level"] <= TOC_MAX_LEVEL:
            text = _runs_text(block["runs"]).strip()
            if text:
                entries.append((block["level"], text))
    return entries if len(entries) >= TOC_MIN_HEADINGS else []


def _meta_rows(job: Dict[str, Any]) -> List[Tuple[str, str]]:
    return [(k, str(v)) for k, v in doc_meta(job) if k != "标题" and v]


# ============ Word（python-docx） ============

def render_docx(job: Dict[str, Any], text: str, path: Path) -> None:
    """导出 Word：标题层级 / run 级加粗斜体行内码 / 原生列表样式 / 真表格 + 文档信息块 + 目录"""
    from docx import Document
    from docx.shared import Pt

    blocks = parse_blocks(text)
    doc = Document()

    doc.add_heading(job.get("title") or "AI 总结", level=0)

    meta_rows = _meta_rows(job)
    if meta_rows:
        para = doc.add_paragraph()
        run = para.add_run("文档信息")
        run.bold = True
        run.font.size = Pt(11)
        for key, value in meta_rows:
            line = doc.add_paragraph()
            line.paragraph_format.space_after = Pt(0)
            krun = line.add_run(f"{key}：")
            krun.bold = True
            krun.font.size = Pt(9)
            line.add_run(value).font.size = Pt(9)

    toc = doc_toc(blocks)
    if toc:
        para = doc.add_paragraph()
        run = para.add_run("目录")
        run.bold = True
        run.font.size = Pt(11)
        for level, entry_text in toc:
            entry = doc.add_paragraph()
            entry.paragraph_format.left_indent = Pt((level - TOC_MIN_LEVEL) * 16)
            entry.paragraph_format.space_after = Pt(0)
            entry.add_run(entry_text).font.size = Pt(10)

    _docx_blocks(doc, blocks)
    doc.save(str(path))


def _docx_blocks(doc, blocks: Sequence[Dict[str, Any]]) -> None:
    from docx.shared import Pt, RGBColor

    for block in blocks:
        kind = block["kind"]
        if kind == "heading":
            level = block["level"]
            heading_text = _runs_text(block["runs"]).strip() or "（空标题）"
            if level <= 1:
                doc.add_heading(heading_text, level=1)
                continue
            para = doc.add_paragraph(style=f"Heading {min(level, 4)}")
            para.add_run(heading_text)
        elif kind == "para":
            _docx_runs(doc.add_paragraph(), block["runs"])
        elif kind == "blockquote":
            para = doc.add_paragraph(style="Intense Quote")
            _docx_runs(para, [r for sub in block["blocks"] for r in sub.get("runs", [])])
        elif kind == "list":
            for order, item in enumerate(block["items"], start=1):
                style = "List Number" if block["ordered"] else "List Bullet"
                para = doc.add_paragraph(style=style)
                if block["ordered"]:
                    para.add_run(f"{order}. ")
                _docx_runs(para, _item_runs(item))
        elif kind == "table":
            _docx_table(doc, block)
        elif kind == "code":
            for line in block["text"].split("\n"):
                para = doc.add_paragraph()
                run = para.add_run(line)
                run.font.name = "Consolas"
                run.font.size = Pt(9)
                run.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
        elif kind == "hr":
            para = doc.add_paragraph()
            para.add_run("———")


def _docx_runs(paragraph, runs: Sequence[Dict[str, Any]]) -> None:
    from docx.shared import Pt

    for run_data in runs:
        text = run_data.get("text", "")
        if not text:
            continue
        run = paragraph.add_run(text)
        run.bold = bool(run_data.get("bold"))
        run.italic = bool(run_data.get("italic"))
        if run_data.get("code"):
            run.font.name = "Consolas"
            run.font.size = Pt(9)


def _docx_table(doc, block: Dict[str, Any]) -> None:
    """真 Word 表格 + 表头加粗；列数按最长行补齐"""
    header = block.get("header") or []
    rows = block.get("rows") or []
    if not header and not rows:
        logger.warning("表格无行内容，跳过 Word 表格渲染")
        return
    columns = max([len(header)] + [len(r) for r in rows]) or 1
    table = doc.add_table(rows=1 if header else 0, cols=columns)
    try:
        table.style = "Table Grid"
    except KeyError:
        logger.warning("docx 默认模板缺少 Table Grid 样式，表格将无边框")
    if header:
        for col in range(columns):
            cell = table.rows[0].cells[col]
            cell.text = ""
            para = cell.paragraphs[0]
            _docx_runs(para, header[col] if col < len(header) else [])
            for run in para.runs:
                run.bold = True
    for row in rows:
        cells = table.add_row().cells
        for col in range(columns):
            cell = cells[col]
            cell.text = ""
            _docx_runs(cell.paragraphs[0], row[col] if col < len(row) else [])


# ============ HTML ============

_CSS = """body {
    font-family: "Noto Sans SC", "Microsoft YaHei", sans-serif;
    max-width: 800px;
    margin: 40px auto;
    padding: 20px;
    line-height: 1.8;
    color: #333;
    background: #fff;
}
h1 { border-bottom: 2px solid #e0e0e0; padding-bottom: 10px; }
h2 { border-bottom: 1px solid #e8e8e8; padding-bottom: 6px; margin-top: 2em; }
h3 { margin-top: 1.5em; }
code { background: #f5f5f5; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }
pre { background: #f5f5f5; padding: 16px; border-radius: 6px; overflow-x: auto; }
pre code { background: none; padding: 0; }
blockquote { border-left: 4px solid #ddd; margin: 1em 0; padding: 0.5em 1em; color: #666; }
table { border-collapse: collapse; margin: 1em 0; width: 100%; }
th, td { border: 1px solid #ddd; padding: 8px 12px; text-align: left; vertical-align: top; }
th { background: #f5f5f5; }
img { max-width: 100%; }
a { color: #1a73e8; }
.meta { background: #fafafa; border: 1px solid #eee; border-radius: 6px; padding: 4px 16px; margin: 12px 0 24px; }
.meta h2 { font-size: 1em; border: none; margin: 8px 0 6px; }
.meta-row { display: flex; gap: 10px; font-size: 0.9em; border-bottom: 1px dashed #eee; padding: 3px 0; }
.meta-k { color: #888; min-width: 6.5em; }
.meta-v { color: #333; word-break: break-all; }
.toc { margin: 16px 0 28px; }
.toc h2 { font-size: 1.1em; border: none; margin: 8px 0 6px; }
.toc ul { list-style: none; padding-left: 0; }
.toc li { font-size: 0.95em; }
.toc .toc-l3 { padding-left: 1.6em; color: #666; }"""


def _runs_html(runs: Sequence[Dict[str, Any]]) -> str:
    out = []
    for run in runs:
        text = html_lib.escape(run.get("text", ""))
        if not text:
            continue
        if run.get("code"):
            text = f"<code>{text}</code>"
        if run.get("bold"):
            text = f"<strong>{text}</strong>"
        if run.get("italic"):
            text = f"<em>{text}</em>"
        out.append(text)
    return "".join(out)


def _blocks_html(blocks: Sequence[Dict[str, Any]]) -> str:
    """块结构 → 正文 HTML（含真表格）"""
    parts: List[str] = []
    for block in blocks:
        kind = block["kind"]
        if kind == "heading":
            level = min(block["level"], 6)
            parts.append(f"<h{level}>{_runs_html(block['runs'])}</h{level}>")
        elif kind == "para":
            parts.append(f"<p>{_runs_html(block['runs'])}</p>")
        elif kind == "blockquote":
            parts.append(f"<blockquote>{_blocks_html(block['blocks'])}</blockquote>")
        elif kind == "list":
            tag = "ol" if block["ordered"] else "ul"
            items = "".join(f"<li>{_runs_html(_item_runs(item))}</li>" for item in block["items"])
            parts.append(f"<{tag}>{items}</{tag}>")
        elif kind == "table":
            parts.append(_table_html(block))
        elif kind == "code":
            parts.append(f"<pre><code>{html_lib.escape(block['text'])}</code></pre>")
        elif kind == "hr":
            parts.append("<hr>")
    return "\n".join(parts)


def _table_html(block: Dict[str, Any]) -> str:
    header = block.get("header") or []
    rows = block.get("rows") or []
    if not header and not rows:
        logger.warning("表格无行内容，跳过 HTML 表格渲染")
        return ""
    columns = max([len(header)] + [len(r) for r in rows]) or 1
    out = ["<table>"]
    if header:
        out.append("<thead><tr>")
        for col in range(columns):
            cell = header[col] if col < len(header) else []
            out.append(f"<th>{_runs_html(cell)}</th>")
        out.append("</tr></thead>")
    out.append("<tbody>")
    for row in rows:
        out.append("<tr>")
        for col in range(columns):
            cell = row[col] if col < len(row) else []
            out.append(f"<td>{_runs_html(cell)}</td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _document(job: Dict[str, Any], body_html: str, toc: Sequence[Tuple[int, str]]) -> str:
    """完整 HTML 文档（HTML 导出与 PDF 的 weasyprint 路径共用同一份样式与信息块）"""
    title = job.get("title") or "AI 总结"
    meta_html = ""
    meta_rows = _meta_rows(job)
    if meta_rows:
        items = "".join(
            f"<div class=\"meta-row\"><span class=\"meta-k\">{html_lib.escape(k)}</span>"
            f"<span class=\"meta-v\">{html_lib.escape(v)}</span></div>"
            for k, v in meta_rows
        )
        meta_html = f"<section class=\"meta\"><h2>文档信息</h2>{items}</section>"
    toc_html = ""
    if toc:
        entries = "".join(f"<li class=\"toc-l{lv}\">{html_lib.escape(t)}</li>" for lv, t in toc)
        toc_html = f"<nav class=\"toc\"><h2>目录</h2><ul>{entries}</ul></nav>"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html_lib.escape(title)}</title>
<style>
{_CSS}
</style>
</head>
<body>
<h1>{html_lib.escape(title)}</h1>
{meta_html}
{toc_html}
{body_html}
</body>
</html>"""


def render_html(job: Dict[str, Any], text: str) -> str:
    """导出 HTML：真表格 + 文档信息块 + （≥3 个二/三级标题时）目录"""
    blocks = parse_blocks(text)
    return _document(job, _blocks_html(blocks), doc_toc(blocks))


# ============ PDF（weasyprint 优先 → fpdf2 兜底） ============

def render_pdf(job: Dict[str, Any], text: str, path: Path) -> None:
    """导出 PDF：weasyprint 优先（HTML 渲染，表格最保真），不可用/失败时降级 fpdf2"""
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as exc:
        logger.info(f"weasyprint 不可用（{type(exc).__name__}），PDF 走 fpdf2 渲染")
    else:
        try:
            HTML(string=render_html(job, text)).write_pdf(str(path))
            return
        except Exception as exc:
            logger.warning(f"weasyprint 生成失败，降级 fpdf2: {exc}")
    _render_pdf_fpdf(job, parse_blocks(text), path)


_MS_YAHEI = os.getenv("EXPORT_PDF_FONT", r"C:\Windows\Fonts\msyh.ttc")


def _register_fonts(pdf) -> None:
    """注册中文字体（正常体 + 加粗体）；字体缺失时明确报错，不静默出乱码 PDF"""
    if not Path(_MS_YAHEI).exists():
        raise FileNotFoundError(
            f"PDF 中文字体不存在: {_MS_YAHEI}（可用环境变量 EXPORT_PDF_FONT 指定 .ttf/.ttc 路径）"
        )
    pdf.add_font("zh", "", _MS_YAHEI)
    pdf.add_font("zh", "B", _MS_YAHEI)


def _render_pdf_fpdf(job: Dict[str, Any], blocks: Sequence[Dict[str, Any]], path: Path) -> None:
    """fpdf2 兜底渲染：标题分级 / 加粗切 B / 列表带符号与序号 / 表格按列数平分画边框"""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(True, 15)
    pdf.add_page()
    _register_fonts(pdf)

    pdf.set_font("zh", "B", 16)
    _pdf_text(pdf, 0, 10, job.get("title") or "AI 总结")
    pdf.ln(2)
    pdf.set_font("zh", "", 9)
    label_w = 22
    for key, value in _meta_rows(job):
        pdf.set_font("zh", "B", 9)
        pdf.cell(label_w, 5, f"{key}：")
        pdf.set_font("zh", "", 9)
        _pdf_text(pdf, pdf.epw - label_w, 5, value)
    pdf.ln(4)
    pdf.set_font("zh", "", 10)

    _pdf_blocks(pdf, blocks)
    pdf.output(str(path))


def _pdf_text(pdf, width: float, height: float, text: str) -> None:
    """写一行并把 x 复位到左边距。

    ★ fpdf2 的 multi_cell 默认 new_x="RIGHT"：写完 x 停在右边距，下一次写就得到负宽度
    （实测 "Not enough horizontal space to render a single character"）。
    故所有正文写入都必须走这里（显式 new_x="LMARGIN"）。
    """
    usable = width if width > 0 else pdf.epw
    pdf.multi_cell(max(usable, 10.0), height, text, align="L", new_x="LMARGIN", new_y="NEXT")


def _pdf_runs(pdf, runs: Sequence[Dict[str, Any]], indent: float = 0.0) -> None:
    """逐 run 写一行：fpdf 不支持行内混排，加粗段切 B 字体另起一行"""
    wrote = False
    for run_data in runs:
        text = (run_data.get("text") or "").replace("\n", " ").strip()
        if not text:
            continue
        pdf.set_font("zh", "B" if run_data.get("bold") else "", 10)
        if indent:
            pdf.set_x(pdf.l_margin + indent)
        _pdf_text(pdf, pdf.epw - indent, 6, text)
        wrote = True
    pdf.set_font("zh", "", 10)
    if not wrote:
        if indent:
            pdf.set_x(pdf.l_margin + indent)
        _pdf_text(pdf, pdf.epw - indent, 6, " ")


def _pdf_blocks(pdf, blocks: Sequence[Dict[str, Any]]) -> None:
    for block in blocks:
        kind = block["kind"]
        if kind == "heading":
            text = _runs_text(block["runs"]).replace("\n", " ").strip()
            if not text:
                continue
            size = {1: 14, 2: 12, 3: 11}.get(block["level"], 11)
            pdf.set_font("zh", "B", size)
            _pdf_text(pdf, pdf.epw, size / 2 + 2, text)
            pdf.ln(1)
            pdf.set_font("zh", "", 10)
        elif kind == "para":
            _pdf_runs(pdf, block["runs"])
        elif kind == "blockquote":
            text = " ".join(_runs_text(sub.get("runs", [])) for sub in block["blocks"]).strip()
            pdf.set_font("zh", "B", 10)
            _pdf_text(pdf, pdf.epw, 6, text or " ")
            pdf.set_font("zh", "", 10)
            pdf.ln(1)
        elif kind == "list":
            for order, item in enumerate(block["items"], start=1):
                marker = f"{order}. " if block["ordered"] else "• "
                pdf.set_font("zh", "", 10)
                pdf.cell(8, 6, marker)
                _pdf_runs(pdf, _item_runs(item), indent=8)
        elif kind == "table":
            _pdf_table(pdf, block)
        elif kind == "code":
            pdf.set_font("zh", "", 9)
            for line in block["text"].split("\n"):
                _pdf_text(pdf, pdf.epw, 5, line or " ")
            pdf.set_font("zh", "", 10)
        elif kind == "hr":
            y = pdf.get_y() + 2
            pdf.set_draw_color(200, 200, 200)
            pdf.line(pdf.l_margin, y, pdf.epw + pdf.l_margin, y)
            pdf.ln(4)


def _pdf_table(pdf, block: Dict[str, Any]) -> None:
    """表格：列宽按列数平分，逐 cell 画边框，表头加粗"""
    header = block.get("header") or []
    rows = block.get("rows") or []
    columns = max([len(header)] + [len(r) for r in rows]) if (header or rows) else 0
    if not columns:
        logger.warning("表格无行内容，跳过 PDF 表格渲染")
        return
    cell_w = pdf.epw / columns
    if header:
        _pdf_row(pdf, [_runs_text(c) for c in header], columns, cell_w, bold=True)
    for row in rows:
        _pdf_row(pdf, [_runs_text(c) for c in row], columns, cell_w, bold=False)
    pdf.ln(3)


def _pdf_row(pdf, cells: Sequence[str], columns: int, cell_w: float, bold: bool) -> None:
    """一行表格：先 dry_run 测高再逐 cell 画框（内容换行也不会边框错位）"""
    texts = [(cells[i] if i < len(cells) else "").replace("\n", " ").strip() for i in range(columns)]
    pdf.set_font("zh", "B" if bold else "", 9)
    height = 5.0
    for text in texts:
        # output="LINES" 在 fpdf2 2.x 返回 list[str]（1.x 返回 int），两种都兼容
        result = pdf.multi_cell(cell_w - 2, 5, text or " ", dry_run=True, output="LINES")
        line_count = len(result) if isinstance(result, (list, tuple)) else int(result)
        height = max(height, max(1, line_count) * 5)
    top = pdf.get_y()
    for i in range(columns):
        x = pdf.l_margin + i * cell_w
        pdf.set_xy(x, top)
        pdf.cell(cell_w, height, "", border=1)
        pdf.set_xy(x + 1, top + 0.5)
        pdf.multi_cell(cell_w - 2, 5, texts[i] or " ", align="L")
    pdf.set_xy(pdf.l_margin, top + height)
    pdf.set_font("zh", "", 10)


# ============ 纯文本 ============

def render_text(job: Dict[str, Any], text: str) -> str:
    """导出纯文本：去标记但保结构（有序列表保留序号、无序用 •、表格用 | 分列）"""
    blocks = parse_blocks(text)
    lines: List[str] = []
    for block in blocks:
        if block["kind"] == "heading":
            lines.append("")
            lines.append("#" * min(block["level"], 6) + " " + _runs_text(block["runs"]).strip())
        else:
            lines.extend(_block_lines(block))
        lines.append("")

    title = job.get("title") or "AI 总结"
    head = [title, "=" * (len(title) * 2)]
    meta_rows = _meta_rows(job)
    if meta_rows:
        head.append("")
        head.extend(f"{key}：{value}" for key, value in meta_rows)
    body = "\n".join(lines).strip()
    return "\n".join(head) + "\n\n" + body + "\n"
