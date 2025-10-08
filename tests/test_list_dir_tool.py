import json
import pytest


class FakeContainerManager:
    def __init__(self, output: str = ""):
        self.last_cmd = None
        self.output = output

    def execute_command(self, command: str, task_id: str, extra_env=None):
        self.last_cmd = command
        return 0, self.output


@pytest.mark.asyncio
async def test_workspace_list_dir_builds_command_and_parses(monkeypatch):
    from effective_potato import server
    fake = FakeContainerManager(output="./proj\n./.git\n./.agent\n./docs\n")
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        res = await server.call_tool("workspace_list_dir", {"path": "."})
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 0
        # Command should use maxdepth/mindepth and exclude .git/.agent
        assert "find . -mindepth 1 -maxdepth 1 -type d ! -name .git ! -name .agent -print" in fake.last_cmd
        # Should include only directories returned from output; we don't post-filter .git/.agent beyond find
        assert "./proj" in payload["items"]
        assert "./docs" in payload["items"]
    finally:
        server.container_manager = orig
