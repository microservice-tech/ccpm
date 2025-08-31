"""
Claude Flow Services Package

This package contains all the core services for the Claude Flow automation system:
- GitHubClient: Handles GitHub API interactions
- IssueProcessor: Manages concurrent issue processing
- WorkflowExecutor: Executes workflow scripts
- ClaudeFlowService: Main orchestration service
"""

try:
    from .github_client import GitHubClient, GitHubRateLimitError, GitHubAuthenticationError
    from .issue_processor import IssueProcessor, IssueStatus, IssueProcessingState
    from .workflow_executor import WorkflowExecutor, WorkflowExecution
    from .claude_flow_service import ClaudeFlowService, ServiceConfiguration
except ImportError:
    from github_client import GitHubClient, GitHubRateLimitError, GitHubAuthenticationError
    from issue_processor import IssueProcessor, IssueStatus, IssueProcessingState
    from workflow_executor import WorkflowExecutor, WorkflowExecution
    from claude_flow_service import ClaudeFlowService, ServiceConfiguration

__all__ = [
    'GitHubClient',
    'GitHubRateLimitError', 
    'GitHubAuthenticationError',
    'IssueProcessor',
    'IssueStatus',
    'IssueProcessingState',
    'WorkflowExecutor',
    'WorkflowExecution',
    'ClaudeFlowService',
    'ServiceConfiguration'
]

__version__ = '1.0.0'