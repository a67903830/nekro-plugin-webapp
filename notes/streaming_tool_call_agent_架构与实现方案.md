# Streaming Tool-Call Agent 架构与实现方案

> 本文档面向 **AI IDE / Agent Runtime / 自动化重构系统** 的工程实现者，
> 系统性说明一种 **支持 streaming、连续 tool_call、及时止损、低 token 浪费** 的 Agent 架构方案。
>
> 重点不局限于示例，而是抽象为 **可复用的工程模式**。

---

## 1. 背景与问题定义

在文件读写、代码重构、批量编辑等 Agent 场景中，存在以下典型痛点：

1. **一个任务需要多次工具调用**（如：读 → 改 → 写 → 校验）
2. 工具参数可能很长（整段代码 / 文件 diff）
3. 工具执行存在失败风险，需要 **尽早止损**
4. 希望 **减少重复请求**，避免浪费输入 token
5. 希望模型在一次生成中完成“决策 + 多步行动”

传统做法的问题：

- 每一步一个 LLM 请求 → token 成本高
- 必须等上一步工具执行完成 → latency 高
- 无法 streaming → 失败时浪费大量输出

---

## 2. 核心思想（结论先行）

**使用 Streaming + 连续 tool_call，是目前最符合 Agent 场景的模式之一。**

关键结论：

- OpenAI 协议 **允许一个 response 中出现多个 tool_call**
- tool_call 本身是 **流式、可分段、可早执行的**
- tool_call 的边界是 **隐式的**（不是显式 end 标记）

这使得我们可以构建：

> **“模型持续生成 → runtime 按顺序执行工具 → 出错立即终止”**

---

## 3. Streaming Tool-Call 的协议语义

### 3.1 tool_call 是如何在 stream 中出现的

在 streaming 模式下：

- tool_call 会出现在 `delta.tool_calls[]`
- 参数 (`arguments`) 是 **token-level delta**
- 一个 response 中可以出现多个 tool_call

示意：

```
[tool_call #0 start]
  arguments delta...
[tool_call #1 start]
  arguments delta...
[stream end]
```

### 3.2 tool_call 的“结束”判定规则（非常重要）

**没有显式的 TOOL_CALL_END 事件。**

正确规则：

- 当 **新的 tool_call(index/name) 出现** → 上一个 tool_call 结束
- 当 **response stream 结束** → 最后一个 tool_call 结束

> ❗ 不要依赖 `finish_reason`

---

## 4. 推荐的 Agent Runtime 架构

### 4.1 角色划分

```
┌────────────┐
│   LLM      │  streaming
└─────┬──────┘
      │ tool_call delta
┌─────▼──────┐
│ Tool Call  │  buffer / boundary detect
│  Collector │
└─────┬──────┘
      │ completed call
┌─────▼──────┐
│ Tool       │  execute immediately
│ Executor   │
└─────┬──────┘
      │ error / success
┌─────▼──────┐
│ Control    │  abort / continue
│ Logic      │
└────────────┘
```

### 4.2 Runtime 行为原则

1. **边生成边解析 tool_call**
2. 一旦 tool_call 完整 → 立刻执行
3. 如果工具失败 → 立即终止 stream
4. 已生成的前序结果仍然有效

这正是“及时止损 + 最大化 token 利用率”的关键。

---

## 5. Prompt 设计原则（工程视角）

不要把 Prompt 当“对话”，而要当 **协议声明**。

### 5.1 推荐的 Prompt 特征

- 使用 *protocol / mandatory / invalid* 语言
- 明确 tool_call 数量或阶段
- 明确禁止自然语言输出

示例（抽象版）：

```
You are an execution agent.
This is a strict machine protocol.

- Emit tool calls only.
- Continue generation until all required actions are emitted.
- Stopping early is invalid output.
```

### 5.2 现实提醒

即使如此：

> **连续 tool_call 是“可诱导但非 100% 保证”的模型行为**

因此 runtime 必须具备 fallback。

---

## 6. Tool 扩展的工程化方案（重点）

为了避免：

- 硬编码 tool schema
- 工具定义与实现分离
- 提示词与代码强耦合

推荐使用 **Python 装饰器 + 注解驱动的工具系统**。

---

## 7. 基于装饰器的 Tool 定义方案

### 7.1 目标

- 一个函数 = 一个工具
- 自动生成 JSON Schema
- 自动注册到 Agent
- 工具可独立测试

---

### 7.2 基础装饰器实现

```python
from typing import Callable, get_type_hints
import inspect

TOOL_REGISTRY = {}


def tool(description: str):
    def decorator(fn: Callable):
        sig = inspect.signature(fn)
        hints = get_type_hints(fn)

        parameters = {
            "type": "object",
            "properties": {},
            "required": [],
        }

        for name, param in sig.parameters.items():
            parameters["properties"][name] = {
                "type": "string",  # 可扩展映射
            }
            if param.default is inspect._empty:
                parameters["required"].append(name)

        TOOL_REGISTRY[fn.__name__] = {
            "type": "function",
            "function": {
                "name": fn.__name__,
                "description": description,
                "parameters": parameters,
            },
            "impl": fn,
        }

        return fn
    return decorator
```

---

### 7.3 定义工具（示例）

```python
@tool("Write content to a file")
def write_file(path: str, content: str):
    with open(path, "w") as f:
        f.write(content)


@tool("Append content to a file")
def edit_file(path: str, append: str):
    with open(path, "a") as f:
        f.write(append)
```

---

### 7.4 自动生成 tools 参数

```python
def get_openai_tools():
    return [v for v in TOOL_REGISTRY.values()]
```

---

## 8. Tool 执行调度

```python
import json

def execute_tool(name: str, args_json: str):
    tool = TOOL_REGISTRY[name]
    args = json.loads(args_json)
    return tool["impl"](**args)
```

结合 streaming 解析即可形成完整 Agent。

---

## 9. 为什么这种方式是“优雅”的

- 工具 = 纯 Python 函数
- Schema 自动生成
- Prompt 不需要写死工具细节
- IDE / AI Agent 可动态发现工具

这非常适合：

- AI IDE
- 自动代码重构
- Agent 插件生态

---

## 10. 工程级注意事项（非常重要）

1. **永远不要假设模型一定给你 N 个 tool_call**
2. Runtime 必须是状态机
3. 工具必须幂等或可回滚
4. 工具执行错误要可结构化返回
5. Prompt 与工具定义应解耦

---

## 11. 推荐的最终架构总结

> **Streaming Tool-Call + 协议 Prompt + 装饰器工具系统 + 容错 Runtime**

这是目前在 Agent 类系统中：

- token 效率
- 可控性
- 可扩展性

三者之间 **最平衡的方案之一**。

---

## 12. 你现在可以怎么交付给 AI IDE

你可以把本文档直接作为：

- Agent Runtime 设计说明
- Tool 扩展规范
- Prompt / Protocol 编写指南

如果需要，我可以继续帮你：

- 拆分成 **架构文档 + 代码规范**
- 给 AI IDE 写 **自动生成工具 schema 的模板**
- 设计 **失败重试 / 补救策略**

只要告诉我下一步你要交付到哪一层即可。

---

任务需求和日志信息挤在右上角的小框区域里，下面大块的黑色区域完全没用，而且我要看到的是能够直观展示agent工作过程的实时渲染画面效果，例如正在编辑某文件（文件树中的文件有互动反馈）、正在查看文件、....，现在的ui完全是垃圾，能不能有点产品思维？我让你用tui是为了提供一个可视化可交互的用户ui，而不是换个地方塞日志给用户看！