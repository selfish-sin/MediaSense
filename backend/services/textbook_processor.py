"""
YouTube Tool V2 - 教科书模式处理器
三层结构：章(Chapter) → 节(Section) → 知识点(Knowledge Point)
独立于 ai_process 管线，通过 summary_type='textbook' 触发
"""
import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

from config import DEFAULT_MODEL
from db.model_repo import get_model_by_key
from services.ai_processor import _call_ai

logger = logging.getLogger(__name__)


# ============ 结构分析 ============

async def _analyze_structure(
    text: str,
    title: str,
    model_cfg: Dict[str, Any],
    sio=None,
    job_id: str = '',
) -> List[Dict[str, Any]]:
    """分析转录文本，输出章/节结构

    返回 [
      {"chapter": "第一章 xxx", "start_ts": "00:00", "sections": [
        {"section": "1.1 xxx", "start_ts": "00:00", "end_ts": "05:30"}
      ]}
    ]
    """
    if sio:
        from socket_handlers import emit_log
        await emit_log(sio, job_id, '📖 教科书模式：正在分析视频结构...')

    # 截取前部分文本分析结构（不需要全文）
    snippet = text[:30000]

    prompt = f"""你是一位教材编写专家。请分析以下视频转录文本，将其划分为合理的章节结构。

视频标题：{title or '未命名'}

转录文本（前 30000 字）：
{snippet}

请输出 JSON 格式的章节结构（只输出 JSON，不要解释）：
{{
  "chapters": [
    {{
      "chapter": "第一章 绪论",
      "sections": [
        {{"section": "1.1 背景介绍", "start_ts": "MM:SS", "end_ts": "MM:SS"}},
        {{"section": "1.2 核心概念", "start_ts": "MM:SS", "end_ts": "MM:SS"}}
      ]
    }}
  ]
}}

要求：
1. 章节数：3-8 章（根据内容量自适应）
2. 每章 2-5 节
3. 时间戳要从转录文本中提取（格式 [MM:SS]），尽量准确
4. 章节标题要有学术感，体现内容层次
5. 如果转录文本没有明确的时间分段，根据内容主题变化划分"""

    content, in_tok, out_tok = await _call_ai(
        model_cfg, prompt, timeout=120.0,
        sio=sio, job_id=job_id, round_label='结构分析',
    )

    # 解析 JSON
    if content.startswith('[处理失败'):
        logger.error(f'结构分析失败: {content}')
        return _fallback_structure(text)

    try:
        data = _parse_json(content)
        chapters = data.get('chapters', [])
        if not chapters:
            return _fallback_structure(text)
        logger.info(f'结构分析完成: {len(chapters)} 章')
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, f'📖 结构分析完成: {len(chapters)} 章')
        return chapters
    except Exception as e:
        logger.warning(f'结构分析解析失败: {e}，使用回退结构')
        return _fallback_structure(text)


def _fallback_structure(text: str) -> List[Dict[str, Any]]:
    """回退：按时间戳简单分组"""
    segments = re.findall(r'\[(\d{2}:\d{2})(?:\s*-\s*\d{2}:\d{2})?\](.*)', text)
    if len(segments) < 5:
        return [{"chapter": "全文内容", "sections": [{"section": "正文", "start_ts": "00:00", "end_ts": "99:99"}]}]
    # 按每 30 段分一组
    chunk_size = 30
    chapters = []
    for i in range(0, len(segments), chunk_size * 3):
        batch = segments[i:i + chunk_size * 3]
        if batch:
            first_ts = re.findall(r'(\d{2}:\d{2})', batch[0][0])[0] if batch else '00:00'
            chapters.append({
                "chapter": f"第 {len(chapters)+1} 部分",
                "sections": [
                    {"section": f"段落 {i//chunk_size + 1}", "start_ts": first_ts, "end_ts": "99:99"}
                ]
            })
    return chapters


# ============ 逐节深度总结 ============

async def _summarize_section(
    text: str,
    chapter_title: str,
    section_title: str,
    start_ts: str,
    end_ts: str,
    model_cfg: Dict[str, Any],
    sio=None,
    job_id: str = '',
) -> str:
    """对单个小节做 5 部分深度总结，返回 Markdown"""

    # 提取该时间段内的文本
    section_text = _extract_time_range(text, start_ts, end_ts)
    if len(section_text) < 100:
        section_text = text[:60000]  # 回退到全文前段

    prompt = f"""你是一位专业的教材编写者。请对以下视频转录的指定小节进行深度分析总结。

当前章节：{chapter_title}
当前小节：{section_title}
时间范围：{start_ts} - {end_ts}

转录文本（该时段内容）：
{section_text[:15000]}

请按以下 Markdown 结构输出该小节的教材内容（保留时间戳标注）：

### {section_title}

#### 📌 内容概述
（2-3 句话概括本小节讲什么）

#### 🔑 核心概念
| 概念 | 解释 |
|------|------|
| 概念名 | 详细解释 |

（至少列出 2-3 个核心概念）

#### 📚 知识点详解
**[MM:SS] 知识点标题**
（400-600字详细讲解，包含原理、逻辑、背景知识。保留转录中的时间戳 [MM:SS]）

**[MM:SS] 知识点标题**
（同上）

（至少 2 个知识点）

#### ⚠️ 注意事项
- 常见误区：xxx
- 重点提醒：xxx
- 学习建议：xxx

#### ✏️ 思考练习
1. **题目**（难度：⭐/⭐⭐/⭐⭐⭐）
   *提示：答案要点*

（至少 1 道题）

要求：
1. 知识点讲解要详细，让学生不看视频也能学会
2. 每个知识点要保留原视频中的时间戳标记
3. 使用中文"""

    content, in_tok, out_tok = await _call_ai(
        model_cfg, prompt, timeout=120.0,
        sio=sio, job_id=job_id,
        round_label=f'教材·{section_title[:20]}',
    )
    return content if not content.startswith('[处理失败') else f'### {section_title}\n\n（处理失败）'


