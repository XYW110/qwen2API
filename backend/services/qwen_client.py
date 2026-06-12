import json
import logging
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import AsyncIterator

from curl_cffi.requests import AsyncSession

from backend.core.account_pool import AccountPool
from backend.core.config import settings
from backend.services.auth_resolver import BASE_URL, AuthResolver
from backend.upstream.payload_builder import build_chat_payload
from backend.upstream.qwen_executor import QwenExecutor
from backend.upstream.sse_consumer import parse_sse_chunk
from backend.services.waf_cookie_manager import WafCookieManager

log = logging.getLogger("qwen2api.client")


class QwenClient:
    def __init__(self, account_pool: AccountPool):
        self.account_pool = account_pool
        self.auth_resolver = AuthResolver(account_pool) if account_pool is not None else None
        self.executor = QwenExecutor(self, account_pool)

        # list_models 结果缓存（60 分钟 TTL）
        self._models_cache: list = []
        self._models_cache_ts: float = 0
        self._models_cache_ttl: float = 3600  # 60 分钟

    @staticmethod
    def _build_headers(token: str) -> dict[str, str]:
        tz = datetime.now(timezone(timedelta(hours=8))).strftime("%a %b %d %Y %H:%M:%S GMT+0800")
        return {
            "Authorization": f"Bearer {token}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": f"{BASE_URL}/",
            "Origin": BASE_URL,
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "X-Accel-Buffering": "no",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "x-request-id": str(uuid.uuid4()),
            "Timezone": tz,
            "source": "web",
            "Version": "0.2.64",
        }

    @staticmethod
    def _build_headers_with_cookies(cookies: str) -> dict[str, str]:
        """使用 cookies 而不是 Bearer token 构建请求头"""
        tz = datetime.now(timezone(timedelta(hours=8))).strftime("%a %b %d %Y %H:%M:%S GMT+0800")
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": f"{BASE_URL}/",
            "Origin": BASE_URL,
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "X-Accel-Buffering": "no",
            "Cookie": cookies,
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "x-request-id": str(uuid.uuid4()),
            "Timezone": tz,
            "source": "web",
            "Version": "0.2.64",
        }

    async def _build_completions_headers(self, token: str, account=None) -> dict[str, str]:
        """为 completions 端点构建请求头（包含 WAF cookies）。"""
        waf_cookies = ""
        if account:
            waf_mgr = WafCookieManager.get_instance()
            waf_cookies = await waf_mgr.get_cookies(account)
        tz = datetime.now(timezone(timedelta(hours=8))).strftime("%a %b %d %Y %H:%M:%S GMT+0800")
        return {
            "Authorization": f"Bearer {token}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": f"{BASE_URL}/",
            "Origin": BASE_URL,
            "Content-Type": "application/json",
            "X-Accel-Buffering": "no",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "x-request-id": str(uuid.uuid4()),
            "Timezone": tz,
            "source": "web",
            "Version": "0.2.64",
            "Cookie": waf_cookies,
        }

    async def _request_json(
        self,
        method: str,
        path: str,
        token: str,
        body: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        request_timeout = (
            timeout if timeout is not None else settings.QWEN_UPSTREAM_REQUEST_TIMEOUT_SECONDS
        )
        async with AsyncSession(impersonate="chrome124", timeout=request_timeout) as s:
            resp = await s.request(
                method,
                f"{BASE_URL}{path}",
                headers=self._build_headers(token),
                data=json.dumps(body, ensure_ascii=False).encode() if body else None,
            )
        return {"status": resp.status_code, "body": resp.text}

    async def create_chat(self, token: str, model: str, chat_type: str = "t2t") -> str:
        return await self.executor.create_chat(token, model, chat_type=chat_type)

    async def delete_chat(self, token: str, chat_id: str):
        await self._request_json("DELETE", f"/api/v2/chats/{chat_id}", token, timeout=20.0)

    async def list_chats(self, token: str, limit: int = 50) -> list[dict]:
        res = await self._request_json("GET", f"/api/v2/chats?limit={limit}", token, timeout=20.0)
        if res["status"] != 200:
            return []
        try:
            data = json.loads(res.get("body", "{}"))
        except Exception:
            return []
        chats = data.get("data", [])
        return chats if isinstance(chats, list) else []

    async def disable_update_memory(self, token: str) -> dict:
        return await self._request_json(
            "POST",
            "/api/v2/users/user/settings/update",
            token,
            body={"tools_enabled": {"history_retriever": False, "bio": False}},
            timeout=20.0,
        )

    async def disable_memory(self, token: str) -> dict:
        return await self._request_json(
            "POST",
            "/api/v2/users/user/settings/update",
            token,
            body={"memory": {"enable_memory": False, "enable_history_memory": False}},
            timeout=20.0,
        )

    async def clear_memories(self, token: str) -> dict:
        return await self._request_json(
            "POST",
            "/api/v2/memories/delete",
            token,
            body={"forget_all": True},
            timeout=20.0,
        )

    async def clear_all_chats(self, token: str) -> dict:
        return await self._request_json(
            "DELETE",
            "/api/v2/chats/",
            token,
            timeout=20.0,
        )

    async def verify_token(self, token: str) -> bool:
        """Verify token validity via direct HTTP (no browser page needed)."""
        if not token:
            return False

        try:
            async with AsyncSession(impersonate="chrome124", timeout=15) as s:
                resp = await s.get(
                    f"{BASE_URL}/api/v1/auths/",
                    headers=self._build_headers(token),
                )
            if resp.status_code != 200:
                return False

            try:
                data = resp.json()
                return data.get("role") == "user"
            except Exception as e:
                log.warning(f"[verify_token] JSON 解析失败（可能被拦截或代理异常）: {e}, status={resp.status_code}, text={resp.text[:100]}")
                if "aliyun_waf" in resp.text.lower() or "<!doctype" in resp.text.lower():
                    log.info("[verify_token] 遇到 WAF 拦截页面，放行交给浏览器自动化账号流程处理。")
                    return True
                return False
        except Exception as e:
            log.warning(f"[verify_token] HTTP 请求异常: {e}")
            return False

    async def list_models(self, account=None) -> list:
        """获取模型列表（带 60 分钟缓存），优先使用 cookies，没有则用 token"""
        now = time.time()
        # 缓存命中：有缓存且未过期
        if self._models_cache and (now - self._models_cache_ts) < self._models_cache_ttl:
            return self._models_cache

        try:
            cookies = getattr(account, 'cookies', None) if account else None
            if cookies:
                headers = self._build_headers_with_cookies(cookies)
            elif account and account.token:
                headers = self._build_headers(account.token)
            else:
                return self._models_cache  # 无凭据时返回缓存（可能为空）

            async with AsyncSession(impersonate="chrome124", timeout=10) as s:
                resp = await s.get(
                    f"{BASE_URL}/api/models",
                    headers=headers,
                )

            if resp.status_code != 200:
                return self._models_cache  # 失败时返回缓存

            result = resp.json()
            models = result.get("data", [])
            # 更新缓存
            self._models_cache = models
            self._models_cache_ts = now
            return models
        except Exception:
            return self._models_cache  # 异常时返回缓存

    def _build_payload(self, chat_id: str, model: str, content: str, has_custom_tools: bool = False, files: list[dict] | None = None) -> dict:
        return build_chat_payload(chat_id, model, content, has_custom_tools, files=files)

    def parse_sse_chunk(self, chunk: str) -> list[dict]:
        return parse_sse_chunk(chunk)

    async def stream(self, token: str, chat_id: str, model: str, content: str, has_custom_tools: bool = False, files: list[dict] | None = None, account=None):
        async for event in self.executor.stream(token, chat_id, model, content, has_custom_tools, files=files, account=account):
            yield event

    async def stream_chat_once(self, token: str, chat_id: str, payload: dict, account=None) -> AsyncIterator[dict]:
        """流式读取 completions 端点，支持 WAF cookie 和 x5sec 重试。"""
        headers = await self._build_completions_headers(token, account)

        timeout_val = settings.QWEN_UPSTREAM_STREAM_TIMEOUT_SECONDS
        async with AsyncSession(impersonate="chrome124", timeout=timeout_val) as s:
            resp = await s.request(
                "POST",
                f"{BASE_URL}/api/v2/chat/completions?chat_id={chat_id}",
                headers=headers,
                data=json.dumps(payload, ensure_ascii=False).encode(),
            )

            # 检测 x5sec 拦截
            resp_text = resp.text or ""
            if resp.status_code == 200 and ("_____tmd_____" in resp_text or "<script>" in resp_text):
                log.warning("[stream_chat_once] 检测到 x5sec 拦截，标记 cookies 过期并重试...")
                if account:
                    waf_mgr = WafCookieManager.get_instance()
                    waf_mgr.mark_expired(account)

                # 重试一次
                new_headers = await self._build_completions_headers(token, account)
                async with AsyncSession(impersonate="chrome124", timeout=timeout_val) as s2:
                    resp = await s2.request(
                        "POST",
                        f"{BASE_URL}/api/v2/chat/completions?chat_id={chat_id}",
                        headers=new_headers,
                        data=json.dumps(payload, ensure_ascii=False).encode(),
                    )
                    resp_text = resp.text or ""

            if resp.status_code != 200:
                body_bytes = resp.content if isinstance(resp.content, bytes) else resp_text.encode()
                log.error(f"[stream_chat_once] non-200 status={resp.status_code} body={resp_text[:500]}")
                yield {"status": resp.status_code, "body": body_bytes}
                return

            log.info(f"[stream_chat_once] status=200 bytes={len(resp_text)} preview={resp_text[:200]}")
            if resp_text:
                yield {"chunk": resp_text}
            yield {"status": "streamed"}

    async def chat_stream_events_with_retry(
        self,
        model: str,
        content: str,
        has_custom_tools: bool = False,
        files: list[dict] | None = None,
        fixed_account=None,
        existing_chat_id: str | None = None,
    ):
        async for item in self.executor.chat_stream_events_with_retry(
            model,
            content,
            has_custom_tools,
            files=files,
            fixed_account=fixed_account,
            existing_chat_id=existing_chat_id,
        ):
            yield item
