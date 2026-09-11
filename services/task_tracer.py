"""任务追踪器 - 记录完整的任务执行时间线

提供 T+ 时间线格式的日志记录，自动保存 VFS 快照和提示词日志，
生成自包含的分析提示文档。
"""

import json
import logging
import os
import shutil
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from .logger import logger

if TYPE_CHECKING:
    from .vfs import ProjectContext


class TaskEvent(str, Enum):
    """任务事件类型枚举
    
    定义所有可追踪的事件类型，用于任务日志记录和分析。
    继承 str 使其在 JSON 序列化和字符串比较时更便捷。
    """
    
    # ==================== 任务生命周期 ====================
    
    TASK_START = "TASK_START"
    """任务开始 - 异步任务被创建时触发"""
    
    TASK_END = "TASK_END"
    """任务结束 - 任务完成（无论成功失败）时触发"""
    
    TASK_DONE = "TASK_DONE"
    """任务完成 - Developer Agent 调用 done 工具，标记任务成功"""
    
    TASK_ABORT = "TASK_ABORT"
    """任务中止 - 任务被主动中止"""
    
    # ==================== Agent 循环 ====================
    
    AGENT_START = "AGENT_START"
    """Agent 启动 - Developer Agent 开始执行"""
    
    LOOP_START = "LOOP_START"
    """循环开始 - Agent 主循环开始"""
    
    LOOP_SUCCESS = "LOOP_SUCCESS"
    """循环成功 - Agent 主循环正常完成"""
    
    LOOP_TIMEOUT = "LOOP_TIMEOUT"
    """循环超时 - 达到最大迭代次数"""
    
    ITERATION_START = "ITERATION_START"
    """迭代开始 - 新一轮迭代开始"""
    
    ITERATION_ERROR = "ITERATION_ERROR"
    """迭代错误 - 迭代过程中发生异常"""
    
    # ==================== LLM 调用 ====================
    
    LLM_CALL_START = "LLM_CALL_START"
    """LLM 调用开始 - 向 LLM 发送请求"""
    
    LLM_ERROR = "LLM_ERROR"
    """LLM 错误 - LLM 返回错误响应"""
    
    LLM_EXCEPTION = "LLM_EXCEPTION"
    """LLM 异常 - LLM 调用过程中发生异常"""
    
    # ==================== 编译（开发中） ====================
    
    COMPILE_START = "COMPILE_START"
    """编译开始 - 开发过程中的增量编译"""
    
    COMPILE_SUCCESS = "COMPILE_SUCCESS"
    """编译成功 - 增量编译通过"""
    
    COMPILE_FAILED = "COMPILE_FAILED"
    """编译失败 - 增量编译出错"""
    
    # ==================== 最终编译（部署前） ====================
    
    FINAL_COMPILE_START = "FINAL_COMPILE_START"
    """最终编译开始 - 生成部署产物前的编译"""
    
    FINAL_COMPILE_SUCCESS = "FINAL_COMPILE_SUCCESS"
    """最终编译成功 - 部署产物生成成功"""
    
    FINAL_COMPILE_FAILED = "FINAL_COMPILE_FAILED"
    """最终编译失败 - 部署产物生成失败"""
    
    # ==================== 部署 ====================
    
    DEPLOY_START = "DEPLOY_START"
    """部署开始 - 开始上传到 Worker"""
    
    DEPLOY_SUCCESS = "DEPLOY_SUCCESS"
    """部署成功 - 成功获取部署 URL"""
    
    DEPLOY_FAILED = "DEPLOY_FAILED"
    """部署失败 - 部署过程出错"""
    
    # ==================== 通知 ====================
    
    NOTIFICATION_SENT = "NOTIFICATION_SENT"
    """通知已发送 - 已通知主 Agent"""
    
    # ==================== 工具调用 ====================
    
    TOOL_CALL = "TOOL_CALL"
    """工具调用 - Developer Agent 调用工具"""
    
    # ==================== 通用/杂项 ====================
    
    INFO = "INFO"
    """通用信息 - 一般性说明或不重要的事件"""
    
    WARNING = "WARNING"
    """警告 - 需要注意但不影响执行的情况"""
    
    ERROR = "ERROR"
    """错误 - 发生错误"""
    
    # ==================== 编译器相关 ====================
    
    COMPILER_CRASH = "COMPILER_CRASH"
    """编译器崩溃 - 本地编译器进程崩溃"""
    
    COMPILER_JSON_ERR = "COMPILER_JSON_ERR"
    """编译器 JSON 错误 - 编译器输出格式错误"""
    
    COMPILER_EXCEPTION = "COMPILER_EXCEPTION"
    """编译器异常 - 编译过程发生异常"""
    
    # ==================== Node.js 运行时 ====================
    
    NODE_DOWNLOAD = "NODE_DOWNLOAD"
    """Node.js 下载 - 开始下载 Node.js"""
    
    NODE_EXTRACT = "NODE_EXTRACT"
    """Node.js 解压 - 解压 Node.js 包"""
    
    NODE_INSTALLED = "NODE_INSTALLED"
    """Node.js 安装完成 - Node.js 安装成功"""
    
    NODE_MISSING = "NODE_MISSING"
    """Node.js 缺失 - 系统未找到 Node.js"""
    
    NODE_UNKNOWN_SYS = "NODE_UNKNOWN_SYS"
    """未知系统 - 无法识别的操作系统"""
    
    NODE_UNKNOWN_ARCH = "NODE_UNKNOWN_ARCH"
    """未知架构 - 无法识别的 CPU 架构"""

    # ==================== Scoped Streaming Agent ====================
    
    SCOPE_DECLARED = "SCOPE_DECLARED"
    """Scope 声明 - Agent 调用 declare_scope 声明操作范围"""
    
    FILES_REQUESTED = "FILES_REQUESTED"
    """文件请求 - Agent 调用 request_files 请求读取文件"""
    
    FILE_WRITTEN = "FILE_WRITTEN"
    """文件写入 - 通过 Text 流写入文件"""
    
    AUTO_COMMITTED = "AUTO_COMMITTED"
    """自动提交 - Scope 中所有文件完成后自动提交"""
    
    FILE_COMPLETED = "FILE_COMPLETED"
    """文件完成（显式）- Agent 调用 complete_file 显式标记文件完成"""
    
    # ==================== 异步流处理 ====================
    
    UNITS_DISCARDED = "UNITS_DISCARDED"
    """单元丢弃 - 执行失败后丢弃的未执行单元"""
    
    PRODUCER_CANCELLED = "PRODUCER_CANCELLED"
    """生产者取消 - LLM 流被取消"""

    # ==================== 依赖解析 ====================

    DEPENDENCY_CHECK = "DEPENDENCY_CHECK"
    """依赖检查 - 开始检查外部依赖配置"""

    DEPENDENCY_RESOLVE_START = "DEPENDENCY_RESOLVE_START"
    """依赖解析开始 - 开始动态解析未知依赖"""

    DEPENDENCY_RESOLVE_SUCCESS = "DEPENDENCY_RESOLVE_SUCCESS"
    """依赖解析成功 - 成功解析所有未知依赖"""

    DEPENDENCY_RESOLVE_FAILED = "DEPENDENCY_RESOLVE_FAILED"
    """依赖解析失败 - 部分依赖无法解析"""


