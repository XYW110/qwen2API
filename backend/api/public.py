from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel
from typing import Optional, Dict, List
import time
import logging

router = APIRouter()
log = logging.getLogger("backend.api.public")

# 频率限制存储
_rate_limit_tracker: Dict[str, List[float]] = {}

def verify_api_key(request: Request, authorization: str = Header(None)):
    """验证 API Key 有效性的依赖函数"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    token = authorization.split("Bearer ")[1]
    
    # 检查 API Key 是否存在于 api_key_manager 中
    if not request.app.state.api_key_manager.keys.get(token):
        raise HTTPException(status_code=403, detail="Forbidden: Invalid API Key")
    
    return token

class CreatePendingAccountRequest(BaseModel):
    """创建待审批账户请求模型"""
    email: str
    password: str
    proxy: Optional[str] = None
    cookies: Optional[str] = ""

def _check_rate_limit(api_key: str, limit: int = 10, window_seconds: int = 3600) -> bool:
    """检查 API Key 的频率限制"""
    now = time.time()
    cutoff = now - window_seconds
    
    # 获取该 API Key 的提交记录
    if api_key not in _rate_limit_tracker:
        _rate_limit_tracker[api_key] = []
    
    # 清理过期的记录
    timestamps = _rate_limit_tracker[api_key]
    timestamps = [t for t in timestamps if t > cutoff]
    timestamps.append(now)
    _rate_limit_tracker[api_key] = timestamps
    
    # 检查是否超过限制
    return len(timestamps) <= limit

@router.post("/public/pending-accounts")
async def create_pending_account(
    request: Request,
    account_data: CreatePendingAccountRequest,
    api_key: str = Depends(verify_api_key)
):
    """提交待审批账户"""
    
    # 验证必填字段
    if not account_data.email or not account_data.password:
        raise HTTPException(status_code=400, detail="Email and password are required")
    
    # 频率限制检查（过去1小时内最多10次提交）
    if not _check_rate_limit(api_key, limit=10, window_seconds=3600):
        raise HTTPException(
            status_code=429, 
            detail="Too Many Requests: Rate limit exceeded (10 submissions per hour)"
        )
    
    # 获取存储实例
    pending_store = request.app.state.pending_account_store
    account_pool = request.app.state.account_pool
    
    # 检查邮箱是否已存在于待审批队列
    existing_pending = pending_store.get_by_email(account_data.email)
    if existing_pending:
        raise HTTPException(
            status_code=409, 
            detail=f"Account with email {account_data.email} already exists in pending queue"
        )
    
    # 检查邮箱是否已存在于账号池
    existing_account = account_pool.get_by_email(account_data.email)
    if existing_account:
        raise HTTPException(
            status_code=409, 
            detail=f"Account with email {account_data.email} already exists"
        )
    
    # 创建待审批账户记录
    try:
        new_entry = await pending_store.create(
            email=account_data.email,
            password=account_data.password,
            submitted_by_api_key=api_key,
            proxy=account_data.proxy,
            cookies=account_data.cookies or ""
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # 返回创建结果
    return {
        "id": new_entry.id,
        "email": new_entry.email,
        "created_at": new_entry.created_at
    }