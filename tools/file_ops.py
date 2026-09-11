"""文件操作工具

提供 write_file, read_file, apply_diff, list_files 等文件操作工具。
所有工具统一返回 ToolResult 类型，tool_name 由框架自动注入。
"""

import re
from typing import List, Union

from ..core.context import ToolContext
from ..core.error_feedback import ErrorType, ToolResult
from . import agent_tool


async def _emit_file_change(path: str, is_new: bool = False) -> None:
    """尝试发送文件变更事件"""
    try:
        from ..cli.stream import EventType, task_stream

        event_type = EventType.FILE_CREATED if is_new else EventType.FILE_MODIFIED
        await task_stream.emit_file_event(event_type, path)
    except (ImportError, ModuleNotFoundError):
        pass


def _tolerant_match(search: str, content: str) -> str | None:
    """低风险容错匹配

    尝试修复常见的空白差异问题:
    1. search 首尾多余空白/换行
    2. 每行末尾多余空格
    3. 连续空行差异

    Returns:
        找到匹配时返回 content 中实际匹配的原始字符串，否则返回 None
    """
    # 策略 1: 去除 search 首尾空白后匹配
    stripped = search.strip()
    if stripped and stripped in content:
        return stripped

    # 策略 2: 去除每行末尾空格后匹配
    search_lines = search.split("\n")
    stripped_lines = [line.rstrip() for line in search_lines]
    stripped_search = "\n".join(stripped_lines)
    if stripped_search in content:
        return stripped_search

    # 也尝试对 content 进行同样处理（双向容错）
    content_stripped = "\n".join(line.rstrip() for line in content.split("\n"))
    if stripped_search in content_stripped:
        # 找到匹配位置，需要返回原始 content 中的对应片段
        start_idx = content_stripped.find(stripped_search)
        if start_idx != -1:
            # 计算原始 content 中的对应范围
            # 通过行号映射回原始内容
            lines_before = content_stripped[:start_idx].count("\n")
            lines_in_match = stripped_search.count("\n")
            original_lines = content.split("\n")
            matched_original = "\n".join(
                original_lines[lines_before : lines_before + lines_in_match + 1],
            )
            if matched_original in content:
                return matched_original

    # 策略 3: 去除首尾空白 + 行末空格组合
    combined = "\n".join(line.rstrip() for line in search.strip().split("\n"))
    if combined in content:
        return combined

    return None


@agent_tool(
    name="write_file",
    description="创建新文件或覆写现有文件。适用于新建文件或需要完整重写的场景。",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径，相对于 src 目录，如 'src/App.tsx'",
            },
            "content": {
                "type": "string",
                "description": "文件完整内容",
            },
        },
        "required": ["path", "content"],
    },
)
async def write_file(ctx: ToolContext, path: str, content: str) -> ToolResult:
    """写入文件（动作型工具，静默成功）"""
    ctx.project.write_file(path, content)

    # 文件覆写成功，重置该文件的 DIFF 失败计数
    if path in ctx.state.diff_fail_counts:
        del ctx.state.diff_fail_counts[path]

    # 检测是否为新文件 (简化逻辑: 假设 write_file 总是可能创建新文件, 或视为 modified)
    # 这里我们统一视为 modified，除非我们检查文件是否存在。
    # 为了简单，write_file 视为 CREATED/MODIFIED 均可，TUI 刷新即可。
    await _emit_file_change(path)

    size = len(content)
    lines = content.count("\n") + 1
    return ToolResult.ok(f"✅ 已写入 {path} ({lines} 行, {size} 字符)")


@agent_tool(
    name="read_file",
    description="读取单个文件内容。用于查看现有文件或检查导出。",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径，相对于 src 目录",
            },
        },
        "required": ["path"],
    },
)
async def read_file(ctx: ToolContext, path: str) -> ToolResult:
    """读取单个文件（查询型工具，反馈结果）"""
    content = ctx.project.read_file(path)
    if content is None:
        return ToolResult.ok(f"❌ 文件不存在: {path}", should_feedback=True)

    lines = content.count("\n") + 1
    # 如果文件过长，截断显示
    if lines > 100:
        content_lines = content.split("\n")
        truncated = (
            "\n".join(content_lines[:50])
            + f"\n\n... 中间省略 {lines - 100} 行 ...\n\n"
            + "\n".join(content_lines[-50:])
        )
        return ToolResult.ok(
            f"📄 {path} ({lines} 行，已截断)\n\n{truncated}",
            should_feedback=True,
        )

    return ToolResult.ok(f"📄 {path} ({lines} 行)\n\n{content}", should_feedback=True)


