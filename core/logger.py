"""日志辅助模块

通过运行时适配器获取 logger：
- 插件模式：使用 nekro_agent.core.logger
- CLI 模式：使用独立的 loguru
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
