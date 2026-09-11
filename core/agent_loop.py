"""Agent 循环 - Text-to-Tool Bridge 版本

基于 asyncio.Queue 的 Developer Agent 执行循环。
纯文本协议：LLM 输出文本流，通过标记解析执行操作。

设计原则：
- LLM = 乐观生产者：输出纯文本流，不调用 Native Tool
- Parser = CommandStreamParser：从文本流解析命令
- Executor = 并行消费者：操作块完成后立即执行
- 失败时打断生产者，节省 token
- 一次 LLM 响应 = 一次迭代
"""

from typing import Any, Dict, List, Optional, Tuple

from ..services.runtime_state import runtime_state
from ..services.task_tracer import TaskTracer
from ..services.vfs import get_project_context
from ..tools import execute_tool_safe
from .context import AgentState, ProductSpec, ToolContext
from .error_feedback import ToolResult
from .logger import logger
from .stream_processor import ControlUnitType, IterationResult, StreamProcessor


async def run_developer_loop(
    chat_key: str,
    task_description: str,
    tracer: TaskTracer,
    model_group: str,
    spec: Optional[ProductSpec] = None,
    max_iterations: int = 20,
    existing_files: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """运行 Developer Agent 主循环 (Text-to-Tool Bridge 版本)

    使用生产者-消费者模式，支持：
    - 纯文本流输出，无 Native Tool Call
    - 操作块完成后立即执行
    - 错误时立即打断 LLM 流
    - 一次 LLM 响应 = 一次迭代

    Args:
        chat_key: 会话键
        task_description: 任务描述
        tracer: 任务追踪器
        model_group: 模型组名称
        spec: 可选产品规格
        max_iterations: 最大迭代次数

    Returns:
        (success, message) 元组
    """
    project = get_project_context(chat_key, tracer.root_agent_id)

    # 初始化状态和上下文
    state = AgentState(max_iterations=max_iterations)
    ctx = ToolContext(
        chat_key=chat_key,
        task_id=tracer.root_agent_id,
        project=project,
        state=state,
        tracer=tracer,
        spec=spec,
    )

    # 初始化运行时状态
    current_runtime = runtime_state.create_state(
        task_id=tracer.root_agent_id,
        chat_key=chat_key,
        task_description=task_description,
        max_iterations=max_iterations,
    )
    runtime_state.update_status(chat_key, tracer.root_agent_id, "running", "思考中")

    logger.info(
        f"[AgentLoop] 启动 Developer Agent (Text-to-Tool Bridge, chat_key={chat_key})",
    )
    tracer.log_event(
        event_type=tracer.EVENT.AGENT_START,
        agent_id=tracer.root_agent_id,
        message="Developer Agent 启动（纯文本协议模式）",
        task_description=task_description,
        max_iterations=max_iterations,
    )

    # 构建初始消息
    messages = _build_initial_messages(task_description, spec, existing_files)
    logger.debug(f"[AgentLoop] 初始消息构建完成，共 {len(messages)} 条")

    # 纯文本模式：无需获取工具定义
    tracer.log_event(
        event_type=tracer.EVENT.LOOP_START,
        agent_id=tracer.root_agent_id,
        message=f"Developer 循环开始 (纯文本协议): {task_description[:100]}...",
        max_iterations=max_iterations,
    )

    # 创建流处理器
    processor = StreamProcessor(
        execute_tool_func=_create_tool_executor(ctx),
        write_file_func=lambda path, content: project.write_file(path, content),
        tracer=tracer,
    )

    # 注册 Processor 到运行时状态以支持打断
    if current_runtime:
        current_runtime.set_processor(processor)

    # 主循环
    iteration = 0
    empty_response_count = 0  # 空响应计数器
    while iteration < max_iterations:
        iteration += 1
        state.iteration = iteration

        # 更新运行时状态
        runtime_state.update_iteration(chat_key, tracer.root_agent_id, iteration)
        runtime_state.update_status(
            chat_key, tracer.root_agent_id, "running", f"迭代 {iteration}...",
        )

        logger.info(f"[AgentLoop] ===== 开始迭代 {iteration}/{max_iterations} =====")
        tracer.log_event(
            event_type=tracer.EVENT.ITERATION_START,
            agent_id=ctx.task_id,
            message=f"开始迭代 {iteration}/{max_iterations}",
            iteration=iteration,
        )

        # 保存 VFS 快照
        tracer.save_vfs_snapshot(project)

        try:
            # 使用 StreamProcessor 执行迭代 (纯文本模式，无 tools 参数)
            result = await processor.run(
                messages=messages,
                model_group=model_group,
                ctx=ctx,
            )

            # --- 实时反馈处理 ---
            pending_feedback = None
            if current_runtime:
                pending_feedback = current_runtime.consume_feedback()

            if pending_feedback:
                logger.info("[AgentLoop] 检测到实时反馈，注入消息历史")
                # 记录部分执行结果（如果有）
                _log_iteration_result(result, tracer, ctx)

                # 添加 assistant 的部分输出（如果有）到历史，保持上下文连贯
                if result.assistant_content:
                    messages.append(
                        {"role": "assistant", "content": result.assistant_content},
                    )

                # 注入反馈消息
                messages.append(
                    {
                        "role": "user",
                        "content": f"⚡ 新需求 (此反馈打断了之前的输出):\n{pending_feedback}\n\n请在当前进度基础上继续。",
                    },
                )

                empty_response_count = 0
                continue  # 跳过后续处理，直接进入下一轮
            # ---------------------

            # 构建 assistant 消息 (纯文本，无 tool_calls)
            assistant_msg = _build_assistant_message(result)

            # 保存提示词日志
            tracer.save_prompt(
                ctx.task_id,
                messages,
                assistant_msg,
            )

            # 记录执行结果
            _log_iteration_result(result, tracer, ctx)

            # 添加 assistant 消息到历史
            messages.append(assistant_msg)
            logger.debug(
                f"[AgentLoop] 添加 assistant 消息，content 长度={len(result.assistant_content)}",
            )

            # 纯文本模式：不需要添加 role: tool 消息
            # 工具调用通过文本解析执行，LLM 不知道有"工具"

            # 检查是否完成
            if result.completed:
                state.completed = True

                # 尝试从 done 工具参数中提取标题
                app_title = None
                for unit in result.executed_units:
                    if unit.type.value == "tool_call" and unit.tool_name == "done":
                        if unit.tool_args:
                            app_title = unit.tool_args.get("title")
                        break

                if app_title:
                    logger.info(f"[AgentLoop] 设置 WebApp 标题: {app_title}")
                    if hasattr(
                        runtime_state, "set_title",
                    ):  # Safety check or direct access
                        pass  # Actually runtime_state doesn't have set_title, we need to access state object

                    # 更新状态中的标题
                    r_state = runtime_state.get_state(chat_key, tracer.root_agent_id)
                    if r_state:
                        r_state.title = app_title

                runtime_state.complete(chat_key, tracer.root_agent_id, True)
                logger.info("[AgentLoop] 任务完成（@@DONE 命令成功执行）")
                tracer.log_event(
                    event_type=tracer.EVENT.LOOP_SUCCESS,
                    agent_id=tracer.root_agent_id,
                    message="Developer 循环成功完成",
                )
                return True, "任务完成"

            # 构建反馈消息
            feedback = _build_feedback_message(result)
            if feedback:
                messages.append({"role": "user", "content": feedback})
                logger.debug(f"[AgentLoop] 添加 user 反馈，长度={len(feedback)}")

            # 如果有错误但可恢复，继续循环让 LLM 尝试修复
            if result.error:
                logger.warning(f"[AgentLoop] 迭代有错误但继续: {result.error}")
                empty_response_count = 0  # 有响应，重置计数

            # 空响应检测
            elif len(result.executed_units) == 0 and not result.completed:
                empty_response_count += 1
                logger.warning(f"[AgentLoop] 空响应检测: {empty_response_count}/3")

                if empty_response_count >= 3:
                    # 连续 3 次空响应，自动中止
                    tracer.log_event(
                        event_type=tracer.EVENT.LOOP_TIMEOUT,
                        agent_id=tracer.root_agent_id,
                        message="连续空响应，任务自动中止",
                        level="ERROR",
                    )
                    runtime_state.complete(chat_key, tracer.root_agent_id, True)
                    return False, "连续空响应，任务自动中止"

                # 添加提示消息
                messages.append(
                    {
                        "role": "user",
                        "content": f"⚠️ 未检测到有效输出 ({empty_response_count}/3)\n"
                        "请按协议输出文件或命令。\n"
                        '完成任务: @@DONE summary="描述"\n'
                        '中止任务: @@ABORT reason="原因"',
                    },
                )
            else:
                # 有正常输出，重置计数
                empty_response_count = 0

        except Exception as e:
            logger.exception(f"[AgentLoop] 迭代 {iteration} 发生异常")
            tracer.log_event(
                event_type=tracer.EVENT.ITERATION_ERROR,
                agent_id=tracer.root_agent_id,
                message=f"迭代异常: {e}",
                level="ERROR",
            )
            # 添加错误消息继续尝试
            messages.append(
                {
                    "role": "user",
                    "content": f"⚠️ 系统异常: {e}\n请继续。",
                },
            )

    # 达到最大迭代次数
    logger.warning(f"[AgentLoop] 达到最大迭代次数 {max_iterations}")
    runtime_state.complete(chat_key, tracer.root_agent_id, success=False)
    tracer.log_event(
        event_type=tracer.EVENT.LOOP_TIMEOUT,
        agent_id=tracer.root_agent_id,
        message=f"达到最大迭代次数 {max_iterations}",
        level="WARNING",
    )
    return False, f"达到最大迭代次数 {max_iterations}"


def _create_tool_executor(ctx: ToolContext):
    """创建工具执行器闭包"""

    async def executor(
        tool_name: str, tool_args: Dict[str, Any], _ctx: Any,
    ) -> ToolResult:
        from .error_feedback import ErrorType
        from .error_feedback import ToolResult as TR

        # 执行工具
        result = await execute_tool_safe(tool_name, tool_args, ctx)

        # 更新运行时状态
        runtime_state.add_tool_call(
            chat_key=ctx.chat_key,
            task_id=ctx.task_id,
            name=tool_name,
            success=result.success,
            message=result.message,
        )

        logger.debug(
            f"[ToolExecutor] {tool_name} -> {'成功' if result.success else '失败'}",
        )
        return result

    return executor


def _log_iteration_result(
    result: IterationResult, tracer: TaskTracer, ctx: ToolContext,
):
    """记录迭代结果到日志"""
    for unit in result.executed_units:
        if unit.type == ControlUnitType.TOOL_CALL:
            tracer.log_event(
                event_type=tracer.EVENT.TOOL_CALL,
                agent_id=ctx.task_id,
                message=f"工具 {unit.tool_name}: {'成功' if unit.success else '失败'}\n{(unit.result or '')[:500]}",
                tool_name=unit.tool_name,
                success=unit.success,
                full_message=unit.result,
            )
        elif unit.type == ControlUnitType.FILE:
            tracer.log_event(
                event_type=tracer.EVENT.FILE_WRITTEN,
                agent_id=ctx.task_id,
                message=f"文件写入: {unit.file_path}",
                file_path=unit.file_path,
                content_size=len(unit.file_content) if unit.file_content else 0,
            )

    if result.discarded_units:
        tracer.log_event(
            event_type=tracer.EVENT.UNITS_DISCARDED,
            agent_id=ctx.task_id,
            message=f"丢弃 {len(result.discarded_units)} 个未执行的单元",
            discarded_count=len(result.discarded_units),
            level="WARNING",
        )


def _build_assistant_message(result: IterationResult) -> Dict[str, Any]:
    """构建 assistant 消息 (纯文本模式)"""
    msg: Dict[str, Any] = {"role": "assistant"}

    # 添加文本内容
    if result.assistant_content:
        msg["content"] = result.assistant_content
    else:
        msg["content"] = ""

    # 纯文本模式：不添加 tool_calls 字段
    # 工具调用通过文本标记 (@@TOOL) 表达

    return msg


def _build_feedback_message(result: IterationResult) -> str:
    """构建反馈消息

    纯文本模式下：
    - 失败的操作：反馈错误信息
    - 成功且 should_feedback=True 的操作：反馈结果（如 read_files 的文件内容）
    - 成功且 should_feedback=False 的操作：静默处理（如 write_file）
    """
    parts = []

    # 收集需要反馈的成功结果（查询型工具）
    feedback_results = []
    for unit in result.executed_units:
        if unit.success and unit.should_feedback and unit.result:
            feedback_results.append(unit.result)

    if feedback_results:
        parts.extend(feedback_results)

    # 收集失败的操作
    failures = []
    for unit in result.executed_units:
        if not unit.success:
            if unit.type == ControlUnitType.TOOL_CALL:
                failures.append(f"❌ [{unit.tool_name}] {unit.result or ''}")
            elif unit.type == ControlUnitType.FILE:
                failures.append(f"❌ [FILE] {unit.file_path}: {unit.result or ''}")

    if failures:
        parts.append("=== 执行错误 ===\n")
        parts.extend(failures)

    # 错误信息
    if result.error:
        parts.append(f"\n⚠️ 执行中断: {result.error}")
        parts.append("请根据错误信息修复后继续。")

    # 截断提示
    if result.truncated:
        parts.append("\n[输出被截断，请继续]")

    return "\n".join(parts)


def _build_initial_messages(
    task_description: str,
    spec: Optional[ProductSpec],
    existing_files: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """构建初始消息列表 (纯文本协议)"""
    from ..prompts.developer import build_system_prompt

    system_prompt = build_system_prompt(spec)

    # 纯文本协议指引
    protocol_instructions = """
## 输出协议

你的输出是**纯文本流**。使用约定标记控制操作。

### 文件操作

使用 `<<<FILE: path>>>` 和 `<<<END_FILE>>>` 包裹文件内容：

```
<<<FILE: src/main.tsx>>>
import React from 'react'
...
<<<END_FILE>>>
```

### 控制命令

在独立的行上使用 `@@COMMAND` 格式：

| 命令 | 语法 | 说明 |
|------|------|------|
| 编译 | `@@COMPILE` | 触发编译验证 |
| 完成 | `@@DONE summary="任务完成描述"` | 标记任务完成 |
| 中止 | `@@ABORT reason="原因"` | 遇到无法解决的问题时中止 |

### 完整示例

```
<<<FILE: src/main.tsx>>>
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
<<<END_FILE>>>

<<<FILE: src/App.tsx>>>
export default function App() {
  return <div className="p-8 text-center">Hello World</div>
}
<<<END_FILE>>>

<<<FILE: src/index.css>>>
@tailwind base;
@tailwind components;
@tailwind utilities;
<<<END_FILE>>>

@@DONE summary="实现了入口、App组件和样式" title="我的精彩应用"
```

### 关键规则

1. **连续输出**: 一次响应中输出所有文件，不要停顿
2. **立即执行**: 每个 `<<<END_FILE>>>` 后文件立即保存
3. **错误修复**: 如果收到错误反馈，修复后继续
4. **不要聊天**: 直接输出文件和命令，不要解释
"""

    if existing_files:
        # 按名称排序
        sorted_files = sorted(existing_files)
        # 限制显示数量，防止 prompt 过长
        display_files = sorted_files[:50]

        file_list_str = "\n".join(f"  - {f}" for f in display_files)
        if len(sorted_files) > 50:
            file_list_str += f"\n  ... (还有 {len(sorted_files) - 50} 个文件)"

        context = (
            f"## 现有项目状态\n\n"
            f"已有 {len(existing_files)} 个文件：\n"
            f"{file_list_str}\n\n"
            f"**重要提示**:\n"
            f"1. 这是一个**修改任务**，请在现有代码基础上工作。\n"
            f'2. 不要猜测文件内容。请先使用 `@@READ paths="path1,path2"` 读取我们需要修改的文件内容。\n'
            f"3. 也可以使用 `@@TOOL list_files` 查看完整的导出信息。\n"
            f"4. 不要重写整个项目，只修改必要的部分。\n\n"
            f"---\n\n"
        )
        task_description = context + task_description

    return [
        {"role": "system", "content": system_prompt + protocol_instructions},
        # --- Warm-up Session (纯文本示例) ---
        {
            "role": "user",
            "content": "System Check: 请使用纯文本协议创建一个测试文件 `Ping.tsx`。",
        },
        # 模拟正确的纯文本输出
        {
            "role": "assistant",
            "content": """<<<FILE: src/Ping.tsx>>>
export const Ping = () => "Pong";
<<<END_FILE>>>

@@DONE summary="System check passed" title="连通性测试"
""",
        },
        # 模拟成功反馈
        {
            "role": "user",
            "content": "✅ 任务成功提交!\n编译输出: Build success.\n总结: System check passed",
        },
        # --- Actual Task Start ---
        {
            "role": "user",
            "content": f"协议验证通过。请开始执行任务:\n\n{task_description}",
        },
    ]
