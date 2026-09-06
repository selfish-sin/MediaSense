"""
YouTube Tool V2 - 追问聊天路由（SSE 流式）

特性：
  - 多会话：每个视频可有多个独立对话线程（chat_sessions），每条复用该视频原文/AI结果作上下文
  - SSE 流式输出（token 级），并捕获思考过程（reasoning_content / thinking）
  - 联网搜索：智谱 web_search 优先，DDGS 兜底；结构化引用源返回前端
  - 编辑 / 重生成 / 中断残回答 均落库
"""
import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Optional, List, Dict

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from db.database import get_connection
from db.job_repo import get_job
from db.model_repo import get_model_by_key
from services.research_processor import web_search_collect
from config import DEFAULT_MODEL

logger = logging.getLogger(__name__)
router = APIRouter()

HTTPX_TIMEOUT = httpx.Timeout(180.0, connect=15.0)

DEFAULT_SESSION_TITLE = "新对话"
SESSION_TITLE_MAX = 24


class ChatMessage(BaseModel):
    content: str
    model: Optional[str] = None
    enable_search: bool = False
    regenerate: bool = False
    user_message_id: Optional[int] = None


class SessionCreate(BaseModel):
    title: Optional[str] = None


class SessionRename(BaseModel):
    title: str


# ============ DB 辅助 ============

def _gen_session_id():
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


def _insert_chat(job_id, session_id, role, content, thinking=None, sources=None, token_usage=None):
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO job_chats (job_id, session_id, role, content, thinking, sources, token_usage) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                job_id, session_id, role, content, thinking,
                json.dumps(sources, ensure_ascii=False) if sources is not None else None,
                json.dumps(token_usage or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _get_session_history(job_id, session_id, limit=30):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT role, content FROM job_chats WHERE job_id = ? AND session_id = ? ORDER BY id DESC LIMIT ?",
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


def _build_messages(original_ctx, result_ctx, history, question, search_text, regenerate):
    ctx = [
        "视频转录原文（节选）：\n" + (original_ctx or "(无)"),
        "\n已有 AI 分析结果：\n" + (result_ctx or "(无)"),
    ]
    if search_text:
        ctx.append("\n联网搜索资料：\n" + search_text)

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
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, role, content, thinking, sources, token_usage, created_at "
            "FROM job_chats WHERE job_id = ? AND session_id = ? ORDER BY id ASC",
            (job_id, session_id),
        ).fetchall()
        messages = []
        for r in rows:
            try:
                sources = _parse_json(r["sources"], [])
            except Exception:
                sources = []
            messages.append({
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "thinking": r["thinking"] or "",
                "sources": sources,
                "token_usage": _parse_json(r["token_usage"], {}),
                "created_at": str(r["created_at"]),
            })
        return {"job_id": job_id, "session_id": session_id, "messages": messages}
    finally:
        conn.close()


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

        try:
            # 先取历史再插入新问题——否则历史里已含本条问题，
            # _build_messages 末尾再 append 一次会把问题发给模型两遍
            history = _get_session_history(job_id, session_id)

            if not msg.regenerate:
                user_id = _insert_chat(job_id, session_id, "user", question)
                _maybe_rename_session(job_id, session_id, question)

            search_text = ""
            if msg.enable_search:
                try:
                    sources, search_text = await web_search_collect(question, result_ctx)
                except Exception as e:
                    logger.warning(f"联网搜索异常: {e}")
                    sources, search_text = [], ""

            messages = _build_messages(
                original_ctx, result_ctx, history, question, search_text, msg.regenerate
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

            async with httpx.AsyncClient(timeout=HTTPX_TIMEOUT) as client:
                async with client.stream("POST", endpoint, json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        err = await resp.aread()
                        try:
                            err_text = err.decode("utf-8", "ignore")
                        except Exception:
                            err_text = str(err)
                        yield _sse({"type": "error", "message": f"模型调用失败 HTTP {resp.status_code}: {err_text[:300]}"})
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
            )
            if not disconnected:
                yield _sse({"type": "sources", "sources": sources})
                yield _sse({
                    "type": "done",
                    "session_id": session_id,
                    "user_id": user_id,
                    "assistant_id": assistant_id,
                    "token_usage": {"input": in_tok, "output": out_tok},
                })
        except Exception as e:
            logger.exception("追问流式生成异常")
            try:
                assistant_id = _insert_chat(
                    job_id, session_id, "assistant", "".join(content_text) or "(生成出错)",
                    thinking="".join(thinking_text) or None,
                    sources=sources,
                    token_usage={"input": in_tok, "output": out_tok},
                )
            except Exception:
                assistant_id = None
            if not await request.is_disconnected():
                yield _sse({"type": "sources", "sources": sources})
                yield _sse({"type": "error", "message": str(e)[:300]})
                yield _sse({
                    "type": "done",
                    "session_id": session_id,
                    "user_id": user_id,
                    "assistant_id": assistant_id,
                    "token_usage": {"input": in_tok, "output": out_tok},
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
    _delete_chat_message(job_id, msg_id)
    return {"message": "已删除该消息"}


@router.delete("/{job_id}/chat/from/{msg_id}")
def delete_chat_from_endpoint(job_id: str, msg_id: int):
    _delete_chat_from(job_id, msg_id)
    return {"message": "已删除该消息及其后续"}
