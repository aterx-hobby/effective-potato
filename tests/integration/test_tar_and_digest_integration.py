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
async def test_tar_and_digest_end_to_end():
    # Set up a temporary workspace with a small project
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

            # Create a tar archive from the proj directory
            res1 = await server.call_tool(
                "workspace_tar_create",
                {"base_dir": "proj", "items": ["."], "archive_name": "bundle.tgz"}
            )
            data1 = json.loads(res1[0].text)
            assert data1.get("exit_code") == 0
            archive_path = data1.get("archive")
            assert archive_path and archive_path.endswith("/proj/bundle.tgz")

            # Verify archive exists inside container
            code, out = cm.execute_command("test -f /workspace/proj/bundle.tgz && echo OK", f"it_{uuid.uuid4()}")
            assert code == 0 and "OK" in out

            # Compute digest of the archive
            res2 = await server.call_tool(
                "workspace_file_digest",
                {"path": "/workspace/proj/bundle.tgz", "algorithm": "sha256"}
            )
            data2 = json.loads(res2[0].text)
            assert data2.get("exit_code") == 0
            digest = data2.get("digest", "")
            assert isinstance(digest, str) and len(digest) == 64  # sha256 hex length
        finally:
            try:
                server.container_manager = orig_cm
            finally:
                cm.cleanup()