@agent_tool(
    name="apply_diff",
    description="使用 SEARCH/REPLACE 格式修改文件。比 write_file 更高效，适用于小范围修改。",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径",
            },
            "diff": {
                "type": "string",
                "description": "SEARCH/REPLACE 格式的修改内容",
            },
        },
        "required": ["path", "diff"],
    },
)
async def apply_diff(ctx: ToolContext, path: str, diff: str) -> ToolResult:
    """应用增量修改（动作型工具，静默成功）

    格式:
        <<<<<<< SEARCH
        原始内容
        =======
        新内容
        >>>>>>> REPLACE

    容错策略:
        1. 精确匹配失败时，尝试低风险自动修复（首尾空白、行末空格）
        2. 仍失败则提示可查阅文件
        3. 连续失败 2 次后附带文件内容，3 次后建议全量重写
    """
    content = ctx.project.read_file(path)
    if content is None:
        return ToolResult.error(
            message=f"文件不存在: {path}",
            error_type=ErrorType.FILE_NOT_FOUND,
            recoverable=True,
        )

    # 解析 SEARCH/REPLACE 块
    pattern = r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE"
    matches = re.findall(pattern, diff, re.DOTALL)

    if not matches:
        return ToolResult.error(
            message="无效的 diff 格式，需要 <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE",
            error_type=ErrorType.DIFF_NOT_FOUND,
            recoverable=True,
        )

    applied = 0
    errors: List[str] = []
    tolerant_applied = 0  # 通过容错匹配成功的数量

    for search, replace in matches:
        # 1. 精确匹配
        match_count = content.count(search)

        if match_count == 1:
            # 唯一匹配，直接替换
            content = content.replace(search, replace, 1)
            applied += 1
            continue

        if match_count > 1:
            # 多处匹配，拒绝执行
            preview = search[:80] + "..." if len(search) > 80 else search
            errors.append(
                f"❌ 发现 {match_count} 处相同内容，无法确定替换哪一个。请扩展 SEARCH 块的上下文使其唯一:\n"
                f"```\n{preview}\n```",
            )
            continue

        # 2. 精确匹配失败，尝试低风险容错
        tolerant_search = _tolerant_match(search, content)
        if tolerant_search:
            content = content.replace(tolerant_search, replace, 1)
            applied += 1
            tolerant_applied += 1
            continue

        # 3. 容错也失败，记录错误
        preview = search[:100] + "..." if len(search) > 100 else search
        errors.append(
            f"❌ 未找到匹配内容（包括容错匹配），请确保 SEARCH 部分与文件内容一致:\n"
            f"```\n{preview}\n```",
        )

    if errors:
        # 获取/更新失败计数
        fail_count = ctx.state.diff_fail_counts.get(path, 0) + 1
        ctx.state.diff_fail_counts[path] = fail_count

        # 根据失败次数构建不同的反馈
        error_msg = (
            f"DIFF 应用失败 ({len(errors)} 处错误, {applied} 处成功):\n\n"
            + "\n\n".join(errors)
        )

        if fail_count == 1:
            # 第一次失败：提示可查阅文件
            error_msg += f'\n\n💡 **提示**: 如果 SEARCH 内容难以确定，可使用 `@@READ paths="{path}"` 查看最新文件内容'
        elif fail_count == 2:
            # 第二次失败：附带完整文件内容
            file_preview = (
                content
                if len(content) <= 2000
                else content[:1000] + "\n\n... [中间省略] ...\n\n" + content[-1000:]
            )
            error_msg += (
                f"\n\n⚠️ **连续失败 2 次**，以下是 `{path}` 的当前内容:\n"
                f"```\n{file_preview}\n```\n"
                f"请仔细对照后重新构建 SEARCH 块"
            )
        else:
            # 第三次及以上：建议全量重写
            error_msg += f"\n\n🚨 **已连续失败 {fail_count} 次**，建议放弃 DIFF 模式，改用 `<<<FILE: {path}>>>` 全量覆写该文件"

        return ToolResult.ok(error_msg, should_feedback=True)

    # 成功：重置失败计数
    if path in ctx.state.diff_fail_counts:
        del ctx.state.diff_fail_counts[path]

    ctx.project.write_file(path, content)
    await _emit_file_change(path)

    if tolerant_applied > 0:
        return ToolResult.ok(
            f"✅ 已应用 {applied} 处修改到 {path} (其中 {tolerant_applied} 处通过容错匹配)",
        )
    return ToolResult.ok(f"✅ 已应用 {applied} 处修改到 {path}")


