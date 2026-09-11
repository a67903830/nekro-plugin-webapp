"""Text 流解析器

解析 LLM 输出的 text，提取 FILE block。
支持流式处理，边接收边解析。

FILE block 格式:
    <<<FILE: src/App.tsx>>>
    import React from 'react'
    // ...
    <<<END_FILE>>>
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FileBlock:
    """文件块

    表示从 text 流中解析出的一个文件内容块。
    """

    path: str
    """文件路径"""

    content: str
    """文件内容"""

    complete: bool = False
    """是否完整（已检测到 END_FILE）"""


@dataclass
class TextStreamParser:
    """流式 Text 解析器

    职责：
    1. 累积流式文本
    2. 检测 FILE block 边界
    3. 返回完整的文件块供写入

    Example:
        parser = TextStreamParser()

        async for chunk in llm_stream:
            completed = parser.feed(chunk.content)
            for fb in completed:
                vfs.write_file(fb.path, fb.content)

        # 流结束，检查未完成的块
        incomplete = parser.flush()
        if incomplete:
            logger.warning(f"文件未完成: {incomplete.path}")
    """

    FILE_START_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"<<<FILE:\s*([^>]+)>>>"),
        repr=False,
    )
    """文件开始标记正则"""

    FILE_END_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"<<<END_FILE>>>"),
        repr=False,
    )
    """文件结束标记正则"""

    buffer: str = ""
    """累积缓冲区"""

    current_file: Optional[str] = None
    """当前正在解析的文件路径"""

    current_content: str = ""
    """当前文件的累积内容"""

    completed_blocks: List[FileBlock] = field(default_factory=list)
    """已完成的文件块列表"""

    def feed(self, chunk: str) -> List[FileBlock]:
        """处理增量文本

        Args:
            chunk: 来自 LLM 流的文本增量

        Returns:
            本次增量中完成的 FileBlock 列表
        """
        self.buffer += chunk
        completed: List[FileBlock] = []

        while True:
            if self.current_file is None:
                # 寻找文件开始标记
                match = self.FILE_START_PATTERN.search(self.buffer)
                if match:
                    self.current_file = match.group(1).strip()
                    self.buffer = self.buffer[match.end() :]
                    self.current_content = ""
                else:
                    # 保留可能被截断的标记
                    # 例如 "<<<FILE:" 可能在下一个 chunk 中完成
                    if "<<<" in self.buffer:
                        idx = self.buffer.rfind("<<<")
                        # 只保留最后一个 <<< 之后的内容
                        if idx > 0:
                            self.buffer = self.buffer[idx:]
                    else:
                        self.buffer = ""
                    break
            else:
                # 寻找文件结束标记
                match = self.FILE_END_PATTERN.search(self.buffer)
                if match:
                    # 找到结束标记
                    self.current_content += self.buffer[: match.start()]
                    block = FileBlock(
                        path=self.current_file,
                        content=self._clean_content(self.current_content),
                        complete=True,
                    )
                    completed.append(block)
                    self.completed_blocks.append(block)

                    # 重置状态，继续处理剩余内容
                    self.buffer = self.buffer[match.end() :]
                    self.current_file = None
                    self.current_content = ""
                else:
                    # 未找到结束标记，继续累积
                    # 保留可能被截断的 <<< 标记
                    if "<<<" in self.buffer:
                        idx = self.buffer.rfind("<<<")
                        self.current_content += self.buffer[:idx]
                        self.buffer = self.buffer[idx:]
                    else:
                        self.current_content += self.buffer
                        self.buffer = ""
                    break

        return completed

    def flush(self) -> Optional[FileBlock]:
        """流结束时刷新未完成的块

        Returns:
            如果有未完成的文件，返回它（标记为 complete=False）
        """
        if self.current_file and self.current_content:
            block = FileBlock(
                path=self.current_file,
                content=self._clean_content(self.current_content + self.buffer),
                complete=False,  # 标记为不完整
            )
            # 重置状态
            self.current_file = None
            self.current_content = ""
            self.buffer = ""
            return block
        return None

    def reset(self) -> None:
        """重置解析器状态"""
        self.buffer = ""
        self.current_file = None
        self.current_content = ""
        self.completed_blocks.clear()

    def _clean_content(self, content: str) -> str:
        """清理内容

        移除开头和结尾的空行，但保留代码缩进。
        """
        # 移除开头的空行
        lines = content.split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        # 移除结尾的空行
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)

    @property
    def is_parsing(self) -> bool:
        """是否正在解析文件"""
        return self.current_file is not None

    @property
    def current_parsing_file(self) -> Optional[str]:
        """当前正在解析的文件路径"""
        return self.current_file
