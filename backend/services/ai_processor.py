"""
YouTube Tool V2 - AI 处理服务
核心：标准AI总结 + 自动打标签 + Token统计
使用 httpx AsyncClient 替代旧代码的 requests.post
"""
import asyncio
import json
import logging
import re
import time
from typing import Optional, Tuple, Dict, Any, List

import httpx

from config import DEFAULT_MODEL
from db.model_repo import get_model_by_key
from db.database import get_connection
from services import ai_cache
from services.prompts import (
    SUMMARY_PROMPTS, build_prompt_vars,
)
from services.httpx_errors import describe_http_error

logger = logging.getLogger(__name__)

# ★ 流读取期间的**时间型**取消轮询间隔（秒）：
#   取消检查原挂在「每 20 个正文 token / 每 20 片 reasoning」上，端点连上却不吐字
#   （半死连接）时零 token → 检查永不触发，用户点了取消要等满 4 次重试（分钟级）
#   才响应。流循环里改成按时间兜底：距上次检查超过这个间隔就查一次。
CANCEL_POLL_INTERVAL = 1.0

# 单轮（一次 HTTP 流）总超时默认值，由调用方按场景下调/上调（不再硬编码在 wait_for 里）
DEFAULT_ROUND_TIMEOUT = 600.0


def _usage_of(usage_state: Optional[dict]) -> Tuple[int, int]:
    """从调用方传入的 usage_state 读回本次调用已累计的 (in_tok, out_tok)。"""
    if not usage_state:
        return 0, 0
    return int(usage_state.get('in_tok') or 0), int(usage_state.get('out_tok') or 0)


class TaskCancelledError(Exception):
    """任务被用户取消时抛出的异常"""
    pass


def _check_cancelled(job_id: str):
    """检查任务是否已被取消，是则抛出 TaskCancelledError"""
    if job_id:
        from socket_handlers import check_cancelled
        if check_cancelled(job_id):
            raise TaskCancelledError(f"任务 {job_id} 已被用户取消")


def _build_headers(model_cfg: Dict[str, Any]) -> Dict[str, str]:
    """构建API请求头"""
    headers = {'Content-Type': 'application/json'}
    auth_type = model_cfg.get('auth_type', 'bearer')
    api_key = model_cfg.get('api_key', '')
    if auth_type == 'bearer' and api_key:
        headers['Authorization'] = f"Bearer {api_key}"
    elif auth_type == 'direct' and api_key:
        headers['Authorization'] = api_key
    return headers


def _client_verify(model_cfg: Dict[str, Any]) -> bool:
    """模型配置勾选 skip_ssl_verify（自签名证书/IP 直连中转）时关闭 httpx 证书校验"""
    return not bool(model_cfg.get('skip_ssl_verify'))


# ============ 核心 AI 调用 ============

async def _emit_cached_content(
    cached: Dict[str, Any],
    sio=None,
    job_id: str = '',
    round_label: str = '',
    final: bool = True,
    state: Optional[dict] = None,
) -> Tuple[str, int, int]:
    """缓存命中：把上次结果按流式的姿势推给前端（分片 + 让出事件循环），
    行为与真实流一致（前端 token 累积 + 末尾校准），但不产生任何 token 消耗。"""
    content = cached.get('content', '')
    if state is not None:
        state['emitted'] = bool(content)
        state['content'] = content
    if sio and content:
        from socket_handlers import emit_ai_token, emit_log
        await emit_log(sio, job_id,
                       f"⚡ AI 缓存命中（{cached.get('model') or '未知模型'}，"
                       f"{cached.get('created_at', '')} 生成）——相同内容直接复用上次结果，不重复调用")
        step = 240
        for i in range(0, len(content), step):
            _check_cancelled(job_id)
            await emit_ai_token(sio, job_id, content[i:i + step], done=False)
            await asyncio.sleep(0)  # 让出事件循环，socket 有机会刷出
        if final:
            await emit_ai_token(sio, job_id, content, done=True)
    if round_label:
        logger.info(f'[{job_id}] {round_label} 完成（AI 缓存命中，token 消耗 0）')
    return content, 0, 0


