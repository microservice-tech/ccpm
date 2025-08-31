"""Parallel execution strategy for concurrent processing."""

import asyncio
import time
from typing import List, Dict, Any, Optional, Set
from concurrent.futures import ThreadPoolExecutor, as_completed
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


class ParallelExecutionStrategy(ExecutionStrategy):
    """Executes multiple issues concurrently with resource management."""
    
    def __init__(self,
                 resource_manager: Optional[ResourceManager] = None,
                 progress_callback: Optional[ProgressCallback] = None,
                 max_concurrent: int = 5,
                 repository_url: str = None,
                 claude_flow_install_command: str = "npm install -g @anthropic-ai/claude-flow"):
        """Initialize parallel execution strategy.
        
        Args:
            resource_manager: Optional resource manager for controlling resources
            progress_callback: Optional progress callback
            max_concurrent: Maximum number of concurrent executions
            repository_url: URL of repository to clone for each issue
            claude_flow_install_command: Command to install Claude Flow
        """
        super().__init__(resource_manager, progress_callback, max_concurrent)
        self.repository_url = repository_url
        self.claude_flow_install_command = claude_flow_install_command
        self.workspace_base = Path("/tmp/claude-flow-issues")
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._dependency_graph: Dict[str, Set[str]] = {}
        self._completed_issues_set: Set[str] = set()
        self._dependency_lock = asyncio.Lock()
    
    async def select_next_issues(self, 
                               available_issues: List[IssueContext],
                               current_capacity: int) -> List[IssueContext]:
        """Select multiple issues to process in parallel.
        
        Considers dependencies and resource constraints when selecting issues.
        
        Args:
            available_issues: List of issues ready for processing
            current_capacity: Number of execution slots available
            
        Returns:
            List of issues to start processing in parallel
        """
        if not available_issues or current_capacity < 1:
            return []
        
        # Build dependency graph
        await self._build_dependency_graph(available_issues)
        
        # Find issues with no pending dependencies
        ready_issues = []
        for issue in available_issues:
            if await self._are_dependencies_satisfied(issue):
                ready_issues.append(issue)
        
        # Sort by priority and select up to capacity
        prioritized_issues = self.get_execution_order(ready_issues)
        return prioritized_issues[:min(current_capacity, len(prioritized_issues))]
    
    def get_execution_order(self, issues: List[IssueContext]) -> List[IssueContext]:
        """Order issues by priority (higher priority first), then by creation time.
        
        Args:
            issues: List of issues to order
            
        Returns:
            Issues ordered by priority (descending) then creation time (ascending)
        """
        return sorted(issues, key=lambda x: (-x.priority, x.created_at))
    
    async def execute_issue(self, issue: IssueContext) -> ExecutionResult:
        """Execute a single issue workflow with proper concurrency control.
        
        Uses semaphore to limit concurrent executions and ensures workspace isolation.
        
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
                # Create a task for this execution
                task = asyncio.create_task(self._execute_issue_workflow(issue))
                self._running_tasks[issue.issue_id] = task
            
            try:
                result = await task
                
                # Mark issue as completed in dependency tracking
                async with self._dependency_lock:
                    self._completed_issues_set.add(issue.issue_id)
                
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
                    message=f"Issue {issue.issue_id} failed: {str(e)}",
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
    
    async def _execute_issue_workflow(self, issue: IssueContext) -> ExecutionResult:
        """Execute the complete issue workflow.
        
        Args:
            issue: Issue context to process
            
        Returns:
            Execution result
        """
        start_time = time.time()
        
        try:
            # Stage 1: Create workspace
            await self._report_progress(issue.issue_id, "workspace", 0.1, "Creating isolated workspace")
            workspace_path = await self._create_workspace(issue)
            issue.workspace_path = str(workspace_path)
            
            # Stage 2: Acquire resources if manager is configured
            resource_requirements = self._get_resource_requirements(issue)
            if not await self._manage_resources(issue.issue_id, resource_requirements):
                raise RuntimeError(f"Failed to acquire required resources for issue {issue.issue_id}")
            
            # Stage 3: Clone repository
            await self._report_progress(issue.issue_id, "clone", 0.2, "Cloning repository")
            await self._clone_repository(workspace_path, issue)
            
            # Stage 4: Create branch
            await self._report_progress(issue.issue_id, "branch", 0.3, "Creating feature branch")
            branch_name = await self._create_branch(workspace_path, issue)
            issue.branch_name = branch_name
            
            # Stage 5: Install Claude Flow
            await self._report_progress(issue.issue_id, "install", 0.4, "Installing Claude Flow")
            await self._install_claude_flow(workspace_path, issue)
            
            # Stage 6: Spawn hive-mind
            await self._report_progress(issue.issue_id, "spawn", 0.5, "Spawning hive-mind with issue context")
            hive_result = await self._spawn_hive_mind(workspace_path, issue)
            
            # Stage 7: Monitor implementation
            await self._report_progress(issue.issue_id, "implement", 0.8, "Monitoring implementation progress")
            implementation_result = await self._monitor_implementation(workspace_path, issue, hive_result)
            
            # Stage 8: Create PR
            await self._report_progress(issue.issue_id, "pr", 0.9, "Creating pull request")
            pr_url = await self._create_pull_request(workspace_path, issue, implementation_result)
            
            # Stage 9: Cleanup
            await self._report_progress(issue.issue_id, "cleanup", 1.0, "Cleaning up workspace")
            await self._cleanup_workspace(workspace_path, issue)
            
            duration = time.time() - start_time
            issue.status = ExecutionStatus.COMPLETED
            issue.completed_at = time.time()
            
            result = ExecutionResult(
                issue_id=issue.issue_id,
                status=ExecutionStatus.COMPLETED,
                success=True,
                message=f"Issue {issue.issue_id} completed successfully in parallel",
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
                    print(f"Cleanup failed for issue {issue.issue_id}: {cleanup_error}")
            
            raise e
        
        finally:
            # Always release resources
            await self._release_resources(issue.issue_id)
    
    async def _build_dependency_graph(self, issues: List[IssueContext]) -> None:
        """Build dependency graph for parallel execution planning.
        
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
            True if all dependencies are satisfied, False otherwise
        """
        async with self._dependency_lock:
            required_deps = self._dependency_graph.get(issue.issue_id, set())
            return required_deps.issubset(self._completed_issues_set)
    
    def _get_resource_requirements(self, issue: IssueContext) -> Dict[str, Any]:
        """Get resource requirements for an issue.
        
        Args:
            issue: Issue context
            
        Returns:
            Dictionary describing resource requirements
        """
        return {
            "cpu_cores": 1,
            "memory_mb": 512,
            "disk_space_mb": 1024,
            "network_access": True,
            "workspace_isolation": True
        }
    
    async def _create_workspace(self, issue: IssueContext) -> Path:
        """Create isolated workspace for the issue with unique naming.
        
        Args:
            issue: Issue context
            
        Returns:
            Path to created workspace
        """
        # Use thread-safe unique workspace path
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        workspace_path = self.workspace_base / f"issue-{issue.issue_id}-{unique_id}"
        workspace_path.mkdir(parents=True, exist_ok=True)
        return workspace_path
    
    async def _clone_repository(self, workspace_path: Path, issue: IssueContext) -> None:
        """Clone repository into workspace.
        
        Args:
            workspace_path: Path to workspace
            issue: Issue context
        """
        if not self.repository_url:
            raise ValueError("Repository URL not configured")
        
        repo_path = workspace_path / "repo"
        
        # Clone repository
        process = await asyncio.create_subprocess_exec(
            "git", "clone", self.repository_url, str(repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace_path
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            raise RuntimeError(f"Git clone failed for issue {issue.issue_id}: {stderr.decode()}")
    
    async def _create_branch(self, workspace_path: Path, issue: IssueContext) -> str:
        """Create feature branch for the issue.
        
        Args:
            workspace_path: Path to workspace
            issue: Issue context
            
        Returns:
            Created branch name
        """
        repo_path = workspace_path / "repo"
        # Include timestamp to ensure uniqueness across parallel executions
        import time
        timestamp = int(time.time())
        branch_name = f"feature/issue-{issue.issue_id}-{timestamp}"
        
        # Create and checkout branch
        process = await asyncio.create_subprocess_exec(
            "git", "checkout", "-b", branch_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=repo_path
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            raise RuntimeError(f"Branch creation failed for issue {issue.issue_id}: {stderr.decode()}")
        
        return branch_name
    
    async def _install_claude_flow(self, workspace_path: Path, issue: IssueContext) -> None:
        """Install Claude Flow in the workspace.
        
        Args:
            workspace_path: Path to workspace
            issue: Issue context
        """
        repo_path = workspace_path / "repo"
        
        # Install Claude Flow locally
        process = await asyncio.create_subprocess_shell(
            self.claude_flow_install_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=repo_path
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            raise RuntimeError(f"Claude Flow installation failed for issue {issue.issue_id}: {stderr.decode()}")
    
    async def _spawn_hive_mind(self, workspace_path: Path, issue: IssueContext) -> Dict[str, Any]:
        """Spawn hive-mind with formatted issue prompt.
        
        Args:
            workspace_path: Path to workspace
            issue: Issue context
            
        Returns:
            Hive-mind spawn result
        """
        repo_path = workspace_path / "repo"
        
        # Format issue prompt
        issue_prompt = self._format_issue_prompt(issue)
        
        # Create prompt file
        prompt_file = workspace_path / "issue_prompt.txt"
        prompt_file.write_text(issue_prompt)
        
        # Spawn hive-mind (simplified - would use actual Claude Flow commands)
        return {
            "status": "spawned",
            "prompt_file": str(prompt_file),
            "workspace": str(repo_path),
            "issue_id": issue.issue_id,
            "parallel_execution": True
        }
    
    async def _monitor_implementation(self, 
                                    workspace_path: Path, 
                                    issue: IssueContext, 
                                    hive_result: Dict[str, Any]) -> Dict[str, Any]:
        """Monitor implementation progress with parallel-aware tracking.
        
        Args:
            workspace_path: Path to workspace
            issue: Issue context
            hive_result: Result from hive-mind spawn
            
        Returns:
            Implementation monitoring result
        """
        # Simplified monitoring with random delay to simulate varying execution times
        import random
        delay = random.uniform(0.5, 2.0)  # Simulate variable implementation times
        await asyncio.sleep(delay)
        
        return {
            "status": "completed",
            "files_changed": [],
            "commits_made": 0,
            "tests_passed": True,
            "parallel_execution": True
        }
    
    async def _create_pull_request(self, 
                                 workspace_path: Path, 
                                 issue: IssueContext,
                                 implementation_result: Dict[str, Any]) -> str:
        """Create pull request for the implemented changes.
        
        Args:
            workspace_path: Path to workspace
            issue: Issue context
            implementation_result: Result from implementation
            
        Returns:
            PR URL
        """
        repo_path = workspace_path / "repo"
        
        # Push branch (with unique branch name to avoid conflicts)
        process = await asyncio.create_subprocess_exec(
            "git", "push", "origin", issue.branch_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=repo_path
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            print(f"Git push warning for issue {issue.issue_id}: {stderr.decode()}")
        
        # Simulate PR creation
        pr_url = f"https://github.com/example/repo/pull/{issue.issue_id}"
        return pr_url
    
    async def _cleanup_workspace(self, workspace_path: Path, issue: IssueContext) -> None:
        """Clean up workspace after completion.
        
        Args:
            workspace_path: Path to workspace to clean
            issue: Issue context
        """
        if workspace_path.exists():
            # Use thread pool for potentially blocking I/O
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, shutil.rmtree, workspace_path)
    
    def _format_issue_prompt(self, issue: IssueContext) -> str:
        """Format issue information into hive-mind prompt for parallel execution.
        
        Args:
            issue: Issue context
            
        Returns:
            Formatted prompt string
        """
        return f"""
