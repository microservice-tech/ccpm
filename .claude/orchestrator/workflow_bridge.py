#!/usr/bin/env python3
"""
Workflow Bridge - High-level interface to workflow template execution

Provides orchestrator-specific workflow management capabilities on top of 
the Service Foundation's workflow executor. This includes priority management,
batch execution, dependency handling, and advanced coordination features.
"""

import logging
import asyncio
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, Set, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum, IntEnum
from queue import PriorityQueue, Empty
import json

from service_bridge import ServiceBridge, ExecutionRequest, ExecutionResult, BridgeStatus


class WorkflowPriority(IntEnum):
    """Priority levels for workflow execution (lower number = higher priority)"""
    CRITICAL = 1
    HIGH = 2 
    NORMAL = 3
    LOW = 4
    BACKGROUND = 5


class WorkflowState(Enum):
    """States of workflow execution"""
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRY_SCHEDULED = "retry_scheduled"


@dataclass
class WorkflowTask:
    """A workflow task with metadata and execution tracking"""
    issue_id: int
    issue_title: str
    issue_body: str
    repo_url: str
    priority: WorkflowPriority = WorkflowPriority.NORMAL
    state: WorkflowState = WorkflowState.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    queued_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Execution options
    force: bool = False
    skip_cleanup: bool = False
    custom_timeout: Optional[int] = None
    dry_run: bool = False
    
    # Retry configuration
    max_retries: int = 2
    retry_count: int = 0
    retry_delay_seconds: int = 60
    next_retry_at: Optional[datetime] = None
    
    # Dependencies and constraints
    depends_on: Set[int] = field(default_factory=set)
    blocks: Set[int] = field(default_factory=set)
    tags: Set[str] = field(default_factory=set)
    
    # Results and tracking
    result: Optional[ExecutionResult] = None
    error_history: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __lt__(self, other):
        """Priority queue comparison - lower priority value = higher urgency"""
        if self.priority != other.priority:
            return self.priority < other.priority
        # Secondary sort by creation time (older first)
        return self.created_at < other.created_at


