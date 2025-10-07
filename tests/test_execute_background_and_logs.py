import json
import pytest


class FakeContainerManager:
    def __init__(self):
        self.started = []
        self.execs = []

    def start_background_task(self, command: str, task_id: str, extra_env=None):
        self.started.append({"cmd": command, "task_id": task_id, "env": extra_env})
        return {"task_id": task_id, "exit_code": 0}

    def execute_command(self, command: str, task_id: str, extra_env=None):
        self.execs.append({"cmd": command, "task_id": task_id})
        # Simulate reading logs
        if command.startswith("test -f '/workspace/.agent/tmp_scripts/task_") and "tail -n" in command:
            return 0, "last line"
        if command.startswith("test -f '/workspace/.agent/tmp_scripts/task_") and "cat '" in command:
            return 0, "line1\nline2\n"
        return 0, "OK"


@pytest.mark.asyncio
async def test_workspace_execute_command_background(monkeypatch):
    from effective_potato import server

    fake = FakeContainerManager()
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        res = await server.call_tool("workspace_execute_command", {"command": "sleep 5", "background": True})
        payload = json.loads(res[0].text)
        assert payload["task_id"]
        assert any(s["cmd"] == "sleep 5" for s in fake.started)
    finally:
        server.container_manager = orig


@pytest.mark.asyncio
async def test_workspace_task_output_tail_and_full(monkeypatch):
    from effective_potato import server

    fake = FakeContainerManager()
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        # Tail last line
        res = await server.call_tool("workspace_task_output", {"task_id": "abc", "tail": 1})
        payload = json.loads(res[0].text)
        assert payload["content"].strip() == "last line"
        # Full content
        res = await server.call_tool("workspace_task_output", {"task_id": "abc", "tail": 0})
        payload = json.loads(res[0].text)
        assert payload["content"].splitlines() == ["line1", "line2"]
    finally:
        server.container_manager = orig
