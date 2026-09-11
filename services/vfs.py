"""Virtual File System (VFS)

简化版虚拟文件系统，管理项目文件的内存状态。
去除所有权机制，支持自由读写。
"""

import re
from typing import Dict, List, Optional

from .logger import logger


class ProjectContext:
    """项目上下文，管理虚拟文件系统"""

    def __init__(self, chat_key: str, task_id: str):
        self.chat_key = chat_key
        self.task_id = task_id
        # filepath -> content
        self.files: Dict[str, str] = {}

    def _normalize_path(self, path: str) -> str:
        """规范化文件路径"""
        return path.strip().lstrip("./").lstrip("/")

    def write_file(self, path: str, content: str) -> None:
        """写入文件"""
        clean_path = self._normalize_path(path)
        self.files[clean_path] = content
        logger.info(f"[VFS] 💾 写入文件: {clean_path} ({len(content)} 字符)")

    def read_file(self, path: str) -> Optional[str]:
        """读取文件"""
        clean_path = self._normalize_path(path)
        return self.files.get(clean_path)

    def delete_file(self, path: str) -> bool:
        """删除文件"""
        clean_path = self._normalize_path(path)
        if clean_path in self.files:
            del self.files[clean_path]
            logger.info(f"[VFS] 🗑️ 删除文件: {clean_path}")
            return True
        return False

    def list_files(self) -> List[str]:
        """列出所有文件"""
        return list(self.files.keys())

    def get_snapshot(self) -> Dict[str, str]:
        """获取所有文件快照（用于编译）"""
        return self.files.copy()

    def clear(self) -> None:
        """清空所有文件"""
        self.files.clear()
        logger.info("[VFS] 🗑️ 已清空所有文件")

    def extract_exports(self, path: str) -> List[str]:
        """从 TypeScript/JavaScript 文件中提取导出名

        支持:
        - export const/let/var/function/class NAME
        - export default function/class NAME
        - export { A, B, C }
        - export type/interface NAME

        Returns:
            导出名列表，默认导出用 'default' 表示
        """
        content = self.read_file(path)
        if not content:
            return []

        exports: List[str] = []

        # 1. export const/let/var/function/class NAME
        pattern1 = r"export\s+(?:const|let|var|function|class|async\s+function)\s+(\w+)"
        exports.extend(re.findall(pattern1, content))

        # 2. export type/interface NAME
        pattern2 = r"export\s+(?:type|interface)\s+(\w+)"
        exports.extend(re.findall(pattern2, content))

        # 3. export default function/class NAME 或匿名
        pattern3 = r"export\s+default\s+(?:function|class)\s+(\w+)?"
        for match in re.finditer(pattern3, content):
            name = match.group(1)
            if name:
                exports.append(f"default ({name})")
            elif "default" not in [e for e in exports if e.startswith("default")]:
                exports.append("default")

        # 4. export default NAME (变量)
        pattern4 = r"export\s+default\s+(\w+)\s*;"
        for match in re.finditer(pattern4, content):
            name = match.group(1)
            if name not in ("function", "class", "async") and f"default ({name})" not in exports:
                exports.append(f"default ({name})")

        # 5. export { A, B, C } 或 export { A as B }
        pattern5 = r"export\s*\{([^}]+)\}"
        for match in re.finditer(pattern5, content):
            items = match.group(1)
            for item in items.split(","):
                item = item.strip()
                if " as " in item:
                    parts = item.split(" as ")
                    if len(parts) == 2:
                        exports.append(parts[1].strip())
                else:
                    if item:
                        exports.append(item)

        # 去重
        return list(dict.fromkeys(exports))


# 全局 VFS 管理器 (key -> ProjectContext)
# key format: "{chat_key}::{task_id}"
_contexts: Dict[str, ProjectContext] = {}


def _make_key(chat_key: str, task_id: str) -> str:
    return f"{chat_key}::{task_id}"


def get_project_context(chat_key: str, task_id: str) -> ProjectContext:
    """获取或创建项目上下文
    
    Args:
        chat_key: 会话 ID
        task_id: 任务 ID
    """
    key = _make_key(chat_key, task_id)
    if key not in _contexts:
        _contexts[key] = ProjectContext(chat_key, task_id)
    return _contexts[key]


def clear_project_context(chat_key: str, task_id: str) -> None:
    """清除项目上下文"""
    key = _make_key(chat_key, task_id)
    if key in _contexts:
        del _contexts[key]
        logger.info(f"[VFS] 已清除项目上下文: {chat_key}/{task_id}")
