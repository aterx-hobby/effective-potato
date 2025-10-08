import json
import pytest


class FakeContainerManager:
    def __init__(self):
        self.last_cmd = None

    def execute_command(self, command: str, task_id: str, extra_env=None):
        self.last_cmd = command
        # Simulate success by default
        return 0, "OK"


@pytest.mark.asyncio
async def test_block_git_init_at_workspace_root(monkeypatch):
    from effective_potato import server

    fake = FakeContainerManager()
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        # Direct init at root
        res = await server.call_tool(
            "workspace_execute_command",
            {"command": "cd /workspace && git init"},
        )
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 3
        assert payload.get("blocked") is True
        assert "git init" in payload.get("message", "").lower()

        # Variations that should also be blocked
        for cmd in [
            "cd -- '/workspace' && git init",
            "cd /workspace; git init",
            "git -C /workspace init",
            "cd /workspace && git init /workspace",
        ]:
            res = await server.call_tool("workspace_execute_command", {"command": cmd})
            payload = json.loads(res[0].text)
            assert payload["exit_code"] == 3
            assert payload.get("blocked") is True
    finally:
        server.container_manager = orig


@pytest.mark.asyncio
async def test_allow_git_init_in_subdirectory(monkeypatch):
    from effective_potato import server

    fake = FakeContainerManager()
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        # Allowed: init in subdir under workspace
        res = await server.call_tool(
            "workspace_execute_command",
            {"command": "cd /workspace && cd proj && git init"},
        )
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 0
        assert "git init" in fake.last_cmd
    finally:
        server.container_manager = orig
