"""Example of using the effective-potato container programmatically."""

import logging
from effective_potato.container import ContainerManager

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    """Example usage of ContainerManager."""
    # Create container manager
    manager = ContainerManager()

    try:
        # Build and start container
        logger.info("Building and starting container...")
        manager.build_image()
        manager.start_container()

        # Execute some commands
        logger.info("Executing commands...")

        # Example 1: Simple echo
        exit_code, output = manager.execute_command(
            "echo 'Hello from the container!'",
            "example1"
        )
        print(f"\nExample 1 - Echo command:")
        print(f"Exit code: {exit_code}")
        print(f"Output: {output}")

        # Example 2: List files
        exit_code, output = manager.execute_command(
            "ls -la /workspace",
            "example2"
        )
        print(f"\nExample 2 - List workspace:")
        print(f"Exit code: {exit_code}")
        print(f"Output:\n{output}")

        # Example 3: Python version
        exit_code, output = manager.execute_command(
            "python3 --version",
            "example3"
        )
        print(f"\nExample 3 - Python version:")
        print(f"Exit code: {exit_code}")
        print(f"Output: {output}")

        # Example 4: Create a file in workspace
        exit_code, output = manager.execute_command(
            "echo 'This is a test file' > /workspace/test.txt && cat /workspace/test.txt",
            "example4"
        )
        print(f"\nExample 4 - Create and read file:")
        print(f"Exit code: {exit_code}")
        print(f"Output: {output}")

    finally:
        # Clean up
        logger.info("Cleaning up...")
        manager.cleanup()


if __name__ == "__main__":
    main()