async def _call_ai(
    model_cfg: Dict[str, Any],
    prompt: str,
    timeout: float = 120.0,
    sio=None,
    job_id: str = '',
    round_label: str = '',
    final: bool = True,
    max_retries: int = 3,
    state: Optional[dict] = None,
    bypass_cache: bool = False,
    usage_state: Optional[dict] = None,
    round_timeout: float = DEFAULT_ROUND_TIMEOUT,
) -> Tuple[str, int, int]:
    """单次AI调用（支持流式输出+批量推送+断连重试），返回(content, input_tokens, output_tokens)

    max_retries: 流式断连时最大重试次数（指数退避: 2s → 4s → 8s）
    round_timeout: 单轮流的总超时（默认 600s）；多次重试各自适用本值。

    ★ state（可选出参，2026-10-05）：调用方传入可变 dict，本函数回填
      - state['emitted']: 本次调用是否已经向前端推送过正文 token
      - state['content']: 成功时的完整正文
      兜底编排（_call_ai_with_fallback）据此执行"只在一字未吐时才换模型"——
      已经吐字再换模型会让前端出现「A 的半截 + B 的完整」的混血文档。

    ★ usage_state（可选出参，2026-10-13）：调用方传入可变 dict，本函数在**流读取
      过程中**就把已累计的 usage 回填（usage_state['in_tok']/['out_tok']）。
      失败分支（超时/断连）会直接返回这批真实数值而不是 0：此前协程被丢弃时
      局部变量一起消失，token_usage 表系统性少记，真实账单高于系统显示。
      换候选重跑时把本 dict 传给下一次调用即可跨候选累计。

    ★ AI 结果缓存（2026-10-06）：默认开启；键 = endpoint+model_id+temperature+max_tokens+prompt。
      命中直接复用上次成功结果（token 消耗 0）；bypass_cache=True 跳过（显式重跑语义）。
      只有成功且非空的结果入池——失败/零输出下次仍是真重试。
    """
    # 重试安全必须由本次调用维护，不能依赖调用方是否需要 state 出参。
    if state is None:
        state = {}
    state['emitted'] = False
    state['content'] = ''
    headers = _build_headers(model_cfg)
    payload = {
        "model": model_cfg.get('model_id', model_cfg.get('key', DEFAULT_MODEL)),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": model_cfg.get('temperature', 0.3),
        "stream": True,  # 启用流式输出
    }
    max_tokens = model_cfg.get('max_tokens')
    if max_tokens:
        payload["max_tokens"] = max_tokens

    # ★ AI 结果缓存：命中即复用（2026-10-06）
    cache_key = ''
    if prompt:
        cache_key = ai_cache.make_key(
            'llm', model_cfg.get('endpoint', ''),
            model_cfg.get('model_id', model_cfg.get('key', '')),
            payload.get('temperature', 0.3), max_tokens or '', prompt,
        )
        cached = None if bypass_cache else ai_cache.cache_get(cache_key)
        if cached:
            return await _emit_cached_content(
                cached, sio=sio, job_id=job_id, round_label=round_label,
                final=final, state=state,
            )

    try:
        async def _stream():
            """在带总超时的协程内完成流式读取，避免 API 半死不活时永久挂起"""
            # ★ 断连重试会把整段重跑一遍：前几次尝试已经烧掉的 token 必须计入本次
            #   返回，否则「重试成功」只记最后一轮的量，账单仍然对不上。
            #   每次进入 _stream 都重新读基线（上次尝试已把用量写进 usage_state）。
            prior_in, prior_out = _usage_of(usage_state)
            content_parts = []
            in_tok = 0
            out_tok = 0
            token_count = 0
            reasoning_count = 0
            emitted_chars = 0
            last_cancel_check = time.monotonic()

            def _sync_usage():
                """把本次尝试已累计的 token 立刻回填到调用方的 usage_state。
                失败分支的协程会被丢弃，局部变量随之消失——不回填等于漏记账。
                没收到 usage 时按已吐字符数粗估（与成功路径 len//2 同一口径）。"""
                if usage_state is None:
                    return
                usage_state['in_tok'] = prior_in + in_tok
                usage_state['out_tok'] = prior_out + (out_tok or emitted_chars // 2)

            # ★ 批量推送缓冲区：每 BATCH_TOKENS 个 token 或每 BATCH_INTERVAL 秒发送一次
            _BATCH_TOKENS = 10
            _BATCH_INTERVAL = 0.05  # 50ms
            token_buffer = []
            thinking_buffer = []
            last_flush_time = time.monotonic()
            batch_flush_count = 0

            async with httpx.AsyncClient(timeout=timeout, verify=_client_verify(model_cfg)) as client:
                async with client.stream("POST", model_cfg['endpoint'], headers=headers, json=payload) as resp:
                    if resp.status_code >= 400:
                        # ★ 流式响应必须先读完整 body，错误处理（describe_http_error）才能读取
                        #   response.text 透出服务端真实原因；否则访问未读的流会抛
                        #   ResponseNotRead（"Attempted to access streaming response content..."），
                        #   把 4xx/5xx 真实原因（如无权访问分组）掩盖成莫名其妙的流错误
                        await resp.aread()
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        # ★ 时间型取消轮询：端点连上却不吐字（半死连接）时零 token，
                        #   挂在 token 计数上的检查永远不触发，用户取消要等满重试才生效。
                        _now = time.monotonic()
                        if _now - last_cancel_check >= CANCEL_POLL_INTERVAL:
                            last_cancel_check = _now
                            _check_cancelled(job_id)
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            # ★ 兼容部分接口（如阿里云 MaaS）发送 choices 为空数组的 usage/keep-alive chunk：
                            #   dict.get(key, default) 只在 key 缺失时返回默认值，choices=[] 时会越界
                            choices = chunk.get('choices') or []
                            delta = choices[0].get('delta', {}) if choices else {}
                            text_piece = delta.get('content', '')
                            reasoning_piece = delta.get('reasoning_content', '')
                            if reasoning_piece:
                                reasoning_count += 1
                                # ★ 思考型模型可能连续几分钟只吐 reasoning——取消检查不能
                                #   只挂在正文 token 上（此前思考期取消要等流自然结束才生效，
                                #   实测 d5615485：取消后打标签仍继续烧了一次 API）
                                if reasoning_count % 20 == 0:
                                    _check_cancelled(job_id)
                                if sio:
                                    thinking_buffer.append(reasoning_piece)
                                    if len(thinking_buffer) >= _BATCH_TOKENS:
                                        from socket_handlers import emit_ai_thinking
                                        await emit_ai_thinking(sio, job_id, ''.join(thinking_buffer), done=False)
                                        thinking_buffer = []
                            if text_piece:
                                content_parts.append(text_piece)
                                emitted_chars += len(text_piece)
                                token_count += 1
                                if state is not None:
                                    state['emitted'] = True
                                # ★ 每 20 个 token 检查一次取消标记（避免高频检查影响性能）
                                if token_count % 20 == 0:
                                    _check_cancelled(job_id)
                                # 每片都回填：断连/超时可能发生在任何一片之后
                                _sync_usage()

                                # ★ 批量推送：累积 token，满足条件时批量发送
                                # 注意：前端只认 data.token（字符串），故每次 flush 拼接成字符串发出
                                if sio:
                                    token_buffer.append(text_piece)
                                    now = time.monotonic()
                                    should_flush = (
                                        len(token_buffer) >= _BATCH_TOKENS
                                        or (now - last_flush_time) >= _BATCH_INTERVAL
                                    )
                                    if should_flush:
                                        from socket_handlers import emit_ai_token
                                        await emit_ai_token(sio, job_id, ''.join(token_buffer), done=False)
                                        batch_flush_count += 1
                                        token_buffer = []
                                        last_flush_time = now
                            # 从最后一个 chunk 提取 usage
                            usage = chunk.get('usage')
                            if usage:
                                in_tok = usage.get('prompt_tokens', 0)
                                out_tok = usage.get('completion_tokens', 0)
                                _sync_usage()
                        except json.JSONDecodeError:
                            continue

            # ★ 冲刷剩余缓冲区（可能还有不足批量的 token）
            if sio and token_buffer:
                from socket_handlers import emit_ai_token
                await emit_ai_token(sio, job_id, ''.join(token_buffer), done=False)
                batch_flush_count += 1
            if sio and thinking_buffer:
                from socket_handlers import emit_ai_thinking
                await emit_ai_thinking(sio, job_id, ''.join(thinking_buffer), done=True)
                thinking_buffer = []
            return content_parts, in_tok, out_tok, batch_flush_count

        # ★ 流式调用 + 断连重试（指数退避: 2s → 4s → 8s）
        for retry in range(max_retries + 1):
            # ★ 每次重试前查一次取消：零输出的断连重试在整轮里可能耗时数分钟，
            #   退避 sleep 期间用户点取消也不该白等。
            _check_cancelled(job_id)
            try:
                content_parts, in_tok, out_tok, batch_flush_count = await asyncio.wait_for(
                    _stream(), timeout=round_timeout)
                break  # 成功
            except asyncio.TimeoutError:
                logger.error(f'AI调用超时（单轮总超时 {round_timeout}s）')
                if sio:
                    from socket_handlers import emit_log
                    await emit_log(sio, job_id, 'AI处理超时（已自动终止，请重试）')
                # ★ 失败也返回已烧掉的 token（2026-10-13）：此前恒 0，token_usage 系统性少记
                return '[处理失败: 超时]', *_usage_of(usage_state)
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout, ConnectionError) as e:
                # ★ 已吐字就不再重试（2026-10-05）：重试会从头再流一遍，
                #   而第一遍的半截已经推给前端了 → 前端变成「断连前的半截 + 重试的完整」，
                #   一份文档里混了两次生成的文本。宁可失败让用户重试，也不产出混血文档。
                #   零输出时（连接握手就断了）重试/换模型才是安全的。
                if state is not None and state.get('emitted'):
                    logger.error(f'AI流式在已输出正文后断开，放弃重试: {e}')
                    if sio:
                        from socket_handlers import emit_log
                        await emit_log(sio, job_id, f'❌ AI连接中断（已输出部分内容，不再重试以免内容重复）: {e}')
                    # 已吐出的正文是真的花了钱，照实记账（缺 usage 时按字符数粗估）
                    return '[处理失败: 连接中断（已输出部分内容）]', *_usage_of(usage_state)
                if retry < max_retries:
                    wait = 2 ** retry  # 1, 2, 4 (retry 0: 1s, retry 1: 2s, retry 2: 4s)
                    logger.warning(f'AI流式断连，{wait}s后重试 ({retry+1}/{max_retries}): {e}')
                    if sio:
                        from socket_handlers import emit_log
                        await emit_log(sio, job_id, f'⚠ AI连接断开，{wait}秒后重试 ({retry+1}/{max_retries})')
                    await asyncio.sleep(wait)
                    continue
                else:
                    logger.error(f'AI调用失败（已重试{max_retries}次）: {e}')
                    if sio:
                        from socket_handlers import emit_log
                        await emit_log(sio, job_id, f'❌ AI连接失败（已重试{max_retries}次）: {e}')
                    return '[处理失败: 连接断开]', *_usage_of(usage_state)
            except TaskCancelledError:
                # ★ 用户取消必须向上抛，不能被下面的通用 except 吞成
                #   '[处理失败: ...]' 哨兵串（否则 main.py 会把 cancelled 记成 failed）
                raise
            except httpx.HTTPStatusError as e:
                # ★ 服务端返回的 4xx/5xx：透出响应体里的真实原因（如 model_not_found、
                #   密钥无效、余额不足），而不是裸的 "Client error '404 Not Found' for url ..."
                detail = describe_http_error(e)
                logger.error(f'AI调用失败（HTTP {e.response.status_code}）: {detail}')
                if sio:
                    from socket_handlers import emit_log
                    await emit_log(sio, job_id, f'AI处理失败: {detail}')
                return f'[处理失败: {detail}]', *_usage_of(usage_state)
            except Exception as e:
                # 非网络错误（如 JSON 解析失败等），不重试
                logger.error(f'AI调用失败: {e}')
                if sio:
                    from socket_handlers import emit_log
                    await emit_log(sio, job_id, f'AI处理失败: {e}')
                return f'[处理失败: {e}]', *_usage_of(usage_state)

        content = ''.join(content_parts)
        if state is not None:
            state['content'] = content

        # ★ 成功结果入缓存池（2026-10-06）：空/失败不入池，下次重试仍是真重试
        if cache_key and content.strip():
            ai_cache.cache_put(
                cache_key, 'llm',
                model_cfg.get('model_id', model_cfg.get('key', '')),
                content, meta={'model_key': model_cfg.get('key', '')},
            )

        # 如果 usage 没拿到，估算
        if not out_tok:
            out_tok = len(content) // 2  # 粗略估算
        # 断连重试前几次尝试烧掉的 token 一并计入（usage_state 是跨尝试累计口径）
        if usage_state is not None:
            in_tok, out_tok = _usage_of(usage_state)

        # 推送完成信号（附带完整内容，前端用作最终校准）
        if sio:
            from socket_handlers import emit_ai_token, emit_log
            if final:
                await emit_ai_token(sio, job_id, content, done=True)
            if round_label:
                await emit_log(sio, job_id, f'{round_label} 完成 | token: in={in_tok} out={out_tok} (批量推送{batch_flush_count}次)')

        return content, in_tok, out_tok
    except TaskCancelledError:
        # ★ 用户取消：透传给上层（_process_job_impl 会把任务标记为 cancelled）
        raise
    except httpx.HTTPStatusError as e:
        detail = describe_http_error(e)
        logger.error(f'AI调用失败（HTTP {e.response.status_code}）: {detail}')
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, f'AI处理失败: {detail}')
        return f'[处理失败: {detail}]', *_usage_of(usage_state)
    except Exception as e:
        logger.error(f'AI调用失败: {e}')
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, f'AI处理失败: {e}')
        return f'[处理失败: {e}]', *_usage_of(usage_state)


