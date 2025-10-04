"""Tests for local repository tracking and presence detection."""

import tempfile
from pathlib import Path
from effective_potato.container import ContainerManager


def test_add_and_list_local_repositories_presence_detection():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir) / "ws"
        ws.mkdir()
        env = Path(tmpdir) / ".env"
        env.write_text("")
        sample = Path(tmpdir) / "sample.env"
        sample.write_text("")

        cm = ContainerManager(workspace_dir=str(ws), env_file=str(env), sample_env_file=str(sample))

        # Simulate a cloned repo by creating a directory
        (ws / "myrepo").mkdir()
        cm.add_tracked_repo("octocat", "myrepo", description="Test repo")

        items = cm.list_local_repositories()
        assert len(items) == 1
        it = items[0]
        assert it["owner"] == "octocat"
        assert it["repo"] == "myrepo"
        assert it["path"] == "myrepo"
        assert it["present"] is True
        # workspace_path points to /workspace/<path> in the container; presence True indicates the local dir exists
        assert it["workspace_path"].endswith("/workspace/myrepo")

        # Now simulate deletion
        (ws / "myrepo").rmdir()
        items2 = cm.list_local_repositories()
        assert len(items2) == 1
        assert items2[0]["present"] is False
