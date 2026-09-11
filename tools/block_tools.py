"""块工具系统

定义块工具（Block Tool）注册装饰器和注册表。
块工具使用 <<<TYPE: arg>>>...<<<END_TYPE>>> 语法。

与行工具（@agent_tool）的区别：
- 块工具：支持多行内容，语法为 <<<TYPE: arg>>>
- 行工具：单行参数，语法为 @@TOOL key="value"
"""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from ..core.context import ToolContext
    from ..core.error_feedback import ToolResult


# 块工具注册表
_BLOCK_TOOL_REGISTRY: Dict[str, "BlockToolDefinition"] = {}


@dataclass
class BlockToolDefinition:
    """块工具定义
    
    Attributes:
        name: 块类型名称（如 "FILE", "DIFF"）
        description: 工具描述
        handler: 处理函数，签名为 async (ctx, path, content) -> ToolResult
        is_direct_write: 是否直接写入 VFS（如 FILE），否则调用 handler
    """
    
    name: str
    description: str
    handler: Optional[Callable] = None
    is_direct_write: bool = False  # FILE 类型直接写入，不需要 handler
    
    @property
    def start_pattern(self) -> str:
        """开始标记正则片段"""
        return self.name
    
    @property
    def end_marker(self) -> str:
        """结束标记"""
        return f"<<<END_{self.name}>>>"
    
    @property
    def end_pattern(self) -> re.Pattern[str]:
        """结束标记正则"""
        return re.compile(rf"<<<END_{self.name}>>>")


def block_tool(
    name: str,
    description: str,
    is_direct_write: bool = False,
) -> Callable:
    """块工具注册装饰器
    
    用法:
        @block_tool(
            name="DIFF",
            description="增量修改文件",
        )
        async def apply_diff_block(ctx: ToolContext, path: str, content: str) -> ToolResult:
            # path 来自 <<<DIFF: path>>>
            # content 是块内的多行内容
            ...
    
    Args:
        name: 块类型名称（大写，如 "DIFF"）
        description: 工具描述
        is_direct_write: 是否直接写入 VFS（如 FILE 类型）
    """
    
    def decorator(func: Callable) -> Callable:
        block_def = BlockToolDefinition(
            name=name.upper(),
            description=description,
            handler=func,
            is_direct_write=is_direct_write,
        )
        _BLOCK_TOOL_REGISTRY[name.upper()] = block_def
        return func
    
    return decorator


def register_direct_write_block(name: str, description: str) -> None:
    """注册直接写入类型的块工具（如 FILE）
    
    这种块工具不需要 handler，内容直接写入 VFS。
    """
    block_def = BlockToolDefinition(
        name=name.upper(),
        description=description,
        handler=None,
        is_direct_write=True,
    )
    _BLOCK_TOOL_REGISTRY[name.upper()] = block_def


def get_all_block_tools() -> List[BlockToolDefinition]:
    """获取所有注册的块工具"""
    return list(_BLOCK_TOOL_REGISTRY.values())


def get_block_tool(name: str) -> Optional[BlockToolDefinition]:
    """获取指定块工具"""
    return _BLOCK_TOOL_REGISTRY.get(name.upper())


def get_block_names() -> List[str]:
    """获取所有块工具名称（用于生成正则）"""
    return list(_BLOCK_TOOL_REGISTRY.keys())


def build_block_start_pattern() -> re.Pattern[str]:
    """动态构建块开始的正则表达式
    
    Returns:
        匹配 <<<TYPE: arg>>> 的正则，其中 TYPE 为所有注册的块名
    """
    if not _BLOCK_TOOL_REGISTRY:
        # 无注册时返回不匹配任何内容的模式
        return re.compile(r"(?!)")
    
    names = "|".join(_BLOCK_TOOL_REGISTRY.keys())
    return re.compile(rf"<<<({names}):\s*([^>]+)>>>")


def get_block_end_pattern(block_name: str) -> Optional[re.Pattern[str]]:
    """获取指定块的结束正则"""
    block = get_block_tool(block_name)
    if block:
        return block.end_pattern
    return None


# ==================== 内置块工具注册 ====================

# FILE: 直接写入 VFS
register_direct_write_block(
    name="FILE",
    description="创建或覆写文件。内容直接写入虚拟文件系统。",
)


# DIFF: 使用 apply_diff 工具处理
@block_tool(
    name="DIFF",
    description="增量修改文件。使用 SEARCH/REPLACE 格式精确修改代码片段。",
)
async def diff_block_handler(
    ctx: "ToolContext",
    path: str,
    content: str,
) -> "ToolResult":
    """DIFF 块处理器
    
    将 DIFF 块内容转换为 apply_diff 工具调用。
    """
    # 延迟导入避免循环依赖
    from . import execute_tool_safe
    
    return await execute_tool_safe(
        name="apply_diff",
        arguments={"path": path, "diff": content},
        ctx=ctx,
    )
