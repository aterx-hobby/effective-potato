import pytest


class FakeContainerManager:
    def __init__(self):
        self.last_command = None
        self.last_task_id = None

    def execute_command(self, command: str, task_id: str):
        self.last_command = command
        self.last_task_id = task_id
        # Simulate a small listing
        return 0, "./file.txt\n./src\n./src/main.py\n./.git/config (should be pruned)\n./venv/bin/python (should be pruned)\n"


@pytest.mark.asyncio
async def test_workspace_find_command_and_prune():
    from effective_potato import server

    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        res = await server.call_tool("workspace_find", {"path": "projects/demo"})
        assert isinstance(res, list) and res
        txt = res[0].text
        import json as _json
        payload = _json.loads(txt)
        assert payload.get("exit_code") == 0

        cmd = fake.last_command
        assert cmd is not None
        # Should cd into /workspace then into relative path
        assert "cd /workspace && cd -- 'projects/demo' && find ." in cmd
        # Should include prune rules for .git, *venv*, *_env*
        assert "\\( -name .git -o -name '*venv*' -o -name '*_env*' \\) -prune" in cmd

        # With name and type filters
        res2 = await server.call_tool("workspace_find", {"path": "projects/demo", "name": "*.py", "type": "file"})
        cmd2 = fake.last_command
        assert " -type f -name '*.py' -print" in cmd2
    finally:
        server.container_manager = orig_cm
