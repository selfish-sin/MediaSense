"""
YouTube Tool V2 - 追问聊天路由（SSE 流式）

特性：
  - 多会话：每个视频可有多个独立对话线程（chat_sessions），每条复用该视频原文/AI结果作上下文
  - SSE 流式输出（token 级），并捕获思考过程（reasoning_content / thinking）
  - 联网搜索：`services/web_search` 单点（Ollama → Tavily → Firecrawl → DuckDuckGo 逐级回退）；
    结构化引用源返回前端，检索失败原因经 SSE `search` 事件显式透出（不静默）
  - 联网双模式（search_mode）：
      strict 严格依据材料——单次检索问题本身，回答严格基于转录原文+AI结果+检索资料并编号引用
      broad  广泛收集——并行两路检索（问题本身 + 视频标题+问题）合并去重，回答围绕问题放开材料约束
  - 编辑 / 重生成 / 中断残回答 均落库
"""
import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from db.database import get_connection
from db.job_repo import get_job
from db.model_repo import get_model_by_key
from services.web_search import web_search_collect, fmt_sources, SearchOutcome
from services.httpx_errors import describe_http_response
from config import DEFAULT_MODEL

logger = logging.getLogger(__name__)
router = APIRouter()

HTTPX_TIMEOUT = httpx.Timeout(180.0, connect=15.0)

DEFAULT_SESSION_TITLE = "新对话"
SESSION_TITLE_MAX = 24

# 广泛模式检索编排：问题本身 + 视频标题组合，两路并行
BROAD_PER_QUERY_RESULTS = 6
BROAD_MAX_SOURCES = 12
BROAD_QUERY_MAX = 120

# 多选批量删除单次上限（防单个请求删除过量；超限提示分批）
MAX_BATCH_DELETE = 200


class ChatMessage(BaseModel):
    content: str
    model: Optional[str] = None
    enable_search: bool = False
    # 联网模式：strict=严格依据材料 / broad=广泛收集；None 时回退 enable_search（向后兼容）
    search_mode: Optional[Literal["strict", "broad", "off"]] = None
    regenerate: bool = False
    user_message_id: Optional[int] = None


class SessionCreate(BaseModel):
    title: Optional[str] = None


class SessionRename(BaseModel):
    title: str


class MessagesDelete(BaseModel):
    """多选批量删除的入参（消息行 id 集合）"""

    ids: List[int] = []


# ============ DB 辅助 ============

def _gen_session_id():
    return str(uuid.uuid4())[:12]


def _gen_variant_group():
    return str(uuid.uuid4())[:12]


def _ensure_session(job_id, session_id):
    """确保会话存在（前端传来的 session_id 若不存在则创建默认标题会话）"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM chat_sessions WHERE id = ? AND job_id = ?",
            (session_id, job_id),
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO chat_sessions (id, job_id, title) VALUES (?, ?, ?)",
                (session_id, job_id, DEFAULT_SESSION_TITLE),
            )
            conn.commit()
    finally:
        conn.close()


def _insert_chat(job_id, session_id, role, content, thinking=None, sources=None, token_usage=None,
                 variant_group="", is_active=1):
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO job_chats (job_id, session_id, role, content, thinking, sources, token_usage, "
            "variant_group, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id, session_id, role, content, thinking,
                json.dumps(sources, ensure_ascii=False) if sources is not None else None,
                json.dumps(token_usage or {}, ensure_ascii=False),
                variant_group or "", 1 if is_active else 0,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _variant_rows(job_id, session_id, group):
    """组内全部变体（id 升序）；空组返回空列表"""
    if not group:
        return []
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, role, is_active, created_at, token_usage FROM job_chats "
            "WHERE job_id = ? AND session_id = ? AND variant_group = ? AND role = 'assistant' ORDER BY id ASC",
            (job_id, session_id, group),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _activate_variant(job_id, session_id, group, msg_id):
    """组内 is_active 互斥切换；msg_id 不在组内时返回 None"""
    conn = get_connection()
    try:
        member = conn.execute(
            "SELECT id FROM job_chats WHERE job_id = ? AND session_id = ? AND variant_group = ? AND id = ?",
            (job_id, session_id, group, msg_id),
        ).fetchone()
        if not member:
            return False
        conn.execute(
            "UPDATE job_chats SET is_active = CASE WHEN id = ? THEN 1 ELSE 0 END "
            "WHERE job_id = ? AND session_id = ? AND variant_group = ?",
            (msg_id, job_id, session_id, group),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _get_session_history(job_id, session_id, limit=30):
    """模型上下文用：排除非激活变体（后续提问只引用生效版本）。

    is_active 兜底：存量行/用户行默认 1；条件写成
    `(role = 'user' OR is_active = 1)`——用户消息恒参与，assistant 只取生效版本。
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT role, content FROM job_chats WHERE job_id = ? AND session_id = ? "
            "AND (role = 'user' OR is_active = 1) ORDER BY id DESC LIMIT ?",
            (job_id, session_id, limit),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    finally:
        conn.close()


