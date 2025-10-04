# launch_and_screenshot tool

This tool is self-contained: it launches the target X11 application and captures a fullscreen screenshot in the same call. Do NOT pre-launch the application outside this tool. If the app is started before the tool runs, the capture may fail or hang because window focus and timing are managed within the tool.

## Contract
- You must provide everything needed to launch the process in this call.
- The tool will:
  - Optionally cd into a workspace-relative directory you provide.
  - Optionally export environment variables you provide.
  - Launch your command in the background.
  - Wait `delay_seconds` to allow the UI to render.
  - Take a fullscreen screenshot and save it under `/workspace/.agent/screenshots/`.
  - If the HTTP server is enabled, it returns a public URL for the image.
- The DISPLAY is handled internally (`:0`). You do not need to set it.

## Input parameters
- `launch_command` (string, required): Command to start the X11 app (e.g., `xclock` or `/usr/bin/gedit`).
- `delay_seconds` (integer, optional, default 2): Seconds to wait after launch before capture.
- `filename` (string, optional): Custom PNG filename. When omitted, a timestamped name is used.
- `working_dir` (string, optional): Workspace-relative path to cd into before launch (relative to `/workspace`).
- `env` (object, optional): Map of environment variables to export for the launched process.

## Example usage (pseudo-code)

Using the MCP server's tool call:

```json
{
  "name": "launch_and_screenshot",
  "arguments": {
    "launch_command": "xclock -d",
    "delay_seconds": 3,
    "filename": "demo_clock.png",
    "working_dir": "projects/gui-demo",
    "env": {
      "LC_ALL": "C.UTF-8",
      "MY_APP_MODE": "demo"
    }
  }
}
```

Notes:
- Do NOT start `xclock` (or any app) before running the tool. The tool must own the launch-and-capture lifecycle.
- If you need longer to render, increase `delay_seconds` (e.g., larger app start times).
- The saved path will be `/workspace/.agent/screenshots/demo_clock.png` and, if the HTTP server is running, a URL will be returned in the tool response.
