import json
import pytest


@pytest.mark.asyncio
async def test_inspect_tools_returns_schemas(monkeypatch):
    from effective_potato import server
    res = await server.call_tool("inspect_tools", {})
    payload = json.loads(res[0].text)
    assert payload["count"] > 0
    names = [t["name"] for t in payload["tools"]]
    assert "inspect_tools" in names
    assert "workspace_execute_command" in names
    assert not any(n.startswith("review_") for n in names)
    # Each tool should have description and inputSchema when available
    first = payload["tools"][0]
    assert "name" in first and "inputSchema" in first
