import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.adapter.standard_request import StandardRequest
from backend.runtime.execution import RuntimeRetryDirective, RuntimeToolDirective, build_usage_delta_factory
from backend.services import completion_bridge
from backend.services.token_calc import count_tokens


class RuntimeUsageTests(unittest.TestCase):
    def test_usage_delta_factory_counts_tool_calls_as_completion_usage(self) -> None:
        prompt = "prompt"
        execution = SimpleNamespace(state=SimpleNamespace(
            answer_text="",
            tool_calls=[{"id": "call_1", "name": "Read", "input": {"path": "README.md"}}],
        ))

        usage_delta = build_usage_delta_factory(prompt)(execution)

        self.assertGreater(usage_delta, count_tokens(prompt))

    def test_usage_delta_factory_uses_token_counts(self) -> None:
        prompt = "hello world hello world hello world"
        answer_text = "I will keep this concise."
        execution = SimpleNamespace(state=SimpleNamespace(answer_text=answer_text))

        usage_delta = build_usage_delta_factory(prompt)(execution)

        self.assertEqual(usage_delta, count_tokens(prompt) + count_tokens(answer_text))
        self.assertNotEqual(usage_delta, len(prompt) + len(answer_text))


class CompletionBridgeUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_retryable_bridge_counts_tool_calls_in_returned_usage(self) -> None:
        prompt = "prompt"
        execution = SimpleNamespace(
            state=SimpleNamespace(
                answer_text="",
                tool_calls=[{"id": "call_1", "name": "Read", "input": {"path": "README.md"}}],
            ),
            acc=None,
            chat_id=None,
        )
        standard_request = StandardRequest(
            prompt=prompt,
            response_model="gpt-4.1",
            resolved_model="qwen3.6-plus",
            surface="openai",
        )

        with patch.object(completion_bridge, "collect_completion_run", AsyncMock(return_value=execution)), \
             patch.object(completion_bridge, "evaluate_retry_directive", return_value=RuntimeRetryDirective(retry=False, next_prompt="")), \
             patch.object(completion_bridge, "build_tool_directive", return_value=RuntimeToolDirective(stop_reason="tool_use")), \
             patch.object(completion_bridge, "_apply_terminal_tool_guard", return_value=(execution, RuntimeToolDirective(stop_reason="tool_use"))), \
             patch.object(completion_bridge, "add_used_tokens", AsyncMock()), \
             patch.object(completion_bridge, "cleanup_runtime_resources", AsyncMock()):
            result = await completion_bridge.run_retryable_completion_bridge(
                client=object(),
                standard_request=standard_request,
                prompt=prompt,
                users_db=object(),
                token="tok",
                history_messages=[],
                max_attempts=1,
            )

        self.assertGreater(result.usage["completion_tokens"], 0)
        self.assertEqual(result.usage["total_tokens"], result.usage["prompt_tokens"] + result.usage["completion_tokens"])


if __name__ == "__main__":
    unittest.main()
