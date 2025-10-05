# effective-potato!

This project provides an MCP (Model Context Protocol) server that hosts a sandboxed Ubuntu 24.04 Docker container for secure command execution.

## Features

- **Sandboxed Environment**: Ubuntu 24.04 container with development tools
- **Script-Based Execution**: Commands are executed via temporary bash scripts for reliability
- **Workspace Persistence**: Mounted workspace directory for file exchange
- **Custom Environment**: Optional environment variables loaded from local/.env

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
  - Inputs: { venv_path: string (workspace-relative), module: string, args?: string[] }
  - Behavior: runs `python -m <module>` using `<venv_path>/bin/python` without activating the venv.

- workspace_python_run_script
  - Inputs: { venv_path: string (workspace-relative), script_path: string (workspace-relative), args?: string[] }
  - Behavior: runs `<venv_path>/bin/python '<script_path>'` without activating the venv.

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

For an MCP client, you can inspect each tool’s JSON Schema from the `list_tools` response to validate arguments before calling.

#### execute_command

Execute a bash command in the sandboxed container.

**Parameters:**
- `command` (string, required): The bash command to execute

**Returns:**
- Exit code and output of the command

**Example:**
```json
{
  "name": "execute_command",
  "arguments": {
    "command": "ls -la /"
  }
}
```

#### list_repositories

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

#### clone_repository

Clone a GitHub repository into the workspace directory. This tool is only available when `GITHUB_PERSONAL_ACCESS_TOKEN` is set in `local/.env`.

**Parameters:**
- `owner` (string, required): The repository owner (username or organization)
- `repo` (string, required): The repository name

**Returns:**
- Exit code and output of the clone operation

**Example:**
```json
{
  "name": "clone_repository",
  "arguments": {
    "owner": "octocat",
    "repo": "Hello-World"
  }
}
```

**Note:** The repository will be cloned into `/workspace/repo-name` in the container.

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


