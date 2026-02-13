import json
import pytest


class FakeContainerManager:
    def __init__(self, files_output: str):
        self.files_output = files_output
        self.exec_calls = []

    def execute_command(self, command: str, task_id: str, extra_env=None):
        self.exec_calls.append(command)
        # Simulate ls of pid files: returns files_output
        return 0, self.files_output

    def get_task_status(self, task_id: str) -> dict:
        # Return a simple status stub
        return {"task_id": task_id, "running": task_id.endswith("a"), "exit_code": None}


@pytest.mark.asyncio
async def test_workspace_task_list_ids_only(monkeypatch):
    from effective_potato import server

    # Simulate two tasks (a, b)
    pid_listing = """\
task_123a.pid
task_456b.pid
"""
    fake = FakeContainerManager(pid_listing)
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        res = await server.call_tool("potato_task_list", {})
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 0
        assert payload["tasks"] == ["123a", "456b"]
        assert "statuses" not in payload
    finally:
        server.container_manager = orig


@pytest.mark.asyncio
async def test_workspace_task_list_with_status(monkeypatch):
    from effective_potato import server

    pid_listing = "task_123a.pid\n"
    fake = FakeContainerManager(pid_listing)
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        res = await server.call_tool("potato_task_list", {"include_status": True})
        payload = json.loads(res[0].text)
        assert payload["tasks"] == ["123a"]
        assert "statuses" in payload and "123a" in payload["statuses"]
        st = payload["statuses"]["123a"]
        assert st.get("task_id") == "123a" and "running" in st
    finally:
        server.container_manager = orig
