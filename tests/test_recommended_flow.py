import json
import pytest


@pytest.mark.skip(reason="potato_recommended_flow is disabled/unpublished")
@pytest.mark.asyncio
async def test_flow_for_screenshot():
    from effective_potato import server

    res = await server.call_tool("potato_recommended_flow", {"query": "launch and screenshot a python app", "context": {"launch_command": "python -m app", "delay_seconds": 2}})
    assert isinstance(res, list) and res
    data = json.loads(res[0].text)
    steps = data["steps"]
    assert steps[0]["tool"] == "potato_find_venvs"
    assert steps[1]["tool"] == "potato_select_venv"
    assert steps[2]["tool"] == "potato_launch_and_screenshot"
    assert "venv" in steps[2]["args"] and steps[2]["args"]["venv"].startswith("${steps[1].")


@pytest.mark.skip(reason="potato_recommended_flow is disabled/unpublished")
@pytest.mark.asyncio
async def test_flow_for_record():
    from effective_potato import server

    res = await server.call_tool("potato_recommended_flow", {"query": "record an interaction video", "context": {}})
    data = json.loads(res[0].text)
    steps = data["steps"]
    assert steps[2]["tool"] == "potato_interact_and_record"
    # No window_title expected anymore; tool automatically targets most recently active window
    assert "window_title" not in steps[2]["args"]


@pytest.mark.skip(reason="potato_recommended_flow is disabled/unpublished")
@pytest.mark.asyncio
async def test_flow_for_archive_and_digest():
    from effective_potato import server

    res = await server.call_tool("potato_recommended_flow", {"query": "archive and compute digest", "context": {"items": ["."], "algorithm": "sha256"}})
    data = json.loads(res[0].text)
    steps = data["steps"]
    assert steps[0]["tool"] == "potato_tar_create"
    assert steps[1]["tool"] == "potato_file_digest"
