"""运行时状态管理器

追踪 Agent 运行时状态，提供给命令查询使用。
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolCallRecord:
    """工具调用记录"""

    name: str
    success: bool
    message: str
    timestamp: float


@dataclass
class AgentRuntimeState:
    """Agent 运行时状态"""

    # 任务基本信息
    task_id: str
    chat_key: str
    task_description: str
    start_time: float
    title: Optional[str] = None  # WebApp 标题

    # 运行状态
    status: str = "initializing"  # initializing, running, compiling, completed, failed
    current_phase: str = "启动中"
    iteration: int = 0
    max_iterations: int = 20

    # 工具调用历史
    tool_calls: List[ToolCallRecord] = field(default_factory=list)

    # 编译状态
    compile_count: int = 0
    last_compile_success: Optional[bool] = None
    last_compile_error: Optional[str] = None

    # 文件状态
    files_created: int = 0
    files_modified: int = 0

    def elapsed_seconds(self) -> float:
        """已运行时间（秒）"""
        return time.time() - self.start_time

    def elapsed_formatted(self) -> str:
        """格式化的运行时间"""
        elapsed = int(self.elapsed_seconds())
        if elapsed < 60:
            return f"{elapsed}秒"
        if elapsed < 3600:
            return f"{elapsed // 60}分{elapsed % 60}秒"
        return f"{elapsed // 3600}时{(elapsed % 3600) // 60}分"

    def progress_percent(self) -> int:
        """进度百分比估算"""
        if self.status == "completed":
            return 100
        if self.status == "failed":
            return 0
        # 基于迭代和编译状态估算
        base = min(80, self.iteration * 15)
        if self.last_compile_success:
            base = max(base, 70)
        return base

    def add_tool_call(self, name: str, success: bool, message: str) -> None:
        """添加工具调用记录"""
        self.tool_calls.append(
            ToolCallRecord(
                name=name,
                success=success,
                message=message,
                timestamp=time.time(),
            ),
        )

        # 更新统计
        if name == "write_file":
            self.files_created += 1
        elif name == "apply_diff":
            self.files_modified += 1
        elif name == "compile":
            self.compile_count += 1
            self.last_compile_success = success
            if not success:
                self.last_compile_error = message[:200]

    # 交互状态
    pending_feedback: Optional[str] = None
    _processor: Optional[Any] = None

    def set_processor(self, processor: Any) -> None:
        self._processor = processor

    def inject_feedback(self, feedback: str) -> bool:
        """注入反馈并打断当前流"""
        self.pending_feedback = feedback
        if self._processor and hasattr(self._processor, "_cancelled"):
            self._processor._cancelled = True  # noqa: SLF001
            return True
        return False

    def consume_feedback(self) -> Optional[str]:
        fb = self.pending_feedback
        self.pending_feedback = None
        return fb


class RuntimeStateManager:
    """运行时状态管理器

    全局单例，追踪所有运行中的任务状态。
    """

    _instance: Optional["RuntimeStateManager"] = None
    _states: Dict[str, AgentRuntimeState]

    def __new__(cls) -> "RuntimeStateManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not hasattr(self, "_states"):
            self._states = {}

    def create_state(
        self,
        task_id: str,
        chat_key: str,
        task_description: str,
        max_iterations: int = 20,
    ) -> AgentRuntimeState:
        """创建新的运行状态"""
        key = f"{chat_key}::{task_id}"
        state = AgentRuntimeState(
            task_id=task_id,
            chat_key=chat_key,
            task_description=task_description,
            start_time=time.time(),
            max_iterations=max_iterations,
        )
        self._states[key] = state
        return state

    def get_state(self, chat_key: str, task_id: str) -> Optional[AgentRuntimeState]:
        """获取特定任务的运行状态"""
        key = f"{chat_key}::{task_id}"
        return self._states.get(key)

    def get_states_by_chat_key(self, chat_key: str) -> List[AgentRuntimeState]:
        """获取会话下所有任务的运行状态"""
        prefix = f"{chat_key}::"
        return [s for k, s in self._states.items() if k.startswith(prefix)]

    def update_status(self, chat_key: str, task_id: str, status: str, phase: str) -> None:
        """更新状态"""
        key = f"{chat_key}::{task_id}"
        state = self._states.get(key)
        if state:
            state.status = status
            state.current_phase = phase

    def update_iteration(self, chat_key: str, task_id: str, iteration: int) -> None:
        """更新迭代次数"""
        key = f"{chat_key}::{task_id}"
        state = self._states.get(key)
        if state:
            state.iteration = iteration

    def add_tool_call(
        self,
        chat_key: str,
        task_id: str,
        name: str,
        success: bool,
        message: str,
    ) -> None:
        """记录工具调用"""
        key = f"{chat_key}::{task_id}"
        state = self._states.get(key)
        if state:
            state.add_tool_call(name, success, message)

    def complete(self, chat_key: str, task_id: str, success: bool) -> None:
        """标记任务完成"""
        key = f"{chat_key}::{task_id}"
        state = self._states.get(key)
        if state:
            state.status = "completed" if success else "failed"
            state.current_phase = "已完成" if success else "失败"

    def remove_state(self, chat_key: str, task_id: str) -> None:
        """移除状态"""
        key = f"{chat_key}::{task_id}"
        self._states.pop(key, None)

    def get_all_running(self) -> List[AgentRuntimeState]:
        """获取所有运行中的任务"""
        return [s for s in self._states.values() if s.status == "running"]


# 全局实例
runtime_state = RuntimeStateManager()