# ============ 兜底编排（2026-10-05）============
# 失败哨兵：_call_ai 全失败路径都返回 '[处理失败: ...]' 字符串而非抛异常（既有契约，
# main.py 靠它把"AI 失败"路由成任务失败而不是把错误当总结写盘）。这里集中判定，避免各处 startswith 漏改。
_FAIL_SENTINEL = '[处理失败'


def is_call_failed(content: str) -> bool:
    """AI 调用是否以失败哨兵结束。"""
    return isinstance(content, str) and content.lstrip().startswith(_FAIL_SENTINEL)


def _strip_sentinel(content: str) -> str:
    """失败哨兵里的原因（供调用链展示），非失败则原样返回。"""
    if not is_call_failed(content):
        return content
    reason = content.lstrip()[len(_FAIL_SENTINEL):].strip().lstrip(':： ').strip()
    return reason.rstrip(']').strip() or '未知原因'


async def _call_ai_with_fallback(
    model: str,
    prompt: str,
    timeout: float = 120.0,
    sio=None,
    job_id: str = '',
    round_label: str = '',
    final: bool = True,
    max_retries: int = 3,
    call_chain: Optional[list] = None,
    bypass_cache: bool = False,
) -> Tuple[str, int, int, str]:
    """流式 AI 调用 + 兜底环编排。返回 (content, in_tok, out_tok, 实际使用的模型 key)。

    换模型规则（**只在一字未吐时才换**）：
    - 候选顺序 = [本次所选模型] + 兜底环其余模型（model_repo.resolve_ai_candidates 单点）
    - 触发条件：本次调用返回失败哨兵
    - **已向前端吐过字 → 绝不换模型**：换过去会变成「A 的半截 + B 的完整」混血文档，
      对术语准确性要求高的场景是净损害。此时直接返回失败，让用户显式重试。
    - 零输出时换模型是安全的：最常见的失败（503 账号池耗尽 / 429 / 鉴权 / 端点错）
      都是请求一进去就被拒、根本没开始流。
    - 每个候选只试一次，不回头（见 model_repo.resolve_ai_candidates 的语义说明）。
    - 用户取消直接上抛，绝不换模型。

    call_chain（可选 list）：每次尝试都追加 {model, ok, reason}，供任务结果展示实际调用链。
    """
    from db.model_repo import resolve_ai_candidates

    candidates = resolve_ai_candidates(model)
    if len(candidates) > 1:
        logger.info(f'[{job_id}] 兜底环生效，候选顺序: {candidates}')

    content, in_tok, out_tok = '', 0, 0
    used = candidates[0]
    # 失败原因**独立于 call_chain 累积**：call_chain 是可选出参，不传时
    # 真实原因不能跟着丢（否则上层只拿到"全部失败"而看不到"为什么"）。
    failures: list = []
    # ★ 跨候选累计的 token（2026-10-13）：换模型重跑是真的又烧了一次，
    #   此前只回传最后一个候选的用量，token_usage 表系统性少记。
    usage_state: dict = {}

    for idx, cand in enumerate(candidates):
        if idx > 0:
            # 换模型决策同时落盘（logger）与推送（socket）：纯 socket 日志服务端排障时不可见
            logger.info(f'[{job_id}] 换模型重试（候选 {idx + 1}/{len(candidates)}）：{cand}')
            if sio:
                from socket_handlers import emit_log
                await emit_log(sio, job_id, f'🔄 自动换模型重试（候选 {idx + 1}/{len(candidates)}）：{cand}')

        model_cfg = get_model_by_key(cand)
        if not model_cfg:
            reason = f'模型 {cand} 不存在'
            logger.warning(f'[{job_id}] {reason}')
            failures.append((cand, reason))
            if call_chain is not None:
                call_chain.append({'model': cand, 'ok': False, 'reason': reason})
            continue

        state: dict = {}
        try:
            content, in_tok, out_tok = await _call_ai(
                model_cfg, prompt, timeout=timeout, sio=sio, job_id=job_id,
                round_label=round_label, final=final, max_retries=max_retries,
                state=state, bypass_cache=bypass_cache, usage_state=usage_state,
            )
        except TaskCancelledError:
            # 用户取消：绝不换模型重试
            failures.append((cand, '用户取消'))
            if call_chain is not None:
                call_chain.append({'model': cand, 'ok': False, 'reason': '用户取消'})
            raise

        # ★ 零正文也视为失败（2026-10-06）：思考型模型（如 minimax）可能只吐
        #   reasoning_content 就正常结束流，content=''——is_call_failed('') 是 False，
        #   此前空串被当成"成功"一路走到落库，任务 completed 但总结为空（实测 41ea670a）。
        #   空内容必然未吐过正文（emitted 只在正文 piece 时置位），换模型是安全的。
        if not content.strip() and not is_call_failed(content):
            content = '[处理失败: 模型返回空内容（可能只输出了思考过程）]'

        if not is_call_failed(content):
            used = cand
            if call_chain is not None:
                call_chain.append({'model': cand, 'ok': True, 'reason': ''})
            if idx > 0 and sio:
                from socket_handlers import emit_log
                await emit_log(sio, job_id, f'✅ 已自动切换到模型 {cand} 完成')
            return content, in_tok, out_tok, used

        reason = _strip_sentinel(content)
        failures.append((cand, reason))
        if call_chain is not None:
            call_chain.append({'model': cand, 'ok': False, 'reason': reason})

        if state.get('emitted'):
            # 已吐字：换模型会造成内容混血，放弃兜底
            logger.warning(f'[{job_id}] 模型 {cand} 在已输出内容后失败，不再换模型: {reason}')
            return content, in_tok, out_tok, cand
        if idx == len(candidates) - 1:
            break
        content, in_tok, out_tok = '', 0, 0

    tried = '、'.join(c for c in candidates)
    # 汇总所有候选的失败原因：调用方（打标签/结果页）要看到真实原因而非只有状态码
    joined = '；'.join(f'{k}: {why}' for k, why in failures) or '未知原因'
    if sio:
        from socket_handlers import emit_log
        await emit_log(sio, job_id, f'❌ 兜底环内所有候选模型均失败（已尝试: {tried}）')
        await emit_log(sio, job_id, f'   失败原因：{joined}')
    return f'[处理失败: 兜底环内全部模型失败（{tried}）——{joined}]', in_tok, out_tok, used


