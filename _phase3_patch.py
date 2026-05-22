"""Phase 3 patch script: Add polling strategy and cooldown mechanism."""
import os
import re

BASE = r"D:\Work\Project\ToolProject\qwen2API"

# ============================================================
# Step 3.1: config.py — Add new config items
# ============================================================
config_path = os.path.join(BASE, "backend", "core", "config.py")
with open(config_path, "r", encoding="utf-8") as f:
    content = f.read()

# Insert after RATE_LIMIT_MAX_COOLDOWN line
anchor = '    RATE_LIMIT_MAX_COOLDOWN: int = int(os.getenv("RATE_LIMIT_MAX_COOLDOWN", 3600))'
insert_text = """    # 账号选择策略与冷却机制
    ACCOUNT_SELECTION_STRATEGY: str = os.getenv("ACCOUNT_SELECTION_STRATEGY", "least_loaded")
    ACCOUNT_MAX_FAILURES_BEFORE_COOLDOWN: int = int(os.getenv("ACCOUNT_MAX_FAILURES_BEFORE_COOLDOWN", 3))
    ACCOUNT_COOLDOWN_PERIOD_SECONDS: int = int(os.getenv("ACCOUNT_COOLDOWN_PERIOD_SECONDS", 300))
"""

if "ACCOUNT_SELECTION_STRATEGY" not in content:
    content = content.replace(anchor, anchor + "\n" + insert_text)
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("config.py: Added 3 new config items")
else:
    print("config.py: ACCOUNT_SELECTION_STRATEGY already exists, skipping")


# ============================================================
# Step 3.2: account_pool.py — Multiple modifications
# ============================================================
pool_path = os.path.join(BASE, "backend", "core", "account_pool.py")
with open(pool_path, "r", encoding="utf-8") as f:
    content = f.read()

# --- 3.2.1: Account.__init__ — Add cooldown_started_at field ---
anchor = '        self.rate_limit_strikes = int(kwargs.get("rate_limit_strikes", 0) or 0)'
insert = '        self.cooldown_started_at = float(kwargs.pop("cooldown_started_at", 0.0) or 0.0)'

if "cooldown_started_at" not in content:
    content = content.replace(anchor, anchor + "\n" + insert)
    print("account_pool.py: Added cooldown_started_at to Account.__init__")
else:
    print("account_pool.py: cooldown_started_at already in __init__, skipping")

# --- 3.2.2: Account.to_dict() — Add cooldown_started_at ---
anchor = '        "rate_limit_strikes": self.rate_limit_strikes,'
insert = '        "cooldown_started_at": self.cooldown_started_at,'

if '"cooldown_started_at"' not in content:
    content = content.replace(anchor, anchor + "\n" + insert)
    print("account_pool.py: Added cooldown_started_at to to_dict()")
else:
    print("account_pool.py: cooldown_started_at already in to_dict(), skipping")

# --- 3.2.3: Account.is_available() — Add cooldown check ---
old_is_available = """    def is_available(self) -> bool:
        return self.valid and not self.is_rate_limited()"""

new_is_available = """    def is_available(self) -> bool:
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
        return True"""

if "cooldown_started_at > 0" not in content:
    content = content.replace(old_is_available, new_is_available)
    print("account_pool.py: Updated is_available() with cooldown check")
else:
    print("account_pool.py: is_available() already has cooldown check, skipping")

# --- 3.2.4: AccountPool.__init__ — Add _round_robin_index ---
anchor = '        self.last_acquire_wait_diagnostics: dict = {}'
insert = '        self._round_robin_index: int = 0'

if "_round_robin_index" not in content:
    content = content.replace(anchor, anchor + "\n" + insert)
    print("account_pool.py: Added _round_robin_index to AccountPool.__init__")
else:
    print("account_pool.py: _round_robin_index already exists, skipping")

