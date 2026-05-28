# ChatIdPool 预加载机制原理与实现

## 1. 概述

**ChatIdPool** 是一个 chat_id 预热池组件，用于消除每次 API 请求时调用 `/api/v2/chats/new` 创建会话的网络握手开销。

- **问题**：每次请求前需同步调用上游接口创建 `chat_id`，耗时 **500ms ~ 6s**（视网络抖动而定）。
- **方案**：服务启动后，在后台为每个可用账号预先创建若干 `chat_id` 放入队列。请求到来时直接从队列 `pop` 一个，实现 **零等待** 获取会话 ID。
- **收益**：常规请求延迟降低 500~3000ms，极端抖动场景节省 5~6s。

---

## 2. 核心工作原理

### 2.1 生命周期流程

```text
服务启动
  └─▶ ChatIdPool.start()
       ├─ 延迟 2 秒（等待账号池加载完成）
       └─ 启动后台补位循环 _refill_loop() (每 30 秒一轮)
            └─ 遍历所有 valid 账号
                 └─ 若队列大小 < target → 调用 _prewarm_one() 补位

请求到达
  └─▶ QwenExecutor 处理
       ├─ 调用 chat_id_pool.acquire(email)
       │    ├─ HIT: 从队列 pop 一个未过期的 chat_id → 直接使用
       │    └─ MISS: 队列空或全部过期 → 返回 None
       └─ 若返回 None → Fallback 到同步 create_chat() (保证可用性)

后台循环 (持续运行)
  └─ 每 30 秒检查一次各账号队列
       └─ 发现 deficit (不足 target) → 补位 1 个
```

### 2.2 关键策略

| 策略 | 说明 |
|------|------|
| **TTL 过期淘汰** | 每个 `chat_id` 记录创建时间。`acquire()` 时检查是否超过 `ttl_seconds`（默认 1800s / 30分钟），过期则丢弃并继续取下一个。 |
| **按需补位** | 后台循环每 30 秒检查一次，仅当队列大小 `< target` 时才补位。 |
| **风控限流** | 每轮每账号**最多补 1 个**，避免短时间内大量调用上游 API 触发风控。 |
| **Graceful Fallback** | 池空或过期时返回 `None`，调用方自动降级到同步创建，**绝不阻塞请求**。 |
| **协程安全** | 所有队列操作均通过 `asyncio.Lock` 保护，避免并发读写冲突。 |

---

## 3. 数据结构

### 3.1 池条目 `_Entry`

```python
class _Entry:
    __slots__ = ("chat_id", "created_at")

    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        self.created_at = time.time()  # 用于 TTL 过期检查
```

### 3.2 池核心 `ChatIdPool`

```python
class ChatIdPool:
    _queues: dict[str, deque[_Entry]]  # 按 email 分组的 chat_id 队列
    _target: int                        # 每账号预热目标数量 (默认 3)
    _ttl: float                         # chat_id 存活秒数 (默认 1800)
    _default_model: str                 # 预热时使用的模型名称 (默认 "qwen3.6-plus")
    _lock: asyncio.Lock                 # 协程安全锁
    _refill_task: Optional[asyncio.Task] # 后台补位任务
```

---

## 4. 关键代码解析

### 4.1 获取 chat_id — `acquire()`

请求到达时的核心入口。优先从池中取，过期自动淘汰。

```python
async def acquire(self, email: str, model: str | None = None) -> Optional[str]:
    """优先从预热池取 chat_id；池空或过期则返回 None（调用方走同步 create_chat）。"""
    if not email:
        return None
    async with self._lock:
        q = self._queues.get(email)
        if not q:
            return None
        now = time.time()
        while q:
            entry = q.popleft()
            # TTL 检查：未过期则命中返回
            if now - entry.created_at < self._ttl:
                log.debug(f"[ChatIdPool] HIT email={email} chat_id={entry.chat_id}")
                return entry.chat_id
            # 已过期，丢弃并继续取下一个
            log.debug(f"[ChatIdPool] expired chat_id={entry.chat_id} email={email}")
    return None  # 池空或全部过期 → 触发 Fallback
```

### 4.2 预热单个账号 — `_prewarm_one()`

调用上游 API 创建 chat_id 并放入队列。

