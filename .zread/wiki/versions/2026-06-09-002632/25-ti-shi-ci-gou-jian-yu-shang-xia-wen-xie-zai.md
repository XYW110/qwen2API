本页深入解析 qwen2API 网关中**提示词构建（Prompt Building）**与**上下文卸载（Context Offloading）**的核心机制。作为连接异构客户端协议与 Qwen 上游模型的关键中间层，该模块不仅负责将 OpenAI/Anthropic/Gemini 等格式的请求归一化为 Qwen 可理解的纯文本流，还通过智能卸载策略解决长上下文与工具定义导致的 Token 溢出问题。其核心设计目标是：**在保持语义完整性的前提下，最大化利用 Qwen 的文本理解能力来模拟原生工具调用与多模态交互**。

## 架构概览：从协议适配到文本合成

提示词构建并非简单的字符串拼接，而是一个包含“清洗-序列化-卸载-注入”四阶段的精密流水线。系统首先根据 `client_profile` 识别请求来源（如 Claude Code、OpenClaw 或标准 OpenAI），应用针对性的内容清洗策略；随后通过 `ContextOffloader` 评估上下文负载，决定采用内联（Inline）、混合（Hybrid）还是全文件（File）模式；最终由 `prompt_builder` 将历史消息、工具指令与系统提示合成为符合 DSML（Document Structure Markup Language）规范的单一文本流。

```mermaid
flowchart TD
    Request[客户端请求] --> Normalize[请求归一化]
    Normalize --> Profile{Client Profile?}
    
    Profile -->|Claude/Qwen Code| SanitizeHeavy[重型工具清洗<br/>省略大文本字段]
    Profile -->|OpenClaw| SanitizeOC[OpenClaw清洗<br/>剥离元数据/提取任务]
    Profile -->|Standard| SanitizeStd[标准清洗]
    
    SanitizeHeavy --> Offload{上下文长度评估}
    SanitizeOC --> Offload
    SanitizeStd --> Offload
    
    Offload -->|< Inline Max| ModeInline[Inline模式<br/>直接拼接消息]
    Offload -->|< Force File Max| ModeHybrid[Hybrid模式<br/>历史转文件+保留最新轮次]
    Offload -->|> Force File Max| ModeFile[File模式<br/>全量历史转附件]
    
    ModeInline --> BuildPrompt[提示词合成]
    ModeHybrid --> GenFiles[生成上下文文件] --> BuildPrompt
    ModeFile --> GenFiles
    
    BuildPrompt --> InjectDSML[注入DSML工具<blog>
#指令块]
 提示词构建    InjectDSML --> Final与上下文卸载Prompt[最终Qwen Prompt]


在 qwen2```

该架构确保了无论原始请求多么复杂API 网关中，发送给 Qwen 的，**提示词始终是一个结构清晰构建（Prompt Building、重点突出的文本对象）**与**上下文，同时将原本属于卸载（Context Offloading）**是确保大语言模型（ API 层面的工具LLM）能够高效、准确处理复杂请求的核心机制。由于定义转化为模型可通过上游模型（如阅读理解执行的“文档”。

