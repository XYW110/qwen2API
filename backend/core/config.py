import os
import json
from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Dict, List, Set, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"

class Settings(BaseSettings):
    # 服务配置
    PORT: int = int(os.getenv("PORT", 8080))
    WORKERS: int = int(os.getenv("WORKERS", 3))
    ADMIN_KEY: str = os.getenv("ADMIN_KEY", "admin")

    MAX_INFLIGHT_PER_ACCOUNT: int = int(os.getenv("MAX_INFLIGHT", 1))
    GLOBAL_MAX_INFLIGHT: int = int(os.getenv("GLOBAL_MAX_INFLIGHT", 0))

    # 容灾与限流
    MAX_RETRIES: int = 3
    RATE_LIMIT_COOLDOWN: int = 600
    ACCOUNT_MIN_INTERVAL_MS: int = int(os.getenv("ACCOUNT_MIN_INTERVAL_MS", 0))
    ACCOUNT_BUSY_TIMEOUT_SECONDS: float = float(os.getenv("ACCOUNT_BUSY_TIMEOUT_SECONDS", 900))
    REQUEST_JITTER_MIN_MS: int = int(os.getenv("REQUEST_JITTER_MIN_MS", 0))
    REQUEST_JITTER_MAX_MS: int = int(os.getenv("REQUEST_JITTER_MAX_MS", 0))
    RATE_LIMIT_BASE_COOLDOWN: int = int(os.getenv("RATE_LIMIT_BASE_COOLDOWN", 600))
    RATE_LIMIT_MAX_COOLDOWN: int = int(os.getenv("RATE_LIMIT_MAX_COOLDOWN", 3600))
    # 账号选择策略与冷却机制
    ACCOUNT_SELECTION_STRATEGY: str = os.getenv("ACCOUNT_SELECTION_STRATEGY", "least_loaded")
    ACCOUNT_MAX_FAILURES_BEFORE_COOLDOWN: int = int(os.getenv("ACCOUNT_MAX_FAILURES_BEFORE_COOLDOWN", 3))
    ACCOUNT_COOLDOWN_PERIOD_SECONDS: int = int(os.getenv("ACCOUNT_COOLDOWN_PERIOD_SECONDS", 300))


    # 日志
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    QWEN_CODE_CODER_MODEL: str = os.getenv("QWEN_CODE_CODER_MODEL", "qwen3-coder-plus")
    QWEN_CODE_FORCE_CODER_FOR_TOOL_CALLS: bool = os.getenv("QWEN_CODE_FORCE_CODER_FOR_TOOL_CALLS", "true").lower() in {"1", "true", "yes", "on"}
    QWEN_CODE_FORCE_CODER_FOR_CODING_TASKS: bool = os.getenv("QWEN_CODE_FORCE_CODER_FOR_CODING_TASKS", "true").lower() in {"1", "true", "yes", "on"}
    TOOLCORE_V2_ENABLED: bool = os.getenv("TOOLCORE_V2_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    DIAGNOSTIC_STACK_DUMP_ENABLED: bool = os.getenv("DIAGNOSTIC_STACK_DUMP_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    UPSTREAM_AUTO_DELETE_ENABLED: bool = os.getenv("UPSTREAM_AUTO_DELETE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}

    # 上游请求超时
    QWEN_UPSTREAM_REQUEST_TIMEOUT_SECONDS: float = float(
        os.getenv("QWEN_UPSTREAM_REQUEST_TIMEOUT_SECONDS", 60)
    )
    QWEN_UPSTREAM_STREAM_TIMEOUT_SECONDS: float = float(
        os.getenv("QWEN_UPSTREAM_STREAM_TIMEOUT_SECONDS", 300)
    )
    OPENAI_JSON_SINGLEFLIGHT_ENABLED: bool = os.getenv("OPENAI_JSON_SINGLEFLIGHT_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    OPENAI_JSON_SINGLEFLIGHT_WAIT_TIMEOUT_SECONDS: float = float(os.getenv("OPENAI_JSON_SINGLEFLIGHT_WAIT_TIMEOUT_SECONDS", 600))
    OPENAI_JSON_SINGLEFLIGHT_RESULT_TTL_SECONDS: float = float(os.getenv("OPENAI_JSON_SINGLEFLIGHT_RESULT_TTL_SECONDS", 120))

    # 数据文件路径
    ACCOUNTS_FILE: str = os.getenv("ACCOUNTS_FILE", str(DATA_DIR / "accounts.json"))
    USERS_FILE: str = os.getenv("USERS_FILE", str(DATA_DIR / "users.json"))
    CAPTURES_FILE: str = os.getenv("CAPTURES_FILE", str(DATA_DIR / "captures.json"))
    CONFIG_FILE: str = os.getenv("CONFIG_FILE", str(DATA_DIR / "config.json"))

    # 预热模型列表
    CHAT_ID_POOL_PREWARM_MODELS: List[str] = []
    MAX_TOTAL_PREWARM_CHAT_IDS: int = 100

    # ????? / ????
    CONTEXT_INLINE_MAX_CHARS: int = int(os.getenv("CONTEXT_INLINE_MAX_CHARS", 4000))
    CONTEXT_FORCE_FILE_MAX_CHARS: int = int(os.getenv("CONTEXT_FORCE_FILE_MAX_CHARS", 10000))
    CONTEXT_ATTACHMENT_TTL_SECONDS: int = int(os.getenv("CONTEXT_ATTACHMENT_TTL_SECONDS", 1800))
    CONTEXT_UPLOAD_PARSE_TIMEOUT_SECONDS: int = int(os.getenv("CONTEXT_UPLOAD_PARSE_TIMEOUT_SECONDS", 60))
    CONTEXT_GENERATED_DIR: str = os.getenv("CONTEXT_GENERATED_DIR", str(DATA_DIR / "context_files"))
    CONTEXT_CACHE_FILE: str = os.getenv("CONTEXT_CACHE_FILE", str(DATA_DIR / "context_cache.json"))
    UPLOADED_FILES_FILE: str = os.getenv("UPLOADED_FILES_FILE", str(DATA_DIR / "uploaded_files.json"))
    CONTEXT_AFFINITY_FILE: str = os.getenv("CONTEXT_AFFINITY_FILE", str(DATA_DIR / "session_affinity.json"))
    CONTEXT_ALLOWED_GENERATED_EXTS: str = os.getenv("CONTEXT_ALLOWED_GENERATED_EXTS", "txt,md,json,log")
    CONTEXT_ALLOWED_USER_EXTS: str = os.getenv("CONTEXT_ALLOWED_USER_EXTS", "txt,md,json,log,xml,yaml,yml,csv,html,css,py,js,ts,java,c,cpp,cs,php,go,rb,sh,zsh,ps1,bat,cmd,pdf,doc,docx,ppt,pptx,xls,xlsx,png,jpg,jpeg,webp,gif,tiff,bmp,svg")

    class Config:
        env_file = ".env"

# ============== API Keys 兼容层 ==============
# 旧代码通过 from backend.core.config import API_KEYS 使用 set[str]
# 新版本使用 ApiKeyManager，但保留兼容性包装

API_KEYS_FILE = DATA_DIR / "api_keys.json"

# 延迟导入避免循环依赖
_api_key_manager_instance = None

def _get_api_key_manager():
    global _api_key_manager_instance
    if _api_key_manager_instance is None:
        from backend.core.api_key_store import ApiKeyManager
        _api_key_manager_instance = ApiKeyManager()
    return _api_key_manager_instance

def load_api_keys() -> set:
    """兼容旧接口：返回 set[str]"""
    try:
        mgr = _get_api_key_manager()
        # 同步加载（仅首次调用时有效）
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            if not loop.is_running():
                loop.run_until_complete(mgr.load())
        except RuntimeError:
            pass
        entries = mgr.keys.get_all()
        return {e.key for e in entries}
    except Exception:
        pass
    return set()

def save_api_keys(keys: set):
    """兼容旧接口：将 set[str] 写入文件"""
    try:
        mgr = _get_api_key_manager()
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            if not loop.is_running():
                # 同步写入（仅首次调用时有效）
                loop.run_until_complete(mgr.load())
        except RuntimeError:
            pass
        # 直接写旧格式文件（兼容）
        API_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(API_KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump({"keys": list(keys)}, f, indent=2)
    except Exception:
        pass

# 兼容性：在内存中存储管理的 API Keys（旧代码使用）
API_KEYS = load_api_keys()

VERSION = "2.0.0"

settings = Settings()

# 全局映射
MODEL_MAP = {
    # OpenAI
    "gpt-4o":            "qwen3.6-plus",
    "gpt-4o-mini":       "qwen3.5-flash",
    "gpt-4-turbo":       "qwen3.6-plus",
    "gpt-4":             "qwen3.6-plus",
    "gpt-4.1":           "qwen3.6-plus",
    "gpt-4.1-mini":      "qwen3.5-flash",
    "gpt-3.5-turbo":     "qwen3.5-flash",
    "gpt-5":             "qwen3.6-plus",
    "o1":                "qwen3.6-plus",
    "o1-mini":           "qwen3.5-flash",
    "o3":                "qwen3.6-plus",
    "o3-mini":           "qwen3.5-flash",
    # Anthropic
    "claude-opus-4-6":   "qwen3.6-plus",
    "claude-sonnet-4-5": "qwen3.6-plus",
    "claude-3-opus":     "qwen3.6-plus",
    "claude-3.5-sonnet": "qwen3.6-plus",
    "claude-3-sonnet":   "qwen3.6-plus",
    "claude-3-haiku":    "qwen3.5-flash",
    # Gemini
    "gemini-2.5-pro":    "qwen3.6-plus",
    "gemini-2.5-flash":  "qwen3.5-flash",
    # Qwen aliases
    "qwen":                  "qwen3.6-plus",
    "qwen-max":              "qwen3.6-plus",
    "qwen-plus":             "qwen3.6-plus",
    "qwen-turbo":            "qwen3.5-flash",
    "qwen3.7-plus-preview":  "qwen-latest-series-invite-beta-v16",
    # DeepSeek
    "deepseek-chat":     "qwen3.6-plus",
    "deepseek-reasoner": "qwen3.6-plus",
}

def resolve_model(name: str) -> str:
    return MODEL_MAP.get(name, name)


GENERIC_QWEN_CODE_MODELS = {
    "qwen3.6-plus",
    "qwen-plus",
    "qwen-max",
    "qwen",
}


def resolve_qwen_code_model(name: str) -> str:
    return resolve_model(settings.QWEN_CODE_CODER_MODEL or name)


def _normalized_model_name(name: str | None) -> str:
    return str(name or "").strip().lower()


def _looks_like_coder_model(name: str | None) -> bool:
    normalized = _normalized_model_name(name)
    return "coder" in normalized or normalized.startswith("qwen-code")


def _is_explicit_non_coder_model(name: str | None) -> bool:
    normalized = _normalized_model_name(name)
    return any(marker in normalized for marker in ("flash", "mini", "turbo"))


def should_route_qwen_code_to_coder(
    requested_model: str,
    *,
    client_profile: str,
    tool_enabled: bool = False,
    coding_intent: bool = False,
) -> bool:
    if client_profile != "qwen_code_openai":
        return False
    if _looks_like_coder_model(requested_model):
        return False
    resolved_model = resolve_model(requested_model)
    if _looks_like_coder_model(resolved_model):
        return False
    if _is_explicit_non_coder_model(requested_model):
        return False

    if tool_enabled and settings.QWEN_CODE_FORCE_CODER_FOR_TOOL_CALLS and resolved_model in GENERIC_QWEN_CODE_MODELS:
        return True
    if coding_intent and settings.QWEN_CODE_FORCE_CODER_FOR_CODING_TASKS and resolved_model in GENERIC_QWEN_CODE_MODELS:
        return True
    return False


def resolve_request_model(
    requested_model: str,
    *,
    client_profile: str,
    tool_enabled: bool = False,
    coding_intent: bool = False,
) -> str:
    if should_route_qwen_code_to_coder(
        requested_model,
        client_profile=client_profile,
        tool_enabled=tool_enabled,
        coding_intent=coding_intent,
    ):
        return resolve_qwen_code_model(requested_model)
    return resolve_model(requested_model)


# ============== 预热配置持久化 ==============
PREWARM_CONFIG_FILE = DATA_DIR / "prewarm_config.json"


def load_prewarm_config() -> dict:
    """从 data/prewarm_config.json 加载预热配置，文件不存在时返回默认值。"""
    default = {
        "version": 1,
        "prewarm_models": list(settings.CHAT_ID_POOL_PREWARM_MODELS),
        "target_per_model": 3,
        "max_total_prewarm": settings.MAX_TOTAL_PREWARM_CHAT_IDS,
    }
    try:
        if PREWARM_CONFIG_FILE.exists():
            with open(PREWARM_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 校验基本结构
            if isinstance(data.get("prewarm_models"), list):
                return data
    except Exception:
        pass
    return default


def save_prewarm_config(config: dict) -> None:
    """将预热配置持久化到 data/prewarm_config.json。"""
    try:
        PREWARM_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PREWARM_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
