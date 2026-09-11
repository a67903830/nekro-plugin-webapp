"""WebApp 快速部署插件

AI 驱动的 Web 应用开发工具，使用单 Agent + Tool Call 架构。
支持异步任务模式，在后台执行并报告进度。
"""

import time
from typing import AsyncGenerator, List, Optional

from nekro_agent.api.schemas import AgentCtx
from nekro_agent.core import logger
from nekro_agent.services.plugin.base import SandboxMethodType
from nekro_agent.services.plugin.task import AsyncTaskHandle, TaskCtl, TaskSignal, task

from . import commands as _commands  # noqa: F401
from . import handlers as _handlers  # noqa: F401
from .plugin import config, plugin
from .services.task_tracer import TaskTracer
from .services.vfs import clear_project_context, get_project_context

__all__ = ["plugin"]


# ==================== 异步任务 ====================


@plugin.mount_async_task("webapp_dev")
async def _webapp_dev_task(
    handle: AsyncTaskHandle,
    requirement: str,
    webapp_task_id: str,
    existing_files: Optional[List[str]] = None,
) -> AsyncGenerator[TaskCtl, None]:
    """WebApp 开发异步任务

    通过 yield TaskCtl 报告状态，支持进度追踪和中断。
    """
    from .core.agent_loop import run_developer_loop
    from .runtime import set_adapter
    from .runtime.nekro import NekroAdapter
    from .services.compiler_client import compile_project
    from .services.deploy import deploy_html_to_worker
    from .services.runtime_state import runtime_state
    from .services.task_manager import task_manager  # Import added for status update

    chat_key = handle.chat_key
    # 使用传递进来的 ID，确保与 task_manager 一致
    task_id = webapp_task_id

    # 初始化运行时适配器 (关键：必须在 run_developer_loop 之前设置)
    adapter = NekroAdapter(
        plugin_data_dir=str(plugin.get_plugin_data_dir()),
        model_group=config.MODEL_GROUP,
    )
    adapter.set_notify_callback(handle.notify_agent)
    set_adapter(adapter)

    # 创建任务追踪器
    tracer = TaskTracer(
        chat_key=chat_key,
        root_agent_id=task_id,
        task_description=requirement.strip()[:200],
        plugin_data_dir=str(plugin.get_plugin_data_dir()),
    )

    tracer.log_event(
        event_type=tracer.EVENT.TASK_START,
        agent_id=task_id,
        message=f"开始任务: {requirement.strip()[:100]}...",
    )

    yield TaskCtl.report_progress("🚀 开始开发...", 0)

    # 检查取消
    if handle.is_cancelled:
        tracer.finalize("CANCELLED")
        yield TaskCtl.cancel("任务已取消")
        return

    # 运行 Developer 循环
    try:
        yield TaskCtl.report_progress("🔧 AI 正在编写代码...", 20)

        success, result = await run_developer_loop(
            chat_key=chat_key,
            task_description=requirement.strip(),
            tracer=tracer,
            model_group=config.MODEL_GROUP,
            max_iterations=config.MAX_ITERATIONS,
            existing_files=existing_files,
        )

        # 保存 VFS 快照
        project = get_project_context(chat_key, task_id)
        tracer.save_vfs_snapshot(project)

        if not success:
            await handle.notify_agent(f"❌ WebApp 开发失败: {result}")
            tracer.log_event(
                event_type=tracer.EVENT.NOTIFICATION_SENT,
                agent_id=task_id,
                message="已通知主 Agent: 开发失败",
            )
            tracer.finalize("FAILED", result)
            yield TaskCtl.fail(f"开发失败: {result}")
            return

        yield TaskCtl.report_progress("📦 编译中...", 70)

        # 最终编译（生成部署产物）
        files = project.get_snapshot()
        tracer.log_event(
            event_type=tracer.EVENT.FINAL_COMPILE_START,
            agent_id=task_id,
            message="最终编译开始",
            file_count=len(files),
        )

        compile_success, js_output, externals = await compile_project(
            files=files,
            env_vars=None,
            tracer=tracer,
            agent_id=task_id,
        )

        if not compile_success:
            tracer.log_event(
                event_type=tracer.EVENT.FINAL_COMPILE_FAILED,
                agent_id=task_id,
                message=f"最终编译失败: {js_output[:200]}",
                level="ERROR",
            )
            await handle.notify_agent(f"❌ WebApp 编译失败 (ID: {task_id})")
            tracer.log_event(
                event_type=tracer.EVENT.NOTIFICATION_SENT,
                agent_id=task_id,
                message="已通知主 Agent: 编译失败",
            )
            tracer.finalize("COMPILE_FAILED", js_output)
            yield TaskCtl.fail(f"编译失败: {js_output[:200]}")
            return

        tracer.log_event(
            event_type=tracer.EVENT.FINAL_COMPILE_SUCCESS,
            agent_id=task_id,
            message="最终编译成功",
            output_size=len(js_output),
            externals=externals,
        )

        # ==================== 外部依赖验证与动态解析 ====================
        from .services.html_generator import generate_shell_html, validate_externals

        extra_imports: dict[str, str] = {}

        if externals:
            tracer.log_event(
                event_type=tracer.EVENT.DEPENDENCY_CHECK,
                agent_id=task_id,
                message=f"检查外部依赖: {', '.join(externals)}",
                externals=externals,
            )

            is_valid, missing = validate_externals(externals)

            if not is_valid:
                # 尝试动态解析缺失的依赖
                tracer.log_event(
                    event_type=tracer.EVENT.DEPENDENCY_RESOLVE_START,
                    agent_id=task_id,
                    message=f"尝试动态解析未知依赖: {', '.join(missing)}",
                    missing_packages=missing,
                )

                from .services.dependency_resolver import resolve_missing_dependencies

                resolved, unresolved = await resolve_missing_dependencies(
                    missing,
                    model_group=config.MODEL_GROUP,
                )

                if resolved:
                    extra_imports.update(resolved)
                    tracer.log_event(
                        event_type=tracer.EVENT.DEPENDENCY_RESOLVE_SUCCESS,
                        agent_id=task_id,
                        message=f"成功解析 {len(resolved)} 个依赖",
                        resolved=list(resolved.keys()),
                    )

                if unresolved:
                    # 仍有无法解析的依赖，拒绝部署
                    error_msg = (
                        f"以下外部依赖未在系统中配置且无法自动解析: {', '.join(unresolved)}\n"
                        "请使用系统支持的库，或联系管理员添加。\n"
                        "支持的库请参考开发文档。"
                    )
                    tracer.log_event(
                        event_type=tracer.EVENT.DEPENDENCY_RESOLVE_FAILED,
                        agent_id=task_id,
                        message=f"依赖解析失败: {', '.join(unresolved)}",
                        unresolved=unresolved,
                        level="ERROR",
                    )
                    await handle.notify_agent(f"❌ WebApp 依赖解析失败 (ID: {task_id})\n{error_msg}")
                    tracer.log_event(
                        event_type=tracer.EVENT.NOTIFICATION_SENT,
                        agent_id=task_id,
                        message="已通知主 Agent: 依赖解析失败",
                    )
                    tracer.finalize("DEPENDENCY_ERROR", error_msg)
                    yield TaskCtl.fail(f"依赖解析失败: {error_msg}")
                    return

        yield TaskCtl.report_progress("🚀 部署中...", 90)

        # 尝试获取 Agent 设定的标题
        state = runtime_state.get_state(chat_key, task_id)
        page_title = state.title if state and state.title else "WebApp"

        html_content = generate_shell_html(
            title=page_title,
            body_js=js_output,
            dependencies=[],
            extra_imports=extra_imports,
        )

        tracer.log_event(
            event_type=tracer.EVENT.DEPLOY_START,
            agent_id=task_id,
            message="开始部署到 Worker",
        )

        # 部署
        url = await deploy_html_to_worker(
            html_content=html_content,
            title="WebApp",
            description=requirement.strip()[:100],
        )

        if url:
            tracer.log_event(
                event_type=tracer.EVENT.DEPLOY_SUCCESS,
                agent_id=task_id,
                message="部署成功",
                url=url,
            )
            desc_short = (
                requirement.strip()[:20] + "..."
                if len(requirement.strip()) > 20
                else requirement.strip()
            )
            await handle.notify_agent(
                f"✅ WebApp 部署成功! (ID: {task_id})\n📝 {desc_short}\n🔗 {url}",
            )
            tracer.log_event(
                event_type=tracer.EVENT.NOTIFICATION_SENT,
                agent_id=task_id,
                message="已通知主 Agent: 部署成功",
            )
            tracer.finalize("SUCCESS")
            yield TaskCtl.success("部署成功", data={"url": url})
        else:
            tracer.log_event(
                event_type=tracer.EVENT.DEPLOY_FAILED,
                agent_id=task_id,
                message="部署失败，URL 为空",
                level="ERROR",
            )
            await handle.notify_agent(
                f"❌ WebApp 部署失败 (ID: {task_id})\n请检查 Worker 配置",
            )
            tracer.log_event(
                event_type=tracer.EVENT.NOTIFICATION_SENT,
                agent_id=task_id,
                message="已通知主 Agent: 部署失败",
            )
            tracer.finalize("DEPLOY_FAILED")
            yield TaskCtl.fail("部署失败")

    except Exception as e:
        logger.exception(f"WebApp 任务异常: {e}")
        await handle.notify_agent(f"❌ WebApp 任务异常 (ID: {task_id}): {e}")
        tracer.log_event(
            event_type=tracer.EVENT.NOTIFICATION_SENT,
            agent_id=task_id,
            message=f"已通知主 Agent: 任务异常 - {e}",
        )
        tracer.finalize("ERROR", str(e))
        yield TaskCtl.fail(f"任务异常: {e}")


