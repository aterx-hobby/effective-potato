"""MCP server for effective-potato."""

import logging
import uuid
from typing import Any
import os
from mcp.server import Server
from mcp.types import Tool, TextContent
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal
from .container import ContainerManager
from .web import (
    create_app as create_http_app,
    start_http_server,
    get_server_config,
    build_screenshot_url,
    get_tool_schema_url,
    record_tool_metric,
    stop_http_server,
)

logger = logging.getLogger(__name__)
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


class TarCreateInput(BaseModel):
    base_dir: str = Field(default=".", description="Workspace-relative directory to run tar from")
    items: list[str] = Field(description="Relative paths (files/dirs) to include in archive")
    archive_name: str | None = Field(default=None, description="Optional archive name (defaults to timestamped)")


class DigestInput(BaseModel):
    path: str = Field(description="Workspace-relative path to file to hash")
    algorithm: str = Field(default="sha256", description="Hash algorithm: sha256 or md5")

def _is_v4a_patch(text: str) -> bool:
    if not isinstance(text, str):
        return False
    s = text.strip()
    return ("*** Begin Patch" in s) and ("*** End Patch" in s) and ("*** Update File:" in s or "*** Add File:" in s or "*** Delete File:" in s)


class ApplyPatchInput(BaseModel):
    base_dir: str = Field(default=".", description="Workspace-relative directory to apply the patch in")
    diff: str = Field(description="Unified diff content: either Git-style a/b headers ('--- a/...', '+++ b/...') or V4A apply_patch format (*** Begin Patch ... *** End Patch).")
    strategy: Literal["git", "patch"] = Field(
        default="git",
        description=(
            "Apply using 'git apply' (default) or the 'patch' utility for Git-style diffs. V4A apply_patch format is auto-detected and applied directly (strategy is ignored)."
        ),
    )
    strip: int = Field(
        default=1,
        ge=0,
        description="Path strip count for 'patch' (-pN). For Git-style a/b diffs this must be 1 (i.e., -p1). Ignored for V4A format.",
    )
    reject: bool = Field(default=True, description="If true, allow partial application and emit .rej hunks when supported")

    @field_validator("strip")
    @classmethod
    def _enforce_p1_for_ab(cls, v: int, info):
        # For git/patch strategies, enforce -p1 for a/b formatted diffs
        data = info.data or {}
        strategy = data.get("strategy", "git")
        diff = data.get("diff", "")
        if strategy in {"git", "patch"} and (not _is_v4a_patch(diff)) and v != 1:
            raise ValueError("For Git-style a/b diffs, strip must be 1 (-p1)")
        return v

    @model_validator(mode="after")
    def _validate_ab_headers(self):
        # For git/patch strategies, require presence of a/b headers
        if self.strategy in {"git", "patch"}:
            if _is_v4a_patch(self.diff):
                return self
            if ("--- a/" not in self.diff) or ("+++ b/" not in self.diff):
                raise ValueError("Apply patch expects Git-style a/b headers: include '--- a/...' and '+++ b/...', or use V4A '*** Begin Patch' format.")
        return self


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
_http_thread = None
_http_server = None
_public_host = None
_public_port = None

# Read-only toolset exposure for code-review models
# These base tool names are considered safe (no write/mutation of workspace state)
READ_ONLY_BASE_TOOLS: set[str] = {
    "workspace_git_status",
    "workspace_git_diff",
    "workspace_read_file",
    "workspace_find",
    "workspace_find_venvs",
    "workspace_select_venv",
    "workspace_list_repositories",
    "workspace_task_status",
    "workspace_task_output",
    "workspace_task_list",
    "workspace_file_digest",
    # GitHub read-only metadata
    "github_get_repository",
}


