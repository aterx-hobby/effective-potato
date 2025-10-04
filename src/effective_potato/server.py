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
        Tool(
            name="potato_multi_tool_pipeline",
            description="Run a staged pipeline of actions (write_file/mkdir/exec/read_file and other tools) as a single call",
            inputSchema={
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "description": "Ordered list of step objects with a 'type' field",
                        "items": {"type": "object"}
                    },
                    "working_dir": {"type": "string", "description": "Optional workspace-relative working directory for exec steps"},
                    "stop_on_error": {"type": "boolean", "default": True},
                    "extra_env": {"type": "object", "description": "Optional environment variables for the pipeline run"}
                },
                "required": ["steps"],
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
        shot_dir = "/workspace/.agent/screenshots"
        # Create directory and run command
        ts = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        out_name = filename or f"screenshot_{ts}.png"
        out_path = f"{shot_dir}/{out_name}"
        
        cmd = (
            "mkdir -p /workspace/.agent/screenshots && "
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
    
    elif name == "potato_multi_tool_pipeline":
        steps = arguments.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("'steps' must be a non-empty array")
        working_dir = arguments.get("working_dir")
        stop_on_error = bool(arguments.get("stop_on_error", True))
        extra_env = arguments.get("extra_env")
        import json
        # We support both low-level file/exec steps and invoking existing tools by name.
        low_level_types = {"write_file", "mkdir", "exec", "run", "command", "read_file"}

        def flush_low_level(buf: list[dict], acc_results: list[dict]):
            if not buf:
                return
            res = container_manager.run_pipeline(
                steps=buf,
                working_dir=working_dir,
                stop_on_error=stop_on_error,
                extra_env=extra_env,
            )
            # pass through per-step results
            acc_results.extend(res.get("results", []))

        results: list[dict] = []
        buf: list[dict] = []

        for idx, step in enumerate(steps):
            stype = (step.get("type") or "").lower()
            # Low-level steps are accumulated and run in a single container exec
            if stype in low_level_types:
                buf.append(step)
                continue

            # Flush any pending low-level steps before handling a tool step
            flush_low_level(buf, results)
            buf = []

            # Dispatch to known tools
            if stype == "execute_command":
                command = step.get("command")
                if not command:
                    raise ValueError("execute_command step requires 'command'")
                task_id = str(uuid.uuid4())
                exit_code, output = container_manager.execute_command(command, task_id)
                results.append({"index": idx, "type": stype, "exit_code": exit_code, "output": output})
            elif stype == "list_repositories":
                if not container_manager.is_github_available():
                    results.append({"index": idx, "type": stype, "error": "GitHub CLI is not available"})
                else:
                    owner = step.get("owner")
                    limit = step.get("limit", 30)
                    exit_code, output = container_manager.list_repositories(owner=owner, limit=limit)
                    results.append({"index": idx, "type": stype, "exit_code": exit_code, "output": output})
            elif stype == "clone_repository":
                if not container_manager.is_github_available():
                    results.append({"index": idx, "type": stype, "error": "GitHub CLI is not available"})
                else:
                    owner = step.get("owner")
                    repo = step.get("repo")
                    if not owner or not repo:
                        raise ValueError("clone_repository requires 'owner' and 'repo'")
                    exit_code, output = container_manager.clone_repository(owner=owner, repo=repo)
                    results.append({"index": idx, "type": stype, "exit_code": exit_code, "output": output})
            elif stype == "launch_and_screenshot":
                launch_command = step.get("launch_command")
                delay = int(step.get("delay_seconds", 2))
                filename = step.get("filename")
                if not launch_command:
                    raise ValueError("launch_and_screenshot requires 'launch_command'")
                shot_dir = "/workspace/.agent_screenshots"
                import datetime as dt
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
                url = None
                if _public_host and _public_port:
                    url = build_screenshot_url(_public_host, int(_public_port), out_name)
                result_obj = {"index": idx, "type": stype, "exit_code": exit_code, "saved": out_path}
                if url:
                    result_obj["url"] = url
                results.append(result_obj)
            elif stype == "potato_multi_tool_pipeline":
                # Nested pipeline: process recursively by making a sub-call
                sub_steps = step.get("steps")
                if not isinstance(sub_steps, list) or not sub_steps:
                    raise ValueError("nested potato_multi_tool_pipeline requires non-empty 'steps'")
                sub_args = {
                    "steps": sub_steps,
                    "working_dir": step.get("working_dir"),
                    "stop_on_error": step.get("stop_on_error", True),
                    "extra_env": step.get("extra_env"),
                }
                # Reuse the same dispatcher recursively
                sub_result = await call_tool("potato_multi_tool_pipeline", sub_args)  # type: ignore
                # sub_result is a [TextContent], extract JSON text
                try:
                    payload = json.loads(sub_result[0].text)
                except Exception:
                    payload = {"error": "failed to parse nested pipeline result"}
                results.append({"index": idx, "type": stype, "result": payload})
            else:
                raise ValueError(f"Unsupported pipeline step type: {stype}")

        # Flush any trailing low-level steps
        flush_low_level(buf, results)

        return [TextContent(type="text", text=json.dumps({"results": results}, ensure_ascii=False, indent=2))]
    
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
