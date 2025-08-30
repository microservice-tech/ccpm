#!/usr/bin/env python3
"""
Issue processor for managing concurrent issue processing.
Handles the lifecycle of processing GitHub issues with Claude Flow.
"""

import json
import logging
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set
from enum import Enum

try:
    from .github_client import GitHubClient
    from .workflow_executor import WorkflowExecutor
except ImportError:
    from github_client import GitHubClient
    from workflow_executor import WorkflowExecutor


class IssueStatus(Enum):
    """Issue processing status."""
    DISCOVERED = "discovered"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class IssueProcessingState:
    """State information for issue processing."""
    issue_id: int
    title: str
    repo_owner: str
    repo_name: str
    repo_url: str
    status: IssueStatus
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    pr_url: Optional[str] = None
    attempt_count: int = 0
    max_attempts: int = 3
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data['status'] = self.status.value
        if self.started_at:
            data['started_at'] = self.started_at.isoformat()
        if self.completed_at:
            data['completed_at'] = self.completed_at.isoformat()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'IssueProcessingState':
        """Create from dictionary."""
        data = data.copy()
        data['status'] = IssueStatus(data['status'])
        if data.get('started_at'):
            data['started_at'] = datetime.fromisoformat(data['started_at'])
        if data.get('completed_at'):
            data['completed_at'] = datetime.fromisoformat(data['completed_at'])
        return cls(**data)