# --- 3.2.5: _account_diagnostic — Add cooldown status ---
# Add cooldown check after is_rate_limited line
old_diag_block = """        is_excluded = bool(exclude and acc.email in exclude)
        is_rate_limited = acc.rate_limited_until > now
        capacity_available = acc.inflight < self.max_inflight

        if is_excluded:
            reason = "excluded"
        elif acc.activation_pending:
            reason = "pending_activation"
        elif not acc.valid:
            reason = acc.status_code if acc.status_code and acc.status_code != "valid" else "invalid"
        elif is_rate_limited:
            reason = "rate_limited"
        elif not capacity_available:
            reason = "busy"
        elif next_available_at > now:
            reason = "cooldown"
        else:
            reason = "ready\""""

new_diag_block = """        is_excluded = bool(exclude and acc.email in exclude)
        is_rate_limited = acc.rate_limited_until > now
        capacity_available = acc.inflight < self.max_inflight
        # 冷却机制检查
        is_in_cooldown = False
        if acc.cooldown_started_at > 0:
            cooldown_period = getattr(settings, "ACCOUNT_COOLDOWN_PERIOD_SECONDS", 300)
            if time.time() - acc.cooldown_started_at < cooldown_period:
                is_in_cooldown = True
            else:
                # 冷却期结束，自动恢复
                acc.cooldown_started_at = 0.0
                acc.consecutive_failures = 0

        if is_excluded:
            reason = "excluded"
        elif acc.activation_pending:
            reason = "pending_activation"
        elif not acc.valid:
            reason = acc.status_code if acc.status_code and acc.status_code != "valid" else "invalid"
        elif is_rate_limited:
            reason = "rate_limited"
        elif is_in_cooldown:
            reason = "cooldown"
        elif not capacity_available:
            reason = "busy"
        elif next_available_at > now:
            reason = "min_interval"
        else:
            reason = "ready\""""

if "is_in_cooldown" not in content:
    content = content.replace(old_diag_block, new_diag_block)
    print("account_pool.py: Added cooldown check to _account_diagnostic")
else:
    print("account_pool.py: _account_diagnostic already has cooldown check, skipping")

# Add cooldown fields to diagnostic return dict
old_return = """        return {
            "email": acc.email,
            "valid": acc.valid,
            "status_code": acc.get_status_code(),
            "status_text": acc.get_status_text(),
            "ready": reason == "ready",
            "selection_block_reason": reason,
            "inflight": acc.inflight,
            "max_inflight": self.max_inflight,
            "capacity_available": capacity_available,
            "is_rate_limited": is_rate_limited,
            "rate_limited_until": acc.rate_limited_until,
            "next_available_at": next_available_at,
            "next_available_in": next_available_in,
            "last_used": acc.last_used,
            "last_request_started": acc.last_request_started,
            "last_request_finished": acc.last_request_finished,
        }"""

new_return = """        return {
            "email": acc.email,
            "valid": acc.valid,
            "status_code": acc.get_status_code(),
            "status_text": acc.get_status_text(),
            "ready": reason == "ready",
            "selection_block_reason": reason,
            "inflight": acc.inflight,
            "max_inflight": self.max_inflight,
            "capacity_available": capacity_available,
            "is_rate_limited": is_rate_limited,
            "is_in_cooldown": is_in_cooldown,
            "rate_limited_until": acc.rate_limited_until,
            "cooldown_started_at": acc.cooldown_started_at,
            "cooldown_ends_at": acc.cooldown_started_at + getattr(settings, "ACCOUNT_COOLDOWN_PERIOD_SECONDS", 300) if acc.cooldown_started_at > 0 else None,
            "next_available_at": next_available_at,
            "next_available_in": next_available_in,
            "last_used": acc.last_used,
            "last_request_started": acc.last_request_started,
            "last_request_finished": acc.last_request_finished,
        }"""

if '"is_in_cooldown"' not in content:
    content = content.replace(old_return, new_return)
    print("account_pool.py: Added cooldown fields to diagnostic return dict")
else:
    print("account_pool.py: diagnostic return dict already has cooldown fields, skipping")

# --- 3.2.6: mark_invalid — Trigger cooldown ---
old_mark_invalid = """    def mark_invalid(self, acc: Account, reason: str = "invalid", error_message: str = ""):
        acc.valid = False
        acc.status_code = reason or "invalid"
        acc.last_error = error_message or acc.last_error
        acc.consecutive_failures += 1
        if reason == "pending_activation":
            acc.activation_pending = True
        if self._sticky_email == acc.email:
            self._sticky_email = None
        log.warning(f"[账号] {acc.email} 已标记为不可用，状态={acc.status_code}")"""