# ============ 标准 AI 处理 ============
async def ai_process(
    text: str,
    summary_type: str = 'summary',
    custom_prompt: str = '',
    model: str = DEFAULT_MODEL,
    sio=None,
    job_id: str = '',
    summary_options: Optional[Dict[str, Any]] = None,
    dimensions: Optional[Dict[str, str]] = None,
    ctx: Optional[dict] = None,
    bypass_cache: bool = False,
) -> Tuple[str, int, int]:
    """
    标准单轮/分批AI处理
    返回：(result_text, input_tokens, output_tokens)

    支持两种提示词模式：
    1. 模块化（推荐）：传入 dimensions 字典（7维），由 prompt_builder 拼装
    2. 回退：使用 summary_type 查找 SUMMARY_PROMPTS（保留兼容）

    ★ ctx（可选出参，2026-10-05）：传入可变 dict，回填 ctx['call_chain']
      = 实际 AI 调用链（每次尝试的模型/成败/原因），供任务结果展示。

    ★ bypass_cache（2026-10-06）：True 跳过 AI 结果缓存（显式重跑语义，如"重新总结"）；
      常规任务管线保持 False——同内容重复提交直接复用上次结果。
    """
    model_cfg = get_model_by_key(model) or get_model_by_key(DEFAULT_MODEL)
    if not model_cfg:
        raise Exception(f'模型 {model} 不存在')

    opts = summary_options or {}

    # ★ 决定 prompt 构建方式：模块化优先
    use_modular = bool(dimensions)

    if use_modular:
        from services.prompt_builder import build_modular_prompt, dimensions_to_legacy_summary_type
        # 兼容 custom_prompt：作为额外指引传入
        user_custom = custom_prompt or opts.get("custom_prompt", "")
        # 兼容 edited_prompt：用户可能直接编辑了完整 prompt
        edited_prompt = opts.get("edited_prompt", "")
        # ★ 完全自定义提示词库（来自 TemplatesView 的 full_override 模板）
        full_override = opts.get("full_override", "")
        if edited_prompt:
            prompt_template = edited_prompt
            # 仍需注入 CONTENT
            prompt = prompt_template.replace('{{CONTENT}}', text)
        else:
            prompt = build_modular_prompt(
                dimensions=dimensions,
                content=text,
                custom_prompt=user_custom,
                full_override=full_override,
            )
        if sio:
            from socket_handlers import emit_progress, emit_log
            await emit_progress(sio, job_id, 'ai', 0, f'正在调用 AI ({model})...')
            legacy_label = dimensions_to_legacy_summary_type(dimensions)
            await emit_log(sio, job_id, f'模块化提示词 | 等效类型: {legacy_label}')
    else:
        if sio:
            from socket_handlers import emit_progress, emit_log
            await emit_progress(sio, job_id, 'ai', 0, f'正在调用 AI ({model})...')
            await emit_log(sio, job_id, f'总结类型: {summary_type}')

        # 旧路径：从 SUMMARY_PROMPTS 查找（保留兼容）
        type_prompt = SUMMARY_PROMPTS.get(summary_type, SUMMARY_PROMPTS['summary'])
        edited_prompt = opts.get("edited_prompt", "")
        if summary_type == 'custom' and custom_prompt:
            prompt_template = type_prompt.replace('{{CUSTOM_PROMPT}}', custom_prompt)
        elif edited_prompt:
            prompt_template = edited_prompt
        else:
            prompt_template = type_prompt

        # 注入模板变量
        vars_map = build_prompt_vars(opts)
        for var, val in vars_map.items():
            prompt_template = prompt_template.replace(var, val)

        # 替换内容占位符
        prompt = prompt_template.replace('{{CONTENT}}', text)

    # ★ full_override 模式：提示词设计为整文处理（如"书记员全文记录"），分块会破坏连贯性
    _has_full_override = bool(opts and isinstance(opts, dict) and str(opts.get("full_override", "")).strip())
    if _has_full_override:
        chunks = [text]
        if sio and len(text) > 80000:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, f'full_override 模式：整文送入（{len(text)} 字符），不分块')
    else:
        # 分批处理（自适应 chunk 大小：根据目标字数调整，避免小目标浪费 token 或大目标截断）
        word_count_raw = None
        if dimensions and isinstance(dimensions, dict):
            word_count_raw = dimensions.get("word_count")
        if not word_count_raw and opts and isinstance(opts, dict):
            word_count_raw = opts.get("word_count")
        # 不限 / 无效 → 兜底 150000；其他按映射表
        _WC_MAP = {"500": 20000, "1000": 30000, "3000": 40000, "5000": 60000, "10000": 80000, "20000": 120000}
        try:
            max_chars = _WC_MAP.get(str(word_count_raw).strip(), 80000)
        except Exception:
            max_chars = 80000
        if len(text) > max_chars:
            chunks = [text[i:i+max_chars] for i in range(0, len(text), max_chars)]
            if sio:
                from socket_handlers import emit_log
                await emit_log(sio, job_id, f'文本较长，分 {len(chunks)} 批处理')
        else:
            chunks = [text]

    results = []
    total_in = 0
    total_out = 0
    # ★ 跨批次记忆：某批次在环上换到了 cand 并成功，后续批次直接从 cand 起步
    #   （否则每批都从环头开始重试一遍已失效的模型，兜底环反而拖慢任务）
    preferred_model = model
    call_chain: list = []

    for i, chunk in enumerate(chunks):
        # ★ 每批开始前检查取消标记
        _check_cancelled(job_id)

        pct = int(10 + (i + 1) / len(chunks) * 80)
        if sio:
            from socket_handlers import emit_progress
            await emit_progress(sio, job_id, 'ai', pct, f'AI处理中... ({i+1}/{len(chunks)})')

        # 分批时每批只传入部分内容
        if len(chunks) > 1:
            chunk_body = f'[第 {i+1}/{len(chunks)} 部分]\n\n{chunk}'
            if edited_prompt:
                # ★ 用户编辑过的完整提示词（2026-10-13 修复 A3）：分批复用分支此前
                #   只认 dimensions/custom_prompt/full_override，把 edited_prompt 丢了——
                #   长视频上用户改过的文字完全不生效，短视频却生效（行为不一致）。
                #   这里复用单批路径的注入点（与下方 prompt_template 分支一致）：
                #   模板正文一个字不动，只把 {{CONTENT}} 换成本批内容。
                #   编辑版提示词可能不带占位符（build_modular_prompt 也是「无占位则
                #   追加内容」的注入约定），此时按同一约定把本批内容附在末尾，
                #   避免整批内容凭空消失。
                if '{{CONTENT}}' in edited_prompt:
                    chunk_prompt = edited_prompt.replace('{{CONTENT}}', chunk_body)
                else:
                    chunk_prompt = f'{edited_prompt}\n\n【待处理内容】\n{chunk_body}'
            elif use_modular:
                from services.prompt_builder import build_modular_prompt
                chunk_prompt = build_modular_prompt(
                    dimensions=dimensions,
                    content=chunk_body,
                    custom_prompt=custom_prompt or opts.get("custom_prompt", ""),
                    full_override=opts.get("full_override", ""),
                )
            else:
                chunk_prompt = prompt_template.replace('{{CONTENT}}', chunk_body)
        else:
            chunk_prompt = prompt

        content, in_tok, out_tok, used_model = await _call_ai_with_fallback(
            preferred_model, chunk_prompt, timeout=120.0,
            sio=sio, job_id=job_id, round_label=f'批次 {i+1}/{len(chunks)}', final=False,
            call_chain=call_chain, bypass_cache=bypass_cache,
        )
        total_in += in_tok
        total_out += out_tok

        # ★ 契约 C：分批模式 fail-fast（2026-10-13 修复 A1）
        #   任一批返回失败哨兵就整份失败：合并时哨兵串被 '# 分批处理结果' 标题前缀
        #   顶到正文里，两处调用方的 result_text.lstrip().startswith('[处理失败')
        #   防线在分批模式下永远不成立 → 任务标 completed、残缺文档写进
        #   jobs.result_text 与 summary_history。宁可显式失败让用户重试。
        #   单批模式保持原样（调用方有哨兵防线）。
        if len(chunks) > 1 and is_call_failed(content):
            detail = _strip_sentinel(content)
            msg = (f'第 {i + 1}/{len(chunks)} 批 AI 处理失败：{detail}；'
                   f'分批总结已中止，不写入残缺结果（本次已消耗 token 已记账）。'
                   f'可重试该任务，或在「模型管理」换用可用模型/检查 Key 与额度后重新总结。')
            logger.error(f'[{job_id}] {msg}')
            if sio:
                from socket_handlers import emit_log
                await emit_log(sio, job_id, f'❌ {msg}')
            # 调用链照实回填（排障要看"这次到底调了谁、哪一批挂了"）
            if isinstance(ctx, dict):
                ctx['call_chain'] = call_chain
            _record_token_usage(model, total_in, total_out, job_id)
            # ★ 抛在 emit_ai_token(done=True) 之前：不能把失败状态推成"AI 完成"，
            #   更不能把半截合并结果播给前端。
            raise Exception(msg)

        # 本批成功后记住它（失败的批不记忆，否则会把"已失效"传染给后续批次）
        if not is_call_failed(content):
            preferred_model = used_model
        results.append(content)

    # 合并结果
    if len(chunks) > 1:
        full_result = f'# 分批处理结果\n\n已分 {len(chunks)} 批处理：\n\n---\n\n' + '\n\n---\n\n'.join(results)
    else:
        full_result = '\n\n'.join(results)

    if sio:
        from socket_handlers import emit_ai_token, emit_progress, emit_log
        # 最终校准信号（完整内容），前端据此停止 append
        await emit_ai_token(sio, job_id, full_result, done=True)
        await emit_progress(sio, job_id, 'ai', 100, 'AI处理完成！')
        await emit_log(sio, job_id, f'AI结果: {len(full_result)} 字符')
        await emit_log(sio, job_id, f'累计token: in={total_in} out={total_out}')

    # 更新Token统计到数据库
    _record_token_usage(model, total_in, total_out, job_id)

    # ★ 回传实际调用链：ctx['call_chain'] = [{model, ok, reason}, ...]
    #   main.py 存进任务结果，前端展示"这次实际调了谁、哪个挂了"——
    #   兜底链最怕的就是配了却看不见有没有用。
    if isinstance(ctx, dict):
        ctx['call_chain'] = call_chain

    return full_result, total_in, total_out


