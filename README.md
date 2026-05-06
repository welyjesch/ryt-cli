# Ryt CLI

Ryt CLI is the client-side component of the Ryt agentic coding system. It provides a real-time, interactive terminal interface for communicating with the Ryt server and synchronizing workspace files.

## Features

- **Bidirectional Workspace Sync**: Automatically synchronizes your local project folder with the server workspace.
- **Real-time Streaming**: Displays agent "thoughts" and messages as they are generated.
- **Tool Transparency**: Visualizes tool calls and their results in real-time.
- **Progress Tracking**: Shows progress bars for file synchronization (Server -> Local and Local -> Server).
- **Interactive Interface**: Premium terminal UI built with `rich` and `prompt_toolkit`.

## Usage

Run the CLI using `uv`:

```bash
uv run cli.py --project <path_to_project> --url <server_url>
```

### Arguments

- `--project`: The local directory to use as the workspace. It will be created if it doesn't exist.
- `--url`: The URL of the Ryt server (e.g., `http://localhost:8000`).

## How it Works

1. **Initial Sync**: On startup, the CLI scans the local directory and sends all files to the server. It also receives any files already present on the server that are missing locally.
2. **Command Execution**: When you type a command, the CLI ensures all local changes are synced to the server before the agent processes your request.
3. **Event Stream**: The CLI listens for WebSocket events from the server, including:
    - `sync_status`: Progress updates for file transfers.
    - `project_update` / `delete_file`: File synchronization commands.
    - `agent_thought_*`: Streaming of the agent's internal reasoning.
    - `agent_message_*`: Streaming of the agent's response.
    - `agent_tool_*`: Reporting of tool execution.