Sources: [prompt Qwen）对输入长度_builder.py](backend/toolcore/prompt_builder.py#L1-L35有限制，且不同), [context_offload.py](backend/toolcore/context_offload.py#L44-L6客户端协议（OpenAI,4)

## 上下文卸载策略 Anthropic, Gemini）对消息结构的定义各异，网关必须在将请求转发给上游之前：动态负载管理

`ContextOffloader`，进行标准化的内容 是防止 Token 超限的第一道防线。清洗、历史消息它不依赖精确的 Tokenizer 计数，而是采用基于字符长度的启发压缩以及工具调用式估算（`estimate_prompt_len`），对每条指令的注入。

本消息增加 24 字符的开销预留，并对特定 Profile页面深入解析 `backend（如 Claude Code/toolcore`）额外增加 512 字符的系统 模块中的三个关键组件：
1. **`prompt_builder.py`**：负责消息内容的提取、清洗开销缓冲。这种轻量级估算避免了与标准化。
2. **在热路径上执行`context_offload昂贵的分词操作。

### 三种卸载模式详解.py`**：负责评估

| 模式 | 触发条件 | 行为描述 | 适用场景 |
| :上下文大小，并将--- | :---超长历史记录或工具定义“ | :--- |卸载”为附件文件。
3. **`prompt_contract.py :--- |
| **inline**`**：定义工具调 | 预估长度 ≤ `CONTEXT_INLINE_MAX_CHARS` 且无工具定义用的 DSML（ | 所有消息保持原始结构直接内联 | 短对话、简单Domain Specific Markup Language）格式问答 |
| **hybrid** | 预估长度 > Inline阈值 且 ≤ `CONTEXT_FORCE_FILE_MAX_CHARS`及指令块生成 | 历史消息序列化为 `qwen2api_context_history.txt`，但保留最新的逻辑。

Sources:用户/助手交互 [backend/toolcore内联 | 中等/prompt_builder.py](backend/toolcore/prompt_builder.py长度对话、需), [backend/toolcore保持即时响应感 |
| **file** | 预估长度/context_offload.py > `CONTEXT_FORCE_FILE_MAX_CHARS`](backend/toolcore | 全量历史（含最新/context_offload.py轮次）均), [backend/tool转为文件附件，仅保留系统提示core/prompt_contract与工具指令 | 超长.py](backend/tool文档分析、代码库core/prompt_contract级重构 |

当.py)

##触发文件卸载时，系统会 架构概览生成两类虚拟文件：
1.  **历史上下文文件** (`qwen2api_context_history.txt`)：按 `：从原始消息## Message {idx} [{role}]` 格式序列化到上游 Payload

提示对话流，确保模型能通过阅读文件还原对话脉络。
2.  **工具定义文件** (`qwen2api_tools词构建并非简单的字符串.txt`)：当拼接，而是一个多存在工具定义时，阶段的流水线过程。其核心目标是将异构的客户端请求转化为上游 Qwen 模型可将其序列化为独立的理解的单一文本流或结构化消息，文本文件而非内联 JSON Schema，大幅减少同时通过“卸载主提示词的认知噪声。

Sources: [context_offload.py](backend/toolcore/context_offload”机制突破上下文窗口限制。.py#L160-L200), [

```mermaid
graph TD
    A[原始 Messages + Tools] --> B{ContextOffloader.plan}
    B -->|估算context_offload.py长度| C[Estimate Prompt Len]
    C](backend/toolcore -->|超过阈值?| D[Generate/context_offload.py Context Files]
    C -->|未超过| E[Inline Mode#L14]
    D --> F[Hy2-L158)

##brid/File Mode] 提示词合成与内容
    F --> G[Serialize清洗

`prompt_builder.py` 承担了最终的文本组装工作。其核心逻辑在于根据 ` History to Text]client_profile` 对内容进行差异化处理，确保上游模型接收到的是
    G --> H[Create LocalContext“干净”且File]“安全”的输入。

### 客户端感知的清洗策略

*  
    E --> I[PromptBuilder.build]
    H **OpenClaw 专用清洗**：针对 OpenClaw 客户端，系统会自动剥离用户消息中的不可信元 --> I
    I -->数据（`strip_openclaw_untrusted_metadata`），并分离“记忆 J[Sanitize召回/Wiki编译 Content”等系统生成的]
    J上下文块与用户的 --> K[Extract实际任务指令。这防止了模型将系统注入的背景信息误认为是 System/User Text]
    K --> L[用户的直接命令。
*  Render Tool Instructions]
    L **重型工具 Profile --> M[Final 压缩**： Prompt String]
    M --> N[Upstream Payload]对于 Claude Code 和
    
    style D fill:#e1f5fe,stroke:#0 Qwen Code 1579b
    style H fill:#e等涉及大量文件操作的客户端1f5fe,stroke:#01579b
    style，历史工具调用中的大文本字段（如 `content`, `old_string`, `patch L fill:#fff3`）若超过 160 字符，e0,stroke:#e会被替换为 `[omitted N chars]` 占位符65100
```。同时，仅保留 `Write/Edit` 等关键操作的路径与摘要

该流程确保了参数，避免历史编辑记录耗尽上下文窗口。
*   **系统提示净化即使面对数千行的**：所有 `system` 角色内容均经过 `sanitize_runtime_prompt_text代码历史或复杂的` 处理，工具定义，网关移除可能干扰 Qwen 行为的也能通过生成虚拟冗余标记或冲突文件（如 `qwen2api_context_history.txt`）的方式，指令。

Sources: [prompt_builder.py](backend/tool让模型以“阅读文件”的形式获取core/prompt_builder上下文，而非直接占用宝贵的.py#L36-L104), [prompt_contract Token 窗口。

Sources:.py](backend/toolcore/prompt_contract [backend/toolcore.py#L19-L36)

## DS/context_offload.pyML 工具桥接协议

由于](backend/toolcore Qwen 原生/context_offload.py不支持外部定义的任意#L16工具调用，q0-L18wen2API 设计了 **DSML (Document Structure Markup Language)** 作为6)

## 上下文中间表示层。这是一种基于 XML 的严格序列化格式，被卸载策略 (Context Offloading注入到提示词的 `=== MANDATORY TOOL CALL INSTRUCTIONS ===)

