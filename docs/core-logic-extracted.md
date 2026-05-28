# qwen2API 核心逻辑提取文档

## 概述

本文档从qwen2API项目中提取了四个核心模块的代码和实现逻辑，供其他项目复用。

### 核心模块

1. **ToolStreamSieve** - 流式工具调用过滤器，实时检测和处理流式响应中的工具调用
2. **流式响应格式化** - 支持Anthropic/OpenAI/Gemini三种SSE格式
3. **工具名称映射** - 客户端与模型工具名之间的双向转换
4. **Gemini消息规范化** - 将Gemini API格式转换为OpenAI兼容格式

---

## 1. ToolStreamSieve 流式工具调用过滤器

### 功能说明
ToolStreamSieve是一个流式处理器，用于从LLM的流式响应中实时检测和提取工具调用。它支持多种工具调用格式：
- DSML格式（`<tool_call>`标签）
- JSON格式（`{"name":`开头）
- Markdown代码块中的工具调用

### 核心常量

```python
TOOL_START_MARKERS = ('{"name":', '<tool_call>', '##tool_call##', 'tool_call##', 'function.name:')
LEGACY_HOLD_CHARS = max(len(marker) for marker in TOOL_START_MARKERS) - 1
MAX_CAPTURE_CHARS = 64 * 1024
```

### 辅助函数

```python
import re

FENCE_OPEN_RE = re.compile(r"(?m)^[ \t]*(```+|~~~+)[^\n]*(?:\n|$)")

def _inside_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    """检查位置是否在指定范围内"""
    return any(start <= pos < end for start, end in spans)

def _unclosed_markdown_code_start(text: str) -> int:
    """查找未闭合的Markdown代码块开始位置"""
    pos = 0
    while True:
        opener = FENCE_OPEN_RE.search(text, pos)
        if opener is None:
            break
        fence = opener.group(1)
        closer = re.compile(rf"(?m)^[ \t]*{re.escape(fence)}[ \t]*(?:\n|$)").search(text, opener.end())
        if closer is None:
            return opener.start()
        pos = closer.end()
    return -1

def _find_legacy_tool_start(text: str) -> int:
    """查找传统格式工具调用的开始位置"""
    lowered = text.lower()
    positions: list[int] = []
    for marker in TOOL_START_MARKERS:
        pos = lowered.find(marker)
        if pos >= 0:
            positions.append(pos)
    return min(positions) if positions else -1

def looks_like_tool_fragment(text: str) -> bool:
    """判断文本是否看起来像工具调用片段"""
    if not text:
        return False
    if '<tool_call>' in text.lower() and '</tool_call>' not in text.lower():
        return True
    if '{"name":' in text.lower():
        return True
    return False
```

### ToolStreamSieve 类实现

```python
from typing import Any

