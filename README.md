# effective-potato!

This project provides an MCP (Model Context Protocol) server that hosts a sandboxed Ubuntu 24.04 Docker container for secure command execution.

## Features

- **Sandboxed Environment**: Ubuntu 24.04 container with development tools
- **Script-Based Execution**: Commands are executed via temporary bash scripts for reliability
- **Workspace Persistence**: Mounted workspace directory for file exchange
- **Custom Environment**: Optional environment variables loaded from local/.env

- **Typed Tool Schemas**: Many tools validate inputs with Pydantic and expose JSON Schema via list_tools
- **Background Tasks**: Run long processes in the background and manage them with task tools
- **Git & Patch Utilities**: Apply unified diffs and review git status/diffs safely


## Olama Custom build instructions with rocm6.4.4

- cmake --fresh --preset "ROCm 6" -DOLLAMA_RUNNER_DIR=rocm --install-prefix /opt/ollama -DCMAKE_PREFIX_PATH=/opt/rocm-6.4.4 -DROCM_PATH=/opt/rocm-6.4.4
- cmake --build --preset "ROCm 6" --parallel $(nproc)
- sudo mkdir -p /opt/ollama && sudo chown -R $USER:$USER /opt/ollama
- cmake --install build --component HIP --strip -v
- CGO_ENABLED=1 go build -trimpath -buildmode=pie -o /opt/ollama/bin/ollama .


## Prevent GPU hangs around 15 second mark on large models (gpt-oss:120b) 

- echo 'options amdgpu queue_preemption_timeout_ms=60000 lockup_timeout=60' | sudo tee /etc/modprobe.d/amdgpu-timeouts.conf
- sudo update-initramfs -u
- sudo reboot
- # verify
- cat /sys/module/amdgpu/parameters/queue_preemption_timeout_ms
- cat /sys/module/amdgpu/parameters/lockup_timeout


## Included Packages

The Docker container includes:
- build-essential
- golang-1.23
- rustup (Rust toolchain)
- xorg-dev, xserver-xorg-core
- python3, python3-pip, python3-venv
- gh (GitHub CLI)

## Installation

1. Clone the repository:
```bash
git clone https://github.com/gavinbarnard/effective-potato.git
cd effective-potato
```

