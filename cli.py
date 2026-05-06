import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
import websockets
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TaskProgressColumn
from rich.live import Live
from rich.panel import Panel
from rich.markdown import Markdown

console = Console(force_terminal=True if not sys.stdout.isatty() else None)

async def listen_to_server(websocket, workspace, response_done: asyncio.Event):
    sync_progress = None
    sync_task_id = None
    live_thought = None
    thought_content = ""
    
    try:
        async for message in websocket:
            try:
                payload = json.loads(message)
                event = payload.get("event")
                
                if event == "sync_status":
                    direction = payload.get("direction", "Syncing")
                    total = payload.get("total", 0)
                    sync_progress = Progress(
                        TextColumn("[bold blue]{task.description}"),
                        BarColumn(),
                        TaskProgressColumn(),
                        console=console,
                        transient=True
                    )
                    sync_progress.start()
                    sync_task_id = sync_progress.add_task(f"[yellow]{direction}", total=total)
                    
                elif event == "project_update":
                    file_path = workspace / payload.get("file_path")
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(payload.get("content", ""))
                    
                    if sync_progress is not None and sync_task_id is not None:
                        sync_progress.advance(sync_task_id)
                        if sync_progress.tasks[sync_task_id].finished:
                            sync_progress.stop()
                            sync_progress = None
                            sync_task_id = None
                            
                elif event == "delete_file":
                    file_path = workspace / payload.get("file_path")
                    if file_path.exists():
                        os.remove(file_path)
                
                elif event == "agent_thought_start":
                    thought_content = ""
                    live_thought = Live(Panel("", title="Thinking", border_style="dim"), console=console, refresh_per_second=10)
                    live_thought.start()
                    
                elif event == "agent_thought_chunk":
                    thought_content += payload.get("content", "")
                    if live_thought:
                        live_thought.update(Panel(thought_content, title="Thinking", border_style="dim"))
                        
                elif event == "agent_thought_end":
                    if live_thought:
                        live_thought.stop()
                        live_thought = None
                    console.print(Panel(thought_content, title="Thought Process", border_style="dim"))
                
                elif event == "agent_message_chunk":
                    content = payload.get("content", "")
                    sys.stdout.write(content)
                    sys.stdout.flush()
                    
                elif event == "agent_message":
                    console.print(f"\n{payload.get('content')}")
                    
                elif event == "agent_tool_call":
                    tool_name = payload.get("tool")
                    args = payload.get("args")
                    console.print(f"\n[bold cyan]🔧 Tool Call:[/bold cyan] [green]{tool_name}[/green]({args})")
                    
                elif event == "agent_tool_result":
                    console.print(f"[bold blue]✅ Tool Result:[/bold blue] [dim]completed[/dim]")
                    
                elif event == "request_completed":
                    console.print()  # Final newline
                    response_done.set()
            except Exception:
                pass
    except websockets.exceptions.ConnectionClosed:
        console.print("[red]Connection closed by server.[/red]")
        response_done.set()
    finally:
        if live_thought:
            live_thought.stop()
        if sync_progress:
            sync_progress.stop()

async def run_cli(project: str, url: str):
    workspace = Path(project).absolute()
    workspace.mkdir(parents=True, exist_ok=True)

    if url.startswith("http://"):
        url = url.replace("http://", "ws://", 1)
    elif url.startswith("https://"):
        url = url.replace("https://", "wss://", 1)

    connect_url = f"{url.rstrip('/')}/ws/{os.path.basename(project)}"

    # The local server doesn't use TLS, so downgrade localhost connections
    if connect_url.startswith("wss://localhost") or connect_url.startswith("wss://127.0.0.1"):
        connect_url = connect_url.replace("wss://", "ws://", 1)
        
    console.print(f"[dim]Connecting to {connect_url}...[/dim]")

    try:
        async with websockets.connect(connect_url) as websocket:
            console.print("[bold green]Connected![/bold green] Initializing workspace sync...")
            last_state = {}
            # Initial state capture
            for root, _, files in os.walk(workspace):
                for file in files:
                    full_path = Path(root) / file
                    rel = os.path.relpath(full_path, workspace)
                    if not (rel.startswith(".agent") or "\\.agent" in rel or "/.agent" in rel):
                        try:
                            last_state[rel] = full_path.stat().st_mtime
                        except: pass

            async def sync_workspace():
                nonlocal last_state
                current_files = {}
                files_to_sync = []
                
                for root, _, files in os.walk(workspace):
                    for file in files:
                        full_path = Path(root) / file
                        rel = os.path.relpath(full_path, workspace)
                        if rel.startswith(".agent") or "\\.agent" in rel or "/.agent" in rel:
                            continue
                        
                        try:
                            mtime = full_path.stat().st_mtime
                            current_files[rel] = mtime
                            if rel not in last_state or mtime > last_state[rel]:
                                files_to_sync.append(rel)
                        except Exception: pass
                
                if files_to_sync:
                    # Show Local -> Server progress bar locally
                    progress = Progress(
                        TextColumn("[bold blue]{task.description}"),
                        BarColumn(),
                        TaskProgressColumn(),
                        console=console,
                        transient=True
                    )
                    with progress:
                        task_id = progress.add_task("[yellow]Local -> Server", total=len(files_to_sync))
                        for rel in files_to_sync:
                            try:
                                full_path = workspace / rel
                                with open(full_path, 'r', encoding='utf-8') as f:
                                    content = f.read()
                                
                                await websocket.send(json.dumps({
                                    "event": "project_update",
                                    "file_path": str(rel),
                                    "content": content,
                                    "action": "modified"
                                }))
                            except Exception: pass
                            progress.advance(task_id)
                
                # Check for deletions
                for rel in list(last_state.keys()):
                    if rel not in current_files:
                        try:
                            await websocket.send(json.dumps({
                                "event": "delete_file",
                                "file_path": str(rel)
                            }))
                        except Exception: pass
                            
                last_state = current_files

            response_done = asyncio.Event()
            listen_task = asyncio.create_task(listen_to_server(websocket, workspace, response_done))
            
            # Perform initial sync
            await sync_workspace()
            await websocket.send(json.dumps({"event": "initial_sync_complete"}))
            
            from prompt_toolkit.patch_stdout import patch_stdout
            from prompt_toolkit.shortcuts import PromptSession
            from prompt_toolkit.styles import Style
            
            style = Style.from_dict({
                'prompt': '#00ff00 bold',
            })
            
            session = PromptSession(style=style)
            console.print(Markdown("# Ryt is online"))
            console.print("[dim]Type 'exit' to quit.[/dim]\n")
            
            while True:
                with patch_stdout():
                    try:
                        user_in = await session.prompt_async("ryt> ")
                        if user_in.strip().lower() in ("exit", "quit"):
                            break
                        if user_in.strip():
                            response_done.clear()
                            await sync_workspace()
                            await websocket.send(json.dumps({
                                "event": "command_execution",
                                "content": user_in.strip()
                            }))
                            await response_done.wait()
                    except (EOFError, KeyboardInterrupt):
                        break
            
            listen_task.cancel()
    except Exception as e:
        console.print(f"[bold red]Fatal Error:[/bold red] {e}")

def main():
    parser = argparse.ArgumentParser(description="Ryt CLI")
    parser.add_argument("--project", required=True, help="Target workspace folder")
    parser.add_argument("--url", required=True, help="Server URL (e.g. ws://localhost:8000)")
    
    args = parser.parse_args()
    asyncio.run(run_cli(args.project, args.url))

if __name__ == "__main__":
    main()
