import json
import types
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.responses import StreamingResponse

from backend.adapter.standard_request import StandardRequest
from backend.api import v1_chat
from backend.runtime.execution import normalize_streamed_tool_calls
from backend.services.openai_stream_translator import OpenAIStreamTranslator


class _DummyLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        del exc_type, exc, tb


class _DummyLocks:
    def hold(self, session_key):
        del session_key
        return _DummyLock()


class _FakeTranslator:
    def __init__(self, **kwargs):
        del kwargs
        self.pending_chunks: list[str] = []

    def on_delta(self, evt, text_chunk, tool_calls):
        del evt, tool_calls
        if text_chunk:
            self.pending_chunks.append(f"data: {text_chunk}\n\n")

    def finalize(self, finish_reason, *, usage=None):
        return [f"data: FINAL-{finish_reason}-{usage['total_tokens'] if usage else 0}\n\n"]


class _FakeRequest:
    def __init__(self, app, payload):
        self.app = app
        self._payload = payload
        self.headers = {}
        self.client = types.SimpleNamespace(host="127.0.0.1")

    async def json(self):
        return self._payload


class V1ChatStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_streaming_response_yields_delta_before_finalize(self) -> None:
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                users_db=object(),
                qwen_client=object(),
                file_store=None,
                session_locks=_DummyLocks(),
                account_pool=types.SimpleNamespace(acquire_wait_preferred=AsyncMock(return_value=None)),
            )
        )
        request = _FakeRequest(app, {"messages": [{"role": "user", "content": "hi"}], "stream": True})
        standard_request = StandardRequest(
            prompt="hi",
            response_model="gpt-4.1",
            resolved_model="qwen3.6-plus",
            surface="openai",
            stream=True,
            client_profile="openclaw_openai",
            tool_names=[],
            tools=[],
        )

        async def fake_bridge(**kwargs):
            on_delta = kwargs["on_delta"]
            await on_delta({"phase": "answer"}, "chunk-1", None)
            return types.SimpleNamespace(
                execution=types.SimpleNamespace(state=types.SimpleNamespace(finish_reason="stop")),
                directive=None,
                usage={"prompt_tokens": 2, "completion_tokens": 5, "total_tokens": 7},
            )

        chunks = []
        with patch.object(v1_chat, "resolve_auth_context", AsyncMock(return_value=types.SimpleNamespace(token="tok"))), \
             patch.object(v1_chat, "derive_session_key", return_value="session"), \
             patch.object(v1_chat, "prepare_context_attachments", AsyncMock(return_value={"payload": request._payload, "upstream_files": [], "session_key": "session", "context_mode": "inline", "bound_account_email": None, "bound_account": None})), \
             patch.object(v1_chat, "_build_standard_request", return_value=standard_request), \
             patch.object(v1_chat, "plan_persistent_session_turn", AsyncMock(return_value=types.SimpleNamespace(enabled=False))), \
             patch.object(v1_chat, "OpenAIStreamTranslator", _FakeTranslator), \
             patch.object(v1_chat, "run_retryable_completion_bridge", new=fake_bridge), \
             patch.object(v1_chat, "build_tool_directive", return_value=types.SimpleNamespace(stop_reason="end_turn")), \
             patch.object(v1_chat, "build_openai_assistant_history_message", return_value={"role": "assistant", "content": "done"}), \
             patch.object(v1_chat, "persist_session_turn", AsyncMock()), \
             patch.object(v1_chat, "clear_invalidated_session_chat", AsyncMock()), \
             patch.object(v1_chat, "update_request_context"):
            response = await v1_chat.chat_completions(request)
            self.assertIsInstance(response, StreamingResponse)
            async for chunk in response.body_iterator:
                chunks.append(chunk)

        self.assertEqual(chunks[0], "data: chunk-1\n\n")
        self.assertEqual(chunks[-1], "data: FINAL-stop-7\n\n")

    async def test_streaming_response_does_not_leak_cross_chunk_tool_prefix(self) -> None:
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                users_db=object(),
                qwen_client=object(),
                file_store=None,
                session_locks=_DummyLocks(),
                account_pool=types.SimpleNamespace(acquire_wait_preferred=AsyncMock(return_value=None)),
            )
        )
        request = _FakeRequest(app, {"messages": [{"role": "user", "content": "hi"}], "stream": True})
        standard_request = StandardRequest(
            prompt="hi",
            response_model="gpt-4.1",
            resolved_model="qwen3.6-plus",
            surface="openai",
            stream=True,
            client_profile="openclaw_openai",
            tool_names=["Read"],
            tools=[{"name": "Read", "parameters": {}}],
            tool_enabled=True,
        )
        directive = types.SimpleNamespace(
            stop_reason="tool_use",
            tool_blocks=[{"type": "tool_use", "id": "call_1", "name": "Read", "input": {"file_path": "README.md"}}],
        )

        async def fake_bridge(**kwargs):
            on_attempt_start = kwargs["on_attempt_start"]
            on_delta = kwargs["on_delta"]
            await on_attempt_start(0, "prompt")
            await on_delta({"phase": "answer"}, "##TOOL_C", None)
            await on_delta({"phase": "answer"}, 'ALL##\n{"name": "Read", "input": {"file_path": "README.md"}}\n##END_CALL##', None)
            return types.SimpleNamespace(
                execution=types.SimpleNamespace(state=types.SimpleNamespace(finish_reason="stop")),
                directive=directive,
            )

        chunks = []
        with patch.object(v1_chat, "resolve_auth_context", AsyncMock(return_value=types.SimpleNamespace(token="tok"))), \
             patch.object(v1_chat, "derive_session_key", return_value="session"), \
             patch.object(v1_chat, "prepare_context_attachments", AsyncMock(return_value={"payload": request._payload, "upstream_files": [], "session_key": "session", "context_mode": "inline", "bound_account_email": None, "bound_account": None})), \
             patch.object(v1_chat, "_build_standard_request", return_value=standard_request), \
             patch.object(v1_chat, "plan_persistent_session_turn", AsyncMock(return_value=types.SimpleNamespace(enabled=False))), \
             patch.object(v1_chat, "run_retryable_completion_bridge", new=fake_bridge), \
             patch.object(v1_chat, "build_tool_directive", return_value=directive), \
             patch.object(v1_chat, "build_openai_assistant_history_message", return_value={"role": "assistant", "content": None, "tool_calls": []}), \
             patch.object(v1_chat, "persist_session_turn", AsyncMock()), \
             patch.object(v1_chat, "clear_invalidated_session_chat", AsyncMock()), \
             patch.object(v1_chat, "update_request_context"):
            response = await v1_chat.chat_completions(request)
            self.assertIsInstance(response, StreamingResponse)
            async for chunk in response.body_iterator:
                chunks.append(chunk)

        joined = "".join(chunks)
        self.assertNotIn("##TOOL_C", joined)
        self.assertIn('"tool_calls"', joined)

    async def test_streaming_tool_call_alias_is_normalized_before_emission(self) -> None:
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                users_db=object(),
                qwen_client=object(),
                file_store=None,
                session_locks=_DummyLocks(),
                account_pool=types.SimpleNamespace(acquire_wait_preferred=AsyncMock(return_value=None)),
            )
        )
        request = _FakeRequest(app, {"messages": [{"role": "user", "content": "hi"}], "stream": True})
        standard_request = StandardRequest(
            prompt="hi",
            response_model="gpt-4.1",
            resolved_model="qwen3.6-plus",
            surface="openai",
            stream=True,
            client_profile="openclaw_openai",
            tool_names=["Bash"],
            tools=[{"name": "Bash", "parameters": {}}],
            tool_enabled=True,
        )
        directive = types.SimpleNamespace(stop_reason="tool_use", tool_blocks=[{"type": "tool_use", "id": "call_1", "name": "Bash", "input": {"command": "echo hi"}}])

        async def fake_bridge(**kwargs):
            on_attempt_start = kwargs["on_attempt_start"]
            on_delta = kwargs["on_delta"]
            await on_attempt_start(0, "prompt")
            normalized_calls = normalize_streamed_tool_calls(
                [{"id": "call_1", "name": "exec", "input": {"command": "echo hi"}}],
                standard_request.tool_names,
            )
            await on_delta({"phase": "tool_call"}, None, normalized_calls)
            return types.SimpleNamespace(
                execution=types.SimpleNamespace(state=types.SimpleNamespace(finish_reason="tool_calls", answer_text="", reasoning_text="", tool_calls=[{"id": "call_1", "name": "exec", "input": {"command": "echo hi"}}])),
                directive=directive,
            )

        chunks = []
        with patch.object(v1_chat, "resolve_auth_context", AsyncMock(return_value=types.SimpleNamespace(token="tok"))), \
             patch.object(v1_chat, "derive_session_key", return_value="session"), \
             patch.object(v1_chat, "prepare_context_attachments", AsyncMock(return_value={"payload": request._payload, "upstream_files": [], "session_key": "session", "context_mode": "inline", "bound_account_email": None, "bound_account": None})), \
             patch.object(v1_chat, "_build_standard_request", return_value=standard_request), \
             patch.object(v1_chat, "plan_persistent_session_turn", AsyncMock(return_value=types.SimpleNamespace(enabled=False))), \
             patch.object(v1_chat, "OpenAIStreamTranslator", OpenAIStreamTranslator), \
             patch.object(v1_chat, "run_retryable_completion_bridge", new=fake_bridge), \
             patch.object(v1_chat, "build_tool_directive", return_value=directive), \
             patch.object(v1_chat, "build_openai_assistant_history_message", return_value={"role": "assistant", "content": None, "tool_calls": []}), \
             patch.object(v1_chat, "persist_session_turn", AsyncMock()), \
             patch.object(v1_chat, "clear_invalidated_session_chat", AsyncMock()), \
             patch.object(v1_chat, "update_request_context"):
            response = await v1_chat.chat_completions(request)
            self.assertIsInstance(response, StreamingResponse)
            async for chunk in response.body_iterator:
                chunks.append(chunk)

        joined = "".join(chunks)
        self.assertIn('"name": "exec"', joined)
        self.assertNotIn('"name": "Bash"', joined)

    async def test_streaming_retry_does_not_leak_failed_attempt_text(self) -> None:
        app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                users_db=object(),
                qwen_client=object(),
                file_store=None,
                session_locks=_DummyLocks(),
                account_pool=types.SimpleNamespace(acquire_wait_preferred=AsyncMock(return_value=None)),
            )
        )
        request = _FakeRequest(app, {"messages": [{"role": "user", "content": "hi"}], "stream": True})
        standard_request = StandardRequest(
            prompt="hi",
            response_model="gpt-4.1",
            resolved_model="qwen3.6-plus",
            surface="openai",
            stream=True,
            client_profile="openclaw_openai",
            tool_names=["exec"],
            tools=[{"name": "exec", "parameters": {}}],
            tool_enabled=True,
        )
        directive = types.SimpleNamespace(stop_reason="tool_use", tool_blocks=[{"type": "tool_use", "id": "call_1", "name": "exec", "input": {"command": "echo hi"}}])

        async def fake_bridge(**kwargs):
            on_attempt_start = kwargs["on_attempt_start"]
            on_delta = kwargs["on_delta"]
            on_retry = kwargs["on_retry"]

            await on_attempt_start(0, "prompt")
            await on_delta({"phase": "answer"}, "Tool exec does not exists.", None)
            await on_retry(0, types.SimpleNamespace(reason="blocked_tool_name:exec"), types.SimpleNamespace())

            await on_attempt_start(1, "prompt")
            await on_delta({"phase": "tool_call"}, None, [{"id": "call_1", "name": "exec", "input": {"command": "echo hi"}}])
            return types.SimpleNamespace(
                execution=types.SimpleNamespace(state=types.SimpleNamespace(finish_reason="tool_calls", answer_text="", reasoning_text="", tool_calls=[{"id": "call_1", "name": "exec", "input": {"command": "echo hi"}}])),
                directive=directive,
            )

        chunks = []
        with patch.object(v1_chat, "resolve_auth_context", AsyncMock(return_value=types.SimpleNamespace(token="tok"))), \
             patch.object(v1_chat, "derive_session_key", return_value="session"), \
             patch.object(v1_chat, "prepare_context_attachments", AsyncMock(return_value={"payload": request._payload, "upstream_files": [], "session_key": "session", "context_mode": "inline", "bound_account_email": None, "bound_account": None})), \
             patch.object(v1_chat, "_build_standard_request", return_value=standard_request), \
             patch.object(v1_chat, "plan_persistent_session_turn", AsyncMock(return_value=types.SimpleNamespace(enabled=False))), \
             patch.object(v1_chat, "run_retryable_completion_bridge", new=fake_bridge), \
             patch.object(v1_chat, "build_tool_directive", return_value=directive), \
             patch.object(v1_chat, "build_openai_assistant_history_message", return_value={"role": "assistant", "content": None, "tool_calls": []}), \
             patch.object(v1_chat, "persist_session_turn", AsyncMock()), \
             patch.object(v1_chat, "clear_invalidated_session_chat", AsyncMock()), \
             patch.object(v1_chat, "update_request_context"):
            response = await v1_chat.chat_completions(request)
            self.assertIsInstance(response, StreamingResponse)
            async for chunk in response.body_iterator:
                chunks.append(chunk)

        joined = "".join(chunks)
        self.assertNotIn("Tool exec does not exists.", joined)
        self.assertIn('"tool_calls"', joined)


if __name__ == "__main__":
    unittest.main()
