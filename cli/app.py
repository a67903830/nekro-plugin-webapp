import asyncio
import time
from datetime import datetime
from typing import Dict, List, Optional, Set

from loguru import logger
from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Static,
    Tree,
)
from textual.widgets.tree import TreeNode

from .config.settings import WebAppConfig
from .stream import EventType, TaskEvent, task_stream


class DeploySuccessModal(ModalScreen):
    """部署成功模态框"""

    CSS = """
    DeploySuccessModal {
        align: center middle;
    }

    #dialog {
        grid-size: 2;
        grid-gutter: 1 2;
        grid-rows: 1fr 3;
        padding: 0 1;
        width: 60;
        height: 11;
        border: thick $background 80%;
        background: $surface;
    }

    #title {
        column-span: 2;
        height: 1fr;
        width: 1fr;
        content-align: center middle;
    }

    #url {
        column-span: 2;
        height: 1fr;
        width: 1fr;
        content-align: center middle;
        text-style: bold;
    }

    Button {
        width: 100%;
    }
    """

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def compose(self) -> ComposeResult:
        yield Container(
            Label("🚀 部署成功!", id="title"),
            Label(self.url, id="url"),
            Button("复制链接", variant="primary", id="copy"),
            Button("关闭", variant="default", id="close"),
            id="dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy":
            # 尝试复制到剪贴板 (依赖系统支持，这里可能只打印)
            self.app.copy_to_clipboard(self.url)
            self.dismiss()
        elif event.button.id == "close":
            self.dismiss()


class VFSTree(Tree):
    """虚拟文件系统树"""

    def __init__(self):
        super().__init__("📁 Project Root")
        self.root.expand()
        self.known_paths: Set[str] = set()

    def add_path(self, path: str) -> None:
        """添加文件路径到树中"""
        if path in self.known_paths:
            return
        
        self.known_paths.add(path)
        parts = path.split("/")
        current_node = self.root
        
        for i, part in enumerate(parts):
            is_file = (i == len(parts) - 1)
            
            # 查找现有节点
            found = False
            for child in current_node.children:
                if str(child.label) == part:
                    current_node = child
                    found = True
                    break
            
            if not found:
                # 创建新节点
                icon = "📄 " if is_file else "📂 "
                if part.endswith((".tsx", ".ts")):
                    icon = "📘 "
                elif part.endswith(".css"):
                    icon = "🎨 "
                elif part.endswith(".json"):
                    icon = "⚙️ "
                
                node = current_node.add(icon + part, expand=True)
                current_node = node


class StatusPanel(Static):
    """状态统计面板"""
    
    start_time: float = 0
    token_count: int = 0
    
    def on_mount(self) -> None:
        self.start_time = time.time()
        self.update_stats()
        self.set_interval(1.0, self.update_stats)

    def update_stats(self) -> None:
        elapsed = time.time() - self.start_time
        speed = self.token_count / elapsed if elapsed > 0 else 0
        
        self.update(f"""
[bold]📊 统计信息[/bold]

⏱️ 耗时: {elapsed:.1f}s
🔤 Tokens: {self.token_count}
⚡ 速度: {speed:.1f} t/s
        """)

    def add_tokens(self, count: int) -> None:
        self.token_count += count
        self.update_stats()


class WebAppTUI(App):
    """Web应用构建器 TUI"""

    CSS = """
    Screen {
        layout: grid;
        grid-size: 2 3;
        grid-columns: 30% 70%;
        grid-rows: 3 1fr 3;
    }

    Header {
        column-span: 2;
        dock: top;
    }

    Footer {
        column-span: 2;
        dock: bottom;
    }

    /* 左侧边栏 */
    #sidebar {
        row-span: 2;
        background: $surface;
        border-right: heavy $background;
    }

    VFSTree {
        height: 100%;
        background: $surface;
    }

    StatusPanel {
        height: auto;
        padding: 1;
        background: $surface-darken-1;
        border-top: solid $background;
    }

    /* 主内容区 */
    #main-content {
        height: 100%;
        background: $background;
    }

    #output-log {
        height: 100%;
        border: none;
    }

    /* 底部输入区 */
    #input-area {
        column-span: 2;
        height: 3;
        border-top: heavy $background;
        background: $surface;
        padding: 0 1;
    }
    
    Input {
        width: 100%;
    }
    
    .status-label {
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("ctrl+x", "quit", "退出"),
        Binding("c", "cancel", "取消任务"),
        Binding("escape", "focus_input", "输入反馈"),
    ]

    def __init__(self, requirement: str, config: "WebAppConfig", deploy: bool = True):
        super().__init__()
        self.requirement = requirement
        self.config = config
        self.deploy = deploy
        self._task_running = False
        self._cancelled = False
        self.vfs_tree = VFSTree()
        self.status_panel = StatusPanel()
        self.llm_buffer = ""
        
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        
        # 左侧边栏
        with Container(id="sidebar"):
            yield self.vfs_tree
            yield self.status_panel
            
        # 右侧主日志
        with Container(id="main-content"):
            yield RichLog(id="output-log", highlight=True, markup=True, wrap=True)
            
        # 底部输入
        with Container(id="input-area"):
            yield Input(placeholder="输入反馈消息... (当前只读)", id="feedback-input", disabled=True)
            
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"Nekro WebApp Builder ({self.config.model})"
        
        log = self.query_one("#output-log", RichLog)
        
        # === 日志重定向 ===
        # 1. 移除所有现有 sink (避免穿透 TUI)
        logger.remove()
        
        # 2. 添加 TUI sink (只显示 INFO 及以上，避免刷屏)
        def tui_sink(message):
            # 移除末尾换行符，因为 write 会自动添加? 不，RichLog.write 默认换行。
            # Loguru message ends with \n.
            text = message.record["message"]
            level = message.record["level"].name
            time_str = message.record["time"].strftime("%H:%M:%S")
            
            color = "white"
            if level == "INFO":
                color = "green"
            elif level == "WARNING":
                color = "yellow"
            elif level == "ERROR":
                color = "red"
            elif level == "DEBUG":
                color = "dim"
            
            log.write(f"[{color}]{time_str} | {text}[/{color}]")

        logger.add(tui_sink, level="INFO", format="{message}")
        
        log.write(Panel(
            Text(self.requirement, style="cyan"),
            title="[bold]任务需求[/bold]",
            border_style="blue",
        ))
        
        # 启动后台任务
        self.run_worker(self._run_task())

    async def _run_task(self) -> None:
        """执行构建任务"""
        from ..services.task_tracer import TaskTracer
        from .config.settings import get_config_dir
        
        log = self.query_one("#output-log", RichLog)
        
        self._task_running = True
        
        event_queue = task_stream.subscribe()
        
        # 获取数据目录
        data_dir = str(get_config_dir() / "data")
        
        tracer = TaskTracer(
            chat_key="tui",
            root_agent_id="TUI",
            task_description=self.requirement,
            plugin_data_dir=data_dir,
            enabled=True,
        )
        
        try:
            from ..core.agent_loop import run_developer_loop
            
            log.write("[green]🚀 开始构建...[/green]")
            
            task = asyncio.create_task(run_developer_loop(
                chat_key="tui",
                task_description=self.requirement,
                tracer=tracer,
                model_group=self.config.model,
                max_iterations=self.config.max_iterations,
            ))
            
            while not task.done() and not self._cancelled:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                    await self._handle_event(event)
                except asyncio.TimeoutError:
                    continue
            
            if self._cancelled:
                task.cancel()
                log.write("[yellow]⚠️ 任务已取消[/yellow]")
                tracer.finalize("CANCELLED", "用户取消")
            else:
                success, result = await task
                if success:
                    log.write("\n[green]✅ 构建完成![/green]")
                    tracer.finalize("SUCCESS", "")
                else:
                    log.write(f"\n[red]❌ 构建失败: {result}[/red]")
                    tracer.finalize("FAILED", result)
                    
        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            log.write(f"\n[red]❌ 系统错误: {e}[/red]")
            log.write(f"[dim]{error_detail}[/dim]")
            tracer.finalize("ERROR", str(e))
        finally:
            self._task_running = False
            task_stream.unsubscribe(event_queue)

    async def _handle_event(self, event) -> None:
        """处理事件"""
        log = self.query_one("#output-log", RichLog)
        
        if event.type == EventType.LLM_CHUNK:
            # LLM 输出用默认颜色
            self.llm_buffer += event.message
            self.status_panel.add_tokens(len(event.message) // 4 + 1)
            
            # 简单的行缓冲
            if "\n" in self.llm_buffer:
                lines = self.llm_buffer.split("\n")
                for line in lines[:-1]:
                    log.write(line) # 这里不再需要 end=""
                self.llm_buffer = lines[-1]
            
        elif event.type == EventType.FILE_CREATED or event.type == EventType.FILE_MODIFIED:
            # Flush buffer before other events
            if self.llm_buffer:
                log.write(self.llm_buffer)
                self.llm_buffer = ""

            path = event.data.get("path", "")
            if path:
                self.vfs_tree.add_path(path)
                log.write(f"\n[bold blue]📄 文件变更: {path}[/bold blue]\n")
                
        elif event.type == EventType.DEPLOY_SUCCESS:
            if self.llm_buffer:
                log.write(self.llm_buffer)
                self.llm_buffer = ""

            url = event.data.get("url", "")
            log.write(f"\n[bold green]🚀 部署成功: {url}[/bold green]\n")
            self.install_screen(DeploySuccessModal(url), name="deploy_success")
            
        elif event.type == EventType.NOTIFICATION:
            if self.llm_buffer:
                log.write(self.llm_buffer)
                self.llm_buffer = ""
            log.write(f"\n[yellow]📢 {event.message}[/yellow]\n")

    def action_cancel(self) -> None:
        if self._task_running:
            self._cancelled = True
            log = self.query_one("#output-log", RichLog)
            log.write("[yellow]正在取消任务...[/yellow]")

def run_tui(requirement: str, config: "WebAppConfig", deploy: bool = True):
    """运行 TUI 应用"""
    app = WebAppTUI(requirement, config, deploy)
    app.run()
