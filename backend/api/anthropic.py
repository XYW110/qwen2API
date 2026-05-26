import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from backend.adapter.standard_request import StandardRequest, enforce_declared_tool_choice
from backend.core.config import resolve_model, settings
from backend.core.request_logging import new_request_id, request_context, update_request_context
from backend.runtime import stream_presenter
from backend.runtime.execution import (
    build_tool_directive,
    cleanup_runtime_resources,
    collect_completion_run,
    evaluate_retry_directive,
    request_max_attempts,
)
from backend.services.auth_quota import resolve_auth_context
from backend.toolcore.stream_sieve import ToolStreamSieve
from backend.services.context_attachment_manager import prepare_context_attachments, derive_session_key
from backend.services.attachment_preprocessor import preprocess_attachments
from backend.services.client_profiles import CLAUDE_CODE_OPENAI_PROFILE
from backend.toolcore.prompt_builder import messages_to_prompt
from backend.services.response_formatters import _client_visible_tool_name, build_anthropic_message_payload
from backend.services.qwen_client import QwenClient
from backend.services.token_calc import calculate_usage, count_tokens
from backend.adapter.standard_request import normalize_tool_choice
from backend.toolcore.request_normalizer import normalize_anthropic_request, to_prompt_payload
from backend.toolcore.task_session import (
    build_anthropic_assistant_history_message,
    build_retry_rebase_prompt,
    clear_invalidated_session_chat,
    log_session_plan_reuse_cancelled,
    persist_session_turn,
    plan_persistent_session_turn,
)
from backend.services.completion_bridge import _fire_and_forget_stats, run_retryable_completion_bridge
from backend.toolcall.normalize import build_tool_name_registry

log = logging.getLogger("qwen2api.anthropic")
router = APIRouter()


class _AnthropicStreamState:
    def __init__(self, *, msg_id: str, model_name: str, prompt: str, extra_prompt_tokens: int = 0):
        self.msg_id = msg_id
        self.model_name = model_name
        self.prompt = prompt
        self.extra_prompt_tokens = extra_prompt_tokens
        self.pending_chunks: list[str] = []
        self.answer_text_buffer: list[tuple[int, str]] = []
        self.block_index = 0
        self.current_block: dict[str, object] = {"type": None, "index": None, "tool_call_id": None}
        self.opened_tool_calls: set[str] = set()

    def ensure_message_start(self) -> None:
        if not self.pending_chunks:
            self.pending_chunks.append(_message_start_event(
                self.msg_id,
                self.model_name,
                self.prompt,
                "",
                extra_prompt_tokens=self.extra_prompt_tokens,
            ))

    def close_current_block(self) -> None:
        index = self.current_block.get("index")
        if not isinstance(index, int):
            return
        self.pending_chunks.append(stream_presenter.anthropic_content_block_stop(index))
        self.current_block = {"type": None, "index": None, "tool_call_id": None}

    def open_textual_block(self, block_type: str) -> int:
        current_type = self.current_block.get("type")
        current_index = self.current_block.get("index")
        if current_type == block_type and isinstance(current_index, int):
            return current_index
        self.close_current_block()
        index = self.block_index
        self.block_index += 1
        if block_type == "thinking":
            content_block = {"type": "thinking", "thinking": ""}
        else:
            content_block = {"type": "text", "text": ""}
        self.pending_chunks.append(stream_presenter.anthropic_content_block_start(index, content_block))
        self.current_block = {"type": block_type, "index": index, "tool_call_id": None}
        return index

    def open_tool_block(self, tool_call_id: str, tool_name: str) -> int:
        current_index = self.current_block.get("index")
        if (
            self.current_block.get("type") == "tool_use"
            and self.current_block.get("tool_call_id") == tool_call_id
            and isinstance(current_index, int)
        ):
            return current_index
        self.close_current_block()
        index = self.block_index
        self.block_index += 1
        self.pending_chunks.append(
            f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': index, 'content_block': {'type': 'tool_use', 'id': tool_call_id, 'name': tool_name, 'input': {}}}, ensure_ascii=False)}\n\n"
        )
        self.current_block = {"type": "tool_use", "index": index, "tool_call_id": tool_call_id}
        self.opened_tool_calls.add(tool_call_id)
        return index

    def append_thinking_delta(self, text_chunk: str) -> None:
        index = self.open_textual_block("thinking")
        self.pending_chunks.append(
            stream_presenter.anthropic_content_block_delta(index, {"type": "thinking_delta", "thinking": text_chunk})
        )

    def buffer_answer_text(self, text_chunk: str) -> None:
        index = self.open_textual_block("text")
        self.answer_text_buffer.append((index, text_chunk))

    def append_tool_delta(self, *, tool_call_id: str, tool_name: str, partial_json: str) -> None:
        index = self.open_tool_block(tool_call_id, tool_name)
        if partial_json:
            self.pending_chunks.append(
                stream_presenter.anthropic_content_block_delta(index, {"type": "input_json_delta", "partial_json": partial_json})
            )

    def flush_answer_text(self) -> None:
        if not self.answer_text_buffer:
            return
        for index, text_chunk in self.answer_text_buffer:
            self.pending_chunks.append(
                stream_presenter.anthropic_content_block_delta(index, {"type": "text_delta", "text": text_chunk})
            )
        self.answer_text_buffer = []

    def clear_answer_text(self) -> None:
        self.answer_text_buffer = []


