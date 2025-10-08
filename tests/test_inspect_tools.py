import json
import os
import pytest


@pytest.mark.asyncio
async def test_inspect_tools_returns_schemas(monkeypatch):
    from effective_potato import server
    # Ensure not in review-only for this test to get full set
    monkeypatch.delenv("POTATO_TOOLKIT", raising=False)
    res = await server.call_tool("inspect_tools", {})
    payload = json.loads(res[0].text)
    assert payload["count"] > 0
    names = [t["name"] for t in payload["tools"]]
    assert "inspect_tools" in names
    assert "workspace_execute_command" in names
    # Each tool should have description and inputSchema when available
    first = payload["tools"][0]
    assert "name" in first and "inputSchema" in first


@pytest.mark.asyncio
async def test_inspect_tools_available_in_review_only(monkeypatch):
    from effective_potato import server
    monkeypatch.setenv("POTATO_TOOLKIT", "review")
    res = await server.call_tool("inspect_tools", {})
    payload = json.loads(res[0].text)
    names = [t["name"] for t in payload["tools"]]
    # In review-only, inspect_tools must still be present
    assert "inspect_tools" in names
    # And review_ tools should be present, but not write-capable ones like workspace_write_file
    assert any(n.startswith("review_") for n in names)
    assert "workspace_write_file" not in names
