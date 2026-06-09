import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from backend.core.config import settings
from backend.core.request_logging import update_request_context
from backend.services.completion_bridge import run_retryable_completion_bridge
from backend.services.openai_stream_translator import OpenAIStreamTranslator
from backend.services.response_formatters import build_openai_completion_payload
from backend.toolcore.task_session import (
    build_openai_assistant_history_message,
    clear_invalidated_session_chat,
    persist_session_turn,
)
from backend.runtime.execution import RuntimeAttemptState, RuntimeToolDirective, build_tool_directive, build_usage_delta_factory, request_max_attempts

log = logging.getLogger("qwen2api.chat")


@dataclass
class OpenAIResponderContext:
    app: Any
    client: Any
    users_db: Any
    token: str
    req_data: dict[str, Any]
    session_key: str
    completion_id: str
    created: int
    req_id: str
    diagnostics: dict[str, Any]
    guard_diagnostics: dict[str, Any]
    model_name: str
    prompt: str
    history_messages: Any
    standard_request: Any
    stream_usage: Callable[[Any, str], dict[str, int]]
    record_repeated_tool_guard: Callable[..., None]
    completion_bridge: Callable[..., Awaitable[Any]] = run_retryable_completion_bridge
    stream_translator_cls: Any = OpenAIStreamTranslator
    build_tool_directive_fn: Callable[..., Any] = build_tool_directive
    build_openai_assistant_history_message_fn: Callable[..., Any] = build_openai_assistant_history_message
    persist_session_turn_fn: Callable[..., Awaitable[Any]] = persist_session_turn
    clear_invalidated_session_chat_fn: Callable[..., Awaitable[Any]] = clear_invalidated_session_chat
    build_openai_completion_payload_fn: Callable[..., dict[str, Any]] = build_openai_completion_payload
    update_request_context_fn: Callable[..., Any] = update_request_context
    singleflight: Any | None = None
    singleflight_owner: bool = False
    singleflight_key: Any | None = None
    openai_content_delta_text: Callable[[str], str | None] | None = None
    is_openai_tool_call_delta_chunk: Callable[[str], bool] | None = None
    filter_staged_chunks_for_tool_calls: Callable[[list[str]], list[str]] | None = None
    log_openai_stream_sse_chunk: Callable[..., None] | None = None
    log_openai_stream_finalize_options: Callable[..., None] | None = None
    log_openai_stream_protocol_diagnostics: Callable[..., None] | None = None
    log_outbound_tool_call_diagnostics: Callable[..., None] | None = None
    stage_directive_tool_calls_if_missing: Callable[..., None] | None = None


