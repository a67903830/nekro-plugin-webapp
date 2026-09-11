"""命令流解析器 (Command Stream Parser)

统一解析 LLM 纯文本输出，提取:
- 块工具: <<<BLOCK_TYPE: arg>>> ... <<<END_BLOCK_TYPE>>>
- 行工具: @@TOOL_NAME key="value" key2="value2"

块工具通过 tools/block_tools.py 中的装饰器注册。
Text-to-Tool Bridge 架构的核心组件。
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .logger import logger


class CommandType:
    """命令类型
    
    块工具类型由 block_tools 动态注册。
    """
    
    TOOL_CALL = "tool_call"  # 行工具调用
    BLOCK = "block"  # 块工具（通用类型，具体由 block_name 区分）


@dataclass
class ParsedCommand:
    """解析出的命令

    表示从文本流中解析出的一个可执行命令。
    """

    type: str  # CommandType.TOOL_CALL 或 CommandType.BLOCK

    # 块工具通用字段
    block_name: Optional[str] = None  # 块类型名（如 "FILE", "DIFF"）
    block_arg: Optional[str] = None  # 主参数（通常是文件路径）
    block_content: Optional[str] = None  # 块内容
    block_complete: bool = False  # 块是否完整结束

    # 行工具字段
    tool_name: Optional[str] = None
    tool_args: Dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        if self.type == CommandType.BLOCK:
            status = "完整" if self.block_complete else "未完成"
            return f"BLOCK({self.block_name}, {self.block_arg}, {status})"
        return f"TOOL({self.tool_name}, {self.tool_args})"


@dataclass
class CommandStreamParser:
    """流式命令解析器

    职责：
    1. 累积流式文本
    2. 检测块工具边界 (<<<TYPE: arg>>> ... <<<END_TYPE>>>)
    3. 检测行工具命令 (@@TOOL_NAME)
    4. 返回 ParsedCommand 供执行

    块工具通过 block_tools.py 动态注册。
    """

    # 行工具命令: @@TOOL_NAME key="value"
    TOOL_CMD_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"^@@(\w+)(?:\s+(.+))?$", re.MULTILINE),
        repr=False,
    )
    ARG_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')'),
        repr=False,
    )

    buffer: str = ""
    """累积缓冲区"""

    current_block_type: Optional[str] = None
    """当前正在解析的块类型 (FILE/DIFF 等)"""

    current_block_arg: Optional[str] = None
    """当前块的主参数 (通常是文件路径)"""

    current_content: str = ""
    """当前块的累积内容"""
    
    # 缓存的正则（在首次使用时初始化）
    _block_start_pattern: Optional[re.Pattern[str]] = field(default=None, repr=False)

    def _get_block_start_pattern(self) -> re.Pattern[str]:
        """获取块开始正则（动态生成）"""
        if self._block_start_pattern is None:
            from ..tools.block_tools import build_block_start_pattern
            self._block_start_pattern = build_block_start_pattern()
        return self._block_start_pattern

    def _get_end_pattern(self) -> Optional[re.Pattern[str]]:
        """获取当前块类型的结束正则"""
        if self.current_block_type is None:
            return None
        from ..tools.block_tools import get_block_end_pattern
        return get_block_end_pattern(self.current_block_type)

    def feed(self, chunk: str) -> List[ParsedCommand]:
        """处理增量文本

        Args:
            chunk: 来自 LLM 流的文本增量

        Returns:
            本次增量中完成的 ParsedCommand 列表
        """
        self.buffer += chunk
        commands: List[ParsedCommand] = []

        while True:

            # 如果不在块内，检测工具命令和块开始
            if self.current_block_type is None:
                # 1. 检测行工具命令 (@@TOOL)
                tool_match = self.TOOL_CMD_PATTERN.search(self.buffer)
                if tool_match:
                    cmd_end = tool_match.end()
                    if cmd_end < len(self.buffer) or "\n" in self.buffer[tool_match.start() :]:
                        tool_name = tool_match.group(1).lower()

                        # 别名映射
                        if tool_name == "read":
                            tool_name = "read_files"

                        args_str = tool_match.group(2) or ""
                        raw_args = self.ARG_PATTERN.findall(args_str)
                        args = {k: v1 or v2 for k, v1, v2 in raw_args}
                        logger.debug(f"[CommandParser] Raw Args Str: {args_str} -> Parsed: {args}")

                        commands.append(
                            ParsedCommand(
                                type=CommandType.TOOL_CALL,
                                tool_name=tool_name,
                                tool_args=args,
                            ),
                        )
                        logger.debug(f"[CommandParser] 解析到行工具: {tool_name}({args})")

                        self.buffer = self.buffer[cmd_end:].lstrip("\n")
                        continue

                # 2. 检测块开始 (<<<TYPE: arg>>>)
                block_pattern = self._get_block_start_pattern()
                block_match = block_pattern.search(self.buffer)
                if block_match:
                    self.current_block_type = block_match.group(1).upper()
                    self.current_block_arg = block_match.group(2).strip()
                    self.current_content = ""
                    self.buffer = self.buffer[block_match.end() :]
                    logger.debug(
                        f"[CommandParser] 块开始: {self.current_block_type}({self.current_block_arg})",
                    )
                    continue

                # 没有匹配到任何模式，检查是否需要保留部分 buffer
                if "<<<" in self.buffer:
                    idx = self.buffer.rfind("<<<")
                    if idx > 0:
                        self.buffer = self.buffer[idx:]
                        continue
                elif "@@" in self.buffer:
                    idx = self.buffer.rfind("@@")
                    if idx > 0:
                        self.buffer = self.buffer[idx:]
                        continue
                break

            # 正在块内，寻找结束标记
            end_pattern = self._get_end_pattern()
            if end_pattern:
                end_match = end_pattern.search(self.buffer)
                if end_match:
                    # 找到结束标记
                    self.current_content += self.buffer[: end_match.start()]
                    cleaned_content = self._clean_content(self.current_content)

                    commands.append(
                        ParsedCommand(
                            type=CommandType.BLOCK,
                            block_name=self.current_block_type,
                            block_arg=self.current_block_arg,
                            block_content=cleaned_content,
                            block_complete=True,
                        ),
                    )
                    logger.debug(
                        f"[CommandParser] 块完成: {self.current_block_type}({self.current_block_arg}) "
                        f"({len(cleaned_content)} 字符)",
                    )

                    # 重置状态
                    self.buffer = self.buffer[end_match.end() :]
                    self.current_block_type = None
                    self.current_block_arg = None
                    self.current_content = ""
                    continue

            # 未找到结束标记，继续累积
            if "<<<" in self.buffer:
                idx = self.buffer.rfind("<<<")
                self.current_content += self.buffer[:idx]
                self.buffer = self.buffer[idx:]
            else:
                self.current_content += self.buffer
                self.buffer = ""
            break

        return commands

    def flush(self) -> List[ParsedCommand]:
        """流结束时刷新缓冲区

        1. 尝试解析残留的工具命令
        2. 处理未完成的块

        Returns:
            遗留的 ParsedCommand 列表
        """
        commands: List[ParsedCommand] = []

        # 1. 尝试解析残留的行工具命令
        if self.buffer.strip():
            tool_match = self.TOOL_CMD_PATTERN.search(self.buffer)
            if tool_match:
                tool_name = tool_match.group(1).lower()
                if tool_name == "read":
                    tool_name = "read_files"

                args_str = tool_match.group(2) or ""
                raw_args = self.ARG_PATTERN.findall(args_str)
                args = {k: v1 or v2 for k, v1, v2 in raw_args}

                commands.append(
                    ParsedCommand(
                        type=CommandType.TOOL_CALL,
                        tool_name=tool_name,
                        tool_args=args,
                    ),
                )
                logger.debug(f"[CommandParser] Flush 解析到行工具: {tool_name}({args})")
                self.buffer = ""

        # 2. 处理未完成的块
        if self.current_block_type and self.current_content:
            commands.append(
                ParsedCommand(
                    type=CommandType.BLOCK,
                    block_name=self.current_block_type,
                    block_arg=self.current_block_arg,
                    block_content=self._clean_content(self.current_content + self.buffer),
                    block_complete=False,
                ),
            )
            logger.warning(
                f"[CommandParser] 块未完成: {self.current_block_type}({self.current_block_arg})",
            )
            self.current_block_type = None
            self.current_block_arg = None
            self.current_content = ""
            self.buffer = ""

        return commands

    def reset(self) -> None:
        """重置解析器状态"""
        self.buffer = ""
        self.current_block_type = None
        self.current_block_arg = None
        self.current_content = ""

    def _clean_content(self, content: str) -> str:
        """清理内容

        移除开头和结尾的空行，但保留代码缩进。
        """
        lines = content.split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)

    @property
    def is_parsing_block(self) -> bool:
        """是否正在解析块"""
        return self.current_block_type is not None

    @property
    def current_parsing_file(self) -> Optional[str]:
        """当前正在解析的文件路径（兼容旧 API）"""
        return self.current_block_arg
