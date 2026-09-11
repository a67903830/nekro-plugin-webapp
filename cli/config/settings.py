"""配置设置

提供 pydantic-settings 类型安全的配置管理。
配置持久化在用户目录下：~/.config/nekro-webapp/config.toml
"""

import os
from pathlib import Path
from typing import Optional

import toml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_config_dir() -> Path:
    """获取配置目录
    
    优先使用 XDG_CONFIG_HOME，否则使用 ~/.config
    
    Returns:
        配置目录路径
    """
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config) if xdg_config else Path.home() / ".config"

    config_dir = base / "nekro-webapp"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


class WebAppConfig(BaseSettings):
    """WebApp 配置
    
    配置优先级：环境变量 > 配置文件 > 默认值
    """

    model_config = SettingsConfigDict(
        env_prefix="NEKRO_WEBAPP_",
        env_file=".env",
    )

    # ==================== LLM 配置 ====================
    
    openai_api_key: str = Field(
        default="",
        description="OpenAI API Key",
    )
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        description="OpenAI API Base URL（兼容其他服务）",
    )
    model: str = Field(
        default="gpt-4",
        description="默认模型",
    )
    temperature: float = Field(
        default=0.7,
        description="温度参数",
    )

    # ==================== Worker 配置 ====================
    
    worker_url: Optional[str] = Field(
        default=None,
        description="Cloudflare Worker URL",
    )
    access_key: Optional[str] = Field(
        default=None,
        description="Worker 访问密钥",
    )

    # ==================== 任务配置 ====================
    
    max_iterations: int = Field(
        default=20,
        description="最大迭代次数",
    )
    task_timeout_minutes: int = Field(
        default=15,
        description="任务超时（分钟）",
    )
    max_concurrent_tasks: int = Field(
        default=3,
        description="最大并行任务数",
    )

    # ==================== 输出配置 ====================
    
    output_dir: Path = Field(
        default=Path("./dist"),
        description="输出目录",
    )

    # ==================== 网络配置 ====================
    
    proxy_url: Optional[str] = Field(
        default=None,
        description="HTTP 代理 URL",
    )

    # ==================== 语言配置 ====================
    
    language: str = Field(
        default="zh-cn",
        description="用户语言",
    )

    @classmethod
    def get_config_path(cls) -> Path:
        """获取配置文件路径"""
        return get_config_dir() / "config.toml"

    @classmethod
    def load(cls) -> "WebAppConfig":
        """加载配置
        
        优先级：环境变量 > 配置文件 > 默认值
        
        Returns:
            配置对象
        """
        config_path = cls.get_config_path()

        if config_path.exists():
            try:
                data = toml.load(config_path)
                # 展平嵌套结构并映射键名
                flat_data = {}
                
                # 键名映射（TOML 键 -> 属性名）
                key_mapping = {
                    "url": "worker_url",  # [worker] 下的 url -> worker_url
                }
                
                for section, values in data.items():
                    if isinstance(values, dict):
                        for k, v in values.items():
                            # 应用键名映射
                            mapped_key = key_mapping.get(k, k)
                            flat_data[mapped_key] = v
                    else:
                        flat_data[section] = values
                        
                return cls(**flat_data)
            except Exception:
                pass

        return cls()

    def save(self) -> None:
        """保存配置到文件"""
        config_path = self.get_config_path()

        data = {
            "llm": {
                "openai_api_key": self.openai_api_key,
                "openai_base_url": self.openai_base_url,
                "model": self.model,
                "temperature": self.temperature,
            },
            "worker": {
                "url": self.worker_url or "",
                "access_key": self.access_key or "",
            },
            "task": {
                "max_iterations": self.max_iterations,
                "task_timeout_minutes": self.task_timeout_minutes,
                "max_concurrent_tasks": self.max_concurrent_tasks,
            },
            "network": {
                "proxy_url": self.proxy_url or "",
            },
            "general": {
                "language": self.language,
            },
        }

        with open(config_path, "w", encoding="utf-8") as f:
            toml.dump(data, f)


# 配置模板
CONFIG_TEMPLATE = """# Nekro WebApp 配置
# 配置文件位置: ~/.config/nekro-webapp/config.toml

[llm]
# OpenAI API Key (必填)
openai_api_key = ""

# API 基础 URL (可选，用于兼容其他服务)
openai_base_url = "https://api.openai.com/v1"

# 默认模型
model = "gpt-4"

# 温度参数
temperature = 0.7

[worker]
# Cloudflare Worker URL (可选，用于部署)
url = ""

# 访问密钥
access_key = ""

[task]
# 最大迭代次数
max_iterations = 20

# 任务超时（分钟）
task_timeout_minutes = 15

# 最大并行任务数
max_concurrent_tasks = 3

[network]
# HTTP 代理 URL (可选)
proxy_url = ""

[general]
# 用户语言 (zh-cn, en-us, ja-jp 等)
language = "zh-cn"
"""
