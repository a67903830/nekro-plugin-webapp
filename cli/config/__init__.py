"""配置系统

提供独立 CLI 模式的配置管理。
"""

from .settings import CONFIG_TEMPLATE, WebAppConfig, get_config_dir

__all__ = ["CONFIG_TEMPLATE", "WebAppConfig", "get_config_dir"]
