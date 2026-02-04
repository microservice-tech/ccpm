"""
Main Orchestrator Class

This module implements the central orchestrator that manages the complete 
per-issue workflow execution within isolated environments. The orchestrator
coordinates all stages of the workflow including workspace creation,
repository operations, Claude Flow installation, hive-mind spawning,
implementation monitoring, PR creation, and cleanup.

The orchestrator integrates with the Service Foundation and Workflow Template
to provide a complete end-to-end solution for automated issue processing.
"""

import asyncio
import logging
import os
import subprocess
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum

from .workflow_manager import WorkflowManager
from .issue_handler import IssueHandler


class WorkflowStatus(Enum):
    """Workflow execution status states"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class IssueContext:
    """Context information for issue processing"""
    issue_id: str
    title: str
    body: str
    repo_url: str
    branch_name: str
    workspace_path: str
    created_at: datetime
    status: WorkflowStatus = WorkflowStatus.PENDING


class Orchestrator:
    """
    Central orchestrator for managing per-issue workflows in isolated environments.
    
    This class coordinates the complete lifecycle of issue processing:
    1. Workspace creation and isolation
    2. Repository cloning and branch management
    3. Local Claude Flow installation
    4. Hive-mind spawning with issue context
    5. Implementation monitoring and feedback handling
    6. PR creation and cleanup
    
    The orchestrator ensures complete isolation between issues and supports
    parallel processing without state contamination.
    """
    
    def __init__(self, 
                 base_workspace_dir: str = "/tmp/claude-flow-issues",
                 workflow_template_path: str = None,
                 max_concurrent_issues: int = 3,
                 cleanup_on_completion: bool = True):
        """
        Initialize the orchestrator.
        
        Args:
            base_workspace_dir: Base directory for creating issue workspaces
            workflow_template_path: Path to workflow template script
            max_concurrent_issues: Maximum number of concurrent issues to process
            cleanup_on_completion: Whether to cleanup workspaces after completion
        """
        self.base_workspace_dir = Path(base_workspace_dir)
        self.workflow_template_path = workflow_template_path
        self.max_concurrent_issues = max_concurrent_issues
        self.cleanup_on_completion = cleanup_on_completion
        
        # Initialize components
        self.workflow_manager = WorkflowManager(self)
        self.issue_handler = IssueHandler(self)
        
        # Track active issues
        self.active_issues: Dict[str, IssueContext] = {}
        self.completed_issues: List[str] = []
        
        # Setup logging
        self.logger = logging.getLogger(__name__)
        self._setup_logging()
        
        # Initialize base workspace directory
        self._ensure_base_workspace()
    
    def _setup_logging(self):
        """Setup structured logging for orchestrator operations"""
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)
    
    def _ensure_base_workspace(self):
        """Ensure base workspace directory exists with proper permissions"""
        try:
            self.base_workspace_dir.mkdir(parents=True, exist_ok=True)
            # Set permissions for multi-user environments
            os.chmod(self.base_workspace_dir, 0o755)
            self.logger.info(f"Base workspace directory ensured: {self.base_workspace_dir}")
        except Exception as e:
            self.logger.error(f"Failed to create base workspace directory: {e}")
            raise
    
    async def process_issue(self, 
                          issue_id: str,
                          title: str,
                          body: str,
                          repo_url: str) -> IssueContext:
        """
        Process a single issue through the complete workflow.
        
        Args:
            issue_id: GitHub issue ID
            title: Issue title
            body: Issue description/body
            repo_url: Repository URL for cloning
            
        Returns:
            IssueContext: Context object with processing status and details
            
        Raises:
            RuntimeError: If issue processing fails
        """
        self.logger.info(f"Starting processing for issue #{issue_id}: {title}")
        
        # Check if we're at capacity
        if len(self.active_issues) >= self.max_concurrent_issues:
            raise RuntimeError(f"Maximum concurrent issues ({self.max_concurrent_issues}) reached")
        
        # Create issue context
        workspace_path = self.base_workspace_dir / f"issue-{issue_id}"
        branch_name = f"feature/issue-{issue_id}"
        
        context = IssueContext(
            issue_id=issue_id,
            title=title,
            body=body,
            repo_url=repo_url,
            branch_name=branch_name,
            workspace_path=str(workspace_path),
            created_at=datetime.now(),
            status=WorkflowStatus.PENDING
        )
        
        # Track the issue
        self.active_issues[issue_id] = context
        
        try:
            # Execute workflow through workflow manager
            await self.workflow_manager.execute_workflow(context)
            
            context.status = WorkflowStatus.COMPLETED
            self.completed_issues.append(issue_id)
            self.logger.info(f"Successfully completed processing for issue #{issue_id}")
            
        except Exception as e:
            context.status = WorkflowStatus.FAILED
            self.logger.error(f"Failed to process issue #{issue_id}: {e}")
            raise
        
        finally:
            # Clean up if requested
            if self.cleanup_on_completion and context.status == WorkflowStatus.COMPLETED:
                await self._cleanup_workspace(context)
            
            # Remove from active tracking
            self.active_issues.pop(issue_id, None)
        
        return context
    
    async def process_issues_batch(self, 
                                 issues: List[Dict[str, str]]) -> List[IssueContext]:
        """
        Process multiple issues concurrently with capacity management.
        
        Args:
            issues: List of issue dictionaries with keys: id, title, body, repo_url
            
        Returns:
            List[IssueContext]: Results for all processed issues
        """
        self.logger.info(f"Processing batch of {len(issues)} issues")
        
        # Create semaphore to limit concurrent processing
        semaphore = asyncio.Semaphore(self.max_concurrent_issues)
        
        async def process_with_semaphore(issue_data):
            async with semaphore:
                return await self.process_issue(
                    issue_id=issue_data['id'],
                    title=issue_data['title'],
                    body=issue_data['body'],
                    repo_url=issue_data['repo_url']
                )
        
        # Process all issues concurrently with limit
        tasks = [process_with_semaphore(issue) for issue in issues]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Handle any exceptions
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.logger.error(f"Failed to process issue {issues[i]['id']}: {result}")
                # Create failed context
                failed_context = IssueContext(
                    issue_id=issues[i]['id'],
                    title=issues[i]['title'],
                    body=issues[i]['body'],
                    repo_url=issues[i]['repo_url'],
                    branch_name=f"feature/issue-{issues[i]['id']}",
                    workspace_path=str(self.base_workspace_dir / f"issue-{issues[i]['id']}"),
                    created_at=datetime.now(),
                    status=WorkflowStatus.FAILED
                )
                processed_results.append(failed_context)
            else:
                processed_results.append(result)
        
        return processed_results
    
    async def _cleanup_workspace(self, context: IssueContext):
        """
        Clean up workspace after successful processing.
        
        Args:
            context: Issue context with workspace information
        """
        try:
            workspace_path = Path(context.workspace_path)
            if workspace_path.exists():
                shutil.rmtree(workspace_path)
                self.logger.info(f"Cleaned up workspace: {workspace_path}")
        except Exception as e:
            self.logger.warning(f"Failed to cleanup workspace {context.workspace_path}: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current orchestrator status and metrics.
        
        Returns:
            Dict containing status information
        """
        return {
            'active_issues': len(self.active_issues),
            'completed_issues': len(self.completed_issues),
            'max_concurrent': self.max_concurrent_issues,
            'base_workspace': str(self.base_workspace_dir),
            'active_issue_ids': list(self.active_issues.keys()),
            'completed_issue_ids': self.completed_issues[-10:],  # Last 10
            'timestamp': datetime.now().isoformat()
        }
    
    async def shutdown(self):
        """
        Gracefully shutdown the orchestrator.
        
        Cancels any in-progress workflows and performs cleanup.
        """
        self.logger.info("Shutting down orchestrator...")
        
        # Cancel any active workflows
        for issue_id, context in self.active_issues.items():
            context.status = WorkflowStatus.CANCELLED
            self.logger.info(f"Cancelled processing for issue #{issue_id}")
            
            # Cleanup workspace if it exists
            if self.cleanup_on_completion:
                await self._cleanup_workspace(context)
        
        # Clear tracking
        self.active_issues.clear()
        
        self.logger.info("Orchestrator shutdown complete")
    
    async def health_check(self) -> Dict[str, Any]:
        """
        Perform health check and return system status.
        
        Returns:
            Dict containing health status information
        """
        health_status = {
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'checks': {}
        }
        
        try:
            # Check base workspace accessibility
            if self.base_workspace_dir.exists() and self.base_workspace_dir.is_dir():
                health_status['checks']['workspace'] = 'ok'
            else:
                health_status['checks']['workspace'] = 'error'
                health_status['status'] = 'unhealthy'
            
            # Check workflow template if specified
            if self.workflow_template_path:
                template_path = Path(self.workflow_template_path)
                if template_path.exists() and template_path.is_file():
                    health_status['checks']['workflow_template'] = 'ok'
                else:
                    health_status['checks']['workflow_template'] = 'error'
                    health_status['status'] = 'degraded'
            
            # Check system resources (basic)
            try:
                # Check available disk space (at least 1GB)
                stat = os.statvfs(self.base_workspace_dir)
                available_bytes = stat.f_frsize * stat.f_bavail
                if available_bytes > 1024**3:  # 1GB
                    health_status['checks']['disk_space'] = 'ok'
                else:
                    health_status['checks']['disk_space'] = 'warning'
                    if health_status['status'] == 'healthy':
                        health_status['status'] = 'degraded'
            except Exception:
                health_status['checks']['disk_space'] = 'unknown'
            
            # Add metrics
            health_status['metrics'] = self.get_status()
            
        except Exception as e:
            health_status['status'] = 'unhealthy'
            health_status['error'] = str(e)
        
        return health_status