new_mark_invalid = """    def mark_invalid(self, acc: Account, reason: str = "invalid", error_message: str = ""):
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
            log.warning(f"[账号] {acc.email} 失败次数达上限({max_failures})，进入冷却期")"""

if "失败次数达上限" not in content:
    content = content.replace(old_mark_invalid, new_mark_invalid)
    print("account_pool.py: Added cooldown trigger to mark_invalid()")
else:
    print("account_pool.py: mark_invalid already has cooldown trigger, skipping")

# --- 3.2.7: acquire() — Strategy-based account selection ---
old_acquire = '''    async def acquire(self, exclude: set = None) -> Optional[Account]:
        strategy = "least_loaded"
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

            ready.sort(key=lambda a: (
                a.inflight,
                a.last_request_started or 0.0,
                a.last_used or 0.0,
                a.email or "",
            ))
            best = ready[0]
            best.inflight += 1
            best.last_used = now
            best.last_request_started = now + _jitter_seconds()
            self._sticky_email = best.email if len(ready) == 1 else None
            self._record_acquire_diagnostics(
                strategy=strategy,
                selected_email=best.email,
                diagnostics=diagnostics,
                now=now,
                exclude=exclude,
            )
            log.info("[账号池] acquire_selected strategy=%s email=%s ready=%s", strategy, best.email, self.last_acquire_diagnostics["ready_count"])
            return best'''

new_acquire = '''    async def acquire(self, exclude: set = None) -> Optional[Account]:
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

            if strategy == "least_loaded":
                # 按负载排序，选最少负载的
                ready.sort(key=lambda a: (
                    a.inflight,
                    a.last_request_started or 0.0,
                    a.last_used or 0.0,
                    a.email or "",
                ))
                best = ready[0]
            elif strategy == "least_used":
                # 最久未使用优先 — 均匀分配请求到所有账户
                ready.sort(key=lambda a: (a.last_used or 0.0, a.email or ""))
                best = ready[0]
            elif strategy == "round_robin":
                # 简单轮询 — 按顺序分配
                self._round_robin_index = self._round_robin_index % len(ready)
                best = ready[self._round_robin_index]
                self._round_robin_index += 1
            else:
                # 未知策略，fallback 到 least_loaded
                ready.sort(key=lambda a: (
                    a.inflight,
                    a.last_request_started or 0.0,
                    a.last_used or 0.0,
                    a.email or "",
                ))
                best = ready[0]

            best.inflight += 1
            best.last_used = now
            best.last_request_started = now + _jitter_seconds()
            self._sticky_email = best.email if len(ready) == 1 else None
            self._record_acquire_diagnostics(
                strategy=strategy,
                selected_email=best.email,
                diagnostics=diagnostics,
                now=now,
                exclude=exclude,
            )
            log.info("[账号池] acquire_selected strategy=%s email=%s ready=%s", strategy, best.email, self.last_acquire_diagnostics["ready_count"])
            return best'''

if 'getattr(settings, "ACCOUNT_SELECTION_STRATEGY"' not in content:
    content = content.replace(old_acquire, new_acquire)
    print("account_pool.py: Updated acquire() with strategy-based selection")
else:
    print("account_pool.py: acquire() already uses settings strategy, skipping")

# --- 3.2.8: status() — Add selection_strategy ---
old_status_return = '''        return {
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
            "next_ready_at": snapshot["next_ready_at"],
            "next_ready_in": snapshot["next_ready_in"],
            "last_acquire_diagnostics": self.last_acquire_diagnostics,
            "last_acquire_wait_diagnostics": self.last_acquire_wait_diagnostics,
        }'''

new_status_return = '''        return {
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
        }'''

if '"selection_strategy"' not in content:
    content = content.replace(old_status_return, new_status_return)
    print("account_pool.py: Added selection_strategy to status()")
else:
    print("account_pool.py: status() already has selection_strategy, skipping")

# Write back
with open(pool_path, "w", encoding="utf-8") as f:
    f.write(content)

print("\nAll Phase 3 modifications complete!")
