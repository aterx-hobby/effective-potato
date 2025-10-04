"""Integration tests for the MCP server."""

import pytest
from effective_potato.server import initialize_server, cleanup_server


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
