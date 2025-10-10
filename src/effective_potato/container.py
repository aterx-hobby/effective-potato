"""Docker container management for effective-potato."""

import io
import logging
import os
import re
import sys
import tarfile
import time
import uuid
from os import PathLike
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
    env_vars: dict[str, str] = {}

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
        *,
        image_name: str | None = None,
        container_name: str | None = None,
    ):
        """Initialize the container manager.

        Args:
            workspace_dir: Path to the workspace directory to mount
            env_file: Path to the environment file
            sample_env_file: Path to the sample environment file
        """
        self.client = docker.from_env()
        self.container: Optional[Container] = None
        self.container_id: Optional[str] = None
        # Allow overriding the host workspace directory via environment variable
        self.workspace_dir = Path(os.getenv("POTATO_WORKSPACE_DIR") or workspace_dir).absolute()
        self.env_file = Path(env_file).absolute()
        self.sample_env_file = Path(sample_env_file).absolute()
        # Allow overrides via args or environment to avoid conflicts (e.g., integration tests)
        base_image = image_name or os.getenv("POTATO_IMAGE_NAME") or "effective-potato-ubuntu"
        self.container_name = container_name or os.getenv("POTATO_CONTAINER_NAME") or "effective-potato-sandbox"
        # If this looks like an integration-test container (name contains -it-),
        # and the image tag matches the common production tag, derive a unique test image tag
        # to avoid clobbering the production image.
        if (
            isinstance(self.container_name, str)
            and self.container_name.startswith("effective-potato-sandbox-it-")
            and base_image in {os.getenv("POTATO_IMAGE_NAME") or "effective-potato-ubuntu", "effective-potato-ubuntu"}
        ):
            try:
                suffix = self.container_name.rsplit("-", 1)[-1]
                if suffix:
                    self.image_name = f"{base_image}-it-{suffix}"
                else:
                    self.image_name = f"{base_image}-it-{uuid.uuid4().hex[:8]}"
                logger.info(f"Using unique test image tag: {self.image_name} (base: {base_image})")
            except Exception:
                self.image_name = f"{base_image}-it-{uuid.uuid4().hex[:8]}"
                logger.info(f"Using unique test image tag: {self.image_name} (base: {base_image})")
        else:
            self.image_name = base_image

        # Track ownership of containers started by this manager instance
        self._owned_container = False
        self._force_stop_once = False

        # Ensure workspace directories exist
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        agent_dir = self.workspace_dir / ".agent"
        (agent_dir / "tmp_scripts").mkdir(parents=True, exist_ok=True)
        (agent_dir / "screenshots").mkdir(parents=True, exist_ok=True)

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
            logger.info(
                f"Environment file not found: {self.env_file}. "
                f"Proceeding without local overrides; process environment will be used if present."
            )
            logger.info(
                f"See {self.sample_env_file} for how to format a local/.env file (optional)"
            )

    def _env_get(self, *keys: str) -> Optional[str]:
        """Return the first non-empty value from loaded env file or process env.

        Checks self.env_vars (local/.env) first, then os.environ.
        """
        for k in keys:
            v = self.env_vars.get(k)
            if v:
                return v
        for k in keys:
            v = os.environ.get(k)
            if v:
                return v
        return None

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
        # Stop and remove existing container if it exists. Allow a one-time force to handle
        # production rotation while still protecting prod from unit-test cleanup calls.
        self._force_stop_once = True
        try:
            self.stop_container()
        finally:
            self._force_stop_once = False

        logger.info("Starting Docker container...")

        # Mount workspace directory (rw)
        volumes: dict[str, dict[str, str]] = {
            str(self.workspace_dir): {"bind": "/workspace", "mode": "rw"},
        }

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
        # Cache the current container id
        try:
            self.container_id = self.container.id
        except Exception:
            self.container_id = None

        logger.info(f"Container started with ID: {str(self.container_id)[:12]}")
        # We own this container instance; safe to stop on cleanup
        self._owned_container = True
        
        # Configure git user and SSH key for ubuntu
        try:
            self._setup_git_and_ssh()
        except Exception as e:
            logger.warning(f"Failed to set up git/ssh: {e}")

        # Authenticate GitHub CLI if token is available (from local/.env or process env)
        if self._env_get("GITHUB_PERSONAL_ACCESS_TOKEN"):
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

        git_name = self._env_get("GIT_USER_NAME", "EFFECTIVE_POTATO_GIT_NAME") or "effective-potato"
        git_email = self._env_get("GIT_USER_EMAIL", "EFFECTIVE_POTATO_GIT_EMAIL") or "effective-potato@aterx.com"

        # Ensure .ssh exists with correct perms
        self.container.exec_run(cmd=["bash", "-lc", "mkdir -p /home/ubuntu/.ssh"], user="ubuntu", demux=True)

        # Set git config as ubuntu
        cfg_cmd = (
            f"git config --global user.name '{git_name.replace('\'', '\'\\\'\'')}' && "
            f"git config --global user.email '{git_email.replace('\'', '\'\\\'\'')}'"
        )
        self.container.exec_run(cmd=["bash", "-lc", cfg_cmd], user="ubuntu", demux=True)

        # Copy SSH key into container if provided; set perms 0400 and owner ubuntu
        ssh_key_path = self._env_get("EFFECTIVE_POTATO_SSH_KEY_PATH", "SSH_PRIVATE_KEY_PATH")
        if ssh_key_path:
            try:
                p = Path(ssh_key_path).expanduser().absolute()
                if not (p.exists() and p.is_file()):
                    logger.warning(f"SSH key path does not exist or is not a file: {p}")
                else:
                    logger.info(f"Installing SSH key for ubuntu from: {p}")
                    # Create a tar stream containing id_rsa
                    data = p.read_bytes()
                    tar_stream = io.BytesIO()
                    with tarfile.open(fileobj=tar_stream, mode="w") as tar:
                        info = tarfile.TarInfo(name="id_rsa")
                        info.size = len(data)
                        info.mode = 0o400  # set 0400 as requested
                        tar.addfile(info, io.BytesIO(data))
                    tar_stream.seek(0)

                    # Put archive into /home/ubuntu/.ssh
                    self.container.put_archive("/home/ubuntu/.ssh", tar_stream.getvalue())

                    # Fix ownership and permissions explicitly
                    fix_cmd = (
                        "chown -R ubuntu:ubuntu /home/ubuntu/.ssh && "
                        "chmod 700 /home/ubuntu/.ssh && chmod 400 /home/ubuntu/.ssh/id_rsa"
                    )
                    self.container.exec_run(cmd=["bash", "-lc", fix_cmd], user="root", demux=True)
            except Exception as e:
                logger.warning(f"Failed to install SSH key: {e}")
        else:
            logger.info("No SSH key path provided (EFFECTIVE_POTATO_SSH_KEY_PATH/SSH_PRIVATE_KEY_PATH)")

        # Always write SSH config for GitHub to use the private key if present
        ssh_config_script = (
            "set -e; "
            "umask 077; "
            "mkdir -p /home/ubuntu/.ssh; "
            "cat > /home/ubuntu/.ssh/config <<'EOF'\n"
            "Host github.com\n"
            "        HostName github.com\n"
            "        User git\n"
            "        IdentityFile ~/.ssh/id_rsa\n"
            "        StrictHostKeyChecking accept-new\n\n"
            "EOF\n"
            "chown -R ubuntu:ubuntu /home/ubuntu/.ssh; "
            "chmod 700 /home/ubuntu/.ssh; chmod 600 /home/ubuntu/.ssh/config"
        )
        self.container.exec_run(cmd=["bash", "-lc", ssh_config_script], user="root", demux=True)

        # Ensure GitHub CLI clones via SSH by default for ubuntu
        try:
            self.container.exec_run(
                cmd=["bash", "-lc", "gh config set git_protocol ssh -h github.com || true"],
                user="ubuntu",
                demux=True,
            )
        except Exception as e:
            logger.warning(f"Failed to set gh git_protocol to ssh: {e}")

    def stop_container(self) -> None:
        """Stop and remove the container if it's running."""
        # Guard against accidentally stopping the production container during tests
        prod_name = (os.getenv("POTATO_CONTAINER_NAME") or "effective-potato-sandbox")
        is_prod = (self.container_name == prod_name)
        is_testish = isinstance(self.container_name, str) and self.container_name.startswith("effective-potato-sandbox-it-")
        explicit = (os.getenv("POTATO_ALLOW_CONTAINER_STOP", "").strip().lower() in ("1", "true", "yes"))
        force_once = getattr(self, "_force_stop_once", False)
        allow = force_once or self._owned_container or is_testish or explicit or (not is_prod)

        if not allow and is_prod:
            logger.warning(
                "Refusing to stop production container during this operation (guarded). "
                "Set POTATO_ALLOW_CONTAINER_STOP=1 to override if this is intentional."
            )
            return

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
        finally:
            # Clear cached handle/id; a future start will populate
            self.container = None
            self.container_id = None

    def is_container_running(self) -> bool:
        """Return True if the managed container exists and is running."""
        try:
            c = self.client.containers.get(self.container_name)
        except docker.errors.NotFound:
            return False
        except Exception as e:
            logger.warning(f"Failed to get container state: {e}")
            return False
        try:
            c.reload()
            state = (c.attrs or {}).get("State", {})
            running = bool(state.get("Running"))
            # Refresh cached handle/id when we can
            self.container = c
            try:
                self.container_id = c.id
            except Exception:
                pass
            return running
        except Exception:
            # Fallback to status field
            status = getattr(c, "status", None)
            is_running = status == "running"
            # Still attempt to refresh id
            try:
                self.container = c
                self.container_id = c.id
            except Exception:
                pass
            return is_running

    def ensure_container_alive(self) -> bool:
        """Ensure the container is running; start it if it is stopped or missing.

        Returns True if the container is running after this call, False on failure.
        """
        if self.is_container_running():
            # Keep a handle to the current container object
            try:
                self.container = self.client.containers.get(self.container_name)
                try:
                    self.container_id = self.container.id
                except Exception:
                    pass
            except Exception:
                pass
            return True
        # Collect diagnostics from the stopped container (if it exists) before restarting
        try:
            stopped = self.client.containers.get(self.container_name)
            try:
                stopped.reload()
            except Exception:
                pass
            self._collect_stopped_container_diagnostics(stopped)
        except docker.errors.NotFound:
            logger.warning("Container missing (not found by name); no diagnostics to collect.")
        except Exception as e:
            logger.warning(f"Failed to collect diagnostics for stopped container: {e}")

        logger.warning("Container is not running; attempting restart...")
        try:
            # Try to start existing container if present
            try:
                existing = self.client.containers.get(self.container_name)
                try:
                    existing.start()
                    time.sleep(1)
                    self.container = existing
                    try:
                        self.container_id = existing.id
                    except Exception:
                        pass
                    return True
                except docker.errors.NotFound:
                    pass
                except Exception:
                    # Fall through to full recreate
                    pass
            except docker.errors.NotFound:
                # Not found by name; recreate
                pass
            # Full recreate
            self.start_container()
            return True
        except Exception as e:
            logger.error(f"Failed to restart container: {e}")
            return False

    def get_container_id(self) -> Optional[str]:
        """Return the current container ID if known."""
        return self.container_id

    def _collect_stopped_container_diagnostics(self, cont: Container) -> None:
        """Collect basic diagnostics for a stopped/exited container and write under workspace/.agent/container.

        Captures:
        - inspect.json (container attrs)
        - logs.txt (last 2000 lines with timestamps)
        - summary.txt (selected state fields)
        - events.txt (recent Docker daemon events for this container)
        """
        try:
            # Prepare diagnostics directory
            diag_dir = self.workspace_dir / ".agent" / "container"
            diag_dir.mkdir(parents=True, exist_ok=True)

            # Determine naming: timestamp + short id if available
            ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            try:
                cid_short = (cont.id or "unknown")[:12]
            except Exception:
                cid_short = "unknown"
            base = f"diag_{ts}_{cid_short}"

            # Collect attrs
            attrs: dict[str, object] = {}
            try:
                attrs = cont.attrs or {}
            except Exception:
                attrs = {}

            # Write inspect.json
            try:
                import json as _json
                (diag_dir / f"{base}_inspect.json").write_text(_json.dumps(attrs, indent=2, sort_keys=True))
            except Exception as e:
                logger.debug(f"Failed writing inspect.json: {e}")

            # Collect logs (last 2000 lines)
            try:
                raw = cont.logs(timestamps=True, tail=2000) or b""
                # Ensure text
                try:
                    text = raw.decode("utf-8", errors="replace")
                except Exception:
                    text = str(raw)
                (diag_dir / f"{base}_logs.txt").write_text(text)
            except Exception as e:
                logger.debug(f"Failed collecting logs: {e}")

            # Write summary
            try:
                state_obj = (attrs or {}).get("State", {})
                # help mypy: ensure mapping[str, object]
                state: dict[str, object] = {}
                try:
                    if isinstance(state_obj, dict):
                        state = {str(k): v for k, v in state_obj.items()}
                    else:
                        state = {}
                except Exception:
                    state = {}
                lines = []
                for k in [
                    "Status",
                    "Running",
                    "Paused",
                    "Restarting",
                    "OOMKilled",
                    "Dead",
                    "ExitCode",
                    "Error",
                    "StartedAt",
                    "FinishedAt",
                ]:
                    v = state.get(k)
                    lines.append(f"{k}: {v}")
                (diag_dir / f"{base}_summary.txt").write_text("\n".join(lines))
            except Exception as e:
                logger.debug(f"Failed writing summary: {e}")

            # Collect recent Docker events for this container (last ~10 minutes)
            try:
                import datetime as _dt
                utc = _dt.timezone.utc
                since_dt = _dt.datetime.now(utc) - _dt.timedelta(minutes=10)
                since_iso = since_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
                # Filter by container name to be robust if id changed; Docker API events supports filters
                # Low-level API for events streaming; use client.api.events for better control
                events = self.client.api.events(filters={"container": [self.container_name]}, decode=True, since=since_iso)
                ev_lines: list[str] = []
                max_lines = 500
                for ev in events:
                    try:
                        # Stop after a sensible number to avoid huge files
                        if len(ev_lines) >= max_lines:
                            break
                        # Normalize timestamp if needed (not used directly; rely on ev['time'] field)
                        # Some events include Actor with Attributes; capture key fields
                        actor = ev.get("Actor", {}) or {}
                        attrs = actor.get("Attributes", {}) or {}
                        line = {
                            "status": ev.get("status"),
                            "id": ev.get("id"),
                            "time": ev.get("time"),
                            "type": ev.get("Type"),
                            "action": ev.get("Action"),
                            "actor_id": actor.get("ID"),
                            "attributes": {k: attrs.get(k) for k in sorted(attrs.keys()) if k in ("name", "exitCode", "signal", "oom-kill", "image")},
                        }
                        import json as _json
                        ev_lines.append(_json.dumps(line))
                    except Exception:
                        continue
                try:
                    (diag_dir / f"{base}_events.txt").write_text("\n".join(ev_lines))
                except Exception as e:
                    logger.debug(f"Failed writing events.txt: {e}")
            except Exception as e:
                logger.debug(f"Failed collecting Docker events: {e}")

            logger.warning(f"Collected container diagnostics at: {diag_dir} (base={base})")
        except Exception as e:
            logger.debug(f"Diagnostics collection encountered an error: {e}")

    def _authenticate_github(self) -> None:
        """Authenticate GitHub CLI with the token from environment variables."""
        if not self.container:
            raise RuntimeError("Container is not running")

        token = self._env_get("GITHUB_PERSONAL_ACCESS_TOKEN") or ""
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
            user="ubuntu",
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
        return bool(self._env_get("GITHUB_PERSONAL_ACCESS_TOKEN"))

    def _build_script_content(self, command: str) -> str:
        """Build the bash script content for a command without embedding env secrets.

        Only the raw command is included. Environment is injected at exec time via the
        Docker exec environment to avoid persisting secrets to disk.

        Args:
            command: The bash command to execute in the container.

        Returns:
            Script content as a string.
        """
        lines: list[str] = ["#!/bin/bash", ""]
        # Avoid exporting any env vars here; inject via exec_run(environment=...)
        lines.append(f"{command}")
        # Ensure a trailing newline
        if not lines[-1].endswith("\n"):
            lines[-1] = lines[-1] + "\n"
        return "\n".join(lines)

    def _compose_exec_env(self, extra_env: dict[str, str] | None = None) -> dict[str, str]:
        """Compose environment variables for container exec without persisting to disk.

        - Starts with values loaded from local .env (self.env_vars)
        - Fills common GitHub token aliases from any available token
        - Ensures DISPLAY defaults to :0
        - Merges any extra_env overrides
        """
        env: dict[str, str] = {}
        # Start with loaded env (do not mutate self.env_vars)
        for k, v in (self.env_vars or {}).items():
            env[str(k)] = str(v)

        # Ensure GitHub tokens are available under common names
        token_from_any = (
            self._env_get("GITHUB_PERSONAL_ACCESS_TOKEN")
            or self._env_get("GH_TOKEN")
            or self._env_get("GITHUB_TOKEN")
        )
        if token_from_any:
            # Respect explicit entries in local .env to avoid override
            if "GITHUB_PERSONAL_ACCESS_TOKEN" not in env:
                env["GITHUB_PERSONAL_ACCESS_TOKEN"] = token_from_any
            if "GH_TOKEN" not in env:
                env["GH_TOKEN"] = token_from_any
            if "GITHUB_TOKEN" not in env:
                env["GITHUB_TOKEN"] = token_from_any

        # DISPLAY for X apps
        env.setdefault("DISPLAY", ":0")

        # Merge caller-provided extras last
        if extra_env:
            for k, v in extra_env.items():
                env[str(k)] = str(v)
        return env

    def execute_command(self, command: str, task_id: str, *, extra_env: dict[str, str] | None = None) -> tuple[int, str]:
        """Execute a command in the container via a script file.

        Args:
            command: The command to execute
            task_id: Unique identifier for this task

        Returns:
            Tuple of (exit_code, output)
        """
        if not self.container:
            raise RuntimeError("Container is not running")

        # Create script file in workspace (no embedded env exports)
        script_dir = self.workspace_dir / ".agent" / "tmp_scripts"
        script_path = script_dir / f"task_{task_id}.sh"
        script_content = self._build_script_content(command)
        script_path.write_text(script_content)

        # Make it executable
        script_path.chmod(0o755)

        # Execute the script in the container
        container_script_path = f"/workspace/.agent/tmp_scripts/task_{task_id}.sh"
        # Compose ephemeral environment for this exec
        exec_env = self._compose_exec_env(extra_env)

        exec_result = self.container.exec_run(
            cmd=["bash", "-lc", container_script_path],
            demux=True,
            user="ubuntu",
            environment=exec_env,
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

    # ---------------------------
    # Task lifecycle (background)
    # ---------------------------
    def start_background_task(
        self,
        command: str,
        task_id: str,
        *,
        extra_env: dict[str, str] | None = None,
    ) -> dict:
        """Start a long-running command in the background inside the container.

        Writes a script file (without secrets), launches it detached, and records pid/code/out files
        under /workspace/.agent/tmp_scripts.

        Returns a dict with task_id.
        """
        if not self.container:
            raise RuntimeError("Container is not running")

        script_dir = self.workspace_dir / ".agent" / "tmp_scripts"
        script_path = script_dir / f"task_{task_id}.sh"
        script_path.write_text(self._build_script_content(command))
        script_path.chmod(0o755)

        container_script = f"/workspace/.agent/tmp_scripts/task_{task_id}.sh"
        container_out = f"/workspace/.agent/tmp_scripts/task_{task_id}.out"
        container_pid = f"/workspace/.agent/tmp_scripts/task_{task_id}.pid"
        container_code = f"/workspace/.agent/tmp_scripts/task_{task_id}.code"

        # Launch detached: run script, capture exit code, redirect all output, store pid
        launch = (
            "( bash '" + container_script + "' ; echo $? > '" + container_code + "' ) "
            "> '" + container_out + "' 2>&1 & echo $! > '" + container_pid + "'"
        )

        exec_env = self._compose_exec_env(extra_env)
        res = self.container.exec_run(
            cmd=["bash", "-lc", launch],
            demux=True,
            user="ubuntu",
            environment=exec_env,
        )
        # We don't require success here; the actual task may still start even if wrapper returns non-zero
        return {"task_id": task_id, "exit_code": getattr(res, "exit_code", None)}

    def get_task_status(self, task_id: str) -> dict:
        """Return background task status using container pid/code files.

        Response keys: running (bool), exit_code (int|None), has_output (bool)
        """
        if not self.container:
            raise RuntimeError("Container is not running")
        pid_p = f"/workspace/.agent/tmp_scripts/task_{task_id}.pid"
        code_p = f"/workspace/.agent/tmp_scripts/task_{task_id}.code"
        out_p = f"/workspace/.agent/tmp_scripts/task_{task_id}.out"
        probe = (
            "state=missing; ec=""; "
            f"if [ -f '{pid_p}' ]; then pid=$(cat '{pid_p}' 2>/dev/null); "
            "if [ -n \"$pid\" ] && kill -0 $pid 2>/dev/null; then state=running; else state=exited; fi; "
            f"fi; if [ -f '{code_p}' ]; then ec=$(cat '{code_p}' 2>/dev/null); fi; "
            "echo STATE:$state; echo EXIT:$ec; "
            f"test -f '{out_p}' && echo OUT:1 || echo OUT:0"
        )
        res = self.container.exec_run(cmd=["bash", "-lc", probe], demux=True, user="ubuntu")
        text = ""
        if res and res.output:
            stdout, stderr = res.output
            if stdout:
                text += stdout.decode("utf-8", errors="replace")
            if stderr:
                text += stderr.decode("utf-8", errors="replace")
        running = False
        exit_code: int | None = None
        has_output = False
        for line in text.splitlines():
            if line.startswith("STATE:"):
                running = (line.split(":", 1)[1].strip() == "running")
            elif line.startswith("EXIT:"):
                val = line.split(":", 1)[1].strip()
                if val != "":
                    try:
                        exit_code = int(val)
                    except ValueError:
                        exit_code = None
            elif line.startswith("OUT:"):
                has_output = line.split(":", 1)[1].strip() == "1"
        return {"task_id": task_id, "running": running, "exit_code": exit_code, "has_output": has_output}

    def kill_task(self, task_id: str, *, signal: str = "TERM") -> dict:
        """Attempt to terminate a background task by signal."""
        if not self.container:
            raise RuntimeError("Container is not running")
        pid_p = f"/workspace/.agent/tmp_scripts/task_{task_id}.pid"
        sig = signal or "TERM"
        # Use double escaping for $ inside f-string to avoid invalid escape sequence warnings
        cmd = (
            "bash -lc \"if [ -f '" + pid_p + "' ]; then pid=\\$(cat '" + pid_p + "' 2>/dev/null); "
            "kill -s " + sig + " \\${pid} 2>/dev/null || true; fi\""
        )
        res = self.container.exec_run(cmd=["bash", "-c", cmd], demux=True, user="ubuntu")
        ok = getattr(res, "exit_code", 1) == 0
        return {"task_id": task_id, "signaled": sig, "ok": ok}

    def list_repositories(self, owner: str | None = None, limit: int = 30) -> tuple[int, str]:
        """List GitHub repositories.
        
        Args:
            owner: The username or organization to list repos for. If None, lists repos for the authenticated user.
            limit: Maximum number of repositories to list (default: 30)
        
        Returns:
            Tuple of (exit_code, output)
        """
        if not self.is_github_available():
            return 1, "GitHub CLI is not available. Provide GITHUB_PERSONAL_ACCESS_TOKEN via environment or local/.env"
        
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
            return 1, "GitHub CLI is not available. Provide GITHUB_PERSONAL_ACCESS_TOKEN via environment or local/.env"
        
        if not self.container:
            raise RuntimeError("Container is not running")
        
        # Generate unique task ID
        task_id = f"gh_clone_{uuid.uuid4()}"
        
        # Build the clone command - clone into workspace
        command = f"cd /workspace && gh repo clone {owner}/{repo}"
        
        # Execute using the standard execute_command method
        exit_code, output = self.execute_command(command, task_id)
        # If successful, add to tracked repos (best-effort)
        if exit_code == 0:
            description: str | None = None
            try:
                # Attempt to read description via gh quickly
                info_cmd = (
                    "gh repo view "
                    f"{owner}/{repo}"
                    " --json description -q .description || true"
                )
                info_code, info_out = self.execute_command(info_cmd, f"info_{uuid.uuid4()}")
                if info_code == 0 and info_out:
                    description = info_out.strip()
            except Exception:
                pass
            try:
                self.add_tracked_repo(owner, repo, path=repo, description=description)
            except Exception as e:
                logger.warning(f"Failed to add tracked repo for {owner}/{repo}: {e}")
        return exit_code, output

    def cleanup(self) -> None:
        """Clean up resources."""
        self.stop_container()
        
        # Clean up any remaining script files
        script_dir = self.workspace_dir / ".agent" / "tmp_scripts"
        if script_dir.exists():
            for script_file in script_dir.glob("task_*.sh"):
                try:
                    script_file.unlink()
                    logger.debug(f"Cleaned up remaining script: {script_file}")
                except Exception as e:
                    logger.warning(f"Failed to clean up script {script_file}: {e}")
        
        logger.info("Cleanup complete")

    # ---------------------------
    # Workspace file I/O helpers
    # ---------------------------
    def _resolve_workspace_path(self, relative_path: str) -> Path:
        """Resolve a path within the workspace and prevent traversal outside it.

        Args:
            relative_path: Path relative to the workspace root.

        Returns:
            Absolute Path inside the workspace.

        Raises:
            ValueError: If the path is absolute or resolves outside the workspace.
        """
        if not relative_path:
            raise ValueError("relative_path must be a non-empty string")

        rel = Path(relative_path)
        if rel.is_absolute():
            raise ValueError("Absolute paths are not allowed; provide a path relative to the workspace root")

        # Resolve against the workspace and ensure containment (handles .. and symlinks)
        root = self.workspace_dir.resolve()
        resolved = (root / rel).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            raise ValueError("Path resolves outside the workspace; operation blocked")
        return resolved

    def write_workspace_file(
        self,
        relative_path: str,
        data,
        *,
        binary: bool | None = None,
        encoding: str = "utf-8",
        makedirs: bool = True,
        executable: bool = False,
        append: bool = False,
    ) -> Path:
        """Write a file under the mounted workspace safely.

        Args:
            relative_path: Path relative to the workspace root.
            data: Text (str) or bytes to write.
            binary: Force binary/text mode. If None, inferred from type of data.
            encoding: Text encoding when writing str data.
            makedirs: Create parent directories if missing.
            executable: Mark file as executable (adds 0o111 to mode) when True.
            append: Open the file for appending instead of overwriting.

        Returns:
            The absolute Path to the written file.
        """
        path = self._resolve_workspace_path(relative_path)

        if makedirs:
            path.parent.mkdir(parents=True, exist_ok=True)

        if binary is None:
            binary = not isinstance(data, str)

        mode = ("ab" if append else "wb") if binary else ("a" if append else "w")
        if binary:
            if not isinstance(data, (bytes, bytearray)):
                raise TypeError("Binary mode requires data to be bytes or bytearray")
            with open(path, mode) as f:
                f.write(data)
        else:
            if not isinstance(data, str):
                raise TypeError("Text mode requires data to be a str; set binary=True for bytes")
            with open(path, mode, encoding=encoding, newline="") as f:
                f.write(data)

        if executable:
            try:
                current = path.stat().st_mode
                path.chmod(current | 0o111)
            except Exception as e:
                logger.warning(f"Failed to mark file executable: {path}: {e}")

        return path

    def read_workspace_file(
        self,
        relative_path: str,
        *,
        binary: bool = False,
        encoding: str = "utf-8",
    ):
        """Read a file from the workspace safely.

        Args:
            relative_path: Path relative to the workspace root.
            binary: When True, return bytes; else return str decoded with encoding.
            encoding: Text encoding to use when binary is False.

        Returns:
            File contents as str or bytes.
        """
        path = self._resolve_workspace_path(relative_path)
        if binary:
            with open(path, "rb") as f:
                return f.read()
        with open(path, "r", encoding=encoding) as f:
            return f.read()

    # ---------------------------
    # JSON / YAML helpers
    # ---------------------------
    def write_workspace_json(self, relative_path: str, data, *, indent: int = 2, ensure_ascii: bool = False) -> Path:
        """Serialize data as JSON to a file in the workspace."""
        import json
        text = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
        return self.write_workspace_file(relative_path, text)

    def read_workspace_json(self, relative_path: str):
        """Read JSON from a file in the workspace and return parsed data."""
        import json
        text = self.read_workspace_file(relative_path)
        return json.loads(text)

    def write_workspace_yaml(self, relative_path: str, data) -> Path:
        """Serialize data as YAML to a file in the workspace."""
        import yaml  # type: ignore
        text = yaml.safe_dump(data, sort_keys=False)
        return self.write_workspace_file(relative_path, text)

    def read_workspace_yaml(self, relative_path: str):
        """Read YAML from a file in the workspace and return parsed data."""
        import yaml  # type: ignore
        text = self.read_workspace_file(relative_path)
        return yaml.safe_load(text)
    # ---------------------------
    # Pipelines
    # ---------------------------
    def run_pipeline(
        self,
        steps: list[dict],
        working_dir: str | None = None,
        stop_on_error: bool = True,
        extra_env: dict[str, str] | None = None,
    ) -> dict:
        """Run a pipeline of actions as a single container command.

        Supported step types:
          - write_file: {type, path, content (str or bytes), binary?, executable?, append?}
          - mkdir: {type, path}
          - exec: {type, command, cwd?}
          - read_file: {type, path, binary?, capture_as?}

        For exec steps, per-step outputs and exit codes are captured under
        /workspace/.agent/tmp_scripts/pipeline and summarized on return.

        Args:
            steps: Ordered list of action dicts.
            working_dir: Optional relative path under workspace used as base CWD for exec steps.
            stop_on_error: When True, abort pipeline on first failing exec.
            extra_env: Optional env vars to export for the script execution only.

        Returns:
            Structured result dict containing overall status and per-step details.
        """
        if not isinstance(steps, list) or not steps:
            raise ValueError("steps must be a non-empty list")

        pipeline_dir = self._resolve_workspace_path(".agent/tmp_scripts/pipeline")
        pipeline_dir.mkdir(parents=True, exist_ok=True)

        pre_results: list[dict] = []
        exec_items: list[tuple[int, dict]] = []
        for idx, step in enumerate(steps):
            stype = (step.get("type") or "").lower()
            if stype == "write_file":
                path = step.get("path")
                if not path:
                    raise ValueError("write_file requires 'path'")
                content = step.get("content", "")
                binary = bool(step.get("binary", False))
                executable = bool(step.get("executable", False))
                append = bool(step.get("append", False))
                self.write_workspace_file(path, content, binary=binary, executable=executable, append=append)
                pre_results.append({"index": idx, "type": stype, "status": "ok", "path": path})
            elif stype == "mkdir":
                path = step.get("path")
                if not path:
                    raise ValueError("mkdir requires 'path'")
                p = self._resolve_workspace_path(path)
                p.mkdir(parents=True, exist_ok=True)
                pre_results.append({"index": idx, "type": stype, "status": "ok", "path": path})
            elif stype in ("exec", "run", "command", "read_file"):
                exec_items.append((idx, step))
            else:
                raise ValueError(f"Unsupported step type: {stype}")

        task_id = f"pipeline_{uuid.uuid4()}"
        script_lines: list[str] = []
        # Do not export extra_env into the script to avoid persisting secrets; inject via exec env
        base_cwd = "/workspace"
        if working_dir:
            self._resolve_workspace_path(working_dir).mkdir(parents=True, exist_ok=True)
            base_cwd = f"/workspace/{working_dir}"
        script_lines.append(f"cd '{base_cwd}'")
        script_lines.append("set -o pipefail")
        script_lines.append("set -e" if stop_on_error else "set +e")

        step_map: dict[int, dict] = {}
        for idx, step in exec_items:
            stype = (step.get("type") or "").lower()
            out_base = f"/workspace/.agent/tmp_scripts/pipeline/{task_id}_{idx}"
            if stype in ("exec", "run", "command"):
                cmd = step.get("command")
                if not cmd:
                    raise ValueError("exec step requires 'command'")
                cwd = step.get("cwd")
                if cwd:
                    self._resolve_workspace_path(cwd).mkdir(parents=True, exist_ok=True)
                    script_lines.append(f"( cd '/workspace/{cwd}' && bash -lc \"{cmd}\" ) >'{out_base}.out' 2>&1")
                else:
                    script_lines.append(f"bash -lc \"{cmd}\" >'{out_base}.out' 2>&1")
                script_lines.append(f"echo $? > '{out_base}.code'")
                if stop_on_error:
                    script_lines.append(f"test \"$(cat '{out_base}.code')\" -eq 0")
                step_map[idx] = {"type": stype, "out": out_base + ".out", "code": out_base + ".code"}
            elif stype == "read_file":
                step_map[idx] = {"type": stype, "path": step.get("path"), "binary": bool(step.get("binary", False))}
            else:
                step_map[idx] = {"type": stype}

        exec_output = ""
        overall_exit = 0
        if any((d.get("type") in ("exec", "run", "command")) for d in step_map.values()):
            composite_cmd = "\n".join(script_lines)
            overall_exit, exec_output = self.execute_command(composite_cmd, task_id, extra_env=extra_env)

        results: list[dict] = []
        pre_by_index = {r["index"]: r for r in pre_results}
        for idx, step in enumerate(steps):
            stype = (step.get("type") or "").lower()
            if idx in pre_by_index:
                results.append(pre_by_index[idx])
                continue
            meta = step_map.get(idx, {"type": stype})
            if stype in ("exec", "run", "command"):
                out_raw = meta.get("out")
                code_raw = meta.get("code")
                out_p = Path(out_raw) if isinstance(out_raw, (str, PathLike)) else None
                code_p = Path(code_raw) if isinstance(code_raw, (str, PathLike)) else None
                out_txt = ""
                code_val = None
                try:
                    if out_p and out_p.exists():
                        out_txt = out_p.read_text(errors="replace")
                    if code_p and code_p.exists():
                        code_val = int((code_p.read_text() or "0").strip() or "0")
                except Exception as e:
                    logger.warning(f"Failed reading pipeline step artifacts: {e}")
                results.append({"index": idx, "type": stype, "exit_code": code_val, "output": out_txt})
            elif stype == "read_file":
                path = meta.get("path")
                binary = bool(meta.get("binary", False))
                try:
                    content = self.read_workspace_file(path, binary=binary) if path else None
                    results.append({"index": idx, "type": stype, "path": path, "binary": binary, "content": content})
                except Exception as e:
                    results.append({"index": idx, "type": stype, "path": path, "error": str(e)})
            else:
                results.append({"index": idx, "type": stype, "status": "ok"})

        # best-effort cleanup
        try:
            for f in pipeline_dir.glob(f"{task_id}_*.out"):
                f.unlink(missing_ok=True)
            for f in pipeline_dir.glob(f"{task_id}_*.code"):
                f.unlink(missing_ok=True)
        except Exception:
            pass

        return {
            "exit_code": overall_exit,
            "exec_output": exec_output,
            "results": results,
        }

    # ---------------------------
    # Repository tracking helpers
    # ---------------------------
    def _tracked_repos_path(self) -> Path:
        return (self.workspace_dir / ".agent" / "track_repos.json").resolve()

    def load_tracked_repos(self) -> list[dict]:
        path = self._tracked_repos_path()
        if not path.exists():
            return []
        try:
            import json
            return json.loads(path.read_text()) or []
        except Exception:
            return []

    def save_tracked_repos(self, repos: list[dict]) -> None:
        try:
            import json
            path = self._tracked_repos_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(repos, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"Failed to save tracked repos: {e}")

    def add_tracked_repo(
        self,
        owner: str,
        repo: str,
        *,
        path: str | None = None,
        description: str | None = None,
        shorthand: str | None = None,
    ) -> None:
        """Add or update a tracked repository record.

        Args:
            owner: GitHub owner/org
            repo: Repository name
            path: Workspace-relative directory name. Defaults to repo.
            description: Optional description
            shorthand: Optional display name; auto-deduped
        """
        from datetime import datetime, timezone

        repos = self.load_tracked_repos()
        full = f"{owner}/{repo}"
        # Default path is repository name
        repo_path = path or repo

        # Try update existing by full
        updated = False
        for r in repos:
            if r.get("full") == full:
                r["owner"] = owner
                r["repo"] = repo
                r["path"] = repo_path
                if description is not None:
                    r["description"] = description
                r["updated_at"] = datetime.now(timezone.utc).isoformat()
                updated = True
                break

        if not updated:
            shorthand_base = shorthand or repo
            name = shorthand_base
            existing_names = {r.get("name") for r in repos}
            if name in existing_names:
                i = 2
                while f"{shorthand_base}-{i}" in existing_names:
                    i += 1
                name = f"{shorthand_base}-{i}"
            entry = {
                "name": name,
                "full": full,
                "owner": owner,
                "repo": repo,
                "path": repo_path,
                "description": description or "",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            repos.append(entry)

        self.save_tracked_repos(repos)

    def list_local_repositories(self) -> list[dict]:
        """List repositories tracked in the workspace and detect presence.

        Returns a list of dicts including:
          - owner, repo, full, name, path
          - present (bool): whether the directory currently exists
          - workspace_path (str): path scoped to the container mount (e.g., /workspace/<path>)
        """
        repos = self.load_tracked_repos()
        results: list[dict] = []
        for r in repos:
            rel_any = r.get("path") or r.get("repo")
            rel = str(rel_any) if rel_any is not None else ""
            try:
                abs_path = self._resolve_workspace_path(rel)
            except Exception:
                abs_path = (self.workspace_dir / str(rel)).resolve()
            present = abs_path.exists() and abs_path.is_dir()
            out = dict(r)
            out["present"] = bool(present)
            # Expose only a workspace-scoped path, not the host absolute path
            rel_str = str(rel).lstrip("/")
            out["workspace_path"] = f"/workspace/{rel_str}"
            results.append(out)
        return results

    def prune_tracked_repositories(self, *, dry_run: bool = False) -> dict:
        """Remove tracked repository entries whose directories are missing.

        Args:
            dry_run: When True, do not modify the tracking file; just report what would change.

        Returns:
            A summary dict with counts and affected entries.
        """
        items = self.list_local_repositories()
        to_remove = [r for r in items if not r.get("present")]
        kept = [r for r in items if r.get("present")]

        if not dry_run:
            # Save only the kept items back to track_repos.json
            # Convert back to storage shape (drop computed fields)
            stored: list[dict] = []
            for r in kept:
                stored.append({
                    "name": r.get("name"),
                    "full": r.get("full"),
                    "owner": r.get("owner"),
                    "repo": r.get("repo"),
                    "path": r.get("path"),
                    "description": r.get("description", ""),
                    **({"created_at": r.get("created_at")} if r.get("created_at") else {}),
                    **({"updated_at": r.get("updated_at")} if r.get("updated_at") else {}),
                })
            self.save_tracked_repos(stored)

        return {
            "dry_run": bool(dry_run),
            "removed_count": len(to_remove),
            "kept_count": len(kept),
            "removed": [{"full": r.get("full"), "path": r.get("path")} for r in to_remove],
        }
