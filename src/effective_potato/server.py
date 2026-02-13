"""MCP server for effective-potato.

This server is hosted over MCP Streamable HTTP (Starlette/uvicorn), not stdio.
"""

import logging
import os
import uuid
from typing import Any, Literal, no_type_check

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool
from pydantic import BaseModel, Field

from .container import ContainerManager
from .web import record_tool_metric

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except Exception:
        return default


def _env_str(name: str, default: str) -> str:
    try:
        v = os.getenv(name)
        return (v if v is not None else default).strip() or default
    except Exception:
        return default
# ---------------------------
# Pydantic models (typed schemas)
# ---------------------------

class ScreenshotInput(BaseModel):
    filename: str | None = Field(default=None, description="Optional filename for the screenshot (png)")
    delay_seconds: int = Field(default=0, ge=0)
    # Optional per-call timeout (seconds); default applied by server logic
    # Not included in schema properties to keep the model small—MCP clients can still pass it.


class PythonRunModuleInput(BaseModel):
    venv_path: str = Field(description="Workspace-relative path to the venv root")
    module: str = Field(description="Python module name to run")
    args: list[str] = Field(default_factory=list)
    background: bool = Field(default=False, description="If true, start as a background task and return task_id")

class PythonRunScriptInput(BaseModel):
    venv_path: str = Field(description="Workspace-relative path to the venv root")
    script_path: str = Field(description="Workspace-relative path to the script")
    args: list[str] = Field(default_factory=list)
    background: bool = Field(default=False, description="If true, start as a background task and return task_id")


class PythonCheckSyntaxInput(BaseModel):
    venv_path: str = Field(description="Workspace-relative path to the venv root")
    source_path: str = Field(description="Workspace-relative path to the Python source file to compile")


class PytestRunInput(BaseModel):
    venv_path: str = Field(description="Workspace-relative path to the venv root")
    args: list[str] = Field(default_factory=list, description="Additional pytest args, e.g., ['-q', 'tests']")


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
    venv: str | None = Field(default=None, description="the exact command to activate the virtual environment the project needs, such as 'source venv/bin/activate'")


class InteractInputItem(BaseModel):
    key_sequence: str | None = Field(default=None, description="Space-separated sequence of key names, e.g., 'Insert h e l l o' or 'Escape d d'")
    delay: int | None = Field(default=None, ge=0, description="Delay in milliseconds (per key when sending sequences, or total when type='sleep')")
    type: Literal["once", "sleep", "repeat"] | None = Field(default=None, description="Action type. 'repeat' loops key_sequence for the entire recording duration.")


class InteractAndRecordInput(BaseModel):
    # Optional app launch and venv activation
    launch_command: str | None = Field(default=None, description="Optional command to launch before recording")
    venv: str | None = Field(default=None, description="Optional venv activate command, e.g., 'source .venv/bin/activate'")
    # User interaction sequence (required: at least one item)
    inputs: list[InteractInputItem] = Field(description="Sequence of interactions; currently only 'keys' are supported")
    # Recording controls
    duration_seconds: int = Field(default=30, ge=1)
    frame_interval_ms: int = Field(
        default=200,
        ge=10,
        description=(
            "Frame interval in milliseconds (lower is smoother). Recommendation: use ≤200ms (≥5 fps) for acceptable smoothness; "
            "for best visual detail aim for ~20ms (≈50 fps), hardware permitting."
        ),
    )
    output_basename: str = Field(default="session")
    # Runtime context
    working_dir: str | None = Field(default=None, description="Workspace-relative directory to cd into before launch/record")
    env: dict[str, str] | None = Field(default=None, description="Environment variables to export before launch/record")
    post_launch_delay_seconds: int = Field(default=1, ge=0, description="Delay after launching before probing/recording")


class RecommendedFlowInput(BaseModel):
    query: str = Field(description="User goal expressed in natural language")
    context: dict[str, Any] | None = Field(default=None, description="Optional hints like paths or filenames")
    preferences: dict[str, Any] | None = Field(default=None, description="Optional preferences (e.g., timeouts)")


def _schema(model: type[BaseModel]) -> dict:
    # Pydantic v2 schema
    return model.model_json_schema()


# ---------------------------
# Exec helpers
# ---------------------------
def _would_git_init_workspace_root(command: str) -> bool:
    """Heuristically detect if the provided shell command would execute
    'git init' at the workspace root (/workspace).

    We simulate a simple flow by splitting the command on ';' and '&&',
    tracking a coarse current working directory. If we encounter a
    'git init' while the simulated CWD is exactly '/workspace', we return True.

    This is intentionally conservative and only aims to prevent the most
    common destructive case like:
      - cd /workspace && git init
      - cd -- '/workspace'; git init
    It allows 'git init' in subdirectories (e.g., cd /workspace && cd proj && git init).
    """
    try:
        if not isinstance(command, str) or not command.strip():
            return False
        s = command.strip()
        import re as _re
        import shlex as _sh
        parts = [p.strip() for p in _re.split(r"\s*(?:&&|;)\s*", s) if p.strip()]
        cwd: str | None = None

        def _norm(p: str | None) -> str | None:
            if not p:
                return None
            # Strip trailing '/.' which is equivalent to the directory itself
            q = p.rstrip()
            if q.endswith("/."):
                q = q[:-2] or "/"
            return q

        for part in parts:
            low = part.strip()
            # Track cd commands to update simulated cwd
            if low.startswith("cd ") or low.startswith("cd\t") or low.startswith("cd\n"):
                rest = low[2:].strip()
                if rest.startswith("--"):
                    rest = rest[2:].strip()
                if (rest.startswith("'") and rest.endswith("'")) or (rest.startswith('"') and rest.endswith('"')):
                    rest = rest[1:-1]
                if rest.startswith("/"):
                    cwd = _norm(rest)
                else:
                    if cwd:
                        base = cwd[:-1] if cwd.endswith("/") else cwd
                        cwd = _norm(base + "/" + rest)
                    else:
                        cwd = _norm(rest)
                continue

            # Parse git invocations more precisely
            try:
                toks = _sh.split(low)
            except Exception:
                toks = low.split()
            if not toks:
                continue
            if toks[0] != "git":
                continue

            # Find any -C <path> option and resolve it against cwd when relative
            git_cwd: str | None = None
            i = 1
            while i < len(toks):
                t = toks[i]
                if t == "-C" and (i + 1) < len(toks):
                    cpath = toks[i + 1]
                    if cpath and not cpath.startswith("/") and cwd:
                        base = cwd[:-1] if cwd.endswith("/") else cwd
                        git_cwd = _norm(base + "/" + cpath)
                    else:
                        git_cwd = _norm(cpath)
                    i += 2
                    continue
                i += 1

            # Look for 'init' subcommand
            if "init" in toks[1:]:
                # Target directory argument (heuristic): token immediately after 'init' if present and not another option
                try:
                    init_idx = toks.index("init")
                except ValueError:
                    init_idx = -1
                init_arg: str | None = None
                if init_idx != -1 and (init_idx + 1) < len(toks):
                    cand = toks[init_idx + 1]
                    if not cand.startswith("-"):
                        init_arg = cand

                # Resolve init_arg to an absolute if possible
                target_abs: str | None = None
                if init_arg:
                    if init_arg.startswith("/"):
                        target_abs = _norm(init_arg)
                    elif (init_arg in {".", "./"}) and (git_cwd or cwd):
                        target_abs = _norm((git_cwd or cwd) or init_arg)
                    elif (git_cwd or cwd) and not init_arg.startswith("-"):
                        base = (git_cwd or cwd) or ""
                        base = base[:-1] if base.endswith("/") else base
                        target_abs = _norm((base + "/" + init_arg) if base else init_arg)

                # Decide the effective directory where init will act (ensure str, not Optional)
                effective_dir = (target_abs or git_cwd or cwd) or ""
                if effective_dir == "/workspace":
                    return True
        return False
    except Exception:
        return False