class TaskTracer:
    """任务追踪器

    记录从任务创建到交付的完整时间线，使用 T+HH:MM:SS.mmm 格式。
    自动保存 VFS 快照、提示词日志，并生成分析提示文档。
    
    通过 EVENT 类属性访问事件类型枚举：tracer.EVENT.TASK_START
    """
    
    # 事件类型枚举，通过 tracer.EVENT.xxx 访问
    EVENT = TaskEvent

    def __init__(
        self,
        chat_key: str,
        root_agent_id: str,
        task_description: str,
        plugin_data_dir: str,
        enabled: bool = True,
    ):
        """初始化任务追踪器

        Args:
            chat_key: 会话键
            root_agent_id: 根 Agent ID
            task_description: 任务描述
            plugin_data_dir: 插件数据目录路径
            enabled: 是否启用追踪（默认 True）
        """
        self.enabled = enabled
        
        # 任务 ID 格式：YYYYMMDD_HHMMSS_AgentID
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.task_id = f"{timestamp}_{root_agent_id}"
        self.chat_key = chat_key
        self.root_agent_id = root_agent_id
        self.task_description = task_description
        self.start_time = time.time()
        
        # 即使禁用，也初始化基本路径属性以防调用出错，但不创建目录
        self.task_dir = Path(plugin_data_dir) / "tasks" / f"task_{self.task_id}"
        self.log_file = self.task_dir / "01_task_trace.log"
        self.events: List[Dict[str, Any]] = []
        self.prompt_counter = 0

        if self.enabled:
            # 创建任务目录
            self.task_dir.mkdir(parents=True, exist_ok=True)
            (self.task_dir / "prompts").mkdir(exist_ok=True)
            (self.task_dir / "vfs_snapshot").mkdir(exist_ok=True)
            
            # 初始化日志文件
            self._init_log_file()
            logger.info(f"[TaskTracer] 任务追踪器已创建: {self.task_id}")
        else:
            logger.debug(f"[TaskTracer] 任务追踪器已禁用: {self.task_id}")

    def _init_log_file(self) -> None:
        """初始化日志文件头部"""
        header = f"""{"=" * 80}
任务追踪日志 - Task {self.task_id}
{"=" * 80}
任务 ID: {self.task_id}
根 Agent: {self.root_agent_id}
任务描述: {self.task_description}
创建时间: {datetime.fromtimestamp(self.start_time).strftime("%Y-%m-%d %H:%M:%S")}
{"=" * 80}

"""
        self.log_file.open("w", encoding="utf-8").write(header)
    
    def log_event(
        self,
        event_type: Union[TaskEvent, str],
        agent_id: str,
        message: str,
        level: str = "INFO",
        **metadata: Any,
    ) -> None:
        """记录事件 (单一入口)
        
        1. 记录到内部事件列表 (用于统计)
        2. 输出到控制台 logger (用户可见)
        3. 携带结构化数据传给 TraceLogHandler (生成详细日志文件)
        
        Args:
            event_type: 事件类型（推荐使用 TaskEvent 枚举，如 tracer.EVENT.TASK_START）
            agent_id: Agent ID
            message: 主要消息
            level: 日志级别 (INFO, WARNING, ERROR)
            **metadata: 额外数据
        """
        if not self.enabled:
            return

        elapsed = time.time() - self.start_time
        timestamp = self._format_t_plus(elapsed)
        
        # 1. 记录内部数据
        event = {
            "timestamp": timestamp,
            "elapsed_seconds": elapsed,
            "event_type": event_type,
            "agent_id": agent_id,
            "message": message,
            **metadata,
        }
        self.events.append(event)
        
        # 2. 直接写入日志文件 (不依赖 Handler)
        try:
            with self.log_file.open("a", encoding="utf-8") as f:
                # === 结构化事件格式 ===
                # T+00:00:01.123 [EVENT_TYPE] AgentID
                #   └─ Message
                #   └─ Key: Value
                f.write(f"\n{timestamp} [{event_type}] {agent_id}\n")
                f.write(f"  └─ {message}\n")
                
                for key, value in metadata.items():
                    val_str = str(value)
                    if len(val_str) > 200: 
                        val_str = val_str[:200] + "..."
                    f.write(f"  └─ {key}: {val_str}\n")
        except Exception as e:
            # 文件写入失败不应崩溃，但要记录错误
            logger.error(f"TaskTracer 文件写入失败: {e}")

        # 3. 调用标准 Logger (控制台输出)
        log_level = getattr(logging, level.upper(), logging.INFO)
        # 控制台只显示简洁信息
        logger.log(log_level, f"[{agent_id}] {message}")

    def _format_t_plus(self, seconds: float) -> str:
        """格式化为 T+HH:MM:SS.mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"T+{hours:02d}:{minutes:02d}:{secs:06.3f}"

    def register_prompt_log(
        self,
        _agent_id: str,
        _round_num: int,
        original_log_path: str,
    ) -> str:
        """注册并复制提示词日志

        Args:
            agent_id: Agent ID
            round_num: 轮次编号
            original_log_path: 原始日志文件路径

        Returns:
            新的日志文件路径
        """
        if not self.enabled:
            return ""

        self.prompt_counter += 1

        # 提取原始日志的时间戳
        # 原格式：20260113_114118_Web_0001.log
        original_filename = Path(original_log_path).name
        parts = original_filename.replace(".log", "").split("_")

        if len(parts) >= 2:
            date_part = parts[0]  # 20260113
            time_part = parts[1]  # 114118
        else:
            # 如果格式不符合预期，使用当前时间
            now = datetime.now()
            date_part = now.strftime("%Y%m%d")
            time_part = now.strftime("%H%M%S")

        # 新文件名：序号_日期_时间.log
        # 格式：001_20260113_114118.log
        new_filename = (
            f"{self.prompt_counter:03d}_"
            f"{date_part}_{time_part}.log"
        )

        new_path = self.task_dir / "prompts" / new_filename

        # 复制日志文件
        try:
            shutil.copy2(original_log_path, new_path)
            logger.debug(f"[TaskTracer] 已复制提示词日志: {new_filename}")
        except Exception as e:
            logger.error(f"[TaskTracer] 复制提示词日志失败: {e}")

        return str(new_path)

    def save_prompt(
        self,
        agent_id: str,
        messages: List[Any],  # OpenAIChatMessage
        response: Union[str, Dict[str, Any]],
    ) -> str:
        """保存 LLM 交互日志
        
        Args:
            agent_id: Agent ID
            messages: 发送给 LLM 的消息列表
            response: LLM 的响应内容 (str 或 Message Dict)
            
        Returns:
            保存的日志文件路径
        """
        if not self.enabled:
            return ""

        self.prompt_counter += 1
        
        # 文件名格式：序号_日期_时间.log
        now = datetime.now()
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        
        new_filename = (
            f"{self.prompt_counter:03d}_"
            f"{timestamp}.log"
        )
        
        log_path = self.task_dir / "prompts" / new_filename
        
        try:
            # 构建日志内容
            content = f"""{'=' * 80}