def _maybe_rename_session(job_id, session_id, question):
    """若会话仍是默认标题（未手动命名），用首句提问自动命名"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT title FROM chat_sessions WHERE id = ? AND job_id = ?",
            (session_id, job_id),
        ).fetchone()
        if not row:
            return
        title = row["title"] or ""
        if title == DEFAULT_SESSION_TITLE or not title.strip():
            new_title = question.strip().replace("\n", " ")[:SESSION_TITLE_MAX] or DEFAULT_SESSION_TITLE
            conn.execute(
                "UPDATE chat_sessions SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND job_id = ?",
                (new_title, session_id, job_id),
            )
            conn.commit()
    finally:
        conn.close()


def _delete_chat_message(job_id, msg_id):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM job_chats WHERE job_id = ? AND id = ?", (job_id, msg_id))
        conn.commit()
    finally:
        conn.close()


def _delete_chat_from(job_id, msg_id):
    """删除该消息及其后续，且仅限该消息所属会话（避免跨会话误删）。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT session_id FROM job_chats WHERE job_id = ? AND id = ?",
            (job_id, msg_id),
        ).fetchone()
        if not row:
            return
        session_id = row["session_id"]
        if session_id:
            conn.execute(
                "DELETE FROM job_chats WHERE job_id = ? AND session_id = ? AND id >= ?",
                (job_id, session_id, msg_id),
            )
        else:
            # 旧数据无 session_id：只删同 job 且 session 为空的后续
            conn.execute(
                "DELETE FROM job_chats WHERE job_id = ? AND id >= ? "
                "AND (session_id IS NULL OR session_id = '')",
                (job_id, msg_id),
            )
        conn.commit()
    finally:
        conn.close()


