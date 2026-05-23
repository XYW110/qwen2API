from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel
from backend.core.config import settings
from backend.core.database import AsyncJsonDB
from backend.core.account_pool import AccountPool, Account
import secrets
import re

# 代理 URL 校验正则
_PROXY_URL_RE = re.compile(r"^(https?|socks5)://\S+$", re.IGNORECASE)

def _is_valid_proxy_url(url: str | None) -> bool:
    """空值视为合法（表示无代理），非空必须匹配协议格式"""
    if not url:
        return True
    return bool(_PROXY_URL_RE.match(url.strip()))

def _parse_account_line(line: str) -> dict | None:
    """
    解析单行账号文本
    支持格式: email:password 或 email:password|proxy_url
    使用 find 而非 split，避免密码中包含 ':' 时截断
    """
    if not isinstance(line, str):
        return None
    trimmed = line.strip()
    if not trimmed:
        return None

    # 先按第一个 '|' 切出可选 proxy
    pipe_idx = trimmed.find("|")
    if pipe_idx == -1:
        credentials = trimmed
        proxy = None
    else:
        credentials = trimmed[:pipe_idx]
        proxy = trimmed[pipe_idx + 1:].strip() or None

    # credentials 部分按第一个 ':' 切分
    colon_idx = credentials.find(":")
    if colon_idx == -1:
        return None

    email = credentials[:colon_idx].strip()
    password = credentials[colon_idx + 1:].strip()

    if not email or not password:
        return None

    return {"email": email, "password": password, "proxy": proxy}

def _parse_batch_accounts_text(text: str) -> dict:
    """
    解析批量账号文本
    Returns: {"lines": [...], "parsed": [...], "invalid_count": int}
    """
    normalized = str(text).replace("\r", "\n")
    lines = [ln.strip() for ln in normalized.split("\n") if ln.strip()]

    parsed = []
    invalid_count = 0

    for line in lines:
        result = _parse_account_line(line)
        if not result:
            invalid_count += 1
            continue
        # proxy 格式校验
        if result["proxy"] and not _is_valid_proxy_url(result["proxy"]):
            invalid_count += 1
            continue
        parsed.append(result)

    return {"lines": lines, "parsed": parsed, "invalid_count": invalid_count}

router = APIRouter()

