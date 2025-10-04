"""Unit tests for ContainerManager.run_pipeline.

These avoid Docker by only using file operations and no exec steps.
"""

import tempfile
from pathlib import Path
from effective_potato.container import ContainerManager


def test_pipeline_write_mkdir_read():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir) / "ws"
        ws.mkdir()
        cm = ContainerManager(workspace_dir=str(ws), env_file=str(Path(tmpdir)/".env"), sample_env_file=str(Path(tmpdir)/"sample.env"))

        steps = [
            {"type": "mkdir", "path": "a/b"},
            {"type": "write_file", "path": "a/b/hello.txt", "content": "hi"},
            {"type": "read_file", "path": "a/b/hello.txt"},
        ]

        result = cm.run_pipeline(steps)
        assert result["exit_code"] == 0
        # The read_file step is index 2
        reads = [r for r in result["results"] if r.get("type") == "read_file"]
        assert reads and reads[0].get("content") == "hi"
