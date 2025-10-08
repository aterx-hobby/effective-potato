import json
import pytest


class FakeContainerManager:
    def __init__(self):
        self.last_cmd = None

    def execute_command(self, command: str, task_id: str, extra_env=None):
        self.last_cmd = command
        return 0, "OK"


@pytest.mark.asyncio
async def test_python_check_syntax_builds_expected_command(monkeypatch):
    from effective_potato import server
    fake = FakeContainerManager()
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        res = await server.call_tool(
            "workspace_python_check_syntax",
            {"venv_path": ".venv", "source_path": "src/app.py"},
        )
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 0
        assert "source '/workspace/.venv/bin/activate' && python -m py_compile '/workspace/src/app.py'" in fake.last_cmd
    finally:
        server.container_manager = orig


@pytest.mark.asyncio
async def test_workspace_pytest_run_builds_expected_command(monkeypatch):
    from effective_potato import server
    fake = FakeContainerManager()
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        res = await server.call_tool(
            "workspace_pytest_run",
            {"venv_path": "venv", "args": ["-q", "tests/test_example.py"]},
        )
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 0
        assert "source '/workspace/venv/bin/activate' && pytest '-q' 'tests/test_example.py'" in fake.last_cmd
    finally:
        server.container_manager = orig
