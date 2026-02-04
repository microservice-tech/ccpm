"""Base execution strategy interface for orchestrator."""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Protocol
from dataclasses import dataclass
from enum import Enum
import asyncio
from concurrent.futures import Future


class ExecutionStatus(Enum):
    """Status of issue execution."""
    PENDING = "pending"
    RUNNING = "running" 
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class IssueContext:
    """Context information for an issue being processed."""
    issue_id: str
    title: str
    body: str
    priority: int = 0
    dependencies: List[str] = None
    workspace_path: str = None
    branch_name: str = None
    status: ExecutionStatus = ExecutionStatus.PENDING
    created_at: float = None
    started_at: float = None
    completed_at: float = None
    error_message: str = None
    retry_count: int = 0
    max_retries: int = 3

    def __post_init__(self):
        if self.dependencies is None:
            self.dependencies = []
        if self.created_at is None:
            import time
            self.created_at = time.time()


@dataclass 
class ExecutionResult:
    """Result of issue execution."""
    issue_id: str
    status: ExecutionStatus
    success: bool
    message: str
    duration: float = None
    pr_url: str = None
    error_details: Dict[str, Any] = None

    def __post_init__(self):
        if self.error_details is None:
            self.error_details = {}


class ProgressCallback(Protocol):
    """Protocol for progress reporting callbacks."""
    
    def __call__(self, issue_id: str, stage: str, progress: float, message: str = None) -> None:
        """Report progress for an issue.
        
        Args:
            issue_id: ID of the issue being processed
            stage: Current processing stage
            progress: Progress percentage (0.0 to 1.0)
            message: Optional progress message
        """
        ...


class ResourceManager(ABC):
    """Abstract resource manager for controlling execution resources."""
    
    @abstractmethod
    async def acquire_resources(self, issue_id: str, requirements: Dict[str, Any]) -> bool:
        """Acquire resources for issue processing.
        
        Args:
            issue_id: ID of the issue requesting resources
            requirements: Resource requirements specification
            
        Returns:
            True if resources were acquired, False otherwise
        """
        pass
    
    @abstractmethod
    async def release_resources(self, issue_id: str) -> None:
        """Release resources for completed issue.
        
        Args:
            issue_id: ID of the issue releasing resources
        """
        pass
    
    @abstractmethod
    def get_available_capacity(self) -> Dict[str, Any]:
        """Get current available resource capacity.
        
        Returns:
            Dictionary describing available resources
        """
        pass


