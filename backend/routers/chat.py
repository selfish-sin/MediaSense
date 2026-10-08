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
import os
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

# ★ 模型调用超时（2026-10-08 并入预算体系）：
#   此前追问写死 httpx.Timeout(180.0)，而批量总结流式是 asyncio.wait_for(..., timeout=600)
#   （services/ai_processor.py）——同一个模型在批里跑得完、在追问里被断，是「莫名其妙失败」
#   的一大类成因，且 180s 到点时错误文案只是一句无信息量的 timeout。
#   现在默认值与批量对齐（600s），两项均可 env 调整：
#     CHAT_MODEL_TIMEOUT_SECONDS        总/读超时（默认 600，与批量流式一致）
#     CHAT_MODEL_CONNECT_TIMEOUT_SECONDS 连接超时（默认 15，快速失败，不占长预算）
def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "")
    try:
        value = float(raw)
    except ValueError:
        if raw:
            logger.warning(f"{name}={raw!r} 不是数字，回退默认 {default}")
        return default
    if value <= 0:
        logger.warning(f"{name}={value} 必须为正数，回退默认 {default}")
        return default
    return value


CHAT_MODEL_TIMEOUT = _env_float("CHAT_MODEL_TIMEOUT_SECONDS", 600.0)
CHAT_MODEL_CONNECT_TIMEOUT = _env_float("CHAT_MODEL_CONNECT_TIMEOUT_SECONDS", 15.0)

HTTPX_TIMEOUT = httpx.Timeout(CHAT_MODEL_TIMEOUT, connect=CHAT_MODEL_CONNECT_TIMEOUT)


def _model_timeout_message(exc: Exception) -> str:
    """超时文案必须点明「多久没等到」并给出可行动建议（否则用户只能干瞪眼）"""
    seconds = int(CHAT_MODEL_TIMEOUT)
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ConnectError, ConnectionError)):
        return (
            f"连接模型服务超时（{CHAT_MODEL_CONNECT_TIMEOUT:.0f} 秒内没连上）：请检查模型地址、网络或代理设置。"
        )
    if isinstance(exc, httpx.PoolTimeout):
        return "模型请求排队超时：连接池已满，请稍后重试。"
    return (
        f"模型 {seconds} 秒内没有返回内容，回答已中断。"
        f"可尝试：换一个更快的模型（设置页切换默认模型）、缩短提问或减少引用材料长度；"
        f"若确实需要更长时间，可把环境变量 CHAT_MODEL_TIMEOUT_SECONDS 调大（当前 {seconds} 秒）。"
    )

DEFAULT_SESSION_TITLE = "新对话"
SESSION_TITLE_MAX = 24

# 广泛模式检索编排：问题本身 + 视频标题组合，两路并行
BROAD_PER_QUERY_RESULTS = 6
BROAD_MAX_SOURCES = 12
BROAD_QUERY_MAX = 120

# 多选批量删除单次上限（防单个请求删除过量；超限提示分批）
MAX_BATCH_DELETE = 200

# 模型上下文预算：`_get_session_history(limit=30)` 只按**行数**截断，单条正文一字不截，
# 长会话（15 轮 × 上万字符回答）会把 prompt 顶到模型窗口之外硬失败。故在行数之外再加字符预算：
#   - 超预算 → 从**最旧整轮**开始丢（不半截丢轮，避免"有问无答"进上下文），最新一轮永远保留；
#   - 单条超 HISTORY_MESSAGE_MAX → 打省略标记（与转录原文/结果同款 `_truncate` 标记）。
# 环境变量可调；设 <=0 = 关闭字符预算（退回纯行数截断的旧行为）。
HISTORY_MAX_CHARS = int(os.getenv("CHAT_HISTORY_MAX_CHARS", "24000"))
HISTORY_MESSAGE_MAX = int(os.getenv("CHAT_HISTORY_MESSAGE_MAX", "16000"))


class ChatMessage(BaseModel):
    content: str
    model: Optional[str] = None
    enable_search: bool = False
    # 联网模式：strict=严格依据材料 / broad=广泛收集；None 时回退 enable_search（向后兼容）
    search_mode: Optional[Literal["strict", "broad", "off"]] = None
    regenerate: bool = False
    # 重新生成的目标轮：指定提问行 id（任意一轮都能重生）；缺省 = 最后一轮
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


