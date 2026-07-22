"""
YouTube Tool V2 - 导出路由 (Word/PDF/HTML/TXT)
将AI结果导出为4种格式，文件保存在data/exports/目录
"""
import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from models.schemas import ExportRequest
from db.job_repo import get_job
from config import DATA_DIR

logger = logging.getLogger(__name__)
router = APIRouter()

EXPORT_DIR = DATA_DIR / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def _sanitize_title(title: str) -> str:
    """清理标题，用于文件名"""
    if not title:
        return "result"
    # 移除特殊字符，保留中文/英文/数字/空格
    clean = re.sub(r'[^\w\s\u4e00-\u9fff]', '', title)
    clean = clean.strip().replace(' ', '_')
    # 截断过长标题
    return clean[:50] if len(clean) > 50 else clean or "result"


def _md_to_plain_text(md_text: str) -> str:
    """将Markdown转为纯文本（去除标记符号）"""
    text = md_text
    # 去除代码块
    text = re.sub(r'```[\s\S]*?```', lambda m: m.group(0).split('\n', 1)[-1].rstrip('```').strip(), text)
    # 去除标题标记
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # 去除加粗/斜体
    text = re.sub(r'\*{1,2}(.*?)\*{1,2}', r'\1', text)
    text = re.sub(r'_{1,2}(.*?)_{1,2}', r'\1', text)
    # 去除链接，保留文本
    text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)
    # 去除图片标记
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    # 去除引用标记
    text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)
    # 去除水平线
    text = re.sub(r'^---+$', '', text, flags=re.MULTILINE)
    # 去除列表标记
    text = re.sub(r'^\s*[-*+]\s+', '  • ', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    return text.strip()


def _md_to_html(md_text: str, title: str = "") -> str:
    """将Markdown转为HTML页面（带样式）"""
    try:
        from markdown_it import MarkdownIt
        md_parser = MarkdownIt("commonmark", {"html": True}).enable("table")
        body_html = md_parser.render(md_text)
    except ImportError:
        # fallback: 简单转义
        body_html = f"<pre>{md_text}</pre>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title or 'AI Summary'}</title>
<style>
body {{
    font-family: "Noto Sans SC", "Microsoft YaHei", sans-serif;
    max-width: 800px;
    margin: 40px auto;
    padding: 20px;
    line-height: 1.8;
    color: #333;
    background: #fff;
}}
h1 {{ border-bottom: 2px solid #e0e0e0; padding-bottom: 10px; }}
h2 {{ border-bottom: 1px solid #e8e8e8; padding-bottom: 6px; margin-top: 2em; }}
h3 {{ margin-top: 1.5em; }}
code {{ background: #f5f5f5; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }}
pre {{ background: #f5f5f5; padding: 16px; border-radius: 6px; overflow-x: auto; }}
pre code {{ background: none; padding: 0; }}
blockquote {{ border-left: 4px solid #ddd; margin: 1em 0; padding: 0.5em 1em; color: #666; }}
table {{ border-collapse: collapse; margin: 1em 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; }}
th {{ background: #f5f5f5; }}
img {{ max-width: 100%; }}
a {{ color: #1a73e8; }}
.tag-line {{ color: #999; font-size: 0.85em; margin-bottom: 1em; }}
</style>
</head>
<body>
<h1>{title or 'AI Summary'}</h1>
{body_html}
</body>
</html>"""


@router.post("/docx")
async def export_docx(req: ExportRequest):
    """导出为 Word 文档（python-docx）"""
    job = get_job(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {req.job_id} 不存在")
    if not job.get('result_text'):
        raise HTTPException(status_code=400, detail="任务没有结果文本")

    title = job.get('title') or f"AI总结_{req.job_id}"
    safe_title = _sanitize_title(title)
    output_path = EXPORT_DIR / f"{safe_title}.docx"

    try:
        from docx import Document
        from docx.shared import Pt, Inches
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError:
        raise HTTPException(status_code=500, detail="python-docx 未安装，请运行: pip install python-docx")

    # 在线程中执行（python-docx是同步库）
    def _create_docx():
        doc = Document()
        # 标题
        doc.add_heading(title, level=0)
        # 选项标签行
        options = job.get('options', {})
        if options:
            tag_line = f"类型: {job.get('summary_type', '?')} | 字数: {options.get('word_count', '?')} | 语气: {options.get('tone', '?')} | 格式: {options.get('format', '?')}"
            p = doc.add_paragraph(tag_line)
            p.style = 'Intense Quote'
        # 正文 — 按Markdown结构拆分
        md_text = job['result_text']
        for line in md_text.split('\n'):
            line = line.rstrip()
            if not line:
                continue
            # 标题行
            if line.startswith('#'):
                level = len(line.split(' ')[0])  # #数量
                heading_text = line.lstrip('#').strip()
                doc.add_heading(heading_text, level=min(level, 4))
            # 代码块标记（简化处理）
            elif line.startswith('```'):
                continue  # 代码块边界跳过，内容在下一段
            # 引用行
            elif line.startswith('>'):
                doc.add_paragraph(line.lstrip('>').strip(), style='Intense Quote')
            # 普通段落
            else:
                # 去除Markdown格式标记（简化）
                clean = re.sub(r'\*{1,2}(.*?)\*{1,2}', r'\1', line)
                clean = re.sub(r'_{1,2}(.*?)_{1,2}', r'\1', clean)
                clean = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', clean)
                doc.add_paragraph(clean)
        doc.save(str(output_path))

    await asyncio.to_thread(_create_docx)

    return FileResponse(
        path=str(output_path),
        filename=f"{safe_title}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.post("/pdf")
async def export_pdf(req: ExportRequest):
    """导出为 PDF（优先级：weasyprint → fpdf2）"""
    job = get_job(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {req.job_id} 不存在")
    if not job.get('result_text'):
        raise HTTPException(status_code=400, detail="任务没有结果文本")

    title = job.get('title') or f"AI总结_{req.job_id}"
    safe_title = _sanitize_title(title)
    result_text = job['result_text']

    # 尝试 weasyprint（需要 GTK/Pango，Windows 通常没有）
    try:
        from weasyprint import HTML
        html_content = _md_to_html(result_text, title)
        pdf_path = EXPORT_DIR / f"{safe_title}.pdf"
        def _gen():
            HTML(string=html_content).write_pdf(str(pdf_path))
        await asyncio.to_thread(_gen)
        return FileResponse(path=str(pdf_path), filename=f"{safe_title}.pdf", media_type="application/pdf")
    except (ImportError, OSError):
        pass  # 降级到 fpdf2

    # fpdf2 兜底（纯 Python，无系统依赖，支持中文）
    try:
        from fpdf import FPDF
        pdf_path = EXPORT_DIR / f"{safe_title}.pdf"
        _MSYH = r'C:\Windows\Fonts\msyh.ttc'
        def _gen_fpdf():
            pdf = FPDF()
            pdf.add_page()
            pdf.add_font('zh', '', _MSYH)
            pdf.add_font('zh', 'B', _MSYH)
            pdf.set_auto_page_break(True, 15)
            # 标题
            pdf.set_font('zh', 'B', 16)
            pdf.multi_cell(0, 10, title, align='L')
            pdf.ln(4)
            # 正文
            pdf.set_font('zh', '', 10)
            for line in result_text.split('\n'):
                line = line.strip()
                if not line:
                    pdf.ln(4)
                    continue
                # 处理标题行
                if line.startswith('# '):
                    pdf.set_font('zh', 'B', 14)
                    pdf.multi_cell(0, 8, line.lstrip('# '))
                    pdf.set_font('zh', '', 10)
                elif line.startswith('## '):
                    pdf.set_font('zh', 'B', 12)
                    pdf.multi_cell(0, 7, line.lstrip('## '))
                    pdf.set_font('zh', '', 10)
                elif line.startswith('### '):
                    pdf.set_font('zh', 'B', 11)
                    pdf.multi_cell(0, 7, line.lstrip('### '))
                    pdf.set_font('zh', '', 10)
                elif line.startswith('- ') or line.startswith('* '):
                    pdf.cell(5)
                    pdf.multi_cell(0, 6, '• ' + line[2:].strip())
                elif line.startswith('> '):
                    pdf.set_font('zh', 'B', 10)
                    pdf.multi_cell(0, 6, line.lstrip('> ').strip())
                    pdf.set_font('zh', '', 10)
                else:
                    pdf.multi_cell(0, 6, line)
            pdf.output(str(pdf_path))
        await asyncio.to_thread(_gen_fpdf)
        return FileResponse(path=str(pdf_path), filename=f"{safe_title}.pdf", media_type="application/pdf")
    except Exception as e:
        logger.error(f"PDF 生成失败: {e}")
        raise HTTPException(status_code=500, detail=f"PDF 生成失败: {e}")


@router.post("/html")
async def export_html(req: ExportRequest):
    """导出为 HTML 页面"""
    job = get_job(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {req.job_id} 不存在")
    if not job.get('result_text'):
        raise HTTPException(status_code=400, detail="任务没有结果文本")

    title = job.get('title') or f"AI总结_{req.job_id}"
    safe_title = _sanitize_title(title)
    output_path = EXPORT_DIR / f"{safe_title}.html"

    html_content = _md_to_html(job['result_text'], title)
    await asyncio.to_thread(output_path.write_text, html_content, 'utf-8')

    return FileResponse(
        path=str(output_path),
        filename=f"{safe_title}.html",
        media_type="text/html",
    )


@router.post("/txt")
async def export_txt(req: ExportRequest):
    """导出为纯文本"""
    job = get_job(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {req.job_id} 不存在")
    if not job.get('result_text'):
        raise HTTPException(status_code=400, detail="任务没有结果文本")

    title = job.get('title') or f"AI总结_{req.job_id}"
    safe_title = _sanitize_title(title)
    output_path = EXPORT_DIR / f"{safe_title}.txt"

    plain_text = _md_to_plain_text(job['result_text'])
    content = f"{title}\n{'=' * len(title.encode('utf-8'))}\n\n{plain_text}"
    await asyncio.to_thread(output_path.write_text, content, 'utf-8')

    return FileResponse(
        path=str(output_path),
        filename=f"{safe_title}.txt",
        media_type="text/plain",
    )
