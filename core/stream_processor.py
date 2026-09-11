"""异步并行流处理器 (Text-to-Tool Bridge 版本)

实现生产者-消费者模式的流式处理：
- Producer: 接收 LLM 纯文本流，使用 CommandStreamParser 解析出操作
- Consumer: 从队列取操作，立即执行（文件写入或工具调用）
- 错误时立即取消 Producer，节省 token

核心设计原则：
1. LLM = 乐观生产者，输出纯文本流
2. 操作块完成后立即执行，不等待整个流结束
3. 失败时打断生产者，丢弃后续内容
4. 一次 LLM 响应 = 一次迭代
"""

import asyncio
from asyncio import CancelledError, Queue
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from .command_parser import CommandStreamParser, CommandType, ParsedCommand
from .logger import logger
from .streaming_client import stream_text_completion

if TYPE_CHECKING:
    from .context import ToolContext


class ControlUnitType(Enum):
    """控制流单元类型"""

    FILE = "file"  # 文件内容块（直接写入 VFS）
    TOOL_CALL = "tool_call"  # 工具调用
    END = "end"  # 流结束标记


@dataclass
class ControlUnit:
    """控制流单元

    表示一个可执行的操作块：文件写入或工具调用
    """

    type: ControlUnitType

    # FILE 类型
    file_path: Optional[str] = None
    file_content: Optional[str] = None

    # TOOL_CALL 类型
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    tool_call_id: Optional[str] = None  # 为兼容保留，纯文本模式下为 None

    # 执行结果
    executed: bool = False
    success: bool = False
    result: Optional[str] = None
    should_feedback: bool = False  # 是否需要将结果反馈给 LLM（查询型工具为 True）

    def __repr__(self) -> str:
        if self.type == ControlUnitType.FILE:
            return f"FILE({self.file_path})"
        if self.type == ControlUnitType.TOOL_CALL:
            return f"TOOL({self.tool_name})"
        return "END"


def process_block_command(cmd: ParsedCommand) -> ControlUnit:
    """处理块命令，根据块类型转换为 ControlUnit
    
    Args:
        cmd: 解析出的块命令
        
    Returns:
        ControlUnit: FILE 类型直接写入，DIFF 类型调用 apply_diff
    """
    from ..tools.block_tools import get_block_tool
    
    block_name = cmd.block_name or ""
    block_tool = get_block_tool(block_name)
    
    if block_tool is None:
        # 未知块类型，当作文件处理
        logger.warning(f"未知块类型 {block_name}，当作 FILE 处理")
        return ControlUnit(
            type=ControlUnitType.FILE,
            file_path=cmd.block_arg,
            file_content=cmd.block_content,
        )
    
    if block_tool.is_direct_write:
        # 直接写入类型（如 FILE）
        return ControlUnit(
            type=ControlUnitType.FILE,
            file_path=cmd.block_arg,
            file_content=cmd.block_content,
        )
    
    # 需要调用工具的类型（如 DIFF -> apply_diff）
    # 根据块名映射到对应的行工具
    tool_mapping = {
        "DIFF": ("apply_diff", {"path": cmd.block_arg, "diff": cmd.block_content}),
    }
    
    if block_name.upper() in tool_mapping:
        tool_name, tool_args = tool_mapping[block_name.upper()]
        return ControlUnit(
            type=ControlUnitType.TOOL_CALL,
            tool_name=tool_name,
            tool_args=tool_args,
        )
    
    # 未配置映射，尝试直接调用 block handler
    # 这种情况理论上不应该发生，因为所有非直接写入的块都应该配置映射
    logger.warning(f"块类型 {block_name} 未配置工具映射，当作 FILE 处理")
    return ControlUnit(
        type=ControlUnitType.FILE,
        file_path=cmd.block_arg,
        file_content=cmd.block_content,
    )


@dataclass
class IterationResult:
    """单次迭代结果"""

    executed_units: List[ControlUnit] = field(default_factory=list)
    discarded_units: List[ControlUnit] = field(default_factory=list)
    assistant_content: str = ""  # LLM 输出的完整 text 内容
    error: Optional[str] = None
    completed: bool = False  # done 工具是否成功调用
    truncated: bool = False  # 是否因 token 限制截断


