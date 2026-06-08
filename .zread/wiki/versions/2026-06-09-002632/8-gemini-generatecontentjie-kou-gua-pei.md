本页文档详解 qwen2API 网关中针对 Google Gemini API (`generateContent` 与 `streamGenerateContent`) 的协议适配实现。该适配层作为统一网关架构的一部分，负责将 Gemini 原生请求归一化为内部标准格式，并在响应阶段将后端执行结果反向转换为符合 Gemini 规范的 JSON 或 SSE 流。核心设计目标是在保持 Gemini 客户端兼容性的同时，复用网关内部的工具调用解析引擎（Toolcore）与账号池管理机制，确保多协议接入的一致性与稳定性。对于希望了解整体协议转换架构的开发者，建议先阅读 [架构总览：统一网关与协议转换](5-jia-gou-zong-lan-tong-wang-guan-yu-xie-yi-zhuan-huan)。

Sources: [gemini.py](backend/api/gemini.py#L1-L30)

## 路由定义与请求入口

Gemini 接口适配通过 FastAPI Router 暴露三组路径模式，以兼容不同版本的 SDK 及直接 HTTP 调用。非流式端点 `generateContent` 与流式端点 `streamGenerateContent` 分别映射到独立的异步处理函数，两者共享请求加载与验证逻辑 `_load_and_validate_request`。该函数在入口处即完成认证上下文解析、请求体反序列化以及标准请求对象的构建，确保后续业务逻辑仅依赖归一化后的内部数据结构。值得注意的是，流式端点额外支持 `alt=sse` 查询参数，用于动态切换响应媒体类型为 Server-Sent Events，否则默认返回换行分隔的 JSON 流（JSONL）。

```mermaid
flowchart LR
    Client[Gemini Client] -->|POST /v1beta/models/{model}:generateContent| Router[FastAPI Router]
    Client -->|POST /v1beta/models/{model}:streamGenerateContent| Router
    Router --> Validate[_load_and_validate_request]
    Validate --> Auth[resolve_auth_context]
    Validate --> Normalize[normalize_gemini_request]
    Normalize --> StdReq[StandardRequest]
    StdReq --> Bridge[run_retryable_completion_bridge]
```

Sources: [gemini.py](backend/api/gemini.py#L137-L193)

## 请求归一化与消息转换

Gemini 的请求体结构（`contents`, `parts`, `functionCall`）与内部标准的 OpenAI 风格消息格式存在显著差异。`request_normalizer.py` 中的 `_normalize_gemini_messages` 函数承担了关键的协议翻译职责。它将 Gemini 的 `model` 角色映射为 `assistant`，将 `user` 角色保持不变；同时将嵌套在 `parts` 数组中的 `functionCall` 对象提取并转换为标准的 `tool_calls` 结构，将 `functionResponse` 转换为 `role: tool` 消息。在此过程中，所有客户端可见的工具名称均通过 `ToolCatalog` 映射为内部模型专用的桥接名称（如 `bridge-0`），实现了工具标识符的解耦与隔离。

| Gemini 字段 | 内部标准字段 | 转换说明 |
| :--- | :--- | :--- |
| `contents[].role: "model"` | `messages[].role: "assistant"` | 角色语义对齐 |
| `parts[].functionCall` | `messages[].tool_calls[]` | 提取并序列化为 JSON 字符串参数 |
| `parts[].functionResponse` | `messages[].role: "tool"` | 关联 tool_call_id 并保留原始响应内容 |
| `tools[].functionDeclarations` | `tools[].type: "function"` | 扁平化工具定义结构 |
| `toolConfig.functionCallingConfig.mode` | `tool_choice` | NONE→none, ANY→required, AUTO→auto |

Sources: [request_normalizer.py](backend/toolcore/request_normalizer.py#L119-L200), [gemini.py](backend/api/gemini.py#L27-L106)

## 流式响应与工具调用安全过滤

在流式生成场景下，适配器采用生产者-消费者模型处理实时数据。`on_delta` 回调函数作为消费者，接收来自执行引擎的文本增量。为防止模型输出的原始工具调用标记（如 DSML 标签）泄露给客户端，当请求包含工具定义时，系统会自动实例化 `ToolStreamSieve`。该组件充当安全过滤器，仅放行纯文本内容至响应队列，而将工具调用指令缓存至执行状态中。只有当整个流结束且确认模型意图为工具调用时，才会通过 `build_tool_directive` 构造最终的 `functionCall` 负载并追加到流末尾。这种机制确保了客户端收到的始终是合法的 Gemini 格式数据，避免了中间态语法污染。

Sources: [gemini.py](backend/api/gemini.py#L195-L288)

## 响应格式化与工具名称还原

非流式响应与流式响应的最终工具调用块均依赖 `response_formatters.py` 进行格式化。`build_gemini_generate_payload` 函数从执行状态中提取工具调用指令，并利用 `ToolCatalog` 将内部桥接名称（`bridge-N`）逆向还原为客户端请求时使用的原始工具名。这一双向映射机制是网关透明代理的核心：对上游模型而言，工具始终是受控的标准化接口；对下游 Gemini 客户端而言，工具名称与其声明完全一致。此外，响应构建器还会根据执行结果自动设置 `finishReason`，当存在有效工具调用时强制标记为 `STOP`，符合 Gemini API 的行为契约。

Sources: [response_formatters.py](backend/services/response_formatters.py#L174-L191), [gemini.py](backend/api/gemini.py#L253-L282)

## 错误处理与诊断集成

Gemini 适配器深度集成了网关的统一请求日志与诊断体系。每个请求在进入路由时即分配唯一 `req_id` 并绑定 `surface=gemini` 标签，便于在分布式日志中追踪跨协议链路。流式生成过程中的异常会被捕获并转换为 `gemini_error_chunk`，以结构化错误消息的形式返回给客户端，而非直接断开连接。对于非流式请求，未预期的异常则转化为标准的 HTTP 500 响应。这种分层错误处理策略既保证了调试信息的完整性，又维持了对外接口的健壮性。开发者可通过 [健康检查与就绪探针](33-jian-kang-jian-cha-yu-jiu-xu-tan-zhen) 进一步监控适配层的运行状态。