import unittest

from backend.services.client_profiles import CLAUDE_CODE_OPENAI_PROFILE, OPENCLAW_OPENAI_PROFILE
from backend.toolcore.prompt_builder import _extract_text, _extract_user_text_only, messages_to_prompt


class ToolCorePromptBuilderTests(unittest.TestCase):
    def test_extract_user_text_only_joins_text_blocks(self) -> None:
        content = [
            {"type": "text", "text": "first"},
            {"type": "tool_result", "content": "ignored"},
            {"type": "text", "text": "second"},
        ]

        self.assertEqual(_extract_user_text_only(content, client_profile=OPENCLAW_OPENAI_PROFILE), "first\nsecond")

    def test_extract_text_renders_tool_and_attachment_blocks(self) -> None:
        content = [
            {"type": "text", "text": "look here"},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "README.md"}},
            {"type": "tool_result", "tool_use_id": "call_1", "content": [{"type": "text", "text": "done"}]},
            {"type": "input_file", "file_id": "file_1", "filename": "spec.md"},
            {"type": "input_image", "file_id": "img_1", "mime_type": "image/png"},
        ]

        rendered = _extract_text(content, client_profile=CLAUDE_CODE_OPENAI_PROFILE)

        self.assertIn("look here", rendered)
        self.assertIn('<|DSML|tool_calls>\n  <|DSML|invoke name="Read">\n    <|DSML|parameter name="file_path"><![CDATA[README.md]]></|DSML|parameter>\n  </|DSML|invoke>\n</|DSML|tool_calls>', rendered)
        self.assertIn("[Tool Result for call call_1]\ndone\n[/Tool Result]", rendered)
        self.assertIn("[Attachment file_id=file_1 filename=spec.md]", rendered)
        self.assertIn("[Attachment image file_id=img_1 mime=image/png]", rendered)

    def test_extract_text_user_tool_mode_keeps_latest_text_block(self) -> None:
        content = [
            {"type": "text", "text": "old instruction"},
            {"type": "text", "text": "latest instruction"},
        ]

        self.assertEqual(
            _extract_text(content, user_tool_mode=True, client_profile=CLAUDE_CODE_OPENAI_PROFILE),
            "latest instruction",
        )

    def test_messages_to_prompt_preserves_required_tool_and_current_task(self) -> None:
        req_data = {
            "system": "You are helpful",
            "messages": [
                {"role": "user", "content": "Read the spec and answer"},
                {"role": "assistant", "content": "Working on it"},
                {"role": "user", "content": "Now inspect README.md"},
            ],
            "tools": [
                {
                    "name": "Read",
                    "description": "Read file content",
                    "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}}},
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "Read"}},
        }

        result = messages_to_prompt(req_data, client_profile=OPENCLAW_OPENAI_PROFILE)

        self.assertTrue(result.tool_enabled)
        self.assertEqual(result.tools[0]["name"], "Read")
        self.assertIn('MUST call the exact tool "Read"', result.prompt)
        self.assertIn("Human (CURRENT TASK - TOP PRIORITY): Now inspect README.md", result.prompt)
        self.assertTrue(result.prompt.endswith("Assistant:"))

    def test_messages_to_prompt_strips_agent_runtime_system_prose(self) -> None:
        req_data = {
            "system": "You are a personal assistant running inside OpenClaw.\n## Tooling\nTool availability (filtered by policy):\n- read: Read file contents\n- write: Create or overwrite files",
            "messages": [
                {"role": "user", "content": "Find the target file and explain it"},
            ],
            "tools": [
                {
                    "name": "read",
                    "description": "Read file contents",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                }
            ],
        }

        result = messages_to_prompt(req_data, client_profile=OPENCLAW_OPENAI_PROFILE)

        self.assertNotIn("running inside OpenClaw", result.prompt)
        self.assertNotIn("Tool availability (filtered by policy)", result.prompt)
        self.assertIn("=== MANDATORY TOOL CALL INSTRUCTIONS ===", result.prompt)
        self.assertIn("Human (CURRENT TASK - TOP PRIORITY): Find the target file and explain it", result.prompt)

    def test_messages_to_prompt_strips_opencode_runtime_system_prose(self) -> None:
        req_data = {
            "system": "You are a personal assistant running inside SomeAgent.\n## Tooling\nTool availability (filtered by policy):\n- read: Read file contents",
            "messages": [
                {"role": "user", "content": "Summarize the repository layout"},
            ],
            "tools": [
                {
                    "name": "read",
                    "description": "Read file contents",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                }
            ],
        }

        result = messages_to_prompt(req_data, client_profile=CLAUDE_CODE_OPENAI_PROFILE)

        self.assertNotIn("running inside SomeAgent", result.prompt)
        self.assertNotIn("Tool availability (filtered by policy)", result.prompt)
        self.assertIn("=== MANDATORY TOOL CALL INSTRUCTIONS ===", result.prompt)

    def test_messages_to_prompt_keeps_normal_system_prompt(self) -> None:
        req_data = {
            "system": "You are a careful code reviewer.",
            "messages": [
                {"role": "user", "content": "Review this diff"},
            ],
            "tools": [
                {
                    "name": "read",
                    "description": "Read file contents",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                }
            ],
        }

        result = messages_to_prompt(req_data, client_profile=OPENCLAW_OPENAI_PROFILE)

        self.assertIn("<system>\nYou are a careful code reviewer.\n</system>", result.prompt)

    def test_messages_to_prompt_strips_agent_runtime_assistant_history(self) -> None:
        req_data = {
            "messages": [
                {
                    "role": "assistant",
                    "content": "You are a personal assistant running inside OpenClaw.\n## Tooling\nTool availability (filtered by policy):\n- read: Read file contents",
                },
                {"role": "user", "content": "请分析这个脚本的作用"},
            ],
            "tools": [
                {
                    "name": "read",
                    "description": "Read file contents",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                }
            ],
        }

        result = messages_to_prompt(req_data, client_profile=OPENCLAW_OPENAI_PROFILE)

        self.assertNotIn("running inside OpenClaw", result.prompt)
        self.assertNotIn("Tool availability (filtered by policy)", result.prompt)
        self.assertIn("Human (CURRENT TASK - TOP PRIORITY): 请分析这个脚本的作用", result.prompt)

    def test_messages_to_prompt_strips_agent_runtime_user_wrapper_but_keeps_task(self) -> None:
        req_data = {
            "messages": [
                {
                    "role": "user",
                    "content": "You are a personal assistant running inside OpenClaw.\n## Tooling\nTool availability (filtered by policy):\n- read: Read file contents\n\n请检查这个脚本的内容",
                },
            ],
            "tools": [
                {
                    "name": "read",
                    "description": "Read file contents",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                }
            ],
        }

        result = messages_to_prompt(req_data, client_profile=OPENCLAW_OPENAI_PROFILE)

        self.assertNotIn("running inside OpenClaw", result.prompt)
        self.assertNotIn("Tool availability (filtered by policy)", result.prompt)
        self.assertIn("请检查这个脚本的内容", result.prompt)

    def test_messages_to_prompt_strips_skill_bootstrap_from_latest_user_line(self) -> None:
        req_data = {
            "messages": [
                {
                    "role": "user",
                    "content": "The following skills provide specialized instructions for specific tasks.\n\nUse the read tool to load a skill's file when the task matches its name.\n\n<available_skills>\n  <skill>\n    <name>agent-orchestrator</name>\n    <location>~/.openclaw/workspace/skills/agent-orchestrator/SKILL.md</location>\n  </skill>\n  <skill>\n    <name>ai-daily-digest</name>\n    <location>~/.openclaw/workspace/skills/ai-daily-digest/SKILL.md</location>\n  </skill>\n</available_skills>\n\n请阅读本地脚本并解释它如何抓取限免信息",
                },
            ],
            "tools": [
                {
                    "name": "read",
                    "description": "Read file contents",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                }
            ],
        }

        result = messages_to_prompt(req_data, client_profile=OPENCLAW_OPENAI_PROFILE)

        self.assertNotIn("The following skills provide specialized instructions", result.prompt)
        self.assertNotIn("<available_skills>", result.prompt)
        self.assertNotIn("agent-orchestrator", result.prompt)
        self.assertIn("请阅读本地脚本并解释它如何抓取限免信息", result.prompt)


if __name__ == "__main__":
    unittest.main()
