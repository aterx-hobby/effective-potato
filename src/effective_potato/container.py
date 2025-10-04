"""Docker container management for effective-potato."""

import os
import logging
import re
import sys
import time
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

        # Build the images: first base, then runner
        base_tag = "effective-potato-base"
        runner_tag = self.image_name
        build_context = Path(".").absolute()

        # Verbosity toggle: concise spinner by default; auto-disable spinner when not interactive (JSON-RPC stdio)
        verbose = os.getenv("POTATO_VERBOSE_BUILD", "").lower() in ("1", "true", "yes", "y", "on")
        interactive_stdout = sys.stdout.isatty()
        allow_concise = (not verbose) and interactive_stdout

        # Spinner/setup for concise mode
        step_re = re.compile(r"(?i)\bstep\s+(\d+)\s*/\s*(\d+)\s*:\s*(.*)")
        spinner_cycle = ["-", "\\", "|", "/"]
        spinner_idx = 0
        current_step: tuple[int, int, str] | None = None
        step_start_ts: float | None = None
        last_shown_secs = -1

        def concise_print(update_now: bool = False) -> None:
            if not allow_concise:
                return
            nonlocal spinner_idx, last_shown_secs
            if not current_step or step_start_ts is None:
                return
            elapsed = int(time.time() - step_start_ts)
            if not update_now and elapsed == last_shown_secs:
                return
            last_shown_secs = elapsed
            spinner_char = spinner_cycle[spinner_idx % len(spinner_cycle)]
            spinner_idx += 1
            idx, total, desc = current_step
            trimmed = desc.strip()
            if len(trimmed) > 100:
                trimmed = trimmed[:97] + "..."
            line = f"Step {idx}/{total}: {trimmed}  [{spinner_char} {elapsed}s]"
            try:
                sys.stdout.write("\r" + line)
                sys.stdout.flush()
            except Exception:
                pass

        def start_new_step(idx: int, total: int, desc: str) -> None:
            if not allow_concise:
                return
            nonlocal current_step, step_start_ts, last_shown_secs, spinner_idx
            if current_step is not None:
                try:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                except Exception:
                    pass
            current_step = (idx, total, desc)
            step_start_ts = time.time()
            spinner_idx = 0
            last_shown_secs = -1
            concise_print(update_now=True)

        def finish_concise() -> None:
            if not allow_concise:
                return
            try:
                if current_step is not None:
                    concise_print(update_now=True)
                    sys.stdout.write("\n")
                sys.stdout.flush()
            except Exception:
                pass

        # Use low-level API to stream build logs live
        def run_build(dockerfile: Path, tag: str, label: str) -> None:
            # Inner function to execute a single build with spinner/verbose modes
            nonlocal spinner_idx, current_step, step_start_ts, last_shown_secs
            spinner_idx = 0
            current_step = None
            step_start_ts = None
            last_shown_secs = -1
            logger.info(f"Building {label} image ({tag})...")
            try:
                build_stream = self.client.api.build(
                    path=str(build_context),
                    dockerfile=str(dockerfile),
                    tag=tag,
                    rm=True,
                    forcerm=True,
                    decode=True,
                )

                for chunk in build_stream:
                    try:
                        if "error" in chunk:
                            if not verbose:
                                finish_concise()
                            err = chunk.get("error")
                            logger.error(err)
                            raise docker.errors.BuildError(err, build_log=iter(()))

                        if verbose or not interactive_stdout:
                            if "stream" in chunk:
                                msg = chunk["stream"].rstrip()
                                if msg:
                                    logger.info(msg)
                            elif "status" in chunk:
                                status = chunk.get("status", "")
                                progress = chunk.get("progress", "")
                                detail = chunk.get("progressDetail", {})
                                if progress:
                                    logger.info(f"{status} {progress}")
                                elif detail:
                                    logger.info(f"{status} {detail}")
                                elif status:
                                    logger.info(status)
                            else:
                                logger.info(str(chunk))
                        else:
                            if "stream" in chunk:
                                msg = chunk["stream"].strip()
                                if not msg:
                                    concise_print()
                                    continue
                                m = step_re.search(msg)
                                if m:
                                    idx = int(m.group(1))
                                    total = int(m.group(2))
                                    desc = m.group(3)
                                    start_new_step(idx, total, desc)
                                    logger.info(f"Step {idx}/{total}: {desc.strip()}")
                                elif msg.lower().startswith("successfully built") or msg.lower().startswith("successfully tagged"):
                                    finish_concise()
                                else:
                                    concise_print()
                            else:
                                concise_print()
                    except Exception as e:
                        logger.debug(f"Failed to parse build log chunk: {e}; raw={chunk}")
            except TypeError:
                # Fallback for SDKs without low-level streaming signature
                image, build_logs = self.client.images.build(
                    path=str(build_context),
                    dockerfile=str(dockerfile),
                    tag=tag,
                    rm=True,
                    forcerm=True,
                )
                for log in build_logs:
                    if "error" in log:
                        if not verbose:
                            finish_concise()
                        logger.error(log.get("error"))
                        continue

                    if verbose or not interactive_stdout:
                        if "stream" in log:
                            msg = log["stream"].rstrip()
                            if msg:
                                logger.info(msg)
                        elif "status" in log:
                            status = log.get("status", "")
                            progress = log.get("progress", "")
                            detail = log.get("progressDetail", {})
                            if progress:
                                logger.info(f"{status} {progress}")
                            elif detail:
                                logger.info(f"{status} {detail}")
                            elif status:
                                logger.info(status)
                        else:
                            logger.info(str(log))
                    else:
                        if "stream" in log:
                            msg = log["stream"].strip()
                            if not msg:
                                concise_print()
                                continue
                            m = step_re.search(msg)
                            if m:
                                idx = int(m.group(1))
                                total = int(m.group(2))
                                desc = m.group(3)
                                start_new_step(idx, total, desc)
                                logger.info(f"Step {idx}/{total}: {desc.strip()}")
                            elif msg.lower().startswith("successfully built") or msg.lower().startswith("successfully tagged"):
                                finish_concise()
                            else:
                                concise_print()
                        else:
                            concise_print()
            except docker.errors.BuildError:
                # Already logged; re-raise to propagate failure
                raise

        # Build base first (Dockerfile.base), then runner (Dockerfile)
        run_build(Path("Dockerfile.base").absolute(), base_tag, label="base")
        logger.info(f"Successfully built image: {base_tag}")
        run_build(Path("Dockerfile").absolute(), runner_tag, label="runner")
        logger.info(f"Successfully built image: {runner_tag}")

    def start_container(self) -> Container:
        """Start the Docker container with workspace mounted.

        Returns:
            The running container instance
        """
        # Stop and remove existing container if it exists
        self.stop_container()

        logger.info("Starting Docker container...")

        # Mount workspace directory (rw)
        volumes: dict[str, dict[str, str]] = {
            str(self.workspace_dir): {"bind": "/workspace", "mode": "rw"}
        }

        # Optionally mount a host SSH private key read-only
        # Env variables (host side, loaded earlier):
        #   EFFECTIVE_POTATO_SSH_KEY_PATH: absolute path to private key file
        ssh_key_path = self.env_vars.get("EFFECTIVE_POTATO_SSH_KEY_PATH") or self.env_vars.get("SSH_PRIVATE_KEY_PATH")
        ssh_host_path: Optional[Path] = None
        if ssh_key_path:
            try:
                p = Path(ssh_key_path).expanduser().absolute()
                if p.exists() and p.is_file():
                    ssh_host_path = p
                    # Bind mount into container at a known read-only path
                    volumes[str(p)] = {"bind": "/ssh-ro/id_rsa", "mode": "ro"}
                else:
                    logger.warning(f"SSH key path does not exist or is not a file: {p}")
            except Exception as e:
                logger.warning(f"Invalid SSH key path '{ssh_key_path}': {e}")

        self.container = self.client.containers.run(
            self.image_name,
            name=self.container_name,
            detach=True,
            volumes=volumes,
            remove=False,
            mem_limit="4g",
            nano_cpus=2_000_000_000,
            environment={"DISPLAY": ":0"},
        )

        logger.info(f"Container started with ID: {self.container.id[:12]}")
        
        # Configure git user and SSH key for ubuntu
        try:
            self._setup_git_and_ssh()
        except Exception as e:
            logger.warning(f"Failed to set up git/ssh: {e}")

        # Authenticate GitHub CLI if token is available
        if "GITHUB_PERSONAL_ACCESS_TOKEN" in self.env_vars:
            self._authenticate_github()
        
        return self.container

    def _setup_git_and_ssh(self) -> None:
        """Initialize git config and install SSH private key for ubuntu if provided.

        Uses env vars:
          - GIT_USER_NAME or EFFECTIVE_POTATO_GIT_NAME (default: 'effective-potato')
          - GIT_USER_EMAIL or EFFECTIVE_POTATO_GIT_EMAIL (default: 'effective-potato@aterx.com')
          - EFFECTIVE_POTATO_SSH_KEY_PATH or SSH_PRIVATE_KEY_PATH (optional, host path)
        """
        if not self.container:
            return

        git_name = (
            self.env_vars.get("GIT_USER_NAME")
            or self.env_vars.get("EFFECTIVE_POTATO_GIT_NAME")
            or "effective-potato"
        )
        git_email = (
            self.env_vars.get("GIT_USER_EMAIL")
            or self.env_vars.get("EFFECTIVE_POTATO_GIT_EMAIL")
            or "effective-potato@aterx.com"
        )

        # Set git config as ubuntu
        cfg_cmd = (
            f"git config --global user.name '{git_name.replace("'", "'\\''")}' && "
            f"git config --global user.email '{git_email.replace("'", "'\\''")}'"
        )
        self.container.exec_run(cmd=["bash", "-lc", cfg_cmd], user="ubuntu", demux=True)

        # Install SSH key if present via mounted ro path
        # Key expected at /ssh-ro/id_rsa (mounted if provided)
        install_key_script = (
            "set -e; "
            "if [ -f /ssh-ro/id_rsa ]; then "
            "  mkdir -p /home/ubuntu/.ssh; "
            "  cp /ssh-ro/id_rsa /home/ubuntu/.ssh/id_rsa; "
            "  chown -R ubuntu:ubuntu /home/ubuntu/.ssh; "
            "  chmod 700 /home/ubuntu/.ssh; chmod 600 /home/ubuntu/.ssh/id_rsa; "
            "fi"
        )
        # Run as root to ensure ownership/permissions adjustments succeed
        self.container.exec_run(cmd=["bash", "-lc", install_key_script], user="root", demux=True)

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

        # Authenticate gh using the token with a timeout to avoid hangs
        auth_script = (
            f"set -e; echo '{token}' | gh auth login --with-token || true; "
            "gh auth status || true"
        )
        auth_cmd = f"timeout 20s bash -lc \"{auth_script}\""
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
        
        # Ensure DISPLAY is set for X apps
        if "DISPLAY" not in self.env_vars:
            script_content += "export DISPLAY=:0\n"
        # Add the actual command
        script_content += f"{command}\n"
        
        script_path.write_text(script_content)

        # Make it executable
        script_path.chmod(0o755)

        # Execute the script in the container
        container_script_path = f"/workspace/.tmp_agent_scripts/task_{task_id}.sh"
        exec_result = self.container.exec_run(
            cmd=["bash", "-lc", container_script_path],
            demux=True,
            user="ubuntu",
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
