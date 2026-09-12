"""
YouTube Tool V2 - AI 处理服务
核心：标准AI总结 + 字幕翻译 + 自动打标签 + Token统计
使用 httpx AsyncClient 替代旧代码的 requests.post
"""
import asyncio
import json
import logging
import re
import time
from typing import Optional, Tuple, Dict, Any, List

import httpx

from config import DEFAULT_MODEL, PROXY, DATA_DIR
from db.model_repo import get_model_by_key
from db.database import get_connection
from services.prompts import (
    SUMMARY_PROMPTS, ORG_STRATEGY_DESCS,
    build_prompt_vars, CATEGORY_PROMPTS,
)
from services.httpx_errors import describe_http_error

logger = logging.getLogger(__name__)


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
async def _call_ai(
    model_cfg: Dict[str, Any],
    prompt: str,
    timeout: float = 120.0,
    sio=None,
    job_id: str = '',
    round_label: str = '',
    final: bool = True,
    max_retries: int = 3,
) -> Tuple[str, int, int]:
    """单次AI调用（支持流式输出+批量推送+断连重试），返回(content, input_tokens, output_tokens)
    
    max_retries: 流式断连时最大重试次数（指数退避: 2s → 4s → 8s）
    """
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

    try:
        async def _stream():
            """在带总超时的协程内完成流式读取，避免 API 半死不活时永久挂起"""
            content_parts = []
            in_tok = 0
            out_tok = 0
            token_count = 0

            # ★ 批量推送缓冲区：每 BATCH_TOKENS 个 token 或每 BATCH_INTERVAL 秒发送一次
            _BATCH_TOKENS = 10
            _BATCH_INTERVAL = 0.05  # 50ms
            token_buffer = []
            thinking_buffer = []
            last_flush_time = time.monotonic()
            batch_flush_count = 0

            async with httpx.AsyncClient(timeout=timeout, verify=_client_verify(model_cfg)) as client:
                async with client.stream("POST", model_cfg['endpoint'], headers=headers, json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
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
                            if reasoning_piece and sio:
                                thinking_buffer.append(reasoning_piece)
                                if len(thinking_buffer) >= _BATCH_TOKENS:
                                    from socket_handlers import emit_ai_thinking
                                    await emit_ai_thinking(sio, job_id, ''.join(thinking_buffer), done=False)
                                    thinking_buffer = []
                            if text_piece:
                                content_parts.append(text_piece)
                                token_count += 1
                                # ★ 每 20 个 token 检查一次取消标记（避免高频检查影响性能）
                                if token_count % 20 == 0:
                                    _check_cancelled(job_id)

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
        last_error = None
        for retry in range(max_retries + 1):
            try:
                content_parts, in_tok, out_tok, batch_flush_count = await asyncio.wait_for(_stream(), timeout=600.0)
                last_error = None
                break  # 成功
            except asyncio.TimeoutError:
                logger.error('AI调用超时（单轮总超时 600s）')
                if sio:
                    from socket_handlers import emit_log
                    await emit_log(sio, job_id, 'AI处理超时（已自动终止，请重试）')
                return '[处理失败: 超时]', 0, 0
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout, ConnectionError) as e:
                last_error = e
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
                    return '[处理失败: 连接断开]', 0, 0
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
                return f'[处理失败: {detail}]', 0, 0
            except Exception as e:
                # 非网络错误（如 JSON 解析失败等），不重试
                logger.error(f'AI调用失败: {e}')
                if sio:
                    from socket_handlers import emit_log
                    await emit_log(sio, job_id, f'AI处理失败: {e}')
                return f'[处理失败: {e}]', 0, 0

        content = ''.join(content_parts)


        # 如果 usage 没拿到，估算
        if not out_tok:
            out_tok = len(content) // 2  # 粗略估算

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
        return f'[处理失败: {detail}]', 0, 0
    except Exception as e:
        logger.error(f'AI调用失败: {e}')
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id, f'AI处理失败: {e}')
        return f'[处理失败: {e}]', 0, 0


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
) -> Tuple[str, int, int]:
    """
    标准单轮/分批AI处理
    返回：(result_text, input_tokens, output_tokens)

    支持两种提示词模式：
    1. 模块化（推荐）：传入 dimensions 字典（7维），由 prompt_builder 拼装
    2. 回退：使用 summary_type 查找 SUMMARY_PROMPTS（保留兼容）
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

    for i, chunk in enumerate(chunks):
        # ★ 每批开始前检查取消标记
        _check_cancelled(job_id)
        
        pct = int(10 + (i + 1) / len(chunks) * 80)
        if sio:
            from socket_handlers import emit_progress
            await emit_progress(sio, job_id, 'ai', pct, f'AI处理中... ({i+1}/{len(chunks)})')

        # 分批时每批只传入部分内容
        if len(chunks) > 1:
            if use_modular:
                from services.prompt_builder import build_modular_prompt
                chunk_prompt = build_modular_prompt(
                    dimensions=dimensions,
                    content=f'[第 {i+1}/{len(chunks)} 部分]\n\n{chunk}',
                    custom_prompt=custom_prompt or opts.get("custom_prompt", ""),
                    full_override=opts.get("full_override", ""),
                )
            else:
                chunk_prompt = prompt_template.replace('{{CONTENT}}', f'[第 {i+1}/{len(chunks)} 部分]\n\n{chunk}')
        else:
            chunk_prompt = prompt

        content, in_tok, out_tok = await _call_ai(
            model_cfg, chunk_prompt, timeout=120.0,
            sio=sio, job_id=job_id, round_label=f'批次 {i+1}/{len(chunks)}', final=False
        )
        results.append(content)
        total_in += in_tok
        total_out += out_tok

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

    return full_result, total_in, total_out


# ============ 字幕翻译 ============
async def translate_subtitles(
    srt_path: str,
    target_lang: str = 'zh-CN',
    model: str = DEFAULT_MODEL,
    category: str = 'auto',
    sio=None,
    job_id: str = '',
) -> str:
    """翻译SRT字幕，返回翻译后的SRT路径"""
    model_cfg = get_model_by_key(model) or get_model_by_key(DEFAULT_MODEL)
    headers = _build_headers(model_cfg)

    from config import TRANSLATE_LANGS
    lang_name = TRANSLATE_LANGS.get(target_lang, {}).get("name", target_lang)

    if sio:
        from socket_handlers import emit_progress, emit_log
        await emit_progress(sio, job_id, 'translate', 0, f'正在翻译字幕 → {lang_name}...')

    with open(srt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    blocks = content.strip().split('\n\n')
    batch_size = 80
    batches = [blocks[i:i+batch_size] for i in range(0, len(blocks), batch_size)]

    cat_cfg = CATEGORY_PROMPTS.get(category, CATEGORY_PROMPTS['auto'])

    translated = []
    failed_batches = []  # 翻译失败保留原文的批次（1-based），完成后必须可见（禁止静默吞错）
    for i, batch in enumerate(batches):
        pct = int((i + 1) / len(batches) * 90)
        if sio:
            from socket_handlers import emit_progress
            await emit_progress(sio, job_id, 'translate', pct, f'翻译中... ({i+1}/{len(batches)}) → {lang_name}')

        batch_text = '\n\n'.join(batch)
        prompt = f"""请将以下{cat_cfg['name']}相关的字幕翻译成{lang_name}。