def _build_standard_request(req_data: dict) -> StandardRequest:
    model_name = req_data.get("model", "claude-3-5-sonnet")
    normalized_request = normalize_anthropic_request(req_data)
    normalized_payload = to_prompt_payload(normalized_request, model=model_name, stream=bool(req_data.get("stream", False)))
    for field_name in ("system", "developer", "instructions"):
        if field_name in req_data:
            normalized_payload[field_name] = req_data.get(field_name, "")
    prompt_result = messages_to_prompt(normalized_payload, client_profile=CLAUDE_CODE_OPENAI_PROFILE)
    prompt = prompt_result.prompt
    tools = prompt_result.tools
    tool_names = [tool_name for tool_name in (tool.get("name") for tool in tools) if isinstance(tool_name, str) and tool_name]
    tool_choice = normalize_tool_choice(normalized_payload.get("tool_choice"))
    tool_choice = enforce_declared_tool_choice(tool_choice, tool_names)
    return StandardRequest(
        prompt=prompt,
        response_model=model_name,
        resolved_model=resolve_model(model_name),
        surface="anthropic",
        client_profile=CLAUDE_CODE_OPENAI_PROFILE,
        stream=req_data.get("stream", False),
        tools=tools,
        tool_names=tool_names,
        tool_name_registry=build_tool_name_registry(tool_names),
        tool_catalog=normalized_request.tool_catalog,
        tool_enabled=prompt_result.tool_enabled,
        tool_choice_mode=tool_choice.mode,
        required_tool_name=tool_choice.required_tool_name,
        tool_choice_raw=tool_choice.raw,
    )


def _anthropic_usage(prompt: str, answer_text: str, *, extra_prompt_tokens: int = 0) -> dict[str, int]:
    return {"input_tokens": count_tokens(prompt) + max(0, int(extra_prompt_tokens or 0)), "output_tokens": count_tokens(answer_text)}


def _message_start_event(msg_id: str, model_name: str, prompt: str, answer_text: str, *, extra_prompt_tokens: int = 0) -> str:
    return stream_presenter.anthropic_message_start(msg_id, model_name, _anthropic_usage(prompt, answer_text, extra_prompt_tokens=extra_prompt_tokens))


def _visible_answer_text_length(*, directive, execution, stream_state: object | None = None) -> int:
    if directive.stop_reason == "tool_use":
        return 0
    return count_tokens(execution.state.answer_text)


async def _add_used_tokens_for_prompt(*, users_db, token: str, prompt_text: str, answer_text_length: int, extra_prompt_tokens: int = 0) -> None:
    users = await users_db.get()
    for user in users:
        if user["id"] == token:
            user["used_tokens"] += answer_text_length + count_tokens(prompt_text) + max(0, int(extra_prompt_tokens or 0))
            break
    await users_db.save(users)


async def _reacquire_bound_account_if_needed(*, client: QwenClient, standard_request: StandardRequest) -> None:
    preferred_email = getattr(standard_request, "bound_account_email", None)
    if preferred_email:
        standard_request.bound_account = await client.account_pool.acquire_wait_preferred(preferred_email, timeout=60)
    else:
        standard_request.bound_account = None


