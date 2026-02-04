#!/usr/bin/env python3
"""
Workflow executor that calls the workflow-template.sh script.
Manages subprocess execution of the Claude Flow workflow for each issue.
"""

import logging
import os
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import psutil


@dataclass
class WorkflowExecution:
    """Information about a workflow execution."""
    issue_id: int
    issue_title: str
    repo_url: str
    process_id: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    stdout_log: Optional[str] = None
    stderr_log: Optional[str] = None


class WorkflowExecutor:
    """Executes Claude Flow workflows using the workflow-template.sh script."""
    
    def __init__(
        self,
        script_path: Path,
        log_directory: Optional[Path] = None,
        timeout: int = 3600,
        environment: Optional[Dict[str, str]] = None
    ):
        """
        Initialize workflow executor.
        
        Args:
            script_path: Path to the workflow-template.sh script
            log_directory: Directory to store execution logs
            timeout: Default timeout for workflow execution (seconds)
            environment: Additional environment variables
        """
        self.script_path = Path(script_path)
        self.log_directory = log_directory or Path("/tmp/claude-flow-logs")
        self.timeout = timeout
        self.environment = environment or {}
        self.logger = logging.getLogger(__name__)
        
        # Ensure script exists and is executable
        if not self.script_path.exists():
            raise FileNotFoundError(f"Workflow script not found: {self.script_path}")
        
        if not os.access(self.script_path, os.X_OK):
            raise PermissionError(f"Workflow script not executable: {self.script_path}")
        
        # Ensure log directory exists
        self.log_directory.mkdir(parents=True, exist_ok=True)
        
        # Track active executions
        self._active_executions: Dict[int, WorkflowExecution] = {}
        self._execution_lock = threading.RLock()
        
        self.logger.info(f"Workflow executor initialized with script: {self.script_path}")
    
    def execute_workflow(
        self,
        issue_id: int,
        issue_title: str,
        repo_url: str,
        force: bool = False,
        skip_cleanup: bool = False,
        custom_timeout: Optional[int] = None,
        dry_run: bool = False
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Execute workflow for a specific issue.
        
        Args:
            issue_id: GitHub issue ID
            issue_title: Issue title
            repo_url: Repository URL for cloning
            force: Force execution even if workspace exists
            skip_cleanup: Skip workspace cleanup after completion
            custom_timeout: Custom timeout for this execution
            dry_run: Show what would be done without executing
            
        Returns:
            Tuple of (success: bool, pr_url: Optional[str], error_message: Optional[str])
        """
        self.logger.info(f"Starting workflow execution for issue #{issue_id}: {issue_title}")
        
        # Check if already running
        with self._execution_lock:
            if issue_id in self._active_executions:
                existing_exec = self._active_executions[issue_id]
                if existing_exec.process_id and self._is_process_running(existing_exec.process_id):
                    error_msg = f"Issue #{issue_id} is already being processed"
                    self.logger.warning(error_msg)
                    return False, None, error_msg
                else:
                    # Clean up stale entry
                    del self._active_executions[issue_id]
        
        # Create execution record
        execution = WorkflowExecution(
            issue_id=issue_id,
            issue_title=issue_title,
            repo_url=repo_url,
            started_at=datetime.now(timezone.utc)
        )
        
        with self._execution_lock:
            self._active_executions[issue_id] = execution
        
        try:
            # Prepare command arguments
            cmd_args = [
                str(self.script_path),
                str(issue_id),
                issue_title,
                repo_url
            ]
            
            if force:
                cmd_args.append("--force")
            
            if skip_cleanup:
                cmd_args.append("--skip-cleanup")
            
            if custom_timeout:
                cmd_args.extend(["--timeout", str(custom_timeout)])
            
            if dry_run:
                cmd_args.append("--dry-run")
            
            cmd_args.append("--verbose")
            
            # Prepare environment
            env = os.environ.copy()
            env.update(self.environment)
            
            # Ensure required environment variables
            if 'ANTHROPIC_API_KEY' not in env:
                error_msg = "ANTHROPIC_API_KEY environment variable not set"
                self.logger.error(error_msg)
                return False, None, error_msg
            
            # Setup log files
            log_base = self.log_directory / f"issue-{issue_id}-{int(time.time())}"
            stdout_log = log_base.with_suffix('.out')
            stderr_log = log_base.with_suffix('.err')
            combined_log = log_base.with_suffix('.log')
            
            execution.stdout_log = str(stdout_log)
            execution.stderr_log = str(stderr_log)
            
            self.logger.info(f"Executing command: {' '.join(cmd_args)}")
            self.logger.info(f"Logs will be written to: {log_base}.*")
            
            # Execute the workflow script
            timeout_value = custom_timeout or self.timeout
            
            with open(stdout_log, 'w') as stdout_file, \
                 open(stderr_log, 'w') as stderr_file, \
                 open(combined_log, 'w') as combined_file:
                
                # Start the process
                process = subprocess.Popen(
                    cmd_args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                    preexec_fn=os.setsid  # Create new process group
                )
                
                execution.process_id = process.pid
                self.logger.info(f"Workflow process started with PID {process.pid}")
                
                # Monitor process output in separate threads
                stdout_thread = threading.Thread(
                    target=self._stream_output,
                    args=(process.stdout, stdout_file, combined_file, "STDOUT"),
                    daemon=True
                )
                stderr_thread = threading.Thread(
                    target=self._stream_output,
                    args=(process.stderr, stderr_file, combined_file, "STDERR"),
                    daemon=True
                )
                
                stdout_thread.start()
                stderr_thread.start()
                
                try:
                    # Wait for process completion with timeout
                    exit_code = process.wait(timeout=timeout_value)
                    execution.exit_code = exit_code
                    
                    # Wait for output threads to finish
                    stdout_thread.join(timeout=5)
                    stderr_thread.join(timeout=5)
                    
                except subprocess.TimeoutExpired:
                    self.logger.warning(f"Workflow execution timed out after {timeout_value}s")
                    
                    # Terminate the process group
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                        process.wait(timeout=10)
                    except (ProcessLookupError, subprocess.TimeoutExpired):
                        # Force kill if termination fails
                        try:
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                            process.wait(timeout=5)
                        except (ProcessLookupError, subprocess.TimeoutExpired):
                            pass
                    
                    execution.exit_code = -1
                    error_msg = f"Workflow execution timed out after {timeout_value} seconds"
                    return False, None, error_msg
            
            execution.completed_at = datetime.now(timezone.utc)
            
            # Analyze results
            if execution.exit_code == 0:
                self.logger.info(f"Workflow completed successfully for issue #{issue_id}")
                
                # Extract PR URL from logs
                pr_url = self._extract_pr_url(combined_log)
                return True, pr_url, None
            else:
                # Read error details from logs
                error_msg = self._extract_error_message(stderr_log, combined_log)
                self.logger.error(f"Workflow failed for issue #{issue_id}: {error_msg}")
                return False, None, error_msg
        
        except Exception as e:
            error_msg = f"Unexpected error during workflow execution: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            execution.completed_at = datetime.now(timezone.utc)
            return False, None, error_msg
        
        finally:
            # Clean up execution record
            with self._execution_lock:
                if issue_id in self._active_executions:
                    del self._active_executions[issue_id]
    
    def _stream_output(self, source, file_handle, combined_handle, prefix: str) -> None:
        """Stream output from subprocess to log files."""
        try:
            for line in source:
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                formatted_line = f"[{timestamp}] {line}"
                
                # Write to individual log
                file_handle.write(line)
                file_handle.flush()
                
                # Write to combined log with prefix
                combined_handle.write(f"[{prefix}] {formatted_line}")
                combined_handle.flush()
                
                # Log important messages
                if any(keyword in line.lower() for keyword in ['error', 'failed', 'success', 'completed']):
                    self.logger.info(f"[{prefix}] {line.strip()}")
        except Exception as e:
            self.logger.error(f"Error streaming {prefix} output: {e}")
    
    def _extract_pr_url(self, log_file: Path) -> Optional[str]:
        """Extract pull request URL from execution logs."""
        try:
            with open(log_file, 'r') as f:
                content = f.read()
            
            # Look for PR URL patterns
            import re
            patterns = [
                r'Pull Request: (https://github\.com/[^\s]+)',
                r'PR created: (https://github\.com/[^\s]+)',
                r'✓.*Pull request.*: (https://github\.com/[^\s]+)',
                r'https://github\.com/[^/]+/[^/]+/pull/\d+'
            ]
            
            for pattern in patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    if match.groups():
                        return match.group(1)
                    else:
                        return match.group(0)
            
        except Exception as e:
            self.logger.warning(f"Failed to extract PR URL from logs: {e}")
        
        return None
    
    def _extract_error_message(self, stderr_log: Path, combined_log: Path) -> str:
        """Extract error message from execution logs."""
        error_lines = []
        
        # Check stderr log first
        try:
            if stderr_log.exists():
                with open(stderr_log, 'r') as f:
                    stderr_content = f.read().strip()
                    if stderr_content:
                        error_lines.append("STDERR:")
                        error_lines.extend(stderr_content.split('\n')[-10:])  # Last 10 lines
        except Exception:
            pass
        
        # Check combined log for error patterns
        try:
            if combined_log.exists():
                with open(combined_log, 'r') as f:
                    lines = f.readlines()
                
                # Look for error patterns in last 50 lines
                for line in lines[-50:]:
                    if any(keyword in line.lower() for keyword in ['error', 'failed', 'exception']):
                        error_lines.append(line.strip())
        except Exception:
            pass
        
        if error_lines:
            return '\n'.join(error_lines[-5:])  # Last 5 error lines
        else:
            return "Workflow execution failed with unknown error"
    
    def _is_process_running(self, pid: int) -> bool:
        """Check if a process is still running."""
        try:
            return psutil.pid_exists(pid)
        except Exception:
            # Fallback to basic check
            try:
                os.kill(pid, 0)
                return True
            except (OSError, ProcessLookupError):
                return False
    
    def get_active_executions(self) -> Dict[int, WorkflowExecution]:
        """Get currently active workflow executions."""
        with self._execution_lock:
            return self._active_executions.copy()
    
    def cancel_execution(self, issue_id: int) -> bool:
        """
        Cancel an active workflow execution.
        
        Args:
            issue_id: Issue ID of execution to cancel
            
        Returns:
            True if cancellation was successful, False otherwise
        """
        with self._execution_lock:
            execution = self._active_executions.get(issue_id)
            if not execution or not execution.process_id:
                return False
            
            if not self._is_process_running(execution.process_id):
                # Process already dead, clean up
                del self._active_executions[issue_id]
                return True
        
        try:
            self.logger.info(f"Cancelling workflow execution for issue #{issue_id} (PID {execution.process_id})")
            
            # Send SIGTERM to process group
            os.killpg(os.getpgid(execution.process_id), signal.SIGTERM)
            
            # Wait a bit for graceful termination
            time.sleep(5)
            
            # Force kill if still running
            if self._is_process_running(execution.process_id):
                os.killpg(os.getpgid(execution.process_id), signal.SIGKILL)
            
            # Clean up execution record
            with self._execution_lock:
                if issue_id in self._active_executions:
                    del self._active_executions[issue_id]
            
            self.logger.info(f"Workflow execution cancelled for issue #{issue_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to cancel workflow execution for issue #{issue_id}: {e}")
            return False
    
    def cleanup_logs(self, max_age_days: int = 7) -> int:
        """
        Clean up old log files.
        
        Args:
            max_age_days: Maximum age of logs to keep
            
        Returns:
            Number of log files cleaned up
        """
        cutoff_time = time.time() - (max_age_days * 24 * 60 * 60)
        cleaned_count = 0
        
        try:
            for log_file in self.log_directory.glob("issue-*.log"):
                if log_file.stat().st_mtime < cutoff_time:
                    log_file.unlink()
                    cleaned_count += 1
            
            # Clean up associated .out and .err files
            for log_file in self.log_directory.glob("issue-*.out"):
                if log_file.stat().st_mtime < cutoff_time:
                    log_file.unlink()
                    cleaned_count += 1
            
            for log_file in self.log_directory.glob("issue-*.err"):
                if log_file.stat().st_mtime < cutoff_time:
                    log_file.unlink()
                    cleaned_count += 1
            
            if cleaned_count > 0:
                self.logger.info(f"Cleaned up {cleaned_count} old log files")
        
        except Exception as e:
            self.logger.error(f"Failed to clean up logs: {e}")
        
        return cleaned_count
    
    def get_execution_logs(self, issue_id: int) -> Optional[Dict[str, str]]:
        """
        Get execution logs for a specific issue.
        
        Args:
            issue_id: Issue ID to get logs for
            
        Returns:
            Dictionary with log contents or None if not found
        """
        logs = {}
        
        # Find log files for this issue
        log_pattern = f"issue-{issue_id}-*.log"
        matching_logs = list(self.log_directory.glob(log_pattern))
        
        if not matching_logs:
            return None
        
        # Get the most recent log file
        latest_log = max(matching_logs, key=lambda p: p.stat().st_mtime)
        base_name = latest_log.stem
        
        # Read all related log files
        for suffix in ['.log', '.out', '.err']:
            log_file = latest_log.parent / (base_name + suffix)
            if log_file.exists():
                try:
                    with open(log_file, 'r') as f:
                        logs[suffix.lstrip('.')] = f.read()
                except Exception as e:
                    self.logger.warning(f"Failed to read {log_file}: {e}")
                    logs[suffix.lstrip('.')] = f"Error reading file: {e}"
        
        return logs if logs else None