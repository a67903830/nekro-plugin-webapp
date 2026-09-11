import asyncio
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Tuple

if TYPE_CHECKING:
    from .task_tracer import TaskTracer

from .logger import logger

# Configuration
NODE_VERSION = "v18.19.0"

# Cache
_CACHED_NODE_PATH: Optional[str] = None
_INIT_LOCK = asyncio.Lock()


def _get_node_dist_name(tracer: "TaskTracer", agent_id: str = "UNKNOWN") -> str:
    """Detect appropriate Node.js distribution for current platform"""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux":
        os_name = "linux"
    elif system == "darwin":
        os_name = "darwin"
    else:
        # Fallback to linux if unknown (e.g. running in wsl but reporting funny?)
        # Or raise error if strict. For plugins, best effort.
        tracer.log_event(tracer.EVENT.NODE_UNKNOWN_SYS, agent_id, f"Unknown system '{system}', defaulting to linux")
        os_name = "linux"

    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        tracer.log_event(tracer.EVENT.NODE_UNKNOWN_ARCH, agent_id, f"Unknown architecture '{machine}', defaulting to x64")
        arch = "x64"

    return f"node-{NODE_VERSION}-{os_name}-{arch}"


def _get_system_node() -> Optional[str]:
    """Check if node exists in PATH or NVM"""

    # 1. Check PATH
    node_path = shutil.which("node")
    if node_path:
        return node_path

    # 2. Check NVM (Common locations)
    home = Path.expanduser(Path("~"))
    nvm_versions_dir = home / ".nvm" / "versions" / "node"
    if nvm_versions_dir.exists():
        try:
            versions = sorted(
                [p.name for p in nvm_versions_dir.iterdir() if p.is_dir()],
                reverse=True,
            )
            if versions:
                potential = nvm_versions_dir / versions[0] / "bin" / "node"
                if potential.exists():
                    return str(potential)
        except Exception:
            pass
    return None


async def _download_and_extract_node(target_dir: Path, tracer: "TaskTracer", agent_id: str = "UNKNOWN"):
    """Download Node.js binary and extract it"""
    dist_name = _get_node_dist_name(tracer, agent_id)
    download_url = f"https://nodejs.org/dist/{NODE_VERSION}/{dist_name}.tar.xz"

    tracer.log_event(tracer.EVENT.NODE_DOWNLOAD, agent_id, f"Downloading Node.js {NODE_VERSION} ({dist_name})...")

    tar_path = target_dir / f"{dist_name}.tar.xz"
    extract_temp = target_dir / "temp_extract"

    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Download
        # Use run_in_executor for blocking IO
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: urllib.request.urlretrieve(download_url, tar_path),
        )

        tracer.log_event(tracer.EVENT.NODE_EXTRACT, agent_id, "Extracting Node.js...")

        def extract():
            with tarfile.open(tar_path, "r:xz") as tar:
                tar.extractall(path=extract_temp)

        await loop.run_in_executor(None, extract)

        # Move bin to final location
        # The tar contains a root folder like node-v18.19.0-linux-x64/
        extracted_root = extract_temp / dist_name

        if not extracted_root.exists():
            # Fallback: find the first directory
            extracted_root = next(extract_temp.iterdir())

        # Clean old installation if exists
        final_dist_dir = target_dir / "dist"
        if final_dist_dir.exists():
            shutil.rmtree(final_dist_dir)

        shutil.move(str(extracted_root), str(final_dist_dir))

        # Cleanup
        tar_path.unlink()
        shutil.rmtree(extract_temp)

        tracer.log_event(tracer.EVENT.NODE_INSTALLED, agent_id, "Node.js installed successfully.")

    except Exception as e:
        # Cleanup on failure
        if tar_path.exists():
            tar_path.unlink()
        if extract_temp.exists():
            shutil.rmtree(extract_temp)
        if target_dir.exists():
            shutil.rmtree(target_dir)  # Clean entire partial dir
        raise RuntimeError(
            f"Failed to download Node.js from {download_url}: {e}",
        ) from e


async def get_node_executable(tracer: "TaskTracer", agent_id: str = "UNKNOWN") -> str:
    """Get the path to a usable Node.js executable.

    Priority:
    1. Cached path
    2. Local standalone installation (services/local_compiler/node/dist/bin/node)
    3. System PATH / NVM
    4. Auto-install local standalone version
    """
    global _CACHED_NODE_PATH

    if _CACHED_NODE_PATH and Path(_CACHED_NODE_PATH).exists():
        return _CACHED_NODE_PATH

    async with _INIT_LOCK:
        # Check again under lock
        if _CACHED_NODE_PATH and Path(_CACHED_NODE_PATH).exists():
            return _CACHED_NODE_PATH

        # Define local path
        current_dir = Path(__file__).parent
        local_node_dir = current_dir / "local_compiler" / "node_runtime"
        local_node_bin = local_node_dir / "dist" / "bin" / "node"

        # 1. Check Local Install first (Preferred for consistency if present)
        if local_node_bin.exists():
            _CACHED_NODE_PATH = str(local_node_bin)
            return _CACHED_NODE_PATH

        # 2. Check System
        # (Optional: You might want to skip this if you want absolute reproducibility,
        # but for performance/disk usage, using system node is often better)
        system_node = _get_system_node()
        if system_node:
            # Validate version? For now assume system node is recent enough
            _CACHED_NODE_PATH = system_node
            return system_node

        # 3. Install Local
        tracer.log_event(tracer.EVENT.NODE_MISSING, agent_id, "Node.js not found in system. Installing standalone runtime...")
        await _download_and_extract_node(local_node_dir, tracer, agent_id)

        if local_node_bin.exists():
            _CACHED_NODE_PATH = str(local_node_bin)

            # Ensure it is executable
            local_node_bin.chmod(0o755)

            return _CACHED_NODE_PATH
        raise RuntimeError(
            "Installed Node.js but binary not found at expected path.",
        )