class ToolStreamSieve:
    """流式工具调用过滤器
    
    实时检测和处理流式响应中的工具调用，将文本内容和工具调用分开。
    """
    
    def __init__(self, tool_names: list[str]):
        """初始化过滤器
        
        Args:
            tool_names: 允许的工具名称列表
        """
        self.tool_names = [name for name in tool_names if isinstance(name, str) and name]
        self.pending = ""
        self.capture = ""
        self.capturing = False
    
    def process_chunk(self, chunk: str) -> list[dict[str, Any]]:
        """处理一个chunk，返回事件列表
        
        Args:
            chunk: 流式响应的一个chunk
            
        Returns:
            事件列表，每个事件是 {"type": "content", "text": ...} 或 {"type": "tool_calls", "calls": [...]}
        """
        if not chunk:
            return []
        self.pending += chunk
        return self._drain_pending()
    
    def _drain_pending(self) -> list[dict[str, Any]]:
        """消耗pending缓冲区，生成事件"""
        events: list[dict[str, Any]] = []
        
        while True:
            if self.capturing:
                self.capture += self.pending
                self.pending = ""
                prefix, calls, suffix, ready = self._consume_capture()
                if not ready:
                    if len(self.capture) > MAX_CAPTURE_CHARS:
                        events.append({"type": "content", "text": self.capture})
                        self.capture = ""
                        self.capturing = False
                    return events
                if prefix:
                    events.append({"type": "content", "text": prefix})
                if calls:
                    events.append({"type": "tool_calls", "calls": calls})
                self.pending = suffix
                self.capture = ""
                self.capturing = False
                if not self.pending:
                    return events
                continue
            
            start = self._find_tool_start(self.pending)
            if start >= 0:
                prefix = self.pending[:start]
                if prefix:
                    events.append({"type": "content", "text": prefix})
                self.capture = self.pending[start:]
                self.pending = ""
                self.capturing = True
                continue
            
            safe, hold = self._split_safe_content(self.pending)
            if safe:
                events.append({"type": "content", "text": safe})
            self.pending = hold
            return events
    
    def flush(self) -> list[dict[str, Any]]:
        """刷新所有缓冲内容，返回剩余事件"""
        events: list[dict[str, Any]] = []
        if self.capturing and self.capture:
            prefix, calls, suffix, ready = self._consume_capture()
            if ready:
                if prefix:
                    events.append({"type": "content", "text": prefix})
                if calls:
                    events.append({"type": "tool_calls", "calls": calls})
                if suffix:
                    events.append({"type": "content", "text": suffix})
            else:
                events.append({"type": "content", "text": self.capture})
            self.capture = ""
            self.capturing = False
        if self.pending:
            events.append({"type": "content", "text": self.pending})
            self.pending = ""
        return events
    
    def _find_tool_start(self, text: str) -> int:
        """查找工具调用的开始位置"""
        return _find_legacy_tool_start(text)
    
    def _consume_capture(self) -> tuple[str, list[dict[str, Any]], str, bool]:
        """消费捕获的内容，提取工具调用"""
        if not self.capture:
            return "", [], "", False
        
        lowered = self.capture.lower()
        if "<tool_call>" in lowered and "</tool_call>" not in lowered:
            return "", [], "", False
        
        # 尝试解析JSON格式工具调用
        # 这里需要根据实际情况实现解析逻辑
        if looks_like_tool_fragment(self.capture):
            return "", [], "", False
        
        return self.capture, [], "", True
    
    def _split_safe_content(self, text: str) -> tuple[str, str]:
        """分割安全内容和可能的工具调用前缀"""
        # 1. 检查未闭合代码块
        unclosed_start = _unclosed_markdown_code_start(text)
        if unclosed_start >= 0:
            return text[:unclosed_start], text[unclosed_start:]
        
        # 2. 检查DSML前缀
        folded = text.lower()
        if folded.startswith('<') and not folded.startswith('</'):
            if folded.startswith('<|') or folded.startswith('<!') or folded.startswith('<?'):
                return "", text
            if len(folded) <= len('<|DSML|'):
                return "", text
        
        # 3. 传统格式缓冲
        if len(text) <= LEGACY_HOLD_CHARS:
            is_marker_prefix = any(
                marker.lower().startswith(folded) or folded.startswith(marker.lower())
                for marker in TOOL_START_MARKERS
            )
            if is_marker_prefix:
                return "", text
            return text, ""
        
        # 4. 长文本处理
        suffix = text[-LEGACY_HOLD_CHARS:]
        lowered_suffix = suffix.lower()
        
        if '<|' in suffix or '<!' in suffix or '<?' in suffix:
            return text[:-LEGACY_HOLD_CHARS], suffix
            
        is_suffix_marker_prefix = any(
            marker.lower().startswith(lowered_suffix) or lowered_suffix.startswith(marker.lower())
            for marker in TOOL_START_MARKERS
        )
        
        if is_suffix_marker_prefix:
            return text[:-LEGACY_HOLD_CHARS], suffix
        else:
            return text, ""
