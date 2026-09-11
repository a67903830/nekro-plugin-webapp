"""动态依赖解析服务

当编译器返回的 externals 中有未知包时，尝试使用 AI 动态解析其 esm.sh URL。
这是一个 fallback 机制，用于处理 OPTIONAL_IMPORTS 中未预先配置的库。
"""

from typing import Dict, List, Tuple

import httpx
from openai import AsyncOpenAI

from .logger import logger


async def resolve_missing_dependencies(
    missing_packages: List[str],
    model_group: str = "fast",
) -> Tuple[Dict[str, str], List[str]]:
    """使用 AI 解析缺失的依赖包

    Args:
        missing_packages: 缺失的包名列表
        model_group: 使用的模型组

    Returns:
        (resolved_imports, unresolved_packages) - 成功解析的映射和无法解析的包列表
    """
    if not missing_packages:
        return {}, []

    logger.info(f"[DependencyResolver] 开始动态解析依赖: {missing_packages}")

    # 构建 AI 提示
    prompt = f"""你是一个专业的前端工程师。我需要为以下 npm 包生成 esm.sh CDN URL。

包列表:
{chr(10).join(f"- {pkg}" for pkg in missing_packages)}

请为每个包返回其 esm.sh URL。规则：
1. 使用最新稳定版本
2. 如果包依赖 react/react-dom，添加 `?external=react,react-dom`
3. 如果包有其他 peer dependencies，也要添加到 external 参数
4. 如果某个包不存在或不确定，跳过它

输出格式（每行一个，冒号分隔，包名和URL之间只有一个冒号和空格）：
package-name: https://esm.sh/package-name@version

只输出 URL 映射，不要任何解释。不确定的包直接跳过不输出。"""

    try:
        # 通过运行时适配器获取模型配置
        from ..runtime import get_adapter
        adapter = get_adapter()
        model_info = adapter.get_model_info(model_group)

        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30, read=60, write=60, pool=30),
        )

        try:
            async with AsyncOpenAI(
                api_key=model_info["api_key"],
                base_url=model_info["base_url"],
                http_client=http_client,
            ) as client:
                response = await client.chat.completions.create(
                    model=model_info["model"],
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=500,
                )

                if not response.choices or not response.choices[0].message.content:
                    logger.warning("[DependencyResolver] AI 返回空响应")
                    return {}, missing_packages

                result_text = response.choices[0].message.content.strip()
        finally:
            await http_client.aclose()

        # 解析响应
        resolved: Dict[str, str] = {}
        for line in result_text.split("\n"):
            line = line.strip()
            if not line or ":" not in line:
                continue

            # 分割包名和 URL（只按第一个冒号分割，因为 URL 中也有冒号）
            parts = line.split(":", 1)
            if len(parts) != 2:
                continue

            pkg_name = parts[0].strip()
            url = parts[1].strip()

            # 验证 URL 格式
            if not url.startswith("https://esm.sh/"):
                logger.warning(f"[DependencyResolver] 无效 URL 格式: {url}")
                continue

            if pkg_name not in missing_packages:
                logger.warning(f"[DependencyResolver] 意外的包名: {pkg_name}")
                continue

            resolved[pkg_name] = url
            logger.info(f"[DependencyResolver] 解析成功: {pkg_name} -> {url}")

        unresolved = [pkg for pkg in missing_packages if pkg not in resolved]

        # 日志输出
        if unresolved:
            logger.warning(f"[DependencyResolver] 无法解析的包: {unresolved}")
        else:
            logger.info(f"[DependencyResolver] 成功解析 {len(resolved)} 个包")

    except Exception as e:
        logger.exception(f"[DependencyResolver] 动态解析失败: {e}")
        return {}, missing_packages

    else:
        return resolved, unresolved