def _exec_with_timeout(cmd: str, *, arguments: dict | None = None, extra_env: dict | None = None) -> tuple[bool, int | None, str]:
    """Run a container command with a default timeout.

    Returns (timed_out, exit_code, output). Default timeout is 120s unless
    arguments contains a numeric 'timeout_seconds'. If timed out, exit_code will
    be None and output may be empty.
    """
    timeout_s = 120
    if isinstance(arguments, dict):
        try:
            timeout_s = int(arguments.get("timeout_seconds", 120))
        except Exception:
            timeout_s = 120

    # Run in a worker thread so we can implement a join timeout
    result: dict[str, Any] = {}

    def _worker():
        try:
            try:
                code, out = container_manager.execute_command(cmd, str(uuid.uuid4()), extra_env=extra_env)
            except TypeError:
                # Some test fakes do not accept extra_env
                code, out = container_manager.execute_command(cmd, str(uuid.uuid4()))
            result["exit_code"] = code
            result["output"] = out
        except Exception as e:
            result["error"] = str(e)

    import threading as _th
    t = _th.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        return True, None, ""
    if "error" in result:
        # Surface errors as exit_code=1 with message in output
        return False, 1, result.get("error", "error")
    return False, result.get("exit_code"), result.get("output", "")


# Initialize the MCP server
app = Server("effective-potato")

# Container manager instance
container_manager: ContainerManager | None = None

