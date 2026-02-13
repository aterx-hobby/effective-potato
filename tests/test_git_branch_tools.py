import json
import pytest


class FakeContainerManager:
    def __init__(self):
        self.last_cmd = None

    def execute_command(self, command: str, task_id: str, extra_env=None):
        self.last_cmd = command
        # Default behavior: successful command
        return 0, "OK"


@pytest.mark.asyncio
async def test_branch_create_checkout(monkeypatch):
    from effective_potato import server
    fake = FakeContainerManager()
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        res = await server.call_tool(
            "potato_git_branch_create",
            {"repo_path": "proj", "name": "feature/x", "checkout": True},
        )
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 0
        assert "cd /workspace && cd -- 'proj' && git checkout -b 'feature/x'" in fake.last_cmd
    finally:
        server.container_manager = orig


@pytest.mark.asyncio
async def test_branch_delete_force(monkeypatch):
    from effective_potato import server
    fake = FakeContainerManager()
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        res = await server.call_tool(
            "potato_git_branch_delete",
            {"repo_path": "proj", "name": "old-topic", "force": True},
        )
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 0
        assert "cd /workspace && cd -- 'proj' && git branch -D 'old-topic'" in fake.last_cmd
    finally:
        server.container_manager = orig


@pytest.mark.asyncio
async def test_merge_into_detected_upstream(monkeypatch):
    from effective_potato import server

    class DetectingContainerManager(FakeContainerManager):
        def execute_command(self, command: str, task_id: str, extra_env=None):
            self.last_cmd = command
            # When detection command is used, return 'master' to simulate upstream
            if "git rev-parse --verify main" in command and "echo main" in command:
                return 0, "master\n"
            # For merge execution, succeed
            return 0, "merged"

    fake = DetectingContainerManager()
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        res = await server.call_tool(
            "potato_git_merge",
            {"repo_path": "proj", "source_branch": "feature/y"},
        )
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 0
        # The merge call should checkout detected target ('master') then merge
        assert "cd /workspace && cd -- 'proj' && git checkout 'master' && git merge --no-ff --no-edit 'feature/y'" in fake.last_cmd
    finally:
        server.container_manager = orig


@pytest.mark.asyncio
async def test_checkout_switch_branch(monkeypatch):
    from effective_potato import server
    fake = FakeContainerManager()
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        res = await server.call_tool(
            "potato_git_checkout",
            {"repo_path": "proj", "branch": "feature/z"},
        )
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 0
        assert "cd /workspace && cd -- 'proj' && git checkout 'feature/z'" in fake.last_cmd
    finally:
        server.container_manager = orig
