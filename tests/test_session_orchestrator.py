import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.adapter.standard_request import StandardRequest
from backend.services.session_orchestrator import SessionOrchestrator


class SessionOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_responses_prepare_applies_context_and_session_plan(self) -> None:
        bound_account = SimpleNamespace(email="bound@example.com")
        preferred_account = SimpleNamespace(email="preferred@example.com")
        app = SimpleNamespace(
            state=SimpleNamespace(
                file_store=object(),
                account_pool=SimpleNamespace(acquire_wait_preferred=AsyncMock(return_value=preferred_account)),
            )
        )
        payload = {"messages": [{"role": "user", "content": "hello"}]}
        preprocessed_payload = {"messages": [{"role": "user", "content": "hello"}], "preprocessed": True}
        context_payload = {"messages": [{"role": "user", "content": "hello"}], "context": True}
        preprocessed = SimpleNamespace(
            payload=preprocessed_payload,
            attachments=[SimpleNamespace(file_id="file_1")],
            uploaded_file_ids=["file_1"],
        )
        context_prepared = {
            "payload": context_payload,
            "upstream_files": [{"file_id": "up_1"}],
            "session_key": "session-responses",
            "context_mode": "attachment",
            "context_attachment_tokens": 42,
            "bound_account_email": bound_account.email,
            "bound_account": bound_account,
        }
        session_plan = SimpleNamespace(
            enabled=True,
            full_prompt="full prompt",
            prompt="planned prompt",
            current_hashes=["hash_1"],
            existing_chat_id="chat_1",
            reuse_chat=True,
            account_email="preferred@example.com",
        )
        built_requests = []

        def build_standard_request(working_payload):
            built_requests.append(working_payload)
            return StandardRequest(
                prompt="original prompt",
                response_model="gpt-4.1",
                resolved_model="qwen3",
                surface="responses",
            )

        with patch(
            "backend.services.session_orchestrator.preprocess_attachments",
            AsyncMock(return_value=preprocessed),
        ) as preprocess_mock, patch(
            "backend.services.session_orchestrator.prepare_context_attachments",
            AsyncMock(return_value=context_prepared),
        ) as context_mock, patch(
            "backend.services.session_orchestrator.plan_persistent_session_turn",
            AsyncMock(return_value=session_plan),
        ) as plan_mock, patch(
            "backend.services.session_orchestrator.log_session_plan_reuse_cancelled",
        ) as cancelled_mock:
            result = await SessionOrchestrator.prepare(
                app=app,
                payload=payload,
                surface="responses",
                token="tok",
                client_profile="openclaw",
                build_standard_request=build_standard_request,
            )

        preprocess_mock.assert_awaited_once_with(payload, app.state.file_store, owner_token="tok")
        context_mock.assert_awaited_once_with(
            app=app,
            payload=preprocessed_payload,
            surface="responses",
            auth_token="tok",
            client_profile="openclaw",
            existing_attachments=preprocessed.attachments,
        )
        plan_mock.assert_awaited_once_with(
            app=app,
            request=result.standard_request,
            payload=context_payload,
            surface="responses",
        )
        cancelled_mock.assert_not_called()
        app.state.account_pool.acquire_wait_preferred.assert_not_awaited()

        self.assertEqual(built_requests, [context_payload])
        self.assertIs(result.working_payload, context_payload)
        self.assertIs(result.preprocessed, preprocessed)
        self.assertIs(result.context_prepared, context_prepared)
        self.assertIs(result.session_plan, session_plan)
        self.assertEqual(result.standard_request.attachments, preprocessed.attachments)
        self.assertEqual(result.standard_request.uploaded_file_ids, ["file_1"])
        self.assertEqual(result.standard_request.upstream_files, [{"file_id": "up_1"}])
        self.assertEqual(result.standard_request.session_key, "session-responses")
        self.assertEqual(result.standard_request.context_mode, "attachment")
        self.assertEqual(result.standard_request.context_attachment_tokens, 42)
        self.assertEqual(result.standard_request.bound_account_email, "bound@example.com")
        self.assertIs(result.standard_request.bound_account, bound_account)
        self.assertTrue(result.standard_request.persistent_session)
        self.assertEqual(result.standard_request.full_prompt, "full prompt")
        self.assertEqual(result.standard_request.prompt, "planned prompt")
        self.assertEqual(result.standard_request.session_message_hashes, ["hash_1"])
        self.assertEqual(result.standard_request.upstream_chat_id, "chat_1")

    async def test_responses_prepare_acquires_preferred_account_for_reuse(self) -> None:
        preferred_account = SimpleNamespace(email="preferred@example.com")
        account_pool = SimpleNamespace(acquire_wait_preferred=AsyncMock(return_value=preferred_account))
        app = SimpleNamespace(state=SimpleNamespace(file_store=None, account_pool=account_pool))
        payload = {"messages": [{"role": "user", "content": "hello"}]}
        context_prepared = {
            "payload": payload,
            "upstream_files": [],
            "session_key": "session-responses",
            "context_mode": "inline",
            "context_attachment_tokens": 0,
            "bound_account_email": None,
            "bound_account": None,
        }
        session_plan = SimpleNamespace(
            enabled=True,
            full_prompt="full prompt",
            prompt="planned prompt",
            current_hashes=["hash_1"],
            existing_chat_id="chat_1",
            reuse_chat=True,
            account_email="preferred@example.com",
        )

        with patch(
            "backend.services.session_orchestrator.preprocess_attachments",
            AsyncMock(),
        ) as preprocess_mock, patch(
            "backend.services.session_orchestrator.prepare_context_attachments",
            AsyncMock(return_value=context_prepared),
        ), patch(
            "backend.services.session_orchestrator.plan_persistent_session_turn",
            AsyncMock(return_value=session_plan),
        ), patch(
            "backend.services.session_orchestrator.log_session_plan_reuse_cancelled",
        ) as cancelled_mock:
            result = await SessionOrchestrator.prepare(
                app=app,
                payload=payload,
                surface="responses",
                token="tok",
                client_profile="openclaw",
                build_standard_request=lambda working_payload: StandardRequest(
                    prompt="original prompt",
                    response_model="gpt-4.1",
                    resolved_model="qwen3",
                    surface="responses",
                ),
            )

        preprocess_mock.assert_not_awaited()
        account_pool.acquire_wait_preferred.assert_awaited_once_with("preferred@example.com", timeout=60)
        cancelled_mock.assert_not_called()
        self.assertIs(result.standard_request.bound_account, preferred_account)
        self.assertEqual(result.standard_request.bound_account_email, "preferred@example.com")
        self.assertEqual(result.standard_request.upstream_chat_id, "chat_1")

    async def test_prepare_uses_existing_preprocessed_without_reprocessing(self) -> None:
        app = SimpleNamespace(
            state=SimpleNamespace(
                file_store=object(),
                account_pool=SimpleNamespace(acquire_wait_preferred=AsyncMock()),
            )
        )
        payload = {"messages": [{"role": "user", "content": "hello"}]}
        preprocessed_payload = {"messages": [{"role": "user", "content": "hello"}], "preprocessed": True}
        preprocessed = SimpleNamespace(
            payload=preprocessed_payload,
            attachments=[SimpleNamespace(file_id="file_1")],
            uploaded_file_ids=["file_1"],
        )
        context_payload = {"messages": [{"role": "user", "content": "hello"}], "context": True}
        context_prepared = {
            "payload": context_payload,
            "upstream_files": [],
            "session_key": "session-openai",
            "context_mode": "attachment",
            "context_attachment_tokens": 7,
            "bound_account_email": None,
            "bound_account": None,
        }
        session_plan = SimpleNamespace(
            enabled=False,
            full_prompt=None,
            prompt=None,
            current_hashes=[],
            existing_chat_id=None,
            reuse_chat=False,
            account_email=None,
        )
        built_requests = []

        def build_standard_request(working_payload):
            built_requests.append(working_payload)
            return StandardRequest(
                prompt="original prompt",
                response_model="gpt-4.1",
                resolved_model="qwen3",
                surface="openai",
            )

        with patch(
            "backend.services.session_orchestrator.preprocess_attachments",
            AsyncMock(),
        ) as preprocess_mock, patch(
            "backend.services.session_orchestrator.prepare_context_attachments",
            AsyncMock(return_value=context_prepared),
        ) as context_mock, patch(
            "backend.services.session_orchestrator.plan_persistent_session_turn",
            AsyncMock(return_value=session_plan),
        ):
            result = await SessionOrchestrator.prepare(
                app=app,
                payload=payload,
                surface="openai",
                token="tok",
                client_profile="openclaw",
                build_standard_request=build_standard_request,
                preprocessed=preprocessed,
            )

        preprocess_mock.assert_not_awaited()
        context_mock.assert_awaited_once_with(
            app=app,
            payload=preprocessed_payload,
            surface="openai",
            auth_token="tok",
            client_profile="openclaw",
            existing_attachments=preprocessed.attachments,
        )
        self.assertEqual(built_requests, [context_payload])
        self.assertIs(result.preprocessed, preprocessed)
        self.assertIs(result.working_payload, context_payload)
        self.assertEqual(result.standard_request.attachments, preprocessed.attachments)
        self.assertEqual(result.standard_request.uploaded_file_ids, ["file_1"])


    async def test_responses_prepare_cancels_reuse_when_bound_account_missing(self) -> None:
        account_pool = SimpleNamespace(acquire_wait_preferred=AsyncMock(return_value=None))
        app = SimpleNamespace(state=SimpleNamespace(file_store=None, account_pool=account_pool))
        payload = {"messages": [{"role": "user", "content": "hello"}]}
        context_prepared = {
            "payload": payload,
            "upstream_files": [],
            "session_key": "session-responses",
            "context_mode": "inline",
            "context_attachment_tokens": 0,
            "bound_account_email": None,
            "bound_account": None,
        }
        session_plan = SimpleNamespace(
            enabled=True,
            full_prompt="full prompt",
            prompt="planned prompt",
            current_hashes=["hash_1"],
            existing_chat_id="chat_1",
            reuse_chat=True,
            account_email="preferred@example.com",
        )

        with patch(
            "backend.services.session_orchestrator.prepare_context_attachments",
            AsyncMock(return_value=context_prepared),
        ), patch(
            "backend.services.session_orchestrator.plan_persistent_session_turn",
            AsyncMock(return_value=session_plan),
        ), patch(
            "backend.services.session_orchestrator.log_session_plan_reuse_cancelled",
        ) as cancelled_mock:
            result = await SessionOrchestrator.prepare(
                app=app,
                payload=payload,
                surface="responses",
                token="tok",
                client_profile="openclaw",
                build_standard_request=lambda working_payload: StandardRequest(
                    prompt="original prompt",
                    response_model="gpt-4.1",
                    resolved_model="qwen3",
                    surface="responses",
                ),
            )

        account_pool.acquire_wait_preferred.assert_awaited_once_with("preferred@example.com", timeout=60)
        cancelled_mock.assert_called_once_with(
            request=result.standard_request,
            planned_chat_id="chat_1",
            reason="missing_bound_account",
        )
        self.assertIsNone(result.standard_request.upstream_chat_id)
        self.assertEqual(result.standard_request.prompt, "full prompt")


if __name__ == "__main__":
    unittest.main()
