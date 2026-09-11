"""工具系统核心

定义工具注册装饰器和工具调用机制。
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from ..core.context import ToolContext
    from ..core.error_feedback import ToolResult

# 工具注册表
_TOOL_REGISTRY: Dict[str, "ToolDefinition"] = {}


@dataclass
class ToolDefinition:
    """工具定义"""

    name: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable

    def to_openai_schema(self) -> Dict[str, Any]:
        """转换为 OpenAI Tool Call 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def agent_tool(
    name: str,
    description: str,
    parameters: Dict[str, Any],
) -> Callable:
    """工具注册装饰器

    用法:
        @agent_tool(
            name="write_file",
            description="写入文件",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        )
        async def write_file(ctx: ToolContext, path: str, content: str) -> str:
            ...
    """

    def decorator(func: Callable) -> Callable:
        tool_def = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            handler=func,
        )
        _TOOL_REGISTRY[name] = tool_def
        return func

    return decorator


def get_all_tools() -> List[ToolDefinition]:
    """获取所有注册的工具"""
    return list(_TOOL_REGISTRY.values())


def get_tool(name: str) -> Optional[ToolDefinition]:
    """获取指定工具"""
    return _TOOL_REGISTRY.get(name)


def get_openai_tools() -> List[Dict[str, Any]]:
    """获取 OpenAI 格式的工具列表"""
    return [tool.to_openai_schema() for tool in _TOOL_REGISTRY.values()]


async def execute_tool(
    name: str,
    arguments: Dict[str, Any],
    ctx: "ToolContext",
) -> str:
    """执行工具调用（返回字符串版本，向后兼容）

    Args:
        name: 工具名称
        arguments: 工具参数
        ctx: 执行上下文

    Returns:
        工具执行结果字符串
    """
    result = await execute_tool_safe(name, arguments, ctx)
    return result.to_feedback()


async def execute_tool_safe(
    name: str,
    arguments: Dict[str, Any],
    ctx: "ToolContext",
) -> "ToolResult":
    """执行工具调用（返回结构化结果）

    框架层自动注入 tool_name，工具函数无需关心。

    Args:
        name: 工具名称
        arguments: 工具参数
        ctx: 执行上下文

    Returns:
        ToolResult: 包含成功/失败状态和详细信息
    """
    from ..core.error_feedback import ErrorType, ToolResult

    tool = get_tool(name)
    if not tool:
        result = ToolResult.error(
            message=f"Tool '{name}' not found",
            error_type=ErrorType.TOOL_NOT_FOUND,
            context={"available_tools": list(_TOOL_REGISTRY.keys())},
        )
        result.tool_name = name
        return result

    try:
        result = await tool.handler(ctx, **arguments)

        # 如果工具返回 ToolResult，使用它
        if isinstance(result, ToolResult):
            result.tool_name = name  # 框架自动注入 tool_name
            return result

        # 否则将字符串结果包装为成功的 ToolResult
        wrapped = ToolResult.ok(message=str(result))
        wrapped.tool_name = name

    except TypeError as e:
        # 参数类型错误
        result = ToolResult.error(
            message=f"Invalid arguments: {e}",
            error_type=ErrorType.TOOL_INVALID_ARGS,
            context={"provided_args": list(arguments.keys())},
        )
        result.tool_name = name
        return result
    except Exception as e:
        # 其他执行错误
        result = ToolResult.error(
            message=f"Execution failed: {e}",
            error_type=ErrorType.INTERNAL_ERROR,
            recoverable=False,
        )
        result.tool_name = name
        return result

    else:
        return wrapped


# ==================== 自动注册 ====================
# 导入所有工具模块以触发注册
# NOTE: scope_tools 已废弃，declare_scope 被纯文本 <<<FILE>>> 替代
from . import compile, control, file_ops  # noqa: A004

__all__ = ["compile", "control", "file_ops"]
