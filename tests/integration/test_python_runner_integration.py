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
async def test_python_run_script_in_container():
    # Prepare a minimal project with a script and a venv path placeholder
    with tempfile.TemporaryDirectory(dir=str(Path.cwd())) as tmp:
        ws = Path(tmp) / "workspace"
        proj = ws / "proj"
        (proj / "bin").mkdir(parents=True, exist_ok=True)
        script = proj / "hello.py"
        script.write_text("print('hello-from-script')\n")

        # Create a "venv" by pointing to the system python inside container via a shim path
        # We'll create a fake venv path with bin/python symlink after container starts

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

        orig_cm = getattr(server, "container_manager", None)
        try:
            cm.build_image()
            cm.start_container()
            server.container_manager = cm

            # Inside the container, create a fake venv at /workspace/proj/.venv with bin/python -> /usr/bin/python3
            code, out = cm.execute_command(
                "mkdir -p /workspace/proj/.venv/bin && ln -sf /usr/bin/python3 /workspace/proj/.venv/bin/python && echo READY",
                f"it_{uuid.uuid4()}"
            )
            assert code == 0 and "READY" in out

            # Run the script using the workspace_python_run_script tool
            res = await server.call_tool(
                "workspace_python_run_script",
                {"venv_path": "proj/.venv", "script_path": "proj/hello.py"}
            )
            data = json.loads(res[0].text)
            assert data.get("exit_code") == 0
            assert "hello-from-script" in (data.get("output") or "")
        finally:
            try:
                server.container_manager = orig_cm
            finally:
                cm.cleanup()
