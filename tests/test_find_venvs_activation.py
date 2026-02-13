import pytest


class FakeContainerManager:
    def __init__(self):
        self.last_command = None
        self.last_task_id = None

    def execute_command(self, command: str, task_id: str):
        self.last_command = command
        self.last_task_id = task_id
        # Return both a root directory and a bin/activate path to test normalization
        return 0, "./proj/.venv/\n./proj2/env/bin/activate\n"


@pytest.mark.asyncio
async def test_find_venvs_returns_activation_commands():
    from effective_potato import server

    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        res = await server.call_tool("potato_find_venvs", {"path": "."})
        assert isinstance(res, list) and res
        import json
        data = json.loads(res[0].text)
        assert "venv_roots" in data and "activations" in data
        roots = set(data["venv_roots"])
        acts = set(data["activations"])
        assert "./proj/.venv" in roots
        assert "./proj2/env" in roots
        assert "source ./proj/.venv/bin/activate" in acts
        assert "source ./proj2/env/bin/activate" in acts
    finally:
        server.container_manager = orig_cm
