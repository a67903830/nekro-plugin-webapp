"""提示词模块

包含 WebDev Agent 的系统提示词。
"""

from .developer import build_system_prompt as build_developer_prompt

__all__ = [
    "build_developer_prompt",
]
