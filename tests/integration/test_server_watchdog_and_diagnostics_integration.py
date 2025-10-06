import os
import tempfile
from pathlib import Path
import uuid
import time
import json

import pytest

from effective_potato import server
from effective_potato.container import ContainerManager


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("POTATO_IT_ENABLE", "0") not in ("1", "true", "yes"), reason="Integration tests disabled. Set POTATO_IT_ENABLE=1 to run.")
async def test_server_watchdog_restarts_and_collects_diagnostics():
    with tempfile.TemporaryDirectory(dir=str(Path.cwd())) as tmp:
        ws = Path(tmp) / "workspace"
        ws.mkdir(parents=True, exist_ok=True)

        env_file = Path(tmp) / ".env"; env_file.write_text("")
        sample_env = Path(tmp) / "sample.env"; sample_env.write_text("# sample\n")

        unique = uuid.uuid4().hex[:8]
        cname = f"effective-potato-sandbox-it-{unique}"
        image = os.environ.get("POTATO_IMAGE_NAME", "effective-potato-ubuntu")

        # Choose a random high port to avoid conflicts for HTTP server
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        os.environ["EFFECTIVE_POTATO_PORT"] = str(free_port)

        # Initialize a fresh container manager and inject into server
        cm = ContainerManager(
            workspace_dir=str(ws),
            env_file=str(env_file),
            sample_env_file=str(sample_env),
            image_name=image,
            container_name=cname,
        )

        # Wire into server and boot
        orig_cm = getattr(server, "container_manager", None)
        try:
            server.container_manager = cm
            server.initialize_server()

            # Stop the container externally to trigger watchdog
            cli = cm.client
            cont = cli.containers.get(cname)
            cont.stop()

            # Give watchdog time to detect and restart
            time.sleep(8)

            # Container should be running again
            assert cm.is_container_running() is True

            # Diagnostics should exist under workspace/.agent/container
            diag_dir = ws / ".agent" / "container"
            # Allow a small delay for file write
            for _ in range(10):
                if list(diag_dir.glob("diag_*_summary.txt")):
                    break
                time.sleep(0.5)
            assert list(diag_dir.glob("diag_*_summary.txt")), "Expected diagnostics summary to be written"
        finally:
            try:
                server.cleanup_server()
            finally:
                server.container_manager = orig_cm
