"""服务层日志辅助模块

直接使用运行时适配器获取 logger，避免循环导入。
"""

from typing import Any


def get_logger() -> Any:
    """获取当前运行时的 logger"""
    try:
        from ..runtime import get_adapter
        adapter = get_adapter()
        return adapter.get_logger()
    except (ImportError, RuntimeError):
        from loguru import logger as _logger
        return _logger


class _LoggerProxy:
    """Logger 代理，延迟获取实际的 logger"""
    
    def __getattr__(self, name: str) -> Any:
        return getattr(get_logger(), name)


logger: Any = _LoggerProxy()

__all__ = ["get_logger", "logger"]
