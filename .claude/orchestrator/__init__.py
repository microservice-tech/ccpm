"""
Claude Flow Orchestrator Module

This module provides the core orchestration capabilities for managing
per-issue workflows in isolated environments. It coordinates workspace
creation, repository operations, Claude Flow installation, hive-mind
spawning, implementation monitoring, PR creation, and cleanup.

Key Components:
- Orchestrator: Main orchestration class
- WorkflowManager: Manages workflow lifecycle and stages
- IssueHandler: Handles issue-specific processing and coordination

The orchestrator ensures complete isolation between issues and enables
parallel processing without state contamination.
"""

from .orchestrator import Orchestrator
from .workflow_manager import WorkflowManager
from .issue_handler import IssueHandler

__all__ = [
    'Orchestrator',
    'WorkflowManager', 
    'IssueHandler'
]

__version__ = '1.0.0'