@dataclass 
class ExecutionStats:
    """Statistics for workflow execution"""
    total_tasks: int = 0
    pending_tasks: int = 0
    queued_tasks: int = 0
    running_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    cancelled_tasks: int = 0
    
    average_execution_time: float = 0.0
    success_rate: float = 0.0
    tasks_per_hour: float = 0.0
    
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class WorkflowBridge:
    """
    High-level workflow bridge that manages complex workflow orchestration
    
    Features:
    - Priority-based task queuing
    - Concurrent execution with limits
    - Dependency resolution
    - Retry logic with backoff
    - Batch operations
    - Progress tracking and metrics
    """
    
    def __init__(self, 
                 service_bridge: ServiceBridge,
                 max_concurrent_executions: int = 3,
                 queue_size_limit: int = 1000):
        """
        Initialize the workflow bridge
        
        Args:
            service_bridge: Service bridge for low-level operations
            max_concurrent_executions: Maximum concurrent workflow executions
            queue_size_limit: Maximum number of queued tasks
        """
        self.logger = logging.getLogger(__name__)
        self.service_bridge = service_bridge
        self.max_concurrent_executions = max_concurrent_executions
        self.queue_size_limit = queue_size_limit
        
        # Task management
        self._task_queue = PriorityQueue(maxsize=queue_size_limit)
        self._tasks: Dict[int, WorkflowTask] = {}  # issue_id -> task
        self._running_tasks: Dict[int, WorkflowTask] = {}
        self._completed_tasks: Dict[int, WorkflowTask] = {}
        self._task_lock = threading.RLock()
        
        # Execution management
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent_executions, 
                                          thread_name_prefix="workflow")
        self._execution_futures: Dict[int, any] = {}  # issue_id -> future
        self._should_stop = threading.Event()
        
        # Statistics and monitoring
        self._stats = ExecutionStats()
        self._stats_lock = threading.Lock()
        
        # Background worker thread for queue processing
        self._worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self._worker_thread.start()
        
        # Retry scheduler thread
        self._retry_thread = threading.Thread(target=self._process_retries, daemon=True)
        self._retry_thread.start()
        
        self.logger.info(f"Workflow bridge initialized with {max_concurrent_executions} max concurrent executions")
    
    def submit_workflow(self, 
                       issue_id: int,
                       issue_title: str, 
                       issue_body: str,
                       repo_url: str,
                       priority: WorkflowPriority = WorkflowPriority.NORMAL,
                       **kwargs) -> bool:
        """
        Submit a workflow for execution
        
        Args:
            issue_id: GitHub issue ID
            issue_title: Issue title
            issue_body: Issue description
            repo_url: Repository URL
            priority: Execution priority
            **kwargs: Additional execution options
            
        Returns:
            True if task was queued successfully
        """
        with self._task_lock:
            # Check if already exists
            if issue_id in self._tasks:
                existing_task = self._tasks[issue_id]
                if existing_task.state in [WorkflowState.RUNNING, WorkflowState.QUEUED]:
                    self.logger.warning(f"Task #{issue_id} already exists in state: {existing_task.state}")
                    return False
            
            # Create new task
            task = WorkflowTask(
                issue_id=issue_id,
                issue_title=issue_title,
                issue_body=issue_body,
                repo_url=repo_url,
                priority=priority,
                **{k: v for k, v in kwargs.items() if hasattr(WorkflowTask, k)}
            )
            
            # Store task
            self._tasks[issue_id] = task
            
            # Check dependencies
            if task.depends_on:
                unmet_deps = self._get_unmet_dependencies(task)
                if unmet_deps:
                    self.logger.info(f"Task #{issue_id} waiting for dependencies: {unmet_deps}")
                    return True  # Task stored but not queued yet
            
            # Queue for execution
            return self._queue_task(task)
    
    def cancel_workflow(self, issue_id: int) -> bool:
        """
        Cancel a workflow execution
        
        Args:
            issue_id: Issue ID to cancel
            
        Returns:
            True if cancellation was successful
        """
        with self._task_lock:
            task = self._tasks.get(issue_id)
            if not task:
                self.logger.warning(f"Task #{issue_id} not found for cancellation")
                return False
            
            # Handle different states
            if task.state == WorkflowState.RUNNING:
                # Cancel running execution
                if self.service_bridge.cancel_execution(issue_id):
                    task.state = WorkflowState.CANCELLED
                    task.completed_at = datetime.now(timezone.utc)
                    self._move_to_completed(task)
                    return True
                return False
                
            elif task.state in [WorkflowState.PENDING, WorkflowState.QUEUED, WorkflowState.RETRY_SCHEDULED]:
                # Mark as cancelled
                task.state = WorkflowState.CANCELLED
                task.completed_at = datetime.now(timezone.utc)
                self._move_to_completed(task)
                return True
            
            else:
                self.logger.warning(f"Cannot cancel task #{issue_id} in state: {task.state}")
                return False
    
    def get_task_status(self, issue_id: int) -> Optional[Dict[str, Any]]:
        """
        Get status information for a specific task
        
        Args:
            issue_id: Issue ID to query
            
        Returns:
            Task status dictionary or None if not found
        """
        with self._task_lock:
            task = self._tasks.get(issue_id)
            if task:
                return self._task_to_dict(task)
            
            # Check completed tasks
            task = self._completed_tasks.get(issue_id)
            if task:
                return self._task_to_dict(task)
        
        return None
    
    def get_queue_status(self) -> Dict[str, Any]:
        """
        Get comprehensive queue status
        
        Returns:
            Dictionary with queue statistics and task lists
        """
        with self._task_lock:
            pending_tasks = [task for task in self._tasks.values() 
                           if task.state in [WorkflowState.PENDING, WorkflowState.QUEUED]]
            running_tasks = list(self._running_tasks.values())
            
            return {
                "queue_size": self._task_queue.qsize(),
                "max_queue_size": self.queue_size_limit,
                "pending_count": len(pending_tasks),
                "running_count": len(running_tasks),
                "max_concurrent": self.max_concurrent_executions,
                "pending_issues": [task.issue_id for task in pending_tasks],
                "running_issues": [task.issue_id for task in running_tasks],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    def get_execution_stats(self) -> ExecutionStats:
        """Get execution statistics"""
        with self._stats_lock:
            self._update_stats()
            return self._stats
    
    def batch_submit(self, workflows: List[Dict[str, Any]]) -> Dict[int, bool]:
        """
        Submit multiple workflows in batch
        
        Args:
            workflows: List of workflow specifications
            
        Returns:
            Dictionary mapping issue_id to success status
        """
        results = {}
        
        for workflow_spec in workflows:
            try:
                issue_id = workflow_spec["issue_id"]
                success = self.submit_workflow(**workflow_spec)
                results[issue_id] = success
                
                if success:
                    self.logger.info(f"Batch submitted workflow #{issue_id}")
                else:
                    self.logger.warning(f"Failed to batch submit workflow #{issue_id}")
                    
            except Exception as e:
                issue_id = workflow_spec.get("issue_id", "unknown")
                self.logger.error(f"Error batch submitting workflow #{issue_id}: {e}")
                results[issue_id] = False
        
        return results
    
    def set_task_priority(self, issue_id: int, priority: WorkflowPriority) -> bool:
        """
        Update task priority (only works for pending/queued tasks)
        
        Args:
            issue_id: Issue ID to update
            priority: New priority level
            
        Returns:
            True if priority was updated
        """
        with self._task_lock:
            task = self._tasks.get(issue_id)
            if not task:
                return False
            
            if task.state in [WorkflowState.PENDING, WorkflowState.QUEUED]:
                task.priority = priority
                self.logger.info(f"Updated priority for task #{issue_id} to {priority}")
                return True
            
            return False
    
    def add_task_dependency(self, issue_id: int, depends_on: int) -> bool:
        """
        Add a dependency between tasks
        
        Args:
            issue_id: Task that depends on another
            depends_on: Task that must complete first
            
        Returns:
            True if dependency was added
        """
        with self._task_lock:
            task = self._tasks.get(issue_id)
            depends_task = self._tasks.get(depends_on)
            
            if not task or not depends_task:
                return False
            
            # Add dependency
            task.depends_on.add(depends_on)
            depends_task.blocks.add(issue_id)
            
            self.logger.info(f"Added dependency: #{issue_id} depends on #{depends_on}")
            return True
    
    def pause_queue_processing(self) -> None:
        """Pause queue processing (stop accepting new tasks)"""
        self._should_stop.set()
        self.logger.info("Queue processing paused")
    
    def resume_queue_processing(self) -> None:
        """Resume queue processing"""
        self._should_stop.clear()
        self.logger.info("Queue processing resumed")
    
    def shutdown(self, wait: bool = True, timeout: Optional[float] = None) -> None:
        """
        Shutdown the workflow bridge
        
        Args:
            wait: Whether to wait for running tasks to complete
            timeout: Maximum time to wait for shutdown
        """
        self.logger.info("Shutting down workflow bridge...")
        
        # Stop accepting new tasks
        self.pause_queue_processing()
        
        # Cancel running tasks if not waiting
        if not wait:
            with self._task_lock:
                for task in list(self._running_tasks.values()):
                    self.cancel_workflow(task.issue_id)
        
        # Shutdown executor
        self._executor.shutdown(wait=wait, timeout=timeout)
        
        self.logger.info("Workflow bridge shutdown complete")
    
    def _queue_task(self, task: WorkflowTask) -> bool:
        """Queue a task for execution"""
        try:
            task.state = WorkflowState.QUEUED
            task.queued_at = datetime.now(timezone.utc)
            self._task_queue.put(task, block=False)
            
            self.logger.info(f"Queued task #{task.issue_id} with priority {task.priority}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to queue task #{task.issue_id}: {e}")
            task.state = WorkflowState.FAILED
            return False
    
    def _process_queue(self) -> None:
        """Background thread to process the task queue"""
        self.logger.info("Queue processor thread started")
        
        while not self._should_stop.is_set():
            try:
                # Check if we can start more tasks
                if len(self._running_tasks) >= self.max_concurrent_executions:
                    time.sleep(1)
                    continue
                
                # Get next task from queue (with timeout to check stop condition)
                try:
                    task = self._task_queue.get(timeout=1)
                except Empty:
                    continue
                
                # Double-check dependencies before execution
                unmet_deps = self._get_unmet_dependencies(task)
                if unmet_deps:
                    self.logger.info(f"Task #{task.issue_id} still has unmet dependencies: {unmet_deps}")
                    # Re-queue for later (this could lead to starvation, but simple for now)
                    self._task_queue.put(task)
                    time.sleep(5)  # Brief delay before retry
                    continue
                
                # Start execution
                self._start_task_execution(task)
                
            except Exception as e:
                self.logger.error(f"Error in queue processor: {e}")
                time.sleep(5)  # Brief delay on error
        
        self.logger.info("Queue processor thread stopped")
    
    def _process_retries(self) -> None:
        """Background thread to process retry scheduling"""
        self.logger.info("Retry processor thread started")
        
        while not self._should_stop.is_set():
            try:
                current_time = datetime.now(timezone.utc)
                
                with self._task_lock:
                    # Find tasks ready for retry
                    retry_ready = [
                        task for task in self._tasks.values()
                        if (task.state == WorkflowState.RETRY_SCHEDULED and 
                            task.next_retry_at and 
                            task.next_retry_at <= current_time)
                    ]
                
                # Process ready retries
                for task in retry_ready:
                    self.logger.info(f"Retrying task #{task.issue_id} (attempt {task.retry_count + 1})")
                    task.state = WorkflowState.PENDING
                    task.retry_count += 1
                    task.next_retry_at = None
                    
                    if self._queue_task(task):
                        self.logger.info(f"Retry queued for task #{task.issue_id}")
                    else:
                        self.logger.error(f"Failed to queue retry for task #{task.issue_id}")
                        task.state = WorkflowState.FAILED
                
                time.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                self.logger.error(f"Error in retry processor: {e}")
                time.sleep(60)  # Longer delay on error
        
        self.logger.info("Retry processor thread stopped")
    
    def _start_task_execution(self, task: WorkflowTask) -> None:
        """Start execution of a task"""
        with self._task_lock:
            # Move to running state
            task.state = WorkflowState.RUNNING
            task.started_at = datetime.now(timezone.utc)
            self._running_tasks[task.issue_id] = task
            
            # Create execution request
            request = ExecutionRequest(
                issue_id=task.issue_id,
                issue_title=task.issue_title,
                issue_body=task.issue_body,
                repo_url=task.repo_url,
                force=task.force,
                skip_cleanup=task.skip_cleanup,
                custom_timeout=task.custom_timeout,
                dry_run=task.dry_run
            )
            
            # Submit to executor
            future = self._executor.submit(self._execute_task, task, request)
            self._execution_futures[task.issue_id] = future
            
            self.logger.info(f"Started execution of task #{task.issue_id}")
    
    def _execute_task(self, task: WorkflowTask, request: ExecutionRequest) -> None:
        """Execute a single task (runs in executor thread)"""
        try:
            # Execute workflow through service bridge
            result = self.service_bridge.execute_workflow(request)
            
            with self._task_lock:
                task.result = result
                task.completed_at = datetime.now(timezone.utc)
                
                if result.success:
                    task.state = WorkflowState.COMPLETED
                    self.logger.info(f"Task #{task.issue_id} completed successfully")
                    
                    # Notify dependent tasks
                    self._check_and_queue_dependents(task.issue_id)
                    
                else:
                    # Handle failure - check if retry is needed
                    task.error_history.append(result.error_message or "Unknown error")
                    
                    if task.retry_count < task.max_retries:
                        # Schedule retry
                        task.state = WorkflowState.RETRY_SCHEDULED
                        task.next_retry_at = datetime.now(timezone.utc) + timedelta(
                            seconds=task.retry_delay_seconds * (2 ** task.retry_count)  # Exponential backoff
                        )
                        self.logger.info(f"Scheduled retry for task #{task.issue_id} at {task.next_retry_at}")
                    else:
                        # Max retries exceeded
                        task.state = WorkflowState.FAILED
                        self.logger.error(f"Task #{task.issue_id} failed after {task.retry_count} retries")
                
                # Move to completed tracking
                self._move_to_completed(task)
                
        except Exception as e:
            # Unexpected error during execution
            with self._task_lock:
                task.state = WorkflowState.FAILED
                task.completed_at = datetime.now(timezone.utc)
                task.error_history.append(f"Execution error: {str(e)}")
                self._move_to_completed(task)
                
            self.logger.error(f"Unexpected error executing task #{task.issue_id}: {e}")
    
    def _move_to_completed(self, task: WorkflowTask) -> None:
        """Move task from running to completed state"""
        # Remove from running tasks
        if task.issue_id in self._running_tasks:
            del self._running_tasks[task.issue_id]
        
        # Remove from execution futures
        if task.issue_id in self._execution_futures:
            del self._execution_futures[task.issue_id]
        
        # Add to completed tasks
        self._completed_tasks[task.issue_id] = task
        
        # Update statistics
        with self._stats_lock:
            self._update_stats()
    
    def _get_unmet_dependencies(self, task: WorkflowTask) -> Set[int]:
        """Get list of unmet dependencies for a task"""
        unmet = set()
        
        for dep_id in task.depends_on:
            dep_task = self._completed_tasks.get(dep_id)
            if not dep_task or dep_task.state != WorkflowState.COMPLETED:
                unmet.add(dep_id)
        
        return unmet
    
    def _check_and_queue_dependents(self, completed_issue_id: int) -> None:
        """Check for tasks waiting on a completed task and queue them if ready"""
        completed_task = self._completed_tasks.get(completed_issue_id)
        if not completed_task:
            return
        
        # Find tasks that were waiting on this one
        for dependent_id in completed_task.blocks:
            dependent_task = self._tasks.get(dependent_id)
            if (dependent_task and 
                dependent_task.state == WorkflowState.PENDING):
                
                # Check if all dependencies are now met
                unmet_deps = self._get_unmet_dependencies(dependent_task)
                if not unmet_deps:
                    self.logger.info(f"Queueing dependent task #{dependent_id}")
                    self._queue_task(dependent_task)
    
    def _task_to_dict(self, task: WorkflowTask) -> Dict[str, Any]:
        """Convert task to dictionary representation"""
        task_dict = asdict(task)
        
        # Convert datetime objects to ISO strings
        for key, value in task_dict.items():
            if isinstance(value, datetime):
                task_dict[key] = value.isoformat() if value else None
            elif isinstance(value, set):
                task_dict[key] = list(value)
            elif isinstance(value, (WorkflowPriority, WorkflowState)):
                task_dict[key] = value.value if hasattr(value, 'value') else str(value)
        
        return task_dict
    
    def _update_stats(self) -> None:
        """Update execution statistics"""
        all_tasks = list(self._tasks.values()) + list(self._completed_tasks.values())
        
        if not all_tasks:
            return
        
        # Count by state
        state_counts = {}
        for state in WorkflowState:
            state_counts[state] = sum(1 for task in all_tasks if task.state == state)
        
        # Update stats
        self._stats.total_tasks = len(all_tasks)
        self._stats.pending_tasks = state_counts[WorkflowState.PENDING]
        self._stats.queued_tasks = state_counts[WorkflowState.QUEUED] 
        self._stats.running_tasks = state_counts[WorkflowState.RUNNING]
        self._stats.completed_tasks = state_counts[WorkflowState.COMPLETED]
        self._stats.failed_tasks = state_counts[WorkflowState.FAILED]
        self._stats.cancelled_tasks = state_counts[WorkflowState.CANCELLED]
        
        # Calculate success rate
        total_finished = self._stats.completed_tasks + self._stats.failed_tasks + self._stats.cancelled_tasks
        if total_finished > 0:
            self._stats.success_rate = self._stats.completed_tasks / total_finished * 100
        
        # Calculate average execution time
        completed_with_times = [
            task for task in all_tasks 
            if task.state == WorkflowState.COMPLETED and task.started_at and task.completed_at
        ]
        
        if completed_with_times:
            total_time = sum(
                (task.completed_at - task.started_at).total_seconds() 
                for task in completed_with_times
            )
            self._stats.average_execution_time = total_time / len(completed_with_times)
        
        # Calculate tasks per hour (based on last 24 hours)
        recent_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        recent_completed = [
            task for task in all_tasks
            if (task.state == WorkflowState.COMPLETED and 
                task.completed_at and task.completed_at > recent_cutoff)
        ]
        self._stats.tasks_per_hour = len(recent_completed) / 24.0
        
        self._stats.last_updated = datetime.now(timezone.utc)