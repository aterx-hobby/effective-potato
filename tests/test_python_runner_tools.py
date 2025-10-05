import pytest


class FakeContainerManager:
    def __init__(self):
        self.calls = []

    def execute_command(self, command: str, task_id: str, extra_env=None):
        self.calls.append({"cmd": command, "task_id": task_id})
        # Simulate success
        return 0, "OK"


@pytest.mark.asyncio
async def test_workspace_python_run_module_builds_command(monkeypatch):
    from effective_potato import server

    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        args = {
            "venv_path": "proj/.venv",
            "module": "http.server",
            "args": ["--bind", "127.0.0.1", "--help"],
        }
        res = await server.call_tool("workspace_python_run_module", args)
        assert isinstance(res, list) and res
        cmd = fake.calls[-1]["cmd"]
        assert cmd.startswith("/workspace/proj/.venv/bin/python -m http.server")
        assert "--bind" in cmd and "127.0.0.1" in cmd
    finally:
        server.container_manager = orig_cm


@pytest.mark.asyncio
async def test_workspace_python_run_script_builds_command(monkeypatch):
    from effective_potato import server

    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        args = {
            "venv_path": "proj/.venv",
            "script_path": "proj/app.py",
            "args": ["--version"],
        }
        res = await server.call_tool("workspace_python_run_script", args)
        assert isinstance(res, list) and res
        cmd = fake.calls[-1]["cmd"]
        # Expect python path under the venv and absolute /workspace path for script
        assert cmd.startswith("/workspace/proj/.venv/bin/python '/workspace/proj/app.py'")
        assert "--version" in cmd
    finally:
        server.container_manager = orig_cm
