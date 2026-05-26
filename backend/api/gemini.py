from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
import asyncio
import json as _json
import logging
from typing import Any

from backend.core.config import resolve_model
from backend.core.config import settings
from backend.core.request_logging import new_request_id, request_context, update_request_context
from backend.runtime import stream_presenter
from backend.runtime.execution import build_tool_directive
from backend.services.completion_bridge import run_retryable_completion_bridge
from backend.services.auth_quota import resolve_auth_context
from backend.services.response_formatters import build_gemini_generate_payload, _client_visible_tool_name
from backend.services.standard_request_builder import build_chat_standard_request
from backend.toolcore.request_normalizer import normalize_gemini_request, to_prompt_payload
from backend.toolcore.stream_sieve import ToolStreamSieve

log = logging.getLogger("qwen2api.gemini")
router = APIRouter()

GEMINI_STREAM_MEDIA_TYPE = "application/json"
GEMINI_SSE_MEDIA_TYPE = "text/event-stream"


def _gemini_to_chat_payload(model: str, body: dict[str, Any], *, force_stream: bool | None = None) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    for message in body.get("contents", []) or []:
        role = "assistant" if message.get("role") == "model" else "user"
        text_parts: list[str] = []
        func_calls: list[dict[str, Any]] = []
        func_responses: list[dict[str, Any]] = []
        for part in message.get("parts", []) or []:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text:
                text_parts.append(text)
            elif "functionCall" in part and isinstance(part["functionCall"], dict):
                func_calls.append(part["functionCall"])
            elif "functionResponse" in part and isinstance(part["functionResponse"], dict):
                func_responses.append(part["functionResponse"])
        if text_parts:
            messages.append({"role": role, "content": "\n".join(text_parts)})
        if func_calls and role == "assistant":
            for fc in func_calls:
                messages.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": f"call_{len(messages)}",
                        "type": "function",
                        "function": {
                            "name": str(fc.get("name", "")),
                            "arguments": _json.dumps(fc.get("args", {}), ensure_ascii=False) if isinstance(fc.get("args"), dict) else "{}",
                        },
                    }],
                })
        if func_responses and role == "user":
            for fr in func_responses:
                resp = fr.get("response")
                content = _json.dumps(resp, ensure_ascii=False) if isinstance(resp, dict) else str(resp or "")
                messages.append({
                    "role": "tool",
                    "tool_call_id": f"call_{len(messages)}",
                    "name": str(fr.get("name", "")),
                    "content": content,
                })

    tools: list[dict[str, Any]] = []
    for tool in body.get("tools", []) or []:
        declarations = tool.get("functionDeclarations", []) if isinstance(tool, dict) else []
        for declaration in declarations or []:
            if not isinstance(declaration, dict):
                continue
            tools.append({
                "type": "function",
                "function": {
                    "name": declaration.get("name", ""),
                    "description": declaration.get("description", ""),
                    "parameters": declaration.get("parameters", {}),
                },
            })

    tool_choice: Any = None
    tool_config = body.get("toolConfig")
    if isinstance(tool_config, dict):
        function_calling = tool_config.get("functionCallingConfig")
        if isinstance(function_calling, dict):
            mode = str(function_calling.get("mode") or "").strip().upper()
            if mode == "NONE":
                tool_choice = "none"
            elif mode == "ANY":
                tool_choice = "required"
            elif mode == "AUTO":
                tool_choice = "auto"

    stream_requested = _is_gemini_stream_request(body) if force_stream is None else force_stream
    return {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice,
        "stream": stream_requested,
    }


def _is_gemini_stream_request(body: dict[str, Any]) -> bool:
    if body.get("stream") is True:
        return True
    generation_config = body.get("generationConfig")
    if isinstance(generation_config, dict) and generation_config.get("stream") is True:
        return True
    return False


def _build_standard_request(model: str, body: dict, *, stream: bool | None = None):
    normalized_request = normalize_gemini_request(body, model=model, force_stream=stream)
    payload = to_prompt_payload(normalized_request, model=model, stream=_is_gemini_stream_request(body) if stream is None else stream)
    return build_chat_standard_request(payload, default_model=model, surface="gemini", client_profile="openclaw_openai")


def _gemini_chunk_payload(text: str) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": text}],
                    "role": "model",
                }
            }
        ]
    }


async def _load_and_validate_request(request: Request, model: str, *, force_stream: bool | None = None):
    app = request.app
    users_db = app.state.users_db
    client = app.state.qwen_client

    auth = await resolve_auth_context(request, users_db)
    token = auth.token

    body = await request.json()
    standard_request = _build_standard_request(model, body, stream=force_stream)
    update_request_context(resolved_model=standard_request.resolved_model)
    return users_db, client, token, standard_request


