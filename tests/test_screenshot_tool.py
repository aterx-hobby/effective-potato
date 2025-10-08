import json
import re
import pytest


class FakeContainerManager:
    def __init__(self):
        self.last_cmd = None

    def execute_command(self, command: str, task_id: str, extra_env=None):
        self.last_cmd = command
        return 0, "OK"


@pytest.mark.asyncio
async def test_workspace_screenshot_builds_command(monkeypatch):
    from effective_potato import server

    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        res = await server.call_tool("workspace_screenshot", {"filename": "one.png", "delay_seconds": 1})
        assert isinstance(res, list) and res
        data = json.loads(res[0].text)
        shot_path = data["screenshot_path"]
        # Expect UUID-suffixed filename preserving basename and extension
        assert shot_path.startswith("/workspace/.agent/screenshots/one_") and shot_path.endswith(".png")
        assert re.search(r"/workspace/.agent/screenshots/one_[0-9a-f]{32}\.png$", shot_path)
        cmd = fake.last_cmd
        assert cmd is not None
        assert "mkdir -p /workspace/.agent/screenshots && " in cmd
        assert "sleep 1; " in cmd
        assert "export DISPLAY=:0; " in cmd
        assert f"xfce4-screenshooter -f -s '{shot_path}'" in cmd
    finally:
        server.container_manager = orig_cm


@pytest.mark.asyncio
async def test_workspace_screenshot_negative_delay_coerces_or_raises(monkeypatch):
    from effective_potato import server

    class FakeContainerManager:
        def __init__(self):
            self.last_cmd = None

        def execute_command(self, command: str, task_id: str, extra_env=None):
            self.last_cmd = command
            return 0, "OK"

    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        # Pydantic schema enforces ge=0; expect ValueError when negative
        with pytest.raises(Exception):
            await server.call_tool("workspace_screenshot", {"delay_seconds": -1})
    finally:
        server.container_manager = orig_cm
