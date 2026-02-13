import json
import pytest


class FakeContainerManager:
    def __init__(self):
        self.last_cmd = None

    def execute_command(self, command: str, task_id: str, extra_env=None):
        self.last_cmd = command
        return 0, "pushed"


@pytest.mark.asyncio
async def test_workspace_git_push_requires_confirmation(monkeypatch):
    from effective_potato import server

    fake = FakeContainerManager()
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        res = await server.call_tool("potato_git_push", {"repo_path": "proj"})
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 2
        assert "requires explicit approval" in payload["message"].lower()
    finally:
        server.container_manager = orig


@pytest.mark.asyncio
async def test_workspace_git_push_with_confirmation(monkeypatch):
    from effective_potato import server

    fake = FakeContainerManager()
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        res = await server.call_tool(
            "potato_git_push",
            {"repo_path": "proj", "remote": "origin", "branch": "main", "confirm": True},
        )
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 0
        assert "git push" in fake.last_cmd
        assert " 'origin' main" in fake.last_cmd
    finally:
        server.container_manager = orig
