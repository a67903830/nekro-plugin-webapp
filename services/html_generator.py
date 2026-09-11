"""HTML 生成器

负责将编译后的 JavaScript 代码包装成完整的 HTML 页面。
包含依赖注入、动态 Import Map 生成等功能。
"""

import json
import re
from typing import Dict, List, Optional

# 核心依赖 (始终包含)
CORE_IMPORTS = {
    "react": "https://esm.sh/react@18.2.0",
    "react/jsx-runtime": "https://esm.sh/react@18.2.0/jsx-runtime",
    "react-dom": "https://esm.sh/react-dom@18.2.0",
    "react-dom/client": "https://esm.sh/react-dom@18.2.0/client",
}

# 可选依赖 (按需注入)
OPTIONAL_IMPORTS = {
    "framer-motion": "https://esm.sh/framer-motion@10.16.4?external=react,react-dom",
    "lucide-react": "https://esm.sh/lucide-react@0.292.0?external=react,react-dom",
    "recharts": "https://esm.sh/recharts@2.10.3?external=react,react-dom",
    "clsx": "https://esm.sh/clsx@2.0.0",
    "tailwind-merge": "https://esm.sh/tailwind-merge@2.1.0",
    "date-fns": "https://esm.sh/date-fns@2.30.0",
    "canvas-confetti": "https://esm.sh/canvas-confetti@1.9.2?deps=canvas-confetti@1.9.2",
    "react-use": "https://esm.sh/react-use@17.4.2?external=react,react-dom",
    "leaflet": "https://esm.sh/leaflet@1.9.4",
    "react-leaflet": "https://esm.sh/react-leaflet@4.2.1?external=react,react-dom,leaflet",
    "three": "https://esm.sh/three@0.160.0",
    "@react-three/fiber": "https://esm.sh/@react-three/fiber@8.15.12?external=react,react-dom,three",
    "@react-three/drei": "https://esm.sh/@react-three/drei@9.96.1?external=react,react-dom,three,@react-three/fiber",
    # zustand 及其子路径
    "zustand": "https://esm.sh/zustand@4.4.7?external=react",
    "zustand/middleware": "https://esm.sh/zustand@4.4.7/middleware?external=react",
    "zustand/shallow": "https://esm.sh/zustand@4.4.7/shallow?external=react",
    "zustand/context": "https://esm.sh/zustand@4.4.7/context?external=react",
    "zustand/vanilla": "https://esm.sh/zustand@4.4.7/vanilla",
    "react-router-dom": "https://esm.sh/react-router-dom@6.21.0?external=react,react-dom",
    "marked": "https://esm.sh/marked@11.1.0",
    # 2D 游戏引擎
    "pixi.js": "https://esm.sh/pixi.js@7.3.2",
    # 动画库
    "gsap": "https://esm.sh/gsap@3.12.4",
    "lottie-react": "https://esm.sh/lottie-react@2.4.0?external=react,react-dom",
    # 音频库
    "tone": "https://esm.sh/tone@14.7.77",
    "howler": "https://esm.sh/howler@2.2.4",
    # 内容渲染
    "react-markdown": "https://esm.sh/react-markdown@9.0.1?external=react,react-dom",
    # 工具库
    "lodash": "https://esm.sh/lodash-es@4.17.21",
    "mathjs": "https://esm.sh/mathjs@12.2.1",
}


def get_all_known_imports() -> Dict[str, str]:
    """获取所有已知的依赖映射（核心 + 可选）"""
    return {**CORE_IMPORTS, **OPTIONAL_IMPORTS}


def validate_externals(externals: List[str]) -> tuple[bool, List[str]]:
    """验证外部依赖是否全部在 Import Map 中配置

    Args:
        externals: 编译器返回的外部依赖列表

    Returns:
        (is_valid, missing_packages) - 是否全部有效，缺失的包列表
    """
    all_known = get_all_known_imports()
    missing = [pkg for pkg in externals if pkg not in all_known]
    return len(missing) == 0, missing


