"""运行时适配器抽象接口

定义核心引擎所需的运行时能力抽象。
不同运行模式（nekro-agent/独立 CLI）通过不同适配器实现。
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional

if TYPE_CHECKING:
    from ..cli.config.settings import WebAppConfig


# ==================== 全局适配器实例 ====================

_current_adapter: Optional["RuntimeAdapter"] = None


def get_adapter() -> "RuntimeAdapter":
    """获取当前运行时适配器"""
    global _current_adapter
    if _current_adapter is None:
        raise RuntimeError(
            "运行时适配器未初始化。"
            "请先调用 set_adapter() 设置适配器，"
            "或使用 NekroAdapter/StandaloneAdapter 初始化。",
        )
    return _current_adapter


def set_adapter(adapter: "RuntimeAdapter") -> None:
    """设置当前运行时适配器"""
    global _current_adapter
    _current_adapter = adapter


# ==================== 适配器抽象接口 ====================


class RuntimeAdapter(ABC):
    """运行时环境抽象适配器
    
    定义核心引擎所需的所有外部能力：
    - 日志系统
    - 配置获取
    - LLM 调用
    - 用户通知
    """

    @abstractmethod
    def get_logger(self) -> Any:
        """获取日志器实例
        
        Returns:
            日志器对象（支持 info/debug/warning/error/exception 方法）
        """

    @abstractmethod
    def log(self, level: str, message: str, **kwargs: Any) -> None:
        """记录日志
        
        Args:
            level: 日志级别 (debug/info/warning/error)
            message: 日志消息
            **kwargs: 额外数据
        """

    @abstractmethod
    def log_exception(self, message: str) -> None:
        """记录异常（带堆栈）"""

    @abstractmethod
    def get_config(self, key: str, default: Any = None) -> Any:
        """获取配置项
        
        Args:
            key: 配置键名
            default: 默认值
            
        Returns:
            配置值
        """

    @abstractmethod
    def get_full_config(self) -> "WebAppConfig":
        """获取完整配置对象"""

    @abstractmethod
    def stream_llm(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: Optional[float] = None,
    ) -> AsyncIterator[str]:
        """流式调用 LLM
        
        Args:
            messages: 消息列表
            model: 模型名称或模型组名称
            temperature: 温度参数（可选）
            
        Yields:
            文本内容增量
        """

    @abstractmethod
    async def notify_user(self, message: str) -> None:
        """通知用户
        
        nekro-agent 模式：通过 AsyncTaskHandle 通知
        CLI 模式：通过事件流发送
        
        Args:
            message: 通知消息
        """

    @abstractmethod
    def get_plugin_data_dir(self) -> str:
        """获取插件数据目录路径"""

    @abstractmethod
    def get_model_info(self, model_group: str) -> Dict[str, Any]:
        """获取模型配置信息
        
        Args:
            model_group: 模型组名称
            
        Returns:
            包含 api_key, base_url, model, temperature 的字典
        """

    @abstractmethod
    def get_proxy_url(self) -> Optional[str]:
        """获取代理 URL（如果配置了）"""
