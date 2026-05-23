import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.core.database import AsyncJsonDB

log = logging.getLogger("qwen2api.account_stats")

# EMA smoothing factor: 0.3 for new value, 0.7 for historical
EMA_ALPHA = 0.3
MAX_HOURLY_USAGE_ENTRIES = 168  # 7 days * 24 hours


class ModelStats(BaseModel):
    tok_s_ema: float = 0.0
    total_tokens: int = 0
    request_count: int = 0


class HourlyUsage(BaseModel):
    hour_key: str  # ISO format: YYYY-MM-DDTHH:00:00Z
    prompt_tokens: int = 0
    completion_tokens: int = 0


class AccountStatEntry(BaseModel):
    email: str
    model_stats: Dict[str, ModelStats] = Field(default_factory=dict)
    hourly_usage: List[HourlyUsage] = Field(default_factory=list)


def _normalize_model(model: str) -> str:
    """Normalize model name: strip whitespace and lowercase."""
    return model.strip().lower()


def _current_hour_key() -> str:
    """Return current UTC hour key in ISO format."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:00:00Z")


class AccountStatsStore:
    """Independent statistics storage for accounts, separated from accounts.json
    to prevent data corruption from high-frequency writes."""

    def __init__(self, path: str = "data/account_stats.json"):
        self._db = AsyncJsonDB(path, default_data=[])
        self._lock = asyncio.Lock()
        self._entries: Dict[str, AccountStatEntry] = {}

    async def load(self) -> None:
        """Load stats from file."""
        try:
            data = await self._db.load()
            if isinstance(data, list):
                self._entries = {}
                for item in data:
                    try:
                        entry = AccountStatEntry(**item)
                        self._entries[entry.email] = entry
                    except Exception as e:
                        log.warning("Failed to parse account stat entry: %s", e)
            elif isinstance(data, dict):
                self._entries = {}
                for email, item in data.items():
                    try:
                        if isinstance(item, dict):
                            item["email"] = email
                        entry = AccountStatEntry(**item)
                        self._entries[entry.email] = entry
                    except Exception as e:
                        log.warning("Failed to parse account stat entry for %s: %s", email, e)
        except Exception as e:
            log.warning("Failed to load account stats: %s", e)
            self._entries = {}

    async def save(self) -> None:
        """Persist stats to file."""
        try:
            data = [entry.model_dump() for entry in self._entries.values()]
            await self._db.save(data)
        except Exception as e:
            log.warning("Failed to save account stats: %s", e)

    def get_by_email(self, email: str) -> Optional[AccountStatEntry]:
        """Get stats entry by email."""
        return self._entries.get(email)

    async def update_tok_s(self, email: str, model: str, tokens: int, elapsed_seconds: float) -> None:
        """Update EMA tok/s for specific model.
        EMA formula: new_value = old_value * 0.7 + current_tok_s * 0.3
        """
        try:
            if elapsed_seconds <= 0 or tokens <= 0:
                return

            model = _normalize_model(model)
            current_tok_s = tokens / elapsed_seconds

            async with self._lock:
                entry = self._entries.get(email)
                if entry is None:
                    entry = AccountStatEntry(email=email)
                    self._entries[email] = entry

                stats = entry.model_stats.get(model)
                if stats is None:
                    stats = ModelStats(tok_s_ema=current_tok_s, total_tokens=tokens, request_count=1)
                    entry.model_stats[model] = stats
                else:
                    if stats.tok_s_ema > 0:
                        stats.tok_s_ema = stats.tok_s_ema * (1 - EMA_ALPHA) + current_tok_s * EMA_ALPHA
                    else:
                        stats.tok_s_ema = current_tok_s
                    stats.total_tokens += tokens
                    stats.request_count += 1

                await self.save()
        except Exception as e:
            log.warning("Failed to update tok_s for %s/%s: %s", email, model, e)

    async def record_usage(self, email: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        """Record hourly usage, aggregate by hour key (YYYY-MM-DDTHH:00:00Z format)."""
        try:
            model = _normalize_model(model)
            hour_key = _current_hour_key()

            async with self._lock:
                entry = self._entries.get(email)
                if entry is None:
                    entry = AccountStatEntry(email=email)
                    self._entries[email] = entry

                # Find existing hourly entry or create new one
                existing = None
                for usage in entry.hourly_usage:
                    if usage.hour_key == hour_key:
                        existing = usage
                        break

                if existing:
                    existing.prompt_tokens += prompt_tokens
                    existing.completion_tokens += completion_tokens
                else:
                    entry.hourly_usage.append(HourlyUsage(
                        hour_key=hour_key,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                    ))

                # Trim to last 168 entries (7 days)
                if len(entry.hourly_usage) > MAX_HOURLY_USAGE_ENTRIES:
                    entry.hourly_usage = entry.hourly_usage[-MAX_HOURLY_USAGE_ENTRIES:]

                await self.save()
        except Exception as e:
            log.warning("Failed to record usage for %s/%s: %s", email, model, e)

    async def migrate_from_legacy(self, accounts_data: List[Dict[str, Any]]) -> None:
        """Idempotent migration from old accounts.json format.
        Only migrate if target entry doesn't already have data.
        """
        try:
            async with self._lock:
                migrated = 0
                for acc in accounts_data:
                    email = acc.get("email", "")
                    if not email:
                        continue

                    # Skip if already has data
                    if email in self._entries:
                        existing = self._entries[email]
                        if existing.model_stats or existing.hourly_usage:
                            continue

                    tok_s = float(acc.get("tok_s", 0.0) or 0.0)
                    if tok_s <= 0:
                        continue

                    entry = AccountStatEntry(email=email)
                    # Migrate tok_s as a generic model stat since legacy doesn't track per-model
                    entry.model_stats["_legacy"] = ModelStats(
                        tok_s_ema=tok_s,
                        total_tokens=0,
                        request_count=0,
                    )
                    self._entries[email] = entry
                    migrated += 1

                if migrated > 0:
                    log.info("Migrated %d account(s) stats from legacy format", migrated)
                    await self.save()
        except Exception as e:
            log.warning("Failed to migrate legacy account stats: %s", e)
