"""Integration tests for the MCP server."""

import os
import pytest
from effective_potato.server import initialize_server, cleanup_server


@pytest.mark.integration
def test_server_initialization_and_cleanup():
    """Test that server can initialize and cleanup properly when enabled.

    This test performs a real Docker build and container start. To avoid
    slowing down normal test runs or failing on systems without Docker,
    it only runs when explicitly enabled and Docker is reachable.
    Enable by setting RUN_INTEGRATION_TESTS=1.
    """

    # Only run when explicitly requested
    if os.environ.get("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run this integration test")

    # Ensure Docker Engine is available
    try:
        import docker  # type: ignore

        client = docker.from_env()
        client.ping()
    except Exception as e:
        pytest.skip(f"Docker is not available: {e}")

    # Run server init and cleanup; will build image and start container
    initialize_server()
    try:
        # If initialize_server returns without raising, consider it success
        assert True
    finally:
        cleanup_server()


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
