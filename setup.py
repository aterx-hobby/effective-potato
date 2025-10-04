"""Setup configuration for effective-potato."""

from setuptools import setup, find_packages

setup(
    name="effective-potato",
    version="0.1.0",
    description="MCP server for sandboxed Ubuntu container execution",
    author="Gavin Barnard",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    python_requires=">=3.8",
    install_requires=[
        "mcp>=1.0.0",
        "docker>=7.0.0",
    ],
    extras_require={
        "dev": [
            "pytest>=8.0.0",
            "pytest-asyncio>=0.23.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "effective-potato=effective_potato.server:main",
        ],
    },
)
