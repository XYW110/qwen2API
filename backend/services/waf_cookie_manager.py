"""
WAF Cookie Manager — 通过轻量 HTTP GET 获取 acw_tc cookie，绕过阿里云 x5sec。

发现：completions 端点只需要 acw_tc cookie（服务端 Set-Cookie 返回），
不需要任何 JS 生成的 cookie（cna, tfstk, isg 等都不需要）。
"""

import logging
import time

from curl_cffi.requests import AsyncSession

log = logging.getLogger("qwen2api.waf_cookies")

WAF_COOKIE_TTL = 1500  # 25 分钟（acw_tc 的 Max-Age=1800，提前刷新）


class WafCookieManager:
    """管理每账号的 WAF cookie（acw_tc），通过轻量 GET 请求获取。"""

    _instance = None

    def __init__(self):
        pass

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def get_cookies(self, account) -> str:
        """获取账号的 WAF cookie，过期时自动刷新。"""
        now = time.time()
        if not account.waf_cookies or now > account.waf_cookies_expires_at:
            await self.refresh_account_cookies(account)
        return account.waf_cookies

    async def refresh_account_cookies(self, account):
        """通过 curl_cffi GET 请求获取 acw_tc cookie。"""
        email = account.email
        try:
            log.info(f"[WafCookie] Refreshing acw_tc for {email}...")
            async with AsyncSession(impersonate="chrome", timeout=15) as s:
                await s.get("https://chat.qwen.ai", allow_redirects=True)
                acw_tc = s.cookies.get("acw_tc", "")

            if acw_tc:
                account.waf_cookies = f"acw_tc={acw_tc}"
                account.waf_cookies_expires_at = time.time() + WAF_COOKIE_TTL
                log.info(f"[WafCookie] Refreshed acw_tc for {email}: {acw_tc[:20]}...")
            else:
                log.warning(f"[WafCookie] No acw_tc returned for {email}")
        except Exception as e:
            log.error(f"[WafCookie] Failed to refresh for {email}: {e}")
            raise

    def mark_expired(self, account):
        """标记 cookie 过期（检测到 x5sec 时调用）。"""
        log.warning(f"[WafCookie] Marking acw_tc expired for {account.email}")
        account.waf_cookies_expires_at = 0
