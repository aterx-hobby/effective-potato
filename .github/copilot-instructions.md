# Copilot Instructions for effective-potato

## Project Overview
effective-potato is an MCP (Model Context Protocol) server that provides callable executions against a sandboxed Ubuntu 24.04 Docker container. The project enables secure, isolated command execution in a containerized environment.

## Architecture
- **MCP Server**: Python-based server implementing MCP protocol
- **Docker Container**: Ubuntu 24.04 image with development tools
- **Workspace**: Mounted directory for persistent storage and file exchange
- **Script Execution**: Commands are wrapped in scripts before execution

## Development Environment

### Python Setup
- Use Python 3 for all development
- Virtual environment location: `./venv` (project root)
- **ALWAYS** use the existing virtual environment in `./venv`.
   - Activate it with: `source venv/bin/activate`
   - Do **not** create a new venv, change interpreters, or attempt to re-provision Python.
   - If activation fails for any reason, stop and ask for guidance instead of creating a new environment.
- Install dependencies using pip within the activated venv only.
   - You have permission to add or update libraries inside this existing venv when needed for development or tests.
  
   Before running any scripts or tests, verify the venv is actually active:
   - `$VIRTUAL_ENV` should be set (non-empty)
   - `which python` should point to `./venv/bin/python`
   - `pip -V` should report a path under `./venv`

### Required Tools
- Docker for container management
- pytest for testing
- Python 3 with pip and venv support

## Coding Standards

### Python Code Quality
- Write effective, clean Python 3 code
- Follow PEP 8 style guidelines
- Use type hints where appropriate
- Write docstrings for functions and classes
- Keep functions focused and modular

### Testing Requirements
- All code must be backed by pytest tests
- Write unit tests for new functionality
- When updating the codebase, re-run the test suite to validate no breakage
- Attempt to repair test case failures when they occur
- Maintain high test coverage

## Project Structure

### Key Directories
- `workspace/`: Mounted directory for container file exchange
- `workspace/.agent/tmp_scripts/`: Temporary scripts for command execution
- `local/`: Local configuration files (gitignored)
- `local/sample.env`: Example environment configuration

### Configuration Files
- `local/.env`: Environment variables (create from sample.env)
  - Appended to container's user `.profile` on startup
  - If missing, a warning is printed
- `.gitignore`: Excludes `local/.env` and Python cache files

## Docker Container Specifications

### Base Image
- Ubuntu 24.04

### Required Packages
- build-essential
- rustup (snap package)
- golang-1.23
- xorg-server-fbdev
- python3, python3-pip, python3-venv

### Container Behavior
- Runs `sleep infinity` to stay alive
- Rebuilds on application startup
- Workspace directory mounted as read/write volume
 - GUI/X stack is ENABLED by default. To build a headless image, pass `--build-arg INSTALL_GUI=0`. At runtime, you can disable GUI startup with `-e POTATO_GUI=0`.

## Command Execution Pattern

### Script-Based Execution
Instead of direct `docker exec` with arguments, commands should be:
1. Written to a bash script in `workspace/.agent/tmp_scripts/`
2. Named as `task_$taskid$.sh`
3. Marked as executable
4. Executed via `docker exec $containerid$ -- /path/to/workspace/.agent/tmp_scripts/task_$taskid$.sh`

### Example
For command `ls -ltrah /`:
```bash
#!/bin/bash
ls -ltrah /
```

## Development Workflow

1. **Before Making Changes**:
   - Activate the existing venv: `source venv/bin/activate`
   - Verify activation: `$VIRTUAL_ENV` is non-empty and `which python` ends with `venv/bin/python`
   - Run existing tests to establish baseline
   - Review relevant code and tests

2. **During Development**:
   - Write code following Python best practices
   - Add/update tests for new functionality
   - Run tests frequently to catch issues early

3. **Before Committing**:
   - Run full test suite: `pytest`
   - Verify all tests pass
   - Check code style and quality
   - Update documentation if needed

## Common Tasks

### Running Tests
```bash
source venv/bin/activate
# Verify venv activation (optional checks)
echo "$VIRTUAL_ENV"  # should be non-empty
which python         # should be ./venv/bin/python
pytest
```

### Activating the Environment
```bash
# Activate the existing venv (do not create a new one)
source venv/bin/activate

# Optional: verify activation
echo "$VIRTUAL_ENV"    # non-empty
which python           # ./venv/bin/python
pip -V                 # .../venv/lib/pythonX.Y/site-packages

# Install dependencies
pip install -r requirements.txt
# Dependency Management

- Install a new library (runtime or dev/testing):
   - Activate the venv, then: `pip install <package>`
   - Update `requirements.txt` accordingly (pin versions when possible)
- Prefer minimal, pinned, widely-used libraries. Avoid changing Python versions or creating new environments.


# Copy environment config
cp local/sample.env local/.env
# Edit local/.env with your values
```

## Important Notes
- Always work within the existing `./venv` virtual environment; never create a new one
- Never commit `local/.env` (it's gitignored)
- The container is rebuilt on each startup
- Use the script-based execution pattern for reliability
- Maintain test coverage when adding features

## Development Mode Policy
- This repository is currently in active development. We do not preserve legacy tool names or APIs during this phase.
- Breaking changes are allowed and expected when they simplify the surface area or align with new conventions.
- Prefer the workspace-prefixed naming for tools. For example:
      - Multi-tool pipeline: DEPRECATED. Do not expose or use `potato_workspace_multi_tool_pipeline` (previously `potato_multi_tool_pipeline`). Invoke individual tools instead.
   - Local workspace operations should start with `potato_workspace_*`.
   - Remote GitHub operations should start with `github_*`.
   - Do not add backward-compatibility aliases during development; update callers/tests instead.

## Next Milestone Goals (update statuses here)

Track progress here by setting Status to one of: not_started, in_progress, completed. Update as tasks move forward.

5) P2 – Typed Tool Schemas and Workspace Utilities
- Goal: Define Pydantic models for tool inputs/outputs to auto-generate JSON Schemas. Add tar/zip/digest helpers and improve GUI robustness (window readiness checks, capture retries).
- Status: not_started

6) P1 – Integration Tests for Mocked Paths
- Goal: For every existing unit test that uses mocked data/clients, add a corresponding fully unmocked integration test that runs against a live container image. Use a unique container name to avoid conflicts on shared hosts, isolate a temporary workspace, and mark with `@pytest.mark.integration`.
- Status: in_progress
- Coverage: Added integration tests for container lifecycle, workspace_find_venvs via the MCP server, and server watchdog auto-restart with diagnostics capture. Tests are gated by POTATO_IT_ENABLE=1 and use unique container names and a temporary workspace to avoid conflicts.
- Next: Add integration coverage for tar/digest utilities, Python runner tools (module/script), and GUI flows (screenshot/launch/record) where environment permits; consider an OpenWeb scripts end-to-end test.
