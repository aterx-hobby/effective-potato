"""MCP server for effective-potato."""

import logging
import uuid
from typing import Any, Optional
from mcp.server import Server
from mcp.types import Tool, TextContent
from pydantic import AnyUrl

from .container import ContainerManager
from .github import GitHubManager

logger = logging.getLogger(__name__)


# Initialize the MCP server
app = Server("effective-potato")

# Container manager instance
container_manager: ContainerManager | None = None
# GitHub manager instance
github_manager: Optional[GitHubManager] = None


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    tools = [
        Tool(
            name="execute_command",
            description="Execute a command in the sandboxed Ubuntu container",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute in the container",
                    }
                },
                "required": ["command"],
            },
        ),
    ]
    
    # Add GitHub tools if authenticated
    if github_manager and github_manager.authenticated:
        tools.extend([
            Tool(
                name="github_list_repos",
                description="List GitHub repositories for a user or the authenticated user",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "owner": {
                            "type": "string",
                            "description": "GitHub username or organization (optional, defaults to authenticated user)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of repositories to list (default: 30)",
                            "default": 30,
                        }
                    },
                },
            ),
            Tool(
                name="github_clone_repo",
                description="Clone a GitHub repository into the workspace",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Repository to clone in format 'owner/repo'",
                        },
                        "destination": {
                            "type": "string",
                            "description": "Optional destination directory name",
                        }
                    },
                    "required": ["repo"],
                },
            ),
        ])
    
    return tools


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls."""
    if not container_manager:
        raise RuntimeError("Container manager not initialized")
    
    if name == "execute_command":
        command = arguments.get("command")
        if not command:
            raise ValueError("Command is required")

        # Generate unique task ID
        task_id = str(uuid.uuid4())

        # Execute the command
        exit_code, output = container_manager.execute_command(command, task_id)

        # Format response
        response = f"Exit code: {exit_code}\n\nOutput:\n{output}"

        return [TextContent(type="text", text=response)]
    
    elif name == "github_list_repos":
        if not github_manager or not github_manager.authenticated:
            raise RuntimeError("GitHub manager not initialized or not authenticated")
        
        owner = arguments.get("owner")
        limit = arguments.get("limit", 30)
        
        exit_code, output = github_manager.list_repos(owner=owner, limit=limit)
        
        response = f"Exit code: {exit_code}\n\nOutput:\n{output}"
        return [TextContent(type="text", text=response)]
    
    elif name == "github_clone_repo":
        if not github_manager or not github_manager.authenticated:
            raise RuntimeError("GitHub manager not initialized or not authenticated")
        
        repo = arguments.get("repo")
        if not repo:
            raise ValueError("Repository is required")
        
        destination = arguments.get("destination")
        
        exit_code, output = github_manager.clone_repo(repo=repo, destination=destination)
        
        response = f"Exit code: {exit_code}\n\nOutput:\n{output}"
        return [TextContent(type="text", text=response)]
    
    else:
        raise ValueError(f"Unknown tool: {name}")


def initialize_server() -> None:
    """Initialize the server and container."""
    global container_manager, github_manager

    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger.info("Initializing effective-potato MCP server...")

    # Create container manager
    container_manager = ContainerManager()

    # Build and start container
    container_manager.build_image()
    container_manager.start_container()

    # Initialize GitHub manager if token is available
    github_token = container_manager.env_vars.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if github_token:
        logger.info("GitHub token found, initializing GitHub manager...")
        github_manager = GitHubManager(container_manager, github_token)
    else:
        logger.info("No GitHub token found, GitHub tools will not be available")
        github_manager = None

    logger.info("Server initialized successfully")


def cleanup_server() -> None:
    """Clean up server resources."""
    global container_manager

    if container_manager:
        logger.info("Cleaning up server...")
        container_manager.cleanup()
        container_manager = None


def main() -> None:
    """Main entry point for the server."""
    import asyncio
    from mcp.server.stdio import stdio_server

    try:
        initialize_server()

        # Run the server
        async def run():
            async with stdio_server() as (read_stream, write_stream):
                await app.run(
                    read_stream,
                    write_stream,
                    app.create_initialization_options(),
                )

        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    finally:
        cleanup_server()


if __name__ == "__main__":
    main()