def _next_user_id(conn, job_id, session_id, after_id):
    """同会话内 after_id 之后的第一条提问行 id（无则 None）；存量无 session 行按"空会话"分组。"""
    if session_id:
        row = conn.execute(
            "SELECT MIN(id) FROM job_chats WHERE job_id = ? AND session_id = ? "
            "AND role = 'user' AND id > ?",
            (job_id, session_id, after_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT MIN(id) FROM job_chats WHERE job_id = ? AND (session_id IS NULL OR session_id = '') "
            "AND role = 'user' AND id > ?",
            (job_id, after_id),
        ).fetchone()
    return row[0] if row and row[0] is not None else None


def _delete_turn(conn, job_id, user_id, session_id):
    """删整轮：该提问 + 其全部回答版本（到下一条提问之前），**不牵连后续轮次**。

    与 `_delete_chat_from`（级联到会话末尾，编辑回填用）的区别：多选语境下用户逐项勾选，
    不能因为勾了一条中间提问就把后面的轮次一起删掉。
    """
    nxt = _next_user_id(conn, job_id, session_id, user_id)
    if session_id:
        sql = "DELETE FROM job_chats WHERE job_id = ? AND session_id = ? AND id >= ?"
        params = [job_id, session_id, user_id]
    else:
        sql = ("DELETE FROM job_chats WHERE job_id = ? AND (session_id IS NULL OR session_id = '') "
               "AND id >= ?")
        params = [job_id, user_id]
    if nxt is not None:
        sql += " AND id < ?"
        params.append(nxt)
    conn.execute(sql, tuple(params))


def _answer_owner_user_id(conn, job_id, session_id, anchor_id):
    """回答行所属的提问行 id（同会话内 anchor 之前最后一条 user 行）"""
    if not session_id:
        return None
    row = conn.execute(
        "SELECT MAX(id) FROM job_chats WHERE job_id = ? AND session_id = ? "
        "AND role = 'user' AND id < ?",
        (job_id, session_id, anchor_id),
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def _prune_orphan_question(conn, job_id, session_id, anchor_id):
    """该轮回答已清空 → 连带删除所属提问行（防孤儿提问，与单删同规则）"""
    owner = _answer_owner_user_id(conn, job_id, session_id, anchor_id)
    if owner is None:
        return
    nxt = _next_user_id(conn, job_id, session_id, owner)
    sql = ("SELECT COUNT(*) FROM job_chats WHERE job_id = ? AND session_id = ? "
           "AND role = 'assistant' AND id > ?")
    params = [job_id, session_id, owner]
    if nxt is not None:
        sql += " AND id < ?"
        params.append(nxt)
    if conn.execute(sql, tuple(params)).fetchone()[0] == 0:
        conn.execute("DELETE FROM job_chats WHERE id = ?", (owner,))


def _delete_answer(conn, job_id, msg_id):
    """删该回答：有变体组则全组删除（含全部历史版本），无组则只删该行；
    两种情形都在删空该轮回答后连带删除提问行（防孤儿）。返回 False 表示行已不存在。"""
    row = conn.execute(
        "SELECT session_id, variant_group FROM job_chats WHERE job_id = ? AND id = ?",
        (job_id, msg_id),
    ).fetchone()
    if not row:
        return False
    session_id = row["session_id"]
    group = row["variant_group"] or ""
    if group:
        first = conn.execute(
            "SELECT MIN(id) FROM job_chats WHERE job_id = ? AND variant_group = ?",
            (job_id, group),
        ).fetchone()[0]
        if session_id:
            conn.execute(
                "DELETE FROM job_chats WHERE job_id = ? AND session_id = ? AND variant_group = ?",
                (job_id, session_id, group),
            )
        else:
            conn.execute(
                "DELETE FROM job_chats WHERE job_id = ? AND variant_group = ? "
                "AND (session_id IS NULL OR session_id = '')",
                (job_id, group),
            )
        anchor = first if first is not None else msg_id
    else:
        conn.execute("DELETE FROM job_chats WHERE job_id = ? AND id = ?", (job_id, msg_id))
        anchor = msg_id
    _prune_orphan_question(conn, job_id, session_id, anchor)
    return True


def _sse(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

def _parse_json(value, default):
    if not value:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _truncate(text, max_len, tail=0):
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    if tail > 0 and len(text) > max_len + tail:
        return text[:max_len] + "\n\n...(中间省略)...\n\n" + text[-tail:]
    return text[:max_len] + "\n\n...(内容较长，已截断)..."


def _resolve_search_mode(msg: "ChatMessage") -> str:
    """search_mode 显式给出时优先；否则回退旧布尔开关（True→strict，向后兼容）"""
    if msg.search_mode is not None:
        return msg.search_mode
    return "strict" if msg.enable_search else "off"


async def _run_search(mode: str, question: str, job_title: str = "") -> SearchOutcome:
    """按模式编排联网检索。

    strict：单次检索问题本身（与 2026-09-16 引入联网时的行为一致）。
    broad：问题本身 + 「视频标题+问题」组合两路并行，合并去重（≤12 条），
           单路失败不拖整体，两路全失败时错误原因合并透出。
    """
    if mode != "broad":
        return await web_search_collect(question, snippet_limit=500)

    queries = [question]
    title = (job_title or "").strip()
    combo = f"{title} {question}".strip()[:BROAD_QUERY_MAX]
    if title and combo != question:
        queries.append(combo)

    outcomes = await asyncio.gather(
        *(web_search_collect(q, max_results=BROAD_PER_QUERY_RESULTS, snippet_limit=900)
          for q in queries),
        return_exceptions=True,
    )

    sources: List[Dict[str, str]] = []
    provider = ""
    errors: List[str] = []
    seen_urls = set()
    for o in outcomes:
        if isinstance(o, Exception):
            errors.append(str(o)[:200])
            continue
        if o.ok:
            provider = provider or o.provider
            for s in o.sources:
                url = (s.get("url") or "").rstrip("/")
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                sources.append(s)
        else:
            errors.append(o.error)

    if not sources:
        return SearchOutcome(error="；".join(e for e in errors if e) or "联网检索失败")
    if len(sources) > BROAD_MAX_SOURCES:
        sources = sources[:BROAD_MAX_SOURCES]
    if errors:
        logger.warning(f"[chat] 广泛检索部分失败: {'；'.join(errors)[:300]}")
    return SearchOutcome(sources=sources, text=fmt_sources(sources), provider=provider)


def _build_messages(original_ctx, result_ctx, history, question, search_text, regenerate,
                    search_mode="off"):
    ctx = [
        "视频转录原文（节选）：\n" + (original_ctx or "(无)"),
        "\n已有 AI 分析结果：\n" + (result_ctx or "(无)"),
    ]
    if search_text:
        ctx.append("\n联网搜索资料：\n" + search_text)

    if search_mode == "broad":
        # 广泛模式：材料仅作背景参考，围绕问题放开作答（检索资料仍须编号引用）
        system = (
            "你是专业的视频内容研究助手。用户会基于一段视频的主题提出问题进行深入追问。"
            "请围绕用户的问题广泛、深入、多角度地作答：下方的视频转录原文与已有 AI 分析结果"
            "仅作背景参考，不是约束；优先综合联网检索资料与你自身的知识展开，信息要充分。"
            "用清晰、准确、结构化的中文回答，合理使用 Markdown（标题、列表、加粗、表格等）。"
        )
        if search_text:
            system += (
                "你已获得联网搜索资料。请在回答正文中用 [1]、[2] 等编号标注引用来源，"
                "并在回答末尾以『## 参考来源』列出对应链接；不要编造资料中不存在的信息。"
            )
        else:
            system += "本次没有联网检索资料，请基于你自身的知识回答，涉及不确定的信息请如实说明。"
    else:
        system = (
            "你是专业的视频内容分析助手。用户会针对一段视频的转录原文与已有的AI分析结果进行追问。"
            "请结合下方背景资料，用清晰、准确、结构化的中文回答，合理使用 Markdown（标题、列表、加粗、表格等）。"
            "不要编造背景资料中不存在的信息；若资料中找不到答案，请明确说明。"
        )
        if search_text:
            system += (
                "你已获得联网搜索资料。请在回答正文中用 [1]、[2] 等编号标注引用来源，"
                "并在回答末尾以『## 参考来源』列出对应链接。"
            )

    messages = [{"role": "system", "content": system + "\n\n# 背景资料\n" + "\n".join(ctx)}]
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})
    if not regenerate:
        messages.append({"role": "user", "content": question})
    return messages


# ============ 会话 CRUD ============

@router.get("/{job_id}/chat/sessions")
def list_sessions(job_id: str):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT s.id, s.title, s.created_at, s.updated_at, "
            "(SELECT COUNT(*) FROM job_chats c WHERE c.session_id = s.id) AS msg_count "
            "FROM chat_sessions s WHERE s.job_id = ? ORDER BY s.created_at ASC, s.id ASC",
            (job_id,),
        ).fetchall()
        sessions = [dict(r) for r in rows]
        return {"job_id": job_id, "sessions": sessions}
    finally:
        conn.close()