def verify_admin(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split("Bearer ")[1]

    from backend.core.config import API_KEYS, settings as backend_settings

    # 允许使用默认管理员 Key (ADMIN_KEY) 或者任何已生成的 API_KEYS 作为管理凭证
    if token != backend_settings.ADMIN_KEY and token not in API_KEYS:
        raise HTTPException(status_code=403, detail="Forbidden: Admin Key Mismatch")
    return token

class UserCreate(BaseModel):
    name: str
    quota: int = 1000000

class User(BaseModel):
    id: str
    name: str
    quota: int
    used_tokens: int

@router.get("/status", dependencies=[Depends(verify_admin)])
async def get_system_status(request: Request):
    pool = request.app.state.account_pool

    return {
        "accounts": pool.status(),
        "request_runtime": {
            "mode": "direct_http",
            "browser_required_for_requests": False,
            "description": "普通请求直连 HTTP，不经过浏览器",
        },
        "browser_automation": {
            "mode": "disabled",
            "available": False,
            "description": "轻量无浏览器镜像不包含注册/激活/刷新 Token 的浏览器自动化能力",
        }
    }

@router.get("/users", dependencies=[Depends(verify_admin)])
async def list_users(request: Request):
    db: AsyncJsonDB = request.app.state.users_db
    data = await db.get()
    return {"users": data}

@router.post("/users", dependencies=[Depends(verify_admin)])
async def create_user(user: UserCreate, request: Request):
    import uuid
    db: AsyncJsonDB = request.app.state.users_db
    data = await db.get()
    new_user = {
        "id": f"sk-{uuid.uuid4().hex}",
        "name": user.name,
        "quota": user.quota,
        "used_tokens": 0
    }
    data.append(new_user)
    await db.save(data)
    return new_user

@router.post("/accounts", dependencies=[Depends(verify_admin)])
async def add_account(request: Request):
    import time
    from backend.core.account_pool import Account, AccountPool
    from backend.services.qwen_client import QwenClient

    pool: AccountPool = request.app.state.account_pool
    client: QwenClient = request.app.state.qwen_client

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, detail="Invalid JSON body")

    token = data.get("token", "")
    email = data.get("email", "")
    password = data.get("password", "")
    # 记忆配置选项：防止账户间记忆互相影响
    clear_memories = data.get("clear_memories", False)
    disable_memory = data.get("disable_memory", False)
    clear_chats = data.get("clear_chats", False)

    # 向后兼容: 支持 token 直传 或 email+password 自动登录
    if not token and not (email and password):
        raise HTTPException(400, detail="需要提供 token 或 email+password")

    if not token:
        # 通过邮箱密码自动登录获取 token
        resolver = client.auth_resolver
        proxy = data.get("proxy", "")
        ok, new_token, error = await resolver.login(email, password, proxy or None)
        if not ok:
            return {"ok": False, "error": f"登录失败: {error}"}
        token = new_token

    acc = Account(
        email=email or f"manual_{int(time.time())}@qwen",
        password=password,
        token=token,
        cookies=data.get("cookies", ""),
        username=data.get("username", ""),
        proxy=data.get("proxy", ""),
    )

    is_valid = await client.verify_token(token)
    if not is_valid:
        return {"ok": False, "error": "Invalid token (验证失败，请确认Token有效)"}

    await pool.add(acc)

    # 根据配置执行记忆操作
    import logging
    log = logging.getLogger("backend.api.admin")
    if clear_memories:
        try:
            await client.clear_memories(acc.token)
            log.info(f"[记忆操作] {acc.email} 清空记忆成功")
        except Exception as e:
            log.warning(f"[记忆操作] {acc.email} 清空记忆失败: {e}")
    if disable_memory:
        try:
            await client.disable_update_memory(acc.token)
            log.info(f"[记忆操作] {acc.email} 关闭更新记忆成功")
        except Exception as e:
            log.warning(f"[记忆操作] {acc.email} 关闭更新记忆失败: {e}")
        try:
            await client.disable_memory(acc.token)
            log.info(f"[记忆操作] {acc.email} 关闭记忆成功")
        except Exception as e:
            log.warning(f"[记忆操作] {acc.email} 关闭记忆失败: {e}")
    if clear_chats:
        try:
            await client.clear_all_chats(acc.token)
            log.info(f"[记忆操作] {acc.email} 清空聊天记录成功")
        except Exception as e:
            log.warning(f"[记忆操作] {acc.email} 清空聊天记录失败: {e}")
    return {"ok": True, "email": acc.email}


@router.get("/accounts", dependencies=[Depends(verify_admin)])
async def list_accounts(request: Request):
    pool: AccountPool = request.app.state.account_pool
    diagnostics_by_email = {item["email"]: item for item in pool.account_diagnostics()}
    accs = []
    for a in pool.accounts:
        d = a.to_dict()
        d.update(diagnostics_by_email.get(a.email, {}))
        accs.append(d)
    return {"accounts": accs}