要求：
1. 序号和时间戳格式保持原样
2. 使用自然流畅的{lang_name}
3. 专有名词可保留原文并附翻译
4. 保持字幕的简洁性

字幕内容：
""" + batch_text

        payload = {
            "model": model_cfg.get('model_id', model),
            "messages": [
                {"role": "system", "content": "你是专业的字幕翻译助手，同时具备基础学术素养，对思想史关键术语保持警觉。\n\n翻译协议：\n1. 序号和时间戳格式严格保持原样\n2. 同一原文术语全文使用统一译名，思想史术语优先采用学界约定俗成的译法\n3. 专有名词保留原文并附翻译\n4. 保持字幕简洁性，但不牺牲概念精确性\n5. 转录有歧义处，标注 [译注：原文此处可能为……]"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0, verify=_client_verify(model_cfg)) as client:
                resp = await client.post(model_cfg['endpoint'], headers=headers, json=payload)
                resp.raise_for_status()
                result = resp.json()
                translated_text = result['choices'][0]['message']['content']
                translated.append(translated_text)
        except httpx.HTTPStatusError as e:
            logger.warning(f'字幕翻译失败 (批次 {i+1}): {describe_http_error(e)}')
            translated.append(batch_text)
            failed_batches.append(i + 1)
        except Exception as e:
            logger.warning(f'字幕翻译失败 (批次 {i+1}): {e}')
            translated.append(batch_text)
            failed_batches.append(i + 1)

    # 生成翻译文件
    lang_suffix = target_lang.replace('-', '_')
    cn_srt_path = srt_path.replace('.srt', f'__{lang_suffix}.srt')
    with open(cn_srt_path, 'w', encoding='utf-8') as f:
        f.write('\n\n'.join(translated))

    # ★ 部分失败必须对用户可见：失败批次保留了原文，产物里会出现中英混杂
    if failed_batches:
        failed_desc = '、'.join(f'#{n}' for n in failed_batches[:10]) + ('…' if len(failed_batches) > 10 else '')
        logger.warning(f'字幕翻译部分失败: {len(failed_batches)}/{len(batches)} 批保留原文 ({failed_desc})')
        if sio:
            from socket_handlers import emit_log
            await emit_log(sio, job_id,
                           f'⚠ 字幕翻译 {len(failed_batches)}/{len(batches)} 批次失败（{failed_desc}），失败段落保留原文')

    if sio:
        from socket_handlers import emit_progress
        await emit_progress(sio, job_id, 'translate', 100, '字幕翻译完成！')

    return cn_srt_path


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
            sio=sio, job_id=job_id, round_label=f'校正批次 {i+1}/{len(chunks)}'
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


# 校正失败自动换模型：最多再试 max_fallbacks 个备选，防大量失效模型无限拖长任务
CORRECTION_MAX_FALLBACKS = 3


async def correct_transcript_with_fallback(
    text: str,
    model: str,
    sio=None,
    job_id: str = '',
    domain: str = 'default',
    fallback_models: Optional[List[str]] = None,
    max_fallbacks: int = CORRECTION_MAX_FALLBACKS,
) -> Tuple[str, int, int, dict, str]:
    """correct_transcript 的多模型兜底包装：所选模型不可用时自动换下一个候选。

    模型"不可用"判定（满足其一即换模型重试）：
    - 调用抛异常（端点/密钥/模型不存在等）；
    - 整批无效：failed_chunks + integrity_warnings == total_chunks > 0，
      即没有任何批次产出可用校正文本（流式不可用的模型每批都拿不到内容）。
    部分批次失败不算不可用——那些批次已逐批回退原文，整体结果仍可用，stats 会告警。
    用户取消（TaskCancelledError）直接上抛，绝不换模型重试。

    返回 (校正后文本, input_tokens, output_tokens, stats, 实际使用的模型 key)。
    所有候选都不可用时抛出异常（由调用方按"校正失败不影响主流程"兜底）。
    """
    candidates = [model] + [k for k in (fallback_models or []) if k != model][:max_fallbacks]
    last_error: Optional[Exception] = None

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
        return corrected, in_tok, out_tok, stats, cand

    raise Exception(f'所有候选模型均无法完成校正（尝试: {"、".join(candidates)}）；最后原因: {last_error}')


# ============ 思维导图 ============

async def generate_mindmap(
    result_text: str,
    title: str = '',
    model: str = DEFAULT_MODEL,
    sio=None,
    job_id: str = '',
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
    import httpx
    headers = _build_headers(model_cfg)
    payload = {
        "model": model_cfg.get('model_id', model_cfg.get('key', '')),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 4096,
    }
    try:
        async with httpx.AsyncClient(timeout=120.0, verify=_client_verify(model_cfg)) as client:
            resp = await client.post(model_cfg['endpoint'], headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            in_tok = data.get('usage', {}).get('prompt_tokens', 0)
            out_tok = data.get('usage', {}).get('completion_tokens', 0)
            _record_token_usage(model, in_tok, out_tok, job_id)
            return content or ''
    except httpx.HTTPStatusError as e:
        logger.error(f'思维导图生成失败: {describe_http_error(e)}')
        return ''
    except Exception as e:
        logger.error(f'思维导图生成失败: {e}')
        return ''


# ============ 自动打标签 ============
async def generate_tags(
    text: str,
    model: str = DEFAULT_MODEL,
    sio=None,
    job_id: str = '',
) -> List[str]:
    """用AI分析文本生成3-5个分类标签"""
    model_cfg = get_model_by_key(model) or get_model_by_key(DEFAULT_MODEL)
    headers = _build_headers(model_cfg)

    prompt = f"""请分析以下视频转录文本的内容主题，返回 3-5 个分类标签。
