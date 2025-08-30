"""Sequential execution strategy for ordered processing."""

import asyncio
import time
from typing import List, Dict, Any, Optional
import subprocess
import tempfile
import shutil
from pathlib import Path

from .base import (
    ExecutionStrategy, 
    IssueContext, 
    ExecutionResult, 
    ExecutionStatus,
    ResourceManager,
    ProgressCallback
)


class SequentialExecutionStrategy(ExecutionStrategy):
    """Executes issues one at a time in order, ensuring complete isolation."""
    
    def __init__(self,
                 resource_manager: Optional[ResourceManager] = None,
                 progress_callback: Optional[ProgressCallback] = None,
                 repository_url: str = None,
                 claude_flow_install_command: str = "npm install -g @anthropic-ai/claude-flow"):
        """Initialize sequential execution strategy.
        
        Args:
            resource_manager: Optional resource manager
            progress_callback: Optional progress callback
            repository_url: URL of repository to clone for each issue
            claude_flow_install_command: Command to install Claude Flow
        """
        super().__init__(resource_manager, progress_callback, max_concurrent=1)
        self.repository_url = repository_url
        self.claude_flow_install_command = claude_flow_install_command
        self.workspace_base = Path("/tmp/claude-flow-issues")
    
    async def select_next_issues(self, 
                               available_issues: List[IssueContext],
                               current_capacity: int) -> List[IssueContext]:
        """Select the next single issue to process sequentially.
        
        Sequential strategy only processes one issue at a time.
        
        Args:
            available_issues: List of issues ready for processing
            current_capacity: Number of execution slots available (ignored for sequential)
            
        Returns:
            List containing at most one issue to process next
        """
        if not available_issues or current_capacity < 1:
            return []
        
        # For sequential, just take the first issue in order
        ordered_issues = self.get_execution_order(available_issues)
        return [ordered_issues[0]] if ordered_issues else []
    
    def get_execution_order(self, issues: List[IssueContext]) -> List[IssueContext]:
        """Order issues by creation time for FIFO processing.
        
        Args:
            issues: List of issues to order
            
        Returns:
            Issues ordered by creation time (oldest first)
        """
        return sorted(issues, key=lambda x: x.created_at)
    
    async def execute_issue(self, issue: IssueContext) -> ExecutionResult:
        """Execute a single issue workflow sequentially.
        
        This implements the complete per-issue workflow:
        1. Create isolated workspace
        2. Clone repository
        3. Create branch
        4. Install Claude Flow locally
        5. Spawn hive-mind with issue context
        6. Monitor implementation
        7. Create pull request
        8. Cleanup workspace
        
        Args:
            issue: Issue context to process
            
        Returns:
            Execution result
        """
        start_time = time.time()
        issue.status = ExecutionStatus.RUNNING
        issue.started_at = start_time
        
        try:
            # Stage 1: Create workspace
            await self._report_progress(issue.issue_id, "workspace", 0.1, "Creating isolated workspace")
            workspace_path = await self._create_workspace(issue)
            issue.workspace_path = str(workspace_path)
            
            # Stage 2: Clone repository
            await self._report_progress(issue.issue_id, "clone", 0.2, "Cloning repository")
            await self._clone_repository(workspace_path, issue)
            
            # Stage 3: Create branch
            await self._report_progress(issue.issue_id, "branch", 0.3, "Creating feature branch")
            branch_name = await self._create_branch(workspace_path, issue)
            issue.branch_name = branch_name
            
            # Stage 4: Install Claude Flow
            await self._report_progress(issue.issue_id, "install", 0.4, "Installing Claude Flow")
            await self._install_claude_flow(workspace_path, issue)
            
            # Stage 5: Spawn hive-mind
            await self._report_progress(issue.issue_id, "spawn", 0.5, "Spawning hive-mind with issue context")
            hive_result = await self._spawn_hive_mind(workspace_path, issue)
            
            # Stage 6: Monitor implementation (simplified for now)
            await self._report_progress(issue.issue_id, "implement", 0.8, "Monitoring implementation progress")
            implementation_result = await self._monitor_implementation(workspace_path, issue, hive_result)
            
            # Stage 7: Create PR
            await self._report_progress(issue.issue_id, "pr", 0.9, "Creating pull request")
            pr_url = await self._create_pull_request(workspace_path, issue, implementation_result)
            
            # Stage 8: Cleanup
            await self._report_progress(issue.issue_id, "cleanup", 1.0, "Cleaning up workspace")
            await self._cleanup_workspace(workspace_path, issue)
            
            duration = time.time() - start_time
            issue.status = ExecutionStatus.COMPLETED
            issue.completed_at = time.time()
            
            result = ExecutionResult(
                issue_id=issue.issue_id,
                status=ExecutionStatus.COMPLETED,
                success=True,
                message=f"Issue {issue.issue_id} completed successfully",
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
                    print(f"Cleanup failed: {cleanup_error}")
            
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
    
    async def _create_workspace(self, issue: IssueContext) -> Path:
        """Create isolated workspace for the issue.
        
        Args:
            issue: Issue context
            
        Returns:
            Path to created workspace
        """
        workspace_path = self.workspace_base / f"issue-{issue.issue_id}"
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
            raise RuntimeError(f"Git clone failed: {stderr.decode()}")
    
    async def _create_branch(self, workspace_path: Path, issue: IssueContext) -> str:
        """Create feature branch for the issue.
        
        Args:
            workspace_path: Path to workspace
            issue: Issue context
            
        Returns:
            Created branch name
        """
        repo_path = workspace_path / "repo"
        branch_name = f"feature/issue-{issue.issue_id}"
        
        # Create and checkout branch
        process = await asyncio.create_subprocess_exec(
            "git", "checkout", "-b", branch_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=repo_path
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            raise RuntimeError(f"Branch creation failed: {stderr.decode()}")
        
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
            raise RuntimeError(f"Claude Flow installation failed: {stderr.decode()}")
    
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
        # For now, we'll simulate the spawn
        return {
            "status": "spawned",
            "prompt_file": str(prompt_file),
            "workspace": str(repo_path),
            "issue_id": issue.issue_id
        }
    
    async def _monitor_implementation(self, 
                                    workspace_path: Path, 
                                    issue: IssueContext, 
                                    hive_result: Dict[str, Any]) -> Dict[str, Any]:
        """Monitor implementation progress.
        
        Args:
            workspace_path: Path to workspace
            issue: Issue context
            hive_result: Result from hive-mind spawn
            
        Returns:
            Implementation monitoring result
        """
        # Simplified monitoring - would implement actual progress tracking
        # For now, simulate some work
        await asyncio.sleep(1)  # Simulate implementation time
        
        return {
            "status": "completed",
            "files_changed": [],
            "commits_made": 0,
            "tests_passed": True
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
        
        # Push branch (simplified)
        process = await asyncio.create_subprocess_exec(
            "git", "push", "origin", issue.branch_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=repo_path
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            print(f"Git push warning: {stderr.decode()}")  # Non-fatal for simulation
        
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
            shutil.rmtree(workspace_path)
    
    def _format_issue_prompt(self, issue: IssueContext) -> str:
        """Format issue information into hive-mind prompt.
        
        Args:
            issue: Issue context
            
        Returns:
            Formatted prompt string
        """
        return f"""
# Issue #{issue.issue_id}: {issue.title}

## Description
{issue.body}

## Instructions
Please implement the solution for this issue following the project guidelines:
1. Analyze the requirements carefully
2. Make minimal, focused changes
3. Ensure all tests pass
4. Follow existing code patterns
5. Create comprehensive commit messages

## Dependencies
{', '.join(issue.dependencies) if issue.dependencies else 'None'}

## Priority Level
{issue.priority}
""".strip()