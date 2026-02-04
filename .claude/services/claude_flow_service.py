#!/usr/bin/env python3
"""
Main Claude Flow service with GitHub polling and workflow orchestration.
This service continuously polls GitHub for issues labeled "ready-for-implementation"
and orchestrates their automated implementation using Claude Flow.
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

try:
    from .github_client import GitHubClient, GitHubRateLimitError, GitHubAuthenticationError
    from .issue_processor import IssueProcessor
    from .workflow_executor import WorkflowExecutor
except ImportError:
    from github_client import GitHubClient, GitHubRateLimitError, GitHubAuthenticationError
    from issue_processor import IssueProcessor
    from workflow_executor import WorkflowExecutor


@dataclass
class ServiceConfiguration:
    """Configuration for the Claude Flow service."""
    github_token: str
    github_base_url: str = "https://api.github.com"
    repositories: List[Dict[str, str]] = None
    polling_interval: int = 300  # 5 minutes
    max_concurrent_workers: int = 3
    workflow_script_path: str = "/home/nic/Documents/development/epic-claude-flow-integration/.claude/scripts/workflow-template.sh"
    log_level: str = "INFO"
    log_file: Optional[str] = None
    state_file: str = "/tmp/claude-flow-service-state.json"
    pid_file: str = "/tmp/claude-flow-service.pid"
    workflow_timeout: int = 3600
    issue_label: str = "ready-for-implementation"
    exclude_labels: List[str] = None
    environment_variables: Dict[str, str] = None
    
    def __post_init__(self):
        """Initialize default values after creation."""
        if self.repositories is None:
            self.repositories = []
        if self.exclude_labels is None:
            self.exclude_labels = ["claude-flow-completed", "claude-flow-processing"]
        if self.environment_variables is None:
            self.environment_variables = {}
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'ServiceConfiguration':
        """Create configuration from dictionary."""
        return cls(**data)
    
    @classmethod
    def from_json_file(cls, file_path: Path) -> 'ServiceConfiguration':
        """Load configuration from JSON file."""
        with open(file_path, 'r') as f:
            data = json.load(f)
        return cls.from_dict(data)
    
    @classmethod
    def from_environment(cls) -> 'ServiceConfiguration':
        """Load configuration from environment variables."""
        config = {
            'github_token': os.getenv('GITHUB_TOKEN', ''),
            'github_base_url': os.getenv('GITHUB_BASE_URL', 'https://api.github.com'),
            'polling_interval': int(os.getenv('POLLING_INTERVAL', '300')),
            'max_concurrent_workers': int(os.getenv('MAX_CONCURRENT_WORKERS', '3')),
            'workflow_script_path': os.getenv('WORKFLOW_SCRIPT_PATH', 
                '/home/nic/Documents/development/epic-claude-flow-integration/.claude/scripts/workflow-template.sh'),
            'log_level': os.getenv('LOG_LEVEL', 'INFO'),
            'log_file': os.getenv('LOG_FILE'),
            'state_file': os.getenv('STATE_FILE', '/tmp/claude-flow-service-state.json'),
            'pid_file': os.getenv('PID_FILE', '/tmp/claude-flow-service.pid'),
            'workflow_timeout': int(os.getenv('WORKFLOW_TIMEOUT', '3600')),
            'issue_label': os.getenv('ISSUE_LABEL', 'ready-for-implementation'),
        }
        
        # Parse repository list from environment
        repos_json = os.getenv('REPOSITORIES', '[]')
        try:
            config['repositories'] = json.loads(repos_json)
        except json.JSONDecodeError:
            config['repositories'] = []
        
        # Parse exclude labels
        exclude_labels = os.getenv('EXCLUDE_LABELS', 'claude-flow-completed,claude-flow-processing')
        config['exclude_labels'] = [label.strip() for label in exclude_labels.split(',') if label.strip()]
        
        # Parse environment variables for workflow execution
        env_vars = {}
        for key, value in os.environ.items():
            if key.startswith('WORKFLOW_'):
                env_key = key[9:]  # Remove 'WORKFLOW_' prefix
                env_vars[env_key] = value
        config['environment_variables'] = env_vars
        
        return cls.from_dict(config)


class ClaudeFlowService:
    """Main service class for Claude Flow automation."""
    
    def __init__(self, config: ServiceConfiguration):
        """
        Initialize the Claude Flow service.
        
        Args:
            config: Service configuration
        """
        self.config = config
        self.logger = self._setup_logging()
        
        # Service state
        self.running = False
        self.shutdown_event = threading.Event()
        self.polling_thread: Optional[threading.Thread] = None
        
        # Initialize components
        self.github_client: Optional[GitHubClient] = None
        self.workflow_executor: Optional[WorkflowExecutor] = None
        self.issue_processor: Optional[IssueProcessor] = None
        
        # Statistics
        self.stats = {
            'service_started': None,
            'last_poll': None,
            'polls_completed': 0,
            'polls_failed': 0,
            'issues_discovered': 0,
            'issues_processed': 0,
            'issues_failed': 0,
            'total_prs_created': 0
        }
        
        self.logger.info("Claude Flow service initialized")
    
    def _setup_logging(self) -> logging.Logger:
        """Setup logging configuration."""
        logger = logging.getLogger(__name__)
        logger.setLevel(getattr(logging, self.config.log_level.upper()))
        
        # Remove existing handlers
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        # File handler if specified
        if self.config.log_file:
            log_path = Path(self.config.log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            file_handler = logging.FileHandler(log_path)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        
        return logger
    
    def start(self) -> None:
        """Start the Claude Flow service."""
        if self.running:
            self.logger.warning("Service is already running")
            return
        
        self.logger.info("Starting Claude Flow service...")
        
        try:
            # Write PID file
            self._write_pid_file()
            
            # Validate configuration
            self._validate_configuration()
            
            # Initialize components
            self._initialize_components()
            
            # Set up signal handlers
            self._setup_signal_handlers()
            
            # Start components
            self.issue_processor.start()
            
            # Mark as running
            self.running = True
            self.stats['service_started'] = datetime.now(timezone.utc)
            
            # Start polling thread
            self.polling_thread = threading.Thread(
                target=self._polling_loop,
                name="GitHubPollingThread",
                daemon=False
            )
            self.polling_thread.start()
            
            self.logger.info("Claude Flow service started successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to start service: {e}")
            self.stop()
            raise
    
    def stop(self, timeout: int = 30) -> None:
        """Stop the Claude Flow service."""
        if not self.running:
            return
        
        self.logger.info("Stopping Claude Flow service...")
        
        # Signal shutdown
        self.running = False
        self.shutdown_event.set()
        
        # Wait for polling thread to finish
        if self.polling_thread and self.polling_thread.is_alive():
            self.polling_thread.join(timeout=timeout)
        
        # Stop components
        if self.issue_processor:
            self.issue_processor.stop(timeout=timeout)
        
        # Clean up PID file
        self._remove_pid_file()
        
        self.logger.info("Claude Flow service stopped")
    
    def _validate_configuration(self) -> None:
        """Validate service configuration."""
        if not self.config.github_token:
            raise ValueError("GitHub token is required")
        
        if not self.config.repositories:
            raise ValueError("At least one repository must be configured")
        
        script_path = Path(self.config.workflow_script_path)
        if not script_path.exists():
            raise FileNotFoundError(f"Workflow script not found: {script_path}")
        
        if not os.access(script_path, os.X_OK):
            raise PermissionError(f"Workflow script not executable: {script_path}")
        
        # Validate required environment variables
        if 'ANTHROPIC_API_KEY' not in os.environ:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required")
        
        self.logger.info("Configuration validation passed")
    
    def _initialize_components(self) -> None:
        """Initialize service components."""
        # Initialize GitHub client
        self.github_client = GitHubClient(
            token=self.config.github_token,
            base_url=self.config.github_base_url
        )
        
        # Test GitHub authentication
        auth_success, auth_message = self.github_client.test_authentication()
        if not auth_success:
            raise GitHubAuthenticationError(f"GitHub authentication failed: {auth_message}")
        
        self.logger.info(f"GitHub authentication successful: {auth_message}")
        
        # Initialize workflow executor
        workflow_env = os.environ.copy()
        workflow_env.update(self.config.environment_variables)
        
        self.workflow_executor = WorkflowExecutor(
            script_path=Path(self.config.workflow_script_path),
            timeout=self.config.workflow_timeout,
            environment=workflow_env
        )
        
        # Initialize issue processor
        self.issue_processor = IssueProcessor(
            github_client=self.github_client,
            workflow_executor=self.workflow_executor,
            max_workers=self.config.max_concurrent_workers,
            state_file=Path(self.config.state_file)
        )
        
        self.logger.info("Components initialized successfully")
    
    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            signal_name = signal.Signals(signum).name
            self.logger.info(f"Received {signal_name}, initiating graceful shutdown...")
            self.stop()
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        if hasattr(signal, 'SIGHUP'):
            def reload_handler(signum, frame):
                self.logger.info("Received SIGHUP, reloading configuration...")
                # Note: Configuration reloading would be implemented here
                self.logger.info("Configuration reload not implemented yet")
            
            signal.signal(signal.SIGHUP, reload_handler)
    
    def _write_pid_file(self) -> None:
        """Write process ID to PID file."""
        pid_path = Path(self.config.pid_file)
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(pid_path, 'w') as f:
            f.write(str(os.getpid()))
        
        self.logger.debug(f"PID file written: {pid_path}")
    
    def _remove_pid_file(self) -> None:
        """Remove PID file."""
        try:
            pid_path = Path(self.config.pid_file)
            if pid_path.exists():
                pid_path.unlink()
        except Exception as e:
            self.logger.warning(f"Failed to remove PID file: {e}")
    
    def _polling_loop(self) -> None:
        """Main polling loop that checks GitHub for issues."""
        self.logger.info(f"Starting polling loop (interval: {self.config.polling_interval}s)")
        
        while not self.shutdown_event.wait(self.config.polling_interval):
            try:
                self._poll_repositories()
                self.stats['polls_completed'] += 1
                self.stats['last_poll'] = datetime.now(timezone.utc)
                
            except Exception as e:
                self.logger.error(f"Polling cycle failed: {e}", exc_info=True)
                self.stats['polls_failed'] += 1
                
                # If it's a rate limit error, wait longer
                if isinstance(e, GitHubRateLimitError):
                    self.logger.info("Waiting additional time due to rate limit...")
                    self.shutdown_event.wait(min(300, self.config.polling_interval))
        
        self.logger.info("Polling loop terminated")
    
    def _poll_repositories(self) -> None:
        """Poll all configured repositories for issues."""
        for repo_config in self.config.repositories:
            if self.shutdown_event.is_set():
                break
            
            try:
                self._poll_repository(repo_config)
            except Exception as e:
                self.logger.error(
                    f"Failed to poll repository {repo_config}: {e}",
                    exc_info=True
                )
    
    def _poll_repository(self, repo_config: Dict[str, str]) -> None:
        """Poll a specific repository for issues."""
        owner = repo_config['owner']
        repo = repo_config['repo']
        repo_url = repo_config['url']
        
        self.logger.debug(f"Polling {owner}/{repo} for issues")
        
        try:
            # Get issues with the target label
            issues = self.github_client.get_issues(
                owner=owner,
                repo=repo,
                labels=[self.config.issue_label],
                state='open'
            )
            
            # Filter out issues that already have exclude labels
            eligible_issues = []
            for issue in issues:
                # Skip pull requests
                if 'pull_request' in issue:
                    continue
                
                issue_labels = [label['name'] for label in issue.get('labels', [])]
                
                # Skip if has exclude labels
                if any(label in issue_labels for label in self.config.exclude_labels):
                    continue
                
                eligible_issues.append(issue)
            
            if eligible_issues:
                self.logger.info(f"Found {len(eligible_issues)} eligible issues in {owner}/{repo}")
                self.stats['issues_discovered'] += len(eligible_issues)
                
                # Process issues
                processing_states = self.issue_processor.process_issues(
                    owner=owner,
                    repo=repo,
                    repo_url=repo_url,
                    label_filter=[self.config.issue_label],
                    exclude_labels=self.config.exclude_labels
                )
                
                # Update statistics
                for state in processing_states:
                    if state.status.value in ['completed']:
                        self.stats['issues_processed'] += 1
                        if state.pr_url:
                            self.stats['total_prs_created'] += 1
                    elif state.status.value == 'failed':
                        self.stats['issues_failed'] += 1
            
        except GitHubRateLimitError:
            self.logger.warning(f"Rate limit exceeded while polling {owner}/{repo}")
            raise
        except Exception as e:
            self.logger.error(f"Error polling {owner}/{repo}: {e}")
            raise
    
    def get_status(self) -> Dict:
        """Get current service status."""
        status = {
            'running': self.running,
            'configuration': {
                'polling_interval': self.config.polling_interval,
                'max_concurrent_workers': self.config.max_concurrent_workers,
                'repositories': len(self.config.repositories),
                'issue_label': self.config.issue_label,
                'exclude_labels': self.config.exclude_labels
            },
            'statistics': self.stats.copy(),
            'active_executions': {}
        }
        
        # Add execution info if components are initialized
        if self.workflow_executor:
            status['active_executions'] = {
                issue_id: {
                    'issue_id': exec_info.issue_id,
                    'issue_title': exec_info.issue_title,
                    'started_at': exec_info.started_at.isoformat() if exec_info.started_at else None,
                    'process_id': exec_info.process_id
                }
                for issue_id, exec_info in self.workflow_executor.get_active_executions().items()
            }
        
        if self.issue_processor:
            processing_states = self.issue_processor.get_processing_status()
            status['processing_states'] = {
                issue_id: state.to_dict()
                for issue_id, state in processing_states.items()
            }
        
        return status
    
    def run(self) -> None:
        """Run the service (blocking call)."""
        try:
            self.start()
            
            # Keep the main thread alive
            while self.running:
                time.sleep(1)
                
        except KeyboardInterrupt:
            self.logger.info("Received keyboard interrupt")
        except Exception as e:
            self.logger.error(f"Service error: {e}", exc_info=True)
        finally:
            self.stop()


def main():
    """Main entry point for the service."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Claude Flow Service - Automated GitHub issue implementation"
    )
    parser.add_argument(
        '--config',
        type=Path,
        help='Configuration file path (JSON format)'
    )
    parser.add_argument(
        '--daemon',
        action='store_true',
        help='Run as daemon process'
    )
    parser.add_argument(
        '--status',
        action='store_true',
        help='Show service status and exit'
    )
    
    args = parser.parse_args()
    
    # Load configuration
    if args.config and args.config.exists():
        config = ServiceConfiguration.from_json_file(args.config)
    else:
        config = ServiceConfiguration.from_environment()
    
    # Create and run service
    service = ClaudeFlowService(config)
    
    if args.status:
        status = service.get_status()
        print(json.dumps(status, indent=2, default=str))
        return
    
    if args.daemon:
        # Basic daemonization (full implementation would use python-daemon library)
        print(f"Starting Claude Flow service as daemon (PID file: {config.pid_file})")
    
    service.run()


if __name__ == '__main__':
    main()