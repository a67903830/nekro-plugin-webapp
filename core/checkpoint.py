"""Checkpoint 系统

提供执行状态的保存与恢复，支持中断后继续任务。

Checkpoint 触发点：
1. declare_scope 后
2. 每个 FILE block 完成后
3. request_files 前
4. 每次 tool 执行后
5. 异常捕获时
"""

import contextlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .logger import logger


@dataclass
class ExecutionCheckpoint:
    """执行检查点

    保存任务执行的完整状态，用于中断恢复。
    """

    task_id: str
    """任务 ID"""

    state: str
    """AgentExecutionState.value"""

    scope: Optional[Dict[str, Any]]
    """DeclaredScope 序列化"""

    completed_files: List[str]
    """已完成的文件列表"""

    pending_files: List[str]
    """待处理的文件列表"""

    vfs_snapshot: Dict[str, str]
    """VFS 文件快照 (path -> content)"""

    llm_messages_count: int
    """LLM 消息历史数量"""

    iteration: int
    """当前迭代次数"""

    timestamp: str
    """创建时间 ISO 格式"""

    def save(self, checkpoint_dir: Path) -> Path:
        """保存 checkpoint 到文件

        Args:
            checkpoint_dir: Checkpoint 存储目录

        Returns:
            保存的文件路径
        """
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = checkpoint_dir / f"checkpoint_{ts}.json"
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2))
        logger.debug(f"[Checkpoint] 保存检查点: {path}")
        return path

    @classmethod
    def load_latest(cls, checkpoint_dir: Path) -> Optional["ExecutionCheckpoint"]:
        """加载最新的 checkpoint

        Args:
            checkpoint_dir: Checkpoint 存储目录

        Returns:
            最新的 checkpoint，如果不存在则返回 None
        """
        if not checkpoint_dir.exists():
            return None

        files = sorted(checkpoint_dir.glob("checkpoint_*.json"), reverse=True)
        if not files:
            return None

        try:
            data = json.loads(files[0].read_text())
            logger.debug(f"[Checkpoint] 加载检查点: {files[0]}")
            return cls(**data)
        except Exception as e:
            logger.warning(f"[Checkpoint] 加载失败: {e}")
            return None

    @classmethod
    def list_all(cls, checkpoint_dir: Path) -> List["ExecutionCheckpoint"]:
        """列出所有 checkpoint

        Args:
            checkpoint_dir: Checkpoint 存储目录

        Returns:
            按时间倒序排列的 checkpoint 列表
        """
        if not checkpoint_dir.exists():
            return []

        checkpoints: List[ExecutionCheckpoint] = []
        for f in sorted(checkpoint_dir.glob("checkpoint_*.json"), reverse=True):
            try:
                data = json.loads(f.read_text())
                checkpoints.append(cls(**data))
            except Exception:
                continue

        return checkpoints


class CheckpointManager:
    """Checkpoint 管理器

    封装 checkpoint 的创建、保存、加载和清理。
    """

    def __init__(self, base_dir: Path, task_id: str, max_checkpoints: int = 10):
        """初始化

        Args:
            base_dir: 基础存储目录
            task_id: 任务 ID
            max_checkpoints: 最大保留的 checkpoint 数量
        """
        self.checkpoint_dir = base_dir / "checkpoints" / task_id
        self.task_id = task_id
        self.max_checkpoints = max_checkpoints

    def create(
        self,
        state: str,
        scope: Optional[Dict[str, Any]],
        completed_files: List[str],
        pending_files: List[str],
        vfs_snapshot: Dict[str, str],
        llm_messages_count: int,
        iteration: int,
    ) -> ExecutionCheckpoint:
        """创建 checkpoint

        Args:
            state: 当前执行状态
            scope: 当前 scope（可选）
            completed_files: 已完成文件
            pending_files: 待处理文件
            vfs_snapshot: VFS 快照
            llm_messages_count: 消息数量
            iteration: 迭代次数

        Returns:
            创建的 checkpoint
        """
        return ExecutionCheckpoint(
            task_id=self.task_id,
            state=state,
            scope=scope,
            completed_files=completed_files.copy(),
            pending_files=pending_files.copy(),
            vfs_snapshot=vfs_snapshot.copy(),
            llm_messages_count=llm_messages_count,
            iteration=iteration,
            timestamp=datetime.now().isoformat(),
        )

    def save(self, checkpoint: ExecutionCheckpoint) -> Path:
        """保存 checkpoint 并清理旧的

        Args:
            checkpoint: 要保存的 checkpoint

        Returns:
            保存的文件路径
        """
        path = checkpoint.save(self.checkpoint_dir)
        self._cleanup_old()
        return path

    def load_latest(self) -> Optional[ExecutionCheckpoint]:
        """加载最新的 checkpoint"""
        return ExecutionCheckpoint.load_latest(self.checkpoint_dir)

    def _cleanup_old(self) -> None:
        """清理旧的 checkpoint，只保留最新的 N 个"""
        if not self.checkpoint_dir.exists():
            return

        files = sorted(self.checkpoint_dir.glob("checkpoint_*.json"), reverse=True)
        for old_file in files[self.max_checkpoints :]:
            with contextlib.suppress(Exception):
                old_file.unlink()
                logger.debug(f"[Checkpoint] 清理旧检查点: {old_file}")

    def clear_all(self) -> None:
        """清除所有 checkpoint"""
        if self.checkpoint_dir.exists():
            for f in self.checkpoint_dir.glob("checkpoint_*.json"):
                with contextlib.suppress(Exception):
                    f.unlink()
            logger.debug(f"[Checkpoint] 已清除所有检查点: {self.checkpoint_dir}")

