import pytest


@pytest.mark.asyncio
async def test_list_tools_includes_core_tools_and_no_review_tools():
    from effective_potato import server

    tools = await server.list_tools()
    names = {t.name for t in tools}

    assert "potato_execute_command" in names
    assert "potato_screenshot" in names
    assert "potato_find_venvs" in names
    assert not any(n.startswith("review_") for n in names)
