import os
import tempfile
from pathlib import Path
import uuid
import json

import pytest

from effective_potato import server
from effective_potato.container import ContainerManager


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(600)
@pytest.mark.skipif(os.environ.get("POTATO_IT_ENABLE", "0") not in ("1", "true", "yes"), reason="Integration tests disabled. Set POTATO_IT_ENABLE=1 to run.")
async def test_workspace_find_venvs_against_live_container():
    # Create host workspace with a fake venv structure
    # Create under project root to avoid host /tmp mount constraints on some systems
    with tempfile.TemporaryDirectory(dir=str(Path.cwd())) as tmp:
        ws = Path(tmp) / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        proj = ws / "proj"
        (proj / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
        (proj / ".venv" / "bin" / "activate").write_text("#!/bin/bash\n")

        env_file = Path(tmp) / ".env"
        env_file.write_text("")
        sample_env = Path(tmp) / "sample.env"
        sample_env.write_text("# sample\n")

        unique = uuid.uuid4().hex[:8]
        cname = f"effective-potato-sandbox-it-{unique}"
        image = os.environ.get("POTATO_IMAGE_NAME", "effective-potato-ubuntu")

        # Initialize container manager and server
        cm = ContainerManager(
            workspace_dir=str(ws),
            env_file=str(env_file),
            sample_env_file=str(sample_env),
            image_name=image,
            container_name=cname,
        )

        try:
            cm.build_image()
            cm.start_container()

            # Wire the global server container_manager for this test scope
            original = getattr(server, "container_manager", None)
            server.container_manager = cm

            # Call potato_find_venvs via the MCP server
            res = await server.call_tool("potato_find_venvs", {"path": "."})
            assert isinstance(res, list) and res
            data = json.loads(res[0].text)
            roots = set(data.get("venv_roots", []))
            acts = set(data.get("activations", []))

            assert "./proj/.venv" in roots
            assert "source ./proj/.venv/bin/activate" in acts
        finally:
            # Restore server state and cleanup container
            server.container_manager = original
            cm.cleanup()
