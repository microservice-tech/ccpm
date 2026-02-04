"""
Workflow Manager

This module manages the complete lifecycle of per-issue workflows.
It coordinates all stages including workspace creation, repository operations,
Claude Flow installation, hive-mind execution, and cleanup. The workflow
manager integrates with the existing workflow template script while providing
sophisticated orchestration and monitoring capabilities.
"""

import asyncio
import logging
import os
import subprocess
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timedelta


class WorkflowStage(Enum):
    """Individual workflow execution stages"""
    WORKSPACE_CREATION = "workspace_creation"
    REPOSITORY_CLONE = "repository_clone"
    BRANCH_CREATION = "branch_creation"
    CLAUDE_FLOW_INSTALL = "claude_flow_install"
    HIVE_MIND_SPAWN = "hive_mind_spawn"
    IMPLEMENTATION_MONITOR = "implementation_monitor"
    PR_CREATION = "pr_creation"
    CLEANUP = "cleanup"


@dataclass
class StageResult:
    """Result of a workflow stage execution"""
    stage: WorkflowStage
    success: bool
    duration: float
    output: str = ""
    error: str = ""
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class WorkflowManager:
    """
    Manages the complete lifecycle of per-issue workflows.
    
    This class coordinates all stages of the workflow execution,
    from workspace creation to cleanup. It integrates with the
    existing workflow template script while providing enhanced
    monitoring, error handling, and recovery capabilities.
    """
    
    def __init__(self, orchestrator):
        """
        Initialize the workflow manager.
        
        Args:
            orchestrator: Reference to the parent orchestrator instance
        """
        self.orchestrator = orchestrator
        self.logger = logging.getLogger(__name__)
        
        # Stage execution timeouts (in seconds)
        self.stage_timeouts = {
            WorkflowStage.WORKSPACE_CREATION: 30,
            WorkflowStage.REPOSITORY_CLONE: 300,  # 5 minutes
            WorkflowStage.BRANCH_CREATION: 30,
            WorkflowStage.CLAUDE_FLOW_INSTALL: 600,  # 10 minutes
            WorkflowStage.HIVE_MIND_SPAWN: 1800,  # 30 minutes
            WorkflowStage.IMPLEMENTATION_MONITOR: 3600,  # 1 hour
            WorkflowStage.PR_CREATION: 120,  # 2 minutes
            WorkflowStage.CLEANUP: 60
        }
        
        # Retry configuration
        self.max_retries = {
            WorkflowStage.WORKSPACE_CREATION: 2,
            WorkflowStage.REPOSITORY_CLONE: 3,
            WorkflowStage.BRANCH_CREATION: 2,
            WorkflowStage.CLAUDE_FLOW_INSTALL: 2,
            WorkflowStage.HIVE_MIND_SPAWN: 1,
            WorkflowStage.IMPLEMENTATION_MONITOR: 1,
            WorkflowStage.PR_CREATION: 2,
            WorkflowStage.CLEANUP: 1
        }
    
    async def execute_workflow(self, context) -> List[StageResult]:
        """
        Execute the complete workflow for an issue.
        
        Args:
            context: IssueContext with all necessary information
            
        Returns:
            List[StageResult]: Results for each stage execution
            
        Raises:
            RuntimeError: If critical workflow stages fail
        """
        self.logger.info(f"Starting workflow execution for issue #{context.issue_id}")
        
        results = []
        
        # Define workflow stages in execution order
        stages = [
            (WorkflowStage.WORKSPACE_CREATION, self._create_workspace),
            (WorkflowStage.REPOSITORY_CLONE, self._clone_repository),
            (WorkflowStage.BRANCH_CREATION, self._create_branch),
            (WorkflowStage.CLAUDE_FLOW_INSTALL, self._install_claude_flow),
            (WorkflowStage.HIVE_MIND_SPAWN, self._spawn_hive_mind),
            (WorkflowStage.IMPLEMENTATION_MONITOR, self._monitor_implementation),
            (WorkflowStage.PR_CREATION, self._create_pull_request)
        ]
        
        try:
            # Execute each stage
            for stage, stage_func in stages:
                self.logger.info(f"Executing stage: {stage.value}")
                
                result = await self._execute_stage_with_retry(
                    stage, stage_func, context
                )
                results.append(result)
                
                # Stop on critical failures
                if not result.success and stage in [
                    WorkflowStage.WORKSPACE_CREATION,
                    WorkflowStage.REPOSITORY_CLONE,
                    WorkflowStage.CLAUDE_FLOW_INSTALL
                ]:
                    raise RuntimeError(f"Critical stage failed: {stage.value} - {result.error}")
                
                # Log stage completion
                if result.success:
                    self.logger.info(f"Stage {stage.value} completed in {result.duration:.2f}s")
                else:
                    self.logger.warning(f"Stage {stage.value} failed: {result.error}")
        
        except Exception as e:
            self.logger.error(f"Workflow execution failed for issue #{context.issue_id}: {e}")
            raise
        
        # Always try cleanup, even if other stages failed
        finally:
            if self.orchestrator.cleanup_on_completion:
                cleanup_result = await self._execute_stage_with_retry(
                    WorkflowStage.CLEANUP, self._cleanup_workspace, context
                )
                results.append(cleanup_result)
        
        self.logger.info(f"Workflow execution completed for issue #{context.issue_id}")
        return results
    
    async def _execute_stage_with_retry(self, 
                                      stage: WorkflowStage, 
                                      stage_func: Callable,
                                      context) -> StageResult:
        """
        Execute a workflow stage with retry logic.
        
        Args:
            stage: The workflow stage to execute
            stage_func: Function to execute for this stage
            context: Issue context
            
        Returns:
            StageResult: Result of the stage execution
        """
        max_attempts = self.max_retries.get(stage, 1) + 1
        timeout = self.stage_timeouts.get(stage, 300)
        
        for attempt in range(max_attempts):
            start_time = time.time()
            
            try:
                self.logger.debug(f"Stage {stage.value} attempt {attempt + 1}/{max_attempts}")
                
                # Execute with timeout
                result = await asyncio.wait_for(
                    stage_func(context), 
                    timeout=timeout
                )
                
                duration = time.time() - start_time
                
                if result.get('success', False):
                    return StageResult(
                        stage=stage,
                        success=True,
                        duration=duration,
                        output=result.get('output', ''),
                        timestamp=datetime.now()
                    )
                else:
                    error = result.get('error', 'Unknown error')
                    if attempt < max_attempts - 1:
                        self.logger.warning(f"Stage {stage.value} failed, retrying: {error}")
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
                        continue
                    else:
                        return StageResult(
                            stage=stage,
                            success=False,
                            duration=duration,
                            error=error,
                            timestamp=datetime.now()
                        )
            
            except asyncio.TimeoutError:
                duration = time.time() - start_time
                error = f"Stage timeout after {timeout} seconds"
                
                if attempt < max_attempts - 1:
                    self.logger.warning(f"Stage {stage.value} timed out, retrying")
                    await asyncio.sleep(2 ** attempt)
                    continue
                else:
                    return StageResult(
                        stage=stage,
                        success=False,
                        duration=duration,
                        error=error,
                        timestamp=datetime.now()
                    )
            
            except Exception as e:
                duration = time.time() - start_time
                error = f"Unexpected error: {str(e)}"
                
                if attempt < max_attempts - 1:
                    self.logger.warning(f"Stage {stage.value} failed with exception, retrying: {e}")
                    await asyncio.sleep(2 ** attempt)
                    continue
                else:
                    return StageResult(
                        stage=stage,
                        success=False,
                        duration=duration,
                        error=error,
                        timestamp=datetime.now()
                    )
        
        # Should not reach here, but failsafe
        return StageResult(
            stage=stage,
            success=False,
            duration=0,
            error="Maximum retry attempts exceeded",
            timestamp=datetime.now()
        )
    
    async def _create_workspace(self, context) -> Dict[str, Any]:
        """Create isolated workspace for the issue"""
        try:
            workspace_path = Path(context.workspace_path)
            workspace_path.mkdir(parents=True, exist_ok=True)
            
            # Set appropriate permissions
            os.chmod(workspace_path, 0o755)
            
            return {
                'success': True,
                'output': f"Workspace created: {workspace_path}"
            }
        
        except Exception as e:
            return {
                'success': False,
                'error': f"Failed to create workspace: {str(e)}"
            }
    
    async def _clone_repository(self, context) -> Dict[str, Any]:
        """Clone repository to workspace"""
        try:
            workspace_path = Path(context.workspace_path)
            
            # Clone repository
            process = await asyncio.create_subprocess_exec(
                'git', 'clone', context.repo_url, str(workspace_path / 'repo'),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_path
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                return {
                    'success': False,
                    'error': f"Git clone failed: {stderr.decode()}"
                }
            
            return {
                'success': True,
                'output': f"Repository cloned to {workspace_path / 'repo'}"
            }
        
        except Exception as e:
            return {
                'success': False,
                'error': f"Failed to clone repository: {str(e)}"
            }
    
    async def _create_branch(self, context) -> Dict[str, Any]:
        """Create feature branch for the issue"""
        try:
            repo_path = Path(context.workspace_path) / 'repo'
            
            # Create and checkout feature branch
            process = await asyncio.create_subprocess_exec(
                'git', 'checkout', '-b', context.branch_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=repo_path
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                return {
                    'success': False,
                    'error': f"Branch creation failed: {stderr.decode()}"
                }
            
            return {
                'success': True,
                'output': f"Branch {context.branch_name} created and checked out"
            }
        
        except Exception as e:
            return {
                'success': False,
                'error': f"Failed to create branch: {str(e)}"
            }
    
    async def _install_claude_flow(self, context) -> Dict[str, Any]:
        """Install Claude Flow locally in the workspace"""
        try:
            repo_path = Path(context.workspace_path) / 'repo'
            
            # Initialize npm project if needed
            package_json_path = repo_path / 'package.json'
            if not package_json_path.exists():
                process = await asyncio.create_subprocess_exec(
                    'npm', 'init', '-y',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=repo_path
                )
                await process.communicate()
            
            # Install Claude Flow
            process = await asyncio.create_subprocess_exec(
                'npm', 'install', 'claude-flow@alpha',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=repo_path
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                return {
                    'success': False,
                    'error': f"Claude Flow installation failed: {stderr.decode()}"
                }
            
            # Initialize Claude Flow
            process = await asyncio.create_subprocess_exec(
                'npx', 'claude-flow', 'init', '--force',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=repo_path
            )
            
            stdout_init, stderr_init = await process.communicate()
            
            if process.returncode != 0:
                return {
                    'success': False,
                    'error': f"Claude Flow init failed: {stderr_init.decode()}"
                }
            
            return {
                'success': True,
                'output': f"Claude Flow installed and initialized successfully"
            }
        
        except Exception as e:
            return {
                'success': False,
                'error': f"Failed to install Claude Flow: {str(e)}"
            }
    
    async def _spawn_hive_mind(self, context) -> Dict[str, Any]:
        """Spawn hive-mind with issue context"""
        try:
            repo_path = Path(context.workspace_path) / 'repo'
            
            # Prepare issue prompt
            issue_prompt = (
                f"Implement GitHub issue #{context.issue_id}: {context.title}. "
                f"Issue description: {context.body}. "
                f"Read the issue, implement the solution, write tests, "
                f"and prepare for PR creation."
            )
            
            # Execute hive-mind
            process = await asyncio.create_subprocess_exec(
                'npx', 'claude-flow@alpha', 'hive-mind', 'spawn',
                issue_prompt, '--claude',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=repo_path
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                return {
                    'success': False,
                    'error': f"Hive-mind spawn failed: {stderr.decode()}"
                }
            
            return {
                'success': True,
                'output': f"Hive-mind spawned successfully for issue #{context.issue_id}"
            }
        
        except Exception as e:
            return {
                'success': False,
                'error': f"Failed to spawn hive-mind: {str(e)}"
            }
    
    async def _monitor_implementation(self, context) -> Dict[str, Any]:
        """Monitor implementation progress and handle feedback"""
        try:
            repo_path = Path(context.workspace_path) / 'repo'
            
            # Check for .swarm directory and implementation progress
            swarm_dir = repo_path / '.swarm'
            
            # Monitor for completion or timeout
            start_time = time.time()
            monitoring_timeout = self.stage_timeouts[WorkflowStage.IMPLEMENTATION_MONITOR]
            
            while time.time() - start_time < monitoring_timeout:
                # Check if implementation is complete (basic check)
                if swarm_dir.exists():
                    # Look for completion indicators
                    session_files = list(swarm_dir.glob('*.json'))
                    
                    if session_files:
                        # Check latest session for completion
                        latest_session = max(session_files, key=os.path.getmtime)
                        try:
                            with open(latest_session, 'r') as f:
                                session_data = json.load(f)
                            
                            # Simple completion check (can be enhanced)
                            if session_data.get('status') == 'completed':
                                return {
                                    'success': True,
                                    'output': 'Implementation completed successfully'
                                }
                        except (json.JSONDecodeError, KeyError):
                            pass
                
                # Wait before next check
                await asyncio.sleep(30)  # Check every 30 seconds
            
            # Implementation monitoring timed out
            return {
                'success': False,
                'error': f'Implementation monitoring timed out after {monitoring_timeout} seconds'
            }
        
        except Exception as e:
            return {
                'success': False,
                'error': f"Failed to monitor implementation: {str(e)}"
            }
    
    async def _create_pull_request(self, context) -> Dict[str, Any]:
        """Create pull request after successful implementation"""
        try:
            repo_path = Path(context.workspace_path) / 'repo'
            
            # Add all changes
            process = await asyncio.create_subprocess_exec(
                'git', 'add', '-A',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=repo_path
            )
            await process.communicate()
            
            # Commit changes
            commit_message = f"feat: implement issue #{context.issue_id}"
            process = await asyncio.create_subprocess_exec(
                'git', 'commit', '-m', commit_message,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=repo_path
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                return {
                    'success': False,
                    'error': f"Commit failed: {stderr.decode()}"
                }
            
            # Push branch
            process = await asyncio.create_subprocess_exec(
                'git', 'push', 'origin', context.branch_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=repo_path
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                return {
                    'success': False,
                    'error': f"Push failed: {stderr.decode()}"
                }
            
            # Create PR using gh CLI if available
            pr_title = f"Fix #{context.issue_id}: {context.title}"
            pr_body = "Automated implementation by Claude Flow hive-mind"
            
            process = await asyncio.create_subprocess_exec(
                'gh', 'pr', 'create',
                '--title', pr_title,
                '--body', pr_body,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=repo_path
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                return {
                    'success': False,
                    'error': f"PR creation failed: {stderr.decode()}"
                }
            
            return {
                'success': True,
                'output': f"PR created successfully for issue #{context.issue_id}"
            }
        
        except Exception as e:
            return {
                'success': False,
                'error': f"Failed to create PR: {str(e)}"
            }
    
    async def _cleanup_workspace(self, context) -> Dict[str, Any]:
        """Clean up workspace after processing"""
        try:
            workspace_path = Path(context.workspace_path)
            
            if workspace_path.exists():
                import shutil
                shutil.rmtree(workspace_path)
                
                return {
                    'success': True,
                    'output': f"Workspace cleaned up: {workspace_path}"
                }
            else:
                return {
                    'success': True,
                    'output': f"Workspace already clean: {workspace_path}"
                }
        
        except Exception as e:
            return {
                'success': False,
                'error': f"Failed to cleanup workspace: {str(e)}"
            }