@router.post("/{job_id}/chat/sessions")
def create_session(job_id: str, body: SessionCreate):
    job = get_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"detail": "任务不存在"})
    sid = _gen_session_id()
    title = (body.title or "").strip() or DEFAULT_SESSION_TITLE
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO chat_sessions (id, job_id, title) VALUES (?, ?, ?)",
            (sid, job_id, title),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": sid, "job_id": job_id, "title": title}


@router.put("/{job_id}/chat/sessions/{session_id}")
def rename_session(job_id: str, session_id: str, body: SessionRename):
    title = (body.title or "").strip()
    if not title:
        return JSONResponse(status_code=400, content={"detail": "标题不能为空"})
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE chat_sessions SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND job_id = ?",
            (title[:50], session_id, job_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": session_id, "title": title[:50]}


@router.delete("/{job_id}/chat/sessions/{session_id}")
def delete_session(job_id: str, session_id: str):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM job_chats WHERE job_id = ? AND session_id = ?", (job_id, session_id))
        conn.execute("DELETE FROM chat_sessions WHERE id = ? AND job_id = ?", (session_id, job_id))
        conn.commit()
    finally:
        conn.close()
    return {"message": "会话已删除"}


@router.delete("/{job_id}/chat/sessions/{session_id}/messages")
def clear_session_messages(job_id: str, session_id: str):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM job_chats WHERE job_id = ? AND session_id = ?", (job_id, session_id))
        conn.commit()
    finally:
        conn.close()
    return {"message": "会话消息已清空"}


@router.get("/{job_id}/chat/sessions/{session_id}/history")
def get_session_history(job_id: str, session_id: str):
    """会话历史（前端渲染用）：返回**全部行**（含非激活变体），
    assistant 消息附 variant_group / is_active / variants 组摘要，由前端按组选显。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, role, content, thinking, sources, token_usage, created_at, variant_group, is_active "
            "FROM job_chats WHERE job_id = ? AND session_id = ? ORDER BY id ASC",
            (job_id, session_id),
        ).fetchall()
        # 组摘要缓存：group -> [{id, created_at, token_usage}]
        variant_index = {}
        for r in rows:
            g = r["variant_group"] or ""
            if r["role"] == "assistant" and g:
                variant_index.setdefault(g, []).append({
                    "id": r["id"],
                    "created_at": str(r["created_at"]),
                    "token_usage": _parse_json(r["token_usage"], {}),
                })
        messages = []
        for r in rows:
            try:
                sources = _parse_json(r["sources"], [])
            except Exception:
                sources = []
            group = r["variant_group"] or ""
            messages.append({
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "thinking": r["thinking"] or "",
                "sources": sources,
                "token_usage": _parse_json(r["token_usage"], {}),
                "created_at": str(r["created_at"]),
                "variant_group": group,
                "is_active": bool(r["is_active"] if r["is_active"] is not None else 1),
                "variants": variant_index.get(group, []) if (r["role"] == "assistant" and group) else [],
            })
        return {"job_id": job_id, "session_id": session_id, "messages": messages}
    finally:
        conn.close()


@router.post("/{job_id}/chat/variants/{msg_id}/activate")
def activate_variant(job_id: str, msg_id: int):
    """切换变体组内生效版本（组归属经消息行反查，前端不用传 group）"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT session_id, variant_group FROM job_chats WHERE job_id = ? AND id = ? AND role = 'assistant'",
            (job_id, msg_id),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return JSONResponse(status_code=404, content={"detail": "消息不存在"})
    group = row["variant_group"] or ""
    if not group:
        # 存量单变体行：无可切换对象，幂等成功
        return {"id": msg_id, "variant_count": 1}
    ok = _activate_variant(job_id, row["session_id"], group, msg_id)
    if not ok:
        return JSONResponse(status_code=404, content={"detail": "消息不存在"})
    return {"id": msg_id, "variant_count": len(_variant_rows(job_id, row["session_id"], group))}


@router.get("/{job_id}/chat/history")
def get_legacy_chat_history(job_id: str):
    """兼容旧版单会话聊天历史接口。新前端应使用 session history。"""
    job = get_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"detail": "任务不存在"})
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, role, content, thinking, sources, token_usage, created_at "
            "FROM job_chats WHERE job_id = ? ORDER BY id ASC",
            (job_id,),
        ).fetchall()
        messages = []
        for r in rows:
            try:
                sources = json.loads(r["sources"]) if r["sources"] else []
            except Exception:
                sources = []
            try:
                token_usage = json.loads(r["token_usage"]) if r["token_usage"] else {}
            except Exception:
                token_usage = {}
            messages.append({
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "thinking": r["thinking"] or "",
                "sources": sources,
                "token_usage": token_usage,
                "created_at": str(r["created_at"]),
            })
        return {"job_id": job_id, "messages": messages}
    finally:
        conn.close()