def _get_session_history(job_id, session_id, limit=30, before_id=None):
    """模型上下文用：排除非激活变体（后续提问只引用生效版本），并按「轮」归位排序。

    排序不是纯 id：中间轮重新生成时新变体行 append 在会话末尾，纯 id 排序会让它在
    上下文里排到最后一轮之后（错位）。归位规则：
      - 变体组的「位置锚点」= 组内最早一行的 id（组的所有版本共享同一位置）；
      - 每行归属「锚点之前最近的一条 user 行」所在轮，轮内按 id 升序。
    before_id 用于只要某轮之前的历史（重生成中间轮）。
    """
    conn = get_connection()
    try:
        sql = ("SELECT id, role, content, is_active, variant_group FROM job_chats "
               "WHERE job_id = ? AND session_id = ?")
        params: List = [job_id, session_id]
        if before_id is not None:
            sql += " AND id < ?"
            params.append(before_id)
        rows = conn.execute(sql + " ORDER BY id ASC", tuple(params)).fetchall()
    finally:
        conn.close()

    group_anchor: Dict[str, int] = {}
    for r in rows:
        g = r["variant_group"] or ""
        if g:
            group_anchor[g] = min(group_anchor.get(g, r["id"]), r["id"])
    user_ids = [r["id"] for r in rows if r["role"] == "user"]

    def _owner_of(anchor: int):
        """锚点之前最近的一条 user 行 id（无则 None）"""
        owner = None
        for uid in user_ids:
            if uid <= anchor:
                owner = uid
            else:
                break
        return owner

    positioned = []
    for r in rows:
        anchor = group_anchor.get(r["variant_group"] or "", r["id"])
        owner = _owner_of(anchor)
        positioned.append((owner if owner is not None else anchor, anchor, r["id"], r))
    positioned.sort(key=lambda t: (t[0], t[1], t[2]))
    kept = [r for _, _, _, r in positioned if r["role"] == "user" or r["is_active"] == 1]
    return [{"role": r["role"], "content": r["content"]} for r in kept[-limit:]]


def _trim_history_by_budget(rows, max_chars=None, message_max=None):
    """按字符预算裁剪模型上下文，返回 `(kept, meta)`（纯函数，便于单测）。

    规则：
      - 单条 > message_max → 用 `_truncate` 打省略标记（与转录原文/结果同款），计入 meta；
      - 超预算 → 从**最旧整轮**开始丢，绝不半截丢轮（否则会留"有问无答"进上下文）；
      - 最新一轮无论多长都保留（它就是用户刚问的那一轮，丢了等于失忆）；
      - `max_chars <= 0` = 关闭预算（退回纯行数截断的旧行为）。
    meta: budget / omitted_turns / omitted_chars / truncated_messages / kept_messages
    """
    # 预算默认值在**调用时**从模块常量取（写成参数默认值会在导入期定死，env/测试都改不动）
    max_chars = HISTORY_MAX_CHARS if max_chars is None else max_chars
    message_max = HISTORY_MESSAGE_MAX if message_max is None else message_max
    zero_meta = {
        "budget": max_chars, "omitted_turns": 0, "omitted_chars": 0,
        "truncated_messages": 0, "kept_messages": len(rows),
    }
    if max_chars <= 0:
        return list(rows), zero_meta

    truncated = 0
    normalized = []
    for r in rows:
        content = r.get("content") or ""
        if message_max > 0 and len(content) > message_max:
            content = _truncate(content, message_max)
            truncated += 1
        normalized.append({**r, "content": content})

    # 切轮：每个 user 行开启一轮（前置无提问的孤儿行自成一轮，不会被误并）
    units: List[List[Dict]] = []
    for r in normalized:
        if r.get("role") == "user" or not units:
            units.append([])
        units[-1].append(r)

    kept_units: List[List[Dict]] = []
    used = 0
    for unit in reversed(units):
        size = sum(len(m.get("content") or "") for m in unit)
        if kept_units and used + size > max_chars:
            break
        kept_units.append(unit)
        used += size
    kept_units.reverse()

    dropped = units[: len(units) - len(kept_units)]
    meta = {
        "budget": max_chars,
        "omitted_turns": sum(1 for u in dropped if any(m.get("role") == "user" for m in u)),
        "omitted_chars": sum(len(m.get("content") or "") for u in dropped for m in u),
        "truncated_messages": truncated,
        "kept_messages": sum(len(u) for u in kept_units),
    }
    return [m for u in kept_units for m in u], meta


def _history_for_prompt(job_id, session_id, limit=30, before_id=None):
    """模型上下文用历史（按轮 + 字符预算裁剪）：返回 `(messages, meta)`。

    `_get_session_history` 的契约（签名/返回值/排序）保持不变——现有测试与调用方
    直接消费它；预算只是外面加的一层。
    """
    rows = _get_session_history(job_id, session_id, limit=limit, before_id=before_id)
    return _trim_history_by_budget(rows, max_chars=HISTORY_MAX_CHARS, message_max=HISTORY_MESSAGE_MAX)


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


