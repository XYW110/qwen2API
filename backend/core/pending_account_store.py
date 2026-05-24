import asyncio
import logging
import uuid
import time
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from backend.core.database import AsyncJsonDB

log = logging.getLogger("qwen2api.pending_account_store")

# ============== 数据模型 ==============

class PendingAccountEntry(BaseModel):
    """待审批账户信息"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: str
    password: str
    proxy: Optional[str] = None
    cookies: Optional[str] = ""
    submitted_by_api_key: str
    created_at: float = Field(default_factory=time.time)


# ============== PendingAccountStore ==============

class PendingAccountStore:
    """管理待审批账户"""
    
    DATA_FILE = "data/pending_accounts.json"
    
    def __init__(self):
        self._db = AsyncJsonDB(self.DATA_FILE, default_data=[])
        self._lock = asyncio.Lock()
        self._entries: Dict[str, PendingAccountEntry] = {}
    
    async def load(self) -> None:
        """加载数据"""
        try:
            data = await self._db.load()
            
            if isinstance(data, list):
                self._entries = {}
                for item in data:
                    try:
                        entry = PendingAccountEntry(**item)
                        self._entries[entry.id] = entry
                    except Exception as e:
                        log.warning("Failed to parse pending account entry: %s", e)
            else:
                log.warning("Unexpected pending accounts data format, starting fresh")
                self._entries = {}
        except Exception as e:
            log.warning("Failed to load pending accounts: %s", e)
            self._entries = {}
    
    async def _save_to_disk(self) -> None:
        """持久化到磁盘"""
        try:
            data = [entry.model_dump() for entry in self._entries.values()]
            await self._db.save(data)
        except Exception as e:
            log.warning("Failed to save pending accounts: %s", e)
    
    def get_all(self) -> List[PendingAccountEntry]:
        """获取所有待审批账户"""
        return list(self._entries.values())
    
    def get_by_id(self, id: str) -> Optional[PendingAccountEntry]:
        """按 ID 获取待审批账户"""
        return self._entries.get(id)
    
    def get_by_email(self, email: str) -> Optional[PendingAccountEntry]:
        """按邮箱查找待审批账户"""
        for entry in self._entries.values():
            if entry.email == email:
                return entry
        return None
    
    async def create(
        self,
        email: str,
        password: str,
        submitted_by_api_key: str,
        proxy: Optional[str] = None,
        cookies: Optional[str] = ""
    ) -> PendingAccountEntry:
        """创建待审批账户"""
        async with self._lock:
            # 检查邮箱是否已存在
            existing = self.get_by_email(email)
            if existing:
                raise ValueError(f"Pending account already exists for email: {email}")
            
            entry = PendingAccountEntry(
                email=email,
                password=password,
                proxy=proxy,
                cookies=cookies or "",
                submitted_by_api_key=submitted_by_api_key,
                created_at=time.time()
            )
            
            self._entries[entry.id] = entry
            await self._save_to_disk()
            log.info("Created pending account for email: %s", email)
            return entry
    
    async def delete(self, id: str) -> bool:
        """删除待审批账户"""
        async with self._lock:
            if id not in self._entries:
                return False
            
            entry = self._entries[id]
            del self._entries[id]
            await self._save_to_disk()
            log.info("Deleted pending account: %s", entry.email)
            return True
