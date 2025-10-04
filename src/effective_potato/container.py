"""Docker container management for effective-potato."""

import os
import logging
import re
import uuid
from pathlib import Path
from typing import Optional
import docker
from docker.models.containers import Container

logger = logging.getLogger(__name__)


def validate_and_load_env_file(env_file_path: Path) -> dict[str, str]:
    """Validate and load environment variables from a .env file.
    
    Args:
        env_file_path: Path to the .env file
        
    Returns:
        Dictionary of environment variable name -> value
        
    Raises:
        ValueError: If the file contains invalid content (not just env vars)
    """
    env_vars = {}
    
    if not env_file_path.exists():
        return env_vars
    
    content = env_file_path.read_text()
    lines = content.strip().split('\n')
    
    # Pattern for valid environment variable assignments
    # Allows: VAR=value, VAR="value", VAR='value', export VAR=value
    env_pattern = re.compile(r'^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$')
    
    for line_num, line in enumerate(lines, 1):
        line = line.strip()
        
        # Skip empty lines and comments
        if not line or line.startswith('#'):
            continue
        
        # Check if line matches environment variable pattern
        match = env_pattern.match(line)
        if not match:
            raise ValueError(
                f"Invalid content in {env_file_path} at line {line_num}: '{line}'\n"
                f"Only environment variable assignments are allowed (e.g., VAR=value)"
            )
        
        var_name = match.group(1)
        var_value = match.group(2).strip()
        
        # Remove surrounding quotes if present
        if var_value.startswith('"') and var_value.endswith('"'):
            var_value = var_value[1:-1]
        elif var_value.startswith("'") and var_value.endswith("'"):
            var_value = var_value[1:-1]
        
        env_vars[var_name] = var_value
    
    return env_vars


