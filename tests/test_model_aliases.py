import json
import unittest
from types import SimpleNamespace

from backend.api.models import _build_model_list_payload, list_models
from backend.core.config import resolve_model


class ModelAliasTests(unittest.TestCase):
    def test_qwen37_plus_preview_resolves_to_invite_beta_upstream_name(self) -> None:
        self.assertEqual(
            resolve_model("qwen3.7-plus-preview"),
            "qwen-latest-series-invite-beta-v16",
        )

    def test_model_list_fallback_includes_qwen37_plus_preview_alias(self) -> None:
        payload = _build_model_list_payload()
        model_ids = {item["id"] for item in payload["data"]}

        self.assertIn("qwen3.7-plus-preview", model_ids)


class _FakeUsersDB:
    async def get(self):
        return [{"id": "test-key", "quota": 100, "used_tokens": 0}]


class _FakeQwenClient:
    async def list_models(self, token: str) -> list[dict]:
        return [
            {"id": "qwen3.6-plus"},
            {"id": "qwen3.6-max-preview"},
            {"id": "qwen3.7-max"},
        ]


class ModelListEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_list_merges_upstream_models_with_downstream_aliases(self) -> None:
        request = SimpleNamespace(
            headers={"Authorization": "Bearer test-key"},
            query_params={},
            app=SimpleNamespace(
                state=SimpleNamespace(
                    users_db=_FakeUsersDB(),
                    qwen_client=_FakeQwenClient(),
                )
            ),
        )

        response = await list_models(request)
        payload = json.loads(response.body.decode("utf-8"))
        model_ids = {item["id"] for item in payload["data"]}

        self.assertIn("qwen3.6-plus", model_ids)
        self.assertIn("qwen3.6-max-preview", model_ids)
        self.assertIn("qwen3.7-max", model_ids)
        self.assertIn("qwen3.7-plus-preview", model_ids)


if __name__ == "__main__":
    unittest.main()
