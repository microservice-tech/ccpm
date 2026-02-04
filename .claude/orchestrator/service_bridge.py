#!/usr/bin/env python3
"""
Service Bridge - Integration with Service Foundation Components

Provides a clean interface between the orchestrator and the Service Foundation
components (service_manager.py, workflow_executor.py, etc.)
"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import json

# Service Foundation imports - relative to the services directory
import sys
import os
sys.path.append(str(Path(__file__).parent.parent / "services"))

from service_manager import ServiceManager, ServiceStatus
from workflow_executor import WorkflowExecutor, WorkflowExecution
from config_manager import ConfigManager
from github_client import GitHubClient
from issue_processor import IssueProcessor


class BridgeStatus(Enum):
    """Status of the bridge components"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class ServiceHealth:
    """Health information for a service component"""
    name: str
    status: BridgeStatus
    last_check: datetime
    details: Dict[str, Any]
    error_message: Optional[str] = None


@dataclass
class ExecutionRequest:
    """Request for workflow execution"""
    issue_id: int
    issue_title: str
    issue_body: str
    repo_url: str
    priority: int = 5  # 1-10, higher is more urgent
    force: bool = False
    skip_cleanup: bool = False
    custom_timeout: Optional[int] = None
    dry_run: bool = False


@dataclass
class ExecutionResult:
    """Result of workflow execution"""
    success: bool
    issue_id: int
    pr_url: Optional[str] = None
    error_message: Optional[str] = None
    execution_time_seconds: Optional[int] = None
    logs: Optional[Dict[str, str]] = None


