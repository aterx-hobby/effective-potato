import json
import pytest


class FakeContainerManager:
    def __init__(self):
        self.last_cmd = None
        self.writes = {}
        self.files = {}

    def write_workspace_file(self, rel, content, **kwargs):
        # Simulate writes to regular workspace files (not used by new flow, but kept for compatibility)
        self.writes[rel] = content
        self.files[rel] = content
        return rel

    def execute_command(self, command: str, task_id: str, extra_env=None):
        self.last_cmd = command
        return 0, "applied"

    def read_workspace_file(self, rel, binary=False):
        if rel in self.files:
            return self.files[rel]
        # default a simple file
        raise FileNotFoundError(rel)


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
        # New flow: write diff to .agent/diffs and pass diff_path
        diff_rel = ".agent/diffs/example.diff"
        fake.write_workspace_file(diff_rel, diff)
        args = {"base_dir": ".", "diff_path": diff_rel, "strategy": "git", "reject": True}
        res = await server.call_tool("workspace_apply_patch", args)
        assert isinstance(res, list) and res
        # The server should build a git apply command using the provided diff_path
        cmd = fake.last_cmd
        assert cmd.startswith("cd /workspace && cd -- '.' && git apply --reject --whitespace=nowarn '/workspace/.agent/diffs/")
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
        diff_rel = ".agent/diffs/strip.diff"
        fake.write_workspace_file(diff_rel, diff)
        args = {"base_dir": "proj", "diff_path": diff_rel, "strategy": "patch", "strip": 1}
        res = await server.call_tool("workspace_apply_patch", args)
        assert isinstance(res, list) and res
        cmd = fake.last_cmd
        assert cmd.startswith("cd /workspace && cd -- 'proj' && patch -p1 -s -i '/workspace/.agent/diffs/")
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
        diff_rel = ".agent/diffs/fallback.diff"
        fake.write_workspace_file(diff_rel, diff)
        res = await server.call_tool("workspace_apply_patch", {"base_dir": ".", "diff_path": diff_rel, "strategy": "git", "strip": 1})
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 0
        assert payload["strategy_used"] in {"patch"}
        assert any(attempt["strategy"] == "git" for attempt in payload["attempts"]) and any(attempt["strategy"] == "patch" for attempt in payload["attempts"]) 
    finally:
        server.container_manager = orig_cm