class StreamProcessor:
    """异步并行流处理器 (Text-to-Tool Bridge 版本)

    使用 asyncio.Queue 实现生产者-消费者模式
    使用 CommandStreamParser 解析纯文本流中的命令
    """

    def __init__(
        self,
        execute_tool_func: Callable,  # async (name, args, ctx) -> ToolResult
        write_file_func: Callable,  # (path, content) -> None
        tracer: Optional[Any] = None,
    ):
        self.execute_tool = execute_tool_func
        self.write_file = write_file_func
        self.tracer = tracer

        # 运行时状态
        self._queue: Queue[ControlUnit] = Queue()
        self._executed_units: List[ControlUnit] = []
        self._discarded_units: List[ControlUnit] = []
        self._assistant_content: str = ""
        self._error: Optional[str] = None
        self._completed: bool = False
        self._truncated: bool = False
        self._producer_task: Optional[asyncio.Task[None]] = None
        self._cancelled: bool = False

    def _log(self, level: str, message: str, **kwargs: Any) -> None:
        """统一日志"""
        log_func = getattr(logger, level, logger.info)
        log_func(f"[StreamProcessor] {message}")

        if self.tracer:
            event_type = kwargs.pop("event_type", f"StreamProcessor.{level.upper()}")
            self.tracer.log_event(
                event_type=event_type,
                agent_id=self.tracer.task_id if hasattr(self.tracer, "task_id") else "unknown",
                message=message,
                **kwargs,
            )

    async def run(
        self,
        messages: List[Dict[str, Any]],
        model_group: str,
        ctx: Any,
    ) -> IterationResult:
        """执行单次迭代

        Args:
            messages: 消息历史
            model_group: 模型组
            ctx: 工具执行上下文

        Returns:
            IterationResult
        """
        # 重置状态
        self._queue = Queue()
        self._executed_units = []
        self._discarded_units = []
        self._assistant_content = ""
        self._error = None
        self._completed = False
        self._truncated = False
        self._cancelled = False

        self._log("info", "开始迭代 (纯文本模式)", messages_count=len(messages))

        # 启动生产者和消费者
        self._producer_task = asyncio.create_task(
            self._producer(messages, model_group),
        )
        consumer_task = asyncio.create_task(
            self._consumer(ctx),
        )

        # 等待两个任务完成
        try:
            await asyncio.gather(
                self._producer_task,
                consumer_task,
                return_exceptions=True,
            )
        except Exception as e:
            self._log("error", f"迭代异常: {e}", exception=str(e))
            self._error = str(e)

        # 清空队列中剩余的单元（被丢弃的）
        while not self._queue.empty():
            try:
                unit = self._queue.get_nowait()
                if unit.type != ControlUnitType.END:
                    self._discarded_units.append(unit)
            except asyncio.QueueEmpty:
                break

        if self._discarded_units:
            self._log(
                "warning",
                f"丢弃 {len(self._discarded_units)} 个未执行的单元",
                discarded=[str(u) for u in self._discarded_units],
            )

        self._log(
            "info",
            f"迭代完成: 执行={len(self._executed_units)}, 丢弃={len(self._discarded_units)}, "
            f"完成={self._completed}, 错误={self._error is not None}",
        )

        return IterationResult(
            executed_units=self._executed_units,
            discarded_units=self._discarded_units,
            assistant_content=self._assistant_content,
            error=self._error,
            completed=self._completed,
            truncated=self._truncated,
        )

    async def _producer(
        self,
        messages: List[Dict[str, Any]],
        model_group: str,
    ) -> None:
        """生产者：接收 LLM 纯文本流，使用 CommandStreamParser 解析"""
        parser = CommandStreamParser()
        chunk_count = 0

        # 记录 LLM 请求开始
        event_type = "LLM_CALL_START"
        if self.tracer and hasattr(self.tracer, "EVENT"):
            event_type = self.tracer.EVENT.LLM_CALL_START

        self._log(
            "info",
            f"LLM 请求开始 (纯文本模式): 消息数={len(messages)}",
            event_type=event_type,
            messages_count=len(messages),
            model_group=model_group,
        )

        try:
            async for text_chunk in stream_text_completion(
                messages=messages,
                model_group=model_group,
            ):
                chunk_count += 1

                # 检查是否被取消
                if self._cancelled:
                    self._log("info", f"Producer 被取消，已处理 {chunk_count} 个 chunk")
                    break

                # 累积 text 内容
                self._assistant_content += text_chunk

                # 解析命令
                for cmd in parser.feed(text_chunk):
                    if cmd.type == CommandType.BLOCK:
                        # 块工具（FILE, DIFF 等）
                        unit = process_block_command(cmd)
                        self._log(
                            "info",
                            f"{cmd.block_name} 块完成: {cmd.block_arg} ({len(cmd.block_content or '')} 字符)",
                            block_name=cmd.block_name,
                            block_arg=cmd.block_arg,
                            content_size=len(cmd.block_content or ""),
                        )
                    elif cmd.type == CommandType.TOOL_CALL:
                        # 行工具
                        unit = ControlUnit(
                            type=ControlUnitType.TOOL_CALL,
                            tool_name=cmd.tool_name,
                            tool_args=cmd.tool_args,
                        )
                        self._log(
                            "info",
                            f"TOOL_CALL 解析: {cmd.tool_name}({cmd.tool_args})",
                            tool_name=cmd.tool_name,
                        )
                    else:
                        continue

                    await self._queue.put(unit)

            # 流结束，刷新剩余的命令
            remaining_commands = parser.flush()
            for cmd in remaining_commands:
                if cmd.type == CommandType.BLOCK:
                    # 块工具
                    unit = process_block_command(cmd)
                    self._log(
                        "warning",
                        f"{cmd.block_name} 块未完整但已处理: {cmd.block_arg}",
                        block_name=cmd.block_name,
                        block_arg=cmd.block_arg,
                    )
                elif cmd.type == CommandType.TOOL_CALL:
                    unit = ControlUnit(
                        type=ControlUnitType.TOOL_CALL,
                        tool_name=cmd.tool_name,
                        tool_args=cmd.tool_args,
                    )
                    self._log(
                        "info",
                        f"Flush 解析到工具命令: {cmd.tool_name}",
                        tool_name=cmd.tool_name,
                    )
                else:
                    continue

                await self._queue.put(unit)

            self._log("debug", f"Producer 完成，共处理 {chunk_count} 个 chunk")

        except CancelledError:
            self._log(
                "info",
                f"Producer 被取消 (CancelledError)，已处理 {chunk_count} 个 chunk",
            )
        except Exception as e:
            self._log("error", f"Producer 异常: {e}", exception=str(e))
            raise
        finally:
            # 放入结束标记
            await self._queue.put(ControlUnit(type=ControlUnitType.END))
            self._log("debug", "Producer 放入 END 标记")

    async def _consumer(self, ctx: Any) -> None:
        """消费者：从队列取操作块，立即执行"""
        self._log("debug", "Consumer 启动")
        unit_count = 0

        try:
            while True:
                unit = await self._queue.get()

                if unit.type == ControlUnitType.END:
                    self._log("debug", "Consumer 收到 END 标记")
                    break

                unit_count += 1
                self._log(
                    "info",
                    f"Consumer 执行 #{unit_count}: {unit}",
                    unit_type=unit.type.value,
                )

                if unit.type == ControlUnitType.FILE:
                    # 写入文件
                    try:
                        self.write_file(unit.file_path, unit.file_content)
                        unit.executed = True
                        unit.success = True
                        content_len = len(unit.file_content) if unit.file_content else 0
                        unit.result = f"写入成功: {content_len} 字符"
                        self._executed_units.append(unit)
                        self._log(
                            "info",
                            f"FILE 写入成功: {unit.file_path}",
                            file_path=unit.file_path,
                            content_size=content_len,
                        )
                    except Exception as e:
                        unit.executed = True
                        unit.success = False
                        unit.result = str(e)
                        self._executed_units.append(unit)
                        self._log(
                            "error",
                            f"FILE 写入失败: {unit.file_path} - {e}",
                            file_path=unit.file_path,
                            error=str(e),
                        )
                        # 文件写入失败不中断流程

                elif unit.type == ControlUnitType.TOOL_CALL:
                    # 执行工具
                    result = await self.execute_tool(
                        unit.tool_name,
                        unit.tool_args,
                        ctx,
                    )
                    unit.executed = True
                    unit.success = result.success
                    unit.result = result.message
                    unit.should_feedback = result.should_feedback  # 传递查询型工具标记
                    self._executed_units.append(unit)

                    self._log(
                        "info" if result.success else "warning",
                        f"TOOL_CALL {'成功' if result.success else '失败'}: {unit.tool_name}",
                        tool_name=unit.tool_name,
                        success=result.success,
                        result_preview=result.message[:200] if result.message else None,
                    )

                    # 检查是否是 done
                    if unit.tool_name == "done":
                        if ctx.state.completed:
                            self._completed = True
                            self._log("info", "done 工具成功提交，任务完成")
                            self._cancel_producer()
                            break

                        self._log("warning", "done 工具调用收到但被拒绝")

                    # 检查是否需要中断
                    if not result.success and not result.recoverable:
                        self._error = result.message
                        self._log(
                            "warning",
                            f"不可恢复错误，中断流程: {result.message}",
                            error=result.message,
                        )
                        self._cancel_producer()
                        break

        except CancelledError:
            self._log("info", "Consumer 被取消")
        except Exception as e:
            self._log("error", f"Consumer 异常: {e}", exception=str(e))
            self._error = str(e)
            self._cancel_producer()

        self._log("debug", f"Consumer 完成，共执行 {unit_count} 个单元")

    def _cancel_producer(self) -> None:
        """取消生产者任务"""
        self._cancelled = True
        if self._producer_task and not self._producer_task.done():
            self._producer_task.cancel()
            self._log("info", "已发送取消信号给 Producer")
