import os
import tempfile
from pathlib import Path
import uuid

import pytest

from effective_potato import server
from effective_potato.container import ContainerManager


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(600)
@pytest.mark.skipif(os.environ.get("POTATO_IT_ENABLE", "0") not in ("1", "true", "yes"), reason="Integration tests disabled. Set POTATO_IT_ENABLE=1 to run.")
async def test_tar_and_digest_end_to_end():
    # These file-centric tools were intentionally removed from the published MCP surface.
    # This integration test now verifies they remain unpublished even with a live container.
    with tempfile.TemporaryDirectory(dir=str(Path.cwd())) as tmp:
        ws = Path(tmp) / "workspace"
        proj = ws / "proj"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "a.txt").write_text("alpha\n")
        (proj / "b.txt").write_text("bravo\n")
        (proj / "sub").mkdir(parents=True, exist_ok=True)
        (proj / "sub" / "c.txt").write_text("charlie\n")

        env_file = Path(tmp) / ".env"; env_file.write_text("")
        sample_env = Path(tmp) / "sample.env"; sample_env.write_text("# sample\n")

        unique = uuid.uuid4().hex[:8]
        cname = f"effective-potato-sandbox-it-{unique}"
        image = os.environ.get("POTATO_IMAGE_NAME", "effective-potato-ubuntu")

        cm = ContainerManager(
            workspace_dir=str(ws),
            env_file=str(env_file),
            sample_env_file=str(sample_env),
            image_name=image,
            container_name=cname,
        )

        # Start container and wire server
        orig_cm = getattr(server, "container_manager", None)
        try:
            cm.build_image()
            cm.start_container()
            server.container_manager = cm

            tools = await server.list_tools()
            names = {t.name for t in tools}

            assert "potato_tar_create" not in names
            assert "potato_file_digest" not in names
        finally:
            try:
                server.container_manager = orig_cm
            finally:
                cm.cleanup()