# ============ 转录文本 AI 校正 ============

# 时间戳标记正则：[MM:SS - MM:SS] / [MM:SS] / [HH:MM:SS ...] 等
_TS_RE = re.compile(r'\[\d{1,2}:\d{2}(?::\d{2})?\s*(?:-\s*\d{1,2}:\d{2}(?::\d{2})?)?\]')
# 说话人标记：[说话人1] / [Speaker A] 等
_SPEAKER_RE = re.compile(r'\[(?:说话人|Speaker| speaker)\s*\w+\]', re.IGNORECASE)


def _count_markers(text: str) -> int:
    """统计时间戳 + 说话人标记数量（用于校正前后完整性比对）"""
    return len(_TS_RE.findall(text)) + len(_SPEAKER_RE.findall(text))


def _split_by_boundary(text: str, max_chars: int) -> list:
    """按行边界分块，避免在时间戳/说话人标记中间硬切。

    优先在换行处切；若单行超长则在该行的标记边界切；最后兜底硬切。
    """
    if len(text) <= max_chars:
        return [text]
    chunks = []
    buf = ''
    for line in text.split('\n'):
        # 加上这行不超限 → 累积
        if len(buf) + len(line) + 1 <= max_chars:
            buf = buf + '\n' + line if buf else line
            continue
        # 当前行本身超限：先 flush buf，再把长行按标记边界切
        if buf:
            chunks.append(buf)
            buf = ''
        if len(line) <= max_chars:
            buf = line
            continue
        # 长行：在 ] 后切（标记结尾），尽量不劈开标记
        parts = re.split(r'(?<=\])', line)
        for p in parts:
            if len(buf) + len(p) <= max_chars:
                buf += p
            else:
                if buf:
                    chunks.append(buf)
                # 单个 part 仍超长 → 兜底硬切
                buf = p[:max_chars] if len(p) > max_chars else p
                if len(p) > max_chars:
                    chunks.append(buf)
                    buf = p[max_chars:]
    if buf:
        chunks.append(buf)
    return chunks


# ============ 校正提示词模板（按内容领域切换） ============
# 'default'：通用内容；'tcm'：中医/针灸类。
# 两套模板共同的核心原则："只改转写错，不改说得对"——对抗实测中校正模型会
# 用自己的领域知识整句替换原文（机械防线拦不住语义级替换），必须在提示词层约束。
_CORRECTION_PROMPTS = {
    'default': """【校正要求】
1. 只修正"明显是语音转写错误"的地方：同音错字、形近字误识别（如"终南山"→"钟南山"）、缺失的标点、明显的断句错误
2. 专有名词（人名、地名、术语、品牌、技术名词）仅在明显误听且有把握时校正；拿不准的一律保留原词
3. ★ 只改"转写错"，不改"说得对"：发言人的观点、用词和内容即使与你的认知不同，也一字不动；禁止用你自己的知识改写、补充或"圆"内容
4. 不整句重写，不合并或拆分行：输出必须与输入逐行对应，只做最小必要的修改
5. 补全缺失的标点（句号、逗号、问号等），保持中文标点规范
6. 保留所有时间戳标记（如 [MM:SS - MM:SS] 格式），不修改时间戳本身
7. 保留 [说话人X] 等说话人标记
8. 不要增删实质内容，不要添加解释、注释、总结
9. 直接输出校正后的纯文本，不要加任何前后缀说明""",
    'tcm': """【通用校正要求】
1. 修正同音错字、形近字误识别，但以"明显是语音误听"为准
2. 补全缺失的标点（句号、逗号、问号等），保持中文标点规范
3. 修正明显的断句错误，但不改变原意
4. 保留所有时间戳标记（如 [MM:SS - MM:SS] 格式），不修改时间戳本身
5. 保留 [说话人X] 等说话人标记
6. 不要增删实质内容，不要添加解释、注释、总结
7. 直接输出校正后的纯文本，不要加任何前后缀说明

【中医专项规则】
8. 穴位名按国标《GB/T 12346 腧穴名称与定位》校正明显误听（如"足山里"→"足三里"）；拿不准时保留原词
9. 方剂名、中成药名、中药名仅在明显误听时校正（如"贵之汤"→"桂枝汤"）；药量与单位（克/钱/两/毫升/贴/剂）原样保留，不换算、不改写
10. 中医术语规范：针灸手法"泻法"不写"泄法"，通称"腧穴"不写"俞穴"；但"荣卫"等古称异写是合法写法，不要强行改成"营卫"
11. 经典原文引文（《黄帝内经》《伤寒论》《金匮要略》等条文）逐字保留：不规范化异体字（"恬惔虚无"不改"恬淡虚无"）、不译成白话、不增删一字
12. ★ 只改"转写错"，不改"说得对"：发言人的医学观点、方药用量、取穴思路即使与教材不同，也一字不动；绝不用你自己的医学知识替换、补充或"圆"内容
13. 无法确定的术语一律保留原文——宁存疑，勿擅改""",
}

_CORRECTION_DOMAIN_LABELS = {'default': '通用', 'tcm': '中医'}


def _diff_lines(orig: str, corrected: str) -> list:
    """逐行对比校正前后文本（按时间戳标记出现顺序对齐），返回内容变化的行。

    每项 {'ts', 'before', 'after'}；行数不一致（模型合并/拆行了）时返回空列表——
    该批次已被标记完整性告警覆盖，细粒度 diff 无法可靠对齐。
    """
    def _parse(text):
        out = []
        for line in text.split('\n'):
            m = _TS_RE.search(line)
            if m:
                content = line[m.end():].strip()
            else:
                content = line.strip()
            # 全空行不参与对齐：校正前后尾随换行/空行差异会造成行数假性不一致
            if not m and not content:
                continue
            out.append((m.group(0) if m else '', content))
        return out

    lo, lc = _parse(orig), _parse(corrected)
    if len(lo) != len(lc):
        return []
    changed = []
    for (ts_a, c_a), (ts_b, c_b) in zip(lo, lc):
        if c_a != c_b:
            changed.append({'ts': ts_a or ts_b, 'before': c_a, 'after': c_b})
    return changed


