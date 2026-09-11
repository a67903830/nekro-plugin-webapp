"""流式文本客户端 (Streaming Text Client)

提供纯文本模式的流式 LLM 调用。
通过运行时适配器获取模型配置，支持插件和 CLI 两种模式。
"""

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
from openai import AsyncOpenAI

from .logger import logger

DEFAULT_USER_AGENT = "NekroWebApp/3.0"


def _create_http_client(
    user_agent: str = DEFAULT_USER_AGENT,
    proxy_url: Optional[str] = None,
    read_timeout: int = 300,
    write_timeout: int = 300,
    connect_timeout: int = 30,
    pool_timeout: int = 30,
) -> httpx.AsyncClient:
    """创建配置好的 httpx.AsyncClient"""
    
    async def enforce_user_agent(request: httpx.Request) -> None:
        request.headers["User-Agent"] = user_agent
    
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=write_timeout,
            pool=pool_timeout,
        ),
        proxies={"http://": proxy_url, "https://": proxy_url} if proxy_url else None,
        event_hooks={"request": [enforce_user_agent]},
    )


async def stream_text_completion(
    messages: List[Dict[str, Any]],
    model_group: str,
    proxy_url: Optional[str] = None,  # noqa: ARG001 - 保留接口兼容
) -> AsyncIterator[str]:
    """流式调用 OpenAI 兼容 API（纯文本模式）

    通过运行时适配器获取模型配置。
    不传递 tools 参数，LLM 只输出纯文本。

    Args:
        messages: 消息列表
        model_group: 模型组名称
        proxy_url: 可选代理 URL（由适配器内部处理）

    Yields:
        str: 文本内容增量
    """
    from ..runtime import get_adapter
    
    adapter = get_adapter()
    
    # 通过适配器的 stream_llm 方法完成调用
    async for chunk in adapter.stream_llm(messages, model_group):
        yield chunk


# ==================== 兼容旧接口 ====================


@dataclass
class ToolCallDelta:
    """Tool Call 增量数据 (已废弃，保留兼容)"""
    index: int
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    arguments_delta: str = ""


@dataclass
class StreamChunk:
    """流式响应块 (已废弃，保留兼容)"""
    content_delta: Optional[str] = None
    tool_calls: List[ToolCallDelta] = field(default_factory=list)
    finish_reason: Optional[str] = None


async def stream_tool_call_completion(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],  # noqa: ARG001 - 保留兼容性
    model_group: str,
    proxy_url: Optional[str] = None,
) -> AsyncIterator[StreamChunk]:
    """流式调用 (已废弃，保留兼容)

    WARNING: 此函数已废弃，将在迁移完成后删除。
    请使用 stream_text_completion() 替代。
    """
    logger.warning(
        "[StreamingClient] stream_tool_call_completion 已废弃，"
        "请迁移到 stream_text_completion",
    )

    async for text in stream_text_completion(messages, model_group, proxy_url):
        yield StreamChunk(content_delta=text)
