import unittest

from backend.core.account_pool import Account
from backend.core.account_scheduling import LeastLoadedStrategy, LeastUsedStrategy, RoundRobinStrategy


class SchedulingStrategyTests(unittest.TestCase):
    def test_least_loaded_prefers_lower_inflight_then_request_start_usage_and_email(self) -> None:
        loaded = Account(email="loaded@example.com")
        loaded.inflight = 1
        loaded.last_request_started = 1.0
        loaded.last_used = 1.0
        idle_newer = Account(email="idle-newer@example.com")
        idle_newer.inflight = 0
        idle_newer.last_request_started = 20.0
        idle_newer.last_used = 10.0
        idle_older = Account(email="idle-older@example.com")
        idle_older.inflight = 0
        idle_older.last_request_started = 10.0
        idle_older.last_used = 20.0

        selected = LeastLoadedStrategy().select([loaded, idle_newer, idle_older])

        self.assertIs(selected, idle_older)

    def test_least_used_prefers_oldest_usage_then_email(self) -> None:
        later_email = Account(email="z-last@example.com")
        later_email.last_used = 10.0
        earlier_email = Account(email="a-first@example.com")
        earlier_email.last_used = 10.0
        newest = Account(email="newest@example.com")
        newest.last_used = 20.0

        selected = LeastUsedStrategy().select([newest, later_email, earlier_email])

        self.assertIs(selected, earlier_email)

    def test_round_robin_advances_index_across_calls(self) -> None:
        first = Account(email="first@example.com")
        second = Account(email="second@example.com")
        third = Account(email="third@example.com")
        strategy = RoundRobinStrategy()

        self.assertIs(strategy.select([first, second, third]), first)
        self.assertIs(strategy.select([first, second, third]), second)
        self.assertIs(strategy.select([first, second, third]), third)
        self.assertIs(strategy.select([first, second, third]), first)


if __name__ == "__main__":
    unittest.main()
