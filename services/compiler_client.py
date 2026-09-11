import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from . import node_manager
from .logger import logger

if TYPE_CHECKING:
    from .task_tracer import TaskTracer



async def compile_project(
    files: Dict[str, str],
    tracer: "TaskTracer",
    env_vars: Optional[Dict[str, str]] = None,
    agent_id: str = "UNKNOWN",
    skip_type_check: bool = False,
) -> Tuple[bool, str, List[str]]:
    """
    使用本地封装的 ESBuild (Wasm) 进行编译
    支持 process.env 变量注入 (通过 define)
    
    Args:
        files: 源文件字典
        tracer: 任务追踪器
        env_vars: 环境变量
        agent_id: Agent ID
        skip_type_check: 跳过类型检查（仅在确认类型错误为误报时使用）
    
    Returns:
        (success, output_code, external_imports)
    """
    try:
        # 0. 获取 Node 环境 (支持自动下载)
        try:
            node_path = await node_manager.get_node_executable(tracer, agent_id)
        except RuntimeError as e:
            return False, str(e), []

        # 1. 准备构建脚本路径
        current_dir = Path(__file__).parent
        compiler_dir = current_dir / "local_compiler"
        script_path = compiler_dir / "build.js"

        if not script_path.exists():
            return False, f"Compiler script not found at {script_path}", []

        # 1.5. 执行类型检查 (TypeScript Strict Check)
        if not skip_type_check:
            type_check_error = await check_project(files, tracer, env_vars, agent_id)
            if type_check_error:
                # 如果类型检查失败，直接返回错误，不需要继续构建 bundle
                # 这能捕获如 undefined variables, missing imports 等关键错误
                return False, f"Type Check Failed:\n{type_check_error}", []

        # 2. 调用 Node 进程
        input_payload = {
            "files": files,
            "env_vars": env_vars or {},
        }
        input_data = json.dumps(input_payload)

        # 准备环境变量，确保 PATH 包含 node 所在目录
        # 否则 esbuild 内部 spawn worker 时会因为找不到 node 而报错 (spawn node ENOENT)
        env = os.environ.copy()
        node_dir = str(Path(node_path).parent)
        env["PATH"] = f"{node_dir}{os.pathsep}{env.get('PATH', '')}"

        # 确保 V8 使用 UTF-8
        env["LANG"] = "en_US.UTF-8"

        process = await asyncio.create_subprocess_exec(
            node_path, 
            str(script_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(compiler_dir),
            env=env,
        )

        stdout_bytes, stderr_bytes = await process.communicate(input=input_data.encode())
        
        stdout = stdout_bytes.decode()
        stderr = stderr_bytes.decode()

        if process.returncode != 0:
            tracer.log_event(tracer.EVENT.COMPILER_CRASH, agent_id, f"Local compiler crashed: {stderr}")
            return False, f"Compiler crashed: {stderr}", []

        # 3. 解析结果
        if not stdout.strip():
            return False, f"Empty output from compiler. Stderr: {stderr}", []

        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            tracer.log_event(tracer.EVENT.COMPILER_JSON_ERR, agent_id, f"Invalid JSON from compiler: {stdout}")
            return False, f"Compiler internal error (Invalid JSON): {stdout[:200]}...", []

        externals = result.get("externals", [])
        if result.get("success"):
            return True, result.get("output", ""), externals
        return False, result.get("error", "Unknown error"), externals

    except Exception as e:
        tracer.log_event(tracer.EVENT.COMPILER_EXCEPTION, agent_id, f"Compilation exception: {e}")
        return False, str(e), []


async def check_project(
    files: Dict[str, str],
    tracer: "TaskTracer",
    env_vars: Optional[Dict[str, str]] = None,
    agent_id: str = "UNKNOWN",
) -> Optional[str]:
    """Run strict type checking on the project."""
    try:
        node_path = await node_manager.get_node_executable(tracer, agent_id)
    except RuntimeError:
        return None  # Skip if node not available

    current_dir = Path(__file__).parent
    compiler_dir = current_dir / "local_compiler"
    check_script = compiler_dir / "check.js"

    if not check_script.exists():
        # If standard check.js missing, fallback or skip
        return None

    # Prepare input
    input_data = {
        "files": files,
        "env_vars": env_vars or {},
    }
    input_json = json.dumps(input_data)

    try:
        # 准备环境变量 (Path injection)
        env = os.environ.copy()
        node_dir = str(Path(node_path).parent)
        env["PATH"] = f"{node_dir}{os.pathsep}{env.get('PATH', '')}"
        
        process = await asyncio.create_subprocess_exec(
            node_path,
            str(check_script),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(compiler_dir),
            env=env,
        )
        
        stdout_bytes, stderr_bytes = await process.communicate(input=input_json.encode())
        stdout = stdout_bytes.decode()

        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            # If stdout is empty, maybe stderr has info?
            if not stdout.strip():
                # check stderr
                return None  # Fail silently or return error?
            return f"Validator Error (Invalid JSON): {stdout[:100]}"

        if result and not result.get("success"):
            return result.get("error")

    except Exception as e:
        logger.warning(f"Validation check failed to run: {e}")
        return None
    else:
        return None

