import json
import pytest


class FakeContainerManager:
    def __init__(self):
        self.last_cmd = None

    def execute_command(self, command: str, task_id: str, extra_env=None):
        self.last_cmd = command
        # Simulate plausible outputs
        if "sha256sum" in command or "md5sum" in command:
            return 0, "d41d8cd98f00b204e9800998ecf8427e  -\n"
        return 0, "OK"


@pytest.mark.asyncio
async def test_workspace_tar_create_builds_command(monkeypatch):
    from effective_potato import server

    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        args = {"base_dir": "proj", "items": ["README.md", "src"], "archive_name": "pkg.tar.gz"}
        res = await server.call_tool("workspace_tar_create", args)
        assert isinstance(res, list) and res
        cmd = fake.last_cmd
        assert cmd.startswith("cd /workspace && cd -- 'proj' && tar -czf 'pkg.tar.gz' ")
        assert "'README.md' 'src'" in cmd
        payload = json.loads(res[0].text)
        assert payload["exit_code"] == 0
        assert payload["archive"].endswith("/workspace/proj/pkg.tar.gz")
    finally:
        server.container_manager = orig_cm


@pytest.mark.asyncio
async def test_workspace_file_digest_builds_command(monkeypatch):
    from effective_potato import server

    fake = FakeContainerManager()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        args = {"path": "proj/empty.txt", "algorithm": "md5"}
        res = await server.call_tool("workspace_file_digest", args)
        assert isinstance(res, list) and res
        cmd = fake.last_cmd
        assert "cd /workspace && md5sum -- 'proj/empty.txt' | awk '{print $1}'" in cmd
        payload = json.loads(res[0].text)
        assert payload["algorithm"] == "md5"
        assert payload["digest"]
    finally:
        server.container_manager = orig_cm


@pytest.mark.asyncio
async def test_invalid_digest_algorithm_raises(monkeypatch):
    from effective_potato import server

    class FakeCM(FakeContainerManager):
        pass

    fake = FakeCM()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        with pytest.raises(ValueError):
            await server.call_tool("workspace_file_digest", {"path": "file.txt", "algorithm": "sha1"})
    finally:
        server.container_manager = orig_cm


@pytest.mark.asyncio
async def test_screenshot_schema_validation_defaults(monkeypatch):
    from effective_potato import server

    class FakeCM(FakeContainerManager):
        pass

    fake = FakeCM()
    orig_cm = getattr(server, "container_manager", None)
    try:
        server.container_manager = fake
        # Missing fields should be acceptable due to defaults
        res = await server.call_tool("workspace_screenshot", {})
        assert isinstance(res, list) and res
        assert "xfce4-screenshooter -f -s" in fake.last_cmd
    finally:
        server.container_manager = orig_cm
