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
            "window_title": "Demo Window",
            "inputs": [
                {"keys": "ctrl+n", "delay_ms": 50},
                {"keys": "Hello", "delay_ms": 120},
            ],
            "duration_seconds": 3,
            "frame_interval_ms": 500,
            "output_basename": "rec_test",
        }

        res = await server.call_tool("potato_workspace_interact_and_record", args)
        assert isinstance(res, list) and res, "Expected a response"
        text = res[0].text
        # Response should be JSON-like containing paths
        assert "rec_test.webm" in text

        cmd = fake.last_command
        assert cmd is not None
        # Should find window id, activate and focus it
        assert "xdotool search --any 'Demo Window'" in cmd
        assert "xdotool windowactivate $win_id && xdotool windowfocus $win_id" in cmd
        # Should start a capture loop and save frames
        assert "/workspace/.agent/screenshots/rec_test_frames" in cmd
        assert "xfce4-screenshooter -f -s" in cmd
        # Should send keys with delays
        assert "xdotool key --clearmodifiers --delay 50" in cmd
        assert "xdotool key --clearmodifiers --delay 120" in cmd
        # Should compile frames with ffmpeg
        assert "ffmpeg -y -framerate $((1000/" in cmd
    finally:
        server.container_manager = orig_cm
        server._public_host = orig_host
        server._public_port = orig_port
