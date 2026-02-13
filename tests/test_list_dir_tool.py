import pytest


@pytest.mark.asyncio
async def test_workspace_list_dir_not_exposed():
    from effective_potato import server

    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert "potato_list_dir" not in names

    with pytest.raises(ValueError):
        await server.call_tool("potato_list_dir", {"path": "."})