async def correct_transcript(
    text: str,
    model: str = DEFAULT_MODEL,
    sio=None,
    job_id: str = '',
    domain: str = 'default',
    bypass_cache: bool = False,
    round_timeout: float = DEFAULT_ROUND_TIMEOUT,
) -> Tuple[str, int, int, dict]:
    """
    用云端模型校正语音转写文本。

    针对语音转写常见错误：同音错字、专有名词误识别、标点缺失、断句不当。
    保持原意与时间戳标记，只修正明显错误，不增删内容。
    domain: 校正提示词模板（_CORRECTION_PROMPTS 键），未知值回退 default。

    返回：(校正后文本, input_tokens, output_tokens, stats)
      stats = {
        'total_chunks': int,        # 总批次数
        'failed_chunks': int,       # 校正失败/回退原文的批次数
        'integrity_warnings': int,  # 时间戳完整性校验未通过的批次数
        'length_warnings': int,     # 长度异常变化的批次数
        'warnings': [str],          # 详细告警信息
      }
    """
    model_cfg = get_model_by_key(model) or get_model_by_key(DEFAULT_MODEL)
    if not model_cfg:
        raise Exception(f'模型 {model} 不存在')

    stats = {
        'total_chunks': 0, 'failed_chunks': 0,
        'integrity_warnings': 0, 'length_warnings': 0, 'warnings': [],
    }

    # ★ 空输入直接返回，避免无意义的 API 调用
    if not text or not text.strip():
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, '⚠ 校正输入为空，跳过 AI 校正')
        return text or '', 0, 0, stats

    if sio:
        from socket_handlers import emit_progress, emit_log
        await emit_progress(sio, job_id, 'correct', 0, f'AI 校正转录文本（{model}）...')
        await emit_log(sio, job_id, f'🔧 开始 AI 校正（模型: {model}，领域: {_CORRECTION_DOMAIN_LABELS.get(domain, domain)}）')

    # ★ 按行/标记边界分块，避免在时间戳标记中间硬切。
    #   块必须小：实测商汤系模型流式 ~18-40 token/s，600s 单轮总超时下
    #   2 万字块（≈2.5 万输出 token）必超时，8 千字块（≈1 万 token）可在超时内完成；
    #   顺带让进度推送更细、单块失败重试代价更小
    max_chars = 8000
    chunks = _split_by_boundary(text, max_chars)
    stats['total_chunks'] = len(chunks)
    if len(chunks) > 1 and sio:
        from socket_handlers import emit_log
        await emit_log(sio, job_id, f'转录文本较长（{len(text)}字符），分 {len(chunks)} 批校正（按行边界）')

    corrected_parts = []
    all_changed = []
    total_in = 0
    total_out = 0

    for i, chunk in enumerate(chunks):
        pct = int((i + 1) / len(chunks) * 100)
        if sio:
            from socket_handlers import emit_progress
            await emit_progress(sio, job_id, 'correct', pct, f'AI 校正中... ({i+1}/{len(chunks)})')

        rules = _CORRECTION_PROMPTS.get(domain, _CORRECTION_PROMPTS['default'])
        prompt = f"""以下是语音识别（ASR）转录的文本。请校正其中的明显错误：

{rules}

【待校正文本】
{chunk}"""

        content, in_tok, out_tok = await _call_ai(
            model_cfg, prompt, timeout=120.0,
            sio=sio, job_id=job_id, round_label=f'校正批次 {i+1}/{len(chunks)}',
            bypass_cache=bypass_cache, round_timeout=round_timeout,
        )

        used = content
        # ① 失败标记检测：LLM 返回失败哨兵 → 回退原文
        if content.startswith('[处理失败'):
            used = chunk
            stats['failed_chunks'] += 1
            warn = f'批次 {i+1} 校正失败，保留原文'
            stats['warnings'].append(warn)
            if sio:
                from socket_handlers import emit_log
                await emit_log(sio, job_id, f'⚠ {warn}')
        else:
            # ② 时间戳/说话人标记完整性校验：校正前后数量应一致
            orig_count = _count_markers(chunk)
            corr_count = _count_markers(used)
            if orig_count != corr_count:
                stats['integrity_warnings'] += 1
                warn = f'批次 {i+1} 标记完整性异常（原文 {orig_count} 个 → 校正后 {corr_count} 个），可能丢失时间戳/说话人标记，回退原文'
                stats['warnings'].append(warn)
                if sio:
                    from socket_handlers import emit_log
                    await emit_log(sio, job_id, f'⚠ {warn}')
                used = chunk  # 标记丢失 → 回退原文，防污染下游总结

            # ③ 长度异常检测：变化超 ±30% 标记可疑（加标点会变长，但不应剧变）
            #   注意：仅告警不回退（标点补全合理地会增长），除非同时触发完整性问题
            if orig_count == corr_count:  # 完整性通过的才做长度告警
                delta = (len(used) - len(chunk)) / max(len(chunk), 1)
                if abs(delta) > 0.30:
                    stats['length_warnings'] += 1
                    warn = f'批次 {i+1} 长度变化 {delta:+.0%}（{len(chunk)}→{len(used)}），请人工复核'
                    stats['warnings'].append(warn)
                    if sio:
                        from socket_handlers import emit_log
                        await emit_log(sio, job_id, f'⚠ {warn}')

        # ④ 逐句改动明细：按时间戳对齐 diff，供结果页「改动明细」与日志复核
        #   （回退原文的批次 used==chunk，diff 自然为空，不会被记成改动）
        all_changed.extend(_diff_lines(chunk, used))
        corrected_parts.append(used)
        total_in += in_tok
        total_out += out_tok

    full_corrected = '\n'.join(corrected_parts)

    # 改动明细入 stats（持久化到 jobs 表；超量截断防 JSON 膨胀）
    stats['changed_count'] = len(all_changed)
    stats['changed_lines'] = all_changed[:100]
    stats['changed_truncated'] = len(all_changed) > 100

    if sio:
        from socket_handlers import emit_progress, emit_log
        await emit_progress(sio, job_id, 'correct', 100, 'AI 校正完成')
        summary = f'✅ 校正完成 | 原文 {len(text)} 字符 → 校正后 {len(full_corrected)} 字符 | 改动 {stats["changed_count"]} 处 | token in={total_in} out={total_out}'
        if stats['failed_chunks'] or stats['integrity_warnings'] or stats['length_warnings']:
            summary += f' | ⚠ 失败{stats["failed_chunks"]}/完整性{stats["integrity_warnings"]}/长度{stats["length_warnings"]} 批次需复核'
        await emit_log(sio, job_id, summary)
        # 改动摘要直接进日志，语义级替换从静默变可见
        if stats['changed_count'] == 0:
            await emit_log(sio, job_id, '✅ 校对零改动：原文没有需要修正的内容')
        else:
            for d in stats['changed_lines'][:15]:
                b = d['before'][:60] + ('…' if len(d['before']) > 60 else '')
                a = d['after'][:60] + ('…' if len(d['after']) > 60 else '')
                await emit_log(sio, job_id, f"✏️ {d['ts']} 「{b}」→「{a}」")
            if stats['changed_count'] > 15:
                await emit_log(sio, job_id, f'…共 {stats["changed_count"]} 处改动，完整明细见结果页「转录文本」tab')

    _record_token_usage(model, total_in, total_out, job_id)
    return full_corrected, total_in, total_out, stats


# 校正失败自动换模型：候选由 db.model_repo.resolve_ai_candidates 统一解析（本次模型 + 兜底环）
# CORRECTION_MAX_FALLBACKS 历史常量已随兜底环改造废弃（2026-10-05）——不再由校正路径自带
# 独立的隐式候选与条数上限，四个 AI 入口共用同一条环。