@router.post("/v1beta/models/{model}:generateContent")
@router.post("/v1/models/{model}:generateContent")
@router.post("/models/{model}:generateContent")
async def gemini_generate_content(model: str, request: Request):
    with request_context(req_id=new_request_id(), surface="gemini", requested_model=model):
        users_db, client, token, standard_request = await _load_and_validate_request(request, model, force_stream=False)
        content = standard_request.prompt
        log.info(f"[Gemini] route=generateContent model={standard_request.resolved_model}, stream={standard_request.stream}, prompt_len={len(content)}")

        try:
            result = await run_retryable_completion_bridge(
                client=client,
                standard_request=standard_request,
                prompt=content,
                users_db=users_db,
                token=token,
                api_key_manager=getattr(request.app.state, "api_key_manager", None),
                history_messages=standard_request.tools and [] or [],
                max_attempts=2 if standard_request.tools else 3,
                allow_after_visible_output=True,
            )
            execution = result.execution
        except Exception as e:
            log.error(f"Gemini proxy failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

        log.info(f"[Gemini] Request complete. Generated {len(execution.state.answer_text)} characters.")
        return JSONResponse(build_gemini_generate_payload(execution=execution, standard_request=standard_request))


@router.post("/v1beta/models/{model}:streamGenerateContent")
@router.post("/v1/models/{model}:streamGenerateContent")
@router.post("/models/{model}:streamGenerateContent")
async def gemini_stream_generate_content(model: str, request: Request):
    with request_context(req_id=new_request_id(), surface="gemini", requested_model=model):
        users_db, client, token, standard_request = await _load_and_validate_request(request, model, force_stream=True)
        content = standard_request.prompt
        
        # Detect SSE format request
        alt_sse = request.query_params.get("alt") == "sse"
        media_type = GEMINI_SSE_MEDIA_TYPE if alt_sse else GEMINI_STREAM_MEDIA_TYPE
        chunk_formatter = stream_presenter.gemini_sse_chunk if alt_sse else stream_presenter.gemini_text_chunk
        log.info(f"[Gemini] route=streamGenerateContent model={standard_request.resolved_model}, stream={standard_request.stream}, prompt_len={len(content)}, format={'sse' if alt_sse else 'jsonl'}")

        async def generate():
            queue: asyncio.Queue[str | None] = asyncio.Queue()

            # Always create a new-version ToolStreamSieve when tools are present.
            # This is the second line of defence — the sieve inside execution.py
            # may use the old ToolSieve when TOOLCORE_V2_ENABLED is False, which
            # cannot detect DSML markup.
            tool_sieve = ToolStreamSieve(standard_request.tool_names) if standard_request.tool_names else None

            async def on_delta(evt, text_chunk, _tool_calls):
                if not text_chunk:
                    return
                # Stream answer-phase text (and flush text where phase may be None).
                phase = evt.get("phase") if evt is not None else None
                if phase not in ("answer", None):
                    return

                if tool_sieve:
                    sieve_events = tool_sieve.process_chunk(text_chunk)
                    for sieve_evt in (sieve_events or []):
                        if not isinstance(sieve_evt, dict):
                            continue
                        if sieve_evt.get("type") == "content":
                            safe_text = sieve_evt.get("text", "")
                            if safe_text:
                                await queue.put(chunk_formatter(safe_text))
                        # tool_calls from sieve are handled post-completion
                        # via build_tool_directive, so we skip them here.
                else:
                    await queue.put(chunk_formatter(text_chunk))

            async def runner():
                try:
                    result = await run_retryable_completion_bridge(
                        client=client,
                        standard_request=standard_request,
                        prompt=content,
                        users_db=users_db,
                        token=token,
                        api_key_manager=getattr(request.app.state, "api_key_manager", None),
                        history_messages=standard_request.tools and [] or [],
                        max_attempts=2 if standard_request.tools else 3,
                        allow_after_visible_output=True,
                        capture_events=False,
                        on_delta=on_delta,
                    )
                    execution = result.execution

                    # Flush any remaining buffered safe text from the sieve.
                    if tool_sieve:
                        for flush_evt in (tool_sieve.flush() or []):
                            if isinstance(flush_evt, dict) and flush_evt.get("type") == "content":
                                safe_text = flush_evt.get("text", "")
                                if safe_text:
                                    await queue.put(chunk_formatter(safe_text))

                    # Emit tool-call result chunk at the end of the stream if
                    # the model decided to call a function.
                    directive = build_tool_directive(standard_request, execution.state)
                    if directive.stop_reason == "tool_use":
                        tool_call_parts = [
                            {
                                "functionCall": {
                                    "name": _client_visible_tool_name(block.get("name", ""), standard_request.tool_catalog),
                                    "args": block.get("input", {}),
                                }
                            }
                            for block in directive.tool_blocks
                            if block.get("type") == "tool_use" and block.get("name")
                        ]
                        if tool_call_parts:
                            tc_payload = {
                                "candidates": [
                                    {
                                        "content": {
                                            "parts": tool_call_parts,
                                            "role": "model",
                                        },
                                        "finishReason": "STOP",
                                        "index": 0,
                                    }
                                ]
                            }
                            import json as _json
                            if alt_sse:
                                await queue.put(f"data: {_json.dumps(tc_payload, ensure_ascii=False)}\n\n")
                            else:
                                await queue.put(_json.dumps(tc_payload, ensure_ascii=False) + "\n")

                    log.info(f"[Gemini] Stream complete. Generated {len(execution.state.answer_text)} chars, tool_use={directive.stop_reason == 'tool_use'}.")
                except Exception as e:
                    await queue.put(stream_presenter.gemini_error_chunk(str(e)))
                finally:
                    await queue.put(None)

            task = asyncio.create_task(runner())
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                yield chunk
            await task

        return StreamingResponse(generate(), media_type=media_type)
