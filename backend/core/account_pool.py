import asyncio
import logging
import random
import time
from typing import Optional

from backend.core.account_pool_diagnostics import AccountPoolDiagnosticsService
from backend.core.account_scheduling import RoundRobinStrategy, strategy_for_name
from backend.core.database import AsyncJsonDB
from backend.core.config import settings

log = logging.getLogger("qwen2api.accounts")


def _jitter_seconds() -> float:
    low = max(0, settings.REQUEST_JITTER_MIN_MS)
    high = max(low, settings.REQUEST_JITTER_MAX_MS)
    return random.uniform(low, high) / 1000.0


class Account:
    def __init__(
        self,
        email="",
        password="",
        token="",
        cookies="",
        proxy="",
        username="",
        activation_pending=False,
        status_code="",
        last_error="",
        **kwargs,
    ):
        self.email = email
        self.password = password
        self.token = token
        self.cookies = cookies
        # 兼容旧数据: kwargs 中可能有 proxy，优先使用显式参数
        self.proxy = kwargs.pop("proxy", proxy) or proxy
        self.username = username
        self.activation_pending = activation_pending
        self.valid = not activation_pending
        self.last_used = 0.0
        self.inflight = 0
        self.rate_limited_until = 0.0
        self.healing = False
        self.status_code = status_code or ("pending_activation" if activation_pending else "valid")
        self.last_error = last_error or ""
        self.last_request_started = float(kwargs.get("last_request_started", 0.0) or 0.0)
        self.last_request_finished = float(kwargs.get("last_request_finished", 0.0) or 0.0)
        self.consecutive_failures = int(kwargs.get("consecutive_failures", 0) or 0)
        self.rate_limit_strikes = int(kwargs.get("rate_limit_strikes", 0) or 0)
        self.cooldown_started_at = float(kwargs.pop("cooldown_started_at", 0.0) or 0.0)
        self.tok_s = float(kwargs.get("tok_s", 0.0) or 0.0)
        self.tok_s_updated_at = float(kwargs.get("tok_s_updated_at", 0.0) or 0.0)
        self.waf_cookies: str = kwargs.get("waf_cookies", "") or ""
        self.waf_cookies_expires_at: float = float(kwargs.get("waf_cookies_expires_at", 0) or 0)
        # Real wall-clock start time for tok/s calculation (not affected by jitter)
        self._tok_s_start_time = 0.0

    def is_rate_limited(self) -> bool:
        return self.rate_limited_until > time.time()

    def is_available(self) -> bool:
        if not self.valid:
            return False
        if self.is_rate_limited():
            return False
        # 冷却机制: consecutive_failures 达阈值后进入冷却期
        if self.cooldown_started_at > 0:
            cooldown_period = getattr(settings, "ACCOUNT_COOLDOWN_PERIOD_SECONDS", 300)
            if time.time() - self.cooldown_started_at < cooldown_period:
                return False
            # 冷却期结束，自动恢复
            self.cooldown_started_at = 0.0
            self.consecutive_failures = 0
        return True

    def next_available_at(self) -> float:
        min_interval = max(0, settings.ACCOUNT_MIN_INTERVAL_MS) / 1000.0
        return max(self.rate_limited_until, self.last_request_started + min_interval)

    def get_status_code(self) -> str:
        if self.activation_pending:
            return "pending_activation"
        if self.is_rate_limited():
            return "rate_limited"
        if self.valid:
            return "valid"
        if self.status_code == "banned":
            return "banned"
        if self.status_code == "auth_error":
            return "auth_error"
        return self.status_code or "invalid"

    def get_status_text(self) -> str:
        status_map = {
            "valid": "正常",
            "pending_activation": "待激活",
            "rate_limited": "限流",
            "banned": "封禁",
            "auth_error": "鉴权失败",
            "invalid": "失效",
            "unknown": "未知",
        }
        return status_map.get(self.get_status_code(), "未知")

    def update_tok_s(self, tokens: int, elapsed_seconds: float) -> None:
        """使用 EMA 更新 tok/s

        Args:
            tokens: 本次请求生成的 token 数
            elapsed_seconds: 本次请求耗时（秒）
        """
        if elapsed_seconds <= 0 or tokens <= 0:
            return

        current_tok_s = tokens / elapsed_seconds

        # 使用 EMA (指数移动平均) 平滑更新
        # 新值权重 0.3，历史值权重 0.7
        if self.tok_s > 0:
            self.tok_s = self.tok_s * 0.7 + current_tok_s * 0.3
        else:
            self.tok_s = current_tok_s

        self.tok_s_updated_at = time.time()

    def to_dict(self):
        # Phase 2: 仅序列化凭证与基础配置字段，统计/运行时状态不再持久化
        return {
            "email": self.email,
            "password": self.password,
            "token": self.token,
            "cookies": self.cookies,
            "proxy": self.proxy,
            "username": self.username,
            "activation_pending": self.activation_pending,
            "status_code": self.status_code,
            "last_error": self.last_error,
        }


