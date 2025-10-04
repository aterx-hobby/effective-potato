"""GitHub CLI integration for effective-potato."""

import logging
from typing import Optional
from .container import ContainerManager

logger = logging.getLogger(__name__)


class GitHubManager:
    """Manages GitHub CLI operations in the container."""

    def __init__(self, container_manager: ContainerManager, github_token: Optional[str] = None):
        """Initialize the GitHub manager.
        
        Args:
            container_manager: The container manager instance
            github_token: GitHub personal access token (optional)
        """
        self.container_manager = container_manager
        self.github_token = github_token
        self.authenticated = False
        
        if self.github_token:
            self._authenticate()
    
    def _authenticate(self) -> None:
        """Authenticate with GitHub CLI using the token."""
        if not self.github_token:
            logger.warning("No GitHub token provided, skipping authentication")
            return
        
        logger.info("Authenticating with GitHub CLI...")
        
        # Authenticate using the token
        import uuid
        task_id = str(uuid.uuid4())
        command = f"echo '{self.github_token}' | gh auth login --with-token"
        exit_code, output = self.container_manager.execute_command(command, task_id)
        
        if exit_code == 0:
            self.authenticated = True
            logger.info("Successfully authenticated with GitHub CLI")
        else:
            logger.error(f"Failed to authenticate with GitHub CLI: {output}")
            self.authenticated = False
    
    def list_repos(self, owner: Optional[str] = None, limit: int = 30) -> tuple[int, str]:
        """List GitHub repositories.
        
        Args:
            owner: GitHub username or organization to list repos for.
                   If None, lists repos for the authenticated user.
            limit: Maximum number of repositories to list (default: 30)
        
        Returns:
            Tuple of (exit_code, output)
        """
        if not self.authenticated:
            return 1, "Error: Not authenticated with GitHub. GITHUB_PERSONAL_ACCESS_TOKEN not set or invalid."
        
        import uuid
        task_id = str(uuid.uuid4())
        
        # Build the gh repo list command
        if owner:
            command = f"gh repo list {owner} --limit {limit}"
        else:
            command = f"gh repo list --limit {limit}"
        
        logger.info(f"Listing repositories: {command}")
        exit_code, output = self.container_manager.execute_command(command, task_id)
        
        return exit_code, output
    
    def clone_repo(self, repo: str, destination: Optional[str] = None) -> tuple[int, str]:
        """Clone a GitHub repository into the workspace.
        
        Args:
            repo: Repository to clone in format 'owner/repo'
            destination: Optional destination directory name. If not provided,
                        uses the repository name.
        
        Returns:
            Tuple of (exit_code, output)
        """
        if not self.authenticated:
            return 1, "Error: Not authenticated with GitHub. GITHUB_PERSONAL_ACCESS_TOKEN not set or invalid."
        
        import uuid
        task_id = str(uuid.uuid4())
        
        # Build the gh repo clone command
        # Clone into /workspace
        if destination:
            command = f"cd /workspace && gh repo clone {repo} {destination}"
        else:
            command = f"cd /workspace && gh repo clone {repo}"
        
        logger.info(f"Cloning repository: {repo}")
        exit_code, output = self.container_manager.execute_command(command, task_id)
        
        return exit_code, output
