WebUI 管理台是 qwen2API 企业网关的可视化运维中枢，专为初学者和运维人员设计，用于实时监控网关状态、管理上游账号池、分发下游 API Key 以及动态调整运行时配置。该管理台采用前后端分离架构，前端基于 React + Vite 构建，通过 RESTful API 与后端 FastAPI 服务交互，所有敏感操作均需通过 Admin Key 进行身份验证。本文档将指导您完成从首次登录到日常运维的核心操作流程，帮助您快速掌握网关的可视化管理能力。

Sources: [admin.py](backend/api/admin.py#L79-L89), [Dashboard.tsx](frontend/src/pages/Dashboard.tsx#L113-L141)

## 认证与会话管理

WebUI 的所有管理接口均受 `verify_admin` 依赖项保护，首次使用时必须在「系统设置」页面配置有效的会话密钥。系统支持两种认证凭证：环境变量中配置的 `ADMIN_KEY` 或任意已生成的下游 API Key。前端会将密钥存储在浏览器的 `localStorage` 中（键名为 `qwen2api_key`），后续请求自动携带 `Authorization: Bearer <token>` 头。若密钥无效或未设置，仪表盘等数据面板将显示“状态获取失败”提示，此时需前往设置页重新输入并保存。

Sources: [admin.py](backend/api/admin.py#L79-L89), [SettingsPage.tsx](frontend/src/pages/SettingsPage.tsx#L26-L71)

## 仪表盘：全局监控概览

仪表盘（Dashboard）是登录后的默认首页，通过 3 秒轮询机制实时展示网关核心指标。页面顶部提供六个维度标签页：**概览**显示账号池健康度与 Chat ID 预热池命中率；**性能趋势**绘制 QPS 与延迟 P50/P95/P99 曲线；**模型分布**以饼图呈现各模型调用占比；**错误分析**统计 HTTP 状态码分布；**Token 概览**追踪 Prompt/Completion Token 消耗趋势；**API Key 排行**列出下游密钥的请求量与 Token 用量 Top 榜。所有图表数据源自 `/api/admin/metrics` 接口，账号级诊断信息则来自 `/api/admin/status` 接口中的 `per_account` 字段。

Sources: [Dashboard.tsx](frontend/src/pages/Dashboard.tsx#L104-L181), [admin.py](backend/api/admin.py#L107-L130)

## 账号池管理

账号池管理页面是运维上游 Qwen 账号的核心工作台，支持单个添加、批量导入、状态验证及记忆清理等操作。由于当前部署模式为轻量无浏览器镜像，系统**不支持**自动注册新账号或页面式激活，所有账号必须通过手动获取 Token 或邮箱密码登录后导入。添加账号时，可勾选「清空记忆」、「关闭记忆更新」和「清空聊天记录」三个选项，防止历史上下文污染网关请求。批量导入支持 `email:password` 或 `email:password|proxy_url` 格式，系统会自动校验代理协议合法性（http/https/socks5）并跳过已存在账号。

Sources: [AccountsPage.tsx](frontend/src/pages/AccountsPage.tsx#L119-L196), [admin.py](backend/api/admin.py#L153-L229), [admin.py](backend/api/admin.py#L277-L403)

### 账号状态标识说明

| 状态码 | UI 显示 | 含义与处理建议 |
| :--- | :--- | :--- |
| `valid` | 可用（绿色） | 账号正常，可参与请求调度 |
| `pending_activation` | 未激活（橙色） | 需手动获取 Token 后重新添加 |
| `rate_limited` | 限流（黄色） | 触发上游频率限制，等待冷却结束 |
| `banned` | 封禁（红色） | 账号被上游永久封禁，建议删除 |
| `auth_error` | 认证失效（灰色） | Token 过期且刷新失败，需更新凭证 |
| `cooldown` | 冷却中（灰色） | 主动熔断保护，倒计时结束后自动恢复 |

Sources: [AccountsPage.tsx](frontend/src/pages/AccountsPage.tsx#L47-L101)

## API Key 分发与管理

API Key 页面用于管理下游客户端访问网关的凭证。点击「生成新 Key」可创建带备注的密钥，生成后自动复制到剪贴板。每个 Key 支持独立查看使用统计（按模型维度的请求数与 Token 总量），便于成本分摊与异常检测。删除操作不可逆，需谨慎执行。值得注意的是，此处生成的 Key 不仅可用于下游 API 调用，也可直接作为 WebUI 的管理员认证凭证，实现了权限体系的统一。

Sources: [TokensPage.tsx](frontend/src/pages/TokensPage.tsx#L31-L131), [admin.py](backend/api/admin.py#L86-L88)

## 系统设置与动态调优

系统设置页面允许在不重启服务的情况下调整关键运行时参数。**并发控制**区域可设置单账号最大并发数（1-10）和全局并发上限（0-200），修改后立即生效；**预热池配置**区域管理 Chat ID 预热目标数、TTL（分钟）、预热模型列表及总预热上限，这些参数影响首请求延迟表现；**模型映射**区域接受 JSON 格式的别名规则，实现请求模型的透明重定向。此外，页面底部提供当前网关地址的 cURL 调用示例，方便开发者快速验证连通性。所有配置变更通过 PUT `/api/admin/settings` 接口持久化，部分参数（如预热池）在下一轮刷新周期生效。

Sources: [SettingsPage.tsx](frontend/src/pages/SettingsPage.tsx#L79-L171), [SettingsPage.tsx](frontend/src/pages/SettingsPage.tsx#L173-L200)

## 推荐阅读路径

完成 WebUI 基础操作后，建议按以下顺序深入理解网关机制：
1. [账号池：并发控制与限流冷却](10-zhang-hao-chi-bing-fa-kong-zhi-yu-xian-liu-leng-que) — 理解账号状态流转与熔断策略
2. [会话管理与Chat ID预热池](11-hui-hua-guan-li-yu-chat-idyu-re-chi) — 掌握预热池参数调优原理
3. [模型映射与别名策略](28-mo-xing-ying-she-yu-bie-ming-ce-lue) — 配置多模型路由规则
4. [限流策略与错误处理](32-xian-liu-ce-lue-yu-cuo-wu-chu-li) — 排查生产环境异常