2. Create a virtual environment and install dependencies:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -e '.[dev]'
```

3. (Optional) Create a custom environment file:
```bash
cp local/sample.env local/.env
# Edit local/.env with your environment variables
```

## Usage

### Running the MCP Server

To start the MCP server:

```bash
source venv/bin/activate
effective-potato
```

The server will:
1. Build the Docker image (if not already built)
2. Start the container
3. Listen for MCP tool requests via stdio

### MCP Configuration

To use effective-potato with an MCP client (like Claude Desktop), add the following to your MCP settings:

```json
{
  "mcpServers": {
    "effective-potato": {
      "command": "/path/to/effective-potato/venv/bin/effective-potato",
      "args": [],
      "cwd": "/path/to/effective-potato"
    }
  }
}
```

Replace `/path/to/effective-potato` with the actual path to your installation.

An example configuration file is provided in `mcp-config.json`.

### Available Tools

### Typed tool inputs (schemas)

The following tools validate their inputs with Pydantic models and expose JSON Schemas via `list_tools`:

- workspace_screenshot
  - Inputs: { filename?: string, delay_seconds?: integer >= 0 }
  - Behavior: captures a fullscreen PNG into `/workspace/.agent/screenshots/<name>.png`. When the HTTP server is active, a `screenshot_url` is included for inline rendering.

- workspace_launch_and_screenshot
  - Inputs: {
      launch_command: string (required),
      delay_seconds?: integer >= 0 (default: 2),
      filename?: string,
      working_dir?: string (workspace-relative),
      env?: { [key: string]: string }
    }
  - Behavior: launches the app, waits, and captures a fullscreen PNG.

- workspace_python_run_module
  - Inputs: { venv_path: string (workspace-relative), module: string, args?: string[], background?: boolean }
  - Behavior: runs `python -m <module>` using `<venv_path>/bin/python` without activating the venv. If `background=true`, returns a task_id.

- workspace_python_run_script
  - Inputs: { venv_path: string (workspace-relative), script_path: string (workspace-relative), args?: string[], background?: boolean }
  - Behavior: runs `<venv_path>/bin/python '<script_path>'` without activating the venv. If `background=true`, returns a task_id.
- workspace_apply_patch
  - Inputs: { base_dir?: string, diff: string (Git-style a/b unified diff), strategy?: 'git'|'patch' (default: 'git'), strip?: integer (must be 1), reject?: boolean }
  - Behavior: writes the diff into the workspace and runs `git apply` (default) or `patch -p1`. Only Git-style diffs with headers `--- a/...` and `+++ b/...` are accepted. Returns attempts and the strategy used.
  - Minimal example (valid a/b hunk):
    ```diff
    --- a/src/file.txt
    +++ b/src/file.txt
    @@ -1,1 +1,1 @@
    -old
    +new
    ```

- workspace_git_status
  - Inputs: { repo_path: string, porcelain?: boolean (default: true) }
  - Behavior: runs `git status` (porcelain mode for machine-friendly output by default).

- workspace_git_diff
  - Inputs: { repo_path: string, staged?: boolean, name_only?: boolean, unified?: integer, paths?: string[] }
  - Behavior: runs `git diff` with options; set staged=true for `--cached`, name_only to list files.

- workspace_git_push (approval-gated)
  - Inputs: { repo_path: string, remote?: string, branch?: string, set_upstream?: boolean, confirm?: boolean }
  - Behavior: requires `confirm=true` and is marked with an x-needs-approval schema field. Returns an instructional error if confirm is not true.

- workspace_task_start
  - Inputs: { command: string, env?: { [k: string]: string } }
  - Behavior: starts a background task and returns a task_id.

- workspace_task_status
  - Inputs: { task_id: string }
  - Behavior: polls a background task; returns {running, exit_code} and details.

- workspace_task_output
  - Inputs: { task_id: string, tail?: number }
  - Behavior: reads the log file of a background task; set tail to return only last N lines.

- workspace_task_list
  - Inputs: { include_status?: boolean }
  - Behavior: lists known task IDs; can include per-task status.

- workspace_task_kill
  - Inputs: { task_id: string, signal?: string (default: TERM) }
  - Behavior: sends a signal to terminate a background task.


- workspace_tar_create
  - Inputs: {
      base_dir?: string (default: '.' workspace-relative),
      items: string[] (files/dirs relative to base_dir),
      archive_name?: string (default: `archive_<timestamp>.tar.gz`)
    }
  - Behavior: creates `base_dir/<archive_name>` as a `.tar.gz` from listed items.

- workspace_file_digest
  - Inputs: { path: string (workspace-relative or /workspace/...), algorithm?: 'sha256' | 'md5' (default: 'sha256') }
  - Behavior: computes the file digest using standard utilities.

Additionally:
- workspace_find
  - Inputs: {
      path?: string (workspace-relative, default: '.'),
      name?: string (glob passed to find -name),
      type?: 'any'|'a'|'file'|'f'|'dir'|'d' (default: 'any')
    }
  - Behavior: prunes .git, *venv*, *_env* directories; applies optional name and type filters.
  - Aliases: 'a' => any, 'f' => file (-type f), 'd' => dir (-type d).

For an MCP client, you can inspect each tool’s JSON Schema from the `list_tools` response to validate arguments before calling.

#### execute_command (workspace_execute_command)

Execute a bash command in the sandboxed container.

**Parameters:**
- `command` (string, required): The bash command to execute
- `timeout_seconds` (integer, optional, default 120): How long to wait before returning; process may continue running
- `background` (boolean, optional): If true, runs in the background and returns a `task_id`
- `env` (object, optional): Extra environment variables for this command

**Returns:**
- If foreground: exit code and output
- If background: `task_id` and a hint to use task tools

**Example:**
```json
{
  "name": "workspace_execute_command",
  "arguments": { "command": "ls -la /" }
}

