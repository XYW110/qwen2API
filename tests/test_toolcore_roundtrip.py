import unittest
from types import SimpleNamespace

from backend.adapter.standard_request import StandardRequest
from backend.services.response_formatters import build_openai_completion_payload, build_openai_response_payload
from backend.services.responses_compat import response_input_item_to_messages
from backend.toolcore.roundtrip import build_response_function_call_item, canonical_tool_result_from_response_output


class ToolCoreRoundtripTests(unittest.TestCase):
    def test_tool_call_emitted_with_stable_call_id(self) -> None:
        item = build_response_function_call_item(call_id="call_123", name="Read", input_data={"file_path": "README.md"})
        self.assertEqual(item["id"], "call_123")
        self.assertEqual(item["call_id"], "call_123")

    def test_function_call_output_maps_back_to_canonical_tool_result(self) -> None:
        result = canonical_tool_result_from_response_output({"call_id": "call_123", "output": "done"})
        self.assertIsNotNone(result)
        self.assertEqual(result.call_id, "call_123")
        self.assertEqual(result.output, "done")

    def test_history_preserves_tool_identity_across_turns(self) -> None:
        messages = response_input_item_to_messages({"type": "function_call_output", "call_id": "call_123", "output": "done"})
        self.assertEqual(messages[0]["role"], "tool")
        self.assertEqual(messages[0]["tool_call_id"], "call_123")

    def test_responses_history_preserves_textual_tool_wrapper_input(self) -> None:
        content = "##TOOL_CALL##\n{\"name\": \"Read\", \"input\": {\"file_path\": \"README.md\"}}\n##END_CALL##"

        messages = response_input_item_to_messages({"type": "message", "role": "assistant", "content": content})

        self.assertEqual(messages, [{"role": "assistant", "content": content}])

    def test_chat_and_responses_expose_equivalent_tool_semantics(self) -> None:
        request = StandardRequest(
            prompt="prompt",
            response_model="gpt-4.1",
            resolved_model="qwen3.6-plus",
            surface="openai",
            tools=[{"name": "Read", "parameters": {}}],
            tool_names=["Read"],
            tool_enabled=True,
        )
        execution = SimpleNamespace(state=SimpleNamespace(answer_text="", reasoning_text="", tool_calls=[{"id": "call_123", "name": "Read", "input": {"file_path": "README.md"}}]))

        completion_payload = build_openai_completion_payload(
            completion_id="chatcmpl_123",
            created=1,
            model_name="gpt-4.1",
            prompt="prompt",
            execution=execution,
            standard_request=request,
        )
        response_payload = build_openai_response_payload(
            response_id="resp_123",
            created=1,
            model_name="gpt-4.1",
            prompt="prompt",
            execution=execution,
            standard_request=request,
        )

        self.assertEqual(completion_payload["choices"][0]["message"]["tool_calls"][0]["id"], "call_123")
        self.assertEqual(response_payload["output"][0]["call_id"], "call_123")


if __name__ == "__main__":
    unittest.main()