class ServiceBridge:
    """
    Bridge between orchestrator and Service Foundation components
    
    This class provides a unified interface for:
    - Service lifecycle management
    - Workflow execution coordination
    - Health monitoring
    - Configuration management
    """
    
    def __init__(self, config: Optional[ConfigManager] = None):
        """
        Initialize the service bridge
        
        Args:
            config: Configuration manager instance. Creates new one if None.
        """
        self.logger = logging.getLogger(__name__)
        self.config = config or ConfigManager()
        
        # Initialize service foundation components
        self.service_manager = ServiceManager(self.config)
        self.github_client = GitHubClient(self.config)
        self.issue_processor = IssueProcessor(self.config, self.github_client)
        
        # Initialize workflow executor
        script_path = Path(self.config.get("orchestrator.workflow_script_path", 
                                         str(Path(__file__).parent.parent / "scripts" / "workflow-template.sh")))
        log_directory = Path(self.config.get("orchestrator.log_directory", "/tmp/claude-flow-logs"))
        default_timeout = self.config.get("orchestrator.default_timeout_seconds", 3600)
        
        self.workflow_executor = WorkflowExecutor(
            script_path=script_path,
            log_directory=log_directory,
            timeout=default_timeout,
            environment=self._get_execution_environment()
        )
        
        # Health monitoring
        self._last_health_check = None
        self._health_cache_duration = self.config.get("orchestrator.health_cache_seconds", 30)
        self._component_health: Dict[str, ServiceHealth] = {}
        
        self.logger.info("Service bridge initialized successfully")
    
    def start_services(self) -> bool:
        """
        Start all required services
        
        Returns:
            True if all services started successfully
        """
        self.logger.info("Starting services through bridge...")
        
        try:
            # Start the main Claude Flow service
            if not self.service_manager.start(wait_for_active=True, timeout=60):
                self.logger.error("Failed to start Claude Flow service")
                return False
            
            self.logger.info("Services started successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Error starting services: {e}")
            return False
    
    def stop_services(self, graceful: bool = True) -> bool:
        """
        Stop all services
        
        Args:
            graceful: Use graceful shutdown vs force stop
            
        Returns:
            True if services stopped successfully
        """
        self.logger.info("Stopping services through bridge...")
        
        try:
            # Cancel any active workflow executions
            active_executions = self.workflow_executor.get_active_executions()
            for issue_id in active_executions.keys():
                self.logger.info(f"Cancelling active execution for issue #{issue_id}")
                self.workflow_executor.cancel_execution(issue_id)
            
            # Stop the main Claude Flow service
            if not self.service_manager.stop(graceful=graceful, timeout=30):
                self.logger.warning("Failed to gracefully stop Claude Flow service")
                return False
            
            self.logger.info("Services stopped successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Error stopping services: {e}")
            return False
    
    def restart_services(self) -> bool:
        """
        Restart all services
        
        Returns:
            True if services restarted successfully
        """
        self.logger.info("Restarting services through bridge...")
        
        if not self.stop_services():
            self.logger.warning("Failed to stop services cleanly, attempting restart anyway")
        
        time.sleep(5)  # Brief pause between stop and start
        
        return self.start_services()
    
    def execute_workflow(self, request: ExecutionRequest) -> ExecutionResult:
        """
        Execute a workflow for the given request
        
        Args:
            request: Workflow execution request
            
        Returns:
            Execution result with success status and details
        """
        self.logger.info(f"Executing workflow for issue #{request.issue_id}: {request.issue_title}")
        
        start_time = time.time()
        
        try:
            # Execute the workflow
            success, pr_url, error_message = self.workflow_executor.execute_workflow(
                issue_id=request.issue_id,
                issue_title=request.issue_title,
                repo_url=request.repo_url,
                force=request.force,
                skip_cleanup=request.skip_cleanup,
                custom_timeout=request.custom_timeout,
                dry_run=request.dry_run
            )
            
            execution_time = int(time.time() - start_time)
            
            # Get execution logs
            logs = self.workflow_executor.get_execution_logs(request.issue_id)
            
            return ExecutionResult(
                success=success,
                issue_id=request.issue_id,
                pr_url=pr_url,
                error_message=error_message,
                execution_time_seconds=execution_time,
                logs=logs
            )
            
        except Exception as e:
            execution_time = int(time.time() - start_time)
            error_message = f"Unexpected error during workflow execution: {str(e)}"
            self.logger.error(error_message, exc_info=True)
            
            return ExecutionResult(
                success=False,
                issue_id=request.issue_id,
                error_message=error_message,
                execution_time_seconds=execution_time
            )
    
    def get_active_executions(self) -> Dict[int, WorkflowExecution]:
        """Get currently active workflow executions"""
        return self.workflow_executor.get_active_executions()
    
    def cancel_execution(self, issue_id: int) -> bool:
        """Cancel an active workflow execution"""
        return self.workflow_executor.cancel_execution(issue_id)
    
    def get_service_health(self, force_refresh: bool = False) -> Dict[str, ServiceHealth]:
        """
        Get health status of all service components
        
        Args:
            force_refresh: Force refresh of health data
            
        Returns:
            Dictionary mapping component names to health information
        """
        current_time = datetime.now(timezone.utc)
        
        # Use cached data if available and recent
        if (not force_refresh and 
            self._last_health_check and 
            (current_time - self._last_health_check).total_seconds() < self._health_cache_duration):
            return self._component_health.copy()
        
        # Refresh health data
        self._component_health.clear()
        
        # Check Claude Flow service health
        try:
            service_status = self.service_manager.get_status()
            is_healthy = self.service_manager.is_healthy()
            
            self._component_health["claude_flow_service"] = ServiceHealth(
                name="Claude Flow Service",
                status=BridgeStatus.HEALTHY if is_healthy else BridgeStatus.UNHEALTHY,
                last_check=current_time,
                details=service_status
            )
        except Exception as e:
            self._component_health["claude_flow_service"] = ServiceHealth(
                name="Claude Flow Service",
                status=BridgeStatus.UNKNOWN,
                last_check=current_time,
                details={},
                error_message=str(e)
            )
        
        # Check workflow executor health
        try:
            active_executions = self.workflow_executor.get_active_executions()
            
            # Determine health based on active executions and resource usage
            status = BridgeStatus.HEALTHY
            details = {
                "active_executions": len(active_executions),
                "script_path": str(self.workflow_executor.script_path),
                "log_directory": str(self.workflow_executor.log_directory),
                "timeout": self.workflow_executor.timeout
            }
            
            if len(active_executions) > 10:  # Arbitrary threshold
                status = BridgeStatus.DEGRADED
                details["warning"] = "High number of active executions"
            
            self._component_health["workflow_executor"] = ServiceHealth(
                name="Workflow Executor",
                status=status,
                last_check=current_time,
                details=details
            )
        except Exception as e:
            self._component_health["workflow_executor"] = ServiceHealth(
                name="Workflow Executor",
                status=BridgeStatus.UNKNOWN,
                last_check=current_time,
                details={},
                error_message=str(e)
            )
        
        # Check GitHub client health
        try:
            # Test GitHub connectivity
            github_status = self.github_client.test_connection()
            
            self._component_health["github_client"] = ServiceHealth(
                name="GitHub Client",
                status=BridgeStatus.HEALTHY if github_status else BridgeStatus.UNHEALTHY,
                last_check=current_time,
                details={"connection_test": github_status}
            )
        except Exception as e:
            self._component_health["github_client"] = ServiceHealth(
                name="GitHub Client",
                status=BridgeStatus.UNKNOWN,
                last_check=current_time,
                details={},
                error_message=str(e)
            )
        
        self._last_health_check = current_time
        return self._component_health.copy()
    
    def get_overall_health(self) -> Tuple[BridgeStatus, Dict[str, Any]]:
        """
        Get overall health status of the service bridge
        
        Returns:
            Tuple of (overall_status, summary_details)
        """
        component_health = self.get_service_health()
        
        # Determine overall status
        healthy_count = sum(1 for h in component_health.values() if h.status == BridgeStatus.HEALTHY)
        degraded_count = sum(1 for h in component_health.values() if h.status == BridgeStatus.DEGRADED)
        unhealthy_count = sum(1 for h in component_health.values() if h.status == BridgeStatus.UNHEALTHY)
        unknown_count = sum(1 for h in component_health.values() if h.status == BridgeStatus.UNKNOWN)
        
        total_components = len(component_health)
        
        # Overall status logic
        if unhealthy_count > 0 or unknown_count > total_components // 2:
            overall_status = BridgeStatus.UNHEALTHY
        elif degraded_count > 0 or unknown_count > 0:
            overall_status = BridgeStatus.DEGRADED
        else:
            overall_status = BridgeStatus.HEALTHY
        
        summary = {
            "overall_status": overall_status.value,
            "total_components": total_components,
            "healthy": healthy_count,
            "degraded": degraded_count,
            "unhealthy": unhealthy_count,
            "unknown": unknown_count,
            "component_details": {name: asdict(health) for name, health in component_health.items()},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return overall_status, summary
    
    def cleanup_old_logs(self, max_age_days: int = 7) -> int:
        """
        Clean up old execution logs
        
        Args:
            max_age_days: Maximum age of logs to keep
            
        Returns:
            Number of log files cleaned up
        """
        return self.workflow_executor.cleanup_logs(max_age_days)
    
    def reload_configuration(self) -> bool:
        """
        Reload configuration for all components
        
        Returns:
            True if configuration reloaded successfully
        """
        try:
            self.config.reload()
            return self.service_manager.reload_config()
        except Exception as e:
            self.logger.error(f"Failed to reload configuration: {e}")
            return False
    
    def get_execution_logs(self, issue_id: int) -> Optional[Dict[str, str]]:
        """Get execution logs for a specific issue"""
        return self.workflow_executor.get_execution_logs(issue_id)
    
    def get_service_metrics(self) -> Dict[str, Any]:
        """
        Get comprehensive metrics about the service bridge
        
        Returns:
            Dictionary with various metrics and statistics
        """
        try:
            overall_status, health_summary = self.get_overall_health()
            active_executions = self.get_active_executions()
            service_status = self.service_manager.get_status()
            
            return {
                "bridge_status": overall_status.value,
                "health_summary": health_summary,
                "active_executions": {
                    "count": len(active_executions),
                    "issues": list(active_executions.keys())
                },
                "service_status": service_status,
                "config_valid": self.config.is_valid(),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            self.logger.error(f"Failed to get service metrics: {e}")
            return {
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    def _get_execution_environment(self) -> Dict[str, str]:
        """Get environment variables for workflow execution"""
        env = {}
        
        # Add required environment variables
        if anthropic_key := self.config.get("claude_flow.anthropic_api_key"):
            env["ANTHROPIC_API_KEY"] = anthropic_key
        
        if github_token := self.config.get("github.token"):
            env["GITHUB_TOKEN"] = github_token
        
        # Add optional environment variables
        if claude_flow_version := self.config.get("claude_flow.version"):
            env["CLAUDE_FLOW_VERSION"] = claude_flow_version
        
        return env