from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.adapter.standard_request import StandardRequest
from backend.services.attachment_preprocessor import PreprocessedAttachments, preprocess_attachments
from backend.services.context_attachment_manager import prepare_context_attachments
from backend.toolcore.task_session import (
    PersistentSessionPlan,
    log_session_plan_reuse_cancelled,
    plan_persistent_session_turn,
)


@dataclass(slots=True)
class OrchestrationResult:
    standard_request: StandardRequest
    working_payload: dict[str, Any]
    preprocessed: PreprocessedAttachments | None
    context_prepared: dict[str, Any]
    session_plan: PersistentSessionPlan


class SessionOrchestrator:
    @staticmethod
    async def prepare(
        *,
        app,
        payload: dict[str, Any],
        surface: str,
        token: str,
        client_profile: str,
        build_standard_request: Callable[[dict[str, Any]], StandardRequest],
        preprocessed: PreprocessedAttachments | None = None,
        skip_preprocess: bool = False,
        preprocess_attachments_fn: Callable[..., Any] | None = None,
        prepare_context_attachments_fn: Callable[..., Any] | None = None,
        plan_persistent_session_turn_fn: Callable[..., Any] | None = None,
    ) -> OrchestrationResult:
        preprocess = preprocess_attachments_fn or preprocess_attachments
        prepare_context = prepare_context_attachments_fn or prepare_context_attachments
        plan_session_turn = plan_persistent_session_turn_fn or plan_persistent_session_turn

        working_payload = preprocessed.payload if preprocessed is not None else payload
        file_store = getattr(app.state, "file_store", None)
        if preprocessed is None and not skip_preprocess and file_store is not None:
            preprocessed = await preprocess(working_payload, file_store, owner_token=token)
            working_payload = preprocessed.payload

        context_prepared = await prepare_context(
            app=app,
            payload=working_payload,
            surface=surface,
            auth_token=token,
            client_profile=client_profile,
            existing_attachments=(preprocessed.attachments if preprocessed is not None else None),
        )
        working_payload = context_prepared["payload"]

        standard_request = build_standard_request(working_payload)
        if inspect.isawaitable(standard_request):
            standard_request = await standard_request

        if preprocessed is not None:
            standard_request.attachments = preprocessed.attachments
            standard_request.uploaded_file_ids = preprocessed.uploaded_file_ids
        standard_request.upstream_files = context_prepared["upstream_files"]
        standard_request.session_key = context_prepared["session_key"]
        standard_request.context_mode = context_prepared["context_mode"]
        standard_request.context_attachment_tokens = context_prepared.get("context_attachment_tokens", 0)
        standard_request.bound_account_email = context_prepared["bound_account_email"]
        standard_request.bound_account = context_prepared["bound_account"]

        session_plan = await plan_session_turn(
            app=app,
            request=standard_request,
            payload=working_payload,
            surface=surface,
        )
        await SessionOrchestrator._apply_session_plan(app=app, request=standard_request, session_plan=session_plan)

        return OrchestrationResult(
            standard_request=standard_request,
            working_payload=working_payload,
            preprocessed=preprocessed,
            context_prepared=context_prepared,
            session_plan=session_plan,
        )

    @staticmethod
    async def _apply_session_plan(*, app, request: StandardRequest, session_plan: PersistentSessionPlan) -> None:
        if not session_plan.enabled:
            return

        request.persistent_session = True
        request.full_prompt = session_plan.full_prompt
        request.prompt = session_plan.prompt
        request.session_message_hashes = session_plan.current_hashes
        request.upstream_chat_id = session_plan.existing_chat_id if session_plan.reuse_chat else None

        if request.bound_account is None and session_plan.account_email:
            request.bound_account = await app.state.account_pool.acquire_wait_preferred(session_plan.account_email, timeout=60)
            if request.bound_account is not None:
                request.bound_account_email = request.bound_account.email
        elif request.bound_account is not None and not request.bound_account_email:
            request.bound_account_email = request.bound_account.email

        if request.upstream_chat_id and request.bound_account is None:
            log_session_plan_reuse_cancelled(
                request=request,
                planned_chat_id=session_plan.existing_chat_id,
                reason="missing_bound_account",
            )
            request.upstream_chat_id = None
            request.prompt = request.full_prompt or request.prompt