标签要求：
- 每个标签 2-5 个字
- 覆盖：学科领域、内容类型、关键主题
- 可包含思想史、学派、思想家相关标签
- 示例：哲学、现象学、黑格尔、学术讲座
- 只输出 JSON 数组，不要其他内容：["标签1","标签2","标签3"]

转录文本（前 3000 字）：
{text[:3000]}"""

    payload = {
        "model": model_cfg.get('model_id', model),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 100,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0, verify=_client_verify(model_cfg)) as client:
            resp = await client.post(model_cfg['endpoint'], headers=headers, json=payload)
            resp.raise_for_status()
            content = resp.json()['choices'][0]['message']['content']
            # 清理 markdown 代码块包裹
            cleaned = content.strip()
            if cleaned.startswith('```'):
                cleaned = re.sub(r'^```\w*\n?', '', cleaned)
                cleaned = re.sub(r'\n?```$', '', cleaned)
            # 提取 JSON 数组
            match = re.search(r'\[.*?\]', cleaned, re.DOTALL)
            if match:
                tags = json.loads(match.group())
                return [t for t in tags if isinstance(t, str)][:5]
            return []
    except httpx.HTTPStatusError as e:
        logger.warning(f'自动打标签失败: {describe_http_error(e)}')
        return []
    except Exception as e:
        logger.warning(f'自动打标签失败: {e}')
        return []


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
