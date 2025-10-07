import json
import pytest


class FakeContainerManager:
    def __init__(self):
        self.last_cmd = None
        self.writes = {}

    def write_workspace_file(self, rel, content, **kwargs):
        self.writes[rel] = content
        return rel

    def execute_command(self, command: str, task_id: str, extra_env=None):
        self.last_cmd = command
        return 0, "applied"


@pytest.mark.asyncio
async def test_workspace_apply_patch_git_strategy(monkeypatch):
    from effective_potato import server

    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        diff = """\
diff --git a/README.md b/README.md
index e69de29..4b825dc 100644
--- a/README.md
+++ b/README.md
@@ -0,0 +1,1 @@
+hello
"""
        args = {"base_dir": ".", "diff": diff, "strategy": "git", "reject": True}
        res = await server.call_tool("workspace_apply_patch", args)
        assert isinstance(res, list) and res
        # The server should have written a temp patch file and built a git apply command
        assert any(k.startswith(".agent/tmp_scripts/patch_") for k in fake.writes.keys())
        cmd = fake.last_cmd
        assert cmd.startswith("cd /workspace && cd -- '.' && git apply --reject --whitespace=nowarn '/workspace/.agent/tmp_scripts/patch_")
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 0
        assert payload["output"] == "applied"
    finally:
        server.container_manager = orig_cm


@pytest.mark.asyncio
async def test_workspace_apply_patch_patch_strategy_with_strip(monkeypatch):
    from effective_potato import server

    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        diff = """\
--- a/src/file.txt\n+++ b/src/file.txt\n@@ -1,1 +1,1 @@\n-old\n+new\n"""
        args = {"base_dir": "proj", "diff": diff, "strategy": "patch", "strip": 1}
        res = await server.call_tool("workspace_apply_patch", args)
        assert isinstance(res, list) and res
        cmd = fake.last_cmd
        assert cmd.startswith("cd /workspace && cd -- 'proj' && patch -p1 -s -i '/workspace/.agent/tmp_scripts/patch_")
    finally:
        server.container_manager = orig_cm


@pytest.mark.asyncio
async def test_workspace_apply_patch_fallback_to_patch(monkeypatch):
    from effective_potato import server

    class FakeCM(FakeContainerManager):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def execute_command(self, command: str, task_id: str, extra_env=None):
            self.last_cmd = command
            self.calls += 1
            if "git apply" in command:
                return 128, "error: No valid patches in input\n"
            if "patch -p1" in command or "patch -p0" in command:
                return 0, "patched"
            return 0, "ok"

    fake = FakeCM()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        diff = """\
--- a/x.txt\n+++ b/x.txt\n@@ -1 +1 @@\n-old\n+new\n"""
        res = await server.call_tool("workspace_apply_patch", {"base_dir": ".", "diff": diff, "strategy": "git", "strip": 1})
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 0
        assert payload["strategy_used"] in {"patch"}
        assert any(attempt["strategy"] == "git" for attempt in payload["attempts"]) and any(attempt["strategy"] == "patch" for attempt in payload["attempts"]) 
    finally:
        server.container_manager = orig_cm
