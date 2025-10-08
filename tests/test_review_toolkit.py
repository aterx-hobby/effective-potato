import json
import pytest


class FakeContainerManager:
    def __init__(self):
        self.last_cmd = None

    def execute_command(self, command: str, task_id: str, extra_env=None):
        self.last_cmd = command
        return 0, "OK"

    def is_github_available(self):
        return False


@pytest.mark.asyncio
async def test_review_tools_list_exposed(monkeypatch):
    from effective_potato import server

    # Patch a minimal container manager for list_tools conditions
    fake = FakeContainerManager()
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        tools = await server.list_tools()
        names = {t.name for t in tools}
        # A representative review tool should be present
        assert "review_workspace_git_status" in names
        assert "review_workspace_git_diff" in names
        # Disallowed review tools should not be fabricated
        assert "review_workspace_write_file" not in names
    finally:
        server.container_manager = orig


@pytest.mark.asyncio
async def test_review_tool_executes_base(monkeypatch):
    from effective_potato import server

    fake = FakeContainerManager()
    orig = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        res = await server.call_tool("review_workspace_git_status", {"repo_path": "proj"})
        assert isinstance(res, list) and res
        assert "cd /workspace && cd -- 'proj' && git status --porcelain=v1 -b" in fake.last_cmd
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 0
    finally:
        server.container_manager = orig


@pytest.mark.asyncio
async def test_review_tool_disallowed(monkeypatch):
    from effective_potato import server

    # No need for container manager since we expect a fast validation error
    with pytest.raises(ValueError):
        await server.call_tool("review_workspace_write_file", {"path": "x", "content": "y"})


@pytest.mark.asyncio
async def test_review_only_mode_exposes_only_prefixed(monkeypatch):
    from effective_potato import server

    fake = FakeContainerManager()
    import os
    orig_mgr = getattr(server, "container_manager", None)
    orig_env = os.environ.get("POTATO_TOOLKIT")
    try:
        server.container_manager = fake
        monkeypatch.setenv("POTATO_TOOLKIT", "review")
        tools = await server.list_tools()
        names = {t.name for t in tools}
        # All tools must be review_-prefixed
        assert names and all(n.startswith("review_") for n in names)
        # Direct base call should be blocked
        with pytest.raises(ValueError):
            await server.call_tool("workspace_git_status", {"repo_path": "proj"})
        # Prefixed call should work
        res = await server.call_tool("review_workspace_git_status", {"repo_path": "proj"})
        assert isinstance(res, list) and res
    finally:
        server.container_manager = orig_mgr
        if orig_env is None:
            monkeypatch.delenv("POTATO_TOOLKIT", raising=False)
        else:
            monkeypatch.setenv("POTATO_TOOLKIT", orig_env)
