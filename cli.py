import argparse
import asyncio
import json
import os
import sys
import logging
import importlib
import locale
from pathlib import Path
import websockets
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TaskProgressColumn
from rich.live import Live
from rich.panel import Panel
from rich.markdown import Markdown
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import PromptSession
from prompt_toolkit.styles import Style
import ctypes
import uuid
import socket
import hashlib

# Diagnostic Logging SETUP
logging.basicConfig(
    filename="ryt_debug.log",
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)

def log_diagnostic(msg, data=None):
    if data is not None:
        logging.debug(f"{msg}: {repr(data)}")
    else:
        logging.debug(msg)

log_diagnostic("CLI STARTUP")
log_diagnostic(f"Platform: {sys.platform}")
log_diagnostic(f"Stdout encoding: {sys.stdout.encoding}")
log_diagnostic(f"Stdout isatty: {sys.stdout.isatty()}")
log_diagnostic(f"Preferred encoding: {locale.getpreferredencoding()}")

def get_user_id():
    """Generates a unique user ID based on hostname and MAC address."""
    try:
        hostname = socket.gethostname()
        mac = uuid.getnode()
        combined = f"{hostname}_{mac}"
        # Return a short hash for better readability in workspace names
        return hashlib.sha256(combined.encode()).hexdigest()[:8]
    except Exception as e:
        log_diagnostic("Error generating user_id", e)
        return "unknown_user"

def enable_win_ansi():
    if sys.platform != "win32":
        return
    
    # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
    # ENABLE_PROCESSED_OUTPUT = 0x0001
    # ENABLE_WRAP_AT_EOL_OUTPUT = 0x0002
    mode_flag = 0x0001 | 0x0002 | 0x0004
    
    kernel32 = ctypes.windll.kernel32
    for handle_id in [-11, -12]: # STDOUT, STDERR
        h = kernel32.GetStdHandle(handle_id)
        if h == -1 or h is None:
            continue
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
            res = kernel32.SetConsoleMode(h, mode.value | mode_flag)
            # log_diagnostic(f"SetConsoleMode ({handle_id}) result: {res}, new mode: {mode.value | mode_flag}")

# Robust UTF-8 encoding for PowerShell
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

enable_win_ansi()
console = Console(highlight=False)

