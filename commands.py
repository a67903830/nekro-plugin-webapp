"""管理员命令

统一的 WebApp 命令系统：
- wa ls [-v]      列出任务和项目状态
- wa info <id>    查看任务详情
- wa stop <id>    取消/停止任务
- wa clear        清空项目
- wa help         帮助信息

所有命令支持 `-` 和 `_` 通配（如 wa_ls, wa-ls）
"""

from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from nekro_agent.adapters.onebot_v11.matchers.command import (
    command_guard,
    finish_with,
    on_command,
)

from .plugin import config
from .services.runtime_state import runtime_state
from .services.vfs import clear_project_context, get_project_context

# ==================== 工具函数 ====================


def _build_file_tree(files: list[str]) -> str:
    """构建目录树格式的文件列表"""
    if not files:
        return "  (空)"

    # 按路径分组
    tree: dict = {}
    for f in sorted(files):
        parts = f.split("/")
        current = tree
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = None  # 文件用 None 标记

    # 递归构建树形字符串
    def render(node: dict, prefix: str = "") -> list[str]:
        lines = []
        # 优先排列文件夹 (children is not None)，再按名称排序
        items = sorted(node.items(), key=lambda x: (x[1] is None, x[0]))
        for i, (name, children) in enumerate(items):
            is_last_item = i == len(items) - 1
            connector = "└─" if is_last_item else "├─"
            icon = _get_file_icon(name) if children is None else "📁"
            lines.append(f"{prefix}{connector} {icon} {name}")
            if children is not None:
                extension = "   " if is_last_item else "｜ "
                lines.extend(render(children, prefix + extension))
        return lines

    return "\n".join(render(tree))


def _get_file_icon(filename: str) -> str:
    """根据文件类型获取图标"""
    if filename.endswith(".tsx"):
        return "⚛️"
    if filename.endswith(".ts"):
        return "📘"
    if filename.endswith(".css"):
        return "🎨"
    if filename.endswith(".html"):
        return "📄"
    if filename.endswith(".json"):
        return "📋"
    return "📄"


def _format_size(chars: int) -> str:
    """格式化大小"""
    if chars < 1000:
        return f"{chars}"
    if chars < 10000:
        return f"{chars / 1000:.1f}K"
    return f"{chars / 1000:.0f}K"


def _progress_bar(percent: int, width: int = 10) -> str:
    """生成进度条"""
    filled = int(width * percent / 100)
    empty = width - filled
    return "▓" * filled + "░" * empty


def _parse_verbose(arg: Message) -> tuple[bool, str]:
    """解析 -v 参数"""
    text = str(arg).strip()
    if text.startswith("-v"):
        return True, text[2:].strip()
    if text.endswith("-v"):
        return True, text[:-2].strip()
    return False, text


def _status_icon(status: str) -> str:
    """状态图标"""
    return {
        "running": "🔄",
        "pending": "⏳",
        "success": "✅",
        "failed": "❌",
        "archived": "📦",
        "initializing": "🔄",
        "compiling": "📦",
        "completed": "✅",
    }.get(status, "?")


# ==================== wa ls / wa list ====================


