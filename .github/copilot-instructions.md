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
- Virtual environment location: `$ROOT_PROJECT_DIR/venv`
- Always activate the venv before running or testing code
- Install dependencies using pip within the venv

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
- `workspace/.tmp_agent_scripts/`: Temporary scripts for command execution
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

## Command Execution Pattern

### Script-Based Execution
Instead of direct `docker exec` with arguments, commands should be:
1. Written to a bash script in `workspace/.tmp_agent_scripts/`
2. Named as `task_$taskid$.sh`
3. Marked as executable
4. Executed via `docker exec $containerid$ -- /path/to/workspace/.tmp_agent_scripts/task_$taskid$.sh`

### Example
For command `ls -ltrah /`:
```bash
#!/bin/bash
ls -ltrah /
```

## Development Workflow

1. **Before Making Changes**:
   - Activate venv: `source venv/bin/activate`
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
pytest
```

### Setting Up Environment
```bash
# Create venv if not exists
python3 -m venv venv

# Activate venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt  # when available

# Copy environment config
cp local/sample.env local/.env
# Edit local/.env with your values
```

## Important Notes
- Always work within the virtual environment
- Never commit `local/.env` (it's gitignored)
- The container is rebuilt on each startup
- Use the script-based execution pattern for reliability
- Maintain test coverage when adding features 