# ============ SSE 流式追问（按会话） ============

@router.post("/{job_id}/chat/sessions/{session_id}/stream")
async def chat_stream(job_id: str, session_id: str, msg: ChatMessage, request: Request):
    job = get_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"detail": "任务不存在"})

    result_text = job.get("result_text", "")
    if not result_text:
        return JSONResponse(status_code=400, content={"detail": "该任务还没有AI分析结果，无法追问"})

    model_key = msg.model or job.get("model", DEFAULT_MODEL)
    model_cfg = get_model_by_key(model_key)
    if not model_cfg:
        return JSONResponse(status_code=400, content={"detail": f"模型 {model_key} 不存在，请在模型管理中添加"})

    endpoint = model_cfg.get("endpoint")
    if not endpoint:
        return JSONResponse(status_code=400, content={"detail": f"模型 {model_key} 未配置 endpoint"})

    _ensure_session(job_id, session_id)

    files = job.get("files", {}) or {}
    if isinstance(files, str):
        try:
            files = json.loads(files)
        except Exception:
            files = {}
    txt_path = files.get("txt", "")
    original_text = ""
    if txt_path:
        try:
            original_text = await asyncio.to_thread(
                Path(txt_path).read_text, encoding="utf-8"
            )
        except Exception:
            original_text = ""

    original_ctx = _truncate(original_text, 12000, tail=4000)
    result_ctx = _truncate(result_text, 16000)
    question = (msg.content or "").strip()
    if not question and not msg.regenerate:
        return JSONResponse(status_code=400, content={"detail": "追问内容不能为空"})

    async def event_generator():
        thinking_text: List[str] = []
        content_text: List[str] = []
        sources: List[Dict[str, str]] = []
        user_id = None
        in_tok = out_tok = 0
        disconnected = False
        variant_group = ""
        variant_count = 1

        try:
            # 先取历史再插入新问题——否则历史里已含本条问题，
            # _build_messages 末尾再 append 一次会把问题发给模型两遍
            history = _get_session_history(job_id, session_id)

            if not msg.regenerate:
                user_id = _insert_chat(job_id, session_id, "user", question)
                _maybe_rename_session(job_id, session_id, question)
            else:
                # 重新生成 = 追加新变体：定位最后一条 user 行之后的当前生效回答，
                # 归入其 variant_group（无组则从本代起新建组），组内旧版本全部转为非激活
                conn = get_connection()
                try:
                    last_user = conn.execute(
                        "SELECT id, content FROM job_chats "
                        "WHERE job_id = ? AND session_id = ? AND role = 'user' ORDER BY id DESC LIMIT 1",
                        (job_id, session_id),
                    ).fetchone()
                    prev = None
                    if last_user:
                        prev = conn.execute(
                            "SELECT id, variant_group FROM job_chats "
                            "WHERE job_id = ? AND session_id = ? AND role = 'assistant' AND id > ? "
                            "ORDER BY id ASC LIMIT 1",
                            (job_id, session_id, last_user["id"]),
                        ).fetchone()
                finally:
                    conn.close()
                if prev is None:
                    yield _sse({"type": "error", "message": "没有可重新生成的回答"})
                    return
                variant_group = prev["variant_group"] or _gen_variant_group()
                conn = get_connection()
                try:
                    # 存量独立回答（无组）先认领进组，否则旧版本仍 is_active=1 继续进上下文
                    if not prev["variant_group"]:
                        conn.execute(
                            "UPDATE job_chats SET variant_group = ? WHERE id = ?",
                            (variant_group, prev["id"]),
                        )
                    conn.execute(
                        "UPDATE job_chats SET is_active = 0 "
                        "WHERE job_id = ? AND session_id = ? AND variant_group = ? AND role = 'assistant'",
                        (job_id, session_id, variant_group),
                    )
                    conn.commit()
                finally:
                    conn.close()

            search_mode = _resolve_search_mode(msg)
            search_text = ""
            if search_mode != "off":
                # 重生成时提问可能为空 → 回退用最近一条用户消息作检索词（无检索词则如实报失败）
                search_query = question
                if not search_query and msg.regenerate:
                    search_query = next(
                        (h["content"] for h in reversed(history) if h.get("role") == "user"), "")
                if not search_query:
                    logger.warning("联网搜索跳过：无可检索的问题内容")
                    yield _sse({"type": "search", "status": "failed", "mode": search_mode,
                                "message": "没有可用于检索的问题内容"})
                else:
                    # 联网检索：结果与失败原因都推给前端（此前失败静默返回空 → 开关形同虚设）
                    yield _sse({"type": "search", "status": "searching", "mode": search_mode})
                    try:
                        outcome = await _run_search(search_mode, search_query, job.get("title", ""))
                    except Exception as e:
                        # _run_search 内部已兜异常，这里只防它自身出 bug：也要可见，不吞
                        logger.exception("联网搜索异常")
                        yield _sse({"type": "search", "status": "failed", "mode": search_mode,
                                    "message": str(e)[:300]})
                    else:
                        sources = outcome.sources
                        search_text = outcome.text
                        if outcome.ok:
                            logger.info(
                                f"追问联网搜索命中 {len(sources)} 条来源（{outcome.provider}，{search_mode}）"
                            )
                            yield _sse({
                                "type": "search", "status": "ok", "mode": search_mode,
                                "provider": outcome.provider, "count": len(sources),
                            })
                        else:
                            logger.warning(f"追问联网搜索不可用: {outcome.error}")
                            yield _sse({
                                "type": "search", "status": "failed", "mode": search_mode,
                                "message": outcome.error,
                            })

            messages = _build_messages(
                original_ctx, result_ctx, history, question, search_text, msg.regenerate,
                search_mode=search_mode,
            )

            payload = {
                "model": model_cfg.get("model_id") or model_cfg.get("key") or model_key,
                "messages": messages,
                "stream": True,
                "temperature": model_cfg.get("temperature", 0.3),
                "max_tokens": model_cfg.get("max_tokens", 4096),
            }
            headers = {"Content-Type": "application/json"}
            if model_cfg.get("auth_type") != "none":
                headers["Authorization"] = f"Bearer {model_cfg.get('api_key', '')}"

            async with httpx.AsyncClient(timeout=HTTPX_TIMEOUT,
                                     verify=not bool(model_cfg.get('skip_ssl_verify'))) as client:
                async with client.stream("POST", endpoint, json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        # ★ 流式响应必须先读完整 body 才能读取 response.text 透出真实原因，
                        #   否则 describe_http_response 访问未读流会抛 ResponseNotRead
                        await resp.aread()
                        yield _sse({"type": "error", "message": f"模型调用失败 {describe_http_response(resp)}"})
                        return
                    async for line in resp.aiter_lines():
                        if await request.is_disconnected():
                            disconnected = True
                            break
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except Exception:
                            continue
                        if chunk.get("error"):
                            yield _sse({"type": "error", "message": str(chunk["error"])[:300]})
                            return
                        choice = (chunk.get("choices") or [{}])[0]
                        delta = choice.get("delta", {})
                        reasoning = delta.get("reasoning_content") or delta.get("thinking") or ""
                        content = delta.get("content") or ""
                        if reasoning:
                            thinking_text.append(reasoning)
                            yield _sse({"type": "thinking", "delta": reasoning})
                        if content:
                            content_text.append(content)
                            yield _sse({"type": "content", "delta": content})
                        usage = chunk.get("usage")
                        if usage and isinstance(usage, dict):
                            in_tok = usage.get("prompt_tokens", in_tok)
                            out_tok = usage.get("completion_tokens", out_tok)

            assistant_id = _insert_chat(
                job_id, session_id, "assistant", "".join(content_text),
                thinking="".join(thinking_text) or None,
                sources=sources,
                token_usage={"input": in_tok, "output": out_tok},
                variant_group=variant_group,
                is_active=1,
            )
            if variant_group:
                variant_count = len(_variant_rows(job_id, session_id, variant_group))
            if not disconnected:
                yield _sse({"type": "sources", "sources": sources})
                yield _sse({
                    "type": "done",
                    "session_id": session_id,
                    "user_id": user_id,
                    "assistant_id": assistant_id,
                    "token_usage": {"input": in_tok, "output": out_tok},
                    "variant_group": variant_group,
                    "variant_count": variant_count,
                })
        except Exception as e:
            logger.exception("追问流式生成异常")
            try:
                assistant_id = _insert_chat(
                    job_id, session_id, "assistant", "".join(content_text) or "(生成出错)",
                    thinking="".join(thinking_text) or None,
                    sources=sources,
                    token_usage={"input": in_tok, "output": out_tok},
                    variant_group=variant_group,
                    is_active=1,
                )
            except Exception:
                assistant_id = None
            if variant_group and assistant_id:
                variant_count = len(_variant_rows(job_id, session_id, variant_group))
            if not await request.is_disconnected():
                yield _sse({"type": "sources", "sources": sources})
                yield _sse({"type": "error", "message": str(e)[:300]})
                yield _sse({
                    "type": "done",
                    "session_id": session_id,
                    "user_id": user_id,
                    "assistant_id": assistant_id,
                    "token_usage": {"input": in_tok, "output": out_tok},
                    "variant_group": variant_group,
                    "variant_count": variant_count,
                })

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream",
        },
    )