@router.post("/messages/count_tokens")
@router.post("/v1/messages/count_tokens")
@router.post("/anthropic/v1/messages/count_tokens")
async def anthropic_count_tokens(request: Request):
    try:
        req_data = await request.json()
    except Exception:
        raise HTTPException(400, {"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}})
    prompt_result = messages_to_prompt(req_data, client_profile=CLAUDE_CODE_OPENAI_PROFILE)
    return JSONResponse({"input_tokens": count_tokens(prompt_result.prompt)})


@router.post("/messages")
@router.post("/v1/messages")
@router.post("/anthropic/v1/messages")
async def anthropic_messages(request: Request):
    app = request.app
    users_db = app.state.users_db
    client: QwenClient = app.state.qwen_client

    auth = await resolve_auth_context(request, users_db)
    token = auth.token

    try:
        req_data = await request.json()
    except Exception:
        raise HTTPException(400, {"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}})

    session_key = derive_session_key("anthropic", token, req_data)
    original_history_messages = req_data.get("messages", [])

    async def prepare_locked_request(payload: dict) -> tuple[StandardRequest, dict, str, str, str, str]:
        file_store = getattr(app.state, "file_store", None)
        preprocessed = None
        working_payload = payload
        if file_store is not None:
            preprocessed = await preprocess_attachments(working_payload, file_store, owner_token=token)
            working_payload = preprocessed.payload
        context_prepared = await prepare_context_attachments(
            app=app,
            payload=working_payload,
            surface="anthropic",
            auth_token=token,
            client_profile=CLAUDE_CODE_OPENAI_PROFILE,
            existing_attachments=(preprocessed.attachments if preprocessed is not None else None),
        )
        working_payload = context_prepared["payload"]
        standard_request = _build_standard_request(working_payload)
        if preprocessed is not None:
            standard_request.attachments = preprocessed.attachments
            standard_request.uploaded_file_ids = preprocessed.uploaded_file_ids
        standard_request.upstream_files = context_prepared["upstream_files"]
        standard_request.session_key = context_prepared["session_key"]
        standard_request.context_mode = context_prepared["context_mode"]
        standard_request.context_attachment_tokens = context_prepared.get("context_attachment_tokens", 0)
        standard_request.bound_account_email = context_prepared["bound_account_email"]
        standard_request.bound_account = context_prepared["bound_account"]

        session_plan = await plan_persistent_session_turn(app=app, request=standard_request, payload=working_payload, surface="anthropic")
        if session_plan.enabled:
            standard_request.persistent_session = True
            standard_request.full_prompt = session_plan.full_prompt
            standard_request.prompt = session_plan.prompt
            standard_request.session_message_hashes = session_plan.current_hashes
            standard_request.upstream_chat_id = session_plan.existing_chat_id if session_plan.reuse_chat else None
            if standard_request.bound_account is None and session_plan.account_email:
                standard_request.bound_account = await app.state.account_pool.acquire_wait_preferred(session_plan.account_email, timeout=60)
                if standard_request.bound_account is not None:
                    standard_request.bound_account_email = standard_request.bound_account.email
            elif standard_request.bound_account is not None and not standard_request.bound_account_email:
                standard_request.bound_account_email = standard_request.bound_account.email
            if standard_request.upstream_chat_id and standard_request.bound_account is None:
                log_session_plan_reuse_cancelled(
                    request=standard_request,
                    planned_chat_id=session_plan.existing_chat_id,
                    reason="missing_bound_account",
                )
                standard_request.upstream_chat_id = None
                standard_request.prompt = standard_request.full_prompt or standard_request.prompt

        model_name = standard_request.response_model
        qwen_model = standard_request.resolved_model
        prompt = standard_request.prompt
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"
        return standard_request, working_payload, model_name, qwen_model, prompt, msg_id

    with request_context(req_id=new_request_id(), surface="anthropic", requested_model=req_data.get("model", "claude-3-5-sonnet"), resolved_model="-"):
        if request.headers.get("x-debug-session-key"):
            pass

        if req_data.get("stream", False):
            async def generate():
                queue: asyncio.Queue[str | None] = asyncio.Queue()

                # Mutable state for SSE block tracking across on_delta calls
                sse_state = {
                    "started": False,
                    "block_type": None,
                    "block_index": -1,
                    "opened_tool_ids": set(),
                    "msg_id": None,
                    "model_name": None,
                    "current_prompt": None,
                    "standard_request": None,
                    "sieve": None,
                }

                async def _emit_safe_text(text: str) -> None:
                    """Send safe (DSML-free) text as an anthropic text_delta."""
                    if not text:
                        return
                    if sse_state["block_type"] != "text":
                        if sse_state["block_type"] is not None:
                            await queue.put(stream_presenter.anthropic_content_block_stop(sse_state["block_index"]))
                        sse_state["block_type"] = "text"
                        sse_state["block_index"] += 1
                        await queue.put(stream_presenter.anthropic_content_block_start(
                            sse_state["block_index"], {"type": "text", "text": ""},
                        ))
                    await queue.put(stream_presenter.anthropic_content_block_delta(
                        sse_state["block_index"], {"type": "text_delta", "text": text},
                    ))

                async def on_delta(evt: dict[str, Any], text_chunk: str | None, _tool_calls: list[dict[str, Any]] | None) -> None:
                    phase = None
                    if evt is not None:
                        phase = evt.get("phase")
                    if text_chunk is None:
                        return

                    # Send message_start on first event
                    if not sse_state["started"]:
                        await queue.put(stream_presenter.anthropic_message_start(
                            sse_state["msg_id"], sse_state["model_name"],
                            _anthropic_usage(sse_state["current_prompt"], "", extra_prompt_tokens=sse_state["standard_request"].context_attachment_tokens),
                        ))
                        sse_state["started"] = True

                    # Handle thinking blocks (not filtered by sieve)
                    if text_chunk and phase in ("think", "thinking_summary"):
                        if sse_state["block_type"] != "thinking":
                            if sse_state["block_type"] is not None:
                                await queue.put(stream_presenter.anthropic_content_block_stop(sse_state["block_index"]))
                            sse_state["block_type"] = "thinking"
                            sse_state["block_index"] += 1
                            await queue.put(stream_presenter.anthropic_content_block_start(
                                sse_state["block_index"], {"type": "thinking", "thinking": ""},
                            ))
                        await queue.put(stream_presenter.anthropic_content_block_delta(
                            sse_state["block_index"], {"type": "thinking_delta", "thinking": text_chunk},
                        ))
                        return

                    # Handle answer text — route through ToolStreamSieve to strip DSML
                    if text_chunk and phase == "answer":
                        sieve = sse_state["sieve"]
                        if sieve is not None:
                            sieve_events = sieve.process_chunk(text_chunk)
                            for se in sieve_events or []:
                                if not isinstance(se, dict):
                                    continue
                                if se.get("type") == "content":
                                    safe = se.get("text", "")
                                    if safe:
                                        await _emit_safe_text(safe)
                                # tool_calls from sieve are handled after completion
                                # via build_tool_directive, so we skip them here.
                        else:
                            await _emit_safe_text(text_chunk)
                        return

                    # Handle flush text (evt may have phase "answer" or be None)
                    if text_chunk and phase is None:
                        sieve = sse_state["sieve"]
                        if sieve is not None:
                            sieve_events = sieve.process_chunk(text_chunk)
                            for se in sieve_events or []:
                                if not isinstance(se, dict):
                                    continue
                                if se.get("type") == "content":
                                    safe = se.get("text", "")
                                    if safe:
                                        await _emit_safe_text(safe)
                        else:
                            await _emit_safe_text(text_chunk)
                        return

                    # Handle tool_call blocks (from native tool call events)
                    if phase == "tool_call":
                        extra = evt.get("extra", {}) or {}
                        tool_call_id = extra.get("tool_call_id")
                        if tool_call_id is None:
                            tool_call_id = f"tc_idx_{extra.get('index', 0)}"
                        tool_name = extra.get("tool_name")
                        if not tool_name:
                            return
                        visible_name = _client_visible_tool_name(str(tool_name), sse_state["standard_request"].tool_catalog)
                        tc_id_str = str(tool_call_id)

                        if sse_state["block_type"] != "tool_use" or tc_id_str not in sse_state["opened_tool_ids"]:
                            if sse_state["block_type"] is not None:
                                await queue.put(stream_presenter.anthropic_content_block_stop(sse_state["block_index"]))
                            sse_state["block_type"] = "tool_use"
                            sse_state["block_index"] += 1
                            await queue.put(
                                f"event: content_block_start\n"
                                f"data: {json.dumps({'type': 'content_block_start', 'index': sse_state['block_index'], 'content_block': {'type': 'tool_use', 'id': tc_id_str, 'name': visible_name, 'input': {}}}, ensure_ascii=False)}\n\n"
                            )
                            sse_state["opened_tool_ids"].add(tc_id_str)

                        partial = evt.get("content", "")
                        if partial:
                            await queue.put(stream_presenter.anthropic_content_block_delta(
                                sse_state["block_index"], {"type": "input_json_delta", "partial_json": partial},
                            ))
                        return

                async def runner():
                    try:
                        async with app.state.session_locks.hold(session_key):
                            standard_request, effective_payload, model_name, qwen_model, prompt, msg_id = await prepare_locked_request(req_data)
                            # Set shared state for on_delta
                            sse_state["standard_request"] = standard_request
                            sse_state["model_name"] = model_name
                            sse_state["msg_id"] = msg_id
                            sse_state["current_prompt"] = prompt
                            # Create a ToolStreamSieve (always new version) to strip DSML
                            # markup from streamed answer text.  This is the second line
                            # of defence — the sieve inside execution.py may use the old
                            # ToolSieve when TOOLCORE_V2_ENABLED is False.
                            if standard_request.tool_names:
                                sse_state["sieve"] = ToolStreamSieve(standard_request.tool_names)
                            else:
                                sse_state["sieve"] = None
                            update_request_context(requested_model=model_name, resolved_model=qwen_model)
                            log.info(f"[ANT] model={qwen_model}, stream={standard_request.stream}, tool_enabled={standard_request.tool_enabled}, tools={[t.get('name') for t in standard_request.tools]}, prompt_len={len(prompt)}")
                            current_prompt = prompt
                            history_messages = original_history_messages
                            max_attempts = request_max_attempts(standard_request)

                            for stream_attempt in range(max_attempts):
                                update_request_context(stream_attempt=stream_attempt + 1)

                                # Reset SSE state for each attempt
                                sse_state["started"] = False
                                sse_state["block_type"] = None
                                sse_state["block_index"] = -1
                                sse_state["opened_tool_ids"] = set()
                                if standard_request.tool_names:
                                    sse_state["sieve"] = ToolStreamSieve(standard_request.tool_names)
                                else:
                                    sse_state["sieve"] = None

                                execution = await collect_completion_run(
                                    client, standard_request, current_prompt,
                                    capture_events=False, on_delta=on_delta,
                                )

                                # Flush any remaining safe text from the sieve
                                sieve = sse_state["sieve"]
                                if sieve is not None:
                                    for fe in sieve.flush() or []:
                                        if isinstance(fe, dict) and fe.get("type") == "content":
                                            safe = fe.get("text", "")
                                            if safe:
                                                await _emit_safe_text(safe)

                                retry = evaluate_retry_directive(
                                    request=standard_request, current_prompt=current_prompt,
                                    history_messages=history_messages, attempt_index=stream_attempt,
                                    max_attempts=max_attempts, state=execution.state,
                                    allow_after_visible_output=True,
                                )
                                if retry.retry:
                                    reused_persistent_chat = bool(standard_request.persistent_session and standard_request.upstream_chat_id)
                                    preserve_chat = reused_persistent_chat
                                    await cleanup_runtime_resources(client, execution.acc, execution.chat_id, preserve_chat=preserve_chat)
                                    if reused_persistent_chat:
                                        next_prompt = build_retry_rebase_prompt(standard_request, reason=retry.reason)
                                    else:
                                        next_prompt = retry.next_prompt
                                    await _reacquire_bound_account_if_needed(client=client, standard_request=standard_request)
                                    # Restart the loop with new prompt
                                    current_prompt = next_prompt
                                    sse_state["current_prompt"] = next_prompt
                                    continue

                                # Close last open content block
                                if sse_state["block_type"] is not None:
                                    await queue.put(stream_presenter.anthropic_content_block_stop(sse_state["block_index"]))

                                # Build directive and handle post-completion tool blocks
                                directive = build_tool_directive(standard_request, execution.state)
                                expected_tool_ids = {
                                    block.get("id") for block in directive.tool_blocks
                                    if block.get("type") == "tool_use" and block.get("id")
                                }
                                for block in directive.tool_blocks:
                                    if block.get("type") != "tool_use":
                                        continue
                                    tool_id = block.get("id")
                                    if tool_id in sse_state["opened_tool_ids"]:
                                        continue
                                    sse_state["block_index"] += 1
                                    visible_name = _client_visible_tool_name(str(block.get("name", "")), standard_request.tool_catalog)
                                    await queue.put(
                                        f"event: content_block_start\n"
                                        f"data: {json.dumps({'type': 'content_block_start', 'index': sse_state['block_index'], 'content_block': {'type': 'tool_use', 'id': str(tool_id), 'name': visible_name, 'input': {}}}, ensure_ascii=False)}\n\n"
                                    )
                                    await queue.put(stream_presenter.anthropic_content_block_delta(
                                        sse_state["block_index"],
                                        {"type": "input_json_delta", "partial_json": json.dumps(block.get("input", {}), ensure_ascii=False)},
                                    ))
                                    await queue.put(stream_presenter.anthropic_content_block_stop(sse_state["block_index"]))
                                    sse_state["opened_tool_ids"].add(str(tool_id))

                                # Send message_delta and message_stop
                                visible_answer_length = _visible_answer_text_length(
                                    directive=directive, execution=execution,
                                )
                                stop_reason = "tool_use" if expected_tool_ids else "end_turn"
                                await queue.put(stream_presenter.anthropic_message_delta(stop_reason, visible_answer_length))
                                await queue.put(stream_presenter.anthropic_message_stop())

                                # Post-processing: stats, persistence, cleanup
                                await _add_used_tokens_for_prompt(
                                    users_db=users_db, token=token,
                                    prompt_text=current_prompt,
                                    answer_text_length=count_tokens(execution.state.answer_text),
                                    extra_prompt_tokens=standard_request.context_attachment_tokens,
                                )
                                _usage = calculate_usage(
                                    current_prompt, execution.state.answer_text,
                                    getattr(execution.state, "tool_calls", []),
                                    extra_prompt_tokens=getattr(standard_request, "context_attachment_tokens", 0),
                                )
                                _model_name_resolved = getattr(standard_request, "resolved_model", None) or getattr(standard_request, "response_model", "unknown")
                                _fire_and_forget_stats(client, execution, _usage, _model_name_resolved, api_key=token, api_key_manager=getattr(app.state, "api_key_manager", None))
                                assistant_message = build_anthropic_assistant_history_message(
                                    execution=execution, request=standard_request, directive=directive,
                                )
                                await persist_session_turn(
                                    app=app, request=standard_request, surface="anthropic",
                                    execution=execution, assistant_message=assistant_message,
                                )
                                await cleanup_runtime_resources(
                                    client, execution.acc, execution.chat_id,
                                    preserve_chat=bool(standard_request.persistent_session),
                                )
                                return
                    except HTTPException as he:
                        await clear_invalidated_session_chat(app=app, request=standard_request)
                        await queue.put(
                            f"event: error\n"
                            f"data: {json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': he.detail}}, ensure_ascii=False)}\n\n"
                        )
                    except Exception as e:
                        await clear_invalidated_session_chat(app=app, request=standard_request)
                        await queue.put(
                            f"event: error\n"
                            f"data: {json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': str(e)}}, ensure_ascii=False)}\n\n"
                        )
                    finally:
                        await queue.put(None)

                task = asyncio.create_task(runner())
                while True:
                    chunk = await queue.get()
                    if chunk is None:
                        break
                    yield chunk
                await task

            return StreamingResponse(
                generate(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        async with app.state.session_locks.hold(session_key):
            standard_request, effective_payload, model_name, qwen_model, prompt, msg_id = await prepare_locked_request(req_data)
            update_request_context(requested_model=model_name, resolved_model=qwen_model)
            log.info(f"[ANT] model={qwen_model}, stream={standard_request.stream}, tool_enabled={standard_request.tool_enabled}, tools={[t.get('name') for t in standard_request.tools]}, prompt_len={len(prompt)}")
            history_messages = original_history_messages
            try:
                result = await run_retryable_completion_bridge(
                    client=client,
                    standard_request=standard_request,
                    prompt=prompt,
                    users_db=users_db,
                    token=token,
                    api_key_manager=getattr(app.state, "api_key_manager", None),
                    history_messages=history_messages,
                    max_attempts=request_max_attempts(standard_request),
                    allow_after_visible_output=True,
                )
                execution = result.execution
                directive = result.directive or build_tool_directive(standard_request, execution.state)
                assistant_message = build_anthropic_assistant_history_message(
                    execution=execution,
                    request=standard_request,
                    directive=directive,
                )
                await persist_session_turn(
                    app=app,
                    request=standard_request,
                    surface="anthropic",
                    execution=execution,
                    assistant_message=assistant_message,
                )
                return JSONResponse(
                    build_anthropic_message_payload(
                        msg_id=msg_id,
                        model_name=model_name,
                        prompt=result.prompt,
                        execution=execution,
                        standard_request=standard_request,
                    )
                )
            except Exception as e:
                await clear_invalidated_session_chat(app=app, request=standard_request)
                raise HTTPException(status_code=500, detail=str(e))
