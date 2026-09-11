"""执行上下文

定义 Developer Agent 的执行上下文，包含项目状态和追踪器。
支持 Scoped Streaming Agent 架构的状态机和 Checkpoint。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..services.task_tracer import TaskTracer
    from ..services.vfs import ProjectContext


# ============== 状态机枚举 ==============


class AgentExecutionState(str, Enum):
    """Agent 执行状态机

    状态流转:
    IDLE -> SCOPE_DECLARED -> GENERATING -> COMMITTED
                           -> WAITING_FOR_FILES -> GENERATING
                           -> FAILED
    """

    IDLE = "idle"
    """初始状态，等待 declare_scope"""

    SCOPE_DECLARED = "scope_declared"
    """已声明 scope，准备接收 text 内容"""

    GENERATING = "generating"
    """正在生成 text（文件内容）"""

    WAITING_FOR_FILES = "waiting_for_files"
    """等待文件读取（同步屏障）"""

    APPLYING = "applying"
    """正在应用变更"""

    FAILED = "failed"
    """执行失败"""

    COMMITTED = "committed"
    """变更已提交（所有文件完成）"""


class ScopeOperation(str, Enum):
    """Scope 操作类型"""

    CREATE = "create"
    """创建新文件"""

    MODIFY = "modify"
    """修改现有文件"""

    READ = "read"
    """只读模式"""


class ScopeFormat(str, Enum):
    """Scope 输出格式"""

    FULL_TEXT = "full_text"
    """完整文件内容"""

    UNIFIED_DIFF = "unified_diff"
    """Unified diff 格式"""


# ============== 数据结构 ==============


@dataclass
class DeclaredScope:
    """已声明的操作范围"""

    operation: ScopeOperation
    """操作类型"""

    files: List[str]
    """涉及的文件列表"""

    format: ScopeFormat = ScopeFormat.FULL_TEXT
    """输出格式"""

    streaming: bool = True
    """是否流式输出"""


class ProductSpec(BaseModel):
    """产品规格（Designer 产出）"""

    # 产品概述
    name: str = Field(description="产品名称")
    description: str = Field(description="产品描述")

    # 类型契约
    type_contracts: str = Field(default="", description="TypeScript 类型定义")

    # 文件结构
    file_structure: List[Dict[str, str]] = Field(
        default_factory=list,
        description="文件结构列表，每项包含 path, purpose, owner (developer/content)",
    )

    # 内容规格（用于 Content Writer）
    content_spec: Optional[Dict[str, Any]] = Field(
        default=None,
        description="内容生成规格",
    )

    # 设计要点
    design_notes: str = Field(default="", description="UI/UX 设计要点")


class AgentState(BaseModel):
    """Agent 状态

    包含迭代控制和执行状态机。
    """

    # ===== 迭代控制 =====
    iteration: int = Field(default=0, description="当前迭代次数")
    max_iterations: int = Field(default=20, description="最大迭代次数")
    completed: bool = Field(default=False, description="是否完成")
    last_error: Optional[str] = Field(default=None, description="最后一次错误")
    compile_success: bool = Field(default=False, description="编译是否成功")

    # ===== 执行状态机 =====
    execution_state: AgentExecutionState = Field(
        default=AgentExecutionState.IDLE,
        description="当前执行状态",
    )
    current_scope: Optional[DeclaredScope] = Field(
        default=None,
        description="当前声明的 scope",
    )
    generating_file: Optional[str] = Field(
        default=None,
        description="当前正在生成的文件路径",
    )
    completed_files: List[str] = Field(
        default_factory=list,
        description="已完成的文件列表",
    )
    pending_files: List[str] = Field(
        default_factory=list,
        description="待处理的文件列表",
    )
    diff_fail_counts: Dict[str, int] = Field(
        default_factory=dict,
        description="每个文件的 DIFF 失败次数追踪",
    )

    class Config:
        arbitrary_types_allowed = True

    def reset_scope(self) -> None:
        """重置 scope 相关状态"""
        self.execution_state = AgentExecutionState.IDLE
        self.current_scope = None
        self.generating_file = None
        self.completed_files = []
        self.pending_files = []


@dataclass
class ToolContext:
    """工具执行上下文

    提供给每个工具调用的上下文信息。
    """

    # 会话信息
    chat_key: str
    task_id: str
    project: "ProjectContext"
    state: AgentState
    tracer: "TaskTracer"  # 必须存在 (可内部禁用)
    spec: Optional[ProductSpec] = None

    def log_tool_call(self, tool_name: str, args: Dict[str, Any], result: str) -> None:
        """记录工具调用"""
        self.tracer.log_event(
            event_type=self.tracer.EVENT.TOOL_CALL,
            agent_id=self.task_id,
            message=f"调用工具: {tool_name}",
            tool_name=tool_name,
            tool_args=args,
            result=result[:200] + "..." if len(result) > 200 else result,
        )

