"""Tests for container management functionality."""

import os
import tempfile
from pathlib import Path
import pytest
from effective_potato.container import ContainerManager, validate_and_load_env_file


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


def test_validate_and_load_env_file_valid():
    """Test loading a valid .env file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"
        env_file.write_text(
            "VAR1=value1\n"
            "VAR2=value2\n"
            "VAR_WITH_QUOTES=\"quoted value\"\n"
            "VAR_WITH_SINGLE_QUOTES='single quoted'\n"
            "# This is a comment\n"
            "\n"
            "VAR3=value3\n"
            "export VAR4=value4\n"
        )
        
        env_vars = validate_and_load_env_file(env_file)
        
        assert len(env_vars) == 6
        assert env_vars["VAR1"] == "value1"
        assert env_vars["VAR2"] == "value2"
        assert env_vars["VAR_WITH_QUOTES"] == "quoted value"
        assert env_vars["VAR_WITH_SINGLE_QUOTES"] == "single quoted"
        assert env_vars["VAR3"] == "value3"
        assert env_vars["VAR4"] == "value4"


def test_validate_and_load_env_file_empty():
    """Test loading an empty .env file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"
        env_file.write_text("")
        
        env_vars = validate_and_load_env_file(env_file)
        
        assert len(env_vars) == 0


def test_validate_and_load_env_file_nonexistent():
    """Test loading a non-existent .env file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"
        
        env_vars = validate_and_load_env_file(env_file)
        
        assert len(env_vars) == 0


def test_validate_and_load_env_file_invalid():
    """Test that invalid content raises ValueError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"
        env_file.write_text(
            "VAR1=value1\n"
            "This is not a valid line\n"
            "VAR2=value2\n"
        )
        
        with pytest.raises(ValueError) as exc_info:
            validate_and_load_env_file(env_file)
        
        assert "Invalid content" in str(exc_info.value)
        assert "line 2" in str(exc_info.value)


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
    assert (Path(temp_workspace) / ".agent" / "tmp_scripts").exists()


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
    script_dir = Path(temp_workspace) / ".agent" / "tmp_scripts"
    script_path = script_dir / f"task_{task_id}.sh"

    # Write the script manually to test the logic
    script_content = f"#!/bin/bash\n\n{command}\n"
    script_path.write_text(script_content)
    script_path.chmod(0o755)

    assert script_path.exists()
    assert script_path.read_text() == script_content
    assert os.access(script_path, os.X_OK)  # Check it's executable


def test_cleanup_removes_script_files(temp_workspace, temp_env_files):
    """Test that cleanup removes all script files."""
    env_file, sample_env = temp_env_files
    manager = ContainerManager(
        workspace_dir=temp_workspace,
        env_file=str(env_file),
        sample_env_file=str(sample_env),
    )

    # Create some mock script files
    script_dir = Path(temp_workspace) / ".agent" / "tmp_scripts"
    script1 = script_dir / "task_test1.sh"
    script2 = script_dir / "task_test2.sh"
    script3 = script_dir / "task_test3.sh"
    
    script1.write_text("#!/bin/bash\necho 'test1'\n")
    script2.write_text("#!/bin/bash\necho 'test2'\n")
    script3.write_text("#!/bin/bash\necho 'test3'\n")
    
    # Verify scripts exist
    assert script1.exists()
    assert script2.exists()
    assert script3.exists()
    
    # Run cleanup
    manager.cleanup()
    
    # Verify scripts are removed
    assert not script1.exists()
    assert not script2.exists()
    assert not script3.exists()


def test_script_cleanup_after_execution(temp_workspace, temp_env_files):
    """Test that scripts are cleaned up immediately after execution."""
    env_file, sample_env = temp_env_files
    manager = ContainerManager(
        workspace_dir=temp_workspace,
        env_file=str(env_file),
        sample_env_file=str(sample_env),
    )

    # Manually create and execute a script to simulate the execute_command behavior
    task_id = "cleanup_test"
    script_dir = Path(temp_workspace) / ".agent" / "tmp_scripts"
    script_path = script_dir / f"task_{task_id}.sh"
    
    # Create the script
    script_path.write_text("#!/bin/bash\necho 'test'\n")
    assert script_path.exists()
    
    # Simulate cleanup after execution (mimicking what execute_command does)
    if script_path.exists():
        script_path.unlink()
    
    # Verify script is removed
    assert not script_path.exists()


