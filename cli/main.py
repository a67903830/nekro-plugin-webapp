#!/usr/bin/env python3
"""Nekro WebApp CLI - 独立命令行工具"""

import asyncio
from pathlib import Path
from typing import Optional

import click
from loguru import logger
from rich.console import Console

console = Console()


@click.group()
@click.version_option(version="3.0.0")
def cli():
    """Nekro WebApp - AI 驱动的 Web 应用开发工具"""


@cli.command()
@click.argument("requirement")
@click.option("--model", "-m", default=None, help="使用的模型")
@click.option("--output", "-o", type=Path, default=None, help="输出目录")
@click.option("--no-deploy", is_flag=True, help="不部署，只生成本地文件")
@click.option("--no-tui", is_flag=True, help="不使用 TUI，直接输出日志")
def build(
    requirement: str,
    model: Optional[str],
    output: Optional[Path],
    no_deploy: bool,
    no_tui: bool,
):
    """根据需求生成 WebApp
    
    REQUIREMENT: 应用需求描述
    """
    from ..runtime import set_adapter
    from ..runtime.standalone import StandaloneAdapter
    from .config.settings import WebAppConfig

    config = WebAppConfig.load()

    if not config.openai_api_key:
        console.print("[red]错误: 未配置 OpenAI API Key[/red]")
        console.print("请运行 [cyan]nekro-webapp config --init[/cyan] 初始化配置")
        console.print("然后编辑 [cyan]~/.config/nekro-webapp/config.toml[/cyan] 设置 API Key")
        raise click.Abort

    if model:
        config.model = model
    if output:
        config.output_dir = output

    adapter = StandaloneAdapter(config)
    set_adapter(adapter)

    if no_tui:
        asyncio.run(_run_build(requirement, config, deploy=not no_deploy))
    else:
        from .app import run_tui
        run_tui(requirement, config, deploy=not no_deploy)


async def _run_build(requirement: str, config, deploy: bool = True):
    """执行构建任务"""
    from ..core.agent_loop import run_developer_loop
    from ..services.compiler_client import compile_project
    from ..services.deploy import deploy_html_to_worker
    from ..services.task_tracer import TaskTracer
    from ..services.vfs import get_project_context
    from .config.settings import get_config_dir
    
    console.print("[green]🚀 开始构建 WebApp[/green]")
    console.print(f"[blue]需求: {requirement}[/blue]")
    console.print(f"[blue]模型: {config.model}[/blue]")
    
    data_dir = str(get_config_dir() / "data")
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    
    tracer = TaskTracer(
        chat_key="cli",
        root_agent_id="CLI",
        task_description=requirement,
        plugin_data_dir=data_dir,
        enabled=True,
    )
    
    try:
        success, result = await run_developer_loop(
            chat_key="cli",
            task_description=requirement,
            tracer=tracer,
            model_group=config.model,
            max_iterations=config.max_iterations,
        )
        
        if not success:
            console.print(f"[red]❌ 构建失败: {result}[/red]")
            tracer.finalize("FAILED", result)
            return
        
        console.print("[green]✅ 代码生成完成[/green]")
        
        project = get_project_context("cli", "CLI")
        files = project.get_snapshot()
        
        console.print("[yellow]📦 编译项目...[/yellow]")
        compile_success, compile_output, _ = await compile_project(files, tracer)
        
        if not compile_success:
            console.print(f"[red]❌ 编译失败: {compile_output}[/red]")
            tracer.finalize("COMPILE_FAILED", compile_output)
            return
        
        console.print("[green]✅ 编译成功[/green]")
        
        if deploy and config.worker_url and config.access_key:
            console.print("[yellow]🚀 部署到 Worker...[/yellow]")
            
            if not compile_output:
                console.print("[red]❌ 无 HTML 内容可部署[/red]")
                tracer.finalize("DEPLOY_FAILED", "无 HTML 内容")
                return
            
            url = await deploy_html_to_worker(
                html_content=compile_output,
                title=result or "WebApp",
                description=requirement[:200],
            )
            
            if url:
                console.print("[green]✅ 部署成功![/green]")
                console.print(f"[blue]🔗 访问地址: {url}[/blue]")
                tracer.finalize("SUCCESS", url)
            else:
                console.print("[red]❌ 部署失败[/red]")
                tracer.finalize("DEPLOY_FAILED", "部署请求失败")
        elif deploy:
            console.print("[yellow]⚠️ 未配置 Worker，跳过部署[/yellow]")
            console.print("请运行 [cyan]nekro-webapp config --show[/cyan] 检查 Worker 配置")
            tracer.finalize("SUCCESS", "已生成但未部署")
        else:
            console.print("[blue]已跳过部署（--no-deploy）[/blue]")
            tracer.finalize("SUCCESS", "已生成")
        
    except Exception as e:
        logger.exception(f"构建异常: {e}")
        tracer.finalize("ERROR", str(e))
        raise