class AccountPool:
    def __init__(self, db: AsyncJsonDB, max_inflight: int = settings.MAX_INFLIGHT_PER_ACCOUNT, stats_store=None):
        self.db = db
        self.max_inflight = max_inflight
        self.accounts: list[Account] = []
        self._lock = asyncio.Lock()
        self._waiters: list[asyncio.Event] = []
        self._sticky_email: Optional[str] = None
        self.last_acquire_diagnostics: dict = {}
        self.last_acquire_wait_diagnostics: dict = {}
        self._round_robin_strategy = RoundRobinStrategy()
        self.stats_store = stats_store
        self._diagnostics = AccountPoolDiagnosticsService(self)

    async def load(self):
        data = await self.db.load()
        self.accounts = [Account(**d) for d in data] if isinstance(data, list) else []
        log.info(f"Loaded {len(self.accounts)} upstream account(s)")

        # Phase 2: 如果注入了 stats_store，执行一次性幂等迁移
        if self.stats_store is not None and isinstance(data, list):
            try:
                await self.stats_store.migrate_from_legacy(data)
            except Exception as e:
                log.warning("Failed to migrate legacy account stats: %s", e)

    async def save(self):
        await self.db.save([a.to_dict() for a in self.accounts])

    async def add(self, account: Account):
        async with self._lock:
            self.accounts = [a for a in self.accounts if a.email != account.email]
            self.accounts.append(account)
            await self.save()

    async def remove(self, email: str):
        async with self._lock:
            self.accounts = [a for a in self.accounts if a.email != email]
            await self.save()

    def set_max_inflight(self, value: int):
        self.max_inflight = max(1, int(value))

    def get_by_email(self, email: str) -> Optional[Account]:
        return next((a for a in self.accounts if a.email == email), None)

    def _reclaim_stale_inflight(self, now: float) -> None:
        timeout = max(0.0, float(getattr(settings, "ACCOUNT_BUSY_TIMEOUT_SECONDS", 0) or 0))
        if timeout <= 0:
            return
        for acc in self.accounts:
            if acc.inflight <= 0 or acc.last_request_started <= 0:
                continue
            age = now - acc.last_request_started
            if age < timeout:
                continue
            log.warning(
                "[账号池] reclaim_stale_busy email=%s inflight=%s age=%.3fs timeout=%.3fs",
                acc.email,
                acc.inflight,
                age,
                timeout,
            )
            acc.inflight = 0
            acc.last_request_finished = now

    def _account_diagnostic(self, acc: Account, now: float, exclude: set | None = None) -> dict:
        return self._diagnostics.account_diagnostic(acc, now, exclude)

    def account_diagnostics(self, exclude: set | None = None) -> list[dict]:
        now = time.time()
        return self._diagnostics.account_diagnostics(now, exclude)

    def _scheduler_snapshot(self, now: float, exclude: set | None = None) -> dict:
        return self._diagnostics.scheduler_snapshot(now, exclude)

    def _record_acquire_diagnostics(
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
        self._diagnostics.record_acquire_diagnostics(
            strategy=strategy,
            selected_email=selected_email,
            diagnostics=diagnostics,
            now=now,
            preferred_email=preferred_email,
            preferred_block_reason=preferred_block_reason,
            exclude=exclude,
        )

    async def acquire_preferred(self, preferred_email: str | None = None, exclude: set = None) -> Optional[Account]:
        if not preferred_email:
            return await self.acquire(exclude)
        preferred_block_reason = "missing"
        async with self._lock:
            now = time.time()
            self._reclaim_stale_inflight(now)
            diagnostics = [self._account_diagnostic(acc, now, exclude) for acc in self.accounts]
            preferred_diag = next((item for item in diagnostics if item["email"] == preferred_email), None)
            preferred = next((a for a in self.accounts if a.email == preferred_email), None)
            if preferred_diag:
                preferred_block_reason = preferred_diag["selection_block_reason"]
            if preferred and preferred_diag and preferred_diag["ready"]:
                preferred.inflight += 1
                preferred.last_used = now
                preferred.last_request_started = now + _jitter_seconds()
                preferred._tok_s_start_time = now
                self._sticky_email = preferred.email
                self._record_acquire_diagnostics(
                    strategy="preferred",
                    selected_email=preferred.email,
                    diagnostics=diagnostics,
                    now=now,
                    preferred_email=preferred_email,
                    exclude=exclude,
                )
                log.info("[账号池] acquire_selected strategy=preferred email=%s ready=%s", preferred.email, self.last_acquire_diagnostics["ready_count"])
                return preferred

        acc = await self.acquire(exclude)
        fallback_diag = dict(self.last_acquire_diagnostics)
        fallback_diag.update({
            "strategy": "fallback",
            "preferred_email": preferred_email,
            "preferred_block_reason": preferred_block_reason,
        })
        self.last_acquire_diagnostics = fallback_diag
        if acc:
            log.info("[账号池] acquire_selected strategy=fallback preferred=%s email=%s reason=%s", preferred_email, acc.email, preferred_block_reason)
        return acc

    async def acquire_wait_preferred(self, preferred_email: str | None = None, timeout: float = 60, exclude: set = None) -> Optional[Account]:
        deadline = time.time() + timeout
        while True:
            acc = await self.acquire_preferred(preferred_email, exclude)
            if acc:
                return acc
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            evt = asyncio.Event()
            self._waiters.append(evt)
            try:
                await asyncio.wait_for(evt.wait(), timeout=min(remaining, 0.5))
            except asyncio.TimeoutError:
                pass
            finally:
                if evt in self._waiters:
                    self._waiters.remove(evt)

    async def acquire(self, exclude: set = None) -> Optional[Account]:
        strategy = getattr(settings, "ACCOUNT_SELECTION_STRATEGY", "least_loaded")
        async with self._lock:
            now = time.time()
            self._reclaim_stale_inflight(now)
            diagnostics = [self._account_diagnostic(acc, now, exclude) for acc in self.accounts]
            ready_emails = {item["email"] for item in diagnostics if item["ready"]}
            ready = [a for a in self.accounts if a.email in ready_emails]
            if not ready:
                self._record_acquire_diagnostics(
                    strategy=strategy,
                    selected_email=None,
                    diagnostics=diagnostics,
                    now=now,
                    exclude=exclude,
                )
                return None

            selector = strategy_for_name(strategy, self._round_robin_strategy)
            best = selector.select(ready)

            best.inflight += 1
            best.last_used = now
            best.last_request_started = now + _jitter_seconds()
            best._tok_s_start_time = now
            self._sticky_email = best.email if len(ready) == 1 else None
            self._record_acquire_diagnostics(
                strategy=strategy,
                selected_email=best.email,
                diagnostics=diagnostics,
                now=now,
                exclude=exclude,
            )
            log.info("[账号池] acquire_selected strategy=%s email=%s ready=%s", strategy, best.email, self.last_acquire_diagnostics["ready_count"])
            return best

    async def acquire_wait(self, timeout: float = 60, exclude: set = None) -> Optional[Account]:
        deadline = time.time() + timeout
        while True:
            acc = await self.acquire(exclude)
            if acc:
                self.last_acquire_wait_diagnostics = {
                    "result": "selected",
                    "timeout": timeout,
                    "selected_email": acc.email,
                    "snapshot": self.last_acquire_diagnostics.get("snapshot", {}),
                }
                return acc

            async with self._lock:
                now = time.time()
                candidates = [
                    a for a in self.accounts
                    if a.valid and (not exclude or a.email not in exclude)
                ]
                snapshot = self._scheduler_snapshot(now, exclude)
                if not candidates:
                    self.last_acquire_wait_diagnostics = {
                        "result": "no_candidates",
                        "timeout": timeout,
                        "snapshot": snapshot,
                    }
                    log.warning("[账号池] acquire_wait_no_candidates snapshot=%s", snapshot)
                    return None
                next_ready_at = min((a.next_available_at() for a in candidates), default=now)

            remaining = deadline - time.time()
            if remaining <= 0:
                self.last_acquire_wait_diagnostics = {
                    "result": "timeout",
                    "timeout": timeout,
                    "snapshot": snapshot,
                }
                log.warning(
                    "[账号池] acquire_wait_timeout timeout=%s ready=%s blocked_reasons=%s waiting=%s",
                    timeout,
                    snapshot["ready"],
                    snapshot["blocked_reasons"],
                    snapshot["waiting"],
                )
                return None

            evt = asyncio.Event()
            self._waiters.append(evt)
            wait_timeout = min(remaining, max(0.05, next_ready_at - time.time() + 0.05))
            try:
                await asyncio.wait_for(evt.wait(), timeout=wait_timeout)
            except asyncio.TimeoutError:
                pass
            finally:
                if evt in self._waiters:
                    self._waiters.remove(evt)

    def release(self, acc: Account):
        acc.inflight = max(0, acc.inflight - 1)
        acc.last_request_finished = time.time()
        if self._waiters:
            evt = self._waiters.pop(0)
            evt.set()

    def mark_invalid(self, acc: Account, reason: str = "invalid", error_message: str = ""):
        acc.valid = False
        acc.status_code = reason or "invalid"
        acc.last_error = error_message or acc.last_error
        acc.consecutive_failures += 1
        if reason == "pending_activation":
            acc.activation_pending = True
        if self._sticky_email == acc.email:
            self._sticky_email = None
        log.warning(f"[账号] {acc.email} 已标记为不可用，状态={acc.status_code}")
        # 冷却机制: consecutive_failures 达阈值后进入冷却期
        max_failures = getattr(settings, "ACCOUNT_MAX_FAILURES_BEFORE_COOLDOWN", 3)
        if acc.consecutive_failures >= max_failures and acc.cooldown_started_at == 0:
            acc.cooldown_started_at = time.time()
            log.warning(f"[账号] {acc.email} 失败次数达上限({max_failures})，进入冷却期")

    def mark_success(self, acc: Account):
        acc.consecutive_failures = 0
        acc.rate_limit_strikes = 0
        if acc.status_code == "rate_limited":
            acc.status_code = "valid"
        if not acc.activation_pending:
            acc.valid = True

    def mark_rate_limited(self, acc: Account, cooldown: int | None = None, error_message: str = ""):
        acc.rate_limit_strikes += 1
        base = cooldown if cooldown is not None else settings.RATE_LIMIT_BASE_COOLDOWN
        dynamic = min(settings.RATE_LIMIT_MAX_COOLDOWN, int(base * (2 ** max(0, acc.rate_limit_strikes - 1))))
        dynamic += int(_jitter_seconds())
        acc.rate_limited_until = time.time() + dynamic
        acc.status_code = "rate_limited"
        acc.last_error = error_message or acc.last_error
        if self._sticky_email == acc.email:
            self._sticky_email = None
        log.warning(f"[账号] {acc.email} 已限流冷却 {dynamic} 秒")

    def status(self):
        available = [a for a in self.accounts if a.is_available()]
        rate_limited = [a for a in self.accounts if a.get_status_code() == "rate_limited"]
        invalid = [a for a in self.accounts if a.get_status_code() not in ("valid", "rate_limited")]
        activation_pending = [a for a in self.accounts if a.get_status_code() == "pending_activation"]
        banned = [a for a in self.accounts if a.get_status_code() == "banned"]
        now = time.time()
        snapshot = self._scheduler_snapshot(now)
        return {
            "total": len(self.accounts),
            "valid": len(available),
            "ready": snapshot["ready"],
            "blocked": snapshot["blocked"],
            "blocked_reasons": snapshot["blocked_reasons"],
            "rate_limited": len(rate_limited),
            "invalid": len(invalid),
            "activation_pending": len(activation_pending),
            "banned": len(banned),
            "in_use": snapshot["in_use"],
            "max_inflight": self.max_inflight,
            "waiting": len(self._waiters),
            "account_min_interval_ms": snapshot["account_min_interval_ms"],
            "selection_strategy": getattr(settings, "ACCOUNT_SELECTION_STRATEGY", "least_loaded"),
            "next_ready_at": snapshot["next_ready_at"],
            "next_ready_in": snapshot["next_ready_in"],
            "last_acquire_diagnostics": self.last_acquire_diagnostics,
            "last_acquire_wait_diagnostics": self.last_acquire_wait_diagnostics,
        }