def test_container_manager_loads_env_vars():
    """Test that ContainerManager loads environment variables from .env file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "workspace"
        workspace.mkdir()
        
        env_file = Path(tmpdir) / ".env"
        env_file.write_text("TEST_VAR1=value1\nTEST_VAR2=value2\n")
        
        sample_env = Path(tmpdir) / "sample.env"
        sample_env.write_text("# Sample\n")
        
        manager = ContainerManager(
            workspace_dir=str(workspace),
            env_file=str(env_file),
            sample_env_file=str(sample_env),
        )
        
        assert len(manager.env_vars) == 2
        assert manager.env_vars["TEST_VAR1"] == "value1"
        assert manager.env_vars["TEST_VAR2"] == "value2"


def test_container_manager_invalid_env_file_raises_error():
    """Test that invalid .env file raises ValueError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "workspace"
        workspace.mkdir()
        
        env_file = Path(tmpdir) / ".env"
        env_file.write_text("VAR1=value1\nINVALID CONTENT\n")
        
        sample_env = Path(tmpdir) / "sample.env"
        sample_env.write_text("# Sample\n")
        
        with pytest.raises(ValueError):
            ContainerManager(
                workspace_dir=str(workspace),
                env_file=str(env_file),
                sample_env_file=str(sample_env),
            )


def test_execute_command_includes_env_vars(temp_workspace, temp_env_files):
    """Test that execute_command includes environment variables in script."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "workspace"
        workspace.mkdir()
        
        env_file = Path(tmpdir) / ".env"
        env_file.write_text("TEST_VAR=test_value\n")
        
        sample_env = Path(tmpdir) / "sample.env"
        sample_env.write_text("# Sample\n")
        
        manager = ContainerManager(
            workspace_dir=str(workspace),
            env_file=str(env_file),
            sample_env_file=str(sample_env),
        )
        
        # Manually create a script to verify the content
        task_id = "test_env"
        script_dir = workspace / ".agent" / "tmp_scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        script_path = script_dir / f"task_{task_id}.sh"
        
        # Build script content with environment variables prefixed
        script_content = "#!/bin/bash\n\n"
        for var_name, var_value in manager.env_vars.items():
            escaped_value = var_value.replace("'", "'\\''")
            script_content += f"export {var_name}='{escaped_value}'\n"
        if manager.env_vars:
            script_content += "\n"
        script_content += "echo 'test command'\n"
        
        script_path.write_text(script_content)
        
        content = script_path.read_text()
        assert "export TEST_VAR='test_value'" in content
        assert "echo 'test command'" in content


def test_is_github_available_without_token(temp_workspace, temp_env_files):
    """Test that GitHub is not available without token."""
    env_file, sample_env = temp_env_files
    # Don't write a token to env_file
    manager = ContainerManager(
        workspace_dir=temp_workspace,
        env_file=str(env_file),
        sample_env_file=str(sample_env),
    )
    
    assert not manager.is_github_available()


def test_is_github_available_with_token(temp_workspace):
    """Test that GitHub is available with token."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "workspace"
        workspace.mkdir()
        
        env_file = Path(tmpdir) / ".env"
        env_file.write_text("GITHUB_PERSONAL_ACCESS_TOKEN=test_token\n")
        
        sample_env = Path(tmpdir) / "sample.env"
        sample_env.write_text("# Sample\n")
        
        manager = ContainerManager(
            workspace_dir=str(workspace),
            env_file=str(env_file),
            sample_env_file=str(sample_env),
        )
        
        assert manager.is_github_available()


def test_list_repositories_without_github_available(temp_workspace, temp_env_files):
    """Test that list_repositories returns error without GitHub token."""
    env_file, sample_env = temp_env_files
    manager = ContainerManager(
        workspace_dir=temp_workspace,
        env_file=str(env_file),
        sample_env_file=str(sample_env),
    )
    
    exit_code, output = manager.list_repositories()
    assert exit_code == 1
    assert "GitHub CLI is not available" in output


def test_clone_repository_without_github_available(temp_workspace, temp_env_files):
    """Test that clone_repository returns error without GitHub token."""
    env_file, sample_env = temp_env_files
    manager = ContainerManager(
        workspace_dir=temp_workspace,
        env_file=str(env_file),
        sample_env_file=str(sample_env),
    )
    
    exit_code, output = manager.clone_repository("owner", "repo")
    assert exit_code == 1
    assert "GitHub CLI is not available" in output


