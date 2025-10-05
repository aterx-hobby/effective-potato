import asyncio
import pytest


class FakeContainerManager:
    def __init__(self):
        self.last_command = None
        self.last_task_id = None

    def execute_command(self, command: str, task_id: str):
        # Capture but simulate success
        self.last_command = command
        self.last_task_id = task_id
        return 0, "OK"


@pytest.mark.asyncio
async def test_launch_and_screenshot_self_contained_command(monkeypatch):
    # Import server module and patch globals
    from effective_potato import server

    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    orig_host = getattr(server, "_public_host", None)
    orig_port = getattr(server, "_public_port", None)
    try:
        server.container_manager = fake
        server._public_host = "localhost"
        server._public_port = 9090

        args = {
            "launch_command": "echo hello",
            "delay_seconds": 3,
            "filename": "test.png",
            "working_dir": "proj/app",
            "env": {
                "FOO": "bar",
                "A": "1",
            },
        }

        res = await server.call_tool("workspace_launch_and_screenshot", args)
        assert isinstance(res, list) and res, "Expected a non-empty TextContent list"
        text = res[0].text

        # Validate response lines include saved path and URL
        assert "/workspace/.agent/screenshots/test.png" in text
        assert "http://localhost:9090/screenshots/test.png" in text

        # Validate constructed command is self-contained and includes env/dir handling
        cmd = fake.last_command
        assert cmd is not None
        assert "mkdir -p /workspace/.agent/screenshots && " in cmd
        assert "cd /workspace && cd -- 'proj/app' && " in cmd
        # Exports should include provided env vars
        assert "export FOO='bar'; export A='1'; " in cmd
        # Launch, delay, DISPLAY and capture
        assert "(echo hello) >/tmp/launch.log 2>&1 & " in cmd
        assert "sleep 3; " in cmd
        assert "export DISPLAY=:0; " in cmd
        assert "xdotool key XF86Refresh" in cmd
        assert "xfce4-screenshooter -f -s '/workspace/.agent/screenshots/test.png'" in cmd
    finally:
        # Restore globals
        server.container_manager = orig_cm
        server._public_host = orig_host
        server._public_port = orig_port


@pytest.mark.asyncio
async def test_launch_and_screenshot_requires_launch_command():
    from effective_potato import server

    # Ensure a fake manager to avoid container access in error path
    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    server.container_manager = fake
    try:
        with pytest.raises(ValueError):
            await server.call_tool("workspace_launch_and_screenshot", {"delay_seconds": 1})
    finally:
        server.container_manager = orig_cm


@pytest.mark.asyncio
async def test_launch_and_screenshot_missing_command_raises(monkeypatch):
    from effective_potato import server

    class FakeContainerManager:
        def execute_command(self, command: str, task_id: str, extra_env=None):
            return 0, "OK"

    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        with pytest.raises(Exception):
            await server.call_tool("workspace_launch_and_screenshot", {"delay_seconds": 1})
    finally:
        server.container_manager = orig_cm
