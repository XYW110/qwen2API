import unittest
from types import SimpleNamespace

from backend.runtime.execution import build_usage_delta_factory
from backend.services.token_calc import count_tokens


class RuntimeUsageTests(unittest.TestCase):
    def test_usage_delta_factory_uses_token_counts(self) -> None:
        prompt = "hello world hello world hello world"
        answer_text = "I will keep this concise."
        execution = SimpleNamespace(state=SimpleNamespace(answer_text=answer_text))

        usage_delta = build_usage_delta_factory(prompt)(execution)

        self.assertEqual(usage_delta, count_tokens(prompt) + count_tokens(answer_text))
        self.assertNotEqual(usage_delta, len(prompt) + len(answer_text))


if __name__ == "__main__":
    unittest.main()
