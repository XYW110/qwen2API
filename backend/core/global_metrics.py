"""
全局请求指标追踪模块
提供 QPS、延迟百分位、成功率、模型分布等聚合指标
"""

import time
import threading
from collections import deque
from datetime import datetime


class GlobalMetrics:
    """线程安全的全局请求指标收集器（单例模式）"""

    def __init__(self) -> None:
        self._lock = threading.Lock()

        # 基础计数
        self.total_requests: int = 0
        self.total_errors: int = 0

        # 延迟缓冲区：最近 1000 条请求的耗时（秒）
        self.latency_buffer: deque[float] = deque(maxlen=1000)

        # 按 HTTP 状态码分组计数
        self.status_code_counts: dict[int, int] = {}

        # 按模型名计数
        self.model_counts: dict[str, int] = {}

        # 滑动窗口 QPS 数据：每个窗口记录 {time, requests, errors}
        # 保留 120 个窗口，每窗口 10 秒
        self.qps_windows: deque = deque(maxlen=120)

        # 服务启动时间
        self.started_at: float = time.time()

        # 当前窗口临时计数（内部使用）
        self._window_start: float = time.time()
        self._window_requests: int = 0
        self._window_errors: int = 0

        # Token 统计
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0

        # 按模型的 Token 分布: {model: {"prompt": int, "completion": int}}
        self.model_tokens: dict[str, dict[str, int]] = {}

        # Token 滑动窗口临时计数（复用现有窗口滚动逻辑）
        self._window_prompt_tokens: int = 0
        self._window_completion_tokens: int = 0

    def record_tokens(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        model: str | None = None,
    ) -> None:
        """记录 Token 使用量（在请求完成后调用）"""
        with self._lock:
            self.total_prompt_tokens += prompt_tokens
            self.total_completion_tokens += completion_tokens

            # 按模型统计
            if model:
                if model not in self.model_tokens:
                    self.model_tokens[model] = {"prompt": 0, "completion": 0}
                self.model_tokens[model]["prompt"] += prompt_tokens
                self.model_tokens[model]["completion"] += completion_tokens

            # Token 窗口计数（复用现有窗口逻辑）
            self._window_prompt_tokens += prompt_tokens
            self._window_completion_tokens += completion_tokens

    def record_request(
        self,
        duration: float,
        status_code: int,
        model: str | None = None,
    ) -> None:
        """记录一次请求的指标数据"""
        with self._lock:
            self.total_requests += 1
            self.latency_buffer.append(duration)
            self._window_requests += 1

            if status_code >= 400:
                self.total_errors += 1
                self._window_errors += 1

            # 状态码分布
            self.status_code_counts[status_code] = (
                self.status_code_counts.get(status_code, 0) + 1
            )

            # 模型分布
            if model:
                self.model_counts[model] = self.model_counts.get(model, 0) + 1

            # 检查是否需要滚动窗口（每 10 秒）
            now = time.time()
            if now - self._window_start >= 10:
                self.qps_windows.append(
                    {
                        "time": self._window_start,
                        "requests": self._window_requests,
                        "errors": self._window_errors,
                        "prompt_tokens": self._window_prompt_tokens,
                        "completion_tokens": self._window_completion_tokens,
                    }
                )
                self._window_start = now
                self._window_requests = 0
                self._window_errors = 0
                self._window_prompt_tokens = 0
                self._window_completion_tokens = 0

    def get_snapshot(self) -> dict:
        """返回当前聚合指标快照"""
        with self._lock:
            # 计算延迟百分位
            sorted_latencies = sorted(self.latency_buffer)
            n = len(sorted_latencies)
            if n > 0:
                p50 = sorted_latencies[int(n * 0.50)]
                p95 = sorted_latencies[min(int(n * 0.95), n - 1)]
                p99 = sorted_latencies[min(int(n * 0.99), n - 1)]
            else:
                p50 = p95 = p99 = 0.0

            # 计算成功率
            success_rate = (
                ((self.total_requests - self.total_errors) / self.total_requests * 100)
                if self.total_requests > 0
                else 100.0
            )

            # 计算 QPS：最近 1 分钟（6 个窗口）平均
            recent_windows = list(self.qps_windows)[-6:]
            if recent_windows:
                total_reqs = sum(w["requests"] for w in recent_windows)
                total_span = len(recent_windows) * 10  # 每个窗口 10 秒
                qps = total_reqs / total_span
            else:
                qps = 0.0

            return {
                "total_requests": self.total_requests,
                "total_errors": self.total_errors,
                "success_rate": round(success_rate, 2),
                "latency_p50": round(p50, 4),
                "latency_p95": round(p95, 4),
                "latency_p99": round(p99, 4),
                "qps": round(qps, 4),
                "model_distribution": dict(self.model_counts),
                "status_code_distribution": dict(self.status_code_counts),
                "uptime_seconds": round(time.time() - self.started_at, 2),
                "total_prompt_tokens": self.total_prompt_tokens,
                "total_completion_tokens": self.total_completion_tokens,
                "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
                "model_token_distribution": dict(self.model_tokens),
            }

    def get_time_series(self) -> list[dict]:
        """返回时序数据（用于前端折线图）"""
        with self._lock:
            result = []
            for window in self.qps_windows:
                ts = window["time"]
                dt = datetime.fromtimestamp(ts)
                qps = window["requests"] / 10  # 每窗口 10 秒
                result.append(
                    {
                        "time": dt.strftime("%H:%M:%S"),
                        "qps": round(qps, 4),
                        "errors": window["errors"],
                    }
                )
            return result

    def get_token_time_series(self) -> list[dict]:
        """返回 Token 时序数据（用于前端面积图）"""
        with self._lock:
            result = []
            for window in self.qps_windows:
                ts = window["time"]
                dt = datetime.fromtimestamp(ts)
                result.append({
                    "time": dt.strftime("%H:%M:%S"),
                    "prompt_tokens": window.get("prompt_tokens", 0),
                    "completion_tokens": window.get("completion_tokens", 0),
                })
            return result


# 模块级单例实例
metrics = GlobalMetrics()
