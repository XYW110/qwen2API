"""Chat ID 预热池：预先为每个可用账号创建若干 chat_id 放在队列里，
请求到来时直接从队列 pop 一个省去 /chats/new 握手（实测 500ms~6s 不等）。

典型收益：每次请求节省 500~3000ms 握手时延；最坏情况抖动时节省 5~6s。

工作流：
  - 服务启动 → 每账号预建 target_per_account 个 chat_id
  - 请求用掉一个 chat_id → 后台立即补位一个
  - 每账号池大小上限：target_per_account (默认 3)
  - chat_id 有 TTL (默认 10 分钟)，超时背景任务丢弃+重建
  - 请求取不到预热 chat_id 时：fallback 到同步 create_chat（当前行为）
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from typing import Optional

log = logging.getLogger("qwen2api.chat_pool")


class _Entry:
    __slots__ = ("chat_id", "created_at")

    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        self.created_at = time.time()


class ChatIdPool:
    """按 (email, model) 双维度隔离的 chat_id 队列。协程安全。"""

    def __init__(
        self,
        client,
        *,
        target_per_account: int = 3,
        ttl_seconds: float = 10,
        prewarm_models: list[str] | None = None,
    ):
        self._client = client
        self._target = target_per_account
        self._ttl = ttl_seconds
        self._prewarm_models: list[str] = list(prewarm_models) if prewarm_models else []
        self._queues: dict[tuple[str, str], deque[_Entry]] = {}
        self._lock = asyncio.Lock()
        self._refill_task: Optional[asyncio.Task] = None
        self._shutdown = False
        self._hit_count = 0
        self._miss_count = 0
        self._error_count = 0
        self._model_failure_counts: dict[str, int] = {}  # model -> 连续失败次数
        self.MAX_CONSECUTIVE_FAILURES = 3
        self._last_config_mtime: float = 0.0
        self._max_total_prewarm: int = 10
        
        # 全局失败熔断机制（防止上游异常时频繁重试）
        self._global_failure_count: int = 0
        self._global_failure_cooldown_until: float = 0.0
        self.GLOBAL_FAILURE_THRESHOLD: int = 10  # 连续失败次数阈值
        self.GLOBAL_FAILURE_COOLDOWN_SECONDS: float = 60.0  # 冷却时间（秒）

    @property
    def target(self) -> int:
        return self._target

    @property
    def ttl(self) -> float:
        return self._ttl

    @property
    def max_total_prewarm(self) -> int:
        return self._max_total_prewarm

    def update_config(self, *, target: int | None = None, ttl_seconds: float | None = None) -> None:
        """运行时热更新参数。"""
        if target is not None:
            self._target = max(0, int(target))
        if ttl_seconds is not None:
            self._ttl = max(30.0, float(ttl_seconds))
        log.info(f"[ChatIdPool] config updated target={self._target} ttl={self._ttl}s")

    async def start(self) -> None:
        """服务启动时调用，完成首轮预热 + 启动后台补位 loop。"""
        self._refill_task = asyncio.create_task(self._refill_loop())
        log.info(f"[ChatIdPool] started (target={self._target}, ttl={self._ttl}s, prewarm_models={self._prewarm_models})")

    async def stop(self) -> None:
        self._shutdown = True
        if self._refill_task:
            self._refill_task.cancel()
            try:
                await self._refill_task
            except (asyncio.CancelledError, Exception):
                pass

    async def acquire(self, email: str, model: str | None = None) -> Optional[str]:
        """按 (email, model) 精确匹配从预热池取 chat_id；池空或过期则返回 None。"""
        if not email or not model:
            self._miss_count += 1
            return None
        key = (email, model)
        async with self._lock:
            q = self._queues.get(key)
            if not q:
                self._miss_count += 1
                return None
            now = time.time()
            while q:
                entry = q.popleft()
                if now - entry.created_at < self._ttl:
                    log.debug(f"[ChatIdPool] HIT key={key} chat_id={entry.chat_id}")
                    self._hit_count += 1
                    return entry.chat_id
                log.debug(f"[ChatIdPool] expired chat_id={entry.chat_id} key={key}")
        self._miss_count += 1
        return None

    def record_error(self) -> None:
        """记录一次从池中取出的 chat_id 使用失败（stream 阶段出错）。"""
        self._error_count += 1
        log.warning(f"[ChatIdPool] chat_id usage error, total_errors={self._error_count}")

    async def _prewarm_one(self, account, model: str) -> None:
        """为某账号按模型预建一个 chat_id 加入队列。"""
        try:
            token = account.token
            email = account.email
            if not token:
                log.warning(f"[ChatIdPool] prewarm skipped email={email}: missing token")
                return
            key = (email, model)
            chat_id = await self._client.create_chat(token, model)
            async with self._lock:
                q = self._queues.setdefault(key, deque())
                q.append(_Entry(chat_id))
            log.info(f"[ChatIdPool] prewarmed key={key} chat_id={chat_id} pool_size={len(q)}")
            self._model_failure_counts.pop(model, None)
            # 成功时重置全局失败计数
            self._global_failure_count = 0
        except Exception as e:
            self._model_failure_counts[model] = self._model_failure_counts.get(model, 0) + 1
            # 累加全局失败计数，触发熔断检查
            self._global_failure_count += 1
            if self._global_failure_count >= self.GLOBAL_FAILURE_THRESHOLD:
                import time as _time
                self._global_failure_cooldown_until = _time.time() + self.GLOBAL_FAILURE_COOLDOWN_SECONDS
                log.warning(f"[ChatIdPool] global failure threshold reached ({self._global_failure_count}), cooling down for {self.GLOBAL_FAILURE_COOLDOWN_SECONDS}s")
            err = str(e) or type(e).__name__
            log.warning(f"[ChatIdPool] prewarm failed email={getattr(account, 'email', '?')}: {err}")

    async def _refill_loop(self) -> None:
        """定期轮询：每账号池低于 target 则补位。30 秒一轮。"""
        interval = 30.0
        # 初始化后短暂延迟再跑首轮，确保账号池已加载
        await asyncio.sleep(2.0)
        while not self._shutdown:
            try:
                await self._refill_once()
            except Exception as e:
                log.warning(f"[ChatIdPool] refill error: {e}")
            await asyncio.sleep(interval)

    async def _reload_config(self) -> None:
        """检查配置文件是否更新，若有变化则重新加载 prewarm_models。"""
        try:
            from backend.core.config import load_prewarm_config, PREWARM_CONFIG_FILE
            if not PREWARM_CONFIG_FILE.exists():
                return
            mtime = PREWARM_CONFIG_FILE.stat().st_mtime
            if mtime <= self._last_config_mtime:
                return
            self._last_config_mtime = mtime
            cfg = load_prewarm_config()
            new_models = cfg.get("prewarm_models", [])
            if new_models and set(new_models) != set(self._prewarm_models):
                old = set(self._prewarm_models)
                self._prewarm_models = new_models
                log.info(f"[ChatIdPool] config reloaded: prewarm_models {old} -> {set(new_models)}")
        except Exception as e:
            log.warning(f"[ChatIdPool] config reload failed: {e}")

    async def _refill_once(self) -> None:
        """遍历账号池里所有 valid 账号，每账号总计不足 target 则随机选模型补位。"""
        if not self._prewarm_models:
            return
        
        # 全局失败熔断检查
        import time as _time
        if self._global_failure_cooldown_until > 0 and _time.time() < self._global_failure_cooldown_until:
            remaining = self._global_failure_cooldown_until - _time.time()
            log.debug(f"[ChatIdPool] in cooldown period, {remaining:.1f}s remaining")
            return
        
        await self._reload_config()
        # 过滤掉连续失败过多的模型
        available_models = [
            m for m in self._prewarm_models
            if self._model_failure_counts.get(m, 0) < self.MAX_CONSECUTIVE_FAILURES
        ]
        if not available_models:
            return
        pool = getattr(self._client, "account_pool", None)
        if pool is None:
            return
        all_accounts = getattr(pool, "accounts", []) or []
        # 只对有 token + 状态 valid 的账号预热
        valid = [
            a for a in all_accounts
            if getattr(a, "token", "") and getattr(a, "status_code", "valid") == "valid"
        ]
        for acc in valid:
            # 检查全局预热总数上限
            async with self._lock:
                current_total = sum(len(q) for q in self._queues.values())
            if current_total >= self._max_total_prewarm:
                log.debug(f"[ChatIdPool] total prewarm {current_total} >= limit {self._max_total_prewarm}, skip refill")
                return
            # 统计该账号所有模型的 chat_id 总数
            async with self._lock:
                acc_total = sum(
                    len(q) for key, q in self._queues.items()
                    if key[0] == acc.email
                )
            deficit = self._target - acc_total
            # 每轮每账号最多补 1 个，随机选模型
            if deficit > 0:
                model = random.choice(available_models)
                await self._prewarm_one(acc, model)

    async def invalidate(self, email: str, chat_id: str) -> None:
        """遍历该 email 所有模型队列，移除匹配的 chat_id。"""
        if not email or not chat_id:
            return
        async with self._lock:
            removed = False
            for key in list(self._queues):
                if key[0] != email:
                    continue
                q = self._queues[key]
                remaining = deque(e for e in q if e.chat_id != chat_id)
                if len(remaining) != len(q):
                    self._queues[key] = remaining
                    removed = True
            if removed:
                log.info(f"[ChatIdPool] invalidated email={email} chat_id={chat_id}")

    async def flush_account(self, email: str) -> int:
        """清空该 email 所有模型的池。返回清理数量。"""
        if not email:
            return 0
        async with self._lock:
            n = 0
            keys_to_flush = [k for k in self._queues if k[0] == email]
            for key in keys_to_flush:
                n += len(self._queues.pop(key))
            if n:
                log.info(f"[ChatIdPool] flushed {n} entries for email={email}")
        return n

    async def size(self, email: str) -> int:
        async with self._lock:
            return sum(len(q) for key, q in self._queues.items() if key[0] == email)

    async def total_size(self) -> int:
        async with self._lock:
            return sum(len(q) for q in self._queues.values())

    async def stats(self) -> dict:
        """返回当前池的统计信息，供 admin API 使用。"""
        async with self._lock:
            per_account = {f"{email}:{model}": len(q) for (email, model), q in self._queues.items()}
            total = sum(len(q) for q in self._queues.values())
            hit = self._hit_count
            miss = self._miss_count
        total_acquires = hit + miss
        hit_rate = (hit / total_acquires * 100) if total_acquires > 0 else 0.0
        return {
            "total_cached": total,
            "target_per_account": self._target,
            "ttl_seconds": self._ttl,
            "per_account": per_account,
            "hit_count": hit,
            "miss_count": miss,
            "error_count": self._error_count,
            "hit_rate": round(hit_rate, 1),
        }