"""Note: review-only mode and review_*-prefixed tools have been removed.

This server now exposes a single toolkit intended for coding agents.
"""


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools (slim set)."""
    tools: list[Tool] = []

    # Workspace: execute raw command (last resort)
    tools.append(
        Tool(
            name="potato_execute_command",
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
                    "background": {"type": "boolean", "default": False, "description": "If true, run in the background and return task_id"},
                },
                "required": ["command"],
            },
        )
    )

    # Note: The recommended flow planner has been disabled to reduce schema surface area and token usage.

    # Workspace: launch app and screenshot
    tools.append(
        Tool(
            name="potato_launch_and_screenshot",
            description=(
                "Launch an app and then capture a fullscreen screenshot. Optionally accept 'venv' to activate before running the launch_command (useful for Python apps)."
            ),
            inputSchema=_schema(LaunchAndScreenshotInput),
        )
    )

    # Workspace: screenshot only (decoupled from launch)
    tools.append(
        Tool(
            name="potato_screenshot",
            description=(
                "Capture a fullscreen screenshot and save it under the workspace .agent/screenshots directory. "
                "Do NOT launch or manage processes in a separate call immediately before this; use the combined launch tool or ensure the UI is ready. Default timeout: 120s (override with timeout_seconds)."
            ),
            inputSchema=_schema(ScreenshotInput),
        )
    )

    # Workspace: interact and record
    tools.append(
        Tool(
            name="potato_interact_and_record",
            description=(
                "Optionally launch an app, perform light UI interactions, and record the desktop to a WebM file. "
                "Pass 'venv' if you need to activate a Python environment before launch. You can also set working_dir and env. "
                "Returns JSON containing 'video_path', window info, and 'exit_code'.\n\n"
                "Inputs format: items run sequentially. Each item supports {key_sequence, delay, type}. Default type is 'once'. "
                "type='sleep' waits for 'delay' milliseconds. type='repeat' loops the given key_sequence continuously for the entire recording duration. "
                "Delays less than 20ms are automatically clamped to 20ms for reliability.\n\n"
                "Recommendation: set frame_interval_ms to ≤200ms (≥5 fps) for smooth playback; for best visual detail aim for ~20ms (≈50 fps), hardware permitting.\n\n"
                "Example inputs:\n"
                "inputs: [\n"
                "  {\"delay\": 100, \"key_sequence\": \"Insert h e l l o w o r l d\", \"type\": \"once\"},\n"
                "  {\"delay\": 2000, \"type\": \"sleep\"},\n"
                "  {\"delay\": 50, \"key_sequence\": \"Escape d d\", \"type\": \"once\"}\n"
                "]\n\n"
                "inputs: [\n"
                "  {\"delay\": 20, \"key_sequence\": \"Up Up Down Down Left Left Right Right\", \"type\": \"repeat\"}\n"
                "]\n"
            ),
            inputSchema=_schema(InteractAndRecordInput),
        )
    )

    # Task lifecycle controls
    tools.append(
        Tool(
            name="potato_task_start",
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
            name="potato_task_status",
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
            name="potato_task_output",
            description="Read or tail the output file of a background task (task_<id>.out)",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "tail": {"type": "integer", "default": 0, "description": "If >0, return only the last N lines"},
                },
                "required": ["task_id"],
            },
        )
    )
    tools.append(
        Tool(
            name="potato_task_list",
            description="List known background task IDs; optionally include per-task status",
            inputSchema={
                "type": "object",
                "properties": {"include_status": {"type": "boolean", "default": False}},
            },
        )
    )
    tools.append(
        Tool(
            name="potato_task_kill",
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
            name="potato_python_run_module",
            description="Run 'python -m <module>' using a specified virtualenv without activating it.",
            inputSchema=_schema(PythonRunModuleInput),
        )
    )
    tools.append(
        Tool(
            name="potato_python_run_script",
            description="Run a Python script file using a specified virtualenv without activating it.",
            inputSchema=_schema(PythonRunScriptInput),
        )
    )

    # Python helpers: syntax check and pytest
    tools.append(
        Tool(
            name="potato_python_check_syntax",
            description="Activate a venv and run 'python -m py_compile <source_file>'.",
            inputSchema=_schema(PythonCheckSyntaxInput),
        )
    )
    tools.append(
        Tool(
            name="potato_pytest_run",
            description="Activate a venv and run pytest with optional arguments (e.g., -q tests).",
            inputSchema=_schema(PytestRunInput),
        )
    )

    # Note: OpenWeb scripts are intentionally NOT exposed as MCP tools to avoid easy tampering.

    # Workspace: list tracked repos
    tools.append(
        Tool(
            name="potato_list_repositories",
            description="List repositories tracked in the workspace and whether their directories exist",
            inputSchema={"type": "object", "properties": {}},
        )
    )

    # NOTE: File search/review/edit tools are intentionally not exposed.
    # Coding agents typically already provide these primitives (glob/list/read/search/write/applyDiff).
    # Keep this server focused on container execution, git operations, and GUI automation.
    tools.append(
        Tool(
            name="potato_select_venv",
            description=(
                "Select the best virtualenv path from candidates using simple heuristics (.venv preferred, then venv, then *_env*, then env; tie-breakers by depth, then parent name length). Returns an 'activate' field with the exact command to activate it."
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
            name="potato_find_venvs",
            description="Find virtualenv roots by matching *venv*/*_env* folders or bin/activate paths (prunes .git and .agent). Also returns 'venv_roots' and 'activations' with 'source <venv_root>/bin/activate' commands.",
            inputSchema={"type": "object", "properties": {"path": {"type": "string"}}},
        )
    )

    # (Removed duplicate workspace_interact_and_record registration)

    # Workspace: basic git operations on a local repo
    tools.extend([
        Tool(
            name="potato_git_add",
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
            name="potato_git_commit",
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
            name="potato_git_push",
            description="Run git push in a workspace repo",
            inputSchema={
                "type": "object",
                "x-needs-approval": True,
                "properties": {
                    "repo_path": {"type": "string"},
                    "remote": {"type": "string", "default": "origin"},
                    "branch": {"type": "string", "description": "Branch name (defaults to current)"},
                    "set_upstream": {"type": "boolean", "default": False},
                    "confirm": {"type": "boolean", "default": False, "description": "Must be true to execute push. LLMs must obtain user approval before setting this."},
                },
                "required": ["repo_path"],
            },
        ),
        Tool(
            name="potato_git_pull",
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

    # Workspace: git status and diff for review
    tools.append(
        Tool(
            name="potato_git_status",
            description="Run git status (porcelain by default) in a workspace repo to list pending/staged changes",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Workspace-relative repo path"},
                    "porcelain": {"type": "boolean", "default": True, "description": "Use --porcelain=v1 -b for machine-friendly output"},
                },
                "required": ["repo_path"],
            },
        )
    )
    tools.append(
        Tool(
            name="potato_git_diff",
            description="Run git diff to show pending changes; set staged=true for staged diffs",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string"},
                    "staged": {"type": "boolean", "default": False},
                    "name_only": {"type": "boolean", "default": False},
                    "unified": {"type": "integer", "default": 3, "minimum": 0, "description": "Context lines (-U N)"},
                    "paths": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["repo_path"],
            },
        )
    )

    # Workspace: branch management and merges
    tools.append(
        Tool(
            name="potato_git_checkout",
            description="Switch to an existing branch using 'git checkout <branch>'.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string"},
                    "branch": {"type": "string", "description": "Existing branch name to checkout"},
                },
                "required": ["repo_path", "branch"],
            },
        )
    )
    tools.append(
        Tool(
            name="potato_git_branch_create",
            description="Create a new branch (optionally checkout) from the current HEAD or a start point.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string"},
                    "name": {"type": "string", "description": "Branch name to create"},
                    "start_point": {"type": "string", "description": "Optional start point (commit or branch)"},
                    "checkout": {"type": "boolean", "default": True, "description": "If true, checkout the branch after creating (uses checkout -b)"},
                },
                "required": ["repo_path", "name"],
            },
        )
    )
    tools.append(
        Tool(
            name="potato_git_branch_delete",
            description="Delete a local branch (-d by default, -D with force=true).",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string"},
                    "name": {"type": "string"},
                    "force": {"type": "boolean", "default": False},
                },
                "required": ["repo_path", "name"],
            },
        )
    )
    tools.append(
        Tool(
            name="potato_git_merge",
            description=(
                "Merge a source branch into a target branch. If target_branch is not provided, we detect 'main' or 'master' as upstream. "
                "By default uses '--no-ff --no-edit'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string"},
                    "source_branch": {"type": "string"},
                    "target_branch": {"type": "string", "description": "Upstream target (e.g., main or master)"},
                    "no_ff": {"type": "boolean", "default": True},
                    "no_edit": {"type": "boolean", "default": True},
                },
                "required": ["repo_path", "source_branch"],
            },
        )
    )

    # GitHub tools (only if gh available)
    has_gh = False
    try:
        has_gh = bool(container_manager and getattr(container_manager, "is_github_available") and container_manager.is_github_available())
    except Exception:
        has_gh = False
    if has_gh:
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
@no_type_check
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls."""
    # If a tool is not published, fail fast as "Unknown tool".
    # This also avoids returning container initialization errors for callers probing tool availability.
    published_names = {t.name for t in (await list_tools())}
    if name not in published_names:
        raise ValueError(f"Unknown tool: {name}")

    # Only require container_manager for tools that interact with the container
    container_required = name not in {"potato_select_venv"}
    if container_required and not container_manager:
        raise RuntimeError("Container manager not initialized")
    # Local non-None alias for type checking
    cm: ContainerManager | None = container_manager
    

    # Add a per-call request ID for structured logging
    req_id = str(uuid.uuid4())
    logger.info(f"[req={req_id}] call_tool name={name}")

    import time as __t
    __start_ms = int(__t.time() * 1000)

    # Helper to enforce progress update reminder in hints
    def _with_progress_reminder(h: str) -> str:
        suffix = " Always include a brief status update on the overall task progress (what's done, what's next, blockers)."
        try:
            h = (h or "").rstrip()
        except Exception:
            h = ""
        # Avoid duplicating the suffix if already present
        if suffix.strip() in h:
            return h
        if h:
            return f"{h} {suffix}"
        return suffix.strip()

    if name == "potato_execute_command":
        import threading
        command = arguments.get("command")
        if not command:
            raise ValueError("Command is required")

        # Guard: prevent accidental repository initialization at workspace root
        if _would_git_init_workspace_root(str(command)):
            import json as _json
            payload = {
                "exit_code": 3,
                "message": "Blocked: git init at workspace root is not allowed.",
                "hint": _with_progress_reminder("Initialize repositories inside a project subdirectory (e.g., /workspace/myproj). Use 'cd myproj && git init'."),
                "blocked": True,
            }
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps(payload))]

        # Generate unique task ID
        task_id = str(uuid.uuid4())

        # Optional timeout for waiting on the command (defaults to 120s)
        try:
            timeout_s = int(arguments.get("timeout_seconds", 120))
        except Exception:
            timeout_s = 120

        # Background mode support
        run_bg = bool(arguments.get("background", False))

        result_holder: dict[str, Any] = {}

        env_map = arguments.get("env") or {}

        if run_bg:
            if not cm:
                raise RuntimeError("Container manager not initialized")
            info = cm.start_background_task(command, task_id, extra_env=env_map)
            import json as _json
            payload = {"task_id": info.get("task_id", task_id), "exit_code": info.get("exit_code"), "hint": _with_progress_reminder("Use potato_task_status to poll, potato_task_output to read logs, and potato_task_kill to stop the process.")}
            logger.info(f"[req={req_id}] tool={name} started background task_id={task_id}")
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps(payload))]

        def _worker():
            try:
                if not cm:
                    raise RuntimeError("Container manager not initialized")
                code, out = cm.execute_command(command, task_id, extra_env=env_map)
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
                "hint": _with_progress_reminder("If you need the final output, call again with a larger timeout or poll until running=false. Alternatively, rerun with background=true and use potato_task_output to tail logs and potato_task_kill to stop when done."),
            }
            logger.info(f"[req={req_id}] tool={name} still running task_id={task_id} timeout={timeout_s}s")
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps(payload))]
        else:
            import json as _json
            if "error" in result_holder:
                logger.error(f"[req={req_id}] tool={name} error={result_holder['error']}")
                record_tool_metric(name, int(__t.time()*1000) - __start_ms)
                return [TextContent(type="text", text=_json.dumps({"exit_code": 1, "error": result_holder["error"], "hint": _with_progress_reminder("Check the error field and adjust the command or environment; re-run if needed.")}))]
            exit_code = result_holder.get("exit_code")
            output = result_holder.get("output", "")
            logger.info(f"[req={req_id}] tool={name} completed exit_code={exit_code}")
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": exit_code, "output": output, "hint": _with_progress_reminder("Parse and surface the command output to the user only if relevant; otherwise keep it in the tool trace.")}))]
    # 'potato_recommended_flow' intentionally disabled
    elif name == "potato_screenshot":
        # Validate and coerce via Pydantic
        parsed = ScreenshotInput(**(arguments or {}))
        import datetime as dt
        import os
        delay = int(parsed.delay_seconds)
        filename = parsed.filename
        ts = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S")
        # Always suffix filenames with a UUID to avoid overwrites
        _uid = uuid.uuid4().hex
        if filename:
            root, ext = os.path.splitext(str(filename))
            ext = ext or ".png"
            out_name = f"{root}_{_uid}{ext}"
        else:
            out_name = f"screenshot_{ts}_{_uid}.png"
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
        # Execute with default timeout behavior (120s unless overridden)
        timed_out, exit_code, output = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            import json as _json
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "Screenshot still running; try again with a larger timeout.", "hint": _with_progress_reminder("Increase timeout_seconds if you need to wait longer for the desktop to settle before capture.")}))]
        import json as _json
        resp = {"exit_code": exit_code, "screenshot_path": out_path, "output": output,
                "hint": _with_progress_reminder("Display the screenshot to the user; use the provided 'screenshot_path'.")}
        logger.info(f"[req={req_id}] tool={name} completed exit_code={exit_code} path={out_path}")
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps(resp))]
    elif name == "potato_select_venv":
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

        activate = f"source {best}/bin/activate" if best else None
        payload = {"best": best, "candidates": list(paths), "activate": activate}
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps(payload))]
    elif name == "potato_find_venvs":
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
        # Build the container-side find command expected by unit tests
        rel_esc = rel.replace("'", "'\\''")
        find_cmd = (
            "cd /workspace && "
            f"cd -- '{rel_esc}' && "
            "find . \\(-name .git -o -name .agent\\) -prune -o "
            "\\( -type d \\( -name '*venv*' -o -name '*_env*' \\) -o -path '*/bin/activate' \\) -print"
        )
        task_id = str(uuid.uuid4())
        timed_out, exit_code, output = _exec_with_timeout(find_cmd, arguments=arguments)
        items: list[str]
        # If the container-side find worked, use it; otherwise fallback to a host-side scan for robustness
        if (not timed_out) and (exit_code == 0) and output and not output.strip().startswith("find:"):
            items = [line for line in (output or "").splitlines() if line.strip()]
        else:
            import os as _os
            from pathlib import Path as _Path
            if not cm:
                raise RuntimeError("Container manager not initialized")
            base_host = (_Path(cm.workspace_dir) / rel).resolve()
            items = []
            for root, dirs, files in _os.walk(base_host):
                # prune
                dirs[:] = [d for d in dirs if d not in {".git", ".agent"}]
                # rel path from base_host
                rel_root = "." if _Path(root) == base_host else "./" + str(_Path(root).relative_to(base_host)).replace("\\", "/")
                # venv-like directories
                for d in dirs:
                    if ("venv" in d) or ("_env" in d):
                        items.append(f"{rel_root}/{d}")
                # bin/activate file
                act = _Path(root)/"bin"/"activate"
                if act.exists():
                    items.append(f"{rel_root}/bin/activate")
            exit_code = 0
        # Derive potential venv roots (if a bin/activate path was returned, strip the /bin/activate)
        def _venv_root(p: str) -> str:
            if p.endswith("/bin/activate"):
                return p[: -len("/bin/activate")]
            return p.rstrip("/")
        venv_roots = sorted(set(_venv_root(it) for it in items))
        activations = [f"source {root}/bin/activate" for root in venv_roots]
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        import json as _json
        return [TextContent(type="text", text=_json.dumps({
            "exit_code": exit_code,
            "items": items,
            "venv_roots": venv_roots,
            "activations": activations,
            "hint": _with_progress_reminder("Chain these: (1) pass venv_roots (or items) to potato_select_venv to get 'activate'; (2) provide that string as 'venv' to potato_launch_and_screenshot or potato_interact_and_record (optionally set 'launch_command'); those tools will run '<venv> && <launch_command>' for you.")
        }))]
    elif name == "potato_task_start":
        command = arguments.get("command")
        if not command:
            raise ValueError("'command' is required")
        env_map = arguments.get("env") or {}
        task_id = str(uuid.uuid4())
        if not cm:
            raise RuntimeError("Container manager not initialized")
        info = cm.start_background_task(command, task_id, extra_env=env_map)
        import json as _json
        payload = {"task_id": task_id, **info, "hint": _with_progress_reminder("Use potato_task_status to poll, potato_task_output to tail logs, and potato_task_kill to terminate if needed.")}
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps(payload))]
    elif name == "potato_task_status":
        task_id = arguments.get("task_id")
        if not task_id:
            raise ValueError("'task_id' is required")
        if not cm:
            raise RuntimeError("Container manager not initialized")
        status = cm.get_task_status(task_id)
        import json as _json
        status["hint"] = _with_progress_reminder("If running=true, continue polling or use potato_task_output to tail logs. When exit_code is not None, summarize results and surface artifacts.")
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps(status))]
    elif name == "potato_task_kill":
        task_id = arguments.get("task_id")
        sig = arguments.get("signal", "TERM")
        if not task_id:
            raise ValueError("'task_id' is required")
        if not cm:
            raise RuntimeError("Container manager not initialized")
        result = cm.kill_task(task_id, signal=sig)
        import json as _json
        result["hint"] = _with_progress_reminder("If the task doesn't stop, try signal=KILL. Then poll status again.")
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps(result))]
    elif name == "potato_task_output":
        task_id = arguments.get("task_id")
        tail = arguments.get("tail", 0)
        if not task_id:
            raise ValueError("'task_id' is required")
        try:
            n = int(tail)
            if n < 0:
                n = 0
        except Exception:
            n = 0
        # Read the out file; apply tail if requested
        out_path = f"/workspace/.agent/tmp_scripts/task_{task_id}.out"
        if n > 0:
            cmd = f"test -f '{out_path}' && tail -n {n} '{out_path}' || true"
        else:
            cmd = f"test -f '{out_path}' && cat '{out_path}' || true"
        if not cm:
            raise RuntimeError("Container manager not initialized")
        code, out = cm.execute_command(cmd, str(uuid.uuid4()))
        import json as _json
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "content": out or "", "path": out_path, "hint": _with_progress_reminder("Show a concise excerpt (use tail for long logs) and offer to open or download if needed.")}))]
    elif name == "potato_task_list":
        include_status = bool(arguments.get("include_status", False)) if isinstance(arguments, dict) else False
        # List files matching task_*.pid under tmp_scripts; derive task IDs
        probe = (
            "cd /workspace/.agent/tmp_scripts 2>/dev/null || exit 0; "
            "ls -1 task_*.pid 2>/dev/null | sed -e 's/^task_//' -e 's/\\.pid$//'"
        )
        if not cm:
            raise RuntimeError("Container manager not initialized")
        code, out = cm.execute_command(probe, str(uuid.uuid4()))
        raw_lines = [line.strip() for line in (out or "").splitlines() if line.strip()]
        def _tid(line: str) -> str:
            if line.startswith("task_") and line.endswith(".pid"):
                return line[len("task_"):-len(".pid")]
            return line
        task_ids = [_tid(line) for line in raw_lines]
        payload = {"exit_code": code, "tasks": task_ids}
        if include_status and task_ids:
            statuses: dict[str, Any] = {}
            for tid in task_ids:
                try:
                    statuses[tid] = cm.get_task_status(tid)
                except Exception as e:
                    statuses[tid] = {"error": str(e)}
            payload["statuses"] = statuses
        import json as _json
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps(payload))]
    
    
    elif name == "github_clone_repository":
        if not cm or not cm.is_github_available():
            raise RuntimeError("GitHub CLI is not available. Set GITHUB_PERSONAL_ACCESS_TOKEN in local/.env")
        
        owner = arguments.get("owner")
        repo = arguments.get("repo")
        
        if not owner or not repo:
            raise ValueError("Both 'owner' and 'repo' are required")
        
        # Execute the clone repository command
        exit_code, output = cm.clone_repository(owner=owner, repo=repo)
        import json as _json
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": exit_code, "output": output, "hint": _with_progress_reminder("If cloning succeeded, add the repo to your workspace context and consider listing files or opening README next.")}))]
    
    elif name == "potato_launch_and_screenshot":
        data = LaunchAndScreenshotInput(**(arguments or {}))
        launch_command = data.launch_command
        delay = int(data.delay_seconds)
        filename = data.filename
        working_dir = data.working_dir
        env_map = data.env or {}
        venv_cmd = (data.venv or "").strip() or None
        if not launch_command:
            raise ValueError("'launch_command' is required")

        # Build the script to launch the app and screenshot
        import datetime as dt
        import os
        shot_dir = "/workspace/.agent/screenshots"
        # Create directory and run command
        ts = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S")
        # Always suffix filenames with a UUID to avoid overwrites
        _uid = uuid.uuid4().hex
        if filename:
            root, ext = os.path.splitext(str(filename))
            ext = ext or ".png"
            out_name = f"{root}_{_uid}{ext}"
        else:
            out_name = f"screenshot_{ts}_{_uid}.png"
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

        # Prepend optional venv activation if provided
        launch_with_venv = f"({venv_cmd} && {launch_command})" if venv_cmd else f"({launch_command})"

        cmd = (
            "mkdir -p /workspace/.agent/screenshots && "
            f"{cd_snippet}{exports}"
            f"{launch_with_venv} >/tmp/launch.log 2>&1 & "
            f"sleep {delay}; "
            "export DISPLAY=:0; "
            "for i in 1 2 3; do xset q >/dev/null 2>&1 && break; sleep 1; done; "
            "xdotool key XF86Refresh >/dev/null 2>&1 || true; "
            f"xfce4-screenshooter -f -s '{out_path}'"
        )
        task_id = str(uuid.uuid4())
        timed_out, exit_code, output = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            import json as _json
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "Launch and capture still running; try again with a larger timeout.", "hint": _with_progress_reminder("Increase timeout_seconds if the app needs longer to render before capture.")}))]
        import json as _json
        resp = {"exit_code": exit_code, "screenshot_path": out_path, "output": output,
                "hint": _with_progress_reminder("Display the screenshot to the user; use the provided 'screenshot_path'.")}
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps(resp))]
    
    elif name == "potato_workspace_multi_tool_pipeline":
        # Deprecated: no longer exposed. Provide a clear deprecation message.
        msg = (
            "The multi-tool pipeline (potato_workspace_multi_tool_pipeline) is deprecated and no longer exposed. "
            "Invoke individual tools directly in sequence instead."
        )
        return [TextContent(type="text", text=msg)]
    elif name == "potato_interact_and_record":
        import json
        # Parse and validate inputs using Pydantic schema
        parsed = InteractAndRecordInput(**(arguments or {}))
        launch_command = (parsed.launch_command or "").strip()
        venv_cmd = (parsed.venv or "").strip()
        inputs = parsed.inputs
        duration = int(parsed.duration_seconds)
        interval = int(parsed.frame_interval_ms)
        base = parsed.output_basename
        working_dir = (parsed.working_dir or "").strip()
        env_map = parsed.env or {}
        # Small grace period after launch so windows can appear
        post_launch_delay = int(parsed.post_launch_delay_seconds)

        # If launch_command starts with a leading 'cd <dir> && ...', extract it as working_dir
        # so that venv activation occurs in the intended directory and the remaining command runs there.
        if launch_command:
            import re as _re
            m = _re.match(r"^\s*cd\s+(.+?)\s*&&\s*(.*)$", launch_command)
            if m:
                wd_raw = m.group(1).strip()
                rest = (m.group(2) or "").strip() or "true"
                # Strip quotes around the directory if provided
                if (wd_raw.startswith("'") and wd_raw.endswith("'")) or (wd_raw.startswith('"') and wd_raw.endswith('"')):
                    wd_raw = wd_raw[1:-1]
                # Only adopt if caller didn't explicitly provide working_dir
                if not working_dir:
                    working_dir = wd_raw
                # Remove the leading cd from the launch command to avoid duplicate cd
                launch_command = rest

        # Build a script that optionally launches, detects the active window, and records a fullscreen video with ffmpeg x11grab
        _uid = uuid.uuid4().hex
        video_name = f"{base}_{_uid}.webm"
        video_out = f"/workspace/.agent/screenshots/{video_name}"
        # Derive FPS from frame_interval_ms; default to at least 1 fps
        fps = max(1, int(1000 / max(1, interval)))

        # Prepare optional env exports and working directory change
        export_snippets: list[str] = []
        if isinstance(env_map, dict):
            for k, v in env_map.items():
                try:
                    ks = str(k)
                    vs = str(v).replace("'", "'\\''")
                    export_snippets.append(f"export {ks}='{vs}'")
                except Exception:
                    continue
        exports = ("; ".join(export_snippets) + "; ") if export_snippets else ""

        cd_snippet = "cd /workspace; "
        if working_dir:
            wd = working_dir.replace("'", "'\\''")
            cd_snippet += f"cd -- '{wd}'; "

        script_lines: list[str] = [
            "set -e",
            "mkdir -p /workspace/.agent/screenshots",
            # Ensure we operate relative to the user's workspace and desired subdir, with optional env
            f"{cd_snippet}{exports}".rstrip()
        ]

        # Optionally launch target command (with optional venv activation)
        if launch_command:
            # Activate venv in current shell so $! captures the real process PID of the launched app
            if venv_cmd:
                script_lines.append(f"{venv_cmd}")
            # Launch app in background, capture its PID, and emit a marker for later parsing
            script_lines.append(
                f"{launch_command} >/tmp/launch_interact.log 2>&1 & LAUNCH_PID=$!; echo LAUNCH_PID:$LAUNCH_PID"
            )
            # Give the app a brief moment to create its window before we probe/record
            script_lines.append(f"sleep {max(0, int(post_launch_delay))}")

        # Prepare display and GUI readiness, then detect the most recently active window (non-fatal), and record
        script_lines += [
            # Ensure DISPLAY is ready before any xdotool calls
            "export DISPLAY=:0",
            "for i in 1 2 3; do xset q >/dev/null 2>&1 && break; sleep 1; done",
            # After launching, wait a bit more to allow windows to appear before probing
            "sleep 1",
            # Non-fatal xdotool queries
            "set +e",
            "active_id=\"$(xdotool getactivewindow 2>/dev/null || true)\"",
            "active_name=\"\"",
            "active_pid=\"\"",
            # Ensure LAUNCH_PID is defined even if nothing was launched
            "LAUNCH_PID=\"${LAUNCH_PID}\"",
            "if [ -n \"$active_id\" ]; then",
            "  xdotool windowraise \"$active_id\" >/dev/null 2>&1 || true",
            "  xdotool windowactivate \"$active_id\" >/dev/null 2>&1 || true",
            "  xdotool windowfocus \"$active_id\" >/dev/null 2>&1 || true",
            "  active_name=\"$(xdotool getwindowname \"$active_id\" 2>/dev/null || true)\"",
            "  active_pid=\"$(xdotool getwindowpid \"$active_id\" 2>/dev/null || true)\"",
            "fi",
            # Compare window PID to launch PID (direct or ancestor-descendant)
            "relation=unknown; pid_match=0;",
            "if [ -n \"$active_pid\" ] && [ -n \"$LAUNCH_PID\" ]; then",
            "  if [ \"$active_pid\" = \"$LAUNCH_PID\" ]; then relation=equal; pid_match=1;",
            "  else",
            "    cur=\"$active_pid\";",
            "    for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do",
            "      p=\"$(ps -o ppid= -p \"$cur\" 2>/dev/null | awk '{print $1}')\";",
            "      [ -z \"$p\" ] && break;",
            "      [ \"$p\" = \"1\" ] && break;",
            "      if [ \"$p\" = \"$LAUNCH_PID\" ]; then relation=descendant; pid_match=1; break; fi;",
            "      cur=\"$p\";",
            "    done",
            "  fi",
            "fi",
            "echo PID_MATCH:$pid_match",
            "echo PID_REL:$relation",
            "set -e",
        ]

        # After detecting the active window, optionally send user-provided inputs to that window id
        # New format: key_sequence + delay + type (once|sleep|repeat). Default type='once'.
        # Backward-compat: if key_sequence not provided, fall back to legacy keys + delay_ms handling.
        def _sec_from_ms(ms: int) -> str:
            try:
                ms_i = max(0, int(ms))
            except Exception:
                ms_i = 0
            if ms_i % 1000 == 0:
                return str(ms_i // 1000)
            val = ms_i / 1000.0
            s = f"{val:.3f}"
            # trim trailing zeros and dot
            while s.endswith("0"):
                s = s[:-1]
            if s.endswith("."):
                s = s[:-1]
            return s or "0"

        # Separate inputs into pre-capture steps and repeat sequences
        pre_steps: list[str] = []
        repeat_cmds: list[str] = []

        for item in inputs:
            action = (item.type or "once").strip().lower()
            # Normalize delay: for non-sleep actions, enforce a minimum of 20ms to avoid xdotool timing issues
            raw_delay = int(item.delay or 0)
            if action == "sleep":
                d_ms = max(0, raw_delay)
            else:
                d_ms = max(20, raw_delay)

            if action == "sleep":
                secs = _sec_from_ms(d_ms)
                pre_steps.append(f"sleep {secs}")
                continue

            # Build a key invocation for key_sequence
            if item.key_sequence:
                raw = item.key_sequence.strip()
                tokens = [t for t in raw.split() if t]
                esc_tokens = [t.replace("'", "'\\''") for t in tokens]
                token_args = " ".join(f"'{t}'" for t in esc_tokens)
                cmd = f"if [ -n \"$active_id\" ]; then xdotool key --delay {d_ms} --clearmodifiers --window \"$active_id\" {token_args} >/dev/null 2>&1 || true; fi"
            else:
                # No key_sequence provided; skip this item silently
                continue

            if action == "repeat":
                repeat_cmds.append(cmd)
            else:
                pre_steps += ["set +e", cmd, "set -e"]

        # Emit pre-capture steps now (sequential)
        script_lines += pre_steps

        # Continue with emitting markers and recording
        script_lines += [
            # Emit markers so we can parse results easily
            "echo WIN_NAME:$active_name",
            "echo WIN_PID:$active_pid",
            "echo LAUNCH_PID:$LAUNCH_PID",
            "echo WIN_ID:$active_id",
            # Determine video size
            "VSIZE=\"$(xrandr | awk '/\\*/ {print $1; exit}')\"",
            "if [ -z \"$VSIZE\" ]; then VSIZE=1280x720; fi",
        ]

        if repeat_cmds:
            # Start recording in background and loop until it ends, sending repeat sequences
            script_lines += [
                f"ffmpeg -y -loglevel error -f x11grab -framerate {fps} -video_size \"$VSIZE\" -i :0.0 -c:v libvpx-vp9 -pix_fmt yuv420p -t {duration} '{video_out}' >/dev/null 2>&1 & FF_PID=$!",
                "set +e",
                "while kill -0 \"$FF_PID\" >/dev/null 2>&1; do",
            ]
            # Add repeat commands inside the loop
            for rc in repeat_cmds:
                script_lines.append(f"  {rc}")
            script_lines += [
                "done",
                "set -e",
                "wait \"$FF_PID\" 2>/dev/null || true",
                f"echo 'OUTPUT_VIDEO: {video_out}'",
            ]
        else:
            # Record in the foreground
            script_lines += [
                f"ffmpeg -y -loglevel error -f x11grab -framerate {fps} -video_size \"$VSIZE\" -i :0.0 -c:v libvpx-vp9 -pix_fmt yuv420p -t {duration} '{video_out}' >/dev/null 2>&1",
                f"echo 'OUTPUT_VIDEO: {video_out}'",
            ]

        # If we launched an app, attempt to terminate it gracefully after recording:
        # 1) SIGINT to the window's client process id
        # 2) Wait up to 5s; if still running, SIGKILL
        if launch_command:
            script_lines += [
                "set +e",
                # First, try to gracefully stop the window's client process
                "if [ -n \"$active_pid\" ]; then",
                "  kill -s INT \"$active_pid\" >/dev/null 2>&1 || true",
                "fi",
                # Also signal the originally launched PID if it's different
                "if [ -n \"$LAUNCH_PID\" ] && [ \"$LAUNCH_PID\" != \"$active_pid\" ]; then",
                "  kill -s INT \"$LAUNCH_PID\" >/dev/null 2>&1 || true",
                "fi",
                # Wait up to 5s for both processes to exit
                "for i in 1 2 3 4 5; do",
                "  ok=1;",
                "  if [ -n \"$active_pid\" ]; then ps -p \"$active_pid\" >/dev/null 2>&1 && ok=0; fi;",
                "  if [ -n \"$LAUNCH_PID\" ]; then ps -p \"$LAUNCH_PID\" >/dev/null 2>&1 && ok=0; fi;",
                "  [ $ok -eq 1 ] && break;",
                "  sleep 1;",
                "done",
                # Force kill if still alive
                "if [ -n \"$active_pid\" ]; then ps -p \"$active_pid\" >/dev/null 2>&1 && kill -s KILL \"$active_pid\" >/dev/null 2>&1 || true; fi",
                "if [ -n \"$LAUNCH_PID\" ]; then ps -p \"$LAUNCH_PID\" >/dev/null 2>&1 && kill -s KILL \"$LAUNCH_PID\" >/dev/null 2>&1 || true; fi",
                "set -e",
            ]

        full_script = "\n".join([line for line in script_lines if line])
        task_id = str(uuid.uuid4())
        if not cm:
            raise RuntimeError("Container manager not initialized")
        exit_code, output = cm.execute_command(full_script, task_id)

        payload = {"exit_code": exit_code}
        # Parse detected window info from output
        wname = None
        wpid = None
        wid = None
        lpid = None
        pid_match = None
        pid_rel = None
        if output:
            for line in (output or "").splitlines():
                if line.startswith("WIN_NAME:"):
                    wname = line.split(":", 1)[1].strip()
                elif line.startswith("WIN_PID:"):
                    wpid = line.split(":", 1)[1].strip()
                elif line.startswith("WIN_ID:"):
                    wid = line.split(":", 1)[1].strip()
                elif line.startswith("LAUNCH_PID:"):
                    lpid = line.split(":", 1)[1].strip()
                elif line.startswith("PID_MATCH:"):
                    try:
                        pid_match = bool(int(line.split(":", 1)[1].strip()))
                    except Exception:
                        pid_match = None
                elif line.startswith("PID_REL:"):
                    pid_rel = line.split(":", 1)[1].strip()
        payload["window_name"] = wname
        payload["window_pid"] = wpid
        payload["window_id"] = wid
        if lpid is not None:
            payload["launch_pid"] = lpid
        if pid_match is not None:
            payload["pid_match"] = pid_match
        if pid_rel is not None:
            payload["pid_relation"] = pid_rel
        # Always return the container path for media
        payload["video_path"] = video_out
        payload["hint"] = _with_progress_reminder("Provide the video to the user; use 'video_path' at /workspace/.agent/screenshots/.")
        return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]
    elif name == "potato_python_run_module":
        data = PythonRunModuleInput(**(arguments or {}))
        venv = data.venv_path
        module = data.module
        args = data.args
        run_bg = bool(getattr(data, "background", False))
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
        if run_bg:
            info = container_manager.start_background_task(cmd, str(uuid.uuid4()))
            import json as _json
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"task_id": info.get("task_id"), "exit_code": info.get("exit_code"), "hint": _with_progress_reminder("Use potato_task_status to poll, potato_task_output to tail logs, and potato_task_kill to stop the module.")}))]
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            import json as _json
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "Module still running; try again with a larger timeout or set background=true.", "hint": _with_progress_reminder("Set background=true to get a task_id, then use potato_task_status to poll, potato_task_output to tail logs, and potato_task_kill to stop when done.")}))]
        import json as _json
        logger.info(f"[req={req_id}] tool={name} completed exit_code={code} module={module}")
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": _with_progress_reminder("Use output to summarize the run results concisely.")}))]
    elif name == "potato_python_run_script":
        data = PythonRunScriptInput(**(arguments or {}))
        venv = data.venv_path
        script_path = data.script_path
        args = data.args
        run_bg = bool(getattr(data, "background", False))
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
        if run_bg:
            info = container_manager.start_background_task(cmd, str(uuid.uuid4()))
            import json as _json
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"task_id": info.get("task_id"), "exit_code": info.get("exit_code"), "hint": _with_progress_reminder("Use potato_task_status to poll, potato_task_output to tail logs, and potato_task_kill to stop the script.")}))]
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            import json as _json
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "Script still running; try again with a larger timeout or set background=true.", "hint": _with_progress_reminder("Set background=true to get a task_id, then use potato_task_status to poll, potato_task_output to tail logs, and potato_task_kill to stop when done.")}))]
        import json as _json
        logger.info(f"[req={req_id}] tool={name} completed exit_code={code} script={script_path}")
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": _with_progress_reminder("Use output to summarize the run results concisely.")}))]
    elif name == "potato_python_check_syntax":
        data = PythonCheckSyntaxInput(**(arguments or {}))
        venv = data.venv_path
        src = data.source_path
        if not venv or not src:
            raise ValueError("'venv_path' and 'source_path' are required")
        def _norm(p: str) -> str:
            p = str(p).strip()
            if p.startswith("/workspace/"):
                return p
            if p.startswith("/"):
                raise ValueError("Absolute paths outside /workspace are not allowed")
            return f"/workspace/{p}"
        act = _norm(venv).rstrip("/") + "/bin/activate"
        sp = _norm(src)
        # Activate then run py_compile
        cmd = (
            f"source '{act}' && python -m py_compile '{sp}'"
        )
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            import json as _json
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "py_compile still running; try again with a larger timeout.", "hint": _with_progress_reminder("Large files or slow disks may need more time.")}))]
        import json as _json
        logger.info(f"[req={req_id}] tool={name} completed exit_code={code} src={src}")
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": _with_progress_reminder("If exit_code is 0, the file is syntactically valid; otherwise surface the compile error lines.")}))]
    elif name == "potato_pytest_run":
        data = PytestRunInput(**(arguments or {}))
        venv = data.venv_path
        args = data.args or []
        if not venv:
            raise ValueError("'venv_path' is required")
        def _norm(p: str) -> str:
            p = str(p).strip()
            if p.startswith("/workspace/"):
                return p
            if p.startswith("/"):
                raise ValueError("Absolute paths outside /workspace are not allowed")
            return f"/workspace/{p}"
        act = _norm(venv).rstrip("/") + "/bin/activate"
        arg_str = " ".join(["'" + str(a).replace("'", "'\\''") + "'" for a in args])
        cmd = f"source '{act}' && pytest {arg_str}".rstrip()
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            import json as _json
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "pytest still running; try again with a larger timeout.", "hint": _with_progress_reminder("Use -q to reduce output or target specific tests for faster runs.")}))]
        import json as _json
        logger.info(f"[req={req_id}] tool={name} completed exit_code={code}")
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": _with_progress_reminder("Summarize pass/fail counts and point to failing tests if any.")}))]
    elif name == "potato_list_repositories":
        import json
        if not cm:
            raise RuntimeError("Container manager not initialized")
        items = cm.list_local_repositories()
        return [TextContent(type="text", text=json.dumps({"items": items, "hint": _with_progress_reminder("Use these repository entries to navigate or run git operations; avoid dumping full repo trees inline.")}, ensure_ascii=False))]
    elif name == "potato_git_add":
        repo_path = arguments.get("repo_path")
        paths = arguments.get("paths") or []
        if not repo_path:
            raise ValueError("'repo_path' is required")
        path_args = " ".join(["'" + str(p).replace("'", "'\\''") + "'" for p in paths]) if paths else "-A"
        cmd = (
            "cd /workspace && "
            f"cd -- '{str(repo_path).replace("'", "'\\''")}' && "
            f"git add {path_args}"
        )
        import json as _json
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            import json as _json
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "git push still running; try again with a larger timeout.", "hint": _with_progress_reminder("Increase timeout_seconds for slow networks or large pushes.")}))]
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({
            "exit_code": code,
            "output": out,
            "hint": _with_progress_reminder("Required next step: make a commit. If exit_code is 0, immediately run potato_git_commit with a clear, concise message summarizing what changed and why.")
        }))]
    elif name == "potato_git_commit":
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
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            import json as _json
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "git pull still running; try again with a larger timeout.", "hint": _with_progress_reminder("Increase timeout_seconds for slow networks or large updates.")}))]
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": _with_progress_reminder("If commit succeeded, summarize the commit message and next steps (push or create PR).")}))]
    elif name == "potato_git_push":
        repo_path = arguments.get("repo_path")
        remote = arguments.get("remote", "origin")
        branch = arguments.get("branch")
        set_upstream = bool(arguments.get("set_upstream", False))
        if not bool(arguments.get("confirm", False)):
            import json as _json
            msg = {
                "exit_code": 2,
                "message": "Push requires explicit approval.",
                "hint": _with_progress_reminder("Do not run this tool unless the user clearly asked to push. Ask the user to confirm and set confirm=true when calling this tool."),
                "required_action": "Ask for user confirmation to proceed with git push.",
            }
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps(msg))]
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
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            import json as _json
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "gh view still running; try again with a larger timeout.", "hint": _with_progress_reminder("Increase timeout_seconds if the GitHub API is slow.")}))]
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": _with_progress_reminder("If push succeeded, share the branch and next steps (e.g., open PR). On failure, show the error and suggest pull/rebase.")}))]
    elif name == "potato_git_pull":
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
        if not cm:
            raise RuntimeError("Container manager not initialized")
        code, out = cm.execute_command(cmd, str(uuid.uuid4()))
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": _with_progress_reminder("If pull succeeded, summarize changes. If conflicts, advise resolving and committing.")}))]
    elif name == "potato_git_branch_create":
        repo_path = arguments.get("repo_path")
        bname = arguments.get("name")
        start = (arguments.get("start_point") or "").strip()
        checkout = bool(arguments.get("checkout", True))
        if not repo_path or not bname:
            raise ValueError("'repo_path' and 'name' are required")
        bq = str(bname).replace("'", "'\\''")
        start_clause = f" '{start.replace("'", "'\\''")}'" if start else ""
        if checkout:
            # git checkout -b <name> [start]
            cmd = (
                "cd /workspace && "
                f"cd -- '{str(repo_path).replace("'", "'\\''")}' && "
                f"git checkout -b '{bq}'{start_clause}"
            )
        else:
            # git branch <name> [start]
            cmd = (
                "cd /workspace && "
                f"cd -- '{str(repo_path).replace("'", "'\\''")}' && "
                f"git branch '{bq}'{start_clause}"
            )
        import json as _json
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "git branch create still running; increase timeout_seconds.", "hint": _with_progress_reminder("If creating from a remote start point, ensure you have fetched first.")}))]
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": _with_progress_reminder("If created successfully, begin committing changes on this branch.")}))]
    elif name == "potato_git_branch_delete":
        repo_path = arguments.get("repo_path")
        bname = arguments.get("name")
        force = bool(arguments.get("force", False))
        if not repo_path or not bname:
            raise ValueError("'repo_path' and 'name' are required")
        bq = str(bname).replace("'", "'\\''")
        flag = "-D" if force else "-d"
        cmd = (
            "cd /workspace && "
            f"cd -- '{str(repo_path).replace("'", "'\\''")}' && "
            f"git branch {flag} '{bq}'"
        )
        import json as _json
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "git branch delete still running; increase timeout_seconds.", "hint": _with_progress_reminder("Use force=true to delete an unmerged branch if you are certain.")}))]
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": _with_progress_reminder("If deletion succeeded, prune remote branches if needed and update any open PRs.")}))]
    elif name == "potato_git_merge":
        repo_path = arguments.get("repo_path")
        source = arguments.get("source_branch")
        target = (arguments.get("target_branch") or "").strip()
        no_ff = bool(arguments.get("no_ff", True))
        no_edit = bool(arguments.get("no_edit", True))
        if not repo_path or not source:
            raise ValueError("'repo_path' and 'source_branch' are required")
        sq = str(source).replace("'", "'\\''")
        tq = str(target).replace("'", "'\\''") if target else ""
        # If target not provided, detect main/master; fallback to 'main' then 'master'
        detect_cmd = (
            "cd /workspace && "
            f"cd -- '{str(repo_path).replace("'", "'\\''")}' && "
            "git rev-parse --verify main >/dev/null 2>&1 && echo main || (git rev-parse --verify master >/dev/null 2>&1 && echo master || echo main)"
        )
        import json as _json
        if not target:
            if not cm:
                raise RuntimeError("Container manager not initialized")
            t_to = cm.execute_command(detect_cmd, str(uuid.uuid4()))
            try:
                _code, _out = t_to
            except Exception:
                _code, _out = (0, "main")
            target = (_out or "main").strip().splitlines()[0] if _out else "main"
            tq = str(target).replace("'", "'\\''")
        # Checkout target, merge source into target with options
        merge_opts = (" --no-ff" if no_ff else "") + (" --no-edit" if no_edit else "")
        cmd = (
            "cd /workspace && "
            f"cd -- '{str(repo_path).replace("'", "'\\''")}' && "
            f"git checkout '{tq}' && git merge{merge_opts} '{sq}'"
        )
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "git merge still running; increase timeout_seconds.", "hint": _with_progress_reminder("Resolve conflicts if present, then commit the merge.")}))]
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": _with_progress_reminder("If merge succeeded, summarize the merged changes and consider pushing the updated target branch if approved.")}))]
    elif name == "potato_git_checkout":
        repo_path = arguments.get("repo_path")
        branch = arguments.get("branch")
        if not repo_path or not branch:
            raise ValueError("'repo_path' and 'branch' are required")
        bq = str(branch).replace("'", "'\\''")
        cmd = (
            "cd /workspace && "
            f"cd -- '{str(repo_path).replace("'", "'\\''")}' && "
            f"git checkout '{bq}'"
        )
        import json as _json
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "git checkout still running; increase timeout_seconds.", "hint": _with_progress_reminder("Ensure the branch exists locally or fetch remote branches first.")}))]
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": _with_progress_reminder("Switched branches. Remember to commit or stash any local changes before switching back if needed.")}))]
    elif name == "github_get_repository":
        if not cm or not cm.is_github_available():
            raise RuntimeError("GitHub CLI is not available. Set GITHUB_PERSONAL_ACCESS_TOKEN in local/.env")
        owner = arguments.get("owner")
        repo = arguments.get("repo")
        if not owner or not repo:
            raise ValueError("Both 'owner' and 'repo' are required")
        # Request common fields as JSON
        fields = "name,description,sshUrl,homepageUrl,url,defaultBranchRef,visibility,createdAt,updatedAt,owner"
        cmd = f"gh repo view {owner}/{repo} --json {fields}"
        import json as _json
        code, out = cm.execute_command(cmd, str(uuid.uuid4()))
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
        payload["hint"] = _with_progress_reminder("Use repository data to navigate or clone; present key fields (name, description, default branch) to the user concisely.")
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps(payload))]
    elif name == "potato_git_status":
        repo_path = arguments.get("repo_path")
        if not repo_path:
            raise ValueError("'repo_path' is required")
        porcelain = bool(arguments.get("porcelain", True))
        fmt = " --porcelain=v1 -b" if porcelain else ""
        cmd = (
            "cd /workspace && "
            f"cd -- '{str(repo_path).replace("'", "'\\''")}' && "
            f"git status{fmt}"
        )
        import json as _json
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "git status still running; increase timeout_seconds.", "hint": _with_progress_reminder("Large repos may need more time.")}))]
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": _with_progress_reminder("Summarize the key changes (modified, added, deleted) and branch info for the user.")}))]
    elif name == "potato_git_diff":
        repo_path = arguments.get("repo_path")
        if not repo_path:
            raise ValueError("'repo_path' is required")
        staged = bool(arguments.get("staged", False))
        name_only = bool(arguments.get("name_only", False))
        unified = arguments.get("unified", 3)
        try:
            u = int(unified)
            if u < 0:
                u = 0
        except Exception:
            u = 3
        files = arguments.get("paths") or []
        files_q = " ".join(["'" + str(p).replace("'", "'\\''") + "'" for p in files])
        # Use --unified=N to bind the value with the option and add '--' before file paths
        # to disambiguate files from revisions (prevents errors like: ambiguous argument '3').
        base = f"git diff{' --cached' if staged else ''}{' --name-only' if name_only else ''} --unified={u}"
        sep = " -- " if files_q else ""
        cmd = (
            "cd /workspace && "
            f"cd -- '{str(repo_path).replace("'", "'\\''")}' && "
            f"{base}{sep}{files_q}"
        )
        import json as _json
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "git diff still running; increase timeout_seconds.", "hint": _with_progress_reminder("For large diffs, consider name_only=true to list files first.")}))]
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": _with_progress_reminder("If the diff is long, summarize key hunks and call out risky changes; include file list with name_only when helpful.")}))]
    
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

    # Create or reuse container manager
    if container_manager is None:
        # In test/integration contexts, avoid clobbering the production container by name.
        # Always generate a unique test-specific name to prevent accidental reuse of production names.
        import os as _os
        import uuid as _uuid
        test_mode = (
            _os.getenv("POTATO_IT_ENABLE", "0").lower() in ("1", "true", "yes")
            or _os.getenv("RUN_INTEGRATION_TESTS", "0") == "1"
            or ("PYTEST_CURRENT_TEST" in _os.environ)
        )
        if test_mode:
            unique = _uuid.uuid4().hex[:8]
            safe_name = f"effective-potato-sandbox-it-{unique}"
            container_manager = ContainerManager(container_name=safe_name)
        else:
            container_manager = ContainerManager()
    # Build and start container (idempotent-ish for tests that inject a manager)
    try:
        container_manager.build_image()
    except Exception as e:
        logger.warning(f"Image build failed or skipped: {e}")
    try:
        ok = container_manager.ensure_container_alive()
        if not ok:
            # As a fallback, attempt a full start
            try:
                container_manager.start_container()
            except Exception as e2:
                logger.warning(f"Container start encountered an issue: {e2}")
    except Exception as e:
        logger.warning(f"Container ensure/start encountered an issue: {e}")

    # On startup, repair/cleanup the local tracked repos list by removing entries whose directories are missing
    try:
        container_manager.prune_tracked_repositories(dry_run=False)
    except Exception as e:
        logger.warning(f"Workspace prune on startup failed: {e}")

    # HTTP server removed: artifacts are referenced by absolute container paths only

    # Write readiness file for clients/diagnostics
    try:
        import datetime as _dt
        import json as _json
        import os as _os
        from pathlib import Path as _Path
        now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        running = False
        try:
            running = bool(container_manager.is_container_running())
        except Exception:
            running = False
        state = {
            "version": 1,
            "timestamp": now,
            "up": True,
            "container": {
                "name": getattr(container_manager, "container_name", None),
                "id": container_manager.get_container_id(),
                "running": running,
            },
            "server": {
                "pid": _os.getpid(),
            },
        }
        p = _Path(container_manager.workspace_dir) / ".agent" / "potato_ready.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps(state, indent=2))
        logger.info(f"Wrote readiness state: {p}")
    except Exception as e:
        logger.warning(f"Failed to write readiness state file: {e}")

    # Start a lightweight watchdog to keep the container alive
    import threading as _th
    import time as _time
    def _watchdog():
        while True:
            try:
                if container_manager and not container_manager.is_container_running():
                    logger.warning("Container stopped; attempting to restart...")
                    ok = container_manager.ensure_container_alive()
                    if ok:
                        try:
                            cid = container_manager.get_container_id()
                        except Exception:
                            cid = None
                        logger.info(f"Container restarted successfully; id={str(cid)[:12] if cid else 'unknown'}")
                    else:
                        logger.error("Container restart failed; will retry")
                # Also a periodic gentle ping via a no-op command to surface issues
                elif container_manager:
                    try:
                        container_manager.execute_command("true", str(uuid.uuid4()))
                    except Exception:
                        # If exec fails, try to restart on next loop
                        pass
            except Exception as e:
                logger.debug(f"Watchdog error: {e}")
            _time.sleep(5)
    _th.Thread(target=_watchdog, daemon=True).start()

    logger.info("Server initialized successfully")