class IssueProcessor:
    """Manages concurrent processing of GitHub issues."""
    
    def __init__(
        self,
        github_client: GitHubClient,
        workflow_executor: WorkflowExecutor,
        max_workers: int = 3,
        state_file: Optional[Path] = None
    ):
        """
        Initialize issue processor.
        
        Args:
            github_client: GitHub API client
            workflow_executor: Workflow execution handler
            max_workers: Maximum number of concurrent workers
            state_file: Path to state persistence file
        """
        self.github_client = github_client
        self.workflow_executor = workflow_executor
        self.max_workers = max_workers
        self.state_file = state_file or Path("/tmp/claude-flow-issue-state.json")
        self.logger = logging.getLogger(__name__)
        
        # Processing state
        self._processing_state: Dict[int, IssueProcessingState] = {}
        self._state_lock = threading.RLock()
        self._shutdown_event = threading.Event()
        
        # Load existing state
        self._load_state()
        
        # Thread pools
        self._process_executor: Optional[ProcessPoolExecutor] = None
        self._thread_executor: Optional[ThreadPoolExecutor] = None
    
    def start(self) -> None:
        """Start the processing executors."""
        if self._process_executor is not None:
            self.logger.warning("Issue processor already started")
            return
        
        self.logger.info(f"Starting issue processor with {self.max_workers} workers")
        self._process_executor = ProcessPoolExecutor(max_workers=self.max_workers)
        self._thread_executor = ThreadPoolExecutor(max_workers=self.max_workers * 2)
        
        # Start state persistence thread
        self._thread_executor.submit(self._state_persistence_loop)
    
    def stop(self, timeout: int = 300) -> None:
        """
        Stop the processing executors and wait for completion.
        
        Args:
            timeout: Maximum time to wait for shutdown
        """
        self.logger.info("Stopping issue processor...")
        self._shutdown_event.set()
        
        if self._process_executor:
            self._process_executor.shutdown(wait=True, cancel_futures=True)
            self._process_executor = None
        
        if self._thread_executor:
            self._thread_executor.shutdown(wait=True, cancel_futures=True)
            self._thread_executor = None
        
        # Save final state
        self._save_state()
        self.logger.info("Issue processor stopped")
    
    def process_issues(
        self,
        owner: str,
        repo: str,
        repo_url: str,
        label_filter: List[str],
        exclude_labels: Optional[List[str]] = None
    ) -> List[IssueProcessingState]:
        """
        Process issues from a repository.
        
        Args:
            owner: Repository owner
            repo: Repository name
            repo_url: Repository clone URL
            label_filter: Labels that issues must have
            exclude_labels: Labels that disqualify issues
            
        Returns:
            List of processing states for discovered issues
        """
        if not self._process_executor:
            raise RuntimeError("Issue processor not started")
        
        self.logger.info(f"Processing issues from {owner}/{repo}")
        
        try:
            # Fetch issues from GitHub
            issues = self.github_client.get_issues(
                owner=owner,
                repo=repo,
                labels=label_filter,
                state='open'
            )
            
            # Filter out pull requests and excluded issues
            filtered_issues = []
            for issue in issues:
                # Skip pull requests
                if 'pull_request' in issue:
                    continue
                
                # Skip if has exclude labels
                if exclude_labels:
                    issue_labels = [label['name'] for label in issue.get('labels', [])]
                    if any(excl_label in issue_labels for excl_label in exclude_labels):
                        continue
                
                filtered_issues.append(issue)
            
            self.logger.info(f"Found {len(filtered_issues)} eligible issues")
            
            # Process each issue
            processing_states = []
            futures = []
            
            for issue in filtered_issues:
                issue_id = issue['number']
                issue_title = issue['title']
                
                # Check if already processed or in progress
                with self._state_lock:
                    existing_state = self._processing_state.get(issue_id)
                    if existing_state:
                        if existing_state.status in [IssueStatus.COMPLETED, IssueStatus.IN_PROGRESS]:
                            self.logger.debug(f"Issue #{issue_id} already {existing_state.status.value}")
                            processing_states.append(existing_state)
                            continue
                        
                        # Reset failed issues if they haven't exceeded max attempts
                        if (existing_state.status == IssueStatus.FAILED and 
                            existing_state.attempt_count < existing_state.max_attempts):
                            existing_state.attempt_count += 1
                            existing_state.status = IssueStatus.DISCOVERED
                            existing_state.error_message = None
                        else:
                            processing_states.append(existing_state)
                            continue
                    else:
                        # Create new processing state
                        state = IssueProcessingState(
                            issue_id=issue_id,
                            title=issue_title,
                            repo_owner=owner,
                            repo_name=repo,
                            repo_url=repo_url,
                            status=IssueStatus.DISCOVERED
                        )
                        self._processing_state[issue_id] = state
                        processing_states.append(state)
                
                # Submit for processing
                future = self._thread_executor.submit(
                    self._process_single_issue,
                    issue_id,
                    issue_title,
                    owner,
                    repo,
                    repo_url
                )
                futures.append((future, issue_id))
            
            # Monitor processing completion
            self._thread_executor.submit(self._monitor_processing, futures)
            
            return processing_states
            
        except Exception as e:
            self.logger.error(f"Failed to process issues from {owner}/{repo}: {e}")
            raise
    
    def _process_single_issue(
        self,
        issue_id: int,
        issue_title: str,
        owner: str,
        repo: str,
        repo_url: str
    ) -> None:
        """Process a single issue."""
        self.logger.info(f"Starting processing of issue #{issue_id}: {issue_title}")
        
        # Update state to in-progress
        with self._state_lock:
            state = self._processing_state.get(issue_id)
            if not state:
                self.logger.error(f"No state found for issue #{issue_id}")
                return
            
            state.status = IssueStatus.IN_PROGRESS
            state.started_at = datetime.now(timezone.utc)
            state.attempt_count += 1
        
        try:
            # Add comment to issue indicating processing has started
            try:
                self.github_client.add_issue_comment(
                    owner=owner,
                    repo=repo,
                    issue_number=issue_id,
                    body="🤖 **Claude Flow Processing Started**\n\n"
                          "This issue has been picked up for automated implementation. "
                          "I'll create a pull request when the implementation is complete.\n\n"
                          f"Started at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
                )
            except Exception as e:
                self.logger.warning(f"Failed to add start comment to issue #{issue_id}: {e}")
            
            # Execute workflow
            success, pr_url, error_msg = self.workflow_executor.execute_workflow(
                issue_id=issue_id,
                issue_title=issue_title,
                repo_url=repo_url
            )
            
            with self._state_lock:
                state = self._processing_state.get(issue_id)
                if state:
                    state.completed_at = datetime.now(timezone.utc)
                    
                    if success:
                        state.status = IssueStatus.COMPLETED
                        state.pr_url = pr_url
                        
                        # Add success comment
                        try:
                            comment_body = f"✅ **Implementation Complete**\n\n"
                            if pr_url:
                                comment_body += f"Pull request created: {pr_url}\n\n"
                            comment_body += (
                                "The automated implementation has been completed successfully. "
                                "Please review the pull request and provide feedback if needed."
                            )
                            
                            self.github_client.add_issue_comment(
                                owner=owner,
                                repo=repo,
                                issue_number=issue_id,
                                body=comment_body
                            )
                            
                            # Update issue labels - remove processing label, add completed label
                            issue_data = self.github_client.get_issue(owner, repo, issue_id)
                            current_labels = [label['name'] for label in issue_data.get('labels', [])]
                            
                            # Remove processing-related labels
                            processing_labels = ['ready-for-implementation', 'claude-flow-processing']
                            updated_labels = [label for label in current_labels if label not in processing_labels]
                            updated_labels.append('claude-flow-completed')
                            
                            self.github_client.update_issue_labels(
                                owner=owner,
                                repo=repo,
                                issue_number=issue_id,
                                labels=updated_labels
                            )
                            
                        except Exception as e:
                            self.logger.warning(f"Failed to add success comment to issue #{issue_id}: {e}")
                        
                        self.logger.info(f"Successfully processed issue #{issue_id}")
                    else:
                        state.status = IssueStatus.FAILED
                        state.error_message = error_msg
                        
                        # Add failure comment
                        try:
                            comment_body = (
                                f"❌ **Implementation Failed**\n\n"
                                f"The automated implementation encountered an error:\n\n"
                                f"```\n{error_msg}\n```\n\n"
                                f"Attempt {state.attempt_count}/{state.max_attempts}\n\n"
                            )
                            
                            if state.attempt_count < state.max_attempts:
                                comment_body += "This issue will be retried automatically."
                            else:
                                comment_body += "Maximum retry attempts reached. Manual intervention required."
                            
                            self.github_client.add_issue_comment(
                                owner=owner,
                                repo=repo,
                                issue_number=issue_id,
                                body=comment_body
                            )
                        except Exception as e:
                            self.logger.warning(f"Failed to add failure comment to issue #{issue_id}: {e}")
                        
                        self.logger.error(f"Failed to process issue #{issue_id}: {error_msg}")
        
        except Exception as e:
            error_msg = str(e)
            self.logger.error(f"Unexpected error processing issue #{issue_id}: {error_msg}")
            
            with self._state_lock:
                state = self._processing_state.get(issue_id)
                if state:
                    state.status = IssueStatus.FAILED
                    state.error_message = error_msg
                    state.completed_at = datetime.now(timezone.utc)
    
    def _monitor_processing(self, futures: List) -> None:
        """Monitor processing futures and handle completion."""
        for future, issue_id in futures:
            try:
                future.result()  # Wait for completion
            except Exception as e:
                self.logger.error(f"Processing future for issue #{issue_id} failed: {e}")
    
    def get_processing_status(self) -> Dict[int, IssueProcessingState]:
        """Get current processing status for all issues."""
        with self._state_lock:
            return self._processing_state.copy()
    
    def get_issue_status(self, issue_id: int) -> Optional[IssueProcessingState]:
        """Get processing status for a specific issue."""
        with self._state_lock:
            return self._processing_state.get(issue_id)
    
    def cleanup_old_states(self, max_age_days: int = 30) -> int:
        """
        Clean up old processing states.
        
        Args:
            max_age_days: Maximum age of states to keep
            
        Returns:
            Number of states cleaned up
        """
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        cleaned_count = 0
        
        with self._state_lock:
            to_remove = []
            for issue_id, state in self._processing_state.items():
                if (state.completed_at and 
                    state.completed_at < cutoff_time and 
                    state.status in [IssueStatus.COMPLETED, IssueStatus.FAILED]):
                    to_remove.append(issue_id)
            
            for issue_id in to_remove:
                del self._processing_state[issue_id]
                cleaned_count += 1
        
        if cleaned_count > 0:
            self.logger.info(f"Cleaned up {cleaned_count} old processing states")
            self._save_state()
        
        return cleaned_count
    
    def _load_state(self) -> None:
        """Load processing state from file."""
        if not self.state_file.exists():
            return
        
        try:
            with open(self.state_file, 'r') as f:
                data = json.load(f)
            
            with self._state_lock:
                for issue_id_str, state_data in data.items():
                    issue_id = int(issue_id_str)
                    state = IssueProcessingState.from_dict(state_data)
                    self._processing_state[issue_id] = state
            
            self.logger.info(f"Loaded {len(self._processing_state)} processing states")
            
        except Exception as e:
            self.logger.warning(f"Failed to load processing state: {e}")
    
    def _save_state(self) -> None:
        """Save processing state to file."""
        try:
            # Ensure directory exists
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            
            with self._state_lock:
                data = {
                    str(issue_id): state.to_dict()
                    for issue_id, state in self._processing_state.items()
                }
            
            # Atomic write
            temp_file = self.state_file.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            temp_file.replace(self.state_file)
            
        except Exception as e:
            self.logger.error(f"Failed to save processing state: {e}")
    
    def _state_persistence_loop(self) -> None:
        """Background loop to periodically save state."""
        while not self._shutdown_event.wait(60):  # Save every minute
            try:
                self._save_state()
            except Exception as e:
                self.logger.error(f"State persistence error: {e}")


from datetime import timedelta