@router.post("/accounts/batch-import", dependencies=[Depends(verify_admin)])
async def batch_import_accounts(request: Request):
    """批量导入账号（支持 email:password 或 email:password|proxy_url 格式）"""
    import asyncio
    import time as _time
    from backend.core.account_pool import Account, AccountPool
    from backend.services.qwen_client import QwenClient

    pool: AccountPool = request.app.state.account_pool
    client: QwenClient = request.app.state.qwen_client

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, detail="Invalid JSON body")

    accounts_text = data.get("accounts_text", "")
    concurrency = min(20, max(1, int(data.get("concurrency", 5))))
    # 记忆配置选项：防止账户间记忆互相影响
    clear_memories = data.get("clear_memories", False)
    disable_memory = data.get("disable_memory", False)
    clear_chats = data.get("clear_chats", False)

    if not accounts_text:
        raise HTTPException(400, detail="accounts_text 不能为空")

    # 解析文本
    parse_result = _parse_batch_accounts_text(accounts_text)
    parsed_accounts = parse_result["parsed"]
    invalid_count = parse_result["invalid_count"]

    if not parsed_accounts:
        return {
            "ok": True,
            "total": len(parse_result["lines"]),
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "invalid": invalid_count,
            "results": [],
        }

    # 跳过已存在的账号
    existing_emails = {a.email for a in pool.accounts}
    new_accounts = []
    skipped = 0
    for pa in parsed_accounts:
        if pa["email"] in existing_emails:
            skipped += 1
            continue
        new_accounts.append(pa)

    # 并发登录 + 导入
    results = []
    success_count = 0
    failed_count = 0
    semaphore = asyncio.Semaphore(concurrency)

    async def _import_one(account_info):
        nonlocal success_count, failed_count
        email = account_info["email"]
        password = account_info["password"]
        proxy = account_info.get("proxy") or ""

        async with semaphore:
            ok, token, error = await client.auth_resolver.login(email, password, proxy or None)
            if not ok:
                failed_count += 1
                results.append({"email": email, "ok": False, "error": f"登录失败: {error}"})
                return

            # 验证 token
            is_valid = await client.verify_token(token)
            if not is_valid:
                failed_count += 1
                results.append({"email": email, "ok": False, "error": "Token 验证失败"})
                return

            # 添加到账号池
            acc = Account(
                email=email,
                password=password,
                token=token,
                proxy=proxy,
            )
            await pool.add(acc)
            # 根据配置执行记忆操作
            import logging
            log = logging.getLogger("backend.api.admin")
            if clear_memories:
                try:
                    await client.clear_memories(acc.token)
                    log.info(f"[记忆操作] {acc.email} 清空记忆成功")
                except Exception as e:
                    log.warning(f"[记忆操作] {acc.email} 清空记忆失败: {e}")
            if disable_memory:
                try:
                    await client.disable_update_memory(acc.token)
                    log.info(f"[记忆操作] {acc.email} 关闭更新记忆成功")
                except Exception as e:
                    log.warning(f"[记忆操作] {acc.email} 关闭更新记忆失败: {e}")
                try:
                    await client.disable_memory(acc.token)
                    log.info(f"[记忆操作] {acc.email} 关闭记忆成功")
                except Exception as e:
                    log.warning(f"[记忆操作] {acc.email} 关闭记忆失败: {e}")
            if clear_chats:
                try:
                    await client.clear_all_chats(acc.token)
                    log.info(f"[记忆操作] {acc.email} 清空聊天记录成功")
                except Exception as e:
                    log.warning(f"[记忆操作] {acc.email} 清空聊天记录失败: {e}")
            success_count += 1
            results.append({"email": email, "ok": True})

    # 执行所有导入任务
    await asyncio.gather(*[_import_one(a) for a in new_accounts])

    return {
        "ok": True,
        "total": len(parse_result["lines"]),
        "success": success_count,
        "failed": failed_count,
        "skipped": skipped,
        "invalid": invalid_count,
        "results": results,
    }

@router.post("/accounts/register", dependencies=[Depends(verify_admin)])
async def register_new_account(request: Request):
    """无浏览器镜像不支持自动注册新千问账号。"""
    import logging

    log = logging.getLogger("backend.api.admin")
    client_ip = request.client.host if request.client else "127.0.0.1"
    log.info(f"[注册] 无浏览器模式拒绝自动注册请求，来源IP: {client_ip}")
    return {"ok": False, "error": "轻量无浏览器镜像不支持自动注册，请手动添加账号 token"}

