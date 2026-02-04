#!/usr/bin/env python3
"""
Branch Management Module for Claude Flow GitHub Integration

This module provides comprehensive branch workflow automation including:
- Feature branch creation with naming conventions
- Branch lifecycle management
- Automated cleanup of merged/stale branches
- Conflict detection and resolution guidance
- Sync with upstream branches
"""

from .manager import BranchManager, BranchOperationResult, BranchConflictInfo
from .naming import (
    BranchNamingConfig, 
    BranchNamingValidator,
    BranchType,
    NamingValidationResult,
    generate_branch_name,
    validate_branch_name,
    suggest_branch_name
)
from .cleanup import (
    BranchCleanupManager,
    CleanupPolicy,
    CleanupResult,
    BranchCleanupStats
)

__all__ = [
    # Manager
    "BranchManager",
    "BranchOperationResult", 
    "BranchConflictInfo",
    
    # Naming
    "BranchNamingConfig",
    "BranchNamingValidator",
    "BranchType",
    "NamingValidationResult",
    "generate_branch_name",
    "validate_branch_name",
    "suggest_branch_name",
    
    # Cleanup
    "BranchCleanupManager",
    "CleanupPolicy",
    "CleanupResult",
    "BranchCleanupStats"
]

__version__ = "1.0.0"