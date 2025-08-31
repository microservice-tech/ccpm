#!/usr/bin/env python3
"""
Pull Request Management System for Claude Flow

This module provides comprehensive PR lifecycle management including:
- Automated PR creation from feature branches
- Dynamic PR template population
- Change detection and description generation  
- Review assignment and notification management

Components:
- manager: Core PR lifecycle management
- templates: PR template handling and population
- reviewer: Review assignment logic based on code ownership
"""

from .manager import PRManager, PRLifecycleError
from .templates import PRTemplateManager, PRTemplate
from .reviewer import PRReviewerManager, CodeOwnershipManager

__all__ = [
    'PRManager',
    'PRLifecycleError',
    'PRTemplateManager', 
    'PRTemplate',
    'PRReviewerManager',
    'CodeOwnershipManager'
]

__version__ = '1.0.0'
__author__ = 'Claude Flow'