@router.post("/verify", dependencies=[Depends(verify_admin)])
async def verify_all_accounts(request: Request):
    """验证所有账号的有效性 (完全复原单文件逻辑)"""
    from backend.core.account_pool import AccountPool
    from backend.services.qwen_client import QwenClient
    import logging

    log = logging.getLogger("qwen2api.admin")
    pool: AccountPool = request.app.state.account_pool
    client: QwenClient = request.app.state.qwen_client

    results = []
    for acc in pool.accounts:
        is_valid = await client.verify_token(acc.token)
        if not is_valid and acc.password:
            log.info(f"[校验] {acc.email} token失效，尝试自动刷新...")
            is_valid = await client.auth_resolver.refresh_token(acc)

        acc.valid = is_valid
        results.append({"email": acc.email, "valid": is_valid, "refreshed": not is_valid})

    await pool.save() # 直接保存全部状态，不调用 mark_invalid 以免熔断影响测试
    return {"ok": True, "results": results}

@router.post("/accounts/{email}/activate", dependencies=[Depends(verify_admin)])
async def activate_account(email: str, request: Request):
    """无浏览器镜像不支持页面式账号激活。"""
    import logging

    log = logging.getLogger("backend.api.admin")
    client_ip = request.client.host if request.client else "127.0.0.1"
    log.info(f"[激活] 无浏览器模式拒绝页面激活请求: {email}, 来源IP: {client_ip}")
    return {"ok": False, "error": "轻量无浏览器镜像不支持页面激活，请手动获取 token 后重新添加账号"}

@router.post("/accounts/{email}/verify", dependencies=[Depends(verify_admin)])
async def verify_account(email: str, request: Request):
    """单独验证某个账号的有效性 (完全复原单文件逻辑)"""
    from backend.services.qwen_client import QwenClient
    from backend.core.account_pool import AccountPool
    import logging

    log = logging.getLogger("qwen2api.admin")
    pool: AccountPool = request.app.state.account_pool
    client: QwenClient = request.app.state.qwen_client

    acc = next((a for a in pool.accounts if a.email == email), None)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")

    is_valid = await client.verify_token(acc.token)
    if not is_valid and acc.password:
        log.info(f"[校验] {acc.email} token失效，尝试自动刷新...")
        is_valid = await client.auth_resolver.refresh_token(acc)

    acc.valid = is_valid
    await pool.save() # 直接保存，不调用 mark_invalid 以免熔断影响正常测试

    return {"email": acc.email, "valid": is_valid}

def _qwen_operation_response(email: str, result: dict):
    status = int(result.get("status", 0) or 0)
    body = result.get("body", "")
    return {
        "ok": 200 <= status < 300,
        "email": email,
        "status": status,
        "body": body,
    }


def _get_account_or_404(pool: AccountPool, email: str) -> Account:
    acc = pool.get_by_email(email)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    return acc


@router.post("/accounts/{email}/settings/disable-update-memory", dependencies=[Depends(verify_admin)])
async def disable_account_update_memory(email: str, request: Request):
    from backend.services.qwen_client import QwenClient

    pool: AccountPool = request.app.state.account_pool
    client: QwenClient = request.app.state.qwen_client
    acc = _get_account_or_404(pool, email)
    result = await client.disable_update_memory(acc.token)
    return _qwen_operation_response(email, result)


@router.post("/accounts/{email}/settings/disable-memory", dependencies=[Depends(verify_admin)])
async def disable_account_memory(email: str, request: Request):
    from backend.services.qwen_client import QwenClient

    pool: AccountPool = request.app.state.account_pool
    client: QwenClient = request.app.state.qwen_client
    acc = _get_account_or_404(pool, email)
    result = await client.disable_memory(acc.token)
    return _qwen_operation_response(email, result)


@router.post("/accounts/{email}/memories/clear", dependencies=[Depends(verify_admin)])
async def clear_account_memories(email: str, request: Request):
    from backend.services.qwen_client import QwenClient

    pool: AccountPool = request.app.state.account_pool
    client: QwenClient = request.app.state.qwen_client
    acc = _get_account_or_404(pool, email)
    result = await client.clear_memories(acc.token)
    return _qwen_operation_response(email, result)


@router.post("/accounts/{email}/chats/clear", dependencies=[Depends(verify_admin)])
async def clear_account_chats(email: str, request: Request):
    from backend.services.qwen_client import QwenClient

    pool: AccountPool = request.app.state.account_pool
    client: QwenClient = request.app.state.qwen_client
    acc = _get_account_or_404(pool, email)
    result = await client.clear_all_chats(acc.token)
    return _qwen_operation_response(email, result)


