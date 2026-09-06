"""
YouTube Tool V2 - Socket.IO 事件处理
集中管理所有实时通信事件
"""
import logging
import asyncio
import inspect
from typing import Dict, Any
import socketio

logger = logging.getLogger(__name__)

# 预览确认事件存储（替代旧代码的全局 _preview_events dict）
_preview_events: Dict[str, asyncio.Event] = {}

# 任务取消标记（替代旧代码的全局 _cancel_flags dict）
_cancel_flags: Dict[str, bool] = {}


def request_cancel(job_id: str) -> None:
    """Mark a job cancelled and wake it if it is waiting for preview input."""
    _cancel_flags[job_id] = True
    preview_event = _preview_events.get(job_id)
    if preview_event is not None:
        preview_event.set()


async def _emit(sio, event: str, payload: Dict[str, Any]):
    result = sio.emit(event, payload)
    if inspect.isawaitable(result):
        await result


def register_socket_events(sio: socketio.AsyncServer):
    """注册所有 Socket.IO 事件处理函数"""

    @sio.on('connect')
    async def on_connect(sid, environ):
        logger.info(f"客户端连接: {sid}")

    @sio.on('disconnect')
    async def on_disconnect(sid):
        logger.info(f"客户端断开: {sid}")

    @sio.on('continue_summary')
    async def on_continue_summary(sid, data):
        """用户确认继续总结（转录预览后）"""
        job_id = data.get('job_id')
        edited_text = data.get('edited_text', '')

        if job_id and job_id in _preview_events:
            # 如果用户编辑了转录文本，更新到数据库
            if edited_text:
                from db.job_repo import update_job
                update_job(job_id, edited_preview_text=edited_text)
                logger.info(f"任务 {job_id}: 用户编辑了转录文本({len(edited_text)}字符)")

            # 释放预览等待事件
            _preview_events[job_id].set()
            logger.info(f"任务 {job_id}: 用户确认继续总结")

    @sio.on('cancel_preview')
    async def on_cancel_preview(sid, data):
        """用户取消转录预览——同时设取消标记 + 释放等待事件"""
        job_id = data.get('job_id')
        if job_id:
            request_cancel(job_id)
            logger.info(f"任务 {job_id}: 用户取消预览")

    @sio.on('cancel_job')
    async def on_cancel_job(sid, data):
        """用户取消正在运行的任务"""
        job_id = data.get('job_id')
        if job_id:
            request_cancel(job_id)
            from db.job_repo import update_job
            update_job(job_id, status='cancelled', error='用户取消')
            logger.info(f"任务 {job_id}: 用户取消任务")

    @sio.on('edit_transcript')
    async def on_edit_transcript(sid, data):
        """用户编辑转录文本"""
        job_id = data.get('job_id')
        text = data.get('text', '')
        if job_id:
            from db.job_repo import update_job
            update_job(job_id, edited_preview_text=text)
            logger.info(f"任务 {job_id}: 转录文本已编辑({len(text)}字符)")

    @sio.on('chat_message')
    async def on_chat_message(sid, data):
        """聊天消息（第二期功能）"""
        message = data.get('message', '')
        job_id = data.get('job_id')
        logger.info(f"聊天消息: job={job_id}, msg={message[:50]}...")
        # TODO: 第二期实现 RAG 聊天


async def emit_progress(sio, job_id: str, step: str, pct: int, msg: str):
    """发送进度更新"""
    await _emit(sio, 'progress', {
        'job_id': job_id,
        'step': step,
        'pct': pct,
        'msg': msg,
    })


async def emit_log(sio, job_id: str, msg: str):
    """发送日志消息"""
    await _emit(sio, 'log', {
        'job_id': job_id,
        'msg': msg,
    })


async def emit_ai_token(sio, job_id: str, token: str, done: bool = False):
    """发送AI流式Token（单条，向后兼容）"""
    await _emit(sio, 'ai_token', {
        'job_id': job_id,
        'token': token,
        'done': done,
    })


async def emit_ai_thinking(sio, job_id: str, token: str, done: bool = False):
    """send streaming thinking (reasoning_content)"""
    await _emit(sio, 'ai_thinking', {
        'job_id': job_id,
        'token': token,
        'done': done,
    })


async def emit_preview(sio, job_id: str, text: str, title: str = '',
                       platform: str = '', **kwargs):
    """发送转录预览"""
    await _emit(sio, 'preview', {
        'job_id': job_id,
        'text': text,
        'title': title,
        'platform': platform,
        **kwargs,
    })


def set_preview_event(job_id: str, event: asyncio.Event):
    """注册预览等待事件"""
    _preview_events[job_id] = event


def pop_preview_event(job_id: str) -> None:
    """移除预览等待事件"""
    _preview_events.pop(job_id, None)


def check_cancelled(job_id: str) -> bool:
    """检查任务是否被取消"""
    return _cancel_flags.get(job_id, False)


def clear_cancel_flag(job_id: str):
    """清除取消标记"""
    _cancel_flags.pop(job_id, None)
