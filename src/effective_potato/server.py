"""MCP server for effective-potato."""

import logging
import uuid
from typing import Any
from mcp.server import Server
from mcp.types import Tool, TextContent
from pydantic import AnyUrl
from pydantic import BaseModel, Field

from .container import ContainerManager
from .web import (
    create_app as create_http_app,
    start_http_server,
    get_server_config,
    build_screenshot_url,
    get_tool_schema_url,
    record_tool_metric,
)

logger = logging.getLogger(__name__)
# ---------------------------
# Pydantic models (typed schemas)
# ---------------------------

class ScreenshotInput(BaseModel):
    filename: str | None = Field(default=None, description="Optional filename for the screenshot (png)")
    delay_seconds: int = Field(default=0, ge=0)


class PythonRunModuleInput(BaseModel):
    venv_path: str = Field(description="Workspace-relative path to the venv root")
    module: str = Field(description="Python module name to run")
    args: list[str] = Field(default_factory=list)


class PythonRunScriptInput(BaseModel):
    venv_path: str = Field(description="Workspace-relative path to the venv root")
    script_path: str = Field(description="Workspace-relative path to the script")
    args: list[str] = Field(default_factory=list)


class TarCreateInput(BaseModel):
    base_dir: str = Field(default=".", description="Workspace-relative directory to run tar from")
    items: list[str] = Field(description="Relative paths (files/dirs) to include in archive")
    archive_name: str | None = Field(default=None, description="Optional archive name (defaults to timestamped)")


class DigestInput(BaseModel):
    path: str = Field(description="Workspace-relative path to file to hash")
    algorithm: str = Field(default="sha256", description="Hash algorithm: sha256 or md5")


class LaunchAndScreenshotInput(BaseModel):
    launch_command: str = Field(description="Command to launch (e.g., 'xclock')")
    delay_seconds: int = Field(default=2, ge=0)
    filename: str | None = Field(default=None)
    working_dir: str | None = Field(default=None, description="Workspace-relative directory to cd into")
    env: dict[str, str] | None = Field(default=None)


def _schema(model: type[BaseModel]) -> dict:
    # Pydantic v2 schema
    return model.model_json_schema()


# Initialize the MCP server
app = Server("effective-potato")

