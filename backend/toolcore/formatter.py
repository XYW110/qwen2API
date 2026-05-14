from __future__ import annotations

import json
import uuid
from typing import Any

from backend.services.token_calc import calculate_usage, completion_text_for_usage, count_tokens


def build_canonical_openai_chat_payload(*, completion_id: str, created: int, model_name: str, prompt: str, answer_text: str, reasoning_text: str, directives: list[dict[str, Any]]) -> dict[str, Any]:
    del reasoning_text
    tool_blocks = [block for block in directives if block.get("type") == "tool_use"]
    if tool_blocks:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": block["id"],
                    "type": "function",
                    "function": {
                        "name": block["name"],
                        "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                    },
                }
                for block in tool_blocks
            ],
        }
        finish_reason = "tool_calls"
    else:
        message = {"role": "assistant", "content": answer_text}
        finish_reason = "stop"
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model_name,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": calculate_usage(prompt, answer_text, message.get("tool_calls", [])),
    }


def build_canonical_openai_responses_payload(*, response_id: str, created: int, model_name: str, prompt: str, answer_text: str, reasoning_text: str, directives: list[dict[str, Any]]) -> dict[str, Any]:
    tool_blocks = [block for block in directives if block.get("type") == "tool_use"]
    output: list[dict[str, Any]] = []
    if tool_blocks:
        if answer_text:
            output.append(
                {
                    "id": f"msg_{uuid.uuid4().hex[:24]}",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": answer_text, "annotations": []}],
                }
            )
        output.extend(
            {
                "id": block["id"],
                "type": "function_call",
                "status": "completed",
                "call_id": block["id"],
                "name": block["name"],
                "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
            }
            for block in tool_blocks
        )
    else:
        output.append(
            {
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": answer_text, "annotations": []}],
            }
        )
    input_tokens = count_tokens(prompt)
    output_tokens = count_tokens(completion_text_for_usage(answer_text, tool_blocks))
    return {
        "id": response_id,
        "object": "response",
        "created_at": created,
        "status": "completed",
        "model": model_name,
        "output": output,
        "output_text": answer_text,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "output_tokens_details": {"reasoning_tokens": count_tokens(reasoning_text)},
        },
    }


def build_canonical_anthropic_message(*, msg_id: str, model_name: str, prompt: str, answer_text: str, reasoning_text: str, directives: list[dict[str, Any]]) -> dict[str, Any]:
    content_blocks: list[dict[str, Any]] = []
    if reasoning_text:
        content_blocks.append({"type": "thinking", "thinking": reasoning_text})
    content_blocks.extend(directives if directives else ([{"type": "text", "text": answer_text}] if answer_text else []))
    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": model_name,
        "content": content_blocks,
        "stop_reason": "tool_use" if any(block.get("type") == "tool_use" for block in directives) else "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": count_tokens(prompt), "output_tokens": count_tokens(answer_text)},
    }


def build_canonical_gemini_payload(*, answer_text: str) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": answer_text}],
                    "role": "model",
                }
            }
        ]
    }