# ==================== 沙盒方法 ====================


@plugin.mount_sandbox_method(SandboxMethodType.TOOL, "创建WebApp任务")
async def create_webapp_task(
    _ctx: AgentCtx,
    requirement: str,
) -> str:
    """创建 WebApp 开发任务

    启动后台 AI 开发任务。任务完成后会自动通知。

    Args:
        requirement: 完整的网页需求描述（必须自包含所有必要信息）

    Returns:
        str: 创建成功返回任务 ID，失败抛出异常
    """
    from .services.task_manager import task_manager

    if not requirement or not requirement.strip():
        raise ValueError("需求描述不能为空")
    if not config.WORKER_URL or not config.ACCESS_KEY:
        raise ValueError("未配置 Worker 地址或访问密钥")

    # 检查并行任务数
    active_count = len(
        [
            t
            for t in task_manager.list_active_tasks(_ctx.chat_key)
            if t.status in ("pending", "running")
        ],
    )
    if active_count >= config.MAX_CONCURRENT_TASKS:
        raise ValueError(f"已达最大并行任务数 ({config.MAX_CONCURRENT_TASKS})")

    # 创建任务记录
    webapp_task = task_manager.create_task(_ctx.chat_key, requirement)
    task_id = webapp_task.task_id

    # 终态回调：统一处理任务状态同步
    def _on_terminal(ctl: TaskCtl) -> None:
        if ctl.signal == TaskSignal.SUCCESS:
            url = ctl.data.get("url") if isinstance(ctl.data, dict) else None
            task_manager.update_status(_ctx.chat_key, task_id, "success", url=url)
        else:
            task_manager.update_status(_ctx.chat_key, task_id, "failed", error=ctl.message)

    # 启动异步执行
    try:
        await task.start(
            task_type="webapp_dev",
            task_id=task_id,
            chat_key=_ctx.chat_key,
            plugin=plugin,
            on_terminal=_on_terminal,
            requirement=requirement.strip(),
            webapp_task_id=task_id,
        )
        task_manager.update_status(_ctx.chat_key, task_id, "running")
    except ValueError as e:
        task_manager.update_status(_ctx.chat_key, task_id, "failed", error=str(e))
        raise ValueError(f"启动失败: {e}") from e

    return task_id


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, "发送WebApp反馈")
async def send_webapp_feedback(
    _ctx: AgentCtx,
    task_id: str,
    feedback: str,
) -> str:
    """向指定任务发送反馈

    可用于：
    - 为运行中的任务追加新需求
    - 为失败的任务提供修复指导（会重新启动任务）

    Args:
        task_id: 任务 ID
        feedback: 反馈内容（新需求或修复指导）

    Returns:
        str: 操作确认信息
    """
    from .services.runtime_state import runtime_state
    from .services.task_manager import task_manager

    if not feedback or not feedback.strip():
        raise ValueError("反馈内容不能为空")

    task_info = task_manager.get_task(_ctx.chat_key, task_id)
    if not task_info:
        raise ValueError(f"任务 {task_id} 不存在")

    # 追加需求
    task_manager.append_requirement(_ctx.chat_key, task_id, feedback)

    # 如果任务正在运行，尝试实时打断
    if task_info.status == "running":
        state_obj = runtime_state.get_state(_ctx.chat_key, task_id)
        if state_obj and state_obj.inject_feedback(feedback):
            return f"⚡ 已注入反馈到任务 {task_id}，正在打断当前操作..."
        return "✅ 已追加 feedback，AI 将在下一轮迭代处理。"

    # 如果任务已失败或已完成，重新启动
    if task_info.status in ("failed", "completed", "success"):
        # 获取现有文件列表用于恢复上下文
        project_ctx = get_project_context(_ctx.chat_key, task_id)
        existing_files = list(project_ctx.list_files())

        # 终态回调
        def _on_terminal(ctl: TaskCtl) -> None:
            if ctl.signal == TaskSignal.SUCCESS:
                url = ctl.data.get("url") if isinstance(ctl.data, dict) else None
                task_manager.update_status(_ctx.chat_key, task_id, "success", url=url)
            else:
                task_manager.update_status(_ctx.chat_key, task_id, "failed", error=ctl.message)

        try:
            await task.start(
                task_type="webapp_dev",
                task_id=task_id,
                chat_key=_ctx.chat_key,
                plugin=plugin,
                on_terminal=_on_terminal,
                requirement=task_info.get_full_requirement(),
                webapp_task_id=task_id,
                existing_files=existing_files,
            )
            task_manager.update_status(_ctx.chat_key, task_id, "running")
        except ValueError as e:
            raise ValueError(f"重启失败: {e}") from e
        else:
            return f"🔄 已重启任务 {task_id} (继承 {len(existing_files)} 个现有文件)"

    return f"已追加反馈到任务 {task_id}"