class ExecutionStrategy(ABC):
    """Abstract base class for execution strategies."""
    
    def __init__(self, 
                 resource_manager: Optional[ResourceManager] = None,
                 progress_callback: Optional[ProgressCallback] = None,
                 max_concurrent: int = 5):
        """Initialize execution strategy.
        
        Args:
            resource_manager: Optional resource manager for controlling resources
            progress_callback: Optional callback for progress reporting
            max_concurrent: Maximum concurrent executions allowed
        """
        self.resource_manager = resource_manager
        self.progress_callback = progress_callback
        self.max_concurrent = max_concurrent
        self._running_tasks: Dict[str, Future] = {}
        self._pending_issues: List[IssueContext] = []
        self._completed_issues: Dict[str, ExecutionResult] = {}
        self._lock = asyncio.Lock()
    
    @abstractmethod
    async def select_next_issues(self, 
                               available_issues: List[IssueContext],
                               current_capacity: int) -> List[IssueContext]:
        """Select next issues to process based on strategy logic.
        
        Args:
            available_issues: List of issues ready for processing
            current_capacity: Number of execution slots available
            
        Returns:
            List of selected issues to start processing
        """
        pass
    
    @abstractmethod
    async def execute_issue(self, issue: IssueContext) -> ExecutionResult:
        """Execute a single issue workflow.
        
        Args:
            issue: Issue context to process
            
        Returns:
            Execution result
        """
        pass
    
    @abstractmethod
    def get_execution_order(self, issues: List[IssueContext]) -> List[IssueContext]:
        """Determine execution order for issues based on strategy.
        
        Args:
            issues: List of issues to order
            
        Returns:
            Ordered list of issues
        """
        pass
    
    async def add_issue(self, issue: IssueContext) -> None:
        """Add an issue to the execution queue.
        
        Args:
            issue: Issue to add for processing
        """
        async with self._lock:
            self._pending_issues.append(issue)
            if self.progress_callback:
                self.progress_callback(issue.issue_id, "queued", 0.0, "Issue added to queue")
    
    async def remove_issue(self, issue_id: str) -> bool:
        """Remove an issue from execution queue or cancel running execution.
        
        Args:
            issue_id: ID of issue to remove/cancel
            
        Returns:
            True if issue was removed/cancelled, False if not found
        """
        async with self._lock:
            # Remove from pending
            for i, issue in enumerate(self._pending_issues):
                if issue.issue_id == issue_id:
                    issue.status = ExecutionStatus.CANCELLED
                    self._pending_issues.pop(i)
                    return True
            
            # Cancel running task
            if issue_id in self._running_tasks:
                task = self._running_tasks[issue_id]
                task.cancel()
                return True
        
        return False
    
    async def get_status(self, issue_id: str) -> Optional[ExecutionStatus]:
        """Get current status of an issue.
        
        Args:
            issue_id: ID of issue to check
            
        Returns:
            Current execution status or None if not found
        """
        # Check completed
        if issue_id in self._completed_issues:
            return self._completed_issues[issue_id].status
        
        # Check running
        if issue_id in self._running_tasks:
            return ExecutionStatus.RUNNING
        
        # Check pending
        for issue in self._pending_issues:
            if issue.issue_id == issue_id:
                return issue.status
        
        return None
    
    async def get_results(self) -> Dict[str, ExecutionResult]:
        """Get all completed execution results.
        
        Returns:
            Dictionary mapping issue_id to execution results
        """
        return self._completed_issues.copy()
    
    async def wait_for_completion(self, timeout: Optional[float] = None) -> Dict[str, ExecutionResult]:
        """Wait for all queued issues to complete.
        
        Args:
            timeout: Optional timeout in seconds
            
        Returns:
            Dictionary of all execution results
        """
        if timeout:
            await asyncio.wait_for(self._wait_all(), timeout=timeout)
        else:
            await self._wait_all()
        
        return await self.get_results()
    
    async def _wait_all(self) -> None:
        """Wait for all pending and running tasks to complete."""
        while self._pending_issues or self._running_tasks:
            if self._running_tasks:
                await asyncio.sleep(0.1)
            else:
                break
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get execution metrics and statistics.
        
        Returns:
            Dictionary containing execution metrics
        """
        total_issues = len(self._completed_issues) + len(self._running_tasks) + len(self._pending_issues)
        completed = len([r for r in self._completed_issues.values() if r.success])
        failed = len([r for r in self._completed_issues.values() if not r.success])
        
        return {
            "total_issues": total_issues,
            "completed": completed,
            "failed": failed,
            "running": len(self._running_tasks),
            "pending": len(self._pending_issues),
            "success_rate": completed / len(self._completed_issues) if self._completed_issues else 0.0
        }
    
    async def _report_progress(self, issue_id: str, stage: str, progress: float, message: str = None):
        """Helper method to report progress if callback is configured."""
        if self.progress_callback:
            self.progress_callback(issue_id, stage, progress, message)
    
    async def _manage_resources(self, issue_id: str, requirements: Dict[str, Any]) -> bool:
        """Helper method to acquire resources if manager is configured."""
        if self.resource_manager:
            return await self.resource_manager.acquire_resources(issue_id, requirements)
        return True
    
    async def _release_resources(self, issue_id: str):
        """Helper method to release resources if manager is configured."""
        if self.resource_manager:
            await self.resource_manager.release_resources(issue_id)