def _resolve_regenerate_turn(job_id, session_id, user_message_id=None):
    """定位要重新生成的一轮。

    返回 `{question, user_id, prev, group}`；无可重生对象返回 None：
      - user_message_id 指定提问行（任意一轮）；缺省取最后一轮（向后兼容）
      - prev = 该轮现有回答里生效的那条（无激活则取最新一条）
      - group = prev 的变体组（空串表示尚未归组）
    """
    conn = get_connection()
    try:
        if user_message_id:
            urow = conn.execute(
                "SELECT id, content FROM job_chats WHERE job_id = ? AND session_id = ? "
                "AND id = ? AND role = 'user'",
                (job_id, session_id, user_message_id),
            ).fetchone()
        else:
            urow = conn.execute(
                "SELECT id, content FROM job_chats WHERE job_id = ? AND session_id = ? "
                "AND role = 'user' ORDER BY id DESC LIMIT 1",
                (job_id, session_id),
            ).fetchone()
        if not urow:
            return None
        nxt = _next_user_id(conn, job_id, session_id, urow["id"])
        sql = ("SELECT id, variant_group, is_active FROM job_chats "
               "WHERE job_id = ? AND session_id = ? AND role = 'assistant' AND id > ?")
        params: List = [job_id, session_id, urow["id"]]
        if nxt is not None:
            sql += " AND id < ?"
            params.append(nxt)
        answers = conn.execute(sql + " ORDER BY id ASC", tuple(params)).fetchall()
    finally:
        conn.close()
    if not answers:
        return None
    prev = next((a for a in answers if a["is_active"] == 1), answers[-1])
    return {
        "question": urow["content"] or "",
        "user_id": urow["id"],
        "prev": prev,
        "group": prev["variant_group"] or "",
    }


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
            # （_history_for_prompt = 取历史 + 上下文预算裁剪，meta 供前端显示"已精简"）
            history, history_meta = _history_for_prompt(job_id, session_id)
            # 本轮发给模型的问题：新提问 = 用户输入；重新生成中间轮 = 该轮原提问
            ask = question

            if not msg.regenerate:
                user_id = _insert_chat(job_id, session_id, "user", ask)
                _maybe_rename_session(job_id, session_id, ask)
            else:
                # 重新生成 = 追加新变体（目标轮由 user_message_id 指定，缺省最后一轮）：
                # 归入该轮回答的 variant_group（无组则从本代起新建组），组内旧版本全部转为非激活。
                # 中间轮的提问不在 history 末尾 → 取该轮之前的历史，并把该提问作为本轮问题传回。
                target = _resolve_regenerate_turn(job_id, session_id, msg.user_message_id)
                if target is None:
                    yield _sse({"type": "error", "message": "没有可重新生成的回答"})
                    return
                prev = target["prev"]
                variant_group = target["group"] or _gen_variant_group()
                history, history_meta = _history_for_prompt(
                    job_id, session_id, before_id=target["user_id"],
                )
                ask = target["question"]
                conn = get_connection()
                try:
                    # 存量独立回答（无组）先认领进组，否则旧版本仍 is_active=1 继续进上下文
                    if not target["group"]:
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
            # 上下文被精简过就如实告知（不是错误，是"模型看不到的那部分"的说明）
            if history_meta.get("omitted_turns") or history_meta.get("truncated_messages"):
                logger.info(
                    f"[chat] 上下文精简 {job_id}/{session_id}: "
                    f"省略 {history_meta['omitted_turns']} 轮 / {history_meta['omitted_chars']} 字符, "
                    f"单条截断 {history_meta['truncated_messages']} 条"
                )
                yield _sse({"type": "context", **history_meta})
            if search_mode != "off":
                # 检索词 = 本轮提问（重生成中间轮时即该轮提问）；
                # 仅存量空提问行才回退用历史里最近一条用户消息
                search_query = ask
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

            # 提问一律显式传入（重生成的中间轮提问不在 history 末尾，故不再靠 history 携带）
            messages = _build_messages(
                original_ctx, result_ctx, history, ask, search_text, False,
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
        except (httpx.TimeoutException, asyncio.TimeoutError) as e:
            # ★ 超时单独成句：通用分支只回 str(e)，对用户等于「莫名其妙失败」
            logger.warning(f"追问模型超时 job={job_id} session={session_id}: {type(e).__name__}: {e}")
            message = _model_timeout_message(e)
            try:
                assistant_id = _insert_chat(
                    job_id, session_id, "assistant", "(回答超时)",
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
                yield _sse({"type": "error", "message": message})
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