@plugin.mount_sandbox_method(SandboxMethodType.AGENT, "查看WebApp任务状态")
async def get_webapp_task_status(
    _ctx: AgentCtx,
    task_id: str,
) -> str:
    """查看指定任务的详细状态

    返回任务进度、文件列表、错误信息等供反馈或分析。

    Args:
        task_id: 任务 ID

    Returns:
        str: 任务详细状态信息
    """
    from .services.task_manager import task_manager

    task_info = task_manager.get_task(_ctx.chat_key, task_id)
    if not task_info:
        return f"任务 {task_id} 不存在"

    lines = [
        f"任务 ID: {task_id}",
        f"状态: {task_info.status}",
        f"描述: {task_info.description}",
    ]

    if task_info.url:
        lines.append(f"部署链接: {task_info.url}")

    if task_info.error:
        lines.append(f"错误信息: {task_info.error}")

    if len(task_info.requirements) > 1:
        lines.append(f"需求历史 ({len(task_info.requirements)} 条):")
        for i, req in enumerate(task_info.requirements, 1):
            preview = req[:80] + "..." if len(req) > 80 else req
            lines.append(f"  {i}. {preview}")

    # 项目文件
    project = get_project_context(_ctx.chat_key, task_id)
    files = project.list_files()
    if files:
        lines.append(f"项目文件 ({len(files)} 个):")
        for f in sorted(files)[:10]:
            content = project.read_file(f)
            size = len(content) if content else 0
            lines.append(f"  - {f} ({size} chars)")
        if len(files) > 10:
            lines.append(f"  ... 还有 {len(files) - 10} 个文件")

    return "\n".join(lines)


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, "取消WebApp任务")
async def cancel_webapp_task(
    _ctx: AgentCtx,
    task_id: str,
) -> str:
    """取消指定的 WebApp 任务

    Args:
        task_id: 任务 ID

    Returns:
        str: 操作确认
    """
    from .services.task_manager import task_manager

    task_info = task_manager.get_task(_ctx.chat_key, task_id)
    if not task_info:
        raise ValueError(f"任务 {task_id} 不存在")

    if task_info.status not in ("pending", "running"):
        raise ValueError(f"任务 {task_id} 状态为 {task_info.status}，无法取消")

    # 尝试取消实际任务
    if task.is_running("webapp_dev", task_id):
        await task.cancel("webapp_dev", task_id)

    task_manager.update_status(_ctx.chat_key, task_id, "failed", error="用户取消")
    return f"已取消任务 {task_id}"


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, "归档WebApp任务")
async def archive_webapp_task(
    _ctx: AgentCtx,
    task_id: str,
) -> str:
    """归档已完成的任务

    归档后的任务不再显示在状态列表中且不再可访问。

    ⚠️ 注意：你应当遵循 “懒归档” 策略，只归档长期未访问的任务，或者在需要创建新任务时才归档不再需要的任务。永远不要在刚完成一个任务后立即归档它！

    Args:
        task_id: 任务 ID

    Returns:
        str: 操作确认
    """
    from .services.task_manager import task_manager

    task_info = task_manager.get_task(_ctx.chat_key, task_id)
    if not task_info:
        raise ValueError(f"任务 {task_id} 不存在")

    # 如果任务还在运行，自动取消
    if task_info.status == "running":
        if task.is_running("webapp_dev", task_id):
            await task.cancel("webapp_dev", task_id)
        task_manager.update_status(
            _ctx.chat_key, task_id, "failed", error="用户归档时取消",
        )

    task_manager.archive_task(_ctx.chat_key, task_id)
    return f"已归档任务 {task_id}"


