import pytest


class FakeContainerManager:
    def __init__(self):
        self.last_command = None
        self.last_task_id = None

    def execute_command(self, command: str, task_id: str):
        self.last_command = command
        self.last_task_id = task_id
        return 0, "./proj/.venv/pyvenv.cfg\n./proj2/env/bin/activate\n"


@pytest.mark.asyncio
async def test_find_venvs_builds_expected_command():
    from effective_potato import server

    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake

        res = await server.call_tool("potato_workspace_find_venvs", {"path": "projects"})
        assert isinstance(res, list) and res
        cmd = fake.last_command
        assert cmd is not None
        # Should search under projects and not exclude venv directories, but prune .git
        assert "cd /workspace && cd -- 'projects' && find . -type d -name .git -prune -o ( -type f -name 'pyvenv.cfg' -o -path '*/bin/activate' ) -print" in cmd
    finally:
        server.container_manager = orig_cm
