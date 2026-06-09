from __future__ import annotations

import logging
from typing import Any

from backend.core.config import settings

log = logging.getLogger("qwen2api.accounts")


class AccountPoolDiagnosticsService:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    def account_diagnostic(self, account: Any, now: float, exclude: set | None = None) -> dict:
        min_interval = max(0, settings.ACCOUNT_MIN_INTERVAL_MS) / 1000.0
        next_available_at = max(account.rate_limited_until, account.last_request_started + min_interval)
        next_available_in = round(max(0.0, next_available_at - now), 3)
        is_excluded = bool(exclude and account.email in exclude)
        is_rate_limited = account.rate_limited_until > now
        capacity_available = account.inflight < self.pool.max_inflight
        is_in_cooldown = False
        if account.cooldown_started_at > 0:
            cooldown_period = getattr(settings, "ACCOUNT_COOLDOWN_PERIOD_SECONDS", 300)
            is_in_cooldown = now - account.cooldown_started_at < cooldown_period

        if is_excluded:
            reason = "excluded"
        elif account.activation_pending:
            reason = "pending_activation"
        elif not account.valid:
            reason = account.status_code if account.status_code and account.status_code != "valid" else "invalid"
        elif is_rate_limited:
            reason = "rate_limited"
        elif is_in_cooldown:
            reason = "cooldown"
        elif not capacity_available:
            reason = "busy"
        elif next_available_at > now:
            reason = "min_interval"
        else:
            reason = "ready"

        tok_s_val = account.tok_s
        tok_s_updated_val = account.tok_s_updated_at
        if self.pool.stats_store is not None:
            try:
                entry = self.pool.stats_store.get_by_email(account.email)
                if entry and entry.model_stats:
                    total_tokens = sum(ms.total_tokens for ms in entry.model_stats.values())
                    if total_tokens > 0:
                        weighted_sum = sum(
                            ms.tok_s_ema * ms.total_tokens for ms in entry.model_stats.values()
                        )
                        tok_s_val = weighted_sum / total_tokens
                        tok_s_updated_val = 0.0
            except Exception as exc:
                log.debug("Failed to read stats for %s: %s", account.email, exc)

        return {
            "email": account.email,
            "valid": account.valid,
            "status_code": account.get_status_code(),
            "status_text": account.get_status_text(),
            "ready": reason == "ready",
            "selection_block_reason": reason,
            "inflight": account.inflight,
            "max_inflight": self.pool.max_inflight,
            "capacity_available": capacity_available,
            "is_rate_limited": is_rate_limited,
            "is_in_cooldown": is_in_cooldown,
            "rate_limited_until": account.rate_limited_until,
            "cooldown_started_at": account.cooldown_started_at,
            "cooldown_ends_at": account.cooldown_started_at + getattr(settings, "ACCOUNT_COOLDOWN_PERIOD_SECONDS", 300) if account.cooldown_started_at > 0 else None,
            "next_available_at": next_available_at,
            "next_available_in": next_available_in,
            "last_used": account.last_used,
            "last_request_started": account.last_request_started,
            "last_request_finished": account.last_request_finished,
            "tok_s": tok_s_val,
            "tok_s_updated_at": tok_s_updated_val,
        }

    def account_diagnostics(self, now: float, exclude: set | None = None) -> list[dict]:
        return [self.account_diagnostic(account, now, exclude) for account in self.pool.accounts]

    def scheduler_snapshot(self, now: float, exclude: set | None = None) -> dict:
        diagnostics = self.account_diagnostics(now, exclude)
        blocked_reasons: dict[str, int] = {}
        for item in diagnostics:
            if item["ready"]:
                continue
            reason = item["selection_block_reason"]
            blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1
        ready_count = sum(1 for item in diagnostics if item["ready"])
        next_candidates = [
            item["next_available_at"]
            for item in diagnostics
            if item["valid"] and item["selection_block_reason"] != "excluded"
        ]
        next_ready_at = min(next_candidates, default=0.0)
        return {
            "total": len(diagnostics),
            "ready": ready_count,
            "blocked": len(diagnostics) - ready_count,
            "blocked_reasons": blocked_reasons,
            "in_use": sum(item["inflight"] for item in diagnostics),
            "waiting": len(self.pool._waiters),
            "max_inflight": self.pool.max_inflight,
            "account_min_interval_ms": getattr(settings, "ACCOUNT_MIN_INTERVAL_MS", 0),
            "next_ready_at": next_ready_at,
            "next_ready_in": round(max(0.0, next_ready_at - now), 3) if next_ready_at else 0.0,
        }

    def record_acquire_diagnostics(
        self,
        *,
        strategy: str,
        selected_email: str | None,
        diagnostics: list[dict],
        now: float,
        preferred_email: str | None = None,
        preferred_block_reason: str | None = None,
        exclude: set | None = None,
    ) -> None:
        ready_count = sum(1 for item in diagnostics if item["ready"])
        blocked_reasons: dict[str, int] = {}
        for item in diagnostics:
            if item["ready"]:
                continue
            reason = item["selection_block_reason"]
            blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1
        self.pool.last_acquire_diagnostics = {
            "strategy": strategy,
            "selected_email": selected_email,
            "preferred_email": preferred_email,
            "preferred_block_reason": preferred_block_reason,
            "ready_count": ready_count,
            "blocked_count": len(diagnostics) - ready_count,
            "blocked_reasons": blocked_reasons,
            "snapshot": self.scheduler_snapshot(now, exclude),
        }