@router.delete("/accounts/{email}", dependencies=[Depends(verify_admin)])
async def delete_account(email: str, request: Request):
    from backend.core.account_pool import AccountPool
    pool: AccountPool = request.app.state.account_pool
    await pool.remove(email)
    return {"ok": True}


@router.get("/accounts/export", dependencies=[Depends(verify_admin)])
async def export_accounts(request: Request):
    """导出所有账号信息（邮箱:密码|代理 格式）"""
    from backend.core.account_pool import AccountPool
    
    pool: AccountPool = request.app.state.account_pool
    
    # 构建导出文本
    lines = []
    for acc in pool.accounts:
        # 只导出有邮箱的账号
        if not acc.email or acc.email.startswith("manual_"):
            continue
            
        line_parts = [acc.email]
        if acc.password:
            line_parts.append(acc.password)
            
        # 添加代理（如果存在）
        if acc.proxy:
            line = f"{'|'.join(line_parts)}|{acc.proxy}"
        else:
            line = ':'.join(line_parts) if len(line_parts) > 1 else line_parts[0]
            
        lines.append(line)
    
    return {
        "ok": True, 
        "accounts_text": "\n".join(lines),
        "count": len(lines)
    }



@router.get("/settings", dependencies=[Depends(verify_admin)])
async def get_settings():
    from backend.core.config import MODEL_MAP
    # 从 settings.py 所在的同级导入 VERSION，避免循环导入或未定义报错
    from backend.core.config import settings as backend_settings

    # 强制将 dict 转换，确保能被 JSON 序列化
    safe_map = {k: v for k, v in MODEL_MAP.items()}
    return {
        "version": "2.0.0",
        "max_inflight_per_account": backend_settings.MAX_INFLIGHT_PER_ACCOUNT,
        "model_aliases": safe_map,
        "account_selection_strategy": backend_settings.ACCOUNT_SELECTION_STRATEGY,
        "account_max_failures_before_cooldown": backend_settings.ACCOUNT_MAX_FAILURES_BEFORE_COOLDOWN,
        "account_cooldown_period_seconds": backend_settings.ACCOUNT_COOLDOWN_PERIOD_SECONDS
    }

@router.put("/settings", dependencies=[Depends(verify_admin)])
async def update_settings(data: dict):
    from backend.core.config import MODEL_MAP
    if "max_inflight_per_account" in data:
        settings.MAX_INFLIGHT_PER_ACCOUNT = data["max_inflight_per_account"]
    if "model_aliases" in data:
        MODEL_MAP.clear()
        MODEL_MAP.update(data["model_aliases"])
    if "account_selection_strategy" in data:
        settings.ACCOUNT_SELECTION_STRATEGY = data["account_selection_strategy"]
    if "account_max_failures_before_cooldown" in data:
        settings.ACCOUNT_MAX_FAILURES_BEFORE_COOLDOWN = data["account_max_failures_before_cooldown"]
    if "account_cooldown_period_seconds" in data:
        settings.ACCOUNT_COOLDOWN_PERIOD_SECONDS = data["account_cooldown_period_seconds"]
    return {"ok": True}

@router.get("/keys", dependencies=[Depends(verify_admin)])
async def get_keys():
    from backend.core.config import API_KEYS
    return {"keys": list(API_KEYS)}

@router.post("/keys", dependencies=[Depends(verify_admin)])
async def create_key():
    from backend.core.config import API_KEYS, save_api_keys

    new_key = f"sk-{secrets.token_hex(24)}"
    API_KEYS.add(new_key)
    save_api_keys(API_KEYS)
    return {"ok": True, "key": new_key}

@router.delete("/keys/{key}", dependencies=[Depends(verify_admin)])
async def delete_key(key: str):
    from backend.core.config import API_KEYS, save_api_keys

    if key in API_KEYS:
        API_KEYS.remove(key)
        save_api_keys(API_KEYS)
    return {"ok": True}