"""MCP server for effective-potato."""

import logging
import uuid
from typing import Any
from mcp.server import Server
from mcp.types import Tool, TextContent
from pydantic import AnyUrl

from .container import ContainerManager
from .web import (
    create_app as create_http_app,
    start_http_server,
    get_server_config,
    build_screenshot_url,
)

logger = logging.getLogger(__name__)


# Initialize the MCP server
app = Server("effective-potato")

# Container manager instance
container_manager: ContainerManager | None = None
_http_thread = None
_public_host = None
_public_port = None


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    tools = [
        Tool(
            name="execute_command",
            description="Execute a command in the sandboxed Ubuntu container",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute in the container",
                    }
                },
                "required": ["command"],
            },
        ),
        Tool(
            name="launch_and_screenshot",
            description="Launch an X11 application and take a fullscreen screenshot via xfce4-screenshooter",
            inputSchema={
                "type": "object",
                "properties": {
                    "launch_command": {
                        "type": "string",
                        "description": "Command to launch the X11 app (e.g., 'xclock' or '/usr/bin/gedit')",
                    },
                    "delay_seconds": {
                        "type": "integer",
                        "description": "Seconds to wait after launching before screenshot",
                        "default": 2
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional filename for the screenshot (png). When omitted, a timestamped name is used.",
                    }
                },
                "required": ["launch_command"],
            },
        ),
    ]
    
    # Add GitHub tools if GitHub CLI is available
    if container_manager and container_manager.is_github_available():
        tools.extend([
            Tool(
                name="list_repositories",
                description="List GitHub repositories for a user or the authenticated user",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "owner": {
                            "type": "string",
                            "description": "The username or organization to list repos for. If not provided, lists repos for the authenticated user.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of repositories to list (default: 30)",
                            "default": 30,
                        }
                    },
                },
            ),
            Tool(
                name="clone_repository",
                description="Clone a GitHub repository into the workspace directory",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "owner": {
                            "type": "string",
                            "description": "The repository owner (username or organization)",
                        },
                        "repo": {
                            "type": "string",
                            "description": "The repository name",
                        }
                    },
                    "required": ["owner", "repo"],
                },
            ),
        ])
    
    return tools


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls."""
    if not container_manager:
        raise RuntimeError("Container manager not initialized")

    if name == "execute_command":
        command = arguments.get("command")
        if not command:
            raise ValueError("Command is required")

        # Generate unique task ID
        task_id = str(uuid.uuid4())

        # Execute the command
        exit_code, output = container_manager.execute_command(command, task_id)

        # Format response
        response = f"Exit code: {exit_code}\n\nOutput:\n{output}"

        return [TextContent(type="text", text=response)]
    
    elif name == "list_repositories":
        if not container_manager.is_github_available():
            raise RuntimeError("GitHub CLI is not available. Set GITHUB_PERSONAL_ACCESS_TOKEN in local/.env")
        
        owner = arguments.get("owner")
        limit = arguments.get("limit", 30)
        
        # Execute the list repositories command
        exit_code, output = container_manager.list_repositories(owner=owner, limit=limit)
        
        # Format response
        response = f"Exit code: {exit_code}\n\nOutput:\n{output}"
        
        return [TextContent(type="text", text=response)]
    
    elif name == "clone_repository":
        if not container_manager.is_github_available():
            raise RuntimeError("GitHub CLI is not available. Set GITHUB_PERSONAL_ACCESS_TOKEN in local/.env")
        
        owner = arguments.get("owner")
        repo = arguments.get("repo")
        
        if not owner or not repo:
            raise ValueError("Both 'owner' and 'repo' are required")
        
        # Execute the clone repository command
        exit_code, output = container_manager.clone_repository(owner=owner, repo=repo)
        
        # Format response
        response = f"Exit code: {exit_code}\n\nOutput:\n{output}"
        
        return [TextContent(type="text", text=response)]
    
    elif name == "launch_and_screenshot":
        launch_command = arguments.get("launch_command")
        delay = int(arguments.get("delay_seconds", 2))
        filename = arguments.get("filename")
        if not launch_command:
            raise ValueError("'launch_command' is required")
        
        # Build the script to launch the app and screenshot
        import time
        import datetime as dt
        shot_dir = "/workspace/.agent_screenshots"
        # Create directory and run command
        ts = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        out_name = filename or f"screenshot_{ts}.png"
        out_path = f"{shot_dir}/{out_name}"
        
        cmd = (
            "mkdir -p /workspace/.agent_screenshots && "
            f"({launch_command}) >/tmp/launch.log 2>&1 & "
            f"sleep {delay}; "
            "export DISPLAY=:0; "
            "xdotool key XF86Refresh >/dev/null 2>&1 || true; "
            f"xfce4-screenshooter -f -s '{out_path}'"
        )
        task_id = str(uuid.uuid4())
        exit_code, output = container_manager.execute_command(cmd, task_id)
        # Build URL if web server is running
        if _public_host and _public_port:
            # The filename for URL is relative to screenshots dir
            fname = out_name
            url = build_screenshot_url(_public_host, int(_public_port), fname)
            render_hint = f"To render the screenshot in chat, embed this URL as an image: ![screenshot]({url})"
            saved_line = f"Saved: {out_path}\nURL: {url}\n{render_hint}"
        else:
            saved_line = f"Saved: {out_path}"
        response = f"Exit code: {exit_code}\n{saved_line}\n\nOutput:\n{output}"
        return [TextContent(type="text", text=response)]
    
    else:
        raise ValueError(f"Unknown tool: {name}")


def initialize_server() -> None:
    """Initialize the server and container."""
    global container_manager

    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger.info("Initializing effective-potato MCP server...")

    # Create container manager
    container_manager = ContainerManager()

    # Build and start container
    container_manager.build_image()
    container_manager.start_container()

    # Start HTTP server for screenshots and future endpoints
    from pathlib import Path
    bind_ip, port, public_host = get_server_config()
    http_app = create_http_app(Path(container_manager.workspace_dir))
    thread = start_http_server(http_app, bind_ip, port)

    # Keep references for URL building
    global _http_thread, _public_host, _public_port
    _http_thread = thread
    _public_host = public_host
    _public_port = port

    logger.info("Server initialized successfully")


def cleanup_server() -> None:
    """Clean up server resources."""
    global container_manager

    if container_manager:
        logger.info("Cleaning up server...")
        container_manager.cleanup()
        container_manager = None


def main() -> None:
    """Main entry point for the server."""
    import asyncio
    from mcp.server.stdio import stdio_server

    try:
        initialize_server()

        # Run the server
        async def run():
            async with stdio_server() as (read_stream, write_stream):
                await app.run(
                    read_stream,
                    write_stream,
                    app.create_initialization_options(),
                )

        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    finally:
        cleanup_server()


if __name__ == "__main__":
    main()
