import pytest


@pytest.mark.asyncio
async def test_file_review_and_search_tools_not_exposed():
    """The server is intended to be used by coding agents that already provide
    filesystem primitives (glob/list/read/search/write/applyDiff). effective-potato
    should not publish overlapping tools.
    """

    from effective_potato import server

    tools = await server.list_tools()
    names = {t.name for t in tools}

    removed = {
        "workspace_apply_patch",
        "workspace_find",
        "workspace_list_dir",
        "workspace_read_file",
        "workspace_write_file",
        "workspace_tar_create",
        "workspace_file_digest",
    }
    assert not (removed & names)

    for tool_name in sorted(removed):
        with pytest.raises(ValueError):
            await server.call_tool(tool_name, {})


@pytest.mark.asyncio
async def test_review_prefixed_file_tools_not_allowed():
    from effective_potato import server

    with pytest.raises(ValueError):
        await server.call_tool("review_workspace_read_file", {"path": "README.md"})

    with pytest.raises(ValueError):
        await server.call_tool("review_workspace_find", {"path": "."})

    with pytest.raises(ValueError):
        await server.call_tool("review_workspace_git_status", {"repo_path": "."})