async def listen_to_server(websocket, workspace, response_done: asyncio.Event):
    sync_progress = None
    sync_task_id = None
    live_thought = None
    thought_content = ""
    
    try:
        async for message in websocket:
            log_diagnostic("WS RECV RAW", message)
            try:
                payload = json.loads(message)
                event = payload.get("event")
                
                if event == "sync_status":
                    direction = payload.get("direction", "Syncing")
                    total = payload.get("total", 0)
                    sync_progress = Progress(
                        TextColumn("{task.description}"),
                        BarColumn(bar_width=None),
                        TaskProgressColumn(),
                        console=console,
                        transient=True
                    )
                    sync_progress.start()
                    sync_task_id = sync_progress.add_task(direction, total=total)
                    
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
                    log_diagnostic("AGENT CHUNK", content)
                    sys.stdout.write(content)
                    sys.stdout.flush()
                    
                elif event == "agent_message":
                    content = payload.get("content", "")
                    log_diagnostic("AGENT MESSAGE", content)
                    console.print(f"\n{content}")
                    
                elif event == "agent_tool_call":
                    tool_name = payload.get("tool")
                    args = payload.get("args")
                    log_diagnostic("TOOL CALL", f"{tool_name}({args})")
                    console.print(f"\n[bold cyan]🔧 Tool Call:[/bold cyan] [green]{tool_name}[/green]({args})")
                    
                elif event == "agent_tool_result":
                    log_diagnostic("TOOL RESULT", "completed")
                    console.print(f"[bold blue]✅ Tool Result:[/bold blue] [dim]completed[/dim]")
                    
                elif event == "request_completed":
                    console.print()  # Final newline
                    response_done.set()
                
                elif event == "save_local_file":
                    p = workspace / payload.get("path")
                    p.parent.mkdir(parents=True, exist_ok=True)
                    with open(p, "w", encoding="utf-8") as f:
                        f.write(payload.get("content", ""))
                    
                elif event == "read_local_file":
                    p = workspace / payload.get("path")
                    content = ""
                    if p.exists():
                        with open(p, "r", encoding="utf-8") as f:
                            content = f.read()
                    await websocket.send(json.dumps({
                        "event": "local_file_content",
                        "callback_id": payload.get("callback_id"),
                        "content": content
                    }))
                
                elif event == "list_local_files":
                    p = workspace / payload.get("path")
                    files = []
                    if p.exists() and p.is_dir():
                        files = [f.name for f in p.iterdir() if f.is_file()]
                    await websocket.send(json.dumps({
                        "event": "local_files_list",
                        "callback_id": payload.get("callback_id"),
                        "files": files
                    }))
                
                elif event == "request_input":
                    with patch_stdout():
                        user_input = await console.input(f"[bold yellow]{payload.get('prompt', 'Input: ')}[/bold yellow]")
                    await websocket.send(json.dumps({
                        "event": "request_input_response",
                        "callback_id": payload.get("callback_id"),
                        "content": user_input
                    }))
                
                elif event == "reconnect_request":
                    console.print("[yellow]Server requested reconnection...[/yellow]")
                    await websocket.close()
                    # Reconnect logic is handled by the loop if we wrap run_cli
            except Exception as e:
                # console.print(f"[dim red]Error handling event {event}: {e}[/dim red]")
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
    
    # Check if path exists and is a directory
    if workspace.exists() and not workspace.is_dir():
        console.print(f"[bold red]Error:[/bold red] {workspace} is not a directory.")
        sys.exit(1)
        
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] Could not create workspace directory: {e}")
        sys.exit(1)

    # Permission check: Read, Write, Execute (traversable)
    missing_perms = []
    if not os.access(workspace, os.R_OK): missing_perms.append("Read")
    if not os.access(workspace, os.W_OK): missing_perms.append("Write")
    if not os.access(workspace, os.X_OK): missing_perms.append("Execute/Traverse")
    
    if missing_perms:
        console.print(f"[bold red]Error:[/bold red] Missing permissions for workspace [yellow]{workspace}[/yellow]: {', '.join(missing_perms)}")
        sys.exit(1)

    if url.startswith("http://"):
        url = url.replace("http://", "ws://", 1)
    elif url.startswith("https://"):
        url = url.replace("https://", "wss://", 1)

    user_id = get_user_id()
    workspace_name = os.path.basename(project)
    
    connect_url = f"{url.rstrip('/')}/ws/{workspace_name}?user_id={user_id}"

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
                    if not (rel.startswith(".agent") or "\\.agent" in rel or "/.agent" in rel \
                        or rel.startswith(".cli") or "\\.cli" in rel or "/.cli" in rel):
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
                        if rel.startswith(".agent") or "\\.agent" in rel or "/.agent" in rel \
                            or rel.startswith(".cli") or "\\.cli" in rel or "/.cli" in rel:
                            continue
                        
                        try:
                            mtime = full_path.stat().st_mtime
                            current_files[rel] = mtime
                            if rel not in last_state or mtime > last_state[rel]:
                                files_to_sync.append(rel)
                        except Exception: pass
                
                if files_to_sync:
                    log_diagnostic("STARTING SYNC UI", {"total": len(files_to_sync)})
                    # Show Local -> Server progress bar locally
                    progress = Progress(
                        TextColumn("{task.description}"),
                        BarColumn(bar_width=None),
                        TaskProgressColumn(),
                        console=console,
                        transient=True
                    )
                    with progress:
                        task_id = progress.add_task("Local -> Server", total=len(files_to_sync))
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
    parser.add_argument("--project", default=".", help="Target workspace folder (default: current directory)")
    parser.add_argument("--url", default="ws://localhost:8000", help="Server URL (default: ws://localhost:8000)")
    
    args = parser.parse_args()
    asyncio.run(run_cli(args.project, args.url))

if __name__ == "__main__":
    while True:
        try:
            main()
            break
        except Exception as e:
            import time
            console.print(f"[dim red]CLI error: {e}. Retrying in 1s...[/dim red]")
            time.sleep(1)
