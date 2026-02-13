import json
import pytest


class FakeContainerManager:
    def __init__(self):
        self.last_cmd = None

    def execute_command(self, command: str, task_id: str, extra_env=None):
        self.last_cmd = command
        return 0, "OK"


@pytest.mark.asyncio
async def test_workspace_git_status_porcelain(monkeypatch):
    from effective_potato import server

    fake = FakeContainerManager()
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        res = await server.call_tool("potato_git_status", {"repo_path": "proj"})
        assert isinstance(res, list) and res
        assert "cd /workspace && cd -- 'proj' && git status --porcelain=v1 -b" in fake.last_cmd
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 0
    finally:
        server.container_manager = orig


@pytest.mark.asyncio
async def test_workspace_git_diff_staged_name_only_with_paths(monkeypatch):
    from effective_potato import server

    fake = FakeContainerManager()
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        res = await server.call_tool(
            "potato_git_diff",
            {"repo_path": "proj", "staged": True, "name_only": True, "unified": 0, "paths": ["src/app.py", "README.md"]},
        )
        assert isinstance(res, list) and res
        cmd = fake.last_cmd
        assert "cd /workspace && cd -- 'proj' && git diff --cached --name-only --unified=0 -- 'src/app.py' 'README.md'" in cmd
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 0
    finally:
        server.container_manager = orig
