import pytest


@pytest.mark.asyncio
async def test_workspace_select_venv_heuristics_and_shape():
    from effective_potato import server

    candidates = [
        "projects/app/.venv",
        "projects/other/env",
        "projects/deep/path/venv",
        "projects/root/.env_custom",
        "projects/short/.venv",
    ]

    res = await server.call_tool("workspace_select_venv", {"paths": candidates})
    # Response is a list of TextContent items with JSON payload
    assert isinstance(res, list) and res
    payload_text = res[0].text
    import json

    data = json.loads(payload_text)
    assert "best" in data and "candidates" in data
    assert set(data["candidates"]) == set(candidates)
    # Expect one of the .venv paths to be chosen; shallower path should win among .venv
    assert data["best"] in {"projects/app/.venv", "projects/short/.venv"}
    # Prefer shallower, so 'projects/short/.venv' should be selected over deeper
    assert data["best"] == "projects/short/.venv"
