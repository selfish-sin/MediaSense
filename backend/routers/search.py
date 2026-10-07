"""
YouTube Tool V2 - 网页搜索路由

引擎选择在 services/web_search 链路内部（Ollama → Tavily → Firecrawl → DDGS），
本路由只透传关键词与条数；engine 字段保留在响应里仅为兼容旧客户端（恒 "auto"）。
"""
import logging
from fastapi import APIRouter, Query
from pydantic import BaseModel

from services.web_search import search_web, format_search_context

logger = logging.getLogger(__name__)
router = APIRouter()


class SearchResponse(BaseModel):
    success: bool
    query: str
    engine: str
    results: list
    context: str = ""  # AI-ready 格式化文本


@router.get("/api/search")
async def web_search(
    q: str = Query(..., description="搜索关键词"),
    max_results: int = Query(5, ge=1, le=50, description="最大结果数"),
    format: str = Query("json", description="返回格式: json | context"),
):
    """网页搜索 API"""
    results = await search_web(q, max_results=max_results)
    context = format_search_context(results)

    return SearchResponse(
        success=len(results) > 0,
        query=q,
        engine="auto",
        results=results,
        context=context,
    )
