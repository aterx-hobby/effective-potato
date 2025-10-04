"""Docker container management for effective-potato."""

import os
import logging
import shutil
from pathlib import Path
from typing import Optional
import docker
from docker.models.containers import Container

logger = logging.getLogger(__name__)


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

    def build_image(self) -> None:
        """Build the Docker image with Ubuntu 24.04 and required packages."""
        logger.info("Building Docker image...")

        # Check if environment file exists
        env_script_path = Path("environment.sh")
        if self.env_file.exists():
            logger.info(f"Found environment file: {self.env_file}")
            shutil.copy(self.env_file, env_script_path)
        else:
            logger.warning(
                f"No custom environment data is being copied. "
                f"Environment file not found: {self.env_file}"
            )
            logger.warning(
                f"See {self.sample_env_file} for how to format a local/.env file"
            )
            # Create an empty environment.sh file
            env_script_path.write_text("")

        try:
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

        finally:
            # Clean up temporary environment script
            if env_script_path.exists():
                env_script_path.unlink()

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

        # Write the script
        script_content = f"#!/bin/bash\n\n{command}\n"
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
