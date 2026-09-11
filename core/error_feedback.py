"""错误反馈系统

提供结构化的错误反馈机制，使 LLM 能够根据错误信息自动修复问题。
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ErrorType(Enum):
    """错误类型分类
    
    用于分类工具执行错误，帮助 LLM 选择正确的修复策略。
    """
    
    # 编译相关
    COMPILE_ERROR = "compile"
    """编译错误：语法错误、类型错误、模块未找到等"""
    
    # 文件操作相关
    FILE_NOT_FOUND = "file_not_found"
    """文件不存在"""
    
    FILE_READ_ERROR = "file_read"
    """文件读取错误"""
    
    # Diff 操作相关
    DIFF_NOT_FOUND = "diff_not_found"
    """diff 搜索模式未找到"""
    
    DIFF_INVALID = "diff_invalid"
    """diff 格式无效"""
    
    # 工具调用相关
    TOOL_NOT_FOUND = "tool_not_found"
    """工具不存在"""
    
    TOOL_INVALID_ARGS = "tool_invalid_args"
    """工具参数无效（JSON 解析失败等）"""
    
    # 系统相关
    INTERNAL_ERROR = "internal"
    """内部错误（不可恢复）"""
    
    TIMEOUT = "timeout"
    """超时"""


@dataclass
class ToolResult:
    """工具执行结果
    
    封装工具执行的成功/失败状态，提供结构化的错误信息。
    """
    
    success: bool
    """是否执行成功"""
    
    message: str
    """结果消息（成功时为结果描述，失败时为错误描述）"""
    
    error_type: Optional[ErrorType] = None
    """错误类型（仅失败时有值）"""
    
    recoverable: bool = True
    """是否可恢复（LLM 可以尝试修复）"""
    
    context: Dict[str, Any] = field(default_factory=dict)
    """错误上下文（如可用文件列表、搜索模式等）"""
    
    tool_name: str = ""
    """工具名称"""
    
    should_feedback: bool = False
    """是否需要将 message 反馈给 LLM
    
    - 动作型工具（write_file, done）：False，静默成功
    - 查询型工具（read_files, list_files）：True，必须反馈结果
    """
    
    @classmethod
    def ok(
        cls,
        message: str,
        should_feedback: bool = False,
    ) -> "ToolResult":
        """创建成功结果
        
        Args:
            message: 结果消息
            should_feedback: 是否需要将结果反馈给 LLM（查询型工具设为 True）
        
        Note:
            tool_name 由框架在 execute_tool_safe 中自动设置，工具函数无需传递。
        """
        return cls(
            success=True,
            message=message,
            should_feedback=should_feedback,
        )
    
    @classmethod
    def error(
        cls,
        message: str,
        error_type: ErrorType,
        recoverable: bool = True,
        context: Optional[Dict[str, Any]] = None,
    ) -> "ToolResult":
        """创建错误结果
        
        Note:
            tool_name 由框架在 execute_tool_safe 中自动设置，工具函数无需传递。
        """
        return cls(
            success=False,
            message=message,
            error_type=error_type,
            recoverable=recoverable,
            context=context or {},
        )
    
    def to_feedback(self) -> str:
        """转换为 LLM 可读的反馈格式
        
        成功时返回简洁消息，失败时返回结构化错误信息和修复提示。
        """
        if self.success:
            return f"[{self.tool_name}] ✅ {self.message}"
        
        # 构建错误反馈
        lines = [
            f"[{self.tool_name}] ❌ FAILED",
            f"Error Type: {self.error_type.value if self.error_type else 'unknown'}",
            f"Message: {self.message}",
        ]
        
        # 添加上下文信息
        if self.context:
            lines.append("Context:")
            for key, value in self.context.items():
                if isinstance(value, list) and len(value) > 10:
                    lines.append(f"  {key}: {value[:10]}... ({len(value)} items)")
                else:
                    lines.append(f"  {key}: {value}")
        
        # 添加修复提示
        hint = self._get_recovery_hint()
        if hint:
            lines.append(f"Hint: {hint}")
        
        # 标记不可恢复
        if not self.recoverable:
            lines.append("⚠️ This error is NOT recoverable. Task may need to abort.")
        
        return "\n".join(lines)
    
    def _get_recovery_hint(self) -> str:
        """根据错误类型生成修复提示"""
        if not self.error_type:
            return ""
        
        hints = {
            ErrorType.COMPILE_ERROR: "Fix the code based on error message and compile again.",
            ErrorType.FILE_NOT_FOUND: "Use write_file to create the missing file.",
            ErrorType.DIFF_NOT_FOUND: "Use read_file to check current content, then retry with exact match.",
            ErrorType.DIFF_INVALID: "Check diff format: <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE",
            ErrorType.TOOL_NOT_FOUND: "Check available tools: write_file, read_file, apply_diff, compile, done.",
            ErrorType.TOOL_INVALID_ARGS: "Check tool arguments format (must be valid JSON).",
            ErrorType.INTERNAL_ERROR: "Internal error occurred. Consider aborting.",
            ErrorType.TIMEOUT: "Operation timed out. Try simpler approach.",
        }
        
        return hints.get(self.error_type, "")


def format_results_for_llm(results: List[ToolResult]) -> str:
    """格式化多个工具结果为 LLM 反馈
    
    Args:
        results: 工具执行结果列表
        
    Returns:
        格式化的反馈字符串
    """
    if not results:
        return "No tool results."
    
    # 统计
    success_count = sum(1 for r in results if r.success)
    fail_count = len(results) - success_count
    
    lines = [
        f"=== Tool Execution Results ({success_count}/{len(results)} succeeded) ===",
        "",
    ]
    
    for i, result in enumerate(results, 1):
        lines.append(f"--- Tool #{i} ---")
        lines.append(result.to_feedback())
        lines.append("")
    
    # 如果有失败，添加总结
    if fail_count > 0:
        lines.append(f"⚠️ {fail_count} tool(s) failed. Please fix the errors and try again.")
    
    return "\n".join(lines)


def create_compile_error_feedback(
    error_msg: str,
    available_files: List[str],
) -> ToolResult:
    """创建编译错误的结构化反馈
    
    Args:
        error_msg: 编译器错误消息
        available_files: 当前可用的文件列表
        
    Returns:
        带上下文的 ToolResult（tool_name 由框架自动设置）
    """
    # 尝试提取文件相关信息
    context: Dict[str, Any] = {
        "available_files": available_files,
    }
    
    # 检测常见错误模式
    if "Could not resolve" in error_msg or "File not found" in error_msg:
        context["error_pattern"] = "missing_file"
    elif "No matching export" in error_msg:
        context["error_pattern"] = "export_mismatch"
    elif "SyntaxError" in error_msg or "Parse error" in error_msg:
        context["error_pattern"] = "syntax_error"
    
    return ToolResult.error(
        message=error_msg,
        error_type=ErrorType.COMPILE_ERROR,
        recoverable=True,
        context=context,
    )


def create_diff_error_feedback(
    search_pattern: str,
    file_path: str,
    current_content_preview: str,
) -> ToolResult:
    """创建 diff 应用失败的结构化反馈
    
    Args:
        search_pattern: 未找到的搜索模式
        file_path: 目标文件路径
        current_content_preview: 当前文件内容预览（前 500 字符）
        
    Returns:
        带上下文的 ToolResult（tool_name 由框架自动设置）
    """
    # 截断搜索模式预览
    pattern_preview = search_pattern[:100] + "..." if len(search_pattern) > 100 else search_pattern
    
    return ToolResult.error(
        message=f"Search pattern not found in {file_path}",
        error_type=ErrorType.DIFF_NOT_FOUND,
        recoverable=True,
        context={
            "file": file_path,
            "search_pattern_preview": pattern_preview,
            "current_content_preview": current_content_preview[:500] + "..." if len(current_content_preview) > 500 else current_content_preview,
        },
    )