@on_command(
    "wa",  # 基础命令，根据子命令路由
    aliases={"wa_ls", "wa-ls", "wa_list", "wa-list", "webapp_ls", "webapp_list"},
    priority=5,
    block=True,
).handle()
async def cmd_ls(
    matcher: Matcher,
    event: MessageEvent,
    bot: Bot,
    arg: Message = CommandArg(),
):
    """列出任务和项目状态"""
    from .services.task_manager import task_manager

    _, _, chat_key, _ = await command_guard(event, bot, arg, matcher)

    verbose, sub_arg = _parse_verbose(arg)

    # 检查是否是其他子命令
    sub_cmd = sub_arg.split()[0] if sub_arg.split() else ""
    if sub_cmd in ("info", "stop", "cancel", "clear", "help"):
        # 路由到对应处理器（通过 finish_with 返回提示）
        await finish_with(matcher, message=f"💡 请使用: wa_{sub_cmd} ...")
        return

    lines = ["🌐 WebApp 状态", "━" * 24]

    # 多任务状态
    tasks = task_manager.list_active_tasks(chat_key)
    if tasks:
        lines.append("")
        lines.append("📋 任务列表")
        for t in tasks:
            icon = _status_icon(t.status)
            desc = t.description[:25] + "..." if len(t.description) > 25 else t.description
            lines.append(f"  {icon} [{t.task_id}] {desc}")

            # 运行时状态
            r_state = runtime_state.get_state(chat_key, t.task_id)
            if r_state and r_state.status in ("initializing", "running", "compiling"):
                progress = r_state.progress_percent()
                phase = r_state.current_phase
                lines.append(f"     🏃 {phase} ({progress}%) | 迭代 {r_state.iteration}/{r_state.max_iterations}")
                if verbose and r_state.tool_calls:
                    recent = r_state.tool_calls[-1]
                    res = "✅" if recent.success else "❌"
                    lines.append(f"     🔧 最近: {res} {recent.name}")

            if verbose:
                # 统计文件数
                project = get_project_context(chat_key, t.task_id)
                f_count = len(project.list_files())
                if f_count > 0:
                    lines.append(f"     📁 文件: {f_count} 个")

                if t.url:
                    lines.append(f"     🔗 {t.url}")
                if t.error:
                    err = t.error[:30] + "..." if len(t.error) > 30 else t.error
                    lines.append(f"     💥 {err}")

    if not tasks:
        lines.extend(["", "📭 暂无活跃任务", "", "💡 发送需求开始开发"])

    lines.extend(["", "━" * 24, "💡 wa_help 查看命令帮助"])

    await finish_with(matcher, message="\n".join(lines))


# ==================== wa info <id> ====================


@on_command(
    "wa_info",
    aliases={"wa-info", "webapp_info", "webapp-info"},
    priority=5,
    block=True,
).handle()
async def cmd_info(
    matcher: Matcher,
    event: MessageEvent,
    bot: Bot,
    arg: Message = CommandArg(),
):
    """查看特定任务详情"""
    from .services.task_manager import task_manager

    _, _, chat_key, _ = await command_guard(event, bot, arg, matcher)

    task_id = str(arg).strip()
    if not task_id:
        # 如果没有指定 ID，显示最近的任务
        tasks = task_manager.list_active_tasks(chat_key)
        if tasks:
            task_id = tasks[0].task_id
        else:
            await finish_with(matcher, message="❌ 请指定任务 ID: wa_info <task_id>\n💡 使用 wa_ls 查看任务列表")
            return

    task_info = task_manager.get_task(chat_key, task_id)
    if not task_info:
        await finish_with(matcher, message=f"❌ 任务 {task_id} 不存在")
        return

    lines = [
        f"📋 任务详情 [{task_id}]",
        "━" * 24,
        "",
        f"状态: {_status_icon(task_info.status)} {task_info.status.upper()}",
        f"描述: {task_info.description}",
    ]

    if task_info.url:
        lines.append(f"链接: {task_info.url}")

    if task_info.error:
        lines.extend(["", "💥 错误信息:", f"   {task_info.error}"])

    if len(task_info.requirements) > 1:
        lines.extend(["", f"📝 需求历史 ({len(task_info.requirements)} 条):"])
        for i, req in enumerate(task_info.requirements[-3:], 1):
            req_preview = req[:50] + "..." if len(req) > 50 else req
            lines.append(f"  {i}. {req_preview}")

    # 关联项目文件
    project = get_project_context(chat_key, task_id)
    files = project.list_files()
    if files:
        lines.extend(["", f"📁 项目文件 ({len(files)} 个):"])
        lines.append(_build_file_tree(files))

    await finish_with(matcher, message="\n".join(lines))


# ==================== wa stop / wa cancel ====================


