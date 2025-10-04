"""Tests for JSON and YAML workspace helpers."""

import tempfile
from pathlib import Path
from effective_potato.container import ContainerManager


def test_json_read_write_unicode_and_nested():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir) / "ws"
        ws.mkdir()
        cm = ContainerManager(workspace_dir=str(ws), env_file=str(Path(tmpdir)/".env"), sample_env_file=str(Path(tmpdir)/"sample.env"))

        obj = {"message": "こんにちは", "num": 42, "nested": {"items": [1, 2, 3]}}
        cm.write_workspace_json("data/config.json", obj)
        read = cm.read_workspace_json("data/config.json")
        assert read == obj


def test_yaml_read_write_nested():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir) / "ws"
        ws.mkdir()
        cm = ContainerManager(workspace_dir=str(ws), env_file=str(Path(tmpdir)/".env"), sample_env_file=str(Path(tmpdir)/"sample.env"))

        obj = {"name": "app", "services": [{"n": 1}, {"n": 2}]}
        cm.write_workspace_yaml("data/config.yaml", obj)
        read = cm.read_workspace_yaml("data/config.yaml")
        assert read == obj
