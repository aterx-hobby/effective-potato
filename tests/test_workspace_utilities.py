import pytest


class FakeContainerManager:
    def __init__(self):
        self.last_cmd = None

    def execute_command(self, command: str, task_id: str, extra_env=None):
        self.last_cmd = command
        return 0, "OK"


@pytest.mark.asyncio
async def test_screenshot_schema_validation_defaults(monkeypatch):
    from effective_potato import server

    class FakeCM(FakeContainerManager):
        pass

    fake = FakeCM()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        # Missing fields should be acceptable due to defaults
        res = await server.call_tool("workspace_screenshot", {})
        assert isinstance(res, list) and res
        assert "xfce4-screenshooter -f -s" in fake.last_cmd
    finally:
        server.container_manager = orig_cm