def _is_review_only_mode() -> bool:
        """Return True when server should expose only read-only review toolkit.

        Controlled by POTATO_TOOLKIT env var:
            POTATO_TOOLKIT in { 'review', 'review-only', 'review_only' }
        """
        v = (os.getenv("POTATO_TOOLKIT", "") or "").strip().lower()
        return v in {"review", "review-only", "review_only"}


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
            name="workspace_launch_and_screenshot",
            description=(
                "Launch an app and then capture a fullscreen screenshot. Optionally accept 'venv' to activate before running the launch_command (useful for Python apps)."
            ),
            inputSchema=_schema(LaunchAndScreenshotInput),
        )
    )

    # Workspace: screenshot only (decoupled from launch)
    tools.append(
        Tool(
            name="workspace_screenshot",
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
            name="workspace_interact_and_record",
            description=(
                "Optionally launch an app, perform light UI interactions, and record the desktop to a WebM file. "
                "Pass 'venv' if you need to activate a Python environment before launch. You can also set working_dir and env. "
                "Returns JSON containing 'video_url', window info, and 'exit_code'.\n"
                "Use the exact video_url as returned (including its port number)—do not modify the host or port; simply embed it in your response.\n\n"
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
            name="workspace_task_output",
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
            name="workspace_task_list",
            description="List known background task IDs; optionally include per-task status",
            inputSchema={
                "type": "object",
                "properties": {"include_status": {"type": "boolean", "default": False}},
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
                "Search the workspace or a subdirectory, pruning .git, .agent, and venv-like directories. Supports name glob and type filter."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "name": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["any", "a", "file", "f", "dir", "d"],
                        "default": "any",
                        "description": "Result filter: any|a (all entries), file|f (-type f), dir|d (-type d).",
                    },
                },
            },
        )
    )
    tools.append(
        Tool(
            name="workspace_select_venv",
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
            name="workspace_find_venvs",
            description="Find virtualenv roots by matching *venv*/*_env* folders or bin/activate paths (prunes .git and .agent). Also returns 'venv_roots' and 'activations' with 'source <venv_root>/bin/activate' commands.",
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
            name="workspace_apply_patch",
            description=(
                "Apply changes from either (1) a Git-style a/b unified diff (preferred) or (2) a V4A apply_patch block. "
                "For Git-style diffs, your diff MUST contain '--- a/<path>' and '+++ b/<path>' and at least one '@@' hunk; strip is -p1. "
                "For V4A, include '*** Begin Patch' with one or more '*** Update File: <path>' sections; hunks are delimited by '@@'. "
                "We auto-detect the format. Git-style diffs are applied via 'git apply' (default) or 'patch -p1'; V4A diffs are applied directly by reconstructing the file.\n"
                "Tip (Git-style without a repo): diff -u --label a/<path> --label b/<path> old new"
            ),
            inputSchema=_schema(ApplyPatchInput),
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

    # (Removed duplicate workspace_interact_and_record registration)

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

    # Workspace: git status and diff for review
    tools.append(
        Tool(
            name="workspace_git_status",
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
            name="workspace_git_diff",
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

    # Also expose a read-only "review_" toolset for code-review models.
    # We duplicate schemas/descriptions from the corresponding base tools and mark them as read-only.
    # Clients can gate to just these prefixed tools for non-mutating operations.
    name_to_tool: dict[str, Tool] = {t.name: t for t in tools}
    for base in sorted(READ_ONLY_BASE_TOOLS):
        base_tool = name_to_tool.get(base)
        if not base_tool:
            # Base tool not available in this runtime (e.g., github_* when gh is missing)
            continue
        # Clone schema and add a vendor extension flag
        schema = base_tool.inputSchema
        try:
            # Make a shallow copy to avoid mutating original
            schema_copy = dict(schema) if isinstance(schema, dict) else schema
            if isinstance(schema_copy, dict):
                schema_copy = {**schema_copy, "x-readonly": True, "x-toolkit": "review"}
        except Exception:
            schema_copy = schema
        tools.append(
            Tool(
                name=f"review_{base}",
                description=f"[READ-ONLY] {base_tool.description}",
                inputSchema=schema_copy,
            )
        )

    # If in review-only mode, only expose the prefixed review toolkit
    if _is_review_only_mode():
        return [t for t in tools if t.name.startswith("review_")]

    return tools


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls."""
    # Support "review_"-prefixed tools by mapping to their base names with a strict whitelist.
    review_mode = False
    if isinstance(name, str) and name.startswith("review_"):
        base = name[len("review_"):]
        if base not in READ_ONLY_BASE_TOOLS:
            raise ValueError(f"Review tool not allowed: {name}")
        # Map to base for execution
        name = base
        review_mode = True

    # In review-only mode, block direct calls to non-prefixed tools
    if _is_review_only_mode() and not review_mode:
        raise ValueError("This server is running in review-only mode; use review_*-prefixed tools only.")

    # Only require container_manager for tools that interact with the container
    container_required = name not in {"workspace_select_venv", "workspace_recommended_flow"}
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

        # Background mode support
        run_bg = bool(arguments.get("background", False))

        result_holder: dict[str, Any] = {}

        env_map = arguments.get("env") or {}

        if run_bg:
            info = container_manager.start_background_task(command, task_id, extra_env=env_map)
            import json as _json
            payload = {"task_id": info.get("task_id", task_id), "exit_code": info.get("exit_code"), "hint": "Use workspace_task_status to poll, workspace_task_output to read logs, and workspace_task_kill to stop the process."}
            logger.info(f"[req={req_id}] tool={name} started background task_id={task_id}")
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps(payload))]

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
                "hint": "If you need the final output, call again with a larger timeout or poll until running=false. Alternatively, rerun with background=true and use workspace_task_output to tail logs and workspace_task_kill to stop when done.",
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
    # 'workspace_recommended_flow' intentionally disabled
    elif name == "workspace_screenshot":
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
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "Screenshot still running; try again with a larger timeout.", "hint": "Increase timeout_seconds if you need to wait longer for the desktop to settle before capture."}))]
        import json as _json
        resp = {"exit_code": exit_code, "screenshot_path": out_path, "output": output}
        if _public_host and _public_port:
            url = build_screenshot_url(_public_host, int(_public_port), out_name)
            resp["screenshot_url"] = url
        resp["hint"] = (
            "You must show the screenshot to the user. If screenshot_url is present, embed it inline. "
            "Example (Markdown): ![screenshot]({screenshot_url}). Do not alter the URL."
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

        activate = f"source {best}/bin/activate" if best else None
        payload = {"best": best, "candidates": list(paths), "activate": activate}
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
        payload = {"task_id": task_id, **info, "hint": "Use workspace_task_status to poll, workspace_task_output to tail logs, and workspace_task_kill to terminate if needed."}
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps(payload))]
    elif name == "workspace_task_status":
        task_id = arguments.get("task_id")
        if not task_id:
            raise ValueError("'task_id' is required")
        status = container_manager.get_task_status(task_id)
        import json as _json
        status["hint"] = "If running=true, continue polling or use workspace_task_output to tail logs. When exit_code is not None, summarize results and surface artifacts."
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
    elif name == "workspace_task_output":
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
        code, out = container_manager.execute_command(cmd, str(uuid.uuid4()))
        import json as _json
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "content": out or "", "path": out_path, "hint": "Show a concise excerpt (use tail for long logs) and offer to open or download if needed."}))]
    elif name == "workspace_task_list":
        include_status = bool(arguments.get("include_status", False)) if isinstance(arguments, dict) else False
        # List files matching task_*.pid under tmp_scripts; derive task IDs
        probe = (
            "cd /workspace/.agent/tmp_scripts 2>/dev/null || exit 0; "
            "ls -1 task_*.pid 2>/dev/null | sed -e 's/^task_//' -e 's/\\.pid$//'"
        )
        code, out = container_manager.execute_command(probe, str(uuid.uuid4()))
        raw_lines = [line.strip() for line in (out or "").splitlines() if line.strip()]
        def _tid(line: str) -> str:
            if line.startswith("task_") and line.endswith(".pid"):
                return line[len("task_"):-len(".pid")]
            return line
        task_ids = [_tid(l) for l in raw_lines]
        payload = {"exit_code": code, "tasks": task_ids}
        if include_status and task_ids:
            statuses = {}
            for tid in task_ids:
                try:
                    statuses[tid] = container_manager.get_task_status(tid)
                except Exception as e:
                    statuses[tid] = {"error": str(e)}
            payload["statuses"] = statuses
        import json as _json
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps(payload))]
    
    
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
        venv_cmd = (data.venv or "").strip() or None
        if not launch_command:
            raise ValueError("'launch_command' is required")
        
        # Build the script to launch the app and screenshot
        import time
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
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "Launch and capture still running; try again with a larger timeout.", "hint": "Increase timeout_seconds if the app needs longer to render before capture."}))]
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
        exit_code, output = container_manager.execute_command(full_script, task_id)

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
        if _public_host and _public_port:
            from .web import build_screenshot_url as _b
            payload["video_url"] = _b(_public_host, int(_public_port), video_name)
        else:
            # Fallback: expose the container path in the video_url field when public URL is unavailable
            payload["video_url"] = video_out
        payload["hint"] = (
            "You must show the video to the user. Provide a clickable Markdown link using the exact video_url returned "
            "(do not alter host, port, or path). Example (Markdown): [Open video]({video_url})."
        )
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
        ftype_raw = (arguments.get("type") or "any").lower()
        # Accept shorthand aliases
        if ftype_raw in {"a"}:
            ftype = "any"
        elif ftype_raw in {"f"}:
            ftype = "file"
        elif ftype_raw in {"d"}:
            ftype = "dir"
        else:
            ftype = ftype_raw
        type_clause = ""
        if ftype == "file":
            type_clause = "-type f"
        elif ftype == "dir":
            type_clause = "-type d"
        # Build a name clause that supports substring matches and extension-trimming
        name_clause = ""
        if isinstance(name_pat, str) and name_pat:
            raw_name = name_pat.strip()
            # If the caller supplied explicit wildcards (e.g., *.py), honor it exactly
            if any(ch in raw_name for ch in ["*", "?", "[", "]"]):
                esc = raw_name.replace("'", "'\\''")
                name_clause = f"-name '{esc}'"
            else:
                # Trim common single extension (e.g., snake.py -> snake) and do substring match
                base_name = raw_name.rsplit(".", 1)[0] if "." in raw_name else raw_name
                patterns: list[str] = []
                if base_name:
                    # Primary pattern: substring anywhere
                    patterns.append(f"*{base_name}*")
                # If the provided name had an extension, optionally also match the raw form
                if base_name != raw_name:
                    patterns.append(f"*{raw_name}*")
                # Deduplicate while preserving order
                seen = set()
                uniq_patterns = []
                for p in patterns:
                    if p not in seen:
                        seen.add(p)
                        uniq_patterns.append(p)
                if uniq_patterns:
                    # Build grouped -name clauses: ( -name 'p1' -o -name 'p2' )
                    parts = []
                    for pat in uniq_patterns:
                        esc = pat.replace("'", "'\\''")
                        parts.append(f"-name '{esc}'")
                    name_clause = "\\( " + " -o ".join(parts) + " \\)"

        # find with escaped parentheses for prune rules: skip .git, .agent, *venv*, *_env*
        prune = "\\( -name .git -o -name .agent -o -name '*venv*' -o -name '*_env*' \\) -prune"
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
        timed_out, exit_code, output = _exec_with_timeout(find_cmd, arguments=arguments)
        if timed_out:
            import json as _json
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "Search still running; try again with a larger timeout.", "hint": "Increase timeout_seconds for very large directories."}))]
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
            base_host = (_Path(container_manager.workspace_dir) / rel).resolve()
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
            "hint": "Chain these: (1) pass venv_roots (or items) to workspace_select_venv to get 'activate'; (2) provide that string as 'venv' to workspace_launch_and_screenshot or workspace_interact_and_record (optionally set 'launch_command'); those tools will run '<venv> && <launch_command>' for you."
        }))]
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
        # Exclude the archive itself and silence 'file changed as we read it' warnings to return exit code 0
        # Keep option ordering so tests that assert prefix 'tar -czf' still pass.
        cmd = (
            "cd /workspace && "
            f"cd -- '{base_rel.replace("'", "'\\''")}' && "
            f"tar -czf '{arch_q}' --warning=no-file-changed --exclude '{arch_q}' {items_quoted}"
        )
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            import json as _json
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "Module still running; try again with a larger timeout.", "hint": "Use timeout_seconds when modules need longer to run."}))]
        import json as _json
        # Some tar variants return exit code 1 for benign warnings; treat 0/1 as success
        code_out = 0 if code in (0, 1) else code
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code_out, "archive": f"/workspace/{base_rel}/{arch_name}", "output": out, "hint": "You can download or share the archive path as needed."}))]
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
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            import json as _json
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "Script still running; try again with a larger timeout.", "hint": "Use timeout_seconds for scripts that take longer."}))]
        digest = (out or "").strip().split()[0] if out else ""
        import json as _json
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "algorithm": algo, "digest": digest, "path": f"/workspace/{rel}", "hint": "Store this digest to verify file integrity later."}))]
    elif name == "workspace_apply_patch":
        data = ApplyPatchInput(**(arguments or {}))
        import json as _json, re as _re
        base = (data.base_dir or ".").strip()
        attempts: list[dict[str, Any]] = []
        strategy_used = data.strategy

        # If it's a V4A patch, handle it directly without invoking git/patch
        if _is_v4a_patch(data.diff):
            v4a = data.diff
            # Extract update blocks
            file_blocks: list[tuple[str, str]] = []  # (path, block_text)
            cur_path: str | None = None
            cur_lines: list[str] = []
            in_patch = False
            for line in v4a.splitlines():
                if line.startswith("*** Begin Patch"):
                    in_patch = True
                    continue
                if line.startswith("*** End Patch"):
                    break
                if in_patch and line.startswith("*** Update File:"):
                    # flush previous
                    if cur_path is not None:
                        file_blocks.append((cur_path, "\n".join(cur_lines)))
                    cur_path = line.split(":", 1)[1].strip()
                    cur_lines = []
                    continue
                if in_patch and cur_path is not None:
                    cur_lines.append(line)
            if cur_path is not None:
                file_blocks.append((cur_path, "\n".join(cur_lines)))

            def _apply_unified_hunks(original: str, hunk_text: str) -> tuple[bool, str]:
                """Apply unified hunks to a single file content, tolerant of offsets.

                Rules:
                - Lines starting with ' ' are context: we seek forward from the current index to match and copy through.
                - Lines starting with '-' are deletions: we seek forward to find the line and drop it.
                - Lines starting with '+' are insertions: we insert at the current output position.
                - '@@' resets hunk state; file-level headers inside block are ignored.
                """
                src = original.splitlines(keepends=False)
                out: list[str] = []
                i = 0  # index into src
                in_hunk = False

                def append_through(to_idx: int):
                    nonlocal i
                    if to_idx > i:
                        out.extend(src[i:to_idx])
                        i = to_idx

                for raw in hunk_text.splitlines():
                    if raw.startswith("@@"):
                        in_hunk = True
                        continue
                    if not in_hunk:
                        continue
                    if raw.startswith("--- ") or raw.startswith("+++ "):
                        continue
                    if raw.startswith(" "):
                        ctx = raw[1:]
                        # find next occurrence of ctx at or after i
                        j = i
                        found = False
                        while j < len(src):
                            if src[j] == ctx:
                                found = True
                                break
                            j += 1
                        if not found:
                            return False, original
                        append_through(j + 1)
                        out.append(ctx)
                    elif raw.startswith("-") and not raw.startswith("--- "):
                        old = raw[1:]
                        j = i
                        found = False
                        while j < len(src):
                            if src[j] == old:
                                found = True
                                break
                            j += 1
                        if not found:
                            return False, original
                        # copy up to just before j, then drop j and advance input
                        append_through(j)
                        i = j + 1
                    elif raw.startswith("+") and not raw.startswith("+++ "):
                        out.append(raw[1:])
                    else:
                        # ignore unknown
                        pass
                # copy remainder
                out.extend(src[i:])
                # preserve trailing newline if present
                return True, "\n".join(out) + ("\n" if original.endswith("\n") else "")

            applied = []
            for rel_path, block in file_blocks:
                target = rel_path.strip()
                if target.startswith("/"):
                    # Force workspace-relative
                    target = target[1:]
                rel = f"{base.rstrip('/')}/{target}" if base and base != "." else target
                try:
                    current = container_manager.read_workspace_file(rel, binary=False)
                except Exception:
                    current = ""
                ok, new_content = _apply_unified_hunks(str(current), block)
                if not ok:
                    attempts.append({"strategy": "v4a", "exit_code": 2, "output": f"Failed to apply hunks for {rel}"})
                    record_tool_metric(name, int(__t.time()*1000) - __start_ms)
                    return [TextContent(type="text", text=_json.dumps({
                        "exit_code": 2,
                        "output": f"Failed to apply hunks for {rel}",
                        "strategy_used": "v4a",
                        "attempts": attempts,
                    }))]
                container_manager.write_workspace_file(rel, new_content, append=False, executable=False)
                applied.append(rel)
            attempts.append({"strategy": "v4a", "exit_code": 0, "output": f"updated {len(applied)} files", "files": applied})
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({
                "exit_code": 0,
                "output": f"updated {len(applied)} files",
                "strategy_used": "v4a",
                "attempts": attempts,
            }))]

        # Otherwise proceed with Git-style a/b patching
        patch_uid = uuid.uuid4().hex
        rel_patch_path = f".agent/tmp_scripts/patch_{patch_uid}.diff"
        container_manager.write_workspace_file(rel_patch_path, data.diff)

        def _run_git() -> tuple[int | None, str | None, bool]:
            reject_clause = " --reject" if data.reject else ""
            cmd = (
                "cd /workspace && "
                f"cd -- '{base.replace("'", "'\\''")}' && "
                f"git apply{reject_clause} --whitespace=nowarn '/workspace/{rel_patch_path.replace("'", "'\\''")}'"
            )
            timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
            attempts.append({"strategy": "git", "timed_out": timed_out, "exit_code": code, "output": out})
            # Return (code, out, done)
            if timed_out:
                return None, None, True
            return code, out, code == 0

        def _run_patch() -> tuple[int | None, str | None]:
            pnum = max(0, int(data.strip or 0))
            cmd = (
                "cd /workspace && "
                f"cd -- '{base.replace("'", "'\\''")}' && "
                f"patch -p{pnum} -s -i '/workspace/{rel_patch_path.replace("'", "'\\''")}'"
            ).strip()
            timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
            attempts.append({"strategy": "patch", "timed_out": timed_out, "exit_code": code, "output": out, "strip": pnum})
            if timed_out:
                return None, None
            return code, out

        code: int | None = None
        out: str | None = None

        if data.strategy == "git":
            gcode, gout, done = _run_git()
            if gcode is None and gout is None and done:  # timed out
                record_tool_metric(name, int(__t.time()*1000) - __start_ms)
                return [TextContent(type="text", text=_json.dumps({
                    "exit_code": None,
                    "timeout_seconds": arguments.get("timeout_seconds", 120) if isinstance(arguments, dict) else 120,
                    "message": "Patch application still running; try again with a larger timeout.",
                    "attempts": attempts,
                    "hint": "If the patch is large, increase timeout_seconds. Consider strategy='patch' for plain unified diffs."
                }))]
            if done:
                code, out = gcode, gout
            else:
                # Heuristic fallback to patch when git reports format/validity issues
                strategy_used = "patch"
                code, out = _run_patch()
        else:
            code, out = _run_patch()

        # No additional fallback beyond git/patch

        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({
            "exit_code": code,
            "output": out,
            "strategy_used": strategy_used,
            "attempts": attempts,
            "patch_file": f"/workspace/{rel_patch_path}",
            "hint": "If exit_code is 0, follow up with workspace_git_add and workspace_git_commit to persist changes. If it fails, pass strategy='patch' and strip=1 for diffs using 'a/' and 'b/' prefixes."
        }))]
    elif name == "workspace_python_run_module":
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
            return [TextContent(type="text", text=_json.dumps({"task_id": info.get("task_id"), "exit_code": info.get("exit_code"), "hint": "Use workspace_task_status to poll, workspace_task_output to tail logs, and workspace_task_kill to stop the module."}))]
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            import json as _json
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "Module still running; try again with a larger timeout or set background=true.", "hint": "Set background=true to get a task_id, then use workspace_task_status to poll, workspace_task_output to tail logs, and workspace_task_kill to stop when done."}))]
        import json as _json
        logger.info(f"[req={req_id}] tool={name} completed exit_code={code} module={module}")
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": "Use output to summarize the run results concisely."}))]
    elif name == "workspace_python_run_script":
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
            return [TextContent(type="text", text=_json.dumps({"task_id": info.get("task_id"), "exit_code": info.get("exit_code"), "hint": "Use workspace_task_status to poll, workspace_task_output to tail logs, and workspace_task_kill to stop the script."}))]
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            import json as _json
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "Script still running; try again with a larger timeout or set background=true.", "hint": "Set background=true to get a task_id, then use workspace_task_status to poll, workspace_task_output to tail logs, and workspace_task_kill to stop when done."}))]
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
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            import json as _json
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "git push still running; try again with a larger timeout.", "hint": "Increase timeout_seconds for slow networks or large pushes."}))]
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
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            import json as _json
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "git pull still running; try again with a larger timeout.", "hint": "Increase timeout_seconds for slow networks or large updates."}))]
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": "If commit succeeded, summarize the commit message and next steps (push or create PR)."}))]
    elif name == "workspace_git_push":
        repo_path = arguments.get("repo_path")
        remote = arguments.get("remote", "origin")
        branch = arguments.get("branch")
        set_upstream = bool(arguments.get("set_upstream", False))
        if not bool(arguments.get("confirm", False)):
            import json as _json
            msg = {
                "exit_code": 2,
                "message": "Push requires explicit approval.",
                "hint": "Do not run this tool unless the user clearly asked to push. Ask the user to confirm and set confirm=true when calling this tool.",
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
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "gh view still running; try again with a larger timeout.", "hint": "Increase timeout_seconds if the GitHub API is slow."}))]
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
    elif name == "workspace_git_status":
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
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "git status still running; increase timeout_seconds.", "hint": "Large repos may need more time."}))]
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": "Summarize the key changes (modified, added, deleted) and branch info for the user."}))]
    elif name == "workspace_git_diff":
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
        base = f"git diff{' --cached' if staged else ''}{' --name-only' if name_only else ''} -U {u}"
        cmd = (
            "cd /workspace && "
            f"cd -- '{str(repo_path).replace("'", "'\\''")}' && "
            f"{base}{(' ' + files_q) if files_q else ''}"
        )
        import json as _json
        timed_out, code, out = _exec_with_timeout(cmd, arguments=arguments)
        if timed_out:
            record_tool_metric(name, int(__t.time()*1000) - __start_ms)
            return [TextContent(type="text", text=_json.dumps({"exit_code": None, "timeout_seconds": arguments.get("timeout_seconds", 120), "message": "git diff still running; increase timeout_seconds.", "hint": "For large diffs, consider name_only=true to list files first."}))]
        record_tool_metric(name, int(__t.time()*1000) - __start_ms)
        return [TextContent(type="text", text=_json.dumps({"exit_code": code, "output": out, "hint": "If the diff is long, summarize key hunks and call out risky changes; include file list with name_only when helpful."}))]
    
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
        import os as _os, uuid as _uuid
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
    # In review-only mode, do not build or (re)start containers; require it to be running already.
    if _is_review_only_mode():
        try:
            running = container_manager.is_container_running()
        except Exception as e:
            running = False
            logger.warning(f"Container check failed in review-only mode: {e}")
        if not running:
            raise RuntimeError("Review-only mode requires an already running container. Start the full server first.")
    else:
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

    # Start HTTP server for screenshots and future endpoints (skip in review-only mode)
    global _http_thread, _http_server, _public_host, _public_port
    if not _is_review_only_mode():
        from pathlib import Path
        bind_ip, port, public_host = get_server_config()
        http_app = create_http_app(Path(container_manager.workspace_dir))
        server_obj, thread = start_http_server(http_app, bind_ip, port)
        _http_thread = thread
        _http_server = server_obj
        _public_host = public_host
        _public_port = port
    else:
        _http_thread = None
        _http_server = None
        _public_host = None
        _public_port = None

    # Write a readiness file for review clients to detect safe startup
    try:
        import json as _json, datetime as _dt
        from pathlib import Path as _Path
        state = {
            "up": True,
            "container_name": getattr(container_manager, "container_name", None),
            "container_id": container_manager.get_container_id(),
            "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
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
    """Clean up server resources."""
    global container_manager

    # Stop HTTP server if running to free the port
    global _http_server
    try:
        if _http_server is not None:
            stop_http_server(_http_server)
    finally:
        _http_server = None

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
