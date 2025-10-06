import json
import re
import pytest


class FakeContainerManager:
    def __init__(self):
        self.last_command = None
        self.last_task_id = None

    def execute_command(self, command: str, task_id: str):
        self.last_command = command
        self.last_task_id = task_id
        return 0, "OK"


@pytest.mark.asyncio
async def test_interact_and_record_builds_script(monkeypatch):
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
            "inputs": [
                {"keys": "ctrl+n", "delay_ms": 50},
                {"keys": "Hello", "delay_ms": 120},
            ],
            "duration_seconds": 3,
            "frame_interval_ms": 500,
            "output_basename": "rec_test",
        }

        res = await server.call_tool("workspace_interact_and_record", args)
        assert isinstance(res, list) and res, "Expected a response"
        data = json.loads(res[0].text)
        video_path = data["video"]
        # Expect UUID-suffixed webm filename
        assert video_path.startswith("/workspace/.agent/screenshots/rec_test_") and video_path.endswith(".webm")
        assert re.search(r"/workspace/.agent/screenshots/rec_test_[0-9a-f]{32}\.webm$", video_path)

        cmd = fake.last_command
        assert cmd is not None
        # Should detect active window and focus it (robust non-fatal form)
        assert "xdotool getactivewindow" in cmd
        assert "xdotool windowactivate" in cmd and "xdotool windowfocus" in cmd
        # Should record video using ffmpeg x11grab for the specified duration
        assert "ffmpeg -y -loglevel error -f x11grab" in cmd
    finally:
        server.container_manager = orig_cm
        server._public_host = orig_host
        server._public_port = orig_port


@pytest.mark.asyncio
async def test_interact_and_record_optional_launch_with_venv(monkeypatch):
    from effective_potato import server

    class FakeContainerManager:
        def __init__(self):
            self.last_command = None
        def execute_command(self, command: str, task_id: str):
            self.last_command = command
            return 0, "OK"

    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        args = {
            "launch_command": "python -m app",
            "venv": "source .venv/bin/activate",
            "inputs": [{"keys": "Return"}],
            "duration_seconds": 1,
            "frame_interval_ms": 200,
            "output_basename": "short",
        }
        res = await server.call_tool("workspace_interact_and_record", args)
        assert isinstance(res, list) and res
        data = json.loads(res[0].text)
        video_path = data["video"]
        cmd = fake.last_command
        assert cmd is not None
        assert "(source .venv/bin/activate && python -m app) >/tmp/launch_interact.log 2>&1 &" in cmd
        assert "xdotool getactivewindow" in cmd
    finally:
        server.container_manager = orig_cm