提示词日志 - {agent_id}
{'=' * 80}
时间: {now.strftime('%Y-%m-%d %H:%M:%S')}
Event: LLM Call {self.prompt_counter}
{'=' * 80}

"""
            # 记录请求消息
            for i, msg in enumerate(messages, 1):
                if isinstance(msg, dict):
                    role = msg.get("role", "unknown")
                    text = msg.get("content", "")
                    if "tool_calls" in msg:
                        tc = msg["tool_calls"]
                        tc_json = json.dumps(tc, ensure_ascii=False, indent=2)
                        text = f"{text}\n\n[Tool Calls]: {tc_json}" if text else f"[Tool Calls]: {tc_json}"
                    if not text:
                        text = str(msg)
                else:
                    role = getattr(msg, "role", "unknown")
                    text = getattr(msg, "content", str(msg))
                content += f"[{i}] Role: {role}\n"
                content += f"Content:\n{text}\n"
                content += f"{'-' * 80}\n\n"
            
            # 记录响应
            content += f"[{len(messages) + 1}] Role: assistant (RESPONSE)\n"
            
            resp_text = ""
            if isinstance(response, dict):
                resp_text = response.get("content", "") or ""
                if "tool_calls" in response:
                    tc = response["tool_calls"]
                    tc_json = json.dumps(tc, ensure_ascii=False, indent=2)
                    resp_text = f"{resp_text}\n\n[Tool Calls]: {tc_json}" if resp_text else f"[Tool Calls]: {tc_json}"
            else:
                resp_text = str(response)
                
            content += f"Content:\n{resp_text}\n"
            content += f"{'=' * 80}\n"

            log_path.write_text(content, encoding="utf-8")
            logger.debug(f"[TaskTracer] 已保存提示词日志: {new_filename}")
            
            return str(log_path)
            
        except Exception as e:
            logger.error(f"[TaskTracer] 保存提示词日志失败: {e}")
            return ""

    def save_vfs_snapshot(self, vfs_context: "ProjectContext") -> None:
        """保存 VFS 虚拟文件系统快照

        Args:
            vfs_context: VFS 项目上下文
        """
        if not self.enabled:
            return

        try:
            all_files = vfs_context.list_files()

            for file_path in all_files:
                content = vfs_context.read_file(file_path)
                if content:
                    full_path = self.task_dir / "vfs_snapshot" / file_path
                    full_path.parent.mkdir(parents=True, exist_ok=True)

                    with full_path.open("w", encoding="utf-8") as f:
                        f.write(content)

            logger.info(f"[TaskTracer] VFS 快照已保存: {len(all_files)} 个文件")
        except Exception as e:
            logger.error(f"[TaskTracer] 保存 VFS 快照失败: {e}")

    def finalize(
        self,
        final_status: str,
        error_summary: str = "",
    ) -> None:
        """任务结束时生成完整报告

        Args:
            final_status: 最终状态（SUCCESS, FAILED, FORCE_DELIVERED 等）
            error_summary: 错误摘要
        """
        if not self.enabled:
            return

        # 防止重复 finalize
        if hasattr(self, "_finalized") and self._finalized:
            logger.warning(
                f"[TaskTracer] 任务 {self.task_id} 已经 finalized，忽略重复调用",
            )
            return

        self._finalized = True

        # 记录任务结束事件
        self.log_event(
            TaskEvent.TASK_END,
            self.root_agent_id,
            f"任务结束: {final_status}",
            final_status=final_status,
            error_summary=error_summary,
        )

        # 保存元数据（00_ 前缀确保排在最前）
        self._save_metadata(final_status, error_summary)

        # 生成分析提示（99_ 前缀确保排在最后）
        self._generate_analysis_prompt(final_status, error_summary)

        # 写入日志统计
        self._write_log_footer()

        logger.info(f"[TaskTracer] 任务追踪已完成: {self.task_id}")

    def _save_metadata(self, final_status: str, error_summary: str) -> None:
        """保存任务元数据

        Args:
            final_status: 最终状态
            error_summary: 错误摘要
        """
        # 统计涉及的 Agent
        agents_involved = list({e["agent_id"] for e in self.events})

        # 统计各类事件
        event_types = {}
        for event in self.events:
            event_type = event["event_type"]
            event_types[event_type] = event_types.get(event_type, 0) + 1

        metadata = {
            "task_id": self.task_id,
            "chat_key": self.chat_key,
            "root_agent_id": self.root_agent_id,
            "task_description": self.task_description,
            "start_time": datetime.fromtimestamp(self.start_time).isoformat(),
            "end_time": datetime.now().isoformat(),
            "duration_seconds": time.time() - self.start_time,
            "final_status": final_status,
            "total_events": len(self.events),
            "agents_involved": agents_involved,
            "event_types": event_types,
            "error_summary": error_summary,
        }

        metadata_path = self.task_dir / "00_metadata.json"
        with metadata_path.open("w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        logger.debug(f"[TaskTracer] 元数据已保存: {metadata_path}")

    def _write_log_footer(self) -> None:
        """写入日志文件尾部统计信息"""
        duration = time.time() - self.start_time
        agents = list({e["agent_id"] for e in self.events})

        # 统计各类事件
        llm_calls = sum(1 for e in self.events if e["event_type"] == TaskEvent.LLM_CALL_START)
        reviews = sum(1 for e in self.events if e["event_type"] == "REVIEW_START")  # REVIEW_START 暂未使用

        footer = f"""
{"=" * 80}
任务统计
{"=" * 80}
总耗时: {int(duration // 60)} 分 {int(duration % 60)} 秒
Agent 数量: {len(agents)} ({", ".join(agents)})
LLM 调用: {llm_calls} 次
审查轮次: {reviews} 次
总事件数: {len(self.events)}

{"=" * 80}
相关文件
{"=" * 80}
元数据: 00_metadata.json
分析提示: 99_analysis_prompt.md
提示词日志: prompts/ 目录（{self.prompt_counter} 个文件）
VFS 快照: vfs_snapshot/ 目录

{"=" * 80}
"""
        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(footer)

    def _generate_diagnostic_section(self) -> str:
        """生成诊断检查部分
        
        检测关键事件是否缺失，返回 markdown 格式的诊断报告。
        
        Returns:
            诊断报告字符串（可能为空）
        """
        event_types_set = {e["event_type"] for e in self.events}
        
        # 根据不同的任务终态，检查应该存在的事件
        # 基础事件（所有任务都应该有）
        base_events = ["TASK_START", "TASK_END"]
        
        # 如果任务成功或编译失败，应该有编译相关事件
        compile_events = ["FINAL_COMPILE_START"]
        if "FINAL_COMPILE_SUCCESS" in event_types_set:
            compile_events.append("FINAL_COMPILE_SUCCESS")
        
        # 如果任务成功，应该有部署事件
        deploy_events = []
        if "DEPLOY_SUCCESS" in event_types_set or "DEPLOY_FAILED" in event_types_set:
            deploy_events = ["DEPLOY_START"]
        
        # 检查通知事件
        should_have_notification = (
            "TASK_END" in event_types_set  # 只要任务结束了，就应该有通知
        )
        
        missing_events = []
        warnings = []
        
        # 检查基础事件
        for evt in base_events:
            if evt not in event_types_set:
                missing_events.append(evt)
        
        # 检查编译事件（只在有编译相关事件时检查）
        if any(e.startswith("FINAL_COMPILE") for e in event_types_set):
            for evt in compile_events:
                if evt not in event_types_set:
                    missing_events.append(evt)
        
        # 检查部署事件
        for evt in deploy_events:
            if evt not in event_types_set:
                missing_events.append(evt)
        
        # 检查通知事件
        if should_have_notification and "NOTIFICATION_SENT" not in event_types_set:
            warnings.append("没有记录到 NOTIFICATION_SENT 事件，主 Agent 可能未收到通知")
        
        # 生成诊断报告
        if not missing_events and not warnings:
            return ""
        
        sections = []
        if missing_events:
            sections.append(f"> [!WARNING]\n> 缺失关键事件: {', '.join(missing_events)}")
        
        for warning in warnings:
            sections.append(f"> [!CAUTION]\n> {warning}")
        
        return "## 🔔 自动诊断\n\n" + "\n\n".join(sections) + "\n"

    def _generate_analysis_prompt(self, final_status: str, error_summary: str) -> None:
        """生成自包含的分析提示文档

        Args:
            final_status: 最终状态
            error_summary: 错误摘要
        """
        duration = time.time() - self.start_time
        agents = list({e["agent_id"] for e in self.events})

        # 提取关键时间线事件
        key_events = []
        for event in self.events:
            if event["event_type"] in [
                "TASK_START",
                "AGENT_CREATED",
                "REVIEW_START",
                "REVIEW_RESULT",
                "FINAL_COMPILE_START",
                "FINAL_COMPILE_SUCCESS",
                "FINAL_COMPILE_FAILED",
                "DEPLOY_START",
                "DEPLOY_SUCCESS",
                "DEPLOY_FAILED",
                "NOTIFICATION_SENT",
                "TASK_END",
            ]:
                key_events.append(
                    f"- {event['timestamp']} [{event['event_type']}] "
                    f"{event['agent_id']}: {event['message']}",
                )

        key_timeline = "\n".join(key_events[:30])  # 最多显示 30 个关键事件

        # 列出所有提示词日志
        prompt_logs = sorted((self.task_dir / "prompts").glob("*.log"))
        prompt_list = "\n".join(f"│   ├── {log.name}" for log in prompt_logs)

        # 生成分析提示文档
        analysis_prompt = f"""# 任务分析提示 - {self.task_id}

## 📋 任务概览

- **任务 ID**: `{self.task_id}`
- **创建时间**: `{datetime.fromtimestamp(self.start_time).strftime("%Y-%m-%d %H:%M:%S")}`
- **根 Agent**: `{self.root_agent_id}`
- **任务描述**: {self.task_description}
- **最终状态**: `{final_status}`
- **总耗时**: `{int(duration // 60)} 分 {int(duration % 60)} 秒`
- **涉及 Agent**: `{", ".join(agents)}`

## 📂 本任务的完整日志结构

```
tasks/task_{self.task_id}/
├── 00_metadata.json                          # 任务元数据
├── 01_task_trace.log                         # T+ 时间线日志
├── 99_analysis_prompt.md                     # 本文件
├── prompts/                                   # 提示词日志（按时间排序）
{prompt_list}
└── vfs_snapshot/                              # VFS 虚拟文件系统快照
    └── (Agent 生成的所有源码文件)
```

**注意**：所有文件名都经过设计，确保字母排序即时间顺序。

## 🔍 问题描述

{error_summary if error_summary else "任务正常完成，流程上无错误。"}

## 📊 关键时间线

```
{key_timeline}
```

{self._generate_diagnostic_section()}

## 📝 分析指引

请遵循以下原则分析本次任务的根本原因：

### 1. 从提示词环境出发
- Agent 实际看到的提示词是什么？（查看 `prompts/` 目录）
- 提示词是否足够准确详细且无歧义？
- Agent 的行为是否符合提示词的要求？
- **禁止推卸责任**：一切输出问题都是提示词问题，一切提示词的问题都是我们的实现问题

### 2. 追踪决策路径
- 查看 `01_task_trace.log` 了解完整的事件时间线
- Agent 在每一步的决策逻辑是什么？
- 为什么 Agent 没有执行某个操作？
- 是否有正确的操作路径？Agent 是否知道这一路径？
- 如果提供更多迭代，Agent 是否有机会修复这些问题？

### 3. 检查实际产物
- 查看 `vfs_snapshot/` 了解 Agent 实际生成的代码
- 对比提示词日志中的输出和最终产物
- 识别差异和问题

### 4. 识别系统性缺陷
- 这是个例问题还是系统性问题？
- 提示词设计是否存在缺陷？
- 是否缺少必要的检查机制？
- 能否通过在 新项目阶段/编译阶段/审查阶段/修订阶段 等流程中进行优化来修复？
- 找出最深层的根本原因，不要草率结论

## 🔧 插件源码位置

```
/home/miose/Projects/nekro-agent/data/nekro_agent/plugins/workdir/nekro-plugin-webapp/
├── prompts/              # 提示词定义
│   ├── architect.py      # Architect Agent 提示词
│   ├── engineer.py       # Engineer Agent 提示词
│   ├── reviewer.py       # Reviewer 提示词
│   └── common.py         # 通用提示词组件
├── services/             # 核心服务
│   ├── agent_runner.py   # Agent 运行逻辑（主循环、审查流程）
│   ├── vfs.py            # 虚拟文件系统（文件读写、权限）
│   ├── stream_parser.py  # 流式解析（解析 Agent 输出）
│   └── task_tracer.py    # 任务追踪器（本文件）
└── models.py             # 数据模型（WebDevAgent 等）
```

## 🎯 期望输出

请提供：

1. **根本原因分析**：
   - 基于提示词日志和实际代码的深度分析
   - 指出具体是哪个环节出了问题
   - 解释为什么会出现这个问题
   - **必须查看真实代码实现，不要基于假设**

2. **修复方案**：
   - 文件路径 + 具体修改内容（带行号）
   - 修改后的预期效果
   - 是否需要同步修改其他文件
   - **严格类型注解，最佳实践，优雅实现**

3. **验证方法**：
   - 如何测试修复是否有效
   - 需要运行什么测试用例

## 🚨 关键原则

1. **禁止推卸责任**：问题出在提示词设计，不是 Agent 智能问题
2. **禁止草率结论**：深入分析，找出最深层的根本原因
3. **禁止假设分析**：必须查看真实源码，基于证据分析
4. **禁止静默错误**：所有错误必须有用户反馈
5. **禁止类型逃避**：严格类型注解，不使用 any/unknown

---

**重要提醒**：
- 请直接查看源码实现和日志文件，不要基于假设进行分析
- 所有日志文件都在本任务目录下，按字母排序即时间顺序
- VFS 快照包含了 Agent 实际生成的所有代码
- 这是有价值的日志信息，请充分利用，深入分析给出有深度的解析说明
- 不要直接修改代码进行修复，而是先提供修复方案供我确认
- 始终使用中文进行回答
"""

        analysis_path = self.task_dir / "99_analysis_prompt.md"
        with analysis_path.open("w", encoding="utf-8") as f:
            f.write(analysis_prompt)

        logger.debug(f"[TaskTracer] 分析提示已生成: {analysis_path}")

    def update_summary(
        self,
        new_status: str,
        additional_events: List[str],
        error_summary: str = "",
    ) -> None:
        """更新 99_analysis_prompt.md 的状态和事件时间线
        
        在 TASK_CONTINUE 后的任何终态（成功/失败/强制交付）调用。
        
        Args:
            new_status: 新的最终状态
            additional_events: 需要追加的事件描述列表
            error_summary: 错误摘要
        """
        import re
        
        analysis_path = self.task_dir / "99_analysis_prompt.md"
        if not analysis_path.exists():
            logger.warning(f"[TaskTracer] 分析文件不存在，无法更新: {analysis_path}")
            return
        
        try:
            content = analysis_path.read_text(encoding="utf-8")
            
            # 更新最终状态
            content = re.sub(
                r"\*\*最终状态\*\*: `[^`]+`",
                f"**最终状态**: `{new_status}`",
                content,
            )
            
            # 更新总耗时
            elapsed = time.time() - self.start_time
            minutes = int(elapsed // 60)
            seconds = int(elapsed % 60)
            content = re.sub(
                r"\*\*总耗时\*\*: `[^`]+`",
                f"**总耗时**: `{minutes} 分 {seconds} 秒`",
                content,
            )
            
            # 更新涉及 Agent 列表
            agents = list({e["agent_id"] for e in self.events})
            content = re.sub(
                r"\*\*涉及 Agent\*\*: `[^`]+`",
                f"**涉及 Agent**: `{', '.join(agents)}`",
                content,
            )
            
            # 更新问题描述
            if error_summary:
                content = re.sub(
                    r"## 🔍 问题描述\n\n[^\n]+",
                    f"## 🔍 问题描述\n\n{error_summary}",
                    content,
                )
            
            # 追加新的时间线事件
            if additional_events:
                timeline_marker = "```\n\n## 📝 分析指引"
                new_events = "\n".join(additional_events)
                insert_content = f"\n# === 用户反馈后的事件 ===\n{new_events}\n"
                content = content.replace(timeline_marker, insert_content + timeline_marker)
            
            analysis_path.write_text(content, encoding="utf-8")
            logger.info(f"[TaskTracer] 分析文件已更新: {new_status}")
            
        except Exception as e:
            logger.error(f"[TaskTracer] 更新分析文件失败: {e}")

    def elapsed(self) -> str:
        """获取当前格式化的 T+ 时间戳"""
        return self._format_t_plus(time.time() - self.start_time)
