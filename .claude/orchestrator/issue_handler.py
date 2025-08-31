"""
Issue Handler

This module handles issue-specific processing and coordination within the
orchestrator system. It provides specialized functionality for GitHub issue
parsing, context preparation, status tracking, and integration with the
Service Foundation for polling and processing issues.

The issue handler acts as a bridge between the GitHub API polling mechanism
and the core orchestrator workflow execution.
"""

import asyncio
import logging
import json
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, asdict
from enum import Enum


class IssueState(Enum):
    """Issue processing states"""
    DISCOVERED = "discovered"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class IssuePriority(Enum):
    """Issue processing priority levels"""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class IssueMetadata:
    """Extended metadata for issue processing"""
    issue_id: str
    title: str
    body: str
    labels: List[str]
    assignees: List[str]
    repo_url: str
    created_at: datetime
    updated_at: datetime
    priority: IssuePriority = IssuePriority.NORMAL
    state: IssueState = IssueState.DISCOVERED
    processing_attempts: int = 0
    last_processed: Optional[datetime] = None
    error_message: Optional[str] = None
    estimated_effort: Optional[str] = None


class IssueHandler:
    """
    Handles issue-specific processing and coordination.
    
    This class manages the discovery, parsing, prioritization, and coordination
    of GitHub issues for automated processing. It integrates with the GitHub
    API polling mechanism and coordinates with the orchestrator for workflow
    execution.
    """
    
    def __init__(self, orchestrator):
        """
        Initialize the issue handler.
        
        Args:
            orchestrator: Reference to the parent orchestrator instance
        """
        self.orchestrator = orchestrator
        self.logger = logging.getLogger(__name__)
        
        # Issue tracking
        self.discovered_issues: Dict[str, IssueMetadata] = {}
        self.processed_issues: Set[str] = set()
        self.failed_issues: Dict[str, IssueMetadata] = {}
        
        # Processing configuration
        self.ready_label = "ready-for-implementation"
        self.max_processing_attempts = 3
        self.retry_delay_hours = 1
        
        # Priority mapping from labels
        self.priority_labels = {
            'priority-critical': IssuePriority.CRITICAL,
            'priority-high': IssuePriority.HIGH,
            'priority-normal': IssuePriority.NORMAL,
            'priority-low': IssuePriority.LOW,
            'urgent': IssuePriority.CRITICAL,
            'enhancement': IssuePriority.LOW
        }
    
    def parse_github_issue(self, issue_data: Dict[str, Any]) -> Optional[IssueMetadata]:
        """
        Parse GitHub API issue data into IssueMetadata.
        
        Args:
            issue_data: Raw GitHub issue data from API
            
        Returns:
            IssueMetadata: Parsed issue metadata or None if invalid
        """
        try:
            # Extract basic information
            issue_id = str(issue_data['number'])
            title = issue_data['title']
            body = issue_data.get('body', '')
            
            # Extract labels
            labels = [label['name'] for label in issue_data.get('labels', [])]
            
            # Extract assignees
            assignees = [assignee['login'] for assignee in issue_data.get('assignees', [])]
            
            # Parse timestamps
            created_at = datetime.fromisoformat(
                issue_data['created_at'].replace('Z', '+00:00')
            )
            updated_at = datetime.fromisoformat(
                issue_data['updated_at'].replace('Z', '+00:00')
            )
            
            # Determine repository URL
            repo_url = issue_data['repository']['clone_url'] if 'repository' in issue_data else None
            if not repo_url and 'html_url' in issue_data:
                # Extract repo URL from issue URL
                repo_match = re.match(r'https://github\.com/([^/]+/[^/]+)', issue_data['html_url'])
                if repo_match:
                    repo_url = f"https://github.com/{repo_match.group(1)}.git"
            
            # Determine priority from labels
            priority = self._determine_priority(labels)
            
            # Estimate effort from labels and content
            estimated_effort = self._estimate_effort(labels, body)
            
            # Create metadata
            metadata = IssueMetadata(
                issue_id=issue_id,
                title=title,
                body=body,
                labels=labels,
                assignees=assignees,
                repo_url=repo_url,
                created_at=created_at,
                updated_at=updated_at,
                priority=priority,
                estimated_effort=estimated_effort
            )
            
            self.logger.debug(f"Parsed issue #{issue_id}: {title}")
            return metadata
        
        except Exception as e:
            self.logger.error(f"Failed to parse GitHub issue: {e}")
            return None
    
    def _determine_priority(self, labels: List[str]) -> IssuePriority:
        """
        Determine issue priority from labels.
        
        Args:
            labels: List of issue labels
            
        Returns:
            IssuePriority: Determined priority level
        """
        # Check for explicit priority labels
        for label in labels:
            if label.lower() in self.priority_labels:
                return self.priority_labels[label.lower()]
        
        # Default priority based on label patterns
        if any(label.lower() in ['bug', 'critical', 'urgent', 'security'] for label in labels):
            return IssuePriority.HIGH
        elif any(label.lower() in ['enhancement', 'feature-request', 'documentation'] for label in labels):
            return IssuePriority.LOW
        
        return IssuePriority.NORMAL
    
    def _estimate_effort(self, labels: List[str], body: str) -> Optional[str]:
        """
        Estimate implementation effort from labels and description.
        
        Args:
            labels: List of issue labels
            body: Issue description
            
        Returns:
            Optional[str]: Effort estimate (S/M/L/XL) or None
        """
        # Check for explicit effort labels
        effort_patterns = {
            r'effort[:\-\s]*s(?:mall)?': 'S',
            r'effort[:\-\s]*m(?:edium)?': 'M', 
            r'effort[:\-\s]*l(?:arge)?': 'L',
            r'effort[:\-\s]*xl|extra[:\-\s]*large': 'XL',
            r'size[:\-\s]*s(?:mall)?': 'S',
            r'size[:\-\s]*m(?:edium)?': 'M',
            r'size[:\-\s]*l(?:arge)?': 'L',
            r'size[:\-\s]*xl': 'XL'
        }
        
        # Check labels first
        for label in labels:
            for pattern, size in effort_patterns.items():
                if re.search(pattern, label.lower()):
                    return size
        
        # Check body content for effort indicators
        if body:
            body_lower = body.lower()
            for pattern, size in effort_patterns.items():
                if re.search(pattern, body_lower):
                    return size
            
            # Heuristic based on content length and complexity
            if len(body) > 2000:
                return 'L'
            elif len(body) > 500:
                return 'M'
            else:
                return 'S'
        
        return None
    
    def is_ready_for_processing(self, metadata: IssueMetadata) -> bool:
        """
        Check if an issue is ready for processing.
        
        Args:
            metadata: Issue metadata to check
            
        Returns:
            bool: True if ready for processing
        """
        # Must have ready label
        if self.ready_label not in metadata.labels:
            return False
        
        # Must not be already processed
        if metadata.issue_id in self.processed_issues:
            return False
        
        # Check if failed too many times
        if metadata.processing_attempts >= self.max_processing_attempts:
            return False
        
        # Check retry delay for previously failed issues
        if metadata.last_processed and metadata.state == IssueState.FAILED:
            retry_time = metadata.last_processed + timedelta(hours=self.retry_delay_hours)
            if datetime.now() < retry_time:
                return False
        
        # Must have repository URL
        if not metadata.repo_url:
            self.logger.warning(f"Issue #{metadata.issue_id} missing repository URL")
            return False
        
        return True
    
    def filter_and_prioritize_issues(self, 
                                   issues: List[IssueMetadata]) -> List[IssueMetadata]:
        """
        Filter and prioritize issues for processing.
        
        Args:
            issues: List of issue metadata
            
        Returns:
            List[IssueMetadata]: Filtered and prioritized issues
        """
        # Filter ready issues
        ready_issues = [
            issue for issue in issues 
            if self.is_ready_for_processing(issue)
        ]
        
        # Sort by priority (critical first) and then by creation time (oldest first)
        prioritized_issues = sorted(
            ready_issues,
            key=lambda x: (-x.priority.value, x.created_at)
        )
        
        self.logger.info(f"Filtered {len(prioritized_issues)} ready issues from {len(issues)} total")
        return prioritized_issues
    
    async def process_issues_batch(self, 
                                 github_issues: List[Dict[str, Any]]) -> List[Any]:
        """
        Process a batch of GitHub issues through the orchestrator.
        
        Args:
            github_issues: Raw GitHub issue data from API
            
        Returns:
            List[Any]: Processing results
        """
        self.logger.info(f"Processing batch of {len(github_issues)} GitHub issues")
        
        # Parse GitHub issues
        parsed_issues = []
        for issue_data in github_issues:
            metadata = self.parse_github_issue(issue_data)
            if metadata:
                parsed_issues.append(metadata)
                # Track discovered issue
                self.discovered_issues[metadata.issue_id] = metadata
        
        # Filter and prioritize
        ready_issues = self.filter_and_prioritize_issues(parsed_issues)
        
        if not ready_issues:
            self.logger.info("No issues ready for processing")
            return []
        
        # Convert to orchestrator format
        orchestrator_issues = []
        for issue in ready_issues:
            # Update state
            issue.state = IssueState.QUEUED
            issue.processing_attempts += 1
            issue.last_processed = datetime.now()
            
            orchestrator_issues.append({
                'id': issue.issue_id,
                'title': issue.title,
                'body': issue.body,
                'repo_url': issue.repo_url
            })
        
        try:
            # Process through orchestrator
            results = await self.orchestrator.process_issues_batch(orchestrator_issues)
            
            # Update tracking based on results
            for i, result in enumerate(results):
                issue_id = ready_issues[i].issue_id
                metadata = self.discovered_issues[issue_id]
                
                if hasattr(result, 'status') and result.status.value == 'completed':
                    metadata.state = IssueState.COMPLETED
                    self.processed_issues.add(issue_id)
                    self.logger.info(f"Issue #{issue_id} processed successfully")
                else:
                    metadata.state = IssueState.FAILED
                    if hasattr(result, 'error'):
                        metadata.error_message = str(result.error)
                    self.failed_issues[issue_id] = metadata
                    self.logger.error(f"Issue #{issue_id} processing failed")
            
            return results
        
        except Exception as e:
            # Mark all issues as failed
            for issue in ready_issues:
                issue.state = IssueState.FAILED
                issue.error_message = str(e)
                self.failed_issues[issue.issue_id] = issue
            
            self.logger.error(f"Batch processing failed: {e}")
            raise
    
    def get_processing_statistics(self) -> Dict[str, Any]:
        """
        Get processing statistics and metrics.
        
        Returns:
            Dict[str, Any]: Processing statistics
        """
        # Count issues by state
        state_counts = {}
        for state in IssueState:
            state_counts[state.value] = sum(
                1 for issue in self.discovered_issues.values()
                if issue.state == state
            )
        
        # Count issues by priority
        priority_counts = {}
        for priority in IssuePriority:
            priority_counts[priority.name.lower()] = sum(
                1 for issue in self.discovered_issues.values()
                if issue.priority == priority
            )
        
        # Calculate success rate
        total_attempts = sum(
            issue.processing_attempts 
            for issue in self.discovered_issues.values()
        )
        successful_issues = len(self.processed_issues)
        success_rate = (successful_issues / total_attempts * 100) if total_attempts > 0 else 0
        
        return {
            'total_discovered': len(self.discovered_issues),
            'total_processed': len(self.processed_issues),
            'total_failed': len(self.failed_issues),
            'states': state_counts,
            'priorities': priority_counts,
            'success_rate': round(success_rate, 2),
            'total_attempts': total_attempts,
            'timestamp': datetime.now().isoformat()
        }
    
    def get_failed_issues_report(self) -> Dict[str, Any]:
        """
        Generate a report of failed issues for debugging.
        
        Returns:
            Dict[str, Any]: Failed issues report
        """
        failed_by_error = {}
        retry_candidates = []
        
        for issue_id, metadata in self.failed_issues.items():
            # Group by error message
            error_key = metadata.error_message or "Unknown error"
            if error_key not in failed_by_error:
                failed_by_error[error_key] = []
            failed_by_error[error_key].append({
                'issue_id': issue_id,
                'title': metadata.title,
                'attempts': metadata.processing_attempts,
                'last_processed': metadata.last_processed.isoformat() if metadata.last_processed else None
            })
            
            # Check if eligible for retry
            if (metadata.processing_attempts < self.max_processing_attempts and
                metadata.last_processed and
                datetime.now() > metadata.last_processed + timedelta(hours=self.retry_delay_hours)):
                retry_candidates.append(issue_id)
        
        return {
            'failed_by_error': failed_by_error,
            'retry_candidates': retry_candidates,
            'total_failed': len(self.failed_issues),
            'timestamp': datetime.now().isoformat()
        }
    
    def reset_issue_state(self, issue_id: str) -> bool:
        """
        Reset an issue's processing state for retry.
        
        Args:
            issue_id: Issue ID to reset
            
        Returns:
            bool: True if reset successful
        """
        if issue_id in self.discovered_issues:
            metadata = self.discovered_issues[issue_id]
            metadata.state = IssueState.DISCOVERED
            metadata.processing_attempts = 0
            metadata.last_processed = None
            metadata.error_message = None
            
            # Remove from failed tracking
            self.failed_issues.pop(issue_id, None)
            self.processed_issues.discard(issue_id)
            
            self.logger.info(f"Reset state for issue #{issue_id}")
            return True
        
        return False
    
    def export_issue_data(self, file_path: str):
        """
        Export issue processing data to file.
        
        Args:
            file_path: Path to export file
        """
        export_data = {
            'discovered_issues': {
                issue_id: asdict(metadata)
                for issue_id, metadata in self.discovered_issues.items()
            },
            'processed_issues': list(self.processed_issues),
            'statistics': self.get_processing_statistics(),
            'export_timestamp': datetime.now().isoformat()
        }
        
        # Convert datetime objects to ISO strings for JSON serialization
        def datetime_converter(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Object {obj} is not JSON serializable")
        
        with open(file_path, 'w') as f:
            json.dump(export_data, f, indent=2, default=datetime_converter)
        
        self.logger.info(f"Exported issue data to {file_path}")