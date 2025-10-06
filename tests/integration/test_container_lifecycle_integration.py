import os
import tempfile
from pathlib import Path
import uuid

import pytest

from effective_potato.container import ContainerManager


@pytest.mark.integration
@pytest.mark.timeout(600)
@pytest.mark.skipif(os.environ.get("POTATO_IT_ENABLE", "0") not in ("1", "true", "yes"), reason="Integration tests disabled. Set POTATO_IT_ENABLE=1 to run.")
def test_container_lifecycle_end_to_end():
    # Use a unique workspace and container name to avoid conflicts on shared hosts
    with tempfile.TemporaryDirectory(dir=str(Path.cwd())) as tmp:
        ws = Path(tmp) / "workspace"
        ws.mkdir(parents=True, exist_ok=True)

        unique = uuid.uuid4().hex[:8]
        cname = f"effective-potato-sandbox-it-{unique}"
        image = os.environ.get("POTATO_IMAGE_NAME", "effective-potato-ubuntu")

        # Ensure env file exists (optional)
        env_file = Path(tmp) / ".env"
        env_file.write_text("")
        sample_env = Path(tmp) / "sample.env"
        sample_env.write_text("# sample\n")

        cm = ContainerManager(
            workspace_dir=str(ws),
            env_file=str(env_file),
            sample_env_file=str(sample_env),
            image_name=image,
            container_name=cname,
        )

        # Build image and start container
        cm.build_image()
        cm.start_container()

        try:
            # Sanity exec inside container
            code, out = cm.execute_command("echo hello && whoami && pwd", f"it_{uuid.uuid4()}")
            assert code == 0
            assert "hello" in out

            # Verify workspace mount exists from inside container
            code2, out2 = cm.execute_command("test -d /workspace && echo MOUNT_OK", f"it_{uuid.uuid4()}")
            assert code2 == 0
            assert "MOUNT_OK" in out2
        finally:
            cm.cleanup()
