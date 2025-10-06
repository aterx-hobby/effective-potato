import json
import pytest


@pytest.mark.asyncio
async def test_flow_for_screenshot():
    from effective_potato import server

    res = await server.call_tool("workspace_recommended_flow", {"query": "launch and screenshot a python app", "context": {"launch_command": "python -m app", "delay_seconds": 2}})
    assert isinstance(res, list) and res
    data = json.loads(res[0].text)
    steps = data["steps"]
    assert steps[0]["tool"] == "workspace_find_venvs"
    assert steps[1]["tool"] == "workspace_select_venv"
    assert steps[2]["tool"] == "workspace_launch_and_screenshot"
    assert "venv" in steps[2]["args"] and steps[2]["args"]["venv"].startswith("${steps[1].")


@pytest.mark.asyncio
async def test_flow_for_record():
    from effective_potato import server

    res = await server.call_tool("workspace_recommended_flow", {"query": "record an interaction video", "context": {"window_title": "App"}})
    data = json.loads(res[0].text)
    steps = data["steps"]
    assert steps[2]["tool"] == "workspace_interact_and_record"
    assert "window_title" in steps[2]["args"]


@pytest.mark.asyncio
async def test_flow_for_archive_and_digest():
    from effective_potato import server

    res = await server.call_tool("workspace_recommended_flow", {"query": "archive and compute digest", "context": {"items": ["."], "algorithm": "sha256"}})
    data = json.loads(res[0].text)
    steps = data["steps"]
    assert steps[0]["tool"] == "workspace_tar_create"
    assert steps[1]["tool"] == "workspace_file_digest"
