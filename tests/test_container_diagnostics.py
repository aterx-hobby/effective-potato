import tempfile
from pathlib import Path

from effective_potato.container import ContainerManager


class _FakeContainer:
    def __init__(self, name: str):
        self.name = name
        self.id = "abcdef1234567890"
        self.attrs = {
            "Name": name,
            "State": {
                "Status": "exited",
                "Running": False,
                "Paused": False,
                "Restarting": False,
                "OOMKilled": True,
                "Dead": False,
                "ExitCode": 137,
                "Error": "",
                "StartedAt": "2025-10-06T01:23:45Z",
                "FinishedAt": "2025-10-06T01:25:00Z",
            },
        }

    def reload(self):
        return None

    def logs(self, timestamps=True, tail=2000):
        return (b"2025-10-06T01:24:59Z app[1]: running...\n"
                b"2025-10-06T01:25:00Z kernel: Out of memory: Kill process 1 (app) score 100 or sacrifice child\n")


class _FakeAPI:
    def events(self, filters=None, decode=True, since=None):
        # Yield a small set of fake events
        yield {
            "status": "die",
            "id": "abcdef1234567890",
            "time": 1696555500,
            "Type": "container",
            "Action": "die",
            "Actor": {
                "ID": "abcdef1234567890",
                "Attributes": {
                    "name": "effective-potato-sandbox",
                    "exitCode": "137",
                    "oom-kill": "true",
                    "image": "effective-potato-ubuntu",
                },
            },
        }


class _FakeContainers:
    def __init__(self, name: str):
        self._name = name

    def get(self, name: str):
        assert name == self._name
        return _FakeContainer(name)


class _FakeDockerClient:
    def __init__(self, name: str):
        self.containers = _FakeContainers(name)
        self.api = _FakeAPI()


def test_collects_diagnostics_on_stopped_container():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "workspace"
        ws.mkdir(parents=True, exist_ok=True)

        # Create manager with fake client
        mgr = ContainerManager(workspace_dir=str(ws), env_file=str(Path(tmp)/".env"), sample_env_file=str(Path(tmp)/"sample.env"))
        mgr.client = _FakeDockerClient(mgr.container_name)

        # Force is_container_running to return False so ensure_container_alive captures diagnostics
        mgr.is_container_running = lambda: False  # type: ignore

        # Avoid actually starting a container
        called = {"start": False}
        def _fake_start():
            called["start"] = True
            return None
        mgr.start_container = _fake_start  # type: ignore

        ok = mgr.ensure_container_alive()
        assert ok is True
        assert called["start"] is True

        diag_dir = ws / ".agent" / "container"
        assert diag_dir.exists(), "Diagnostics directory should be created"

        # Expect files with diag_* prefix
        inspect_files = list(diag_dir.glob("diag_*_inspect.json"))
        logs_files = list(diag_dir.glob("diag_*_logs.txt"))
        summary_files = list(diag_dir.glob("diag_*_summary.txt"))
        events_files = list(diag_dir.glob("diag_*_events.txt"))

        assert inspect_files, "inspect.json should be written"
        assert logs_files, "logs.txt should be written"
        assert summary_files, "summary.txt should be written"
        # events may be empty content but file should be present since we provide a fake stream
        assert events_files, "events.txt should be written"

        # Spot-check summary content includes ExitCode and OOMKilled values
        summary_text = summary_files[0].read_text()
        assert "ExitCode: 137" in summary_text
        assert "OOMKilled: True" in summary_text
