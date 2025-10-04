# Examples

This directory contains example scripts demonstrating how to use effective-potato.

## basic_usage.py

A simple example showing how to:
- Create a ContainerManager
- Build and start the container
- Execute commands
- Handle outputs
- Clean up resources

To run:
```bash
cd /home/runner/work/effective-potato/effective-potato
source venv/bin/activate
python examples/basic_usage.py
```

Note: The first run will build the Docker image, which may take a few minutes.

## launch_and_screenshot

See `examples/launch_and_screenshot.md` for a detailed guide. Important: the tool must launch the process itself; do not pre-start the app outside the tool invocation or the capture may fail/hang.
