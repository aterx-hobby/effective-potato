"""Tests for workspace read/write helpers."""

import os
from pathlib import Path
import tempfile
import pytest
from effective_potato.container import ContainerManager


def test_write_and_read_text_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir) / "ws"
        ws.mkdir()
        cm = ContainerManager(workspace_dir=str(ws), env_file=str(Path(tmpdir)/".env"), sample_env_file=str(Path(tmpdir)/"sample.env"))

        rel = "sub/dir/hello.txt"
        cm.write_workspace_file(rel, "hello world")
        assert (ws / rel).exists()
        content = cm.read_workspace_file(rel)
        assert content == "hello world"


def test_write_and_read_binary_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir) / "ws"
        ws.mkdir()
        cm = ContainerManager(workspace_dir=str(ws), env_file=str(Path(tmpdir)/".env"), sample_env_file=str(Path(tmpdir)/"sample.env"))

        rel = "bin/data.bin"
        data = bytes([0, 1, 2, 3, 255])
        cm.write_workspace_file(rel, data)
        out = cm.read_workspace_file(rel, binary=True)
        assert isinstance(out, (bytes, bytearray))
        assert bytes(out) == data


def test_executable_flag_sets_permissions():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir) / "ws"
        ws.mkdir()
        cm = ContainerManager(workspace_dir=str(ws), env_file=str(Path(tmpdir)/".env"), sample_env_file=str(Path(tmpdir)/"sample.env"))

        rel = "scripts/run.sh"
        cm.write_workspace_file(rel, "#!/bin/bash\necho hi\n", executable=True)
        mode = (ws / rel).stat().st_mode
        assert mode & 0o111


def test_prevent_path_traversal():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir) / "ws"
        ws.mkdir()
        cm = ContainerManager(workspace_dir=str(ws), env_file=str(Path(tmpdir)/".env"), sample_env_file=str(Path(tmpdir)/"sample.env"))

        with pytest.raises(ValueError):
            cm.write_workspace_file("../outside.txt", "oops")
        with pytest.raises(ValueError):
            cm.read_workspace_file("../outside.txt")


def test_append_mode():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir) / "ws"
        ws.mkdir()
        cm = ContainerManager(workspace_dir=str(ws), env_file=str(Path(tmpdir)/".env"), sample_env_file=str(Path(tmpdir)/"sample.env"))

        rel = "logs/out.log"
        cm.write_workspace_file(rel, "a\n")
        cm.write_workspace_file(rel, "b\n", append=True)
        cm.write_workspace_file(rel, b"c\n", binary=True, append=True)
        content = cm.read_workspace_file(rel)
        assert content == "a\nb\nc\n"
