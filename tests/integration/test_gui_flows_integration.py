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
async def test_workspace_screenshot_integration():
    # Take a fullscreen screenshot and verify it exists inside the container
    with tempfile.TemporaryDirectory(dir=str(Path.cwd())) as tmp:
        ws = Path(tmp) / "workspace"
        ws.mkdir(parents=True, exist_ok=True)

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

            # Request screenshot
            res = await server.call_tool("workspace_screenshot", {"delay_seconds": 1})
            data = json.loads(res[0].text)
            assert data.get("exit_code") == 0
            path = data.get("screenshot_path")
            assert isinstance(path, str) and path.startswith("/workspace/.agent/screenshots/")

            # Validate file exists inside the container
            code, out = cm.execute_command(f"test -f '{path}' && echo OK", f"it_{uuid.uuid4()}")
            assert code == 0 and "OK" in out
        finally:
            server.container_manager = orig_cm
            cm.cleanup()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(600)
@pytest.mark.skipif(os.environ.get("POTATO_IT_ENABLE", "0") not in ("1", "true", "yes"), reason="Integration tests disabled. Set POTATO_IT_ENABLE=1 to run.")
async def test_workspace_launch_and_screenshot_integration():
    # Launch a simple command and capture a screenshot
    with tempfile.TemporaryDirectory(dir=str(Path.cwd())) as tmp:
        ws = Path(tmp) / "workspace"
        ws.mkdir(parents=True, exist_ok=True)

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

            # Launch a benign command (no GUI window required) and take a screenshot
            res = await server.call_tool(
                "workspace_launch_and_screenshot",
                {"launch_command": "bash -lc 'echo started'", "delay_seconds": 1},
            )
            data = json.loads(res[0].text)
            assert data.get("exit_code") == 0
            path = data.get("screenshot_path")
            assert isinstance(path, str) and path.startswith("/workspace/.agent/screenshots/")

            code, out = cm.execute_command(f"test -f '{path}' && echo OK", f"it_{uuid.uuid4()}")
            assert code == 0 and "OK" in out
        finally:
            server.container_manager = orig_cm
            cm.cleanup()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(600)
@pytest.mark.skipif(os.environ.get("POTATO_IT_ENABLE", "0") not in ("1", "true", "yes"), reason="Integration tests disabled. Set POTATO_IT_ENABLE=1 to run.")
async def test_workspace_interact_and_record_integration():
    # Record a short video and verify the artifact exists
    with tempfile.TemporaryDirectory(dir=str(Path.cwd())) as tmp:
        ws = Path(tmp) / "workspace"
        ws.mkdir(parents=True, exist_ok=True)

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

            res = await server.call_tool(
                "workspace_interact_and_record",
                {"inputs": [{"keys": "Return"}], "duration_seconds": 3, "frame_interval_ms": 500, "output_basename": "it_video"},
            )
            data = json.loads(res[0].text)
            assert data.get("exit_code") == 0
            video = data.get("video")
            assert isinstance(video, str) and video.startswith("/workspace/.agent/screenshots/") and video.endswith(".webm")

            code, out = cm.execute_command(f"test -f '{video}' && echo OK", f"it_{uuid.uuid4()}")
            assert code == 0 and "OK" in out
        finally:
            server.container_manager = orig_cm
            cm.cleanup()
