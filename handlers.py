"""Web 路由处理"""

from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from nekro_agent.core import logger

from .plugin import config, plugin


@plugin.mount_router()
def create_router() -> APIRouter:
    """创建并配置插件路由"""
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse, summary="管理界面")
    async def index() -> HTMLResponse:
        """返回管理界面"""
        static_path = Path(__file__).parent / "static" / "index.html"
        if not static_path.exists():
            raise HTTPException(404, "管理界面未找到")

        try:
            html_content = static_path.read_text(encoding="utf-8")
            return HTMLResponse(content=html_content)
        except Exception as e:
            logger.exception("读取管理界面失败")
            raise HTTPException(500, f"读取管理界面失败: {e}") from e

    @router.get("/health", summary="健康检查")
    async def health_check() -> dict:
        """检查插件和 Worker 状态"""
        result: dict[str, str | bool] = {
            "status": "ok",
            "worker_configured": bool(config.WORKER_URL),
            "worker_url": config.WORKER_URL or "",  # 返回 Worker URL 供前端使用
        }

        # 如果配置了 Worker URL，检查其可用性
        if config.WORKER_URL:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(
                        f"{config.WORKER_URL.rstrip('/')}/api/health",
                    )
                    if response.status_code == 200:
                        worker_data = response.json()
                        result["worker_status"] = "healthy"
                        result["worker_initialized"] = worker_data.get("initialized", False)
                    else:
                        result["worker_status"] = "error"
                        result["worker_error"] = f"HTTP {response.status_code}"
            except Exception as e:
                logger.warning(f"Worker 健康检查失败: {e}")
                result["worker_status"] = "error"
                result["worker_error"] = str(e)
        else:
            result["worker_status"] = "not_configured"

        return result

    @router.api_route(
        "/proxy/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        summary="Worker 代理",
    )
    async def proxy_worker(path: str, request: Request) -> Response:
        """代理所有对 Worker 的请求，解决跨域问题

        Args:
            path: Worker 的路径（如 api/health, admin/keys 等）
            request: 原始请求对象

        Returns:
            Response: Worker 的响应
        """
        if not config.WORKER_URL:
            raise HTTPException(
                status_code=400,
                detail="Worker URL 未配置，请先在插件配置中设置 WORKER_URL",
            )

        # 构造完整的 Worker URL
        worker_url = f"{config.WORKER_URL.rstrip('/')}/{path.lstrip('/')}"

        try:
            # 获取原始请求的 body
            body = await request.body()

            # 转发请求头（过滤掉一些不需要的头）
            headers = {
                key: value
                for key, value in request.headers.items()
                if key.lower()
                not in {
                    "host",
                    "connection",
                    "content-length",
                    "accept-encoding",  # 防止 Worker 返回压缩响应
                }
            }

            # 发送请求到 Worker
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    method=request.method,
                    url=worker_url,
                    headers=headers,
                    content=body if body else None,
                    params=request.query_params,
                )

                # 转发响应头（过滤掉一些不需要的头）
                response_headers = {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower()
                    not in {
                        "content-encoding",
                        "content-length",
                        "transfer-encoding",
                        "connection",
                    }
                }

                # 返回响应
                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=response_headers,
                )

        except httpx.TimeoutException as e:
            logger.error(f"Worker 请求超时: {worker_url}")
            raise HTTPException(
                status_code=504,
                detail=f"Worker 请求超时: {e}",
            ) from e
        except httpx.HTTPStatusError as e:
            logger.error(f"Worker 返回错误: {e.response.status_code}")
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Worker 返回错误: {e.response.text}",
            ) from e
        except httpx.HTTPError as e:
            logger.error(f"Worker 请求失败: {e}")
            raise HTTPException(
                status_code=502,
                detail=f"Worker 请求失败: {e}",
            ) from e
        except Exception as e:
            logger.exception("代理请求出错")
            raise HTTPException(
                status_code=500,
                detail=f"代理请求出错: {e}",
            ) from e

    return router
