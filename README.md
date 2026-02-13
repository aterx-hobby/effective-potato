This is currently being refactored as an MCP server for Pochi, instead of being an MCP server that provides coding tools.
It will be updated to only provide the sandbox'ed Ubuntu 24.04 environment, and remove some of the tools available that are already built in to Pochi.

If you have no heard of pochi please check it out: https://github.com/TabbyML/pochi/

# effective-potato!

This project provides an MCP (Model Context Protocol) server that hosts a sandboxed Ubuntu 24.04 Docker container for secure command execution.

## Features

- **Sandboxed Environment**: Ubuntu 24.04 container with development tools
- **Script-Based Execution**: Commands are executed via temporary bash scripts for reliability
- **Workspace Persistence**: Mounted workspace directory for file exchange
- **Custom Environment**: Optional environment variables loaded from local/.env

- **Typed Tool Schemas**: Many tools validate inputs with Pydantic and expose JSON Schema via list_tools
- **Background Tasks**: Run long processes in the background and manage them with task tools
- **Git Utilities**: Execute git commands and review git status/diffs


## Ollama Custom build instructions with rocm6.4.4  (STABLE)

- cmake --fresh --preset "ROCm 6" -DOLLAMA_RUNNER_DIR=rocm --install-prefix /opt/ollama -DCMAKE_PREFIX_PATH=/opt/rocm-6.4.4 -DROCM_PATH=/opt/rocm-6.4.4
- cmake --build --preset "ROCm 6" --parallel $(nproc)
- sudo mkdir -p /opt/ollama && sudo chown -R $USER:$USER /opt/ollama
- cmake --install build --component HIP --strip -v
- CGO_ENABLED=1 go build -trimpath -buildmode=pie -o /opt/ollama/bin/ollama .
- cmake --install build --component CPU --strip -v #this will throw an error but it will install the rocm ggml lib I don't know why this fixes things.

## Ollama custom build instructions with rocm 7.0.2 (VERY BROKEN - 1 poor response slow token rate, follow ups crash).

-    cmake --fresh --preset "ROCm 6" -DOLLAMA_RUNNER_DIR=rocm --install-prefix /opt/ollama -DCMAKE_PREFIX_PATH=/opt/rocm-7.0.2 -DROCM_PATH=/opt/rocm-7.0.2
-    cmake --build --preset "ROCm 6" --parallel $(nproc)
-    sudo mkdir -p /opt/ollama && sudo chown -R $USER:$USER /opt/ollama
-    cmake --install build --component HIP --strip -v
-    CGO_ENABLED=1 go build -trimpath -buildmode=pie -o /opt/ollama/bin/ollama .
-    cmake --install build --component CPU --strip -v   #this will throw an error but it will install the rocm ggml lib

# Ollama other install stuff

- sudo mkdir -p /usr/share/ollama/.ollama/models
- sudo adduser --system ollama
- sudo addgroup --system ollama
- sudo usermod ollama -G ollama
- sudo chown ollama:ollama /usr/share/ollama/.ollama
- sudo chown ollama:ollama /usr/share/ollama/.ollama/models

# Ollama systemd service file

    [Unit] 
    Description=Ollama Service After=network-online.target
    
    [Service]
    ExecStart=/opt/ollama/bin/ollama serve
    User=ollama
    Group=ollama
    Restart=always
    RestartSec=3
    Environment="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin"
    Environment="OLLAMA_HOST=0.0.0.0"
    Environment="OLLAMA_MODELS=/usr/share/ollama/.ollama/models"
    Environment="OLLAMA_LIBRARY_PATH=/opt/ollama/lib/ollama:/opt/ollama/lib/ollama/rocm"
    
    [Install]
    WantedBy=default.target



## Prevent GPU hangs around 15 second mark on large models (gpt-oss:120b) 

- echo 'options amdgpu queue_preemption_timeout_ms=60000 lockup_timeout=60' | sudo tee /etc/modprobe.d/amdgpu-timeouts.conf
- sudo update-initramfs -u
- sudo reboot
- # verify
- cat /sys/module/amdgpu/parameters/queue_preemption_timeout_ms
- cat /sys/module/amdgpu/parameters/lockup_timeout

- in /etc/default/grub set GRUB_CMDLINE_LINUX="amdgpu.dc=0"



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
3. Host the MCP server over Streamable HTTP (default: `http://127.0.0.1:8000/mcp`)

Alternatively, you can launch via the helper script:

```bash
./mcp.sh                # builds venv+package if needed
```

### MCP Configuration (Streamable HTTP)

Start the server (in a terminal) and point your MCP client at its Streamable HTTP URL.

To use effective-potato with an MCP client, add something like the following to your MCP settings:

