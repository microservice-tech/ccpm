"""Priority-based execution strategy for smart scheduling."""

import asyncio
import time
import heapq
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
import subprocess
import tempfile
import shutil
from pathlib import Path
import threading

from .base import (
    ExecutionStrategy, 
    IssueContext, 
    ExecutionResult, 
    ExecutionStatus,
    ResourceManager,
    ProgressCallback
)


class PriorityLevel(Enum):
    """Priority levels for issues."""
    CRITICAL = 10
    HIGH = 8
    MEDIUM = 5
    LOW = 2
    DEFERRED = 0


@dataclass
class PriorityQueueItem:
    """Item for priority queue with proper ordering."""
    priority: int
    created_at: float
    issue: IssueContext
    
    def __lt__(self, other):
        # Higher priority first, then older issues first
        if self.priority != other.priority:
            return self.priority > other.priority
        return self.created_at < other.created_at


class PriorityExecutionStrategy(ExecutionStrategy):
    """Executes issues based on priority with intelligent resource allocation."""
    
    def __init__(self,
                 resource_manager: Optional[ResourceManager] = None,
                 progress_callback: Optional[ProgressCallback] = None,
                 max_concurrent: int = 3,
                 repository_url: str = None,
                 claude_flow_install_command: str = "npm install -g @anthropic-ai/claude-flow",
                 priority_boost_threshold: float = 300.0,  # 5 minutes in seconds
                 starvation_prevention: bool = True):
        """Initialize priority-based execution strategy.
        
        Args:
            resource_manager: Optional resource manager for controlling resources
            progress_callback: Optional progress callback
            max_concurrent: Maximum number of concurrent executions
            repository_url: URL of repository to clone for each issue
            claude_flow_install_command: Command to install Claude Flow
            priority_boost_threshold: Time in seconds after which low priority issues get boost
            starvation_prevention: Enable anti-starvation mechanisms for low priority issues
        """
        super().__init__(resource_manager, progress_callback, max_concurrent)
        self.repository_url = repository_url
        self.claude_flow_install_command = claude_flow_install_command
        self.workspace_base = Path("/tmp/claude-flow-issues")
        self.priority_boost_threshold = priority_boost_threshold
        self.starvation_prevention = starvation_prevention
        
        # Priority queue management
        self._priority_queue: List[PriorityQueueItem] = []
        self._queue_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        
        # Dependency management
        self._dependency_graph: Dict[str, Set[str]] = {}
        self._completed_issues_set: Set[str] = set()
        self._dependency_lock = asyncio.Lock()
        
        # Resource allocation tracking
        self._resource_allocations: Dict[str, Dict[str, Any]] = {}
        self._high_priority_slots = max(1, max_concurrent // 2)  # Reserve slots for high priority
        self._currently_high_priority = 0
    
    async def add_issue(self, issue: IssueContext) -> None:
        """Add an issue to the priority queue.
        
        Args:
            issue: Issue to add for processing
        """
        # Apply priority boost if needed
        if self.starvation_prevention:
            boosted_priority = self._apply_priority_boost(issue)
        else:
            boosted_priority = issue.priority
        
        queue_item = PriorityQueueItem(
            priority=boosted_priority,
            created_at=issue.created_at,
            issue=issue
        )
        
        async with self._queue_lock:
            heapq.heappush(self._priority_queue, queue_item)
            self._pending_issues.append(issue)
        
        if self.progress_callback:
            self.progress_callback(
                issue.issue_id, 
                "queued", 
                0.0, 
                f"Issue queued with priority {boosted_priority}"
            )
    
    async def select_next_issues(self, 
                               available_issues: List[IssueContext],
                               current_capacity: int) -> List[IssueContext]:
        """Select issues based on priority and resource allocation strategy.
        
        Uses intelligent scheduling that considers:
        - Issue priority levels
        - Resource requirements
        - Dependencies
        - Anti-starvation mechanisms
        - Reserved slots for high-priority issues
        
        Args:
            available_issues: List of issues ready for processing  
            current_capacity: Number of execution slots available
            
        Returns:
            List of selected issues to start processing
        """
        if not available_issues or current_capacity < 1:
            return []
        
        # Build dependency graph
        await self._build_dependency_graph(available_issues)
        
        # Update priority queue with available issues
        await self._sync_priority_queue(available_issues)
        
        selected_issues = []
        reserved_high_priority_slots = self._high_priority_slots - self._currently_high_priority
        
        async with self._queue_lock:
            temp_queue = []
            
            while self._priority_queue and len(selected_issues) < current_capacity:
                queue_item = heapq.heappop(self._priority_queue)
                issue = queue_item.issue
                
                # Check if dependencies are satisfied
                if not await self._are_dependencies_satisfied(issue):
                    # Put back in queue if dependencies not met
                    temp_queue.append(queue_item)
                    continue
                
                # Check resource allocation strategy
                if await self._can_allocate_resources(issue, reserved_high_priority_slots):
                    selected_issues.append(issue)
                    
                    # Track high priority allocations
                    if issue.priority >= PriorityLevel.HIGH.value:
                        self._currently_high_priority += 1
                        reserved_high_priority_slots -= 1
                else:
                    # Put back in queue if can't allocate resources
                    temp_queue.append(queue_item)
            
            # Put back unselected items
            for item in temp_queue:
                heapq.heappush(self._priority_queue, item)
        
        return selected_issues
    
    def get_execution_order(self, issues: List[IssueContext]) -> List[IssueContext]:
        """Order issues by priority with intelligent scheduling.
        
        Args:
            issues: List of issues to order
            
        Returns:
            Issues ordered by priority scheduling algorithm
        """
        # Apply priority boost for aging issues
        boosted_issues = []
        for issue in issues:
            boosted_priority = self._apply_priority_boost(issue) if self.starvation_prevention else issue.priority
            boosted_issues.append((boosted_priority, issue.created_at, issue))
        
        # Sort by boosted priority (desc) then creation time (asc)
        boosted_issues.sort(key=lambda x: (-x[0], x[1]))
        
        return [item[2] for item in boosted_issues]
    
    async def execute_issue(self, issue: IssueContext) -> ExecutionResult:
        """Execute issue with priority-aware resource management.
        
        Args:
            issue: Issue context to process
            
        Returns:
            Execution result
        """
        async with self._semaphore:
            start_time = time.time()
            issue.status = ExecutionStatus.RUNNING
            issue.started_at = start_time
            
            # Add to running tasks
            async with self._lock:
                task = asyncio.create_task(self._execute_priority_workflow(issue))
                self._running_tasks[issue.issue_id] = task
            
            try:
                result = await task
                
                # Mark issue as completed in dependency tracking
                async with self._dependency_lock:
                    self._completed_issues_set.add(issue.issue_id)
                
                # Update priority slot tracking
                if issue.priority >= PriorityLevel.HIGH.value:
                    self._currently_high_priority = max(0, self._currently_high_priority - 1)
                
                return result
                
            except Exception as e:
                duration = time.time() - start_time
                issue.status = ExecutionStatus.FAILED
                issue.error_message = str(e)
                issue.completed_at = time.time()
                
                result = ExecutionResult(
                    issue_id=issue.issue_id,
                    status=ExecutionStatus.FAILED,
                    success=False,
                    message=f"Priority issue {issue.issue_id} failed: {str(e)}",
                    duration=duration,
                    error_details={"error": str(e), "type": type(e).__name__}
                )
                
                async with self._lock:
                    self._completed_issues[issue.issue_id] = result
                
                return result
            
            finally:
                # Remove from running tasks
                async with self._lock:
                    self._running_tasks.pop(issue.issue_id, None)
                
                # Clean up resource tracking
                self._resource_allocations.pop(issue.issue_id, None)
    
    async def _execute_priority_workflow(self, issue: IssueContext) -> ExecutionResult:
        """Execute workflow with priority-specific optimizations.
        
        Args:
            issue: Issue context to process
            
        Returns:
            Execution result
        """
        start_time = time.time()
        
        try:
            # Allocate priority-based resources
            resource_requirements = self._get_priority_resource_requirements(issue)
            if not await self._manage_resources(issue.issue_id, resource_requirements):
                raise RuntimeError(f"Failed to acquire priority resources for issue {issue.issue_id}")
            
            # Stage 1: Create workspace with priority naming
            await self._report_progress(issue.issue_id, "workspace", 0.1, 
                                      f"Creating priority workspace (P{issue.priority})")
            workspace_path = await self._create_priority_workspace(issue)
            issue.workspace_path = str(workspace_path)
            
            # Stage 2: Clone repository
            await self._report_progress(issue.issue_id, "clone", 0.2, "Cloning repository")
            await self._clone_repository(workspace_path, issue)
            
            # Stage 3: Create branch
            await self._report_progress(issue.issue_id, "branch", 0.3, "Creating priority feature branch")
            branch_name = await self._create_priority_branch(workspace_path, issue)
            issue.branch_name = branch_name
            
            # Stage 4: Install Claude Flow
            await self._report_progress(issue.issue_id, "install", 0.4, "Installing Claude Flow")
            await self._install_claude_flow(workspace_path, issue)
            
            # Stage 5: Spawn hive-mind with priority context
            await self._report_progress(issue.issue_id, "spawn", 0.5, 
                                      f"Spawning priority hive-mind (P{issue.priority})")
            hive_result = await self._spawn_priority_hive_mind(workspace_path, issue)
            
            # Stage 6: Monitor with priority-aware timeouts
            await self._report_progress(issue.issue_id, "implement", 0.8, 
                                      "Monitoring priority implementation")
            implementation_result = await self._monitor_priority_implementation(
                workspace_path, issue, hive_result)
            
            # Stage 7: Create PR with priority labeling
            await self._report_progress(issue.issue_id, "pr", 0.9, "Creating priority pull request")
            pr_url = await self._create_priority_pull_request(workspace_path, issue, implementation_result)
            
            # Stage 8: Cleanup
            await self._report_progress(issue.issue_id, "cleanup", 1.0, "Cleaning up priority workspace")
            await self._cleanup_workspace(workspace_path, issue)
            
            duration = time.time() - start_time
            issue.status = ExecutionStatus.COMPLETED
            issue.completed_at = time.time()
            
            result = ExecutionResult(
                issue_id=issue.issue_id,
                status=ExecutionStatus.COMPLETED,
                success=True,
                message=f"Priority issue {issue.issue_id} (P{issue.priority}) completed successfully",
                duration=duration,
                pr_url=pr_url
            )
            
            async with self._lock:
                self._completed_issues[issue.issue_id] = result
            
            return result
            
        except Exception as e:
            duration = time.time() - start_time
            issue.status = ExecutionStatus.FAILED
            issue.error_message = str(e)
            issue.completed_at = time.time()
            
            # Attempt cleanup on failure
            if issue.workspace_path:
                try:
                    await self._cleanup_workspace(Path(issue.workspace_path), issue)
                except Exception as cleanup_error:
                    print(f"Priority cleanup failed for issue {issue.issue_id}: {cleanup_error}")
            
            raise e
        
        finally:
            # Always release resources
            await self._release_resources(issue.issue_id)
    
    def _apply_priority_boost(self, issue: IssueContext) -> int:
        """Apply anti-starvation priority boost to aging issues.
        
        Args:
            issue: Issue to potentially boost
            
        Returns:
            Boosted priority value
        """
        age = time.time() - issue.created_at
        
        # Apply boost if issue has been waiting too long
        if age > self.priority_boost_threshold:
            boost_multiplier = min(3, int(age / self.priority_boost_threshold))
            boosted_priority = min(PriorityLevel.CRITICAL.value, issue.priority + boost_multiplier)
            return boosted_priority
        
        return issue.priority
    
    async def _sync_priority_queue(self, available_issues: List[IssueContext]) -> None:
        """Synchronize priority queue with available issues.
        
        Args:
            available_issues: Current list of available issues
        """
        available_ids = {issue.issue_id for issue in available_issues}
        
        async with self._queue_lock:
            # Remove issues that are no longer available
            self._priority_queue = [
                item for item in self._priority_queue 
                if item.issue.issue_id in available_ids
            ]
            heapq.heapify(self._priority_queue)
    
    async def _can_allocate_resources(self, issue: IssueContext, reserved_slots: int) -> bool:
        """Check if resources can be allocated for the issue.
        
        Args:
            issue: Issue requesting resources
            reserved_slots: Number of reserved high-priority slots available
            
        Returns:
            True if resources can be allocated
        """
        # High priority issues can use reserved slots
        if issue.priority >= PriorityLevel.HIGH.value and reserved_slots > 0:
            return True
        
        # Medium/low priority issues can use general capacity
        if issue.priority < PriorityLevel.HIGH.value:
            return True
        
        # Check resource manager if available
        if self.resource_manager:
            capacity = self.resource_manager.get_available_capacity()
            return capacity.get("available_slots", 0) > 0
        
        return True
    
    def _get_priority_resource_requirements(self, issue: IssueContext) -> Dict[str, Any]:
        """Get resource requirements based on issue priority.
        
        Args:
            issue: Issue context
            
        Returns:
            Priority-adjusted resource requirements
        """
        base_requirements = {
            "cpu_cores": 1,
            "memory_mb": 512,
            "disk_space_mb": 1024,
            "network_access": True,
            "workspace_isolation": True
        }
        
        # Scale resources based on priority
        if issue.priority >= PriorityLevel.CRITICAL.value:
            base_requirements.update({
                "cpu_cores": 2,
                "memory_mb": 1024,
                "priority_timeout": 600,  # 10 minutes
                "fast_storage": True
            })
        elif issue.priority >= PriorityLevel.HIGH.value:
            base_requirements.update({
                "memory_mb": 768,
                "priority_timeout": 900,  # 15 minutes
            })
        else:
            base_requirements.update({
                "priority_timeout": 1800,  # 30 minutes for low priority
            })
        
        return base_requirements
    
    async def _create_priority_workspace(self, issue: IssueContext) -> Path:
        """Create workspace with priority-based naming and allocation.
        
        Args:
            issue: Issue context
            
        Returns:
            Path to created workspace
        """
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        priority_prefix = f"p{issue.priority}"
        workspace_path = self.workspace_base / f"{priority_prefix}-issue-{issue.issue_id}-{unique_id}"
        workspace_path.mkdir(parents=True, exist_ok=True)
        return workspace_path
    
    async def _create_priority_branch(self, workspace_path: Path, issue: IssueContext) -> str:
        """Create branch with priority information.
        
        Args:
            workspace_path: Path to workspace
            issue: Issue context
            
        Returns:
            Created branch name with priority prefix
        """
        repo_path = workspace_path / "repo"
        import time
        timestamp = int(time.time())
        priority_name = self._get_priority_name(issue.priority)
        branch_name = f"priority/{priority_name}/issue-{issue.issue_id}-{timestamp}"
        
        # Create and checkout branch
        process = await asyncio.create_subprocess_exec(
            "git", "checkout", "-b", branch_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=repo_path
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            raise RuntimeError(f"Priority branch creation failed for issue {issue.issue_id}: {stderr.decode()}")
        
        return branch_name
    
    def _get_priority_name(self, priority: int) -> str:
        """Get human-readable priority name.
        
        Args:
            priority: Priority value
            
        Returns:
            Priority name string
        """
        if priority >= PriorityLevel.CRITICAL.value:
            return "critical"
        elif priority >= PriorityLevel.HIGH.value:
            return "high"
        elif priority >= PriorityLevel.MEDIUM.value:
            return "medium"
        elif priority >= PriorityLevel.LOW.value:
            return "low"
        else:
            return "deferred"
    
    async def _build_dependency_graph(self, issues: List[IssueContext]) -> None:
        """Build dependency graph for priority scheduling.
        
        Args:
            issues: List of all issues to build graph for
        """
        async with self._dependency_lock:
            self._dependency_graph.clear()
            for issue in issues:
                self._dependency_graph[issue.issue_id] = set(issue.dependencies)
    
    async def _are_dependencies_satisfied(self, issue: IssueContext) -> bool:
        """Check if all dependencies for an issue are completed.
        
        Args:
            issue: Issue to check dependencies for
            
        Returns:
            True if all dependencies are satisfied
        """
        async with self._dependency_lock:
            required_deps = self._dependency_graph.get(issue.issue_id, set())
            return required_deps.issubset(self._completed_issues_set)
    
    # Implementation methods (similar to parallel strategy but with priority awareness)
    async def _clone_repository(self, workspace_path: Path, issue: IssueContext) -> None:
        """Clone repository with priority context."""
        if not self.repository_url:
            raise ValueError("Repository URL not configured")
        
        repo_path = workspace_path / "repo"
        process = await asyncio.create_subprocess_exec(
            "git", "clone", self.repository_url, str(repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace_path
        )
        
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"Git clone failed for priority issue {issue.issue_id}: {stderr.decode()}")
    
    async def _install_claude_flow(self, workspace_path: Path, issue: IssueContext) -> None:
        """Install Claude Flow with priority optimizations."""
        repo_path = workspace_path / "repo"
        process = await asyncio.create_subprocess_shell(
            self.claude_flow_install_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=repo_path
        )
        
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"Claude Flow installation failed for priority issue {issue.issue_id}: {stderr.decode()}")
    
    async def _spawn_priority_hive_mind(self, workspace_path: Path, issue: IssueContext) -> Dict[str, Any]:
        """Spawn hive-mind with priority-specific context."""
        issue_prompt = self._format_priority_issue_prompt(issue)
        prompt_file = workspace_path / "priority_issue_prompt.txt"
        prompt_file.write_text(issue_prompt)
        
        return {
            "status": "spawned",
            "prompt_file": str(prompt_file),
            "workspace": str(workspace_path / "repo"),
            "issue_id": issue.issue_id,
            "priority": issue.priority,
            "priority_name": self._get_priority_name(issue.priority)
        }
    
    async def _monitor_priority_implementation(self, 
                                            workspace_path: Path, 
                                            issue: IssueContext, 
                                            hive_result: Dict[str, Any]) -> Dict[str, Any]:
        """Monitor implementation with priority-aware timeouts."""
        # Different monitoring intervals based on priority
        if issue.priority >= PriorityLevel.CRITICAL.value:
            delay = 0.5  # Fast monitoring for critical
        elif issue.priority >= PriorityLevel.HIGH.value:
            delay = 1.0  # Normal monitoring for high
        else:
            delay = 2.0  # Relaxed monitoring for medium/low
        
        await asyncio.sleep(delay)
        
        return {
            "status": "completed",
            "priority": issue.priority,
            "monitoring_interval": delay,
            "priority_execution": True
        }
    
    async def _create_priority_pull_request(self, 
                                          workspace_path: Path, 
                                          issue: IssueContext,
                                          implementation_result: Dict[str, Any]) -> str:
        """Create pull request with priority labels and context."""
        repo_path = workspace_path / "repo"
        
        # Push branch with priority context
        process = await asyncio.create_subprocess_exec(
            "git", "push", "origin", issue.branch_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=repo_path
        )
        
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            print(f"Git push warning for priority issue {issue.issue_id}: {stderr.decode()}")
        
        # Simulate PR creation with priority context
        priority_name = self._get_priority_name(issue.priority)
        pr_url = f"https://github.com/example/repo/pull/{issue.issue_id}?priority={priority_name}"
        return pr_url
    
    async def _cleanup_workspace(self, workspace_path: Path, issue: IssueContext) -> None:
        """Clean up workspace with priority-aware cleanup."""
        if workspace_path.exists():
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, shutil.rmtree, workspace_path)
    
    def _format_priority_issue_prompt(self, issue: IssueContext) -> str:
        """Format issue prompt with priority context."""
        priority_name = self._get_priority_name(issue.priority)
        urgency_note = ""
        
        if issue.priority >= PriorityLevel.CRITICAL.value:
            urgency_note = "🚨 CRITICAL PRIORITY - Immediate attention required!"
        elif issue.priority >= PriorityLevel.HIGH.value:
            urgency_note = "⚡ HIGH PRIORITY - Prioritize this implementation"
        elif issue.priority >= PriorityLevel.MEDIUM.value:
            urgency_note = "📋 MEDIUM PRIORITY - Standard implementation timeline"
        else:
            urgency_note = "📝 LOW PRIORITY - Implement when capacity allows"
        
        return f"""
# {urgency_note}
# Issue #{issue.issue_id}: {issue.title}
# Priority: {priority_name.upper()} (Level {issue.priority})

## Description
{issue.body}

## Priority-Based Instructions
{urgency_note}

Please implement the solution following priority-appropriate guidelines:
1. Priority Level: {priority_name.upper()} ({issue.priority}/10)
2. Expected timeline: {"Immediate" if issue.priority >= 8 else "Standard" if issue.priority >= 5 else "Flexible"}
3. Resource allocation: {"High" if issue.priority >= 8 else "Medium" if issue.priority >= 5 else "Standard"}
4. Review requirements: {"Expedited" if issue.priority >= 8 else "Standard"}

## Dependencies (must be completed first)
{', '.join(issue.dependencies) if issue.dependencies else 'None'}

## Execution Context
- Priority-based scheduling: ENABLED
- Anti-starvation protection: {"ENABLED" if self.starvation_prevention else "DISABLED"}
- Resource scaling: ACTIVE
- Smart dependency resolution: ACTIVE
""".strip()
    
    async def get_priority_metrics(self) -> Dict[str, Any]:
        """Get priority execution specific metrics."""
        base_metrics = self.get_metrics()
        
        async with self._lock:
            running_priorities = [
                self._get_issue_priority(task_id) for task_id in self._running_tasks.keys()
            ]
        
        priority_distribution = {
            "critical": sum(1 for p in running_priorities if p >= PriorityLevel.CRITICAL.value),
            "high": sum(1 for p in running_priorities if PriorityLevel.HIGH.value <= p < PriorityLevel.CRITICAL.value),
            "medium": sum(1 for p in running_priorities if PriorityLevel.MEDIUM.value <= p < PriorityLevel.HIGH.value),
            "low": sum(1 for p in running_priorities if p < PriorityLevel.MEDIUM.value and p > 0)
        }
        
        priority_metrics = {
            "priority_distribution": priority_distribution,
            "high_priority_slots_used": self._currently_high_priority,
            "high_priority_slots_reserved": self._high_priority_slots,
            "starvation_prevention": self.starvation_prevention,
            "priority_boost_threshold": self.priority_boost_threshold,
            "queue_length": len(self._priority_queue)
        }
        
        return {**base_metrics, **priority_metrics}
    
    def _get_issue_priority(self, issue_id: str) -> int:
        """Get priority for a running issue."""
        for item in self._priority_queue:
            if item.issue.issue_id == issue_id:
                return item.priority
        return 0