@agent_tool(
    name="delete_file",
    description="删除文件。",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径",
            },
        },
        "required": ["path"],
    },
)
async def delete_file(ctx: ToolContext, path: str) -> ToolResult:
    """删除文件（动作型工具，静默成功）"""
    if ctx.project.read_file(path) is None:
        return ToolResult.ok(f"❌ 文件不存在: {path}")

    ctx.project.delete_file(path)
    await _emit_file_change(path, is_new=False)  # 删除也触发刷新
    return ToolResult.ok(f"✅ 已删除 {path}")


@agent_tool(
    name="list_files",
    description="列出项目所有文件及其导出信息。",
    parameters={
        "type": "object",
        "properties": {},
    },
)
async def list_files(ctx: ToolContext) -> ToolResult:
    """列出所有文件（查询型工具，反馈结果）"""
    files = ctx.project.list_files()

    if not files:
        return ToolResult.ok("📁 项目为空，尚无文件", should_feedback=True)

    lines = ["📁 项目文件:"]
    for f in sorted(files):
        size = len(ctx.project.files.get(f, ""))

        # 提取导出信息
        exports_hint = ""
        if f.endswith((".ts", ".tsx")):
            exports = ctx.project.extract_exports(f)
            if exports:
                exports_str = ", ".join(exports[:5])
                if len(exports) > 5:
                    exports_str += f" (+{len(exports) - 5})"
                exports_hint = f" [exports: {exports_str}]"

        lines.append(f"  • {f} ({size} chars){exports_hint}")

    return ToolResult.ok("\n".join(lines), should_feedback=True)


@agent_tool(
    name="read_files",
    description="读取指定文件的内容。调用后必须停止输出，等待文件内容反馈。",
    parameters={
        "type": "object",
        "properties": {
            "paths": {
                "type": "string",
                "description": "要读取的文件路径，多个用逗号分隔，如 'src/App.tsx,src/utils.ts'",
            },
        },
        "required": ["paths"],
    },
)
async def read_files(ctx: ToolContext, paths: Union[str, List[str]]) -> ToolResult:
    """读取多个文件内容（查询型工具，反馈结果）

    Args:
        ctx: 工具上下文
        paths: 文件路径（逗号分隔字符串或列表）
    """
    # 处理参数格式
    if isinstance(paths, str):
        path_list = [p.strip() for p in paths.split(",") if p.strip()]
    else:
        path_list = paths

    if not path_list:
        return ToolResult.ok("❌ 未指定文件路径", should_feedback=True)

    # 限制单次最多读取 6 个文件
    MAX_FILES = 6
    remaining_paths: List[str] = []
    if len(path_list) > MAX_FILES:
        remaining_paths = path_list[MAX_FILES:]
        path_list = path_list[:MAX_FILES]

    results = []
    found_count = 0

    for path in path_list:
        content = ctx.project.read_file(path)
        if content:
            found_count += 1
            results.append(f"=== {path} ({len(content)} chars) ===\n{content}")
        else:
            results.append(f"=== {path} ===\n[文件不存在]")

    header = f"读取 {found_count}/{len(path_list)} 个文件:\n"
    body = "\n\n".join(results)

    # 如果有超出限制的文件，提示 Agent 再次调用
    if remaining_paths:
        remaining_str = ", ".join(remaining_paths)
        footer = (
            f"\n\n⚠️ 还有 {len(remaining_paths)} 个文件未读取: {remaining_str}\n"
            f'如需继续读取，请再次调用 @@READ paths="{remaining_str}"'
        )
        return ToolResult.ok(header + body + footer, should_feedback=True)

    return ToolResult.ok(header + body, should_feedback=True)
