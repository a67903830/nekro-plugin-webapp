"""编译工具

提供编译验证功能。
所有工具统一返回 ToolResult 类型，tool_name 由框架自动注入。
"""

import re

from ..core.context import ToolContext
from ..core.error_feedback import ToolResult
from ..services.compiler_client import check_project, compile_project
from . import agent_tool


@agent_tool(
    name="compile",
    description="编译项目并验证代码正确性。成功返回编译结果，失败返回错误信息。",
    parameters={
        "type": "object",
        "properties": {},
    },
)
async def compile_project_tool(ctx: ToolContext) -> ToolResult:
    """编译项目（动作型工具，静默成功）"""
    files = ctx.project.get_snapshot()

    if not files:
        return ToolResult.ok("❌ 项目为空，无法编译")

    # 检查入口文件
    if "src/main.tsx" not in files:
        return ToolResult.ok("❌ 缺少入口文件 src/main.tsx")

    # 记录编译事件
    if ctx.tracer:
        ctx.tracer.log_event(
            event_type=ctx.tracer.EVENT.COMPILE_START,
            agent_id=ctx.task_id,
            message="开始编译",
            file_count=len(files),
        )

    # 执行编译
    success, output, externals = await compile_project(
        files=files,
        tracer=ctx.tracer,
        agent_id=ctx.task_id,
    )

    # 更新状态
    ctx.state.compile_success = success

    if success:
        if ctx.tracer:
            ctx.tracer.log_event(
                event_type=ctx.tracer.EVENT.COMPILE_SUCCESS,
                agent_id=ctx.task_id,
                message="编译成功",
                output_size=len(output),
                externals=externals,
            )

        return ToolResult.ok(
            f"✅ 编译成功!\n外部依赖: {', '.join(externals) if externals else '无'}",
        )

    if ctx.tracer:
        ctx.tracer.log_event(
            event_type=ctx.tracer.EVENT.COMPILE_FAILED,
            agent_id=ctx.task_id,
            message="编译失败",
            error=output[:500],
        )

    # 增强错误信息
    enhanced_error = enhance_compile_error(output, ctx)
    ctx.state.last_error = enhanced_error

    return ToolResult.ok(f"❌ 编译失败:\n{enhanced_error}")


@agent_tool(
    name="type_check",
    description="运行 TypeScript 类型检查，不执行完整编译。",
    parameters={
        "type": "object",
        "properties": {},
    },
)
async def type_check(ctx: ToolContext) -> ToolResult:
    """类型检查（动作型工具，静默成功）"""
    files = ctx.project.get_snapshot()

    if not files:
        return ToolResult.ok("❌ 项目为空")

    error = await check_project(
        files=files,
        tracer=ctx.tracer,
        agent_id=ctx.task_id,
    )

    if error:
        return ToolResult.ok(f"❌ 类型检查失败:\n{error}")

    return ToolResult.ok("✅ 类型检查通过")


def enhance_compile_error(error_msg: str, ctx: ToolContext) -> str:
    """增强编译错误信息

    对常见错误添加修复提示。
    """

    enhanced = error_msg
    hints = []

    # 处理 "File not found" 错误 - 最常见的问题
    if "File not found" in error_msg or "Could not resolve" in error_msg:
        match = re.search(
            r'(?:File not found in VFS|Could not resolve)[:\s]*"?([^"\s]+)"?',
            error_msg,
        )
        if match:
            missing_file = match.group(1)
            # 规范化路径
            if missing_file.startswith("./"):
                missing_file = missing_file[2:]
            if not missing_file.startswith("src/"):
                missing_file = "src/" + missing_file
            if not missing_file.endswith((".tsx", ".ts", ".css")):
                missing_file += ".tsx"

            hints.append(f"💡 缺失文件: {missing_file}")
            hints.append("   请使用 write_file 创建该文件")

    # 处理 "No matching export" 错误
    if "No matching export" in error_msg:
        match = re.search(r'No matching export in "([^"]+)"', error_msg)
        if match:
            target_file = match.group(1)
            if target_file.startswith("./"):
                target_file = "src/" + target_file[2:]
            elif not target_file.startswith("src/"):
                target_file = "src/" + target_file

            exports = ctx.project.extract_exports(target_file)
            if exports:
                hints.append(f"💡 '{target_file}' 的实际导出: {', '.join(exports)}")

    # 添加当前文件列表
    files = ctx.project.list_files()
    if files:
        hints.append(f"📁 当前项目文件: {', '.join(sorted(files))}")
    else:
        hints.append("📁 当前项目为空，请先创建所有必要文件")

    if hints:
        enhanced += "\n\n" + "\n".join(hints)

    return enhanced
