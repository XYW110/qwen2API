import asyncio
import logging
import uuid
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from backend.core.database import AsyncJsonDB

log = logging.getLogger("qwen2api.api_key_store")

# ============== 数据模型 ==============

class ApiKeyEntry(BaseModel):
    """API Key 基本信息"""
    key: str
    note: Optional[str] = None
    created_at: float = Field(default_factory=time.time)


class ApiKeyUsageEntry(BaseModel):
    """API Key 使用量统计（按模型）"""
    api_key: str
    model: str
    request_count: int = 0  # 包含成功和失败的请求
    total_tokens: int = 0   # 仅成功请求的 tokens


class ApiKeyFailureRecord(BaseModel):
    """API Key 失败记录"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    api_key: str
    account_email: str
    model: str
    timestamp: float = Field(default_factory=time.time)
    error_message: str


# ============== ApiKeyStore ==============

class ApiKeyStore:
    """管理 API Key 基本信息（key、note、created_at）"""
    
    DATA_FILE = "data/api_keys.json"
    LEGACY_FILE = "data/api_keys.json"  # 旧文件路径相同
    
    def __init__(self):
        self._db = AsyncJsonDB(self.DATA_FILE, default_data=[])
        self._lock = asyncio.Lock()
        self._entries: Dict[str, ApiKeyEntry] = {}  # key -> ApiKeyEntry
    
    async def load(self) -> None:
        """加载数据，支持从旧格式自动迁移"""
        try:
            data = await self._db.load()
            
            # 检查是否是旧格式: {"keys": [...]}
            if isinstance(data, dict) and "keys" in data:
                log.info("Detected legacy API keys format, migrating...")
                await self._migrate_from_legacy(data)
                return
            
            # 新格式: 列表
            if isinstance(data, list):
                self._entries = {}
                for item in data:
                    try:
                        entry = ApiKeyEntry(**item)
                        self._entries[entry.key] = entry
                    except Exception as e:
                        log.warning("Failed to parse API key entry: %s", e)
            else:
                log.warning("Unexpected API keys data format, starting fresh")
                self._entries = {}
        except Exception as e:
            log.warning("Failed to load API keys: %s", e)
            self._entries = {}
    
    async def _migrate_from_legacy(self, legacy_data: dict) -> None:
        """从旧格式迁移: {"keys": ["sk-xxx", ...]} -> [{"key": "sk-xxx", "note": null, "created_at": ...}]"""
        try:
            keys = legacy_data.get("keys", [])
            self._entries = {}
            now = time.time()
            
            for key in keys:
                if isinstance(key, str):
                    entry = ApiKeyEntry(
                        key=key,
                        note=None,
                        created_at=now
                    )
                    self._entries[key] = entry
            
            await self._save_to_disk()
            log.info("Migrated %d API keys from legacy format", len(self._entries))
        except Exception as e:
            log.error("Failed to migrate legacy API keys: %s", e)
            self._entries = {}
    
    async def _save_to_disk(self) -> None:
        """持久化到磁盘"""
        try:
            data = [entry.model_dump() for entry in self._entries.values()]
            await self._db.save(data)
        except Exception as e:
            log.warning("Failed to save API keys: %s", e)
    
    def get_all(self) -> List[ApiKeyEntry]:
        """获取所有 API Key 列表"""
        return list(self._entries.values())
    
    def get(self, key: str) -> Optional[ApiKeyEntry]:
        """获取单个 API Key"""
        return self._entries.get(key)
    
    async def create(self, key: str, note: Optional[str] = None) -> ApiKeyEntry:
        """创建新的 API Key"""
        async with self._lock:
            if key in self._entries:
                raise ValueError(f"API Key already exists: {key}")
            
            entry = ApiKeyEntry(
                key=key,
                note=note,
                created_at=time.time()
            )
            self._entries[key] = entry
            await self._save_to_disk()
            log.info("Created new API key: %s", key[:20] + "...")
            return entry
    
    async def delete(self, key: str) -> bool:
        """删除 API Key"""
        async with self._lock:
            if key not in self._entries:
                return False
            
            del self._entries[key]
            await self._save_to_disk()
            log.info("Deleted API key: %s", key[:20] + "...")
            return True
    
    async def update_note(self, key: str, note: Optional[str]) -> Optional[ApiKeyEntry]:
        """更新 API Key 的备注"""
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            
            entry.note = note
            await self._save_to_disk()
            log.info("Updated note for API key: %s", key[:20] + "...")
            return entry


# ============== ApiKeyUsageStore ==============

class ApiKeyUsageStore:
    """管理 API Key 使用量统计（按模型聚合）"""
    
    DATA_FILE = "data/api_key_stats.json"
    
    def __init__(self):
        self._db = AsyncJsonDB(self.DATA_FILE, default_data=[])
        self._lock = asyncio.Lock()
        # (api_key, model) -> ApiKeyUsageEntry
        self._entries: Dict[str, ApiKeyUsageEntry] = {}
    
    async def load(self) -> None:
        """加载使用量统计"""
        try:
            data = await self._db.load()
            if isinstance(data, list):
                self._entries = {}
                for item in data:
                    try:
                        entry = ApiKeyUsageEntry(**item)
                        key = self._make_key(entry.api_key, entry.model)
                        self._entries[key] = entry
                    except Exception as e:
                        log.warning("Failed to parse API key usage entry: %s", e)
            else:
                self._entries = {}
        except Exception as e:
            log.warning("Failed to load API key usage stats: %s", e)
            self._entries = {}
    
    async def _save_to_disk(self) -> None:
        """持久化到磁盘"""
        try:
            data = [entry.model_dump() for entry in self._entries.values()]
            await self._db.save(data)
        except Exception as e:
            log.warning("Failed to save API key usage stats: %s", e)
    
    def _make_key(self, api_key: str, model: str) -> str:
        """生成复合键"""
        return f"{api_key}::{model.lower().strip()}"
    
    def get_all(self) -> List[ApiKeyUsageEntry]:
        """获取所有使用量统计"""
        return list(self._entries.values())
    
    def get_by_api_key(self, api_key: str) -> List[ApiKeyUsageEntry]:
        """获取某个 API Key 的所有模型统计"""
        result = []
        for entry in self._entries.values():
            if entry.api_key == api_key:
                result.append(entry)
        return result
    
    async def record_usage(
        self,
        api_key: str,
        model: str,
        success: bool,
        tokens: int = 0
    ) -> None:
        """记录使用量
        
        Args:
            api_key: API Key
            model: 模型名称
            success: 是否成功
            tokens: 消耗的 tokens（仅成功请求计入）
        """
        async with self._lock:
            key = self._make_key(api_key, model)
            entry = self._entries.get(key)
            
            if entry is None:
                entry = ApiKeyUsageEntry(
                    api_key=api_key,
                    model=model.lower().strip(),
                    request_count=0,
                    total_tokens=0
                )
                self._entries[key] = entry
            
            entry.request_count += 1
            if success and tokens > 0:
                entry.total_tokens += tokens
            
            await self._save_to_disk()
    
    async def delete_by_api_key(self, api_key: str) -> int:
        """删除某个 API Key 的所有统计记录"""
        async with self._lock:
            keys_to_delete = [
                key for key, entry in self._entries.items()
                if entry.api_key == api_key
            ]
            for key in keys_to_delete:
                del self._entries[key]
            
            if keys_to_delete:
                await self._save_to_disk()
            
            return len(keys_to_delete)


# ============== ApiKeyFailureStore ==============

class ApiKeyFailureStore:
    """管理 API Key 失败记录"""
    
    DATA_FILE = "data/api_key_failures.json"
    RETENTION_DAYS = 3  # 默认保留天数
    MAX_ERROR_LENGTH = 300  # 错误信息最大长度
    
    def __init__(self):
        self._db = AsyncJsonDB(self.DATA_FILE, default_data=[])
        self._lock = asyncio.Lock()
        self._entries: Dict[str, ApiKeyFailureRecord] = {}
    
    async def load(self) -> None:
        """加载失败记录"""
        try:
            data = await self._db.load()
            if isinstance(data, list):
                self._entries = {}
                for item in data:
                    try:
                        entry = ApiKeyFailureRecord(**item)
                        self._entries[entry.id] = entry
                    except Exception as e:
                        log.warning("Failed to parse API key failure record: %s", e)
            else:
                self._entries = {}
        except Exception as e:
            log.warning("Failed to load API key failures: %s", e)
            self._entries = {}
    
    async def _save_to_disk(self) -> None:
        """持久化到磁盘"""
        try:
            data = [entry.model_dump() for entry in self._entries.values()]
            await self._db.save(data)
        except Exception as e:
            log.warning("Failed to save API key failures: %s", e)
    
    def get_all(self) -> List[ApiKeyFailureRecord]:
        """获取所有失败记录（按时间倒序）"""
        return sorted(
            self._entries.values(),
            key=lambda x: x.timestamp,
            reverse=True
        )
    
    def get_by_api_key(self, api_key: str) -> List[ApiKeyFailureRecord]:
        """获取某个 API Key 的失败记录"""
        return [
            entry for entry in self._entries.values()
            if entry.api_key == api_key
        ]
    
    async def record(
        self,
        api_key: str,
        account_email: str,
        model: str,
        error_message: str
    ) -> ApiKeyFailureRecord:
        """记录失败
        
        Args:
            api_key: 使用的 API Key
            account_email: 使用的账户邮箱
            model: 请求的模型
            error_message: 错误信息（自动截断到 300 字符）
        """
        async with self._lock:
            # 截断错误信息
            if len(error_message) > self.MAX_ERROR_LENGTH:
                error_message = error_message[:self.MAX_ERROR_LENGTH] + "..."
            
            entry = ApiKeyFailureRecord(
                api_key=api_key,
                account_email=account_email,
                model=model,
                error_message=error_message
            )
            
            self._entries[entry.id] = entry
            await self._save_to_disk()
            log.debug("Recorded failure for API key %s: %s", api_key[:20], error_message[:50])
            return entry
    
    async def cleanup(self) -> int:
        """清理过期记录（默认保留3天）
        
        Returns:
            删除的记录数量
        """
        async with self._lock:
            cutoff_time = time.time() - (self.RETENTION_DAYS * 24 * 60 * 60)
            
            keys_to_delete = [
                key for key, entry in self._entries.items()
                if entry.timestamp < cutoff_time
            ]
            
            for key in keys_to_delete:
                del self._entries[key]
            
            if keys_to_delete:
                await self._save_to_disk()
                log.info("Cleaned up %d expired API key failure records", len(keys_to_delete))
            
            return len(keys_to_delete)
    
    async def clear_all(self) -> int:
        """一键清理所有记录
        
        Returns:
            删除的记录数量
        """
        async with self._lock:
            count = len(self._entries)
            self._entries.clear()
            await self._save_to_disk()
            log.info("Cleared all %d API key failure records", count)
            return count


# ============== 组合管理器 ==============

class ApiKeyManager:
    """API Key 管理组合器，方便统一管理"""
    
    def __init__(self):
        self.keys = ApiKeyStore()
        self.usage = ApiKeyUsageStore()
        self.failures = ApiKeyFailureStore()
    
    async def load(self) -> None:
        """加载所有数据"""
        await self.keys.load()
        await self.usage.load()
        await self.failures.load()
    
    async def delete_key(self, key: str) -> bool:
        """删除 API Key 及其相关统计"""
        success = await self.keys.delete(key)
        if success:
            # 删除相关统计数据
            await self.usage.delete_by_api_key(key)
            # 注意：失败记录保留，因为可能与历史审计有关
        return success
    
    async def run_cleanup(self) -> int:
        """运行清理任务"""
        return await self.failures.cleanup()
