from __future__ import annotations

from typing import Any, Protocol


class SchedulingStrategy(Protocol):
    def select(self, candidates: list[Any]) -> Any:
        """Select one account from already-ready candidates."""


class LeastLoadedStrategy:
    def select(self, candidates: list[Any]) -> Any:
        if not candidates:
            raise ValueError("candidates must not be empty")
        return min(
            candidates,
            key=lambda account: (
                account.inflight,
                account.last_request_started or 0.0,
                account.last_used or 0.0,
                account.email or "",
            ),
        )


class LeastUsedStrategy:
    def select(self, candidates: list[Any]) -> Any:
        if not candidates:
            raise ValueError("candidates must not be empty")
        return min(candidates, key=lambda account: (account.last_used or 0.0, account.email or ""))


class RoundRobinStrategy:
    def __init__(self) -> None:
        self._index = 0

    def select(self, candidates: list[Any]) -> Any:
        if not candidates:
            raise ValueError("candidates must not be empty")
        self._index %= len(candidates)
        selected = candidates[self._index]
        self._index += 1
        return selected


def strategy_for_name(name: str | None, round_robin_strategy: RoundRobinStrategy) -> SchedulingStrategy:
    if name == "least_used":
        return LeastUsedStrategy()
    if name == "round_robin":
        return round_robin_strategy
    return LeastLoadedStrategy()