@plugin.mount_sandbox_method(SandboxMethodType.BEHAVIOR, "清空WebApp项目")
async def clear_webapp_project(_ctx: AgentCtx, task_id: str) -> str:
    """清空当前任务的项目文件

    Args:
        task_id: 任务 ID
    """
    from .services.task_manager import task_manager

    # 检查是否有任务运行
    if task.is_running("webapp_dev", task_id):
        raise ValueError("该任务正在运行，请先取消任务")

    # 验证任务是否存在 (可选，也可以允许清理未知的孤儿上下文)
    # task_info = task_manager.get_task(_ctx.chat_key, task_id)

    project = get_project_context(_ctx.chat_key, task_id)
    file_count = len(project.list_files())

    if file_count == 0:
        return f"任务 {task_id} 的项目已为空"

    clear_project_context(_ctx.chat_key, task_id)
    return f"已清空 {file_count} 个文件 (任务: {task_id})"


# ==================== 提示词注入 ====================


@plugin.mount_prompt_inject_method("webapp_status")
async def webapp_status_inject(_ctx: AgentCtx) -> str:
    """注入任务状态视图，供主 Agent 按 task_id 协调操作"""
    from .services.task_manager import task_manager

    try:
        tasks = task_manager.list_active_tasks(_ctx.chat_key)

        # 统计活跃任务数（pending + running）
        active_count = sum(1 for t in tasks if t.status in ("pending", "running"))
        max_tasks = config.MAX_CONCURRENT_TASKS

        if not tasks:
            # 无任务时仍显示槽位信息
            return f"[WebApp] 任务槽位: {active_count}/{max_tasks}"

        lines = [f"[WebApp 任务] 槽位: {active_count}/{max_tasks}"]
        for t in tasks[:5]:
            icon = {
                "running": "🔄",
                "pending": "⏳",
                "success": "✅",
                "failed": "❌",
            }.get(t.status, "?")

            # 突出显示 task_id
            desc = (
                t.description[:35] + "..." if len(t.description) > 35 else t.description
            )
            lines.append(f"{icon} task_id={t.task_id} | {desc}")

            if t.url:
                lines.append(f"   └─ {t.url}")
            if t.error:
                err = t.error[:40] + "..." if len(t.error) > 40 else t.error
                lines.append(f"   └─ 错误: {err}")

        # 操作提示
        has_failed = any(t.status == "failed" for t in tasks)
        has_success = any(t.status == "success" for t in tasks)

        if has_failed:
            lines.append("可用 发送WebApp反馈(task_id, feedback) 重启失败任务")

        # 提醒不要过早归档
        if has_success:
            lines.append("注意: 不要完成任务后立即归档它，保留供用户可能的后续修改")

        return "\n".join(lines)

    except Exception:
        return ""


# ==================== 生命周期 ====================


@plugin.on_enabled()
async def _startup() -> None:
    """插件启动"""
    try:
        from .services import node_manager
        from .services.task_tracer import TaskTracer

        # 使用 Dummy Tracer 检查环境，避免生成日志文件
        tracer = TaskTracer(
            chat_key="system",
            root_agent_id="startup",
            task_description="environment check",
            plugin_data_dir=str(plugin.get_plugin_data_dir()),
            enabled=False,
        )

        node_path = await node_manager.get_node_executable(tracer, agent_id="startup")
        logger.info(f"WebApp 插件已启用 (Node.js: {node_path})")
    except Exception as e:
        logger.error(f"WebApp 插件启动警告: 本地编译环境自检失败 - {e}")
        logger.error("请确保系统安装了 Node.js (>=16)")


@plugin.on_disabled()
async def _cleanup() -> None:
    """插件停用"""
    # 停止所有任务
    count = await task.stop_all()
    if count > 0:
        logger.info(f"WebApp 插件停用，已停止 {count} 个任务")
    logger.info("WebApp 插件已停用")
