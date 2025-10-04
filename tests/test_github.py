"""Tests for GitHub CLI integration."""

import pytest
from unittest.mock import MagicMock, patch
from effective_potato.github import GitHubManager
from effective_potato.container import ContainerManager


def test_github_manager_initialization_without_token():
    """Test GitHubManager initialization without a token."""
    mock_container = MagicMock(spec=ContainerManager)
    
    github_manager = GitHubManager(mock_container, github_token=None)
    
    assert github_manager.container_manager == mock_container
    assert github_manager.github_token is None
    assert github_manager.authenticated is False


def test_github_manager_initialization_with_token():
    """Test GitHubManager initialization with a token."""
    mock_container = MagicMock(spec=ContainerManager)
    mock_container.execute_command.return_value = (0, "Logged in as test-user")
    
    github_manager = GitHubManager(mock_container, github_token="test-token")
    
    assert github_manager.container_manager == mock_container
    assert github_manager.github_token == "test-token"
    assert github_manager.authenticated is True
    
    # Verify authentication command was called
    mock_container.execute_command.assert_called_once()
    call_args = mock_container.execute_command.call_args
    assert "gh auth login --with-token" in call_args[0][0]


def test_github_manager_authentication_failure():
    """Test GitHubManager handles authentication failure."""
    mock_container = MagicMock(spec=ContainerManager)
    mock_container.execute_command.return_value = (1, "Authentication failed")
    
    github_manager = GitHubManager(mock_container, github_token="invalid-token")
    
    assert github_manager.authenticated is False


def test_list_repos_not_authenticated():
    """Test list_repos when not authenticated."""
    mock_container = MagicMock(spec=ContainerManager)
    github_manager = GitHubManager(mock_container, github_token=None)
    
    exit_code, output = github_manager.list_repos()
    
    assert exit_code == 1
    assert "Not authenticated" in output


def test_list_repos_authenticated():
    """Test list_repos when authenticated."""
    mock_container = MagicMock(spec=ContainerManager)
    # First call for authentication, second for list_repos
    mock_container.execute_command.side_effect = [
        (0, "Logged in"),
        (0, "owner/repo1\nowner/repo2")
    ]
    
    github_manager = GitHubManager(mock_container, github_token="test-token")
    exit_code, output = github_manager.list_repos()
    
    assert exit_code == 0
    assert "owner/repo1" in output


def test_list_repos_with_owner():
    """Test list_repos with specific owner."""
    mock_container = MagicMock(spec=ContainerManager)
    mock_container.execute_command.side_effect = [
        (0, "Logged in"),
        (0, "octocat/hello-world")
    ]
    
    github_manager = GitHubManager(mock_container, github_token="test-token")
    exit_code, output = github_manager.list_repos(owner="octocat")
    
    assert exit_code == 0
    # Verify the command included the owner
    call_args = mock_container.execute_command.call_args_list[1]
    assert "gh repo list octocat" in call_args[0][0]


def test_list_repos_with_limit():
    """Test list_repos with custom limit."""
    mock_container = MagicMock(spec=ContainerManager)
    mock_container.execute_command.side_effect = [
        (0, "Logged in"),
        (0, "repos...")
    ]
    
    github_manager = GitHubManager(mock_container, github_token="test-token")
    exit_code, output = github_manager.list_repos(limit=50)
    
    assert exit_code == 0
    # Verify the command included the limit
    call_args = mock_container.execute_command.call_args_list[1]
    assert "--limit 50" in call_args[0][0]


def test_clone_repo_not_authenticated():
    """Test clone_repo when not authenticated."""
    mock_container = MagicMock(spec=ContainerManager)
    github_manager = GitHubManager(mock_container, github_token=None)
    
    exit_code, output = github_manager.clone_repo("owner/repo")
    
    assert exit_code == 1
    assert "Not authenticated" in output


def test_clone_repo_authenticated():
    """Test clone_repo when authenticated."""
    mock_container = MagicMock(spec=ContainerManager)
    mock_container.execute_command.side_effect = [
        (0, "Logged in"),
        (0, "Cloning into 'repo'...")
    ]
    
    github_manager = GitHubManager(mock_container, github_token="test-token")
    exit_code, output = github_manager.clone_repo("owner/repo")
    
    assert exit_code == 0
    assert "Cloning" in output
    
    # Verify the command was correct
    call_args = mock_container.execute_command.call_args_list[1]
    assert "cd /workspace" in call_args[0][0]
    assert "gh repo clone owner/repo" in call_args[0][0]


def test_clone_repo_with_destination():
    """Test clone_repo with custom destination."""
    mock_container = MagicMock(spec=ContainerManager)
    mock_container.execute_command.side_effect = [
        (0, "Logged in"),
        (0, "Cloning into 'custom-dir'...")
    ]
    
    github_manager = GitHubManager(mock_container, github_token="test-token")
    exit_code, output = github_manager.clone_repo("owner/repo", destination="custom-dir")
    
    assert exit_code == 0
    
    # Verify the command included the destination
    call_args = mock_container.execute_command.call_args_list[1]
    assert "gh repo clone owner/repo custom-dir" in call_args[0][0]