class ContainerManager:
    """Manages the Ubuntu 24.04 Docker container lifecycle."""

    def __init__(
        self,
        workspace_dir: str = "workspace",
        env_file: str = "local/.env",
        sample_env_file: str = "local/sample.env",
    ):
        """Initialize the container manager.

        Args:
            workspace_dir: Path to the workspace directory to mount
            env_file: Path to the environment file
            sample_env_file: Path to the sample environment file
        """
        self.client = docker.from_env()
        self.container: Optional[Container] = None
        self.workspace_dir = Path(workspace_dir).absolute()
        self.env_file = Path(env_file).absolute()
        self.sample_env_file = Path(sample_env_file).absolute()
        self.image_name = "effective-potato-ubuntu"
        self.container_name = "effective-potato-sandbox"

        # Ensure workspace directories exist
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        (self.workspace_dir / ".tmp_agent_scripts").mkdir(parents=True, exist_ok=True)
        
        # Load and validate environment variables from .env file
        self.env_vars: dict[str, str] = {}
        if self.env_file.exists():
            try:
                self.env_vars = validate_and_load_env_file(self.env_file)
                logger.info(f"Loaded {len(self.env_vars)} environment variables from {self.env_file}")
            except ValueError as e:
                logger.error(f"Failed to load environment file: {e}")
                raise
        else:
            logger.warning(
                f"Environment file not found: {self.env_file}. "
                f"No custom environment variables will be set."
            )
            logger.warning(
                f"See {self.sample_env_file} for how to format a local/.env file"
            )

    def build_image(self) -> None:
        """Build the Docker image with Ubuntu 24.04 and required packages."""
        logger.info("Building Docker image...")

        # Build the image
        dockerfile_path = Path("Dockerfile").absolute()
        build_context = Path(".").absolute()

        image, build_logs = self.client.images.build(
            path=str(build_context),
            dockerfile=str(dockerfile_path),
            tag=self.image_name,
            rm=True,
            forcerm=True,
        )

        for log in build_logs:
            if "stream" in log:
                logger.debug(log["stream"].strip())

        logger.info(f"Successfully built image: {self.image_name}")

    def start_container(self) -> Container:
        """Start the Docker container with workspace mounted.

        Returns:
            The running container instance
        """
        # Stop and remove existing container if it exists
        self.stop_container()

        logger.info("Starting Docker container...")

        # Mount workspace directory
        volumes = {
            str(self.workspace_dir): {"bind": "/workspace", "mode": "rw"}
        }

        self.container = self.client.containers.run(
            self.image_name,
            name=self.container_name,
            detach=True,
            volumes=volumes,
            remove=False,
        )

        logger.info(f"Container started with ID: {self.container.id[:12]}")
        
        # Authenticate GitHub CLI if token is available
        if "GITHUB_PERSONAL_ACCESS_TOKEN" in self.env_vars:
            self._authenticate_github()
        
        return self.container

    def stop_container(self) -> None:
        """Stop and remove the container if it's running."""
        try:
            # Try to get existing container
            existing = self.client.containers.get(self.container_name)
            logger.info(f"Stopping existing container: {self.container_name}")
            existing.stop()
            existing.remove()
        except docker.errors.NotFound:
            pass  # Container doesn't exist, nothing to do
        except Exception as e:
            logger.warning(f"Error stopping container: {e}")

    def _authenticate_github(self) -> None:
        """Authenticate GitHub CLI with the token from environment variables."""
        if not self.container:
            raise RuntimeError("Container is not running")
        
        token = self.env_vars.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
        if not token:
            return
        
        logger.info("Authenticating GitHub CLI...")
        
        # Authenticate gh using the token
        auth_cmd = f"echo '{token}' | gh auth login --with-token"
        exec_result = self.container.exec_run(
            cmd=["bash", "-c", auth_cmd],
            demux=True,
        )
        
        if exec_result.exit_code == 0:
            logger.info("GitHub CLI authenticated successfully")
        else:
            stdout, stderr = exec_result.output
            error_output = ""
            if stdout:
                error_output += stdout.decode("utf-8")
            if stderr:
                error_output += stderr.decode("utf-8")
            logger.warning(f"GitHub CLI authentication failed: {error_output}")

    def is_github_available(self) -> bool:
        """Check if GitHub CLI is authenticated and available.
        
        Returns:
            True if GitHub CLI is authenticated, False otherwise
        """
        return "GITHUB_PERSONAL_ACCESS_TOKEN" in self.env_vars

    def execute_command(self, command: str, task_id: str) -> tuple[int, str]:
        """Execute a command in the container via a script file.

        Args:
            command: The command to execute
            task_id: Unique identifier for this task

        Returns:
            Tuple of (exit_code, output)
        """
        if not self.container:
            raise RuntimeError("Container is not running")

        # Create script file in workspace
        script_dir = self.workspace_dir / ".tmp_agent_scripts"
        script_path = script_dir / f"task_{task_id}.sh"

        # Build script content with environment variables prefixed
        script_content = "#!/bin/bash\n\n"
        
        # Add environment variable exports at the beginning
        for var_name, var_value in self.env_vars.items():
            # Escape single quotes in the value
            escaped_value = var_value.replace("'", "'\\''")
            script_content += f"export {var_name}='{escaped_value}'\n"
        
        if self.env_vars:
            script_content += "\n"
        
        # Add the actual command
        script_content += f"{command}\n"
        
        script_path.write_text(script_content)

        # Make it executable
        script_path.chmod(0o755)

        # Execute the script in the container
        container_script_path = f"/workspace/.tmp_agent_scripts/task_{task_id}.sh"
        exec_result = self.container.exec_run(
            cmd=container_script_path,
            demux=True,
        )

        exit_code = exec_result.exit_code
        stdout, stderr = exec_result.output

        # Combine stdout and stderr
        output = ""
        if stdout:
            output += stdout.decode("utf-8")
        if stderr:
            output += stderr.decode("utf-8")

        # Clean up the script file after execution
        try:
            if script_path.exists():
                script_path.unlink()
                logger.debug(f"Cleaned up script: {script_path}")
        except Exception as e:
            logger.warning(f"Failed to clean up script {script_path}: {e}")

        return exit_code, output

    def list_repositories(self, owner: str | None = None, limit: int = 30) -> tuple[int, str]:
        """List GitHub repositories.
        
        Args:
            owner: The username or organization to list repos for. If None, lists repos for the authenticated user.
            limit: Maximum number of repositories to list (default: 30)
        
        Returns:
            Tuple of (exit_code, output)
        """
        if not self.is_github_available():
            return 1, "GitHub CLI is not available. Set GITHUB_PERSONAL_ACCESS_TOKEN in local/.env"
        
        if not self.container:
            raise RuntimeError("Container is not running")
        
        # Generate unique task ID
        task_id = f"gh_list_{uuid.uuid4()}"
        
        # Build the gh repo list command
        if owner:
            command = f"gh repo list {owner} --limit {limit}"
        else:
            command = f"gh repo list --limit {limit}"
        
        # Execute using the standard execute_command method
        return self.execute_command(command, task_id)

    def clone_repository(self, owner: str, repo: str) -> tuple[int, str]:
        """Clone a GitHub repository to the workspace.
        
        Args:
            owner: The repository owner (username or organization)
            repo: The repository name
        
        Returns:
            Tuple of (exit_code, output)
        """
        if not self.is_github_available():
            return 1, "GitHub CLI is not available. Set GITHUB_PERSONAL_ACCESS_TOKEN in local/.env"
        
        if not self.container:
            raise RuntimeError("Container is not running")
        
        # Generate unique task ID
        task_id = f"gh_clone_{uuid.uuid4()}"
        
        # Build the clone command - clone into workspace
        command = f"cd /workspace && gh repo clone {owner}/{repo}"
        
        # Execute using the standard execute_command method
        return self.execute_command(command, task_id)

    def cleanup(self) -> None:
        """Clean up resources."""
        self.stop_container()
        
        # Clean up any remaining script files
        script_dir = self.workspace_dir / ".tmp_agent_scripts"
        if script_dir.exists():
            for script_file in script_dir.glob("task_*.sh"):
                try:
                    script_file.unlink()
                    logger.debug(f"Cleaned up remaining script: {script_file}")
                except Exception as e:
                    logger.warning(f"Failed to clean up script {script_file}: {e}")
        
        logger.info("Cleanup complete")
