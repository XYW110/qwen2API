import unittest
from types import SimpleNamespace

from backend.adapter.standard_request import StandardRequest
from backend.services.responses_compat import ResponsesStreamTranslator, sse_chunk_to_payload
from backend.toolcore.tool_catalog import ToolCatalog
from backend.toolcore.types import ToolDefinition


class ResponsesStreamTranslatorTests(unittest.TestCase):
    def test_finalize_splits_function_call_argument_deltas(self) -> None:
        translator = ResponsesStreamTranslator(response_id="resp_1", created=1, model_name="gpt-4.1")
        response_payload = {
            "output": [
                {
                    "id": "call_1",
                    "type": "function_call",
                    "status": "completed",
                    "call_id": "call_1",
                    "name": "Read",
                    "arguments": '{"file_path": "%s"}' % ("a" * 300),
                }
            ]
        }
        request = StandardRequest(
            prompt="prompt",
            response_model="gpt-4.1",
            resolved_model="qwen3.6-plus",
            surface="responses",
            tools=[{"name": "Read", "parameters": {}}],
            tool_names=["Read"],
            tool_enabled=True,
        )
        execution = SimpleNamespace(state=SimpleNamespace(answer_text="", reasoning_text="", tool_calls=[{"id": "call_1", "name": "Read", "input": {"file_path": "a" * 300}}]))

        chunks = translator.finalize(response_payload=response_payload, standard_request=request, execution=execution)
        delta_chunks = [chunk for chunk in chunks if 'response.function_call_arguments.delta' in chunk]

        self.assertGreater(len(delta_chunks), 2)
        self.assertIn('response.function_call_arguments.done', ''.join(chunks))

    def test_streaming_maps_internal_tool_name_back_to_client_name(self) -> None:
        catalog = ToolCatalog([ToolDefinition(name="Read", client_name="Read", model_name="bridge-0")])
        translator = ResponsesStreamTranslator(
            response_id="resp_1",
            created=1,
            model_name="gpt-4.1",
            tool_catalog=catalog,
        )

        translator.on_tool_calls([
            {"id": "call_1", "name": "bridge-0", "input": {"path": "README.md"}},
        ])

        payloads = [sse_chunk_to_payload(chunk) for chunk in translator.pending_chunks]
        items = [payload["item"] for payload in payloads if payload.get("type") == "response.output_item.added"]
        self.assertEqual(items[0]["name"], "Read")
        self.assertNotEqual(items[0]["name"], "bridge-0")

    def test_streaming_does_not_emit_malformed_wrapper_text_when_tool_call_succeeds(self) -> None:
        translator = ResponsesStreamTranslator(response_id="resp_1", created=1, model_name="gpt-4.1")
        malformed_wrapper = '##TOOL_CALL##\n{"name": "exec", "input": {"command": "ls -la /tmp"'
        translator.on_text_delta(malformed_wrapper)
        translator.on_tool_calls([
            {"id": "call_1", "name": "Read", "input": {"path": "README.md"}},
        ])
        response_payload = {
            "output": [
                {
                    "id": "call_1",
                    "type": "function_call",
                    "status": "completed",
                    "call_id": "call_1",
                    "name": "Read",
                    "arguments": '{"path": "README.md"}',
                }
            ]
        }
        request = StandardRequest(
            prompt="prompt",
            response_model="gpt-4.1",
            resolved_model="qwen3.6-plus",
            surface="responses",
            tools=[{"name": "Read", "parameters": {}}],
            tool_names=["Read"],
            tool_enabled=True,
        )
        execution = SimpleNamespace(state=SimpleNamespace(answer_text=malformed_wrapper, reasoning_text="", tool_calls=[{"id": "call_1", "name": "Read", "input": {"path": "README.md"}}]))

        chunks = translator.finalize(response_payload=response_payload, standard_request=request, execution=execution)
        joined = ''.join(chunks)

        self.assertNotIn('##TOOL_CALL##', joined)
        self.assertIn('response.function_call_arguments.done', joined)


if __name__ == "__main__":
    unittest.main()
