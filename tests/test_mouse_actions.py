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
async def test_mouse_actions_builds_commands():
    from effective_potato import server

    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake

        args = {
            "window_title": "Demo",
            "actions": [
                {"type": "move", "x": 100, "y": 200, "sync": True},
                {"type": "move_relative", "dx": -20, "dy": 15, "polar": False},
                {"type": "mousedown", "button": 1, "clearmodifiers": True},
                {"type": "mouseup", "button": 1},
                {"type": "get_location", "shell": True, "prefix": "MOUSE_"},
            ],
        }

        res = await server.call_tool("potato_workspace_mouse_actions", args)
        assert isinstance(res, list) and res

        cmd = fake.last_command
        assert cmd is not None
        # Window search and focus present
        assert "xdotool search --any 'Demo' | head -n1" in cmd
        assert "xdotool windowactivate $win_id && xdotool windowfocus $win_id" in cmd
        # Move absolute with sync (may include --window $win_id)
        assert "xdotool mousemove --sync" in cmd
        assert " 100 200" in cmd
        # Move relative with '--' before coords and negative dx
        assert "xdotool mousemove_relative --  -20 15" in cmd or "xdotool mousemove_relative -- -20 15" in cmd
        # Click down/up
        assert "xdotool mousedown --clearmodifiers --window $win_id 1" in cmd
        assert "xdotool mouseup --window $win_id 1" in cmd
        # Location with shell and prefix
        assert "xdotool getmouselocation --shell --prefix 'MOUSE_'" in cmd
    finally:
        server.container_manager = orig_cm
