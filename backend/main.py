import asyncio
import faulthandler
import json
import logging
import signal
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
import sys

# 将项目根目录加入到 sys.path，解决直接运行 main.py 时找不到 backend 模块的问题
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.core.config import settings
from backend.core.database import AsyncJsonDB
from backend.core.account_pool import AccountPool
from backend.core.account_stats import AccountStatsStore
from backend.core.session_affinity import SessionAffinityStore
from backend.core.upstream_file_cache import UpstreamFileCache
from backend.core.session_lock import SessionLockRegistry
from backend.core.request_logging import configure_logging, request_context
from backend.core.diagnostics import install_stack_dump_handler
from backend.services.qwen_client import QwenClient
from backend.services.file_store import LocalFileStore
from backend.toolcore.context_offload import ContextOffloader
from backend.services.response_store import InMemoryResponseStore
from backend.services.upstream_file_uploader import UpstreamFileUploader
import backend.api.models as models
from backend.api import admin, v1_chat, responses_api, probes, anthropic, gemini, embeddings, images, files_api, public
from backend.services.garbage_collector import garbage_collect_chats
from backend.services.context_cleanup import context_cleanup_loop

configure_logging(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
install_stack_dump_handler(
    settings=settings,
    faulthandler_module=faulthandler,
    signal_module=signal,
    stream=sys.stderr,
)
log = logging.getLogger("qwen2api")


async def cleanup_loop(app):
    """每24小时清理过期的API Key失败记录"""
    while True:
        await asyncio.sleep(86400)  # 24 hours
        try:
            manager = app.state.api_key_manager
            cleaned_count = await manager.run_cleanup()
            log.info("Expired API key failure records cleaned up: %d", cleaned_count)
        except Exception as e:
            log.error("Cleanup task failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    with request_context(surface="startup"):
        log.info("正在启动 qwen2API v2.0 企业网关...")

        # 初始化数据存储 (带锁 JSON)
        app.state.accounts_db = AsyncJsonDB(settings.ACCOUNTS_FILE, default_data=[])
        app.state.users_db = AsyncJsonDB(settings.USERS_FILE, default_data=[])
        app.state.captures_db = AsyncJsonDB(settings.CAPTURES_FILE, default_data=[])
        app.state.session_affinity_db = AsyncJsonDB(settings.CONTEXT_AFFINITY_FILE, default_data=[])
        app.state.context_cache_db = AsyncJsonDB(settings.CONTEXT_CACHE_FILE, default_data=[])
        app.state.uploaded_files_db = AsyncJsonDB(settings.UPLOADED_FILES_FILE, default_data=[])

        # 初始化独立统计存储
        stats_store = AccountStatsStore("data/account_stats.json")
        await stats_store.load()
        app.state.stats_store = stats_store
        
        # 初始化 API Key 管理器
        from backend.core.api_key_store import ApiKeyManager
        api_key_manager = ApiKeyManager()
        await api_key_manager.load()
        app.state.api_key_manager = api_key_manager

        # 初始化组件
        app.state.account_pool = AccountPool(
            app.state.accounts_db,
            max_inflight=settings.MAX_INFLIGHT_PER_ACCOUNT,
            stats_store=stats_store,
        )
        app.state.qwen_client = QwenClient(app.state.account_pool)
        app.state.qwen_executor = app.state.qwen_client.executor
        app.state.file_store = LocalFileStore(settings.CONTEXT_GENERATED_DIR, app.state.uploaded_files_db)
        app.state.session_affinity = SessionAffinityStore(app.state.session_affinity_db)
        app.state.upstream_file_cache = UpstreamFileCache(app.state.context_cache_db)
        app.state.context_offloader = ContextOffloader(settings)
        app.state.upstream_file_uploader = UpstreamFileUploader(app.state.qwen_client, settings)
        app.state.session_locks = SessionLockRegistry()
        app.state.response_store = InMemoryResponseStore()

        # 初始化待审批账户存储
        from backend.core.pending_account_store import PendingAccountStore
        pending_account_store = PendingAccountStore()
        await pending_account_store.load()
        app.state.pending_account_store = pending_account_store

        # 加载账号并启动后台清理任务
        await app.state.account_pool.load()
        await app.state.file_store.load()
        await app.state.session_affinity.load()
        await app.state.upstream_file_cache.load()
        asyncio.create_task(garbage_collect_chats(app))
        asyncio.create_task(context_cleanup_loop(app))
        
        # 启动 API Key 失败记录清理任务
        cleanup_task = asyncio.create_task(cleanup_loop(app))

        # 初始化 ChatIdPool 预热池（减少每次请求的握手时延）
        from backend.services.chat_id_pool import ChatIdPool
        from backend.core.config import load_prewarm_config
        prewarm_cfg = load_prewarm_config()
        chat_id_pool = ChatIdPool(
            app.state.qwen_client,
            prewarm_models=prewarm_cfg.get("prewarm_models", settings.CHAT_ID_POOL_PREWARM_MODELS),
            target_per_account=prewarm_cfg.get("target_per_model", 3),
        )
        await chat_id_pool.start()
        chat_id_pool._max_total_prewarm = prewarm_cfg.get("max_total_prewarm", settings.MAX_TOTAL_PREWARM_CHAT_IDS)
        app.state.chat_id_pool = chat_id_pool
        app.state.qwen_executor.chat_id_pool = chat_id_pool

    try:
        yield
    finally:
        await chat_id_pool.stop()
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

    with request_context(surface="shutdown"):
        log.info("正在关闭网关服务...")


app = FastAPI(title="qwen2API Enterprise Gateway", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def metrics_middleware(request, call_next):
    """请求指标记录中间件：仅记录业务 API 路径，忽略管理端点和静态资源"""
    path = request.url.path
    # 只记录业务 API（/v1/），排除管理端点（/api/admin/）、静态资源、健康检查、docs 等
    if not path.startswith("/v1/"):
        return await call_next(request)
    
    # 请求体大小限制（防止超大 JSON 导致内存/CPU 峰值）
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > settings.REQUEST_MAX_BODY_BYTES:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=413,
            content={"detail": f"Request body too large. Max {settings.REQUEST_MAX_BODY_BYTES // (1024*1024)}MB"}
        )

    # 轻量提取 model 字段：读取完整 body（Starlette 会缓存供路由再用），
    # 但只对前 2KB 做正则扫描提取 model，避免对 100KB+ body 做 json.loads（CPU 密集型）
    model = ""
    try:
        import re as _re
        body = await request.body()
        if body:
            head = body[:2048]
            m = _re.search(rb'"model"\s*:\s*"([^"]+)"', head)
            if m:
                model = m.group(1).decode("utf-8", errors="ignore")
    except Exception:
        pass

    start = time.time()
    response = await call_next(request)
    duration = time.time() - start

    # 记录到全局指标
    from backend.core.global_metrics import metrics
    metrics.record_request(duration, response.status_code, model)

    return response


# 挂载路由
app.include_router(v1_chat.router, tags=["OpenAI Compatible"])
app.include_router(responses_api.router, tags=["Responses Compatible"])
app.include_router(models.router, tags=["Models"])
app.include_router(anthropic.router, tags=["Claude Compatible"])
app.include_router(gemini.router, tags=["Gemini Compatible"])
app.include_router(embeddings.router, tags=["Embeddings"])
app.include_router(images.router, tags=["Images"])
app.include_router(files_api.router, tags=["Files"])
app.include_router(probes.router, tags=["Probes"])
app.include_router(public.router, prefix="/api", tags=["Public"])
app.include_router(admin.router, prefix="/api/admin", tags=["Dashboard Admin"])

@app.get("/api", tags=["System"])
async def root():
    return {
        "status": "qwen2API Enterprise Gateway is running",
        "docs": "/docs",
        "version": "2.0.0"
    }

# 托管前端构建产物
FRONTEND_DIST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")
if os.path.exists(FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:
    log.warning(f"未找到前端构建目录: {FRONTEND_DIST}，WebUI 将不可用。")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=settings.PORT, workers=1)