@cli.command()
@click.option("--init", is_flag=True, help="初始化配置文件")
@click.option("--show", is_flag=True, help="显示当前配置")
@click.option("--set", "set_values", multiple=True, help="设置配置项 (格式: key=value)")
@click.option("--path", is_flag=True, help="显示配置文件路径")
def config(init: bool, show: bool, set_values: tuple, path: bool):
    """管理配置"""
    from .config.settings import CONFIG_TEMPLATE, WebAppConfig, get_config_dir

    config_path = WebAppConfig.get_config_path()

    if path:
        console.print(f"[blue]配置文件路径: {config_path}[/blue]")
        console.print(f"[blue]配置目录: {get_config_dir()}[/blue]")
        return

    if init:
        if config_path.exists() and not click.confirm(f"配置文件已存在 ({config_path})，是否覆盖?"):
            return

        config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        console.print(f"[green]✅ 配置文件已创建: {config_path}[/green]")
        console.print("[yellow]请编辑配置文件设置 API Key[/yellow]")
        return

    if show:
        if not config_path.exists():
            console.print("[red]配置文件不存在[/red]")
            console.print("请运行 [cyan]nekro-webapp config --init[/cyan] 初始化")
            return

        cfg = WebAppConfig.load()
        console.print(f"[blue]配置文件: {config_path}[/blue]")
        console.print()
        console.print("[bold]LLM 配置:[/bold]")
        console.print(f"  模型: {cfg.model}")
        console.print(f"  API Key: {'*' * 8 + cfg.openai_api_key[-4:] if len(cfg.openai_api_key) > 4 else '(未设置)'}")
        console.print(f"  Base URL: {cfg.openai_base_url}")
        console.print()
        console.print("[bold]Worker 配置:[/bold]")
        console.print(f"  URL: {cfg.worker_url or '(未设置)'}")
        console.print(f"  Access Key: {'*' * 8 if cfg.access_key else '(未设置)'}")
        console.print()
        console.print("[bold]任务配置:[/bold]")
        console.print(f"  最大迭代: {cfg.max_iterations}")
        console.print(f"  超时: {cfg.task_timeout_minutes} 分钟")
        return

    if set_values:
        cfg = WebAppConfig.load()
        for kv in set_values:
            if "=" not in kv:
                console.print(f"[red]无效格式: {kv}[/red]")
                continue
            key, value = kv.split("=", 1)
            if hasattr(cfg, key):
                current_value = getattr(cfg, key)
                if isinstance(current_value, int):
                    value = int(value)
                elif isinstance(current_value, float):
                    value = float(value)
                elif isinstance(current_value, bool):
                    value = value.lower() in ("true", "1", "yes")
                setattr(cfg, key, value)
                console.print(f"[green]✓ {key} = {value}[/green]")
            else:
                console.print(f"[yellow]未知配置项: {key}[/yellow]")
        cfg.save()
        return

    click.echo(click.get_current_context().get_help())


def main():
    """CLI 入口点"""
    cli()


if __name__ == "__main__":
    main()
