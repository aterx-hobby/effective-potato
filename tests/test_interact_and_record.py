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
                {"key_sequence": "ctrl+n", "delay": 50, "type": "once"},
                {"key_sequence": "H e l l o", "delay": 120, "type": "once"},
            ],
            "duration_seconds": 3,
            "frame_interval_ms": 500,
            "output_basename": "rec_test",
        }

        res = await server.call_tool("workspace_interact_and_record", args)
        assert isinstance(res, list) and res, "Expected a response"
        data = json.loads(res[0].text)
        video_url = data["video_url"]
        # Expect URL or fallback path containing the UUID-suffixed webm filename
        assert video_url.endswith(".webm")
        assert re.search(r"rec_test_[0-9a-f]{32}\.webm$", video_url)

        cmd = fake.last_command
        assert cmd is not None
        # Should detect active window and focus it (robust non-fatal form)
        assert "xdotool getactivewindow" in cmd
        assert "xdotool windowactivate" in cmd and "xdotool windowfocus" in cmd
        # Should send inputs to the detected window id with --delay timing using key_sequence
        assert "xdotool key --delay 50 --clearmodifiers --window \"$active_id\" 'ctrl+n'" in cmd
        assert "xdotool key --delay 120 --clearmodifiers --window \"$active_id\" 'H' 'e' 'l' 'l' 'o'" in cmd
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
            "inputs": [{"key_sequence": "Return", "delay": 0, "type": "once"}],
            "duration_seconds": 1,
            "frame_interval_ms": 200,
            "output_basename": "short",
        }
        res = await server.call_tool("workspace_interact_and_record", args)
        assert isinstance(res, list) and res
        data = json.loads(res[0].text)
        video_url = data["video_url"]
        cmd = fake.last_command
        assert cmd is not None
        # New behavior: venv is activated in the shell, then command is launched with LAUNCH_PID captured
        assert "source .venv/bin/activate" in cmd
        assert "python -m app >/tmp/launch_interact.log 2>&1 & LAUNCH_PID=$!; echo LAUNCH_PID:$LAUNCH_PID" in cmd
        assert "xdotool getactivewindow" in cmd
    finally:
        server.container_manager = orig_cm


@pytest.mark.asyncio
async def test_interact_and_record_key_sequence_and_sleep(monkeypatch):
    from effective_potato import server

    class FakeContainerManager:
        def __init__(self):
            self.last_command = None
        def execute_command(self, command: str, task_id: str):
            self.last_command = command
            # Simulate xdotool markers so JSON parse still works
            return 0, "WIN_NAME:Test\nWIN_PID:123\nWIN_ID:456\nOUTPUT_VIDEO: /workspace/.agent/screenshots/session_abcdef.webm\n"

    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        args = {
            "inputs": [
                {"delay": 100, "key_sequence": "Insert h e l l o w o r l d", "type": "once"},
                {"delay": 2000, "type": "sleep"},
                {"delay": 50, "key_sequence": "Escape d d", "type": "once"},
            ],
            "duration_seconds": 2,
            "frame_interval_ms": 200,
            "output_basename": "session",
        }
        res = await server.call_tool("workspace_interact_and_record", args)
        assert isinstance(res, list) and res
        cmd = fake.last_command
        assert cmd is not None
        # Should include xdotool key with multiple tokens and the correct --delay
        assert "xdotool key --delay 100 --clearmodifiers --window \"$active_id\" 'Insert' 'h' 'e' 'l' 'l' 'o' 'w' 'o' 'r' 'l' 'd'" in cmd
        # Should include a sleep for 2000ms (2 seconds)
        assert "sleep 2" in cmd or "sleep 2.0" in cmd
        # And a second key sequence with delay 50
        assert "xdotool key --delay 50 --clearmodifiers --window \"$active_id\" 'Escape' 'd' 'd'" in cmd
    finally:
        server.container_manager = orig_cm


@pytest.mark.asyncio
async def test_interact_and_record_repeat_loops_until_end(monkeypatch):
    from effective_potato import server

    class FakeContainerManager:
        def __init__(self):
            self.last_command = None
        def execute_command(self, command: str, task_id: str):
            self.last_command = command
            # Simulate output markers
            return 0, "WIN_NAME:Test\nWIN_PID:123\nWIN_ID:456\nOUTPUT_VIDEO: /workspace/.agent/screenshots/s.webm\n"

    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        args = {
            "inputs": [
                {"delay": 20, "key_sequence": "Up Up Down Down Left Left Right Right", "type": "repeat"}
            ],
            "duration_seconds": 1,
            "frame_interval_ms": 200,
            "output_basename": "s",
        }
        res = await server.call_tool("workspace_interact_and_record", args)
        assert isinstance(res, list) and res
        cmd = fake.last_command
        assert cmd is not None
        # ffmpeg should be started in background with a captured PID and a while loop present
        assert "ffmpeg -y -loglevel error -f x11grab" in cmd and "& FF_PID=$!" in cmd
        assert "while kill -0 \"$FF_PID\"" in cmd
        # The repeated key sequence must appear inside the loop body as xdotool key with tokens and delay 20
        assert "xdotool key --delay 20 --clearmodifiers --window \"$active_id\" 'Up' 'Up' 'Down' 'Down' 'Left' 'Left' 'Right' 'Right'" in cmd
    finally:
        server.container_manager = orig_cm


@pytest.mark.asyncio
async def test_interact_min_delay_applied(monkeypatch):
    from effective_potato import server

    class FakeContainerManager:
        def __init__(self):
            self.last_command = None
        def execute_command(self, command: str, task_id: str):
            self.last_command = command
            return 0, "WIN_NAME:T\nWIN_PID:1\nWIN_ID:2\nOUTPUT_VIDEO: /workspace/.agent/screenshots/min.webm\n"

    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        # once with delay=0 should clamp to 20ms
        args_once = {
            "inputs": [{"key_sequence": "A", "delay": 0, "type": "once"}],
            "duration_seconds": 1,
            "frame_interval_ms": 100,
            "output_basename": "min",
        }
        await server.call_tool("workspace_interact_and_record", args_once)
        cmd = fake.last_command
        assert "xdotool key --delay 20 --clearmodifiers --window \"$active_id\" 'A'" in cmd

        # repeat with delay=0 should also clamp to 20ms
        args_rep = {
            "inputs": [{"key_sequence": "B", "delay": 0, "type": "repeat"}],
            "duration_seconds": 1,
            "frame_interval_ms": 100,
            "output_basename": "min",
        }
        await server.call_tool("workspace_interact_and_record", args_rep)
        cmd = fake.last_command
        assert "xdotool key --delay 20 --clearmodifiers --window \"$active_id\" 'B'" in cmd
    finally:
        server.container_manager = orig_cm
