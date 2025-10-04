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
    get_tool_schema_url,
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
            description=(
                "Last resort: execute a raw bash command in the sandboxed container (supports optional timeout). "
                "Prefer dedicated published tools when available; avoid crafting custom commands unless necessary."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute in the container",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional time to wait for completion before prompting (default: 120 seconds). The process will continue running if the timeout elapses.",
                        "default": 120
                    }
                },
                "required": ["command"],
            },
        ),
        Tool(
            name="launch_and_screenshot",
            description=(
                "Self-contained: launches the process and captures a fullscreen screenshot via xfce4-screenshooter. "
                "Do not pre-launch the app outside this tool; provide everything needed here. "
                "Hint: when launching Python programs, activate the appropriate virtualenv as part of launch_command (e.g., 'source .venv/bin/activate && python app.py')."
            ),
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
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "Optional workspace-relative directory to cd into before launching (relative to /workspace)",
                    },
                    "env": {
                        "type": "object",
                        "description": "Optional environment variables to export for the launched process",
                        "additionalProperties": {"type": "string"}
                    }
                },
                "required": ["launch_command"],
            },
        ),
        # Note: The former multi-tool pipeline is deprecated and intentionally not exposed.
        Tool(
            name="potato_workspace_list_repositories",
            description="List repositories tracked in the locally deployed effective-potato workspace (.agent/track_repos.json) and whether their directories currently exist",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="potato_get_toolset_schema",
            description="Return a URL to an OpenAPI schema for the available tools. The LLM should use this URL to understand tool descriptions.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="potato_workspace_mouse_actions",
            description=(
                "Move the mouse, click (mousedown/mouseup), or get mouse location using xdotool. "
                "Optionally focus a window by title before performing actions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {"type": "string", "description": "Optional substring to match and focus via xdotool search --any"},
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ["move", "move_relative", "mousedown", "mouseup", "get_location"]},
                                "x": {"type": "integer"},
                                "y": {"type": "integer"},
                                "dx": {"type": "integer"},
                                "dy": {"type": "integer"},
                                "button": {"type": "integer", "description": "Mouse button number for down/up"},
                                "sync": {"type": "boolean", "default": False},
                                "clearmodifiers": {"type": "boolean", "default": False},
                                "screen": {"type": "integer"},
                                "window_id": {"type": "string"},
                                "polar": {"type": "boolean", "default": False},
                                "shell": {"type": "boolean", "default": False},
                                "prefix": {"type": "string"}
                            },
                            "required": ["type"],
                        },
                        "description": "Ordered list of mouse actions to perform"
                    }
                },
                "required": ["actions"],
            },
        ),
        Tool(
            name="potato_workspace_find",
            description=(
                "Context discovery: search the workspace (or a subdirectory) while excluding .git and directories "
                "containing 'venv' or '_env' to avoid polluted results. Use this to find code and docs, not venvs. "
                "Path should be workspace-relative; absolute paths starting with '/workspace' are accepted and normalized. "
                "Optional filters: name (glob) and type (file/dir)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Optional workspace-relative path to search under (default: '.')"},
                    "name": {"type": "string", "description": "Optional glob to match names (e.g., '*.py')"},
                    "type": {"type": "string", "enum": ["any", "file", "dir"], "default": "any"}
                }
            },
        ),
        Tool(
            name="potato_workspace_find_venvs",
            description=(
                "Locate Python virtual environments (venvs) within the workspace (or a subdirectory). "
                "Looks for markers like 'pyvenv.cfg' or 'bin/activate'. Excludes .git, but DOES NOT exclude venv directories. "
                "Use this when you specifically want to find venv roots."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Optional workspace-relative path to search under (default: '.')"}
                }
            },
        ),
        Tool(
            name="potato_workspace_interact_and_record",
            description=(
                "Focus a window by title, send a sequence of key inputs, and record the desktop as a sequence of screenshots "
                "captured every ~500ms. At the end of the capture window, compile frames into a webm."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {"type": "string", "description": "Substring to match the target window title (xdotool search --any)"},
                    "inputs": {
                        "type": "array",
                        "description": "Ordered list of key sequences to send (xdotool key syntax). Each item may include 'keys' and optional 'delay_ms'.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "keys": {"type": "string"},
                                "delay_ms": {"type": "integer", "default": 100}
                            },
                            "required": ["keys"]
                        }
                    },
                    "duration_seconds": {"type": "integer", "description": "Capture window length (default 30)", "default": 30},
                    "frame_interval_ms": {"type": "integer", "description": "Capture interval in milliseconds (default 500)", "default": 500},
                    "output_basename": {"type": "string", "description": "Base filename (without extension) for output video and frames", "default": "session"}
                },
                "required": ["window_title", "inputs"],
            },
        ),
    ]
    
    # Add GitHub tools if GitHub CLI is available
    if container_manager and container_manager.is_github_available():
        tools.extend([
            Tool(
                name="github_list_repositories",
                description="List repositories available on github.com in owner/repo format, for a given owner or the authenticated user (public or accessible via SSH key/GH_TOKEN).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "owner": {
                            "type": "string",
                            "description": "Username or organization to list repos for. If omitted, lists for the authenticated user.",
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
        import threading
        import time as _time
        command = arguments.get("command")
        if not command:
            raise ValueError("Command is required")

        # Generate unique task ID
        task_id = str(uuid.uuid4())

        # Optional timeout for waiting on the command (defaults to 120s)
        try:
            timeout_s = int(arguments.get("timeout_seconds", 120))
        except Exception:
            timeout_s = 120

        result_holder: dict[str, Any] = {}

        def _worker():
            try:
                code, out = container_manager.execute_command(command, task_id)
                result_holder["exit_code"] = code
                result_holder["output"] = out
            except Exception as e:
                result_holder["error"] = str(e)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=timeout_s)

        if t.is_alive():
            # Don't cancel; just inform the user and return
            prompt = (
                f"Command is still running after {timeout_s}s. Task ID: {task_id}.\n"
                "The process continues in the container. If you want to keep waiting, call execute_command again "
                "with a larger timeout_seconds. If you don't want to wait, you can proceed with other tasks."
            )
            return [TextContent(type="text", text=prompt)]
        else:
            if "error" in result_holder:
                return [TextContent(type="text", text=f"Error: {result_holder['error']}")]
            exit_code = result_holder.get("exit_code")
            output = result_holder.get("output", "")
            response = f"Exit code: {exit_code}\n\nOutput:\n{output}"
            return [TextContent(type="text", text=response)]
    
    elif name == "github_list_repositories":
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
        working_dir = arguments.get("working_dir")
        env_map = arguments.get("env") or {}
        if not launch_command:
            raise ValueError("'launch_command' is required")
        
        # Build the script to launch the app and screenshot
        import time
        import datetime as dt
        shot_dir = "/workspace/.agent/screenshots"
        # Create directory and run command
        ts = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S")
        out_name = filename or f"screenshot_{ts}.png"
        out_path = f"{shot_dir}/{out_name}"
        
        # Prepare optional env exports and working directory change
        export_snippets = []
        if isinstance(env_map, dict):
            for k, v in env_map.items():
                try:
                    ks = str(k)
                    vs = str(v).replace("'", "'\\''")
                    export_snippets.append(f"export {ks}='{vs}'")
                except Exception:
                    continue
        exports = ("; ".join(export_snippets) + "; ") if export_snippets else ""

        cd_snippet = ""
        if working_dir:
            wd = str(working_dir).replace("'", "'\\''")
            cd_snippet = f"cd /workspace && cd -- '{wd}' && "

        cmd = (
            "mkdir -p /workspace/.agent/screenshots && "
            f"{cd_snippet}{exports}"
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
    
    elif name == "potato_workspace_multi_tool_pipeline":
        # Deprecated: no longer exposed. Provide a clear deprecation message.
        msg = (
            "The multi-tool pipeline (potato_workspace_multi_tool_pipeline) is deprecated and no longer exposed. "
            "Invoke individual tools directly in sequence instead."
        )
        return [TextContent(type="text", text=msg)]
    elif name == "potato_workspace_interact_and_record":
        import json
        window_title = arguments.get("window_title")
        inputs = arguments.get("inputs") or []
        duration = int(arguments.get("duration_seconds", 30))
        interval = int(arguments.get("frame_interval_ms", 500))
        base = arguments.get("output_basename", "session")
        if not window_title or not isinstance(inputs, list) or not inputs:
            raise ValueError("'window_title' and non-empty 'inputs' are required")

        # Build a script that focuses the window, sends inputs, and records frames using xfce4-screenshooter
        # Frames saved to /workspace/.agent/screenshots/<base>_frames/frame_%06d.png
        frames_dir = f"/workspace/.agent/screenshots/{base}_frames"
        video_out = f"/workspace/.agent/screenshots/{base}.webm"
        pattern = window_title.replace("'", "'\\''")
        script_lines = [
            "set -e",
            f"mkdir -p '{frames_dir}'",
            # Find target window id (first match)
            f"win_id=\"$(xdotool search --any '{pattern}' | head -n1)\"",
            "test -n \"$win_id\" || { echo 'window not found' >&2; exit 1; }",
            # Focus window
            "xdotool windowactivate $win_id && xdotool windowfocus $win_id",
            # Start background frame capture loop
            "( \n"
            "  idx=0; \n"
            "  start=$(date +%s%3N); \n"
            f"  end=$((start + {duration}*1000)); \n"
            "  while [ $(date +%s%3N) -lt $end ]; do \n"
            f"    xfce4-screenshooter -f -s '{frames_dir}/frame_$(printf %06d $idx).png' >/dev/null 2>&1; \n"
            f"    idx=$((idx+1)); sleep {max(1, interval)//1000}.{(interval%1000):03d}; \n"
            "  done \n"
            ") & cap_pid=$!",
        ]

        # Append input sending as discrete key events with optional delays
        for item in inputs:
            keys = item.get("keys")
            delay_ms = int(item.get("delay_ms", 100)) if isinstance(item, dict) else 100
            if not keys:
                continue
            esc_keys = str(keys)
            script_lines.append(
                f"xdotool key --clearmodifiers --delay {delay_ms} --repeat 0 --repeat-delay 0 --window $win_id {esc_keys} || true"
            )

        # Wait for capture loop to finish, then compile frames into a webm using ffmpeg (libvpx-vp9)
        script_lines.extend([
            "wait $cap_pid || true",
            f"ffmpeg -y -framerate $((1000/{max(1, interval)})) -pattern_type glob -i '{frames_dir}/frame_*.png' -c:v libvpx-vp9 -pix_fmt yuv420p '{video_out}' >/dev/null 2>&1 || true",
            "echo OUTPUT_VIDEO:\n" + video_out,
        ])

        full_script = "\n".join(script_lines)
        task_id = str(uuid.uuid4())
        exit_code, output = container_manager.execute_command(full_script, task_id)

        # Provide JSON-like response including output paths and, if available, URL to video
        payload = {"exit_code": exit_code, "frames_dir": frames_dir, "video": video_out}
        if _public_host and _public_port:
            from urllib.parse import quote as _q
            from .web import build_screenshot_url as _b
            # Reuse screenshots route to serve the video file
            fname = f"{base}.webm"
            payload["video_url"] = _b(_public_host, int(_public_port), fname)
        return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]
    elif name == "potato_workspace_mouse_actions":
        import json
        window_title = arguments.get("window_title")
        actions = arguments.get("actions") or []
        if not isinstance(actions, list) or not actions:
            raise ValueError("'actions' must be a non-empty array")

        lines = ["set -e"]
        # Optionally focus window by title (first match)
        if window_title:
            pattern = str(window_title).replace("'", "'\\''")
            lines += [
                f"win_id=\"$(xdotool search --any '{pattern}' | head -n1)\"",
                "test -n \"$win_id\" || { echo 'window not found' >&2; exit 1; }",
                "xdotool windowactivate $win_id && xdotool windowfocus $win_id",
            ]
        else:
            lines += ["win_id="]

        def flag(b: bool, name: str) -> str:
            return f" --{name}" if b else ""

        for idx, act in enumerate(actions):
            atype = (act.get("type") or "").lower()
            if atype == "move":
                x = int(act.get("x"))
                y = int(act.get("y"))
                sync = bool(act.get("sync", False))
                cm = bool(act.get("clearmodifiers", False))
                screen = act.get("screen")
                win = act.get("window_id") or "$win_id"
                cmd = f"xdotool mousemove{flag(cm, 'clearmodifiers')}{flag(sync, 'sync')}"
                if screen is not None:
                    cmd += f" --screen {int(screen)}"
                if win:
                    cmd += f" --window {win}"
                cmd += f" {x} {y}"
                lines.append(cmd)
            elif atype == "move_relative":
                dx = int(act.get("dx"))
                dy = int(act.get("dy"))
                sync = bool(act.get("sync", False))
                cm = bool(act.get("clearmodifiers", False))
                polar = bool(act.get("polar", False))
                cmd = f"xdotool mousemove_relative{flag(cm, 'clearmodifiers')}{flag(sync, 'sync')}{flag(polar, 'polar')}"
                # To support negative numbers with getopt parsing, add '--' before coordinates
                cmd += f" -- {dx} {dy}"
                lines.append(cmd)
            elif atype == "mousedown":
                btn = int(act.get("button", 1))
                cm = bool(act.get("clearmodifiers", False))
                win = act.get("window_id") or "$win_id"
                cmd = f"xdotool mousedown{flag(cm, 'clearmodifiers')}"
                if win:
                    cmd += f" --window {win}"
                cmd += f" {btn}"
                lines.append(cmd)
            elif atype == "mouseup":
                btn = int(act.get("button", 1))
                cm = bool(act.get("clearmodifiers", False))
                win = act.get("window_id") or "$win_id"
                cmd = f"xdotool mouseup{flag(cm, 'clearmodifiers')}"
                if win:
                    cmd += f" --window {win}"
                cmd += f" {btn}"
                lines.append(cmd)
            elif atype == "get_location":
                shell = bool(act.get("shell", False))
                prefix = act.get("prefix")
                cmd = "xdotool getmouselocation"
                if shell:
                    cmd += " --shell"
                if prefix:
                    pf = str(prefix).replace("'", "'\\''")
                    cmd += f" --prefix '{pf}'"
                lines.append(cmd)
            else:
                raise ValueError(f"Unsupported mouse action type: {atype}")

        script = "\n".join(lines)
        task_id = str(uuid.uuid4())
        exit_code, output = container_manager.execute_command(script, task_id)
        return [TextContent(type="text", text=f"Exit code: {exit_code}\n\nOutput:\n{output}")]
    elif name == "potato_workspace_find":
        # Validate workspace-relative path
        subpath = arguments.get("path") or "."
        if not isinstance(subpath, str):
            raise ValueError("'path' must be a string if provided")
        # Prevent absolute paths or traversal; resolve within workspace via container-side cd
        # Build a safe command using bash to cd into /workspace and then into the relative path.
        raw = str(subpath).strip()
        if raw == "/workspace":
            rel = "."
        elif raw.startswith("/workspace/"):
            rel = raw[len("/workspace/"):]
            if not rel:
                rel = "."
        elif raw.startswith("/"):
            raise ValueError("Absolute paths outside /workspace are not allowed; provide a workspace-relative path or one under /workspace")
        else:
            rel = raw
        # Options
        name_pat = arguments.get("name")
        ftype = (arguments.get("type") or "any").lower()
        type_clause = ""
        if ftype == "file":
            type_clause = "-type f"
        elif ftype == "dir":
            type_clause = "-type d"
        name_clause = ""
        if isinstance(name_pat, str) and name_pat:
            esc = name_pat.replace("'", "'\\''")
            name_clause = f"-name '{esc}'"

        # find with escaped parentheses for prune rules: skip .git, *venv*, *_env*
        prune = "\\( -name .git -o -name '*venv*' -o -name '*_env*' \\) -prune"
        # Combine filters for the non-pruned branch
        filters = " ".join([c for c in [type_clause, name_clause] if c])
        if filters:
            filters = " " + filters
        find_cmd = (
            "cd /workspace && "
            f"cd -- '{rel.replace("'", "'\\''")}' && "
            f"find . -type d {prune} -o{filters} -print"
        )
        task_id = str(uuid.uuid4())
        exit_code, output = container_manager.execute_command(find_cmd, task_id)
        return [TextContent(type="text", text=f"Exit code: {exit_code}\n\nOutput:\n{output}")]
    elif name == "potato_workspace_find_venvs":
        subpath = arguments.get("path") or "."
        if not isinstance(subpath, str):
            raise ValueError("'path' must be a string if provided")
        raw = str(subpath).strip()
        if raw == "/workspace":
            rel = "."
        elif raw.startswith("/workspace/"):
            rel = raw[len("/workspace/"):]
            if not rel:
                rel = "."
        elif raw.startswith("/"):
            raise ValueError("Absolute paths outside /workspace are not allowed; provide a workspace-relative path or one under /workspace")
        else:
            rel = raw

        # Search for common venv markers; exclude .git but do NOT exclude venv-like directories
        # A venv root typically has either 'pyvenv.cfg' or 'bin/activate' present
        find_cmd = (
            "cd /workspace && "
            f"cd -- '{rel.replace("'", "'\\''")}' && "
            "find . -type d -name .git -prune -o "
            "( -type f -name 'pyvenv.cfg' -o -path '*/bin/activate' ) -print"
        )
        task_id = str(uuid.uuid4())
        exit_code, output = container_manager.execute_command(find_cmd, task_id)
        return [TextContent(type="text", text=f"Exit code: {exit_code}\n\nOutput:\n{output}")]
    elif name == "potato_workspace_list_repositories":
        import json
        items = container_manager.list_local_repositories()
        return [TextContent(type="text", text=json.dumps({"items": items}, ensure_ascii=False, indent=2))]
    elif name == "potato_get_toolset_schema":
        import json
        import urllib.request
        import urllib.error
        url = get_tool_schema_url()
        payload: dict = {"url": url}
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = getattr(resp, "status", resp.getcode())
                raw = resp.read()
                # Best-effort charset detection
                charset = None
                try:
                    charset = resp.headers.get_content_charset()  # type: ignore[attr-defined]
                except Exception:
                    pass
                text = raw.decode(charset or "utf-8", errors="replace")
                try:
                    schema_obj = json.loads(text)
                    payload.update({
                        "fetched": True,
                        "status": int(status) if status is not None else None,
                        "openapi": schema_obj,
                        "note": "Fetched OpenAPI schema; use this content for tool descriptions.",
                    })
                except Exception:
                    payload.update({
                        "fetched": True,
                        "status": int(status) if status is not None else None,
                        "raw": text,
                        "note": "Fetched schema but could not parse JSON; raw text included.",
                    })
        except urllib.error.URLError as e:
            payload.update({
                "fetched": False,
                "error": str(e),
                "note": "Schema URL unreachable; use the URL to retrieve the OpenAPI schema yourself.",
            })
        return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]
    
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

    # On startup, repair/cleanup the local tracked repos list by removing entries whose directories are missing
    try:
        container_manager.prune_tracked_repositories(dry_run=False)
    except Exception as e:
        logger.warning(f"Workspace prune on startup failed: {e}")

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