# ============ 主流程 ============

async def textbook_process(
    text: str,
    title: str = '',
    model: str = DEFAULT_MODEL,
    sio=None,
    job_id: str = '',
) -> Tuple[str, int, int]:
    """教科书模式主流程

    返回：(textbook_markdown, total_input_tokens, total_output_tokens)
    """
    model_cfg = get_model_by_key(model) or get_model_by_key(DEFAULT_MODEL)
    if not model_cfg:
        raise Exception(f'模型 {model} 不存在')

    if sio:
        from socket_handlers import emit_progress, emit_log
        await emit_progress(sio, job_id, 'ai', 0, '📖 教科书模式：开始分析视频结构...')
        await emit_log(sio, job_id, '📖 教科书模式启动')

    total_in = 0
    total_out = 0

    # Step 1: 结构分析
    chapters = await _analyze_structure(text, title, model_cfg, sio, job_id)

    # Step 2: 计算总小节数
    all_sections = []
    for ch in chapters:
        for sec in ch.get('sections', []):
            all_sections.append((ch, sec))
    total_sections = len(all_sections)

    if total_sections == 0:
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, '⚠ 结构分析未检测到章节，使用全文总结')
        from services.ai_processor import ai_process
        return await ai_process(text, summary_type='summary', model=model, sio=sio, job_id=job_id)

    if sio:
        from socket_handlers import emit_log
        await emit_log(sio, job_id, f'📖 共 {len(chapters)} 章 {total_sections} 节，开始逐节编写教材...')

    # Step 3: 逐节深度总结
    chapter_outputs = {}  # {chapter_title: [section_markdown, ...]}
    done = 0

    for chapter, section in all_sections:
        ch_title = chapter.get('chapter', '未命名章节')
        sec_title = section.get('section', '未命名节')
        start_ts = section.get('start_ts', '00:00')
        end_ts = section.get('end_ts', '99:99')

        pct = int(5 + done / total_sections * 85)
        if sio:
            from socket_handlers import emit_progress, emit_log
            await emit_progress(sio, job_id, 'ai', pct, f'📖 {ch_title} > {sec_title}...')
            await emit_log(sio, job_id, f'📝 编写教材: {ch_title} > {sec_title} ({done+1}/{total_sections})')

        sec_md = await _summarize_section(
            text, ch_title, sec_title, start_ts, end_ts,
            model_cfg, sio, job_id,
        )
        chapter_outputs.setdefault(ch_title, []).append(sec_md)
        done += 1
        total_in += 0  # _call_ai 内部已记录
        total_out += 0

    # Step 4: 合并输出
    if sio:
        from socket_handlers import emit_progress
        await emit_progress(sio, job_id, 'ai', 92, '📖 合并教材...')

    textbook_parts = [
        f"# 📖 {title or '视频教材'}\n",
        f"> 本教材由 AI 根据视频内容自动生成，采用三层结构（章→节→知识点），适合脱离视频自学。\n",
        f"> 共 {len(chapters)} 章 {total_sections} 节\n\n",
    ]

    for ch in chapters:
        ch_title = ch.get('chapter', '未命名')
        textbook_parts.append(f"\n## {ch_title}\n")
        sections_md = chapter_outputs.get(ch_title, [])
        for sec_md in sections_md:
            textbook_parts.append(sec_md)
            textbook_parts.append('\n')

    textbook_md = '\n'.join(textbook_parts)

    if sio:
        from socket_handlers import emit_progress, emit_log
        await emit_progress(sio, job_id, 'ai', 100, '📖 教科书生成完成！')
        await emit_log(sio, job_id, f'📖 教科书生成完成: {len(textbook_md)} 字符')

    return textbook_md, total_in, total_out


# ============ 工具函数 ============

def _extract_time_range(text: str, start_ts: str, end_ts: str) -> str:
    """从转录文本中提取指定时间范围的内容"""
    lines = text.split('\n')
    result = []
    in_range = False
    for line in lines:
        ts_match = re.search(r'\[(\d{2}:\d{2})', line)
        if ts_match:
            current_ts = ts_match.group(1)
            if current_ts >= start_ts:
                in_range = True
            if current_ts > end_ts:
                break
        if in_range:
            result.append(line)
    return '\n'.join(result) if result else text[:30000]


def _parse_json(content: str) -> dict:
    """从 LLM 响应中解析 JSON"""
    content = content.strip()
    # 去掉 markdown 代码块包裹
    if content.startswith('```'):
        content = re.sub(r'^```\w*\n?', '', content)
        content = re.sub(r'\n?```$', '', content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # 尝试提取 {...}
        m = re.search(r'\{[\s\S]*\}', content)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return {}