```json
{
  "mcpServers": {
    "effective-potato": {
      "type": "streamable_http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

Replace `/path/to/effective-potato` with the actual path to your installation.

An example configuration file is provided in `mcp-config.json`.

### Available Tools

### Tool schemas

All published tools expose JSON Schemas via `list_tools`.

The following tools additionally validate/coerce inputs with Pydantic models:

- potato_screenshot
  - Inputs: { filename?: string, delay_seconds?: integer >= 0 }
  - Behavior: captures a fullscreen PNG into `/workspace/.agent/screenshots/<name>.png`. The tool returns `screenshot_path`; clients should surface or fetch the file as appropriate for their UI.

- potato_launch_and_screenshot
  - Inputs: {
      launch_command: string (required),
      delay_seconds?: integer >= 0 (default: 2),
      filename?: string,
      working_dir?: string (workspace-relative),
      env?: { [key: string]: string }
    }
  - Behavior: launches the app, waits, and captures a fullscreen PNG.

- potato_interact_and_record
  - Inputs: { inputs: Array<{ key_sequence?: string, delay?: integer >= 0, type?: 'once'|'sleep'|'repeat' }>, launch_command?: string, venv?: string, duration_seconds?: integer >= 1, frame_interval_ms?: integer >= 10, output_basename?: string, working_dir?: string, env?: { [key: string]: string }, post_launch_delay_seconds?: integer >= 0 }
  - Behavior: optionally launches an app, sends key sequences, and records a WebM into `/workspace/.agent/recordings/`. Returns `video_path` plus window/probe info.

- potato_python_run_module
  - Inputs: { venv_path: string (workspace-relative), module: string, args?: string[], background?: boolean }
  - Behavior: runs `python -m <module>` using `<venv_path>/bin/python` without activating the venv. If `background=true`, returns a task_id.

- potato_python_run_script
  - Inputs: { venv_path: string (workspace-relative), script_path: string (workspace-relative), args?: string[], background?: boolean }
  - Behavior: runs `<venv_path>/bin/python '<script_path>'` without activating the venv. If `background=true`, returns a task_id.

- potato_python_check_syntax
  - Inputs: { venv_path: string (workspace-relative), source_path: string (workspace-relative) }
  - Behavior: runs `python -m py_compile <source_path>` using `<venv_path>/bin/python`.

- potato_pytest_run
  - Inputs: { venv_path: string (workspace-relative), args?: string[] }
  - Behavior: runs pytest using `<venv_path>/bin/python -m pytest`.

Other published tools (schemas are defined inline and exposed via `list_tools`):

- potato_execute_command
  - Inputs: { command: string, timeout_seconds?: integer, env?: { [k: string]: string }, background?: boolean }
  - Behavior: runs an arbitrary bash command in the container. If `background=true`, returns a task id.

- potato_task_start / potato_task_status / potato_task_output / potato_task_list / potato_task_kill
  - Behavior: manage background processes started in the container.

- potato_list_repositories
  - Behavior: list git repos tracked in the workspace.

- potato_select_venv
  - Inputs: { paths: string[] }
  - Behavior: select the best venv candidate and return an `activate` command.

- potato_find_venvs
  - Inputs: { path?: string }
  - Behavior: searches under `path` (workspace-relative) for venv roots (e.g. `.venv`, `venv`, `*_env*`, and `bin/activate`). Returns `venv_roots` and `activations` (e.g. `source <root>/bin/activate`).

Git tools:

- potato_git_add / potato_git_commit / potato_git_pull / potato_git_push (approval-gated)
- potato_git_status / potato_git_diff
- potato_git_checkout / potato_git_branch_create / potato_git_branch_delete / potato_git_merge

GitHub tools (only if `gh` is available in the container):

- github_get_repository
- github_clone_repository

Note: effective-potato intentionally does not publish general workspace file search/review/edit tools (glob/list/read/search/write/applyDiff).
Coding agents typically already provide those primitives; this server focuses on container execution, git operations, and GUI automation. `potato_find_venvs` is a special-case helper because other tools/workflows depend on quickly discovering venv roots.


For an MCP client, you can inspect each tool’s JSON Schema from the `list_tools` response to validate arguments before calling.

#### execute_command (potato_execute_command)

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
  "name": "potato_execute_command",
  "arguments": { "command": "ls -la /" }
}

{
  "name": "potato_execute_command",
  "arguments": { "command": "uvicorn app:app", "background": true }
}
```

Follow-ups for background tasks:
- `potato_task_status` to poll for completion
- `potato_task_output` with `tail` to read logs
- `potato_task_kill` to stop the process

#### GitHub tools (github_get_repository / github_clone_repository)

Get details for a GitHub repository. These tools are only available when `GITHUB_PERSONAL_ACCESS_TOKEN` is set in `local/.env`.

**Parameters:**
- `owner` (string, required): The username or organization
- `repo` (string, required): The repository name

**Returns:**
- Exit code and repository details

**Example:**
```json
{
  "name": "github_get_repository",
  "arguments": {
    "owner": "octocat",
    "repo": "Hello-World"
  }
}
```

Clone a GitHub repository into the workspace directory with `github_clone_repository`.

### Git workflow helpers

Beyond add/commit/pull/push, the server exposes tools to review changes:

- Review changes:
  - `potato_git_status` — machine-friendly status (porcelain)
  - `potato_git_diff` — pending or staged changes; use `name_only=true` for a quick file list

- Safety on push:
  - `potato_git_push` is approval-gated; callers must set `confirm=true` and should obtain explicit user consent

### Background tasks at a glance

Any long-running command can be backgrounded via `potato_execute_command` (background=true) or the Python runners. Manage them with:

- `potato_task_list` — discover known tasks
- `potato_task_status` — poll until `running=false`
- `potato_task_output` — tail logs with `tail: 200`
- `potato_task_kill` — terminate with `signal: "TERM"` (or `"KILL"` if needed)

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

## Environment Configuration

The `local/.env` file is loaded and validated at startup. Environment variables defined in this file are automatically exported at the beginning of each command execution script.

**Important:** The `.env` file must contain only environment variable assignments (e.g., `VAR=value`) and comments. Any other content will cause a validation error.

If no `local/.env` file is present, a warning is printed on startup. See `local/sample.env` for the format.

### GitHub CLI Integration

### Workspace location override

By default, the host workspace directory is `./workspace` (relative to the repo). To override it, set:

```bash
export POTATO_WORKSPACE_DIR=/absolute/path/to/your/workspace
```

The server mounts this path at `/workspace` inside the container and writes a readiness file at `$POTATO_WORKSPACE_DIR/.agent/potato_ready.json`.

To enable GitHub CLI tools (`github_get_repository` and `github_clone_repository`), add your GitHub Personal Access Token to the `local/.env` file:

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


