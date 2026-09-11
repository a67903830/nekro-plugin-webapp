"""WebApp 快速部署插件配置

简化版本：单 Agent 架构。
"""

from pydantic import Field

from nekro_agent.api import i18n
from nekro_agent.api.plugin import ConfigBase, ExtraField, NekroPlugin

# 插件元信息
plugin = NekroPlugin(
    name="WebApp 智能开发助手",
    module_name="nekro_plugin_webapp",
    description="AI 驱动的 Web 应用开发工具 | 单 Agent 原生 Tool Call 架构 | 自动编译部署",
    version="3.0.0",
    author="KroMiose",
    url="https://github.com/KroMiose/nekro-plugin-webapp",
)


@plugin.mount_config()
class WebAppConfig(ConfigBase):
    """WebApp 部署配置"""

    # ==================== Worker 配置 ====================
    WORKER_URL: str = Field(
        default="",
        title="Worker 访问地址",
        description="Cloudflare Worker 的完整 URL",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="Worker 访问地址",
                en_US="Worker URL",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="Cloudflare Worker 的完整 URL (如: https://your-worker.workers.dev)",
                en_US="Full URL of Cloudflare Worker",
            ),
        ).model_dump(),
    )

    ACCESS_KEY: str = Field(
        default="",
        title="访问密钥",
        description="用于创建页面的访问密钥",
        json_schema_extra=ExtraField(
            is_secret=True,
            i18n_title=i18n.i18n_text(
                zh_CN="访问密钥",
                en_US="Access Key",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="用于创建页面的访问密钥（需在管理界面创建）",
                en_US="Access key for creating pages",
            ),
        ).model_dump(),
    )

    # ==================== 模型配置 ====================

    MODEL_GROUP: str = Field(
        default="default",
        title="开发模型组",
        description="Developer Agent 使用的 LLM 模型组",
        json_schema_extra=ExtraField(
            ref_model_groups=True,
            model_type="chat",
            i18n_title=i18n.i18n_text(
                zh_CN="开发模型组",
                en_US="Development Model Group",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="Developer Agent 使用的 LLM 模型组",
                en_US="LLM model group used by Developer Agent",
            ),
        ).model_dump(),
    )

    # ==================== 迭代控制 ====================

    MAX_ITERATIONS: int = Field(
        default=20,
        title="最大迭代次数",
        description="Developer Agent 最大迭代次数",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="最大迭代次数",
                en_US="Max Iterations",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="Developer Agent 最大修改迭代次数",
                en_US="Maximum modification iterations for Developer Agent",
            ),
        ).model_dump(),
    )

    MAX_CONCURRENT_TASKS: int = Field(
        default=3,
        title="最大并行任务数",
        description="每个会话可同时运行的任务数量上限",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="最大并行任务数",
                en_US="Max Concurrent Tasks",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="每个会话可同时运行的任务数量上限",
                en_US="Maximum number of concurrent tasks per session",
            ),
        ).model_dump(),
    )

    # ==================== 超时控制 ====================

    TASK_TIMEOUT_MINUTES: int = Field(
        default=15,
        title="任务超时时间（分钟）",
        description="单次任务的最大执行时间",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="任务超时时间（分钟）",
                en_US="Task Timeout (Minutes)",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="单次任务的最大执行时间，超时后自动标记为失败",
                en_US="Maximum execution time for a single task",
            ),
        ).model_dump(),
    )

    # ==================== 语言配置 ====================

    LANGUAGE: str = Field(
        default="zh-cn",
        title="用户语言",
        description="生成的网页内容主要用户语言",
        json_schema_extra=ExtraField(
            i18n_title=i18n.i18n_text(
                zh_CN="用户语言",
                en_US="User Language",
            ),
            i18n_description=i18n.i18n_text(
                zh_CN="生成的网页内容主要用户语言 (如 zh-cn, en-us, ja-jp)",
                en_US="Primary user language for generated web content",
            ),
        ).model_dump(),
    )


# 获取配置实例
config: WebAppConfig = plugin.get_config(WebAppConfig)

# 获取插件存储
store = plugin.store
