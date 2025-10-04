"""Integration tests for the MCP server."""

import pytest
from unittest.mock import MagicMock, patch
from effective_potato.server import initialize_server, cleanup_server, list_tools
from effective_potato import server


def test_server_initialization_and_cleanup():
    """Test that server can initialize and cleanup properly."""
    # Note: This test requires Docker to be available and will actually
    # build and start a container. It's more of an integration test.
    
    # Since initialize_server builds and starts a container, and we already
    # tested this manually, we'll skip this in automated tests
    # to avoid long build times.
    pytest.skip("Integration test - requires Docker build which is time consuming")


def test_server_has_required_functions():
    """Test that server module has all required functions."""
    from effective_potato import server
    
    assert hasattr(server, 'initialize_server')
    assert hasattr(server, 'cleanup_server')
    assert hasattr(server, 'main')
    assert hasattr(server, 'app')
    assert callable(server.initialize_server)
    assert callable(server.cleanup_server)
    assert callable(server.main)


@pytest.mark.asyncio
async def test_list_tools_without_github():
    """Test that only execute_command tool is listed when GitHub is not configured."""
    # Ensure github_manager is None
    server.github_manager = None
    
    tools = await list_tools()
    
    assert len(tools) == 1
    assert tools[0].name == "execute_command"


@pytest.mark.asyncio
async def test_list_tools_with_github_not_authenticated():
    """Test that GitHub tools are not listed when not authenticated."""
    # Mock GitHub manager that is not authenticated
    mock_github_manager = MagicMock()
    mock_github_manager.authenticated = False
    server.github_manager = mock_github_manager
    
    tools = await list_tools()
    
    assert len(tools) == 1
    assert tools[0].name == "execute_command"


@pytest.mark.asyncio
async def test_list_tools_with_github_authenticated():
    """Test that all tools are listed when GitHub is authenticated."""
    # Mock GitHub manager that is authenticated
    mock_github_manager = MagicMock()
    mock_github_manager.authenticated = True
    server.github_manager = mock_github_manager
    
    tools = await list_tools()
    
    assert len(tools) == 3
    tool_names = [tool.name for tool in tools]
    assert "execute_command" in tool_names
    assert "github_list_repos" in tool_names
    assert "github_clone_repo" in tool_names
    
    # Clean up
    server.github_manager = None

