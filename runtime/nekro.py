"""nekro-agent 模式适配器

为 nekro-agent 插件模式提供运行时支持。
使用延迟导入避免在 CLI 模式下触发 nekro-agent 初始化。
"""

from typing import Any, AsyncIterator, Dict, List, Optional

from .adapter import RuntimeAdapter


class NekroAdapter(RuntimeAdapter):
    """nekro-agent 模式适配器
    
    特点：
    - 延迟导入 nekro-agent 模块（只在实际使用时触发）
    - 复用 nekro-agent 的日志系统
    - 使用 nekro-agent 的模型组配置
    - 通过 AsyncTaskHandle 通知用户
    """

    def __init__(self, plugin_data_dir: str, model_group: str = "default"):
        """初始化适配器
        
        Args:
            plugin_data_dir: 插件数据目录
            model_group: 模型组名称
        """
        self._plugin_data_dir = plugin_data_dir
        self._model_group = model_group
        
        # 延迟初始化
        self._logger: Any = None
        self._core_config: Any = None
        self._notify_callback: Optional[Any] = None

    def _ensure_imports(self) -> None:
        """确保 nekro-agent 模块已导入"""
        if self._logger is None:
            from nekro_agent.core import config
            from nekro_agent.core.logger import logger
            self._logger = logger
            self._core_config = config

    def set_notify_callback(self, callback: Any) -> None:
        """设置通知回调（用于 AsyncTaskHandle.notify_agent）"""
        self._notify_callback = callback

    def get_logger(self) -> Any:
        self._ensure_imports()
        return self._logger

    def log(self, level: str, message: str, **kwargs: Any) -> None:  # noqa: ARG002
        self._ensure_imports()
        log_func = getattr(self._logger, level.lower(), self._logger.info)
        log_func(message)

    def log_exception(self, message: str) -> None:
        self._ensure_imports()
        self._logger.exception(message)

    def get_config(self, key: str, default: Any = None) -> Any:
        self._ensure_imports()
        return getattr(self._core_config, key, default)

    def get_full_config(self) -> Any:
        """返回兼容的配置对象"""
        from ..plugin import config as plugin_config
        
        return {
            "model": self._model_group,
            "worker_url": plugin_config.WORKER_URL,
            "access_key": plugin_config.ACCESS_KEY,
            "max_iterations": plugin_config.MAX_ITERATIONS,
            "task_timeout_minutes": plugin_config.TASK_TIMEOUT_MINUTES,
        }

    async def stream_llm(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: Optional[float] = None,
    ) -> AsyncIterator[str]:
        """使用 nekro-agent 的模型配置进行流式调用"""
        self._ensure_imports()
        
        model_group = model or self._model_group
        model_info = self._core_config.get_model_group_info(model_group)
        
        # 使用适配后的流式客户端
        import httpx
        from openai import AsyncOpenAI
        
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30, read=300, write=300, pool=30),
        )
        
        try:
            async with AsyncOpenAI(
                api_key=model_info.API_KEY.strip() if model_info.API_KEY else None,
                base_url=model_info.BASE_URL,
                http_client=http_client,
            ) as client:
                stream = await client.chat.completions.create(
                    model=model_info.CHAT_MODEL,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=temperature or model_info.TEMPERATURE,
                    stream=True,
                )

                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content

        except Exception:
            self._logger.exception("LLM 调用异常")
            raise
        finally:
            await http_client.aclose()

    async def notify_user(self, message: str) -> None:
        """通过回调通知用户"""
        if self._notify_callback is not None:
            await self._notify_callback(message)
        else:
            self._ensure_imports()
            self._logger.info(f"[通知] {message}")

    def get_plugin_data_dir(self) -> str:
        return self._plugin_data_dir

    def get_model_info(self, model_group: str) -> Dict[str, Any]:
        """获取模型配置信息"""
        self._ensure_imports()
        model_info = self._core_config.get_model_group_info(model_group or self._model_group)
        return {
            "api_key": model_info.API_KEY.strip() if model_info.API_KEY else None,
            "base_url": model_info.BASE_URL,
            "model": model_info.CHAT_MODEL,
            "temperature": model_info.TEMPERATURE,
        }

    def get_proxy_url(self) -> Optional[str]:
        self._ensure_imports()
        return getattr(self._core_config, "DEFAULT_PROXY", None)