# ============ 单条消息删除 ============

@router.delete("/{job_id}/chat/{msg_id}")
def delete_chat_message(job_id: str, msg_id: int):
    """单条删除：assistant 且同组有其他变体 → 只删该变体，删的是生效版本则由组内最新接管；
    组删空 → 连同所属 user 行删除（防孤儿提问）。user 行 → 等同整轮删除。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT role, session_id, variant_group FROM job_chats WHERE job_id = ? AND id = ?",
            (job_id, msg_id),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"message": "已删除该消息"}
    session_id = row["session_id"]
    group = row["variant_group"] or ""
    if row["role"] == "assistant" and session_id and group:
        conn = get_connection()
        try:
            conn.execute(
                "DELETE FROM job_chats WHERE id = ? AND job_id = ?", (msg_id, job_id))
            remaining = conn.execute(
                "SELECT id FROM job_chats WHERE job_id = ? AND session_id = ? AND variant_group = ? "
                "AND role = 'assistant' ORDER BY id DESC",
                (job_id, session_id, group),
            ).fetchall()
            if remaining:
                # 删的是生效版本 → 组内最新接管；否则保持原激活行不动
                still_active = conn.execute(
                    "SELECT id FROM job_chats WHERE job_id = ? AND session_id = ? AND variant_group = ? AND is_active = 1",
                    (job_id, session_id, group),
                ).fetchone()
                if not still_active:
                    conn.execute(
                        "UPDATE job_chats SET is_active = 1 WHERE id = ?", (remaining[0]["id"],))
            else:
                # 组空：删所属 user 行（组已无行，以其被删变体的最小 id 为上界找最后一条 user）
                conn.execute(
                    "DELETE FROM job_chats WHERE id = ("
                    "  SELECT MAX(id) FROM job_chats"
                    "  WHERE job_id = ? AND session_id = ? AND role = 'user' AND id < ?"
                    ")",
                    (job_id, session_id, msg_id),
                )
            conn.commit()
        finally:
            conn.close()
        return {"message": "已删除该回答版本"}
    if row["role"] == "user" and session_id:
        # user 行 = 整轮：它及之后全部（含该轮的整个变体组）
        _delete_chat_from(job_id, msg_id)
        return {"message": "已删除该消息及其后续"}
    _delete_chat_message(job_id, msg_id)
    return {"message": "已删除该消息"}


@router.delete("/{job_id}/chat/from/{msg_id}")
def delete_chat_from_endpoint(job_id: str, msg_id: int):
    _delete_chat_from(job_id, msg_id)
    return {"message": "已删除该消息及其后续"}


# ============ 多选批量删除 ============

@router.post("/{job_id}/chat/messages/delete")
def delete_chat_messages(job_id: str, body: MessagesDelete):
    """多选批量删除（单事务）。语义与单删一致但按"项"删除、不级联到后续轮次：

    - 提问行 → 删该提问整轮（提问 + 其全部回答版本）；
    - 回答行 → 删该回答的全部版本（含历史变体）；该轮回答删空后提问成孤儿 → 一并删除。
    不存在/已删除的 id 静默跳过（幂等），返回实际删除行数。
    """
    ids = list(dict.fromkeys(body.ids))  # 去重保序
    if not ids:
        raise HTTPException(status_code=400, detail="未选择要删除的消息")
    if len(ids) > MAX_BATCH_DELETE:
        raise HTTPException(
            status_code=400, detail=f"一次最多删除 {MAX_BATCH_DELETE} 项，请分批操作")
    conn = get_connection()
    try:
        before = conn.total_changes
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id, role, session_id FROM job_chats WHERE job_id = ? AND id IN ({placeholders})",
            (job_id, *ids),
        ).fetchall()
        by_id = {r["id"]: r for r in rows}
        # 先处理提问行（整轮），后处理回答行——被整轮覆盖的回答行查不到会自然跳过
        for rid in sorted(i for i in ids if i in by_id and by_id[i]["role"] == "user"):
            _delete_turn(conn, job_id, rid, by_id[rid]["session_id"])
        for rid in sorted(i for i in ids if i in by_id and by_id[i]["role"] == "assistant"):
            _delete_answer(conn, job_id, rid)
        conn.commit()
        deleted = conn.total_changes - before
    finally:
        conn.close()
    logger.info(f"[chat] 批量删除 {job_id}: 请求 {len(ids)} 项 → 实际删除 {deleted} 行")
    return {"deleted": deleted, "message": f"已删除 {deleted} 行"}
