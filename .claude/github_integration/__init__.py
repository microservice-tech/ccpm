#!/usr/bin/env python3
"""
GitHub Integration Module for Claude Flow

This module extends the existing PM scripts with GitHub integration capabilities,
providing autonomous PR creation, branch management, and workflow automation.
"""

from .github_service import GitHubService
from .cli.wrapper import GitHubCLIWrapper
from .cli.commands import GitHubPMCommands

__version__ = "1.0.0"
__author__ = "Claude Flow Team"

__all__ = [
    "GitHubService",
    "GitHubCLIWrapper", 
    "GitHubPMCommands"
]

def get_version():
    """Get the GitHub integration module version."""
    return __version__