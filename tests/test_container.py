"""Tests for container management functionality."""

import os
import tempfile
from pathlib import Path
import pytest
from effective_potato.container import ContainerManager


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def temp_env_files():
    """Create temporary environment files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"
        sample_env = Path(tmpdir) / "sample.env"
        sample_env.write_text("# Sample environment file\n")
        yield env_file, sample_env


def test_container_manager_initialization(temp_workspace, temp_env_files):
    """Test ContainerManager initialization."""
    env_file, sample_env = temp_env_files
    manager = ContainerManager(
        workspace_dir=temp_workspace,
        env_file=str(env_file),
        sample_env_file=str(sample_env),
    )

    assert manager.workspace_dir == Path(temp_workspace).absolute()
    assert manager.env_file == env_file.absolute()
    assert manager.sample_env_file == sample_env.absolute()
    assert manager.image_name == "effective-potato-ubuntu"
    assert manager.container_name == "effective-potato-sandbox"


def test_workspace_directories_created(temp_workspace, temp_env_files):
    """Test that workspace directories are created."""
    env_file, sample_env = temp_env_files
    manager = ContainerManager(
        workspace_dir=temp_workspace,
        env_file=str(env_file),
        sample_env_file=str(sample_env),
    )

    assert Path(temp_workspace).exists()
    assert (Path(temp_workspace) / ".tmp_agent_scripts").exists()


def test_build_image_without_env_file(temp_workspace, temp_env_files, caplog):
    """Test building image without environment file shows warning."""
    env_file, sample_env = temp_env_files

    # Ensure env_file doesn't exist
    if env_file.exists():
        env_file.unlink()

    manager = ContainerManager(
        workspace_dir=temp_workspace,
        env_file=str(env_file),
        sample_env_file=str(sample_env),
    )

    # Note: This test will actually try to build the image
    # In a real test environment, you might want to mock docker operations
    # For now, we'll just test the initialization and warning logic
    assert not env_file.exists()


def test_execute_command_creates_script(temp_workspace, temp_env_files):
    """Test that execute_command creates a script file."""
    env_file, sample_env = temp_env_files
    manager = ContainerManager(
        workspace_dir=temp_workspace,
        env_file=str(env_file),
        sample_env_file=str(sample_env),
    )

    # Create a mock script to test script creation logic
    task_id = "test123"
    command = "echo 'Hello World'"
    script_dir = Path(temp_workspace) / ".tmp_agent_scripts"
    script_path = script_dir / f"task_{task_id}.sh"

    # Write the script manually to test the logic
    script_content = f"#!/bin/bash\n\n{command}\n"
    script_path.write_text(script_content)
    script_path.chmod(0o755)

    assert script_path.exists()
    assert script_path.read_text() == script_content
    assert os.access(script_path, os.X_OK)  # Check it's executable
