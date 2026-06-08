在容器化部署与微服务架构中，准确区分“进程存活”与“服务可用”是保障系统稳定性的基石。qwen2API 网关遵循 Kubernetes 探针设计范式，提供了两个独立的 HTTP 端点：`/healthz` 用于轻量级存活检测，`/readyz` 用于深度业务就绪校验。这种分离机制确保了负载均衡器仅在网关完成所有核心组件（如账号池、数据库连接、缓存预热）初始化后才导入流量，有效避免了冷启动期间的请求失败。本文档将解析这两个探针的实现逻辑、依赖检查清单及 Docker 集成配置。

## 探针接口定义与行为差异

qwen2API 在 `backend/api/probes.py` 中定义了标准的 RESTful 探针接口。对于初学者而言，理解两者的语义差异至关重要：**Liveness Probe（存活探针）** 仅确认 Web 服务器进程是否响应，任何业务阻塞都不应导致其失败；而 **Readiness Probe（就绪探针）** 则必须验证所有关键依赖项是否已加载到内存中。如果 `/readyz` 返回非 200 状态码，编排系统（如 Docker Swarm 或 K8s）应暂停向该实例转发用户请求，但不会重启容器。

| 端点 | 方法 | 用途 | 成功响应 | 失败响应 | 典型调用方 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `/healthz` | GET | 进程存活检查 | `200 {"status": "ok"}` | N/A (无异常处理) | Docker HEALTHCHECK, K8s Liveness |
| `/readyz` | GET | 业务就绪检查 | `200 {"status": "ready"}` | `503 {"status": "not_ready", "missing": [...]}` | K8s Readiness, 负载均衡器健康检查 |
| `/admin/dev/captures` | GET/DELETE | 调试抓包数据 | JSON / Cleared | 401/403 | 管理员运维 |

Sources: [probes.py](backend/api/probes.py#L8-L28)

## 就绪探针的核心依赖检查清单

`/readyz` 端点的核心价值在于其显式的依赖验证逻辑。代码通过遍历 `required_state` 元组，逐一检查 `request.app.state` 对象上是否存在对应的属性。这一设计将“应用启动”从一个模糊的时间概念转化为确定性的状态集合。只有当以下 8 个核心组件全部初始化完毕时，网关才会对外宣告“Ready”。若任一组件缺失，接口将立即返回 **503 Service Unavailable**，并在响应体中精确列出未就绪的组件名称，极大简化了启动故障排查。

```mermaid
flowchart TD
    Start[GET /readyz] --> Check{遍历 required_state}
    Check -->|accounts_db| DB1[(账号数据库)]
    Check -->|users_db| DB2[(用户数据库)]
    Check -->|captures_db| DB3[(抓包数据库)]
    Check -->|account_pool| Pool[账号池管理器]
    Check -->|qwen_client| Client[Qwen API 客户端]
    Check -->|file_store| Store[文件存储服务]
    Check -->|session_affinity| Affinity[会话亲和性]
    Check -->|upstream_file_cache| Cache[上游文件缓存]
    
    DB1 & DB2 & DB3 & Pool & Client & Store & Affinity & Cache --> AllPresent{全部存在?}
    AllPresent -->|Yes| Ready[200 OK: status=ready]
    AllPresent -->|No| NotReady[503 Error: missing=[...]]
```

**关键依赖项说明：**
*   **accounts_db / users_db / captures_db**: 异步 JSON 数据库实例，确保存储层可读写。
*   **account_pool**: 账号池核心，负责并发控制与限流，未就绪意味着无法处理任何 AI 请求。
*   **qwen_client**: 上游 Qwen API 的 HTTP 客户端引擎。
*   **file_store / upstream_file_cache**: 文件处理与缓存层，影响多模态请求能力。
*   **session_affinity**: 会话粘性管理，保证多轮对话上下文路由正确。

Sources: [probes.py](backend/api/probes.py#L13-L28)

## Docker 容器健康检查集成

在 `Dockerfile` 中，qwen2API 利用 Docker 原生 `HEALTHCHECK` 指令集成了存活探针。该配置直接使用 `curl` 命令轮询 `/healthz` 端点。注意这里特意选择了轻量级的 `/healthz` 而非 `/readyz`，这是为了避免因暂时性的业务初始化延迟（如账号池预热）导致容器被 Docker 守护进程误判为“不健康”并触发不必要的重启循环。`--start-period=120s` 参数为应用提供了充足的冷启动宽限期，在此期间健康检查失败不会被计入重试次数。

```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl --max-time 5 -fsS "http://127.0.0.1:${PORT:-7860}/healthz" || exit 1
```

**配置参数解读：**
*   `--interval=30s`: 每 30 秒执行一次检查。
*   `--timeout=10s`: 单次检查超时时间，防止网络抖动导致误判。
*   `--start-period=120s`: 容器启动后的初始化宽限期，适应 Python 应用加载大量依赖的特性。
*   `--retries=3`: 连续 3 次失败后才标记为 unhealthy。
*   `-fsS`: curl 静默模式，仅在错误时输出信息，避免污染容器日志。

Sources: [Dockerfile](Dockerfile#L40-L41)

## 运维建议与下一步

对于本地开发或源码运行环境，建议在启动脚本或反向代理（如 Nginx/Caddy）中同样配置对 `/readyz` 的检查，以确保前端页面加载时后端已完全准备好接受 API 调用。在生产环境中，若您使用 Kubernetes 部署，请务必将 `/readyz` 配置为 `readinessProbe`，将 `/healthz` 配置为 `livenessProbe`，并合理设置 `initialDelaySeconds` 以匹配 `start-period`。此外，`/admin/dev/captures` 端点受管理员权限保护，可用于在排查问题时动态查看或清除请求抓包数据，但不应作为自动化健康检查的一部分。

掌握探针机制后，建议您继续阅读以下文档以深入理解被检查组件的内部运作：
*   [账号池：并发控制与限流冷却](10-zhang-hao-chi-bing-fa-kong-zhi-yu-xian-liu-leng-que)：了解 `account_pool` 的初始化与状态管理。
*   [后端入口与生命周期管理](14-hou-duan-ru-kou-yu-sheng-ming-zhou-qi-guan-li)：查看 `app.state` 中各组件的注入时序。
*   [限流策略与错误处理](32-xian-liu-ce-lue-yu-cuo-wu-chu-li)：理解当探针通过但请求仍被拒绝时的业务层防护机制。