@on_command(
    "wa_stop",
    aliases={"wa-stop", "wa_cancel", "wa-cancel", "webapp_stop", "webapp_cancel"},
    priority=5,
    block=True,
).handle()
async def cmd_stop(
    matcher: Matcher,
    event: MessageEvent,
    bot: Bot,
    arg: Message = CommandArg(),
):
    """取消/停止任务"""
    from nekro_agent.services.plugin.task import task

    _, _, chat_key, _ = await command_guard(event, bot, arg, matcher)

    task_id = str(arg).strip()

    # 检查是否有运行中的任务
    if task.is_running("webapp_dev", chat_key):
        success = await task.cancel("webapp_dev", chat_key)
        if success:
            msg = """✅ 任务已取消
━━━━━━━━━━━━━━━━━━━━

🛑 Agent 已停止工作
📁 项目文件已保留

💡 使用 wa_clear 清空项目"""
            await finish_with(matcher, message=msg)
            return
        await finish_with(matcher, message="❌ 取消失败")
        return

    if not task_id:
        await finish_with(matcher, message="📭 没有正在运行的任务\n💡 使用 wa_ls 查看任务列表")
        return

    await finish_with(matcher, message=f"❌ 任务 {task_id} 不存在或已完成")


# ==================== wa clear ====================


@on_command(
    "wa_clear",
    aliases={"wa-clear", "webapp_clear", "webapp-clear"},
    priority=5,
    block=True,
).handle()
async def cmd_clear(
    matcher: Matcher,
    event: MessageEvent,
    bot: Bot,
    arg: Message = CommandArg(),
):
    """清空项目"""
    from nekro_agent.services.plugin.task import task

    from .services.task_manager import task_manager

    _, _, chat_key, _ = await command_guard(event, bot, arg, matcher)

    task_id = str(arg).strip()
    
    # 如果未指定 ID，尝试智能判定
    if not task_id:
        tasks = task_manager.list_active_tasks(chat_key)
        if len(tasks) == 1:
            task_id = tasks[0].task_id
        elif len(tasks) > 1:
            await finish_with(matcher, message="⚠️ 有多个任务，请指定 ID 清除:\nwa_clear <task_id>")
            return
        else:
            await finish_with(matcher, message="📭 无活跃任务可清除")
            return

    # 检查是否有运行中的任务
    if task.is_running("webapp_dev", task_id):
        # ... (使用 task_id 获取状态，如果有的话)
        msg = f"""⚠️ 任务 {task_id} 正在运行中
━━━━━━━━━━━━━━━━━━━━

请先停止任务:
wa_stop {task_id}"""
        await finish_with(matcher, message=msg)
        return

    project = get_project_context(chat_key, task_id)
    file_count = len(project.list_files())

    if file_count == 0:
        await finish_with(matcher, message=f"📭 任务 {task_id} 的项目已为空")
        return

    project.clear()
    clear_project_context(chat_key, task_id)
    # 如果任务已失败/完成，是否要归档？
    # webapp_clear 通常只清空文件，不移除任务记录。用户可以用 wa_stop 停止/自动归档?
    # 不，通常 clear 是清理环境。这里只清理 VFS。

    msg = f"""✅ 项目已清空
━━━━━━━━━━━━━━━━━━━━

🗑️ 已删除 {file_count} 个文件 (任务 {task_id})"""
    await finish_with(matcher, message=msg)


# ==================== wa help ====================


@on_command(
    "wa_help",
    aliases={"wa-help", "webapp_help", "webapp-help"},
    priority=5,
    block=True,
).handle()
async def cmd_help(
    matcher: Matcher,
    event: MessageEvent,
    bot: Bot,
    arg: Message = CommandArg(),
):
    """帮助"""
    _, _, _, _ = await command_guard(event, bot, arg, matcher)

    msg = """🌐 WebApp 开发助手
━━━━━━━━━━━━━━━━━━━━

📋 命令列表

  wa_ls [-v]      查看任务和项目状态
  wa_info <id>    查看任务详情
  wa_stop [id]    取消/停止任务
  wa_clear        清空项目文件
  wa_help         显示本帮助

━━━━━━━━━━━━━━━━━━━━

💡 使用说明

直接描述你想要的 Web 应用:
  "做一个计时器"
  "写一个待办事项应用"

Agent 会自动:
  📝 分析需求 → 💻 编写代码
  ✅ 编译验证 → 🚀 部署上线

使用 wa_ls -v 查看详细状态

━━━━━━━━━━━━━━━━━━━━

📖 命令别名

所有命令支持 - 和 _ 通配:
  wa_ls = wa-ls = wa_list = wa-list"""
    await finish_with(matcher, message=msg)