```python
async def _prewarm_one(self, account, model: str) -> None:
    """为某账号预建一个 chat_id 加入队列。"""
    try:
        token = account.token
        email = account.email
        if not token:
            log.warning(f"[ChatIdPool] prewarm skipped email={email}: missing token")
            return
        # 调用上游 API 创建 chat_id
        chat_id = await self._client.create_chat(token, model)
        async with self._lock:
            q = self._queues.setdefault(email, deque())
            q.append(_Entry(chat_id))
        log.info(f"[ChatIdPool] prewarmed email={email} chat_id={chat_id} pool_size={len(q)}")
    except Exception as e:
        err = str(e) or type(e).__name__
        log.warning(f"[ChatIdPool] prewarm failed email={getattr(account, 'email', '?')}: {err}")
```

### 4.3 后台补位循环 — `_refill_loop()` & `_refill_once()`

持续运行的后台任务，维持池中 chat_id 数量。

```python
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

async def _refill_once(self) -> None:
    """遍历账号池里所有 valid 账号，每个不足 target 就补位。"""
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
        async with self._lock:
            q_size = len(self._queues.get(acc.email, []))
        deficit = self._target - q_size
        # ⚡ 风控保护：每轮每账号最多补 1 个，避免突发 API 压力
        if deficit > 0:
            await self._prewarm_one(acc, self._default_model)
```

### 4.4 统计信息 — `stats()`

供管理接口 (`GET /api/admin/status`) 返回池状态。

```python
async def stats(self) -> dict:
    """返回当前池的统计信息，供 admin API 使用。"""
    async with self._lock:
        per_account = {email: len(q) for email, q in self._queues.items()}
        total = sum(per_account.values())
    return {
        "total_cached": total,
        "target_per_account": self._target,
        "ttl_seconds": self._ttl,
        "per_account": per_account,
    }
```

---

## 5. 系统集成点

### 5.1 服务启动与关闭 (`backend/main.py`)

在 FastAPI lifespan 中初始化和清理：

```python
# 启动阶段
from backend.services.chat_id_pool import ChatIdPool
chat_id_pool = ChatIdPool(app.state.qwen_client)
await chat_id_pool.start()
app.state.chat_id_pool = chat_id_pool
app.state.qwen_executor.chat_id_pool = chat_id_pool  # 注入到执行器

# 关闭阶段
finally:
    await chat_id_pool.stop()
```

### 5.2 执行器调用点 (`backend/upstream/qwen_executor.py`)

在两处需要创建 chat_id 的地方，优先从池获取：

```python
if existing_chat_id:
    chat_id = existing_chat_id
elif self.chat_id_pool:
    chat_id = await self.chat_id_pool.acquire(acc.email, model)
    if chat_id:
        log.info(f"[Executor] got chat_id from pool email={acc.email}")
    else:
        chat_id = await self.create_chat(acc.token, model)  # Fallback
else:
    chat_id = await self.create_chat(acc.token, model)
```

### 5.3 管理接口 (`backend/api/admin.py`)

- **`GET /api/admin/status`**：返回 `chat_id_pool.stats()` 和 `per_account` 诊断数据。
- **`GET /api/admin/settings`**：返回 `chat_id_pool_target` 和 `chat_id_pool_ttl_seconds`。
- **`PUT /api/admin/settings`**：支持运行时热更新 `_target` 和 `_ttl`。

---

## 6. 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `target_per_account` | `3` | 每个账号预热的 chat_id 数量。降低可减少上游 API 调用频率，但命中率可能下降。 |
| `ttl_seconds` | `1800` (30分钟) | chat_id 的有效时长。过期后 acquire() 自动丢弃并触发后台补位。 |
| `default_model` | `"qwen3.6-plus"` | 预热时使用的模型名称。请求实际模型不同时仍可使用该 chat_id（上游支持模型切换）。 |

---

## 7. 注意事项

1. **模型兼容性**：预热使用固定模型 `default_model`。如果上游 API 不允许跨模型复用 chat_id，需扩展 `acquire()` 支持按模型筛选。
2. **风控平衡**：`target=3` + 每轮补 1 个是经验值。若账号数较多且上游限流严格，可进一步降低 target 或延长 refill interval。
3. **内存占用**：每个 `_Entry` 仅存储字符串和时间戳，`__slots__` 优化后内存开销极小，无需担心池膨胀。
4. **故障恢复**：`_prewarm_one()` 捕获所有异常并记录日志，单个账号预热失败不影响其他账号和服务正常运行。
