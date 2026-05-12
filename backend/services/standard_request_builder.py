from __future__ import annotations

from backend.adapter.standard_request import StandardRequest, enforce_declared_tool_choice, normalize_tool_choice
from backend.core.config import resolve_request_model
from backend.services.client_profiles import infer_client_profile, request_looks_like_coding_task
from backend.toolcore.prompt_builder import messages_to_prompt
from backend.toolcore.request_normalizer import normalize_chat_request, to_prompt_payload
from backend.toolcall.normalize import build_tool_name_registry


def build_chat_standard_request(req_data: dict, *, default_model: str, surface: str, client_profile: str = "openclaw_openai") -> StandardRequest:
    requested_model = req_data.get("model", default_model)
    effective_client_profile = infer_client_profile(req_data, fallback_profile=client_profile)
    normalized_request = normalize_chat_request(req_data)
    normalized_payload = to_prompt_payload(normalized_request, model=requested_model, stream=bool(req_data.get("stream", False)))
    for field_name in ("system", "developer", "instructions"):
        if field_name in req_data:
            normalized_payload[field_name] = req_data.get(field_name, "")
    prompt_result = messages_to_prompt(normalized_payload, client_profile=effective_client_profile)
    tools = prompt_result.tools
    tool_names = [tool_name for tool_name in (tool.get("name") for tool in tools) if isinstance(tool_name, str) and tool_name]
    coding_intent = request_looks_like_coding_task(req_data, client_profile=effective_client_profile)
    tool_choice = normalize_tool_choice(normalized_request.raw_tool_choice)
    tool_choice = enforce_declared_tool_choice(tool_choice, tool_names)
    return StandardRequest(
        prompt=prompt_result.prompt,
        response_model=requested_model,
        resolved_model=resolve_request_model(
            requested_model,
            client_profile=effective_client_profile,
            tool_enabled=prompt_result.tool_enabled,
            coding_intent=coding_intent,
        ),
        surface=surface,
        client_profile=effective_client_profile,
        requested_model=requested_model,
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
