import pytest


@pytest.mark.asyncio
async def test_workspace_find_not_exposed():
    from effective_potato import server

    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert "workspace_find" not in names

    with pytest.raises(ValueError):
        await server.call_tool("workspace_find", {"path": "."})
