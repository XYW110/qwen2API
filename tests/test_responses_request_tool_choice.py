import unittest
from types import SimpleNamespace

from backend.adapter.standard_request import StandardRequest
from backend.services.response_formatters import build_openai_response_payload
from backend.services.responses_compat import prepare_responses_request
from backend.toolcore.tool_catalog import ToolCatalog
from backend.toolcore.types import ToolDefinition


class _DummyStore:
    async def get(self, _response_id):
        return None


class ResponsesToolChoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_responses_request_preserves_tool_choice(self) -> None:
        prepared = await prepare_responses_request(
            response_store=_DummyStore(),
            req_data={
                "model": "gpt-4.1",
                "input": "inspect file",
                "tools": [{"type": "function", "function": {"name": "Read", "parameters": {}}}],
                "tool_choice": {"type": "function", "function": {"name": "Read"}},
            },
        )

        self.assertEqual(
            prepared.transformed_payload["tool_choice"],
            {"type": "function", "function": {"name": "Read"}},
        )

    async def test_response_payload_echoes_truthful_tool_choice(self) -> None:
        request = StandardRequest(
            prompt="hello",
            response_model="gpt-4.1",
            resolved_model="qwen3.6-plus",
            surface="responses",
            tool_choice_mode="required",
            required_tool_name="Read",
            tool_choice_raw={"type": "function", "function": {"name": "Read"}},
        )
        execution = SimpleNamespace(state=SimpleNamespace(answer_text="done", reasoning_text="", tool_calls=[]))

        payload = build_openai_response_payload(
            response_id="resp_test",
            created=1,
            model_name="gpt-4.1",
            prompt="hello",
            execution=execution,
            standard_request=request,
        )

        self.assertEqual(payload["tool_choice"], {"type": "function", "function": {"name": "Read"}})

    async def test_response_payload_maps_internal_required_tool_name_back_to_client_name(self) -> None:
        request = StandardRequest(
            prompt="hello",
            response_model="gpt-4.1",
            resolved_model="qwen3.6-plus",
            surface="responses",
            tool_choice_mode="required",
            required_tool_name="bridge-0",
            tool_choice_raw={"type": "function", "function": {"name": "Read"}},
            tool_catalog=ToolCatalog([ToolDefinition(name="Read", client_name="Read", model_name="bridge-0")]),
        )
        execution = SimpleNamespace(state=SimpleNamespace(answer_text="done", reasoning_text="", tool_calls=[]))

        payload = build_openai_response_payload(
            response_id="resp_test",
            created=1,
            model_name="gpt-4.1",
            prompt="hello",
            execution=execution,
            standard_request=request,
        )

        self.assertEqual(payload["tool_choice"], {"type": "function", "function": {"name": "Read"}})

    async def test_response_payload_maps_tools_field_back_to_client_names(self) -> None:
        request = StandardRequest(
            prompt="hello",
            response_model="gpt-4.1",
            resolved_model="qwen3.6-plus",
            surface="responses",
            tools=[{"name": "bridge-0", "description": "Read file", "parameters": {}}],
            tool_names=["bridge-0"],
            tool_enabled=True,
            tool_catalog=ToolCatalog([ToolDefinition(name="Read", client_name="Read", model_name="bridge-0")]),
        )
        execution = SimpleNamespace(state=SimpleNamespace(answer_text="done", reasoning_text="", tool_calls=[]))

        payload = build_openai_response_payload(
            response_id="resp_test",
            created=1,
            model_name="gpt-4.1",
            prompt="hello",
            execution=execution,
            standard_request=request,
        )

        self.assertEqual(payload["tools"], [{"name": "Read", "description": "Read file", "parameters": {}}])
        self.assertNotIn("bridge-0", str(payload["tools"]))

    async def test_response_payload_drops_malformed_tool_wrapper_text_when_tool_use_exists(self) -> None:
        request = StandardRequest(
            prompt="hello",
            response_model="gpt-4.1",
            resolved_model="qwen3.6-plus",
            surface="responses",
            tools=[{"name": "Read", "parameters": {}}],
            tool_names=["Read"],
            tool_enabled=True,
        )
        execution = SimpleNamespace(
            state=SimpleNamespace(
                answer_text='##TOOL_CALL##\n{"name": "exec", "input": {"command": "ls -la /tmp"',
                reasoning_text="",
                tool_calls=[{"id": "call_1", "name": "Read", "input": {"path": "README.md"}}],
            )
        )

        payload = build_openai_response_payload(
            response_id="resp_test",
            created=1,
            model_name="gpt-4.1",
            prompt="hello",
            execution=execution,
            standard_request=request,
        )

        self.assertEqual(payload["output_text"], "")
        self.assertEqual(len(payload["output"]), 1)
        self.assertEqual(payload["output"][0]["type"], "function_call")

    async def test_response_payload_drops_blocked_exec_text_when_tool_use_exists(self) -> None:
        request = StandardRequest(
            prompt="hello",
            response_model="gpt-4.1",
            resolved_model="qwen3.6-plus",
            surface="responses",
            tools=[{"name": "Bash", "parameters": {}}],
            tool_names=["Bash"],
            tool_enabled=True,
        )
        execution = SimpleNamespace(
            state=SimpleNamespace(
                answer_text="Tool exec does not exists.",
                reasoning_text="",
                tool_calls=[{"id": "call_1", "name": "exec", "input": {"command": "echo hi"}}],
            )
        )

        payload = build_openai_response_payload(
            response_id="resp_test",
            created=1,
            model_name="gpt-4.1",
            prompt="hello",
            execution=execution,
            standard_request=request,
        )

        self.assertEqual(payload["output_text"], "")
        self.assertEqual(len(payload["output"]), 1)
        self.assertEqual(payload["output"][0]["type"], "message")


if __name__ == "__main__":
    unittest.main()