async def correct_transcript_with_fallback(
    text: str,
    model: str,
    sio=None,
    job_id: str = '',
    domain: str = 'default',
    fallback_models: Optional[List[str]] = None,
    max_fallbacks: Optional[int] = None,
    bypass_cache: bool = False,
    total_timeout: float = 1800.0,
    round_timeout: float = DEFAULT_ROUND_TIMEOUT,
) -> Tuple[str, int, int, dict, str]:
    """correct_transcript 的多模型兜底包装：所选模型不可用时自动换下一个候选。

    ★ 2026-10-05 候选来源改为**统一兜底环**（db.model_repo.resolve_ai_candidates）：
      校正不再用"其余全部有密钥的模型"这套隐式候选，也不再自带条数上限，
      四个 AI 入口（总结/校正/打标签/思维导图）共用同一条用户可排序的环。
      fallback_models / max_fallbacks 保留为**可选覆盖**（仅测试与临时编排用），
      两者都为 None 时走兜底环；显式传入时以传入为准。

    模型"不可用"判定（满足其一即换模型重试）：
    - 调用抛异常（端点/密钥/模型不存在等）；
    - 整批无效：failed_chunks + integrity_warnings == total_chunks > 0，
      即没有任何批次产出可用校正文本（流式不可用的模型每批都拿不到内容）。
    部分批次失败不算不可用——那些批次已逐批回退原文，整体结果仍可用，stats 会告警。
    用户取消（TaskCancelledError）直接上抛，绝不换模型重试。

    ★ total_timeout（2026-10-13）：与 ai_process 同级的**整体超时外壳**。此前只有
      单轮上限，长转录按块累加可以挂数小时；超限按可行动文案抛异常。
      round_timeout：单轮流上限（透传给 correct_transcript → _call_ai）。
      <=0 关闭对应超时。

    返回 (校正后文本, input_tokens, output_tokens, stats, 实际使用的模型 key)。
    token 为**跨候选累计**（换模型重跑是真的又烧了一次，此前只回传最后一个候选）。
    所有候选都不可用时抛出异常（由调用方按"校正失败不影响主流程"兜底）。
    """
    if fallback_models is None:
        from db.model_repo import resolve_ai_candidates
        candidates = resolve_ai_candidates(model)
    else:
        candidates = [model] + [k for k in fallback_models if k != model]
        if max_fallbacks is not None:
            candidates = candidates[:max_fallbacks + 1]
    last_error: Optional[Exception] = None
    # ★ 跨候选累计的 token（2026-10-13）：失败候选跑掉的 token 也是真金白银
    spent_in = 0
    spent_out = 0

    async def _try_candidates():
        nonlocal last_error, spent_in, spent_out
        for idx, cand in enumerate(candidates):
            if idx > 0:
                # 换模型决策同时落盘（logger）与推送（socket）：纯 socket 日志服务端排障时不可见
                logger.info(f'[{job_id}] 校正换模型重试（候选 {idx + 1}/{len(candidates)}）：{cand}')
                if sio:
                    from socket_handlers import emit_log
                    await emit_log(sio, job_id, f'🔄 自动换模型重试（候选 {idx + 1}/{len(candidates)}）：{cand}')
            try:
                corrected, in_tok, out_tok, stats = await correct_transcript(
                    text, model=cand, sio=sio, job_id=job_id, domain=domain,
                    bypass_cache=bypass_cache, round_timeout=round_timeout,
                )
            except TaskCancelledError:
                raise
            except Exception as e:
                last_error = e
                logger.warning(f'[{job_id}] 校正模型 {cand} 抛异常：{e}')
                if sio:
                    from socket_handlers import emit_log
                    await emit_log(sio, job_id, f'⚠ 模型 {cand} 校正失败：{e}')
                continue

            spent_in += in_tok
            spent_out += out_tok

            total = stats.get('total_chunks', 0)
            dead = stats.get('failed_chunks', 0) + stats.get('integrity_warnings', 0)
            if total > 0 and dead >= total:
                last_error = Exception(f'模型 {cand} 全部批次校正无效（失败 {stats.get("failed_chunks", 0)}/完整性 {stats.get("integrity_warnings", 0)}/共 {total} 批）')
                logger.warning(f'[{job_id}] {last_error}，视为该模型不可用')
                if sio:
                    from socket_handlers import emit_log
                    await emit_log(sio, job_id, f'⚠ {last_error}，视为该模型不可用')
                continue

            if idx > 0 and sio:
                from socket_handlers import emit_log
                await emit_log(sio, job_id, f'✅ 已自动切换到模型 {cand} 完成校正')
            return corrected, spent_in, spent_out, stats, cand

        raise Exception(f'所有候选模型均无法完成校正（尝试: {"、".join(candidates)}）；最后原因: {last_error}')

    # ★ 整体超时外壳（2026-10-13）：与 ai_process 的 1800s 同级，防止长转录
    #   按块累加挂数小时；超限抛可行动异常（不是裸 TimeoutError）。
    try:
        if total_timeout and total_timeout > 0:
            return await asyncio.wait_for(_try_candidates(), timeout=total_timeout)
        return await _try_candidates()
    except asyncio.TimeoutError:
        msg = (f'AI 校正整体超时（超过 {int(total_timeout)}s 已自动终止，'
               f'单轮流上限 {int(round_timeout)}s）；可把文本拆小分批校正，'
               f'或在「模型管理」换用更快的模型后重试')
        logger.error(f'[{job_id}] {msg}')
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, f'❌ {msg}')
        raise Exception(msg)


# ============ 非流式 AI 调用（2026-10-05）============
async def _post_chat_once(
    model_cfg: Dict[str, Any],
    payload: dict,
    timeout: float,
    bypass_cache: bool = False,
    sio=None,
    job_id: str = '',
) -> Tuple[str, int, int]:
    """单次非流式 chat/completions，返回 (content, in_tok, out_tok)。

    与 _call_ai 的差别：**失败一律抛异常**（不返回哨兵串）。非流式路径（打标签、
    思维导图）的调用方本来就把失败转成"这一项没产出"，由编排层决定换不换模型，
    这里再套一层哨兵只会让真实原因被 startswith 匹配反复误判。

    ★ AI 结果缓存（2026-10-06）：键 = endpoint+model_id+整个 payload（含提示词）。
    """
    import httpx as _httpx
    try:
        payload_sig = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        payload_sig = repr(payload)
    cache_key = ai_cache.make_key(
        'llm_once', model_cfg.get('endpoint', ''),
        model_cfg.get('model_id', model_cfg.get('key', '')), payload_sig,
    )
    if not bypass_cache:
        cached = ai_cache.cache_get(cache_key)
        if cached:
            if sio:
                from socket_handlers import emit_log
                await emit_log(sio, job_id,
                               f"⚡ AI 缓存命中（{cached.get('model') or '未知模型'}，"
                               f"{cached.get('created_at', '')} 生成）——相同内容直接复用上次结果，不重复调用")
            return cached.get('content', ''), 0, 0

    headers = _build_headers(model_cfg)
    async with _httpx.AsyncClient(timeout=timeout, verify=_client_verify(model_cfg)) as client:
        resp = await client.post(model_cfg['endpoint'], headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        # 链式安全取值：中转/推理类模型可能 200 但没有 content 字段（实测 2026-10-05）
        content = (data.get('choices') or [{}])[0].get('message', {}).get('content', '')
        usage = data.get('usage') or {}
        if cache_key and (content or '').strip():
            ai_cache.cache_put(
                cache_key, 'llm',
                model_cfg.get('model_id', model_cfg.get('key', '')),
                content, meta={'model_key': model_cfg.get('key', '')},
            )
        return content or '', usage.get('prompt_tokens', 0), usage.get('completion_tokens', 0)


async def run_with_fallback(
    model: str,
    step_name: str,
    timeout: float,
    build_payload: Any,
    sio=None,
    job_id: str = '',
    call_chain: Optional[list] = None,
    on_success: Optional[Any] = None,
    bypass_cache: bool = False,
):
    """非流式 AI 步骤的统一兜底编排：依次试候选模型，异常即换下一个。

    与流式的区别：这里**没有"已吐字"约束**——非流式要么整段拿到、要么什么都没拿到，
    换模型永远是安全的（这是打标签/思维导图能放心兜底的前提）。

    build_payload(model_cfg) -> payload：按候选模型配置拼请求体。
    on_success(content, model_key, in_tok, out_tok) -> Any：成功后的处理与返回值。
    返回 on_success 的结果；全部候选失败则抛出最后一个异常。
    """
    from db.model_repo import resolve_ai_candidates

    candidates = resolve_ai_candidates(model)
    if len(candidates) > 1:
        logger.info(f'[{job_id}] {step_name} 兜底环生效，候选顺序: {candidates}')

    last_error: Optional[Exception] = None
    last_detail: str = ''
    for idx, cand in enumerate(candidates):
        # ★ 每个候选尝试前查取消（2026-10-06 排查）：非流式路径此前全程不查取消，
        #   思考期取消的任务会继续把打标签/导图的剩余候选烧完（实测 d5615485）
        _check_cancelled(job_id)
        model_cfg = get_model_by_key(cand)
        if not model_cfg:
            last_error = Exception(f'模型 {cand} 不存在')
            last_detail = '模型不存在'
            if call_chain is not None:
                call_chain.append({'model': cand, 'ok': False, 'reason': last_detail})
            continue
        if idx > 0:
            logger.info(f'[{job_id}] {step_name} 换模型重试（候选 {idx + 1}/{len(candidates)}）：{cand}')
            if sio:
                from socket_handlers import emit_log
                await emit_log(sio, job_id, f'🔄 {step_name}自动换模型重试（候选 {idx + 1}/{len(candidates)}）：{cand}')
        try:
            content, in_tok, out_tok = await _post_chat_once(
                model_cfg, build_payload(model_cfg), timeout,
                bypass_cache=bypass_cache, sio=sio, job_id=job_id,
            )
        except TaskCancelledError:
            raise
        except Exception as e:
            last_error = e
            # ★ 存**人话版**原因而不是原始异常：原始 httpx 异常 str() 只会得到
            #   "403 Forbidden"，而 describe_http_error 能拿到服务端真实原因
            #   （如"无权访问 xxx 分组""账号池耗尽"）。最终抛给上层/推给前端的是这个 detail，
            #   否则一次具体的鉴权/额度失败会被降级成人看不懂的状态码。
            last_detail = describe_http_error(e) if isinstance(e, httpx.HTTPStatusError) else str(e)
            logger.warning(f'[{job_id}] {step_name} 模型 {cand} 失败: {last_detail}')
            if call_chain is not None:
                call_chain.append({'model': cand, 'ok': False, 'reason': last_detail})
            continue

        # ★ 零正文视为该候选失败（与流式路径同一不变量）：思考型模型可能只吐
        #   reasoning 就正常返回，content=''——换下一个候选，而不是把空串当结果
        if not (content or '').strip():
            last_error = Exception('模型返回空内容（可能只输出了思考过程）')
            last_detail = str(last_error)
            logger.warning(f'[{job_id}] {step_name} 模型 {cand} 返回空内容，视为失败换下一候选')
            if call_chain is not None:
                call_chain.append({'model': cand, 'ok': False, 'reason': last_detail})
            continue

        if call_chain is not None:
            call_chain.append({'model': cand, 'ok': True, 'reason': ''})
        if idx > 0 and sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, f'✅ {step_name}已自动切换到模型 {cand} 完成')
        return on_success(content, cand, in_tok, out_tok) if on_success else content

    raise Exception(
        f'{step_name}所有候选模型均失败（尝试: {"、".join(candidates)}）；最后原因: {last_detail or last_error}'
    )