def generate_shell_html(
    title: str,
    body_js: str,
    dependencies: Optional[List[str]] = None,
    extra_imports: Optional[Dict[str, str]] = None,
) -> str:
    """生成最终的 Shell HTML，注按需注入脚本和样式

    Args:
        title: 页面标题
        body_js: 编译后的 JavaScript 代码 (ESM)
        dependencies: 显式声明的依赖列表

    Returns:
        完整的 HTML 字符串
    """
    if dependencies is None:
        dependencies = []

    scripts = []
    
    # 1. Tailwind (Heavy, optional) - 始终注入，因为它是核心样式系统
    # 也可以检查 content 中是否包含 tailwind 类名，但为了稳健性建议默认开启
    scripts.append(
        '<script src="https://cdn.tailwindcss.com"></script>',
    )
    
    # 2. Leaflet CSS (Map)
    if "leaflet" in dependencies or "leaflet" in body_js:
        scripts.append(
            '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="" />',
        )
    
    # 3. KaTeX CSS (Math formulas)
    if "katex" in dependencies:
        scripts.append(
            '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css" integrity="sha384-n8MVd4RsNIU0tAv4ct0nTaAbDJwPJzDEaqSD1odI+WdtXRGWt2kTvGFasHpSy3SV" crossorigin="anonymous">',
        )

    scripts_html = "\n    ".join(scripts)

    # 动态构建 import map
    final_imports = CORE_IMPORTS.copy()

    # 合并所有可用的依赖源（静态配置 + 动态解析）
    scannable_imports = {**OPTIONAL_IMPORTS, **(extra_imports or {})}

    # 扫描代码特征和依赖声明
    # 简单的启发式搜索: 检查所有可用依赖的 key 是否出现在 body_js 中
    # 或者是否在 dependencies 列表中
    for pkg_name, url in scannable_imports.items():
        # 1. 显式声明
        if pkg_name in dependencies:
            final_imports[pkg_name] = url
            continue

        # 2. 代码引用检测 (简单字符串匹配)
        # 例如: import { Canvas } from "@react-three/fiber" -> 包含 "@react-three/fiber"
        # 或者 import * as THREE from "three"
        # 注意: body_js 是编译后的代码，esbuild 对于 external 模块会保留 import "pkg_name"
        if f'"{pkg_name}"' in body_js or f"'{pkg_name}'" in body_js:
            final_imports[pkg_name] = url

    # 自动解析隐式依赖：从 esm.sh URL 的 external= 参数提取依赖链
    # 例如: "external=react,react-dom,leaflet" -> 需要确保这些包也在 import map 中
    def extract_external_deps(esm_url: str) -> List[str]:
        """从 esm.sh URL 提取 external 参数中的依赖列表"""
        if "external=" not in esm_url:
            return []
        # 提取 external= 后的包列表 (可能被 & 截断)
        match = re.search(r"external=([^&]+)", esm_url)
        if match:
            return [dep.strip() for dep in match.group(1).split(",") if dep.strip()]
        return []

    # 遍历已添加的包，解析其 external 依赖并补充到 import map
    added_deps = True
    # 合并静态配置和动态解析的依赖
    all_imports = {**CORE_IMPORTS, **OPTIONAL_IMPORTS, **(extra_imports or {})}
    
    processed_deps = set()
    
    while added_deps:  # 循环直到没有新依赖被添加（处理传递依赖）
        added_deps = False
        current_imports = list(final_imports.items())
        
        for pkg_name, url in current_imports:
            if pkg_name in processed_deps:
                continue
            
            processed_deps.add(pkg_name)
            
            for dep in extract_external_deps(url):
                if dep not in final_imports and dep in all_imports:
                    final_imports[dep] = all_imports[dep]
                    added_deps = True

    import_map = {"imports": final_imports}

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title or "WebApp"}</title>
    <style>
      /* Base styles to prevent white flash */
      html, body, #root {{ width: 100%; height: 100%; margin: 0; padding: 0; }}
    </style>
    {scripts_html}
    <script type="importmap">
    {json.dumps(import_map, indent=4)}
    </script>
    <script type="module">
{body_js}
    </script>
</head>
<body>
    <div id="root"></div>
</body>
</html>
"""