{
  "name": "workspace_execute_command",
  "arguments": { "command": "uvicorn app:app", "background": true }
}
```

Follow-ups for background tasks:
- `workspace_task_status` to poll for completion
- `workspace_task_output` with `tail` to read logs
- `workspace_task_kill` to stop the process

#### list_repositories / clone_repository

List GitHub repositories for a user or the authenticated user. This tool is only available when `GITHUB_PERSONAL_ACCESS_TOKEN` is set in `local/.env`.

**Parameters:**
- `owner` (string, optional): The username or organization to list repos for. If not provided, lists repos for the authenticated user.
- `limit` (integer, optional): Maximum number of repositories to list (default: 30)

**Returns:**
- Exit code and list of repositories

**Example:**
```json
{
  "name": "list_repositories",
  "arguments": {
    "owner": "octocat",
    "limit": 10
  }
}
```

Clone a GitHub repository into the workspace directory. This tool is only available when `GITHUB_PERSONAL_ACCESS_TOKEN` is set in `local/.env`.
### Git workflow helpers

Beyond add/commit/pull/push, the server exposes tools to review and safely apply changes:

- Review changes:
  - `workspace_git_status` — machine-friendly status (porcelain)
  - `workspace_git_diff` — pending or staged changes; use `name_only=true` for a quick file list

- Apply diffs:
  - `workspace_apply_patch` — try `git apply` first; if it fails, it falls back to `patch -pN`

- Safety on push:
  - `workspace_git_push` is approval-gated; callers must set `confirm=true` and should obtain explicit user consent

### Background tasks at a glance

Any long-running command can be backgrounded via `workspace_execute_command` (background=true) or the Python runners. Manage them with:

- `workspace_task_list` — discover known tasks
- `workspace_task_status` — poll until `running=false`
- `workspace_task_output` — tail logs with `tail: 200`
- `workspace_task_kill` — terminate with `signal: "TERM"` (or `"KILL"` if needed)

## Development

### Running Tests

Use the provided harness to set up the environment and run pytest:

```bash
./run-tests.sh                # unit tests only (default)
RUN_INTEGRATION_TESTS=1 ./run-tests.sh   # include integration tests
```

You can pass any extra pytest args through the harness, for example:

```bash
./run-tests.sh -q tests/test_container.py::test_validate_and_load_env_file_valid
```

### Project Structure

```
effective-potato/
├── src/
│   └── effective_potato/
│       ├── __init__.py
│       ├── server.py          # MCP server implementation
│       └── container.py       # Docker container management
├── tests/                     # Test suite
├── workspace/                 # Mounted directory for container
│   └── .agent/tmp_scripts/   # Temporary execution scripts
├── local/
│   ├── sample.env            # Example environment file
│   └── .env                  # Your custom environment (gitignored)
├── Dockerfile.base           # Base image (system packages)
├── Dockerfile                # Runner image (FROM base)
└── setup.py                  # Package configuration
```

## How It Works

### Command Execution Pattern

Instead of passing commands directly via `docker exec` with arguments, effective-potato:

1. Creates a bash script in `workspace/.agent/tmp_scripts/task_$taskid$.sh`
2. Writes the command to the script
3. Makes it executable
4. Executes the script in the container via `docker exec`

This approach avoids issues with argument escaping and provides more reliable execution.

### Example

For the command `ls -ltrah /`, the system creates:

```bash
#!/bin/bash