```

---

## 2. 流式响应格式化函数

### 功能说明
这些函数用于将统一的内部格式转换为不同AI服务提供商的SSE格式：
- **Anthropic格式**: 用于Claude API兼容
- **OpenAI格式**: 用于GPT API兼容  
- **Gemini格式**: 用于Google Gemini API兼容

### Anthropic格式函数

```python
import json
from typing import Any


def anthropic_message_start(msg_id: str, model_name: str, usage: dict[str, Any]) -> str:
    """Anthropic消息开始事件"""
    payload = {
        "type": "message_start",
        "message": {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model_name,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": usage,
        },
    }
    return f"event: message_start\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def anthropic_content_block_start(index: int, content_block: dict[str, Any]) -> str:
    """Anthropic内容块开始事件"""
    return f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': index, 'content_block': content_block}, ensure_ascii=False)}\n\n"


def anthropic_content_block_delta(index: int, delta: dict[str, Any]) -> str:
    """Anthropic内容块增量事件"""
    return f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': index, 'delta': delta}, ensure_ascii=False)}\n\n"


def anthropic_content_block_stop(index: int) -> str:
    """Anthropic内容块停止事件"""
    return f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': index}, ensure_ascii=False)}\n\n"


def anthropic_message_delta(stop_reason: str, output_tokens: int) -> str:
    """Anthropic消息增量事件"""
    return f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': stop_reason}, 'usage': {'output_tokens': output_tokens}}, ensure_ascii=False)}\n\n"


def anthropic_message_stop() -> str:
    """Anthropic消息停止事件"""
    return f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'}, ensure_ascii=False)}\n\n"
```

### OpenAI格式函数

```python
def openai_chunk(completion_id: str, created: int, model_name: str, delta: dict[str, Any], finish_reason: str | None = None) -> str:
    """OpenAI流式响应chunk"""
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_name,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def openai_done() -> str:
    """OpenAI流式响应结束标记"""
    return "data: [DONE]\n\n"
```

### Gemini格式函数

```python
def gemini_text_chunk(text: str) -> str:
    """Gemini文本chunk（非SSE格式）"""
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": text}],
                    "role": "model",
                }
            }
        ]
    }
    return json.dumps(payload, ensure_ascii=False) + "\n"


def gemini_sse_chunk(text: str) -> str:
    """Return Gemini chunk in SSE format (for ?alt=sse)."""
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": text}],
                    "role": "model",
                }
            }
        ]
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def gemini_error_chunk(message: str) -> str:
    """Gemini错误chunk"""
    return json.dumps({"error": message}, ensure_ascii=False) + "\n"
```

---

## 3. 工具名称映射逻辑

### 功能说明
工具名称映射函数负责在客户端可见的工具名称和模型内部使用的工具名称之间进行转换。这在使用桥接工具（如bridge-0, bridge-1）时特别重要。

### 核心函数实现

```python
def _tool_name_map_entries(standard_request) -> list[dict[str, str]]:
    """生成工具名称映射条目
    
    Args:
        standard_request: 标准化请求对象，包含tools和tool_catalog属性
        
    Returns:
        包含model、canonical、client三个名称的映射列表
        - model: 模型实际使用的工具名称（如bridge-0）
        - canonical: 规范化名称（如get_weather）
        - client: 客户端显示的名称
    """
    entries: list[dict[str, str]] = []
    catalog = standard_request.tool_catalog
    for tool in standard_request.tools:
        model_name = str(tool.get("name") or "")
        canonical_name = catalog.get_canonical_name(model_name) if catalog is not None else None
        canonical_name = canonical_name or model_name
        client_name = catalog.get_client_name(canonical_name) if catalog is not None else model_name
        entries.append({"model": model_name, "canonical": canonical_name, "client": client_name})
    return entries