# Container manager instance
container_manager: ContainerManager | None = None
_http_thread = None
_public_host = None
_public_port = None


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools (slim set)."""
    tools: list[Tool] = []

    # Workspace: execute raw command (last resort)
    tools.append(
        Tool(
            name="workspace_execute_command",
            description=(
                "Execute a bash command in the sandboxed container with an optional wait timeout."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Bash command to execute in the container"},
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional time to wait before returning (default: 120). Process keeps running.",
                        "default": 120,
                    },
                    "env": {"type": "object", "additionalProperties": {"type": "string"}},
                },
                "required": ["command"],
            },
        )
    )

    # Workspace: launch app and screenshot
    tools.append(
        Tool(
            name="workspace_launch_and_screenshot",
            description=(
                "Launch an app and then capture a fullscreen screenshot."
            ),
            inputSchema=_schema(LaunchAndScreenshotInput),
        )
    )

    # Workspace: screenshot only (decoupled from launch)
    tools.append(
        Tool(
            name="workspace_screenshot",
            description="Capture a fullscreen screenshot and save it under the workspace .agent/screenshots directory.",
            inputSchema=_schema(ScreenshotInput),
        )
    )

    # Task lifecycle controls
    tools.append(
        Tool(
            name="workspace_task_start",
            description="Start a long-running command in the background and get a task_id",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "env": {"type": "object", "additionalProperties": {"type": "string"}},
                },
                "required": ["command"],
            },
        )
    )
    tools.append(
        Tool(
            name="workspace_task_status",
            description="Poll task status by task_id",
            inputSchema={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        )
    )
    tools.append(
        Tool(
            name="workspace_task_kill",
            description="Terminate a task by task_id with a signal (default TERM)",
            inputSchema={
                "type": "object",
                "properties": {"task_id": {"type": "string"}, "signal": {"type": "string", "default": "TERM"}},
                "required": ["task_id"],
            },
        )
    )

    # Python runner & venv selection
    tools.append(
        Tool(
            name="workspace_python_run_module",
            description="Run 'python -m <module>' using a specified virtualenv without activating it.",
            inputSchema=_schema(PythonRunModuleInput),
        )
    )
    tools.append(
        Tool(
            name="workspace_python_run_script",
            description="Run a Python script file using a specified virtualenv without activating it.",
            inputSchema=_schema(PythonRunScriptInput),
        )
    )

    # Note: OpenWeb scripts are intentionally NOT exposed as MCP tools to avoid easy tampering.

    # Workspace: list tracked repos
    tools.append(
        Tool(
            name="workspace_list_repositories",
            description="List repositories tracked in the workspace and whether their directories exist",
            inputSchema={"type": "object", "properties": {}},
        )
    )

    # Workspace: search files (context) and venv roots
    tools.append(
        Tool(
            name="workspace_find",
            description=(
                "Search the workspace or a subdirectory, pruning .git and venv-like directories. Supports name glob and type filter."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": ["any", "file", "dir"], "default": "any"},
                },
            },
        )
    )
    tools.append(
        Tool(
            name="workspace_select_venv",
            description=(
                "Select the best virtualenv path from candidates using simple heuristics (.venv preferred, then venv, then *_env*, then env; tie-breakers by depth, then parent name length)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "paths": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["paths"],
            },
        )
    )
    tools.append(
        Tool(
            name="workspace_find_venvs",
            description="Find virtualenv roots by looking for pyvenv.cfg or bin/activate (prunes .git only)",
            inputSchema={"type": "object", "properties": {"path": {"type": "string"}}},
        )
    )

    # (workspace_select_venv listed earlier)

    # Workspace: read/write files
    tools.append(
        Tool(
            name="workspace_read_file",
            description="Read a file from the workspace",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative path"},
                    "binary": {"type": "boolean", "default": False},
                },
                "required": ["path"],
            },
        )
    )
    # Workspace utilities: tar and digest
    tools.append(
        Tool(
            name="workspace_tar_create",
            description="Create a .tar.gz archive from workspace items under a base directory",
            inputSchema=_schema(TarCreateInput),
        )
    )
    tools.append(
        Tool(
            name="workspace_file_digest",
            description="Compute a file digest (sha256 or md5) for a workspace file",
            inputSchema=_schema(DigestInput),
        )
    )
    tools.append(
        Tool(
            name="workspace_write_file",
            description="Write a file to the workspace",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative path"},
                    "content": {"type": "string", "description": "Content to write (raw string)"},
                    "append": {"type": "boolean", "default": False},
                    "executable": {"type": "boolean", "default": False},
                },
                "required": ["path", "content"],
            },
        )
    )

    # Workspace: interact and record desktop
    tools.append(
        Tool(
            name="workspace_interact_and_record",
            description=(
                "Focus a window, send key inputs, and record frames to a webm."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_title": {"type": "string"},
                    "inputs": {
                        "type": "array",
                        "items": {"type": "object", "properties": {"keys": {"type": "string"}, "delay_ms": {"type": "integer", "default": 100}}, "required": ["keys"]},
                    },
                    "duration_seconds": {"type": "integer", "default": 30},
                    "frame_interval_ms": {"type": "integer", "default": 500},
                    "output_basename": {"type": "string", "default": "session"},
                },
                "required": ["window_title", "inputs"],
            },
        )
    )

    # Workspace: basic git operations on a local repo
    tools.extend([
        Tool(
            name="workspace_git_add",
            description="Run git add in a workspace repo",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Workspace-relative repo path"},
                    "paths": {"type": "array", "items": {"type": "string"}, "description": "Paths to add (default: all)"},
                },
                "required": ["repo_path"],
            },
        ),
        Tool(
            name="workspace_git_commit",
            description="Run git commit in a workspace repo",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string"},
                    "message": {"type": "string"},
                    "all": {"type": "boolean", "default": False},
                },
                "required": ["repo_path", "message"],
            },
        ),
        Tool(
            name="workspace_git_push",
            description="Run git push in a workspace repo",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string"},
                    "remote": {"type": "string", "default": "origin"},
                    "branch": {"type": "string", "description": "Branch name (defaults to current)"},
                    "set_upstream": {"type": "boolean", "default": False},
                },
                "required": ["repo_path"],
            },
        ),
        Tool(
            name="workspace_git_pull",
            description="Run git pull in a workspace repo",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string"},
                    "remote": {"type": "string", "default": "origin"},
                    "branch": {"type": "string", "description": "Branch name (defaults to current)"},
                    "rebase": {"type": "boolean", "default": False},
                },
                "required": ["repo_path"],
            },
        ),
    ])

    # GitHub tools (only if gh available)
    if container_manager and container_manager.is_github_available():
        tools.extend([
            Tool(
                name="github_get_repository",
                description="Get details for a GitHub repository",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string"},
                        "repo": {"type": "string"},
                    },
                    "required": ["owner", "repo"],
                },
            ),
            Tool(
                name="github_clone_repository",
                description="Clone a GitHub repository into the workspace",
                inputSchema={
                    "type": "object",
                    "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}},
                    "required": ["owner", "repo"],
                },
            ),
        ])

    return tools


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls."""
    # Only require container_manager for tools that interact with the container
    container_required = name not in {"workspace_select_venv"}
    if container_required and not container_manager:
        raise RuntimeError("Container manager not initialized")

    # Add a per-call request ID for structured logging
    req_id = str(uuid.uuid4())
    logger.info(f"[req={req_id}] call_tool name={name}")

    import time as __t
    __start_ms = int(__t.time() * 1000)

    if name == "workspace_execute_command":
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

        env_map = arguments.get("env") or {}

        def _worker():
            try:
                code, out = container_manager.execute_command(command, task_id, extra_env=env_map)
                result_holder["exit_code"] = code
                result_holder["output"] = out
            except Exception as e:
                result_holder["error"] = str(e)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=timeout_s)

        if t.is_alive():
            import json as _json
            payload = {
                "exit_code": None,
                "running": True,
                "task_id": task_id,
                "timeout_seconds": timeout_s,
                "message": "Command still running; call again with a larger timeout to wait longer.",
                "hint": "If you need the final output, call this tool again with a larger timeout or poll until running=false.",
            }
            logger.info(f"[req={req_id}] tool={name} still running task_id={task_id} timeout={timeout_s}s")
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps(payload))]
        else:
            import json as _json
            if "error" in result_holder:
                logger.error(f"[req={req_id}] tool={name} error={result_holder['error']}")
                record_tool_metric(name, int(__t.time()*1000) - __start_ms)
                return [TextContent(type="text", text=_json.dumps({"exit_code": 1, "error": result_holder["error"], "hint": "Check the error field and adjust the command or environment; re-run if needed."}))]
            exit_code = result_holder.get("exit_code")
            output = result_holder.get("output", "")
            logger.info(f"[req={req_id}] tool={name} completed exit_code={exit_code}")
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": exit_code, "output": output, "hint": "Parse and surface the command output to the user only if relevant; otherwise keep it in the tool trace."}))]
    elif name == "workspace_screenshot":
        # Validate and coerce via Pydantic
        parsed = ScreenshotInput(**(arguments or {}))
        import datetime as dt
        delay = int(parsed.delay_seconds)
        filename = parsed.filename
        ts = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S")
        out_name = filename or f"screenshot_{ts}.png"
        out_path = f"/workspace/.agent/screenshots/{out_name}"
        # GUI readiness: ensure DISPLAY responds; try small retry loop before capture
        cmd = (
            "mkdir -p /workspace/.agent/screenshots && "
            f"sleep {max(0, delay)}; "
            "export DISPLAY=:0; "
            "for i in 1 2 3; do xset q >/dev/null 2>&1 && break; sleep 1; done; "
            "xdotool key XF86Refresh >/dev/null 2>&1 || true; "
            f"xfce4-screenshooter -f -s '{out_path}'"
        )
        task_id = str(uuid.uuid4())
        exit_code, output = container_manager.execute_command(cmd, task_id)
        import json as _json
        resp = {"exit_code": exit_code, "screenshot_path": out_path, "output": output}
        if _public_host and _public_port:
            url = build_screenshot_url(_public_host, int(_public_port), out_name)
            resp["screenshot_url"] = url
        resp["hint"] = (
            "Always display the screenshot to the user in the chat. "
            "If screenshot_url is present, render it inline."
        )
        logger.info(f"[req={req_id}] tool={name} completed exit_code={exit_code} path={out_path}")
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps(resp))]
    elif name == "workspace_select_venv":
        import json as _json
        paths = arguments.get("paths") or []
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise ValueError("'paths' must be a list of strings")

        def _category(p: str) -> int:
            base = (p.rstrip("/").split("/") or [""])[-1]
            if base == ".venv":
                return 0
            if base == "venv":
                return 1
            if "_env" in base or base.endswith("env") or base.endswith("_env"):
                return 2
            if base == "env":
                return 3
            return 9

        def _depth(p: str) -> int:
            return len([s for s in p.split("/") if s])

        def _parent_len(p: str) -> int:
            parts = [s for s in p.rstrip("/").split("/") if s]
            return len(parts[-2]) if len(parts) >= 2 else 0

        best = None
        if paths:
            best = sorted(paths, key=lambda p: (_category(p), _depth(p), -_parent_len(p), p))[0]

        payload = {"best": best, "candidates": list(paths)}
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps(payload))]
    elif name == "workspace_task_start":
        command = arguments.get("command")
        if not command:
            raise ValueError("'command' is required")
        env_map = arguments.get("env") or {}
        task_id = str(uuid.uuid4())
        info = container_manager.start_background_task(command, task_id, extra_env=env_map)
        import json as _json
        payload = {"task_id": task_id, **info, "hint": "Use workspace_task_status to poll and workspace_task_kill to terminate if needed."}
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps(payload))]
    elif name == "workspace_task_status":
        task_id = arguments.get("task_id")
        if not task_id:
            raise ValueError("'task_id' is required")
        status = container_manager.get_task_status(task_id)
        import json as _json
        status["hint"] = "If running=true, you can continue polling. When exit_code is not None, fetch logs from the path you used in the command or design the command to emit artifacts."
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps(status))]
    elif name == "workspace_task_kill":
        task_id = arguments.get("task_id")
        sig = arguments.get("signal", "TERM")
        if not task_id:
            raise ValueError("'task_id' is required")
        result = container_manager.kill_task(task_id, signal=sig)
        import json as _json
        result["hint"] = "If the task doesn't stop, try signal=KILL. Then poll status again."
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps(result))]
    
    
    elif name == "github_clone_repository":
        if not container_manager.is_github_available():
            raise RuntimeError("GitHub CLI is not available. Set GITHUB_PERSONAL_ACCESS_TOKEN in local/.env")
        
        owner = arguments.get("owner")
        repo = arguments.get("repo")
        
        if not owner or not repo:
            raise ValueError("Both 'owner' and 'repo' are required")
        
        # Execute the clone repository command
        exit_code, output = container_manager.clone_repository(owner=owner, repo=repo)
        import json as _json
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": exit_code, "output": output, "hint": "If cloning succeeded, add the repo to your workspace context and consider listing files or opening README next."}))]
    
    elif name == "workspace_launch_and_screenshot":
        data = LaunchAndScreenshotInput(**(arguments or {}))
        launch_command = data.launch_command
        delay = int(data.delay_seconds)
        filename = data.filename
        working_dir = data.working_dir
        env_map = data.env or {}
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
            "for i in 1 2 3; do xset q >/dev/null 2>&1 && break; sleep 1; done; "
            "xdotool key XF86Refresh >/dev/null 2>&1 || true; "
            f"xfce4-screenshooter -f -s '{out_path}'"
        )
        task_id = str(uuid.uuid4())
        exit_code, output = container_manager.execute_command(cmd, task_id)
        import json as _json
        resp = {"exit_code": exit_code, "screenshot_path": out_path, "output": output}
        if _public_host and _public_port:
            fname = out_name
            url = build_screenshot_url(_public_host, int(_public_port), fname)
            resp["screenshot_url"] = url
        # Always include a UX hint: screenshots should be displayed to the user
        resp["hint"] = (
            "Always display the screenshot to the user in the chat. "
            "If screenshot_url is present, render it inline (e.g., Markdown: ![screenshot]({screenshot_url})). "
            "If no URL, show the local path and offer to open it."
        )
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps(resp))]
    
    elif name == "potato_workspace_multi_tool_pipeline":
        # Deprecated: no longer exposed. Provide a clear deprecation message.
        msg = (
            "The multi-tool pipeline (potato_workspace_multi_tool_pipeline) is deprecated and no longer exposed. "
            "Invoke individual tools directly in sequence instead."
        )
        return [TextContent(type="text", text=msg)]
    elif name == "workspace_interact_and_record":
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
        # UX hint: videos should be offered as playable content to the user
        payload["hint"] = "If video_url is present, present a playable video to the user; otherwise provide a download link and describe where frames are saved."
        return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]
    elif name == "workspace_find":
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
        import json as _json
        items = [line for line in (output or "").splitlines() if line.strip()]
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": exit_code, "items": items, "hint": "Use these paths for follow-up file reads or summaries; do not print long lists verbatim unless helpful."}))]
    elif name == "workspace_find_venvs":
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

            # Search for venv roots: directories named like *venv* or *_env*, or subfolders containing bin/activate.
            # Exclude .git but do NOT exclude venv-like directories.
        find_cmd = (
            "cd /workspace && "
            f"cd -- '{rel.replace("'", "'\\''")}' && "
                "find . -type d -name .git -prune -o "
                "\\( -type d \\( -name '*venv*' -o -name '*_env*' \\) -o -path '*/bin/activate' \\) -print"
        )
        task_id = str(uuid.uuid4())
        exit_code, output = container_manager.execute_command(find_cmd, task_id)
        import json as _json
        items = [line for line in (output or "").splitlines() if line.strip()]
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": exit_code, "items": items, "hint": "Use these virtualenv roots to set up Python execution contexts or to activate environments before running code."}))]
    elif name == "workspace_tar_create":
        data = TarCreateInput(**(arguments or {}))
        base = data.base_dir.strip() or "."
        items = data.items
        arch = data.archive_name
        if not items:
            raise ValueError("'items' must be a non-empty list of relative paths")
        # Normalize base and construct tar command; ensure we stay under /workspace
        def _norm_rel(p: str) -> str:
            s = str(p).strip()
            if s.startswith("/"):
                raise ValueError("Absolute paths are not allowed; provide workspace-relative paths")
            return s
        base_rel = _norm_rel(base)
        items_quoted = " ".join(["'" + _norm_rel(p).replace("'", "'\\''") + "'" for p in items])
        import datetime as _dt
        ts = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%S")
        arch_name = arch or f"archive_{ts}.tar.gz"
        arch_q = arch_name.replace("'", "'\\''")
        cmd = (
            "cd /workspace && "
            f"cd -- '{base_rel.replace("'", "'\\''")}' && "
            f"tar -czf '{arch_q}' {items_quoted}"
        )
        code, out = container_manager.execute_command(cmd, str(uuid.uuid4()))
        import json as _json
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "archive": f"/workspace/{base_rel}/{arch_name}", "output": out, "hint": "You can download or share the archive path as needed."}))]
    elif name == "workspace_file_digest":
        data = DigestInput(**(arguments or {}))
        algo = (data.algorithm or "sha256").lower()
        if algo not in {"sha256", "md5"}:
            raise ValueError("Unsupported algorithm; use 'sha256' or 'md5'")
        path = data.path
        if not path:
            raise ValueError("'path' is required")
        p = str(path).strip()
        if p.startswith("/") and not p.startswith("/workspace/"):
            raise ValueError("Absolute paths outside /workspace are not allowed")
        rel = p[len("/workspace/"):] if p.startswith("/workspace/") else p
        rel_q = rel.replace("'", "'\\''")
        bin_name = "sha256sum" if algo == "sha256" else "md5sum"
        cmd = (
            "cd /workspace && "
            f"{bin_name} -- '{rel_q}' | awk '{{print $1}}'"
        )
        code, out = container_manager.execute_command(cmd, str(uuid.uuid4()))
        digest = (out or "").strip().split()[0] if out else ""
        import json as _json
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "algorithm": algo, "digest": digest, "path": f"/workspace/{rel}", "hint": "Store this digest to verify file integrity later."}))]
    elif name == "workspace_python_run_module":
        data = PythonRunModuleInput(**(arguments or {}))
        venv = data.venv_path
        module = data.module
        args = data.args
        if not venv or not module:
            raise ValueError("'venv_path' and 'module' are required")
        def _norm(p: str) -> str:
            p = str(p).strip()
            if p.startswith("/workspace/"):
                return p
            if p.startswith("/"):
                raise ValueError("Absolute paths outside /workspace are not allowed")
            return f"/workspace/{p}"
        py = _norm(venv).rstrip("/") + "/bin/python"
        arg_str = " ".join(["'" + str(a).replace("'", "'\\''") + "'" for a in args])
        cmd = f"{py} -m {module} {arg_str}".rstrip()
        code, out = container_manager.execute_command(cmd, str(uuid.uuid4()))
        import json as _json
        logger.info(f"[req={req_id}] tool={name} completed exit_code={code} module={module}")
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": "Use output to summarize the run results concisely."}))]
    elif name == "workspace_python_run_script":
        data = PythonRunScriptInput(**(arguments or {}))
        venv = data.venv_path
        script_path = data.script_path
        args = data.args
        if not venv or not script_path:
            raise ValueError("'venv_path' and 'script_path' are required")
        def _norm(p: str) -> str:
            p = str(p).strip()
            if p.startswith("/workspace/"):
                return p
            if p.startswith("/"):
                raise ValueError("Absolute paths outside /workspace are not allowed")
            return f"/workspace/{p}"
        py = _norm(venv).rstrip("/") + "/bin/python"
        sp = _norm(script_path)
        arg_str = " ".join(["'" + str(a).replace("'", "'\\''") + "'" for a in args])
        cmd = f"{py} '{sp}' {arg_str}".rstrip()
        code, out = container_manager.execute_command(cmd, str(uuid.uuid4()))
        import json as _json
        logger.info(f"[req={req_id}] tool={name} completed exit_code={code} script={script_path}")
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": "Use output to summarize the run results concisely."}))]
    elif name == "workspace_list_repositories":
        import json
        items = container_manager.list_local_repositories()
        return [TextContent(type="text", text=json.dumps({"items": items, "hint": "Use these repository entries to navigate or run git operations; avoid dumping full repo trees inline."}, ensure_ascii=False))]
    elif name == "workspace_read_file":
        rel = arguments.get("path")
        binary = bool(arguments.get("binary", False))
        if not rel:
            raise ValueError("'path' is required")
        # Normalize absolute /workspace paths to relative
        if isinstance(rel, str):
            raw = rel.strip()
            if raw == "/workspace":
                rel = "."
            elif raw.startswith("/workspace/"):
                rel = raw[len("/workspace/"):]
        data = container_manager.read_workspace_file(rel, binary=binary)
        if binary:
            import json as _json
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"path": rel, "binary": True, "length": len(data), "hint": "This is binary content; offer a download or summarize, do not inline raw bytes."}))]
        else:
            import json as _json
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"path": rel, "binary": False, "content": str(data), "hint": "Summarize long files; for short text, show key excerpts. Avoid flooding chat with large content."}))]
    elif name == "workspace_write_file":
        rel = arguments.get("path")
        content = arguments.get("content")
        append = bool(arguments.get("append", False))
        executable = bool(arguments.get("executable", False))
        if not rel or content is None:
            raise ValueError("'path' and 'content' are required")
        # Normalize absolute /workspace paths to relative
        if isinstance(rel, str):
            raw = rel.strip()
            if raw == "/workspace":
                rel = "."
            elif raw.startswith("/workspace/"):
                rel = raw[len("/workspace/"):]
        import json as _json
        p = container_manager.write_workspace_file(rel, str(content), append=append, executable=executable)
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"path": rel, "absolute": str(p), "appended": append, "executable": executable, "hint": "Proceed with next action that uses this file (e.g., run it, open it, or commit it) rather than echoing the entire content."}))]
    elif name == "workspace_git_add":
        repo_path = arguments.get("repo_path")
        paths = arguments.get("paths") or []
        if not repo_path:
            raise ValueError("'repo_path' is required")
        path_args = " ".join([f"'" + str(p).replace("'", "'\\''") + "'" for p in paths]) if paths else "-A"
        cmd = (
            "cd /workspace && "
            f"cd -- '{str(repo_path).replace("'", "'\\''")}' && "
            f"git add {path_args}"
        )
        import json as _json
        code, out = container_manager.execute_command(cmd, str(uuid.uuid4()))
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": "If exit_code is 0, you can proceed to commit or push; otherwise surface the error succinctly."}))]
    elif name == "workspace_git_commit":
        repo_path = arguments.get("repo_path")
        message = arguments.get("message")
        all_flag = bool(arguments.get("all", False))
        if not repo_path or not message:
            raise ValueError("'repo_path' and 'message' are required")
        msg = str(message).replace("'", "'\\''")
        all_clause = " -a" if all_flag else ""
        cmd = (
            "cd /workspace && "
            f"cd -- '{str(repo_path).replace("'", "'\\''")}' && "
            f"git commit{all_clause} -m '{msg}'"
        )
        import json as _json
        code, out = container_manager.execute_command(cmd, str(uuid.uuid4()))
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": "If commit succeeded, summarize the commit message and next steps (push or create PR)."}))]
    elif name == "workspace_git_push":
        repo_path = arguments.get("repo_path")
        remote = arguments.get("remote", "origin")
        branch = arguments.get("branch")
        set_upstream = bool(arguments.get("set_upstream", False))
        if not repo_path:
            raise ValueError("'repo_path' is required")
        remote_s = str(remote).replace("'", "'\\''")
        branch_s = str(branch).replace("'", "'\\''") if branch else ""
        branch_clause = f" {branch_s}" if branch_s else ""
        upstream = " -u" if set_upstream else ""
        cmd = (
            "cd /workspace && "
            f"cd -- '{str(repo_path).replace("'", "'\\''")}' && "
            f"git push{upstream} '{remote_s}'{branch_clause}"
        )
        import json as _json
        code, out = container_manager.execute_command(cmd, str(uuid.uuid4()))
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": "If push succeeded, share the branch and next steps (e.g., open PR). On failure, show the error and suggest pull/rebase."}))]
    elif name == "workspace_git_pull":
        repo_path = arguments.get("repo_path")
        remote = arguments.get("remote", "origin")
        branch = arguments.get("branch")
        rebase = bool(arguments.get("rebase", False))
        if not repo_path:
            raise ValueError("'repo_path' is required")
        remote_s = str(remote).replace("'", "'\\''")
        branch_s = str(branch).replace("'", "'\\''") if branch else ""
        branch_clause = f" {branch_s}" if branch_s else ""
        rebase_clause = " --rebase" if rebase else ""
        cmd = (
            "cd /workspace && "
            f"cd -- '{str(repo_path).replace("'", "'\\''")}' && "
            f"git pull{rebase_clause} '{remote_s}'{branch_clause}"
        )
        import json as _json
        code, out = container_manager.execute_command(cmd, str(uuid.uuid4()))
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": "If pull succeeded, summarize changes. If conflicts, advise resolving and committing."}))]
    elif name == "github_get_repository":
        if not container_manager.is_github_available():
            raise RuntimeError("GitHub CLI is not available. Set GITHUB_PERSONAL_ACCESS_TOKEN in local/.env")
        owner = arguments.get("owner")
        repo = arguments.get("repo")
        if not owner or not repo:
            raise ValueError("Both 'owner' and 'repo' are required")
        # Request common fields as JSON
        fields = "name,description,sshUrl,homepageUrl,url,defaultBranchRef,visibility,createdAt,updatedAt,owner"
        cmd = f"gh repo view {owner}/{repo} --json {fields}"
        import json as _json
        code, out = container_manager.execute_command(cmd, str(uuid.uuid4()))
        # Try to parse JSON output from gh; if it fails, return as string
        parsed = None
        try:
            parsed = _json.loads(out) if out else None
        except Exception:
            parsed = None
        payload = {"exit_code": code}
        if parsed is not None:
            payload["repository"] = parsed
        else:
            payload["output"] = out
        payload["hint"] = "Use repository data to navigate or clone; present key fields (name, description, default branch) to the user concisely."
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps(payload))]
    
    else:
        raise ValueError(f"Unknown tool: {name}")


def initialize_server() -> None:
    """Initialize the server and container."""
    global container_manager

    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
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
