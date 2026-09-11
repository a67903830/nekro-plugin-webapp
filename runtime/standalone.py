"""独立 CLI 模式适配器

为独立运行模式提供运行时支持，不依赖 nekro-agent。
使用 loguru 日志和 OpenAI SDK。
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional

import httpx
from loguru import logger
from openai import AsyncOpenAI

from .adapter import RuntimeAdapter

if TYPE_CHECKING:
    from ..cli.config.settings import WebAppConfig


class StandaloneAdapter(RuntimeAdapter):
    """独立 CLI 模式适配器
    
    特点：
    - 使用 loguru 日志
    - 直接调用 OpenAI 兼容 API
    - 配置来自本地文件
    - 通过事件流通知用户
    """

    def __init__(self, config: "WebAppConfig"):
        """初始化适配器
        
        Args:
            config: WebApp 配置对象
        """
        self._config = config
        self._data_dir: Optional[Path] = None

    def get_logger(self) -> Any:
        return logger

    def log(self, level: str, message: str, **kwargs: Any) -> None:
        log_func = getattr(logger, level.lower(), logger.info)
        if kwargs:
            message = f"{message} | {kwargs}"
        log_func(message)

    def log_exception(self, message: str) -> None:
        logger.exception(message)

    def get_config(self, key: str, default: Any = None) -> Any:
        return getattr(self._config, key, default)

    def get_full_config(self) -> "WebAppConfig":
        return self._config

    def stream_llm(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: Optional[float] = None,
    ) -> AsyncIterator[str]:
        """流式调用 OpenAI 兼容 API"""
        return self._stream_llm_impl(messages, model, temperature)

    async def _stream_llm_impl(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: Optional[float] = None,
    ) -> AsyncIterator[str]:
        """实际的流式调用实现"""
        proxy_url = self.get_proxy_url()
        
        # 创建 HTTP 客户端
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30, read=300, write=300, pool=30),
            proxies={"http://": proxy_url, "https://": proxy_url} if proxy_url else None,
        )

        try:
            async with AsyncOpenAI(
                api_key=self._config.openai_api_key,
                base_url=self._config.openai_base_url,
                http_client=http_client,
            ) as client:
                stream = await client.chat.completions.create(
                    model=model or self._config.model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=temperature or self._config.temperature,
                    stream=True,
                )

                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content

        except Exception as e:
            logger.exception(f"LLM 调用异常: {e}")
            raise
        finally:
            await http_client.aclose()

    async def notify_user(self, message: str) -> None:
        """通过事件流通知用户"""
        # 尝试导入事件流（如果正在 TUI 模式运行）
        try:
            from ..cli.stream import task_stream
            await task_stream.emit_notification(message)
        except ImportError:
            # 如果事件流不可用，直接打印
            logger.info(f"[通知] {message}")

    def get_plugin_data_dir(self) -> str:
        """获取插件数据目录"""
        if self._data_dir is None:
            from ..cli.config.settings import get_config_dir
            self._data_dir = get_config_dir() / "data"
            self._data_dir.mkdir(parents=True, exist_ok=True)
        return str(self._data_dir)

    def get_model_info(self, model_group: str) -> Dict[str, Any]:
        """获取模型配置信息"""
        return {
            "api_key": self._config.openai_api_key,
            "base_url": self._config.openai_base_url,
            "model": model_group or self._config.model,
            "temperature": self._config.temperature,
        }

    def get_proxy_url(self) -> Optional[str]:
        """获取代理 URL"""
        return getattr(self._config, "proxy_url", None)