# Issue #{issue.issue_id}: {issue.title}

## Description
{issue.body}

## Instructions for Parallel Execution
Please implement the solution for this issue following the project guidelines:
1. Analyze the requirements carefully
2. Make minimal, focused changes
3. Ensure all tests pass
4. Follow existing code patterns
5. Create comprehensive commit messages
6. Be aware this is running in parallel with other issues

## Dependencies (must be completed first)
{', '.join(issue.dependencies) if issue.dependencies else 'None'}

## Priority Level
{issue.priority}

## Execution Context
- Workspace isolation: ENABLED
- Parallel execution: ENABLED
- Resource constraints: MANAGED
""".strip()
    
    async def get_parallel_metrics(self) -> Dict[str, Any]:
        """Get parallel execution specific metrics.
        
        Returns:
            Dictionary containing parallel execution metrics
        """
        base_metrics = self.get_metrics()
        
        async with self._lock:
            concurrent_count = len(self._running_tasks)
        
        async with self._dependency_lock:
            dependency_satisfied = len(self._completed_issues_set)
        
        parallel_metrics = {
            "max_concurrent": self.max_concurrent,
            "currently_running": concurrent_count,
            "dependencies_satisfied": dependency_satisfied,
            "semaphore_available": self._semaphore._value,
            "resource_utilization": concurrent_count / self.max_concurrent if self.max_concurrent > 0 else 0.0
        }
        
        return {**base_metrics, **parallel_metrics}