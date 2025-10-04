"""Tests for pruning tracked repositories."""

import tempfile
from pathlib import Path
from effective_potato.container import ContainerManager


def test_prune_tracked_repositories_dry_run_and_apply():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir) / "ws"
        ws.mkdir()
        env = Path(tmpdir) / ".env"
        env.write_text("")
        sample = Path(tmpdir) / "sample.env"
        sample.write_text("")

        cm = ContainerManager(workspace_dir=str(ws), env_file=str(env), sample_env_file=str(sample))

        # Create two repos, track both
        (ws / "r1").mkdir()
        (ws / "r2").mkdir()
        cm.add_tracked_repo("o", "r1")
        cm.add_tracked_repo("o", "r2")

        # Delete r2 to simulate missing
        (ws / "r2").rmdir()

        # Dry run: should report removal of r2 but not modify file
        dry = cm.prune_tracked_repositories(dry_run=True)
        assert dry["dry_run"] is True
        assert dry["removed_count"] == 1
        removed_fulls = {x["full"] for x in dry["removed"]}
        assert "o/r2" in removed_fulls

        # Apply prune
        applied = cm.prune_tracked_repositories(dry_run=False)
        assert applied["dry_run"] is False
        assert applied["removed_count"] == 1

        # Now listing should only return r1
        items = cm.list_local_repositories()
        names = {x["repo"] for x in items}
        assert names == {"r1"}
