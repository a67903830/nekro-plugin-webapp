"""事件流系统

提供 TUI 和 API 的实时事件订阅。
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class EventType(str, Enum):
    """事件类型"""
    
    # 任务生命周期
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    
    # LLM 输出
    LLM_CHUNK = "llm_chunk"
    LLM_COMPLETE = "llm_complete"
    
    # 工具调用
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    
    # 文件操作
    FILE_CREATED = "file_created"
    FILE_MODIFIED = "file_modified"
    
    # 编译
    COMPILE_START = "compile_start"
    COMPILE_SUCCESS = "compile_success"
    COMPILE_FAILED = "compile_failed"
    
    # 部署
    DEPLOY_START = "deploy_start"
    DEPLOY_SUCCESS = "deploy_success"
    DEPLOY_FAILED = "deploy_failed"
    
    # 通知
    NOTIFICATION = "notification"
    
    # 用户反馈
    USER_FEEDBACK = "user_feedback"


@dataclass
class TaskEvent:
    """任务事件"""
    
    type: EventType
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    data: Dict[str, Any] = field(default_factory=dict)


class TaskStream:
    """任务事件流
    
    支持多订阅者的事件发布/订阅系统。
    """
    
    def __init__(self):
        self._subscribers: List[asyncio.Queue] = []
        self._events: List[TaskEvent] = []
        self._feedback_queue: asyncio.Queue[str] = asyncio.Queue()
    
    def subscribe(self) -> asyncio.Queue[TaskEvent]:
        """订阅事件流"""
        queue: asyncio.Queue[TaskEvent] = asyncio.Queue()
        self._subscribers.append(queue)
        return queue
    
    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """取消订阅"""
        if queue in self._subscribers:
            self._subscribers.remove(queue)
    
    async def emit(self, event: TaskEvent) -> None:
        """发布事件"""
        self._events.append(event)
        for queue in self._subscribers:
            await queue.put(event)
    
    async def emit_notification(self, message: str) -> None:
        """发布通知事件"""
        await self.emit(TaskEvent(
            type=EventType.NOTIFICATION,
            message=message,
        ))
    
    async def emit_llm_chunk(self, chunk: str) -> None:
        """发布 LLM 输出增量"""
        await self.emit(TaskEvent(
            type=EventType.LLM_CHUNK,
            message=chunk,
        ))
    
    async def emit_progress(self, message: str, progress: float = 0.0) -> None:
        """发布进度事件"""
        await self.emit(TaskEvent(
            type=EventType.TASK_PROGRESS,
            message=message,
            data={"progress": progress},
        ))

    async def emit_file_event(self, event_type: EventType, path: str) -> None:
        """发布文件事件"""
        await self.emit(TaskEvent(
            type=event_type,
            message=f"文件变更: {path}",
            data={"path": path},
        ))

    async def emit_deploy_event(self, event_type: EventType, url: str = "", message: str = "") -> None:
        """发布部署事件"""
        await self.emit(TaskEvent(
            type=event_type,
            message=message,
            data={"url": url} if url else {},
        ))
    
    async def wait_feedback(self, timeout: Optional[float] = None) -> Optional[str]:
        """等待用户反馈"""
        try:
            return await asyncio.wait_for(
                self._feedback_queue.get(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return None
    
    async def submit_feedback(self, feedback: str) -> None:
        """提交用户反馈"""
        await self._feedback_queue.put(feedback)
    
    def get_history(self) -> List[TaskEvent]:
        """获取事件历史"""
        return self._events.copy()
    
    def clear(self) -> None:
        """清空事件历史"""
        self._events.clear()


# 全局任务流实例
task_stream = TaskStream()
