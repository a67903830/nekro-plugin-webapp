"""部署服务

负责将 HTML 内容部署到 Cloudflare Worker。
通过运行时适配器获取配置，支持 CLI 和插件两种模式。
"""

import asyncio
from typing import Dict, Optional

import httpx
from ..models import CreatePageRequest, CreatePageResponse
from .logger import logger


async def _emit_deploy_event(url: str) -> None:
    try:
        from ..cli.stream import EventType, task_stream
        await task_stream.emit_deploy_event(EventType.DEPLOY_SUCCESS, url=url, message=f"部署成功: {url}")
    except (ImportError, ModuleNotFoundError):
        pass


def _get_deploy_config() -> tuple[Optional[str], Optional[str]]:
    """获取部署配置（worker_url, access_key）
    
    通过运行时适配器获取，支持 CLI 和插件两种模式。
    """
    try:
        from ..runtime import get_adapter
        adapter = get_adapter()
        config = adapter.get_full_config()
        
        # CLI 模式：config 是 WebAppConfig 对象
        if hasattr(config, "worker_url"):
            return config.worker_url, config.access_key
        # 插件模式：config 可能是字典
        if isinstance(config, dict):
            return config.get("worker_url"), config.get("access_key")
    except (ImportError, RuntimeError):
        pass
    
    return None, None


def render_template_vars(html_content: str, template_vars: Dict[str, str]) -> str:
    """渲染模板变量，替换 {{key}} 占位符

    Args:
        html_content: 原始 HTML 内容
        template_vars: 模板变量字典

    Returns:
        替换后的 HTML 内容
    """
    result = html_content
    for key, value in template_vars.items():
        placeholder = "{{" + key + "}}"
        result = result.replace(placeholder, value)
    return result


async def deploy_html_to_worker(
    html_content: str,
    title: str,
    description: str,
    expires_in_days: int = 30,
    template_vars: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """部署 HTML 到 Worker

    Args:
        html_content: HTML 内容
        title: 页面标题
        description: 页面描述
        expires_in_days: 过期天数
        template_vars: 模板变量（用于替换 {{key}} 占位符）

    Returns:
        部署后的 URL，失败返回 None
    """
    # 渲染模板变量
    if template_vars:
        html_content = render_template_vars(html_content, template_vars)
        logger.debug(f"已替换 {len(template_vars)} 个模板变量")
    
    worker_url, access_key = _get_deploy_config()
    
    if not worker_url:
        logger.error("未配置 Worker URL")
        return None

    if not access_key:
        logger.error("未配置访问密钥")
        return None

    worker_url = worker_url.rstrip("/")
    api_url = f"{worker_url}/api/pages"

    # 确保 description 非空
    if not description or not description.strip():
        description = title or "WebApp Page"
        logger.debug(f"description 为空，使用默认值: {description}")

    request_data = CreatePageRequest(
        title=title,
        description=description,
        html_content=html_content,
        expires_in_days=expires_in_days,
    )

    max_retries = 3
    base_delay = 1.0

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    api_url,
                    json=request_data.model_dump(),
                    headers={
                        "Authorization": f"Bearer {access_key}",
                    },
                )

                if response.status_code in (200, 201):
                    data = response.json()
                    result = CreatePageResponse.model_validate(data)
                    logger.info(f"部署成功: {result.url}")
                    await _emit_deploy_event(result.url)
                    return result.url

                logger.warning(
                    f"部署尝试 {attempt + 1}/{max_retries} 失败: HTTP {response.status_code}, {response.text}",
                )

        except httpx.TimeoutException:
            logger.warning(f"部署尝试 {attempt + 1}/{max_retries} 超时")
        except Exception as e:
            logger.warning(f"部署尝试 {attempt + 1}/{max_retries} 异常: {e}")

        # 如果不是最后一次尝试，等待后重试
        if attempt < max_retries - 1:
            delay = base_delay * (2**attempt)
            logger.info(f"等待 {delay}秒后重试...")
            await asyncio.sleep(delay)

    logger.error("部署最终失败")
    return None


async def check_worker_health() -> bool:
    """检查 Worker 健康状态

    Returns:
        是否健康
    """
    worker_url, _ = _get_deploy_config()
    
    if not worker_url:
        return False

    worker_url = worker_url.rstrip("/")
    health_url = f"{worker_url}/health"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(health_url)
            return response.status_code == 200
    except Exception:
        return False