class OpenAIStreamResponder:
    def __init__(self, context: OpenAIResponderContext):
        self.ctx = context
        self.translator: OpenAIStreamTranslator | None = None
        self.staged_chunks: list[str] = []
        self.emitted_protocol_chunks: list[str] = []
        self.emitted_tool_call_chunks: list[str] = []
        self.buffered_content_chars = 0
        self.min_stream_content_chars = 80
        self.streamed_content_to_client = False

    def response(self) -> StreamingResponse:
        return StreamingResponse(
            self.generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def generate(self):
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def producer() -> None:
            async with self.ctx.app.state.session_locks.hold(self.ctx.session_key):
                try:
                    self.ctx.update_request_context_fn(stream_attempt=1)
                    if self.ctx.standard_request.tools:
                        await self._produce_tool_stream(queue)
                    else:
                        await self._produce_text_stream(queue)
                except HTTPException as he:
                    await self.ctx.clear_invalidated_session_chat_fn(app=self.ctx.app, request=self.ctx.standard_request)
                    await queue.put(f"data: {json.dumps({'error': he.detail})}\n\n")
                except Exception as e:
                    log.exception(
                        "[OAI] stream_error req_id=%s completion_id=%s prompt_hash=%s error=%s",
                        self.ctx.req_id,
                        self.ctx.completion_id,
                        self.ctx.diagnostics["prompt_hash"],
                        e,
                    )
                    await self.ctx.clear_invalidated_session_chat_fn(app=self.ctx.app, request=self.ctx.standard_request)
                    await queue.put(f"data: {json.dumps({'error': str(e)})}\n\n")
                finally:
                    log.info(
                        "[OAI] stream_producer_done req_id=%s completion_id=%s prompt_hash=%s",
                        self.ctx.req_id,
                        self.ctx.completion_id,
                        self.ctx.diagnostics["prompt_hash"],
                    )
                    await queue.put(None)

        producer_task = asyncio.create_task(producer())
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                if self.ctx.log_openai_stream_sse_chunk is not None:
                    self.ctx.log_openai_stream_sse_chunk(
                        req_id=self.ctx.req_id,
                        completion_id=self.ctx.completion_id,
                        prompt_hash=self.ctx.diagnostics["prompt_hash"],
                        chunk=chunk,
                    )
                yield chunk
        finally:
            if not producer_task.done():
                log.warning(
                    "[OAI] stream_client_disconnect req_id=%s completion_id=%s prompt_hash=%s",
                    self.ctx.req_id,
                    self.ctx.completion_id,
                    self.ctx.diagnostics["prompt_hash"],
                )
                producer_task.cancel()
                try:
                    await producer_task
                except Exception:
                    pass

    def _new_translator(self, *, include_tool_catalog: bool = True) -> OpenAIStreamTranslator:
        kwargs = {
            "completion_id": self.ctx.completion_id,
            "created": self.ctx.created,
            "model_name": self.ctx.model_name,
            "client_profile": self.ctx.standard_request.client_profile,
            "build_final_directive": lambda answer_text: self.ctx.build_tool_directive_fn(
                self.ctx.standard_request,
                RuntimeAttemptState(answer_text=answer_text),
            ),
            "allowed_tool_names": self.ctx.standard_request.tool_names,
            "toolcore_enabled": settings.TOOLCORE_V2_ENABLED,
        }
        if include_tool_catalog:
            kwargs["tool_catalog"] = self.ctx.standard_request.tool_catalog
        return self.ctx.stream_translator_cls(**kwargs)

    def _reset_tool_stream_state(self) -> None:
        self.translator = self._new_translator()
        self.staged_chunks = []
        self.emitted_protocol_chunks = []
        self.emitted_tool_call_chunks = []
        self.buffered_content_chars = 0

    async def _on_tool_attempt_start(self, _attempt_index: int, _attempt_prompt: str) -> None:
        self._reset_tool_stream_state()

    async def _on_tool_retry(self, _attempt_index: int, _retry, _execution) -> None:
        self.staged_chunks = []
        self.emitted_protocol_chunks = []
        self.emitted_tool_call_chunks = []
        self.buffered_content_chars = 0

    async def _on_tool_delta(
        self,
        evt: dict[str, Any],
        text_chunk: str | None,
        tool_calls: list[dict[str, Any]] | None,
        queue: asyncio.Queue[str | None],
    ) -> None:
        if self.translator is None:
            return
        self.translator.on_delta(evt, text_chunk, tool_calls)
        while self.translator.pending_chunks:
            chunk = self.translator.pending_chunks.pop(0)
            content_text = self.ctx.openai_content_delta_text(chunk) if self.ctx.openai_content_delta_text is not None else None
            if content_text is None:
                self.emitted_protocol_chunks.append(chunk)
                if self.ctx.is_openai_tool_call_delta_chunk is not None and self.ctx.is_openai_tool_call_delta_chunk(chunk):
                    self.emitted_tool_call_chunks.append(chunk)
                await queue.put(chunk)
                continue
            if self.streamed_content_to_client:
                await queue.put(chunk)
                continue
            self.staged_chunks.append(chunk)
            self.buffered_content_chars += len(content_text)
            if self.buffered_content_chars >= self.min_stream_content_chars:
                for staged_chunk in self.staged_chunks:
                    await queue.put(staged_chunk)
                self.staged_chunks = []
                self.streamed_content_to_client = True

    async def _produce_tool_stream(self, queue: asyncio.Queue[str | None]) -> None:
        result = await self.ctx.completion_bridge(
            client=self.ctx.client,
            standard_request=self.ctx.standard_request,
            prompt=self.ctx.prompt,
            users_db=self.ctx.users_db,
            token=self.ctx.token,
            api_key_manager=getattr(self.ctx.app.state, "api_key_manager", None),
            history_messages=self.ctx.history_messages,
            max_attempts=request_max_attempts(self.ctx.standard_request),
            usage_delta_factory=build_usage_delta_factory(
                self.ctx.prompt,
                extra_prompt_tokens=self.ctx.standard_request.context_attachment_tokens,
            ),
            allow_after_visible_output=True,
            capture_events=False,
            on_delta=lambda evt, text_chunk, tool_calls: self._on_tool_delta(evt, text_chunk, tool_calls, queue),
            on_attempt_start=self._on_tool_attempt_start,
            on_retry=self._on_tool_retry,
        )
        execution = result.execution
        directive = result.directive or self.ctx.build_tool_directive_fn(self.ctx.standard_request, execution.state)
        if self.streamed_content_to_client and directive.stop_reason == "tool_use":
            log.warning(
                "[OAI] suppress_tool_calls_after_streamed_content req_id=%s completion_id=%s prompt_hash=%s",
                self.ctx.req_id,
                self.ctx.completion_id,
                self.ctx.diagnostics["prompt_hash"],
            )
            directive = RuntimeToolDirective(
                tool_blocks=[{"type": "text", "text": execution.state.answer_text or ""}],
                stop_reason="end_turn",
            )
        await self._persist_and_finalize_stream_result(
            queue=queue,
            result=result,
            execution=execution,
            directive=directive,
            staged_chunk_count=len(self.staged_chunks),
            tool_stream=True,
        )

    async def _produce_text_stream(self, queue: asyncio.Queue[str | None]) -> None:
        self.translator = self._new_translator(include_tool_catalog=False)

        async def on_delta(evt: dict[str, Any], text_chunk: str | None, tool_calls: list[dict[str, Any]] | None) -> None:
            if self.translator is None:
                return
            self.translator.on_delta(evt, text_chunk, tool_calls)
            while self.translator.pending_chunks:
                await queue.put(self.translator.pending_chunks.pop(0))

        result = await self.ctx.completion_bridge(
            client=self.ctx.client,
            standard_request=self.ctx.standard_request,
            prompt=self.ctx.prompt,
            users_db=self.ctx.users_db,
            token=self.ctx.token,
            api_key_manager=getattr(self.ctx.app.state, "api_key_manager", None),
            history_messages=self.ctx.history_messages,
            max_attempts=request_max_attempts(self.ctx.standard_request),
            usage_delta_factory=build_usage_delta_factory(
                self.ctx.prompt,
                extra_prompt_tokens=self.ctx.standard_request.context_attachment_tokens,
            ),
            allow_after_visible_output=True,
            capture_events=False,
            on_delta=on_delta,
        )
        execution = result.execution
        directive = result.directive or self.ctx.build_tool_directive_fn(self.ctx.standard_request, execution.state)
        pending_count = len(self.translator.pending_chunks) if self.translator is not None else 0
        await self._persist_and_finalize_stream_result(
            queue=queue,
            result=result,
            execution=execution,
            directive=directive,
            staged_chunk_count=pending_count,
            tool_stream=False,
        )

    async def _persist_and_finalize_stream_result(
        self,
        *,
        queue: asyncio.Queue[str | None],
        result: Any,
        execution: Any,
        directive: Any,
        staged_chunk_count: int,
        tool_stream: bool,
    ) -> None:
        assistant_message = self.ctx.build_openai_assistant_history_message_fn(
            execution=execution,
            request=self.ctx.standard_request,
            directive=directive,
        )
        await self.ctx.persist_session_turn_fn(
            app=self.ctx.app,
            request=self.ctx.standard_request,
            surface="openai",
            execution=execution,
            assistant_message=assistant_message,
        )
        final_finish_reason = "tool_calls" if directive.stop_reason == "tool_use" else (execution.state.finish_reason or "stop")
        tool_names = [block.get("name") for block in directive.tool_blocks if block.get("type") == "tool_use"]
        self.ctx.record_repeated_tool_guard(
            session_key=self.ctx.standard_request.session_key or self.ctx.session_key,
            diagnostics=self.ctx.guard_diagnostics,
            final_diagnostics=self.ctx.diagnostics,
            tool_names=tool_names,
            finish_reason=final_finish_reason,
        )
        if tool_stream and self.ctx.stage_directive_tool_calls_if_missing is not None:
            self.ctx.stage_directive_tool_calls_if_missing(
                translator=self.translator,
                directive=directive,
                staged_chunks=self.staged_chunks,
            )
            staged_chunk_count = len(self.staged_chunks)
        log.info(
            "[OAI] stream_final req_id=%s completion_id=%s chat_id=%s prompt_hash=%s finish_reason=%s stop_reason=%s tool_names=%s answer_chars=%s staged_chunks=%s",
            self.ctx.req_id,
            self.ctx.completion_id,
            execution.chat_id,
            self.ctx.diagnostics["prompt_hash"],
            final_finish_reason,
            directive.stop_reason,
            tool_names,
            len(execution.state.answer_text or ""),
            staged_chunk_count,
        )
        if tool_stream:
            output_staged_chunks = (
                self.ctx.filter_staged_chunks_for_tool_calls(self.staged_chunks)
                if final_finish_reason == "tool_calls" and self.ctx.filter_staged_chunks_for_tool_calls is not None
                else self.staged_chunks
            )
            if final_finish_reason == "tool_calls" and self.ctx.log_outbound_tool_call_diagnostics is not None:
                self.ctx.log_outbound_tool_call_diagnostics(
                    req_id=self.ctx.req_id,
                    completion_id=self.ctx.completion_id,
                    prompt_hash=self.ctx.diagnostics["prompt_hash"],
                    standard_request=self.ctx.standard_request,
                    chunks=self.emitted_tool_call_chunks + output_staged_chunks,
                )
            for chunk in output_staged_chunks:
                await queue.put(chunk)
        else:
            output_staged_chunks = []
        if self.translator is not None:
            usage = self.ctx.stream_usage(result, self.ctx.prompt)
            if self.ctx.log_openai_stream_finalize_options is not None:
                self.ctx.log_openai_stream_finalize_options(
                    req_id=self.ctx.req_id,
                    completion_id=self.ctx.completion_id,
                    prompt_hash=self.ctx.diagnostics["prompt_hash"],
                    req_data=self.ctx.req_data,
                    finish_reason=final_finish_reason,
                    usage=usage,
                    answer_text=execution.state.answer_text or "",
                )
            final_chunks = self.translator.finalize(final_finish_reason, usage=usage)
            if tool_stream and final_finish_reason == "tool_calls" and self.ctx.log_openai_stream_protocol_diagnostics is not None:
                self.ctx.log_openai_stream_protocol_diagnostics(
                    req_id=self.ctx.req_id,
                    completion_id=self.ctx.completion_id,
                    prompt_hash=self.ctx.diagnostics["prompt_hash"],
                    chunks=self.emitted_protocol_chunks + output_staged_chunks + final_chunks,
                )
            for chunk in final_chunks:
                await queue.put(chunk)


class OpenAISyncResponder:
    def __init__(self, context: OpenAIResponderContext):
        self.ctx = context

    async def response(self) -> JSONResponse:
        try:
            async with self.ctx.app.state.session_locks.hold(self.ctx.session_key):
                self.ctx.update_request_context_fn(stream_attempt=1)
                result = await self.ctx.completion_bridge(
                    client=self.ctx.client,
                    standard_request=self.ctx.standard_request,
                    prompt=self.ctx.prompt,
                    users_db=self.ctx.users_db,
                    token=self.ctx.token,
                    api_key_manager=getattr(self.ctx.app.state, "api_key_manager", None),
                    history_messages=self.ctx.history_messages,
                    max_attempts=request_max_attempts(self.ctx.standard_request),
                    usage_delta_factory=build_usage_delta_factory(
                        self.ctx.prompt,
                        extra_prompt_tokens=self.ctx.standard_request.context_attachment_tokens,
                    ),
                    allow_after_visible_output=True,
                )
                execution = result.execution
                directive = result.directive or self.ctx.build_tool_directive_fn(self.ctx.standard_request, execution.state)
                assistant_message = self.ctx.build_openai_assistant_history_message_fn(
                    execution=execution,
                    request=self.ctx.standard_request,
                    directive=directive,
                )
                await self.ctx.persist_session_turn_fn(
                    app=self.ctx.app,
                    request=self.ctx.standard_request,
                    surface="openai",
                    execution=execution,
                    assistant_message=assistant_message,
                )
                tool_names = [block.get("name") for block in directive.tool_blocks if block.get("type") == "tool_use"]
                final_finish_reason = "tool_calls" if directive.stop_reason == "tool_use" else (execution.state.finish_reason or "stop")
                self.ctx.record_repeated_tool_guard(
                    session_key=self.ctx.standard_request.session_key or self.ctx.session_key,
                    diagnostics=self.ctx.guard_diagnostics,
                    final_diagnostics=self.ctx.diagnostics,
                    tool_names=tool_names,
                    finish_reason=final_finish_reason,
                )
                log.info(
                    "[OAI] json_final req_id=%s completion_id=%s chat_id=%s prompt_hash=%s finish_reason=%s stop_reason=%s tool_names=%s answer_chars=%s",
                    self.ctx.req_id,
                    self.ctx.completion_id,
                    execution.chat_id,
                    self.ctx.diagnostics["prompt_hash"],
                    final_finish_reason,
                    directive.stop_reason,
                    tool_names,
                    len(execution.state.answer_text or ""),
                )

                payload = self.ctx.build_openai_completion_payload_fn(
                    completion_id=self.ctx.completion_id,
                    created=self.ctx.created,
                    model_name=self.ctx.model_name,
                    prompt=result.prompt,
                    execution=execution,
                    standard_request=self.ctx.standard_request,
                )
                if self.ctx.singleflight_owner and self.ctx.singleflight_key is not None and self.ctx.singleflight is not None:
                    await self.ctx.singleflight.complete(self.ctx.singleflight_key, payload)
                return JSONResponse(payload)
        except Exception as e:
            if self.ctx.singleflight_owner and self.ctx.singleflight_key is not None and self.ctx.singleflight is not None:
                await self.ctx.singleflight.fail(self.ctx.singleflight_key, e)
            await self.ctx.clear_invalidated_session_chat_fn(app=self.ctx.app, request=self.ctx.standard_request)
            raise HTTPException(status_code=500, detail=str(e))