def cleanup_server() -> None:
    """Clean up server resources (no HTTP server to stop)."""
    global container_manager
    if container_manager:
        logger.info("Cleaning up server...")
        container_manager.cleanup()
        container_manager = None


def main() -> None:
    """Main entry point for the server."""

    import contextlib

    from starlette.applications import Starlette
    from starlette.routing import Route
    import uvicorn

    class _StreamableHTTPASGIApp:
        def __init__(self, session_manager: StreamableHTTPSessionManager):
            self._session_manager = session_manager

        async def __call__(self, scope, receive, send) -> None:
            await self._session_manager.handle_request(scope, receive, send)

    def _create_starlette_app() -> Starlette:
        session_manager = StreamableHTTPSessionManager(
            app=app,
            json_response=(_env_str("POTATO_MCP_JSON_RESPONSE", "0").lower() in ("1", "true", "yes")),
            stateless=(_env_str("POTATO_MCP_STATELESS", "0").lower() in ("1", "true", "yes")),
        )
        mcp_path = _env_str("POTATO_MCP_PATH", "/mcp")
        asgi = _StreamableHTTPASGIApp(session_manager)

        @contextlib.asynccontextmanager
        async def lifespan(_app: Starlette):
            async with session_manager.run():
                yield

        return Starlette(routes=[Route(mcp_path, endpoint=asgi)], lifespan=lifespan)

    try:
        initialize_server()

        host = _env_str("POTATO_HOST", "127.0.0.1")
        port = _env_int("POTATO_PORT", 8000)
        log_level = _env_str("POTATO_HTTP_LOG_LEVEL", "info").lower()

        uvicorn.run(
            _create_starlette_app(),
            host=host,
            port=port,
            log_level=log_level,
        )
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    finally:
        cleanup_server()


if __name__ == "__main__":
    main()
