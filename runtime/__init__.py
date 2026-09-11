"""运行时适配层

提供统一的运行时抽象，支持 nekro-agent 和独立 CLI 两种模式。
通过适配器模式解耦核心引擎与宿主环境。
"""

from .adapter import RuntimeAdapter, get_adapter, set_adapter

__all__ = ["RuntimeAdapter", "get_adapter", "set_adapter"]