ls -ltrah /
```

And executes it as:
```bash
docker exec $containerid$ /workspace/.agent/tmp_scripts/task_$taskid$.sh
```

### Observability: Metrics

When the server is running, a lightweight metrics endpoint is exposed:

```
GET http://<host>:<port>/metrics
```

It returns text (Prometheus exposition style) with:
- effective_potato_up: process up flag (1/0)
- effective_potato_requests_total: total tool requests processed
- effective_potato_tool_calls_total{tool="<name>"}: per-tool call counts
- effective_potato_tool_duration_ms_sum{tool="<name>"}: cumulative per-tool elapsed time (ms)

You can scrape this endpoint or just curl it for quick inspection during development.

## Environment Configuration

The `local/.env` file is loaded and validated at startup. Environment variables defined in this file are automatically exported at the beginning of each command execution script.

**Important:** The `.env` file must contain only environment variable assignments (e.g., `VAR=value`) and comments. Any other content will cause a validation error.

If no `local/.env` file is present, a warning is printed on startup. See `local/sample.env` for the format.

### GitHub CLI Integration

To enable GitHub CLI tools (`list_repositories` and `clone_repository`), add your GitHub Personal Access Token to the `local/.env` file:

```bash
GITHUB_PERSONAL_ACCESS_TOKEN=your_token_here
```

The token will be used to authenticate the GitHub CLI when the container starts. Once authenticated, you can use the GitHub tools to list and clone repositories.

### How it works:

When you execute a command, the environment variables from `local/.env` are automatically prefixed into the execution script:

```bash
#!/bin/bash

export GITHUB_PERSONAL_ACCESS_TOKEN='5678'
export HF_TOKEN='1234'

# Your command here
ls -la /
```

This ensures that all environment variables are available to every command executed in the container.

## License

[Add your license here]

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.


## OpenWeb Model Export

To export the custom workspace model (e.g., "effective-potato") from your OpenWeb server, set these env vars in your shell (secrets are not written to disk):

- DEV_OPENWEBAPI_URL (e.g., https://openweb.example.com)
- DEV_OPENWEBAPI_KEY (secret)
- DEV_OPENWEBAPI_SCHEMA (optional)

Then run the helper script:

```bash
source venv/bin/activate
export DEV_OPENWEBAPI_URL=...
export DEV_OPENWEBAPI_KEY=...
MODEL_NAME=effective-potato OUTPUT_DIR=./openweb_exports/effective-potato ./scripts/export_openweb_model.sh
```

The resulting artifact will be written under `openweb_exports/<model>/<timestamp>.<ext>` so it can be checked into this repository and redeployed on other OpenWeb servers.

Note: These shell helpers are not exposed as MCP tools to avoid easy tampering; run them manually as needed.


## OpenWeb One-shot Redeploy

To redeploy a model end-to-end (export → import → register → publish) to another OpenWeb server:

- DEV_OPENWEB_URL or DEV_OPENWEBAPI_URL
- DEV_OPENWEB_KEY or DEV_OPENWEBAPI_KEY
- MODEL_NAME: source model to export (unless MODEL_FILE provided)
- NEW_MODEL_NAME: target model name on destination
- MODEL_FILE: optional pre-existing export JSON (skip export stage)
- DELETE_EXISTING=1: optionally replace existing target
- SET_DEFAULT=1: optionally set as default in workspace
- BASE_MODEL_ID, DESCRIPTION, ACTIVATE: optional overrides for UI registration

Example:

```bash
source venv/bin/activate
export DEV_OPENWEB_URL=https://openweb.example.com
export DEV_OPENWEB_KEY=... # secret
MODEL_NAME=effective-potato NEW_MODEL_NAME=silly-pertato SET_DEFAULT=1 \
  ./scripts/redeploy_openweb_model.sh
```

Notes:
- Secrets are injected at runtime and never written to disk.
- The script is idempotent: if NEW_MODEL_NAME already exists and DELETE_EXISTING is not set, it will warn and keep the existing model.
- UI visibility requires register/update to succeed; the script verifies presence before publishing into the workspace config.