# ============ 思维导图 ============

async def generate_mindmap(
    result_text: str,
    title: str = '',
    model: str = DEFAULT_MODEL,
    sio=None,
    job_id: str = '',
    bypass_cache: bool = False,
) -> str:
    """基于总结结果生成 markmap 兼容的 Markdown 思维导图

    调用一次 LLM，把总结文本转为层级化 Markdown。
    返回可直接传给 markmap 渲染的 Markdown 字符串。
    """
    model_cfg = get_model_by_key(model) or get_model_by_key(DEFAULT_MODEL)
    if not model_cfg:
        raise Exception(f'模型 {model} 不存在')

    # 截断过长的输入，思维导图不需要完整总结
    text_snippet = result_text[:8000] if len(result_text) > 8000 else result_text

    prompt = f"""你是一个思维导图生成专家。请根据以下视频总结内容，生成一个结构清晰的思维导图（纯 Markdown 格式，用于 markmap 渲染）。

视频标题：{title or '未命名'}

总结内容：
{text_snippet}

请严格按以下规则输出：
1. 第一行必须是 # 标题（作为根节点）
2. 使用 ## 作为一级分支（如：内容概要、章节要点、核心观点、关键概念）
3. 使用 ### 作为二级分支
4. 使用 - 作为叶子节点（具体要点）
5. 每个叶子节点不超过 20 个中文字，简洁有力
6. 层级不超过 4 层
7. 只输出 Markdown 内容，不要加任何解释、前言、后记"""

    # ★ 非流式调用：导图 prompt 短，不走流式，避免智谱断连
    def _build(cfg: dict) -> dict:
        return {
            "model": cfg.get('model_id', cfg.get('key', '')),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 4096,
        }

    def _on_ok(content: str, used_model: str, in_tok: int, out_tok: int) -> str:
        _record_token_usage(used_model, in_tok, out_tok, job_id)
        return content

    try:
        return await run_with_fallback(
            model, '思维导图', 120.0, _build, sio=sio, job_id=job_id, on_success=_on_ok,
            bypass_cache=bypass_cache,
        )
    except httpx.HTTPStatusError as e:
        detail = describe_http_error(e)
        logger.error(f'思维导图生成失败: {detail}')
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, f'⚠ 思维导图生成失败: {detail}')
        return ''
    except Exception as e:
        logger.error(f'思维导图生成失败: {e}')
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, f'⚠ 思维导图生成失败: {e}')
        return ''


# ============ 自动打标签 ============
async def generate_tags(
    text: str,
    model: str = DEFAULT_MODEL,
    sio=None,
    job_id: str = '',
    bypass_cache: bool = False,
) -> Optional[List[str]]:
    """用AI分析文本生成3-5个分类标签（走统一兜底环：任一候选失败自动换下一个）

    ★ 返回值语义（2026-10-13 契约 A，原为「失败也返回 []」）——调用方必须能分清
      「调用失败」和「模型没吐出标签」，否则会把"打标签挂了"当成"没有标签"而
      清空已有标签并返回成功：
      - list（非空）：成功且有标签
      - []：调用成功但模型没吐出合法标签数组（解析不出，不算失败）
      - None：**调用失败**（兜底环内全部候选抛异常）
    """
    prompt = f"""请分析以下视频转录文本的内容主题，返回 3-5 个分类标签。
标签要求：
- 每个标签 2-5 个字
- 覆盖：学科领域、内容类型、关键主题
- 可包含思想史、学派、思想家相关标签
- 示例：哲学、现象学、黑格尔、学术讲座
- 只输出 JSON 数组，不要其他内容：["标签1","标签2","标签3"]

转录文本（前 3000 字）：
{text[:3000]}"""

    def _build(model_cfg: dict) -> dict:
        return {
            "model": model_cfg.get('model_id', model_cfg.get('key', DEFAULT_MODEL)),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 100,
        }

    def _parse(content: str, used_model: str, in_tok: int, out_tok: int) -> List[str]:
        # ★ token 记账（2026-10-13）：打标签此前**从不记账**（全项目只有 3 处调用
        #   _record_token_usage，它不在其中），真实账单高于系统显示。记**实际使用**
        #   的模型 key 而非请求的那个，与 generate_mindmap 的 _on_ok 同一口径。
        _record_token_usage(used_model, in_tok, out_tok, job_id)
        # 清理 markdown 代码块包裹
        cleaned = content.strip()
        if cleaned.startswith('```'):
            cleaned = re.sub(r'^```\w*\n?', '', cleaned)
            cleaned = re.sub(r'\n?```$', '', cleaned)
        # 提取 JSON 数组
        match = re.search(r'\[.*?\]', cleaned, re.DOTALL)
        if match:
            try:
                tags = json.loads(match.group())
                return [t for t in tags if isinstance(t, str)][:5]
            except json.JSONDecodeError:
                logger.warning(f'打标签：模型返回的 JSON 数组无法解析: {match.group()[:80]}')
        return []

    try:
        return await run_with_fallback(
            model, '打标签', 30.0, _build, sio=sio, job_id=job_id,
            on_success=_parse, bypass_cache=bypass_cache,
        )
    except TaskCancelledError:
        # ★ 必须排在 except Exception 之前（2026-10-13）：TaskCancelledError 是
        #   Exception 子类，被当成普通失败吞成 None 会丢掉取消语义——上层因此把
        #   用户取消的任务继续跑完（或记成"只是没打上标签"）。
        raise
    except Exception as e:
        # 失败返回 None（= 调用失败），不是 []（= 调用成功但没标签）：调用方据此
        # 区分"打标签挂了"和"确实没有标签"，不再清空已有标签后当成功。
        logger.warning(f'[{job_id}] 自动打标签失败（调用失败，返回 None）: {e}')
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id,
                           f'⚠ 打标签失败（未产出任何标签）: {e}；可稍后重试或在模型管理页换用可用模型')
        return None


# ============ Token 统计 ============
def _record_token_usage(model_key: str, input_tokens: int, output_tokens: int, job_id: str = ''):
    """记录Token使用到数据库"""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO token_usage (job_id, model_key, input_tokens, output_tokens)
            VALUES (?, ?, ?, ?)
        """, (job_id, model_key, input_tokens, output_tokens))
        conn.commit()
    except Exception as e:
        logger.warning(f'Token统计写入失败: {e}')
    finally:
        conn.close()
