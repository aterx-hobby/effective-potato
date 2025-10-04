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
pip install -e .
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

### Available Tools

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

## Development

### Running Tests

```bash
source venv/bin/activate
pytest tests/ -v
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
│   └── .tmp_agent_scripts/   # Temporary execution scripts
├── local/
│   ├── sample.env            # Example environment file
│   └── .env                  # Your custom environment (gitignored)
├── Dockerfile                # Container definition
└── setup.py                  # Package configuration
```

## How It Works

### Command Execution Pattern

Instead of passing commands directly via `docker exec` with arguments, effective-potato:

1. Creates a bash script in `workspace/.tmp_agent_scripts/task_$taskid$.sh`
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
docker exec $containerid$ /workspace/.tmp_agent_scripts/task_$taskid$.sh
```

## Environment Configuration

The `local/.env` file is copied to the container's user `.profile` during build. This allows you to set custom environment variables that will be available in all commands.

If no `local/.env` file is present, a warning is printed on startup. See `local/sample.env` for the format.

## License

[Add your license here]

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.


