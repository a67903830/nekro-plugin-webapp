"""提示词日志保存工具"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Union


def save_prompt_log_to_file(
    agent_id: str,
    messages: List[Union[Dict[str, Any], Any]],
    plugin_data_dir: str,
) -> str:
    """保存提示词日志到文件
    
    Args:
        agent_id: Agent ID
        messages: 消息列表（支持 Dict 或具有 role/content 属性的对象）
        plugin_data_dir: 插件数据目录
        
    Returns:
        保存的日志文件路径
    """
    prompts_dir = Path(plugin_data_dir) / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{agent_id}.log"
    log_path = prompts_dir / filename
    
    log_content = f"""{'=' * 80}
提示词日志 - {agent_id}
{'=' * 80}
时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Agent ID: {agent_id}
消息数量: {len(messages)}
{'=' * 80}

"""
    
    for i, msg in enumerate(messages, 1):
        # 支持 Dict 和对象两种格式
        if isinstance(msg, dict):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
        else:
            role = getattr(msg, "role", "unknown")
            content = getattr(msg, "content", "")
        
        log_content += f"[{i}] Role: {role}\n"
        log_content += f"Content:\n{content}\n"
        log_content += f"{'-' * 80}\n\n"
    
    log_path.write_text(log_content, encoding="utf-8")
    
    return str(log_path)
