import unittest
from unittest.mock import patch

from backend.adapter.standard_request import StandardRequest
from backend.core.config import settings
from backend.runtime.execution import RuntimeAttemptState, parse_tool_directive_once


class ToolCoreFeatureFlagTests(unittest.TestCase):
    def test_toolcore_flag_exists(self) -> None:
        self.assertTrue(hasattr(settings, "TOOLCORE_V2_ENABLED"))

    def test_parse_tool_directive_once_works_with_toolcore_enabled(self) -> None:
        request = StandardRequest(
            prompt="prompt",
            response_model="gpt-4.1",
            resolved_model="qwen3.6-plus",
            surface="openai",
            tools=[{"name": "Read", "parameters": {}}],
            tool_names=["Read"],
            tool_enabled=True,
        )
        state = RuntimeAttemptState(answer_text='##TOOL_CALL##\n{"name": "Read", "input": {"file_path": "README.md"}}\n##END_CALL##')

        with patch.object(settings, "TOOLCORE_V2_ENABLED", True):
            directive = parse_tool_directive_once(request, state)

        self.assertEqual(directive.stop_reason, "tool_use")

    def test_parse_tool_directive_once_works_with_toolcore_disabled(self) -> None:
        request = StandardRequest(
            prompt="prompt",
            response_model="gpt-4.1",
            resolved_model="qwen3.6-plus",
            surface="openai",
            tools=[{"name": "Read", "parameters": {}}],
            tool_names=["Read"],
            tool_enabled=True,
        )
        state = RuntimeAttemptState(answer_text='##TOOL_CALL##\n{"name": "Read", "input": {"file_path": "README.md"}}\n##END_CALL##')

        with patch.object(settings, "TOOLCORE_V2_ENABLED", False):
            directive = parse_tool_directive_once(request, state)

        self.assertEqual(directive.stop_reason, "tool_use")


if __name__ == "__main__":
    unittest.main()