### 1. 长度估算与模式` 块中选择

`ContextOffloader` 类首先通过 `estimate_prompt_len。

### 为什么选择 DSML 而非 JSON？

1. ` 方法粗略 **抗幻觉性**：XML 标签的闭合特性比计算当前消息列表 JSON 的括号匹配和工具定义的字符更容易被 LLM 稳定生成，尤其是在流式输出场景下。
2.  **CDATA 安全封装**：所有总数。根据配置字符串参数强制使用项 `<![CDATA[...]]>` 包裹，彻底解决了 `CONTEXT_INLINE_MAX_CHARS`代码片段、正则表达式或特殊字符 和 `CONTEXT导致的格式解析错误。
_FORCE_FILE_MAX_CHARS`，系统决定采用3.  **以下三种模式之一：

| 模式 | 触发条件 | 行为描述 |桥接槽位映射**：提示词中仅暴露 `bridge-0`, `bridge-1
| :--- |` 等抽象槽位名， :--- | :而非真实的工具函数--- |
|名。这层 **Inline** |间接映射允许网关在不改变模型提示词的情况下 总长度 < `动态重命名或替换后端工具实现。

### 指令块结构

`INLINE_MAX` | 所有消息build_tool_instruction_block` 生成的指令直接保留在消息块包含三个关键部分：
1.  **列表中，无额外文件免责声明**：明确告知生成。 |模型这些是“网
| **Hybrid关注入的桥接工具”，禁止调用平台原生工具系统。** | `INLINE_MAX`
2.  < 长度 <= **格式规范**：提供精确的 DSML  `FORCE_FILE_MAX模板与 CDATA 使用规则。
3` | 历史记录.  **约束注入**：根据 `tool_choice` 参数动态被序列化为文本并插入强制调用（`required`）或禁止调用（`none生成附件文件，但最新`）的双语指令，确保用户消息仍以内模型行为严格遵循客户端意图。

Sources: [prompt_contract.py](backend/toolcore联方式发送，以保持/prompt_contract.py#L141-L200), [prompt对话连贯性。 |_contract.py](backend/toolcore/prompt
| **File_contract.py#L** | 长度70-L80)

##  > `FORCE_FILE_MAX` |历史记录的工具调用渲染

在构建多轮对话提示词时，历史的 几乎所有历史上下文都被卸载为工具调用不能以文件，仅保留原始 JSON 形式呈现，而必须转换为与当前 DSML 协议一致的格式。`极少量的近期交互render_history_tool_call` 函数负责这一转换，它复用 `compact_history_tool或直接依赖文件引用_input` 的压缩逻辑，并将结果渲染为标准的。 |

Sources: [ DSML `<|DSML|invoke>` 块backend/toolcore/context。

对于工具_offload.py](返回结果（`tool_result`），系统backend/toolcore/context_offload.py#L48-L63), [backend/toolcore/context_offload.py](backend/toolcore/context_offload.py将其包装在 `[Tool Result for#L16 call {id}]...[/Tool Result]0-L18` 标记中。6)

###这种显式的边界 2.标记帮助模型区分“助手发出的调用”与“环境 历史消息序列化

当返回的结果”，维持需要卸载时，`了对话状态机的逻辑一致性。即使在上下文被卸载到plan` 方法会将文件中后，这种结构化消息列表转换为结构标记依然能让模型通过文件检索化的文本块。准确定位历史信息。

Sources: [prompt_builder.py](backend/toolcore/prompt_builder.py#L10每条消息被格式化为 `##6-L145), [prompt_contract Message {idx}.py](backend/toolcore/prompt_contract [{role}]`.py#L70-L79)

## 延伸阅读

*   理解提示 标题，后词构建的前置步骤：[请求归一化与单次飞行控制](26-qing-qiu-gui-h跟提取出的纯ua-yu-dan-ci-fei-xing-kong-zhi)
文本内容。这种*   了解 DSML 输出如何被解析回结构化响应：[流式状态机与工具格式便于模型识别调用幻觉防护](消息边界和角色归属。

```python
#24-liu-shi-zhu 序列化示例逻辑ang-tai-
for idx, msg in enumerate(messages or [], 1):ji-yu-g
    role = msg.get("role", "unknown")
    text = self._extract_text(msgong-ju-d)
    if not text.strip():
        continue
    serialized_parts.appendiao-yong-h(f"## Messageuan-jue-fang-hu) {idx} [{
*   掌握工具定义的来源与管理：[Toolcore V2：role}]\n{text.strip指令解析与策略执行](23-toolcore-v2()}\n")-zhi-ling-jie-xi
```

生成的-yu-ce-lue-zhi-xing)