```

### 名称转换流程
1. **model_name**: 模型实际使用的工具名称（如bridge-0）
2. **canonical_name**: 规范化名称（如get_weather）
3. **client_name**: 客户端显示的名称（可能包含前缀或后缀）

---

## 4. Gemini消息规范化处理

### 功能说明
Gemini消息规范化函数将Gemini API格式的消息转换为OpenAI兼容格式，处理：
- 角色转换（model → assistant）
- 函数调用格式转换
- 函数响应格式转换
- 工具名称映射

### 核心函数实现

```python
import json
from typing import Any


def _normalize_gemini_messages(contents: Any, *, tool_catalog=None) -> list[dict[str, Any]]:
    """规范化Gemini消息格式为OpenAI格式
    
    Args:
        contents: Gemini格式的消息列表
        tool_catalog: 工具目录（用于名称映射）
        
    Returns:
        OpenAI格式的消息列表
    """
    if contents is None:
        return []
    if not isinstance(contents, list):
        raise ValueError("contents must be a list")
    
    messages: list[dict[str, Any]] = []
    call_id_counter = 0
    
    for message in contents:
        if not isinstance(message, dict):
            continue
        role = "assistant" if message.get("role") == "model" else "user"
        text_parts: list[str] = []
        function_calls: list[dict[str, Any]] = []
        function_responses: list[dict[str, Any]] = []
        
        for part in message.get("parts", []) or []:
            if not isinstance(part, dict):
                continue
            # 处理文本部分
            text = part.get("text")
            if isinstance(text, str) and text:
                text_parts.append(text)
            # 处理functionCall部分（来自model消息）
            elif "functionCall" in part:
                fc = part["functionCall"]
                if isinstance(fc, dict):
                    function_calls.append(fc)
            # 处理functionResponse部分（来自user消息）
            elif "functionResponse" in part:
                fr = part["functionResponse"]
                if isinstance(fr, dict):
                    function_responses.append(fr)
        
        # 发送文本消息
        if text_parts:
            messages.append({"role": role, "content": "\n".join(text_parts)})
        
        # 处理model消息中的functionCall
        # 转换Gemini functionCall → OpenAI assistant message with tool_calls
        if function_calls and role == "assistant":
            tool_calls = []
            for fc in function_calls:
                call_id = f"call_{call_id_counter}"
                call_id_counter += 1
                name = str(fc.get("name", ""))
                args = fc.get("args") or fc.get("arguments") or {}
                # 将客户端可见名称转换为模型名称（bridge-N）
                if tool_catalog is not None:
                    model_name = tool_catalog.get_model_name(name)
                    if model_name is not None:
                        name = model_name
                # 将args序列化为JSON字符串（OpenAI格式要求字符串）
                if isinstance(args, dict):
                    args_str = json.dumps(args, ensure_ascii=False)
                else:
                    args_str = str(args) if args else "{}"
                tool_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": args_str,
                    },
                })
            if tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": tool_calls,
                })
        
        # 处理user消息中的functionResponse
        # 转换Gemini functionResponse → OpenAI tool role message
        if function_responses and role == "user":
            for fr in function_responses:
                fr_name = str(fr.get("name", ""))
                response_data = fr.get("response")
                # 将客户端可见名称转换为模型名称（bridge-N）
                if tool_catalog is not None:
                    model_name = tool_catalog.get_model_name(fr_name)
                    if model_name is not None:
                        fr_name = model_name
                # 序列化响应内容
                if isinstance(response_data, dict):
                    content = json.dumps(response_data, ensure_ascii=False)
                elif isinstance(response_data, str):
                    content = response_data
                else:
                    content = str(response_data) if response_data else ""
                call_id = f"call_{call_id_counter}"
                call_id_counter += 1
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": fr_name,
                    "content": content,
                })
    
    return messages
```

---

## 5. 使用示例

### ToolStreamSieve 使用示例

```python
# 初始化过滤器
sieve = ToolStreamSieve(tool_names=["get_weather", "search_web"])

# 处理流式响应
for chunk in stream_response:
    events = sieve.process_chunk(chunk)
    for event in events:
        if event["type"] == "content":
            print(event["text"], end="", flush=True)
        elif event["type"] == "tool_calls":
            print(f"\n工具调用: {event['calls']}")

# 刷新剩余内容
final_events = sieve.flush()
for event in final_events:
    if event["type"] == "content":
        print(event["text"], end="", flush=True)
```

### 流式响应格式化示例

```python
# OpenAI格式
yield openai_chunk("chatcmpl-123", 1234567890, "gpt-4", {"content": "Hello"}, None)
yield openai_chunk("chatcmpl-123", 1234567890, "gpt-4", {}, "stop")
yield openai_done()

# Anthropic格式
yield anthropic_message_start("msg_123", "claude-3", {"input_tokens": 100})
yield anthropic_content_block_start(0, {"type": "text", "text": ""})
yield anthropic_content_block_delta(0, {"text": "Hello"})
yield anthropic_content_block_stop(0)
yield anthropic_message_delta("end_turn", 50)
yield anthropic_message_stop()

# Gemini格式
yield gemini_sse_chunk("Hello")
yield gemini_sse_chunk(" world")
```

### 工具名称映射示例

```python
# 假设standard_request包含工具定义
entries = _tool_name_map_entries(standard_request)
# 输出示例: [{"model": "bridge-0", "canonical": "get_weather", "client": "get_weather"}]
```

### Gemini消息规范化示例

```python
# Gemini格式输入
gemini_messages = [
    {"role": "user", "parts": [{"text": "What's the weather?"}]},
    {"role": "model", "parts": [
        {"text": "I'll check the weather for you."},
        {"functionCall": {"name": "get_weather", "args": {"location": "Beijing"}}}
    ]},
    {"role": "user", "parts": [
        {"functionResponse": {"name": "get_weather", "response": {"temp": 25, "condition": "sunny"}}}
    ]}
]

# 转换为OpenAI格式
openai_messages = _normalize_gemini_messages(gemini_messages, tool_catalog=catalog)
```

---

## 6. 注意事项

### 依赖关系
这些模块依赖以下内部模块：

1. **backend.services.tool_parser** - `parse_tool_calls_silent`
   - 用于解析工具调用文本
   - 支持多种工具调用格式

2. **backend.toolcall.formats_dsml** - `consume_dsml_tool_capture`, `has_open_dsml_tool_tag`
   - DSML格式工具调用处理
   - 用于检测和消费DSML标记

3. **backend.toolcall.markup_scan** - `find_partial_tool_markup_start`, `find_tool_markup_tag_outside_ignored`
   - 标记扫描工具
   - 用于查找工具调用标记的开始位置

4. **backend.toolcore.tool_catalog** - `ToolCatalog`类
   - 工具目录管理
   - 提供工具名称映射功能

### 集成注意事项

1. **流式处理顺序**
   - ToolStreamSieve必须按顺序处理chunk
   - 不能跳过或重复处理chunk
   - 必须在流结束时调用flush()

2. **工具名称一致性**
   - 工具名称映射必须在请求整个生命周期内保持一致
   - 模型名称、规范名称、客户端名称三者必须正确映射

3. **格式兼容性**
   - 不同AI服务提供商的格式有细微差别
   - 必须使用对应的格式化函数
   - 注意SSE格式的换行符要求（\n\n）

4. **错误处理**
   - 工具调用解析失败时应返回原始文本
   - 超过大小限制的捕获内容应直接输出
   - 网络错误或格式错误应有适当的错误处理

5. **性能考虑**
   - ToolStreamSieve使用缓冲机制，可能增加少量延迟
   - 大文本处理时注意内存使用
   - 复杂的工具调用格式可能影响解析速度

---

*文档生成时间: 2026-05-28*
*来源: qwen2API项目 backend模块*
