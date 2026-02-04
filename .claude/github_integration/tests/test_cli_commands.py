#!/usr/bin/env python3
"""
Unit tests for GitHubPMCommands class.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch, MagicMock, mock_open
from pathlib import Path
from datetime import datetime

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.config_manager import ConfigManager
from github_integration.github_service import GitHubService, PRCreationResult
from github_integration.cli.wrapper import GitHubCLIWrapper, PRInfo, IssueInfo
from github_integration.cli.commands import GitHubPMCommands, TaskInfo


class TestGitHubPMCommands(unittest.TestCase):
    """Test cases for GitHubPMCommands."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_path = Path(self.temp_dir)
        
        # Create test directory structure
        self.pm_scripts_dir = self.workspace_path / ".claude" / "scripts" / "pm"
        self.epics_dir = self.workspace_path / ".claude" / "epics"
        self.pm_scripts_dir.mkdir(parents=True, exist_ok=True)
        self.epics_dir.mkdir(parents=True, exist_ok=True)
        
        # Create test epic directory with task
        self.test_epic_dir = self.epics_dir / "test-epic"
        self.test_epic_dir.mkdir(exist_ok=True)
        
        # Mock configuration
        self.mock_config = Mock(spec=ConfigManager)
        
        # Mock services
        self.mock_github_service = Mock(spec=GitHubService)
        self.mock_cli_wrapper = Mock(spec=GitHubCLIWrapper)
        self.mock_cli_wrapper.is_authenticated.return_value = True
        
        # Patch Path.cwd to return our temp directory
        with patch('pathlib.Path.cwd', return_value=self.workspace_path):
            with patch('github_integration.cli.commands.GitHubService') as mock_gh_service:
                with patch('github_integration.cli.commands.GitHubCLIWrapper') as mock_cli:
                    mock_gh_service.return_value = self.mock_github_service
                    mock_cli.return_value = self.mock_cli_wrapper
                    
                    self.pm_commands = GitHubPMCommands(self.mock_config)
    
    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def _create_test_task_file(self, task_id: str, epic_name: str, **kwargs):
        """Create a test task file."""
        epic_dir = self.epics_dir / epic_name
        epic_dir.mkdir(exist_ok=True)
        
        task_file = epic_dir / f"{task_id}.md"
        
        # Default task content
        frontmatter = {
            'status': 'open',
            'assignee': 'testuser',
            'priority': 'high',
            **kwargs
        }
        
        content_lines = ["---"]
        for key, value in frontmatter.items():
            if value is not None:
                content_lines.append(f"{key}: {value}")
        content_lines.extend([
            "---",
            "",
            f"# Task {task_id}: Test Task",
            "",
            "This is a test task description."
        ])
        
        task_file.write_text("\n".join(content_lines))
        return task_file
    
    def test_init_success(self):
        """Test successful initialization."""
        self.assertIsNotNone(self.pm_commands.github_service)
        self.assertIsNotNone(self.pm_commands.cli_wrapper)
        self.assertEqual(self.pm_commands.pm_scripts_dir, self.pm_scripts_dir)
        self.assertEqual(self.pm_commands.epics_dir, self.epics_dir)
    
    def test_init_missing_pm_scripts(self):
        """Test initialization with missing PM scripts directory."""
        # Remove PM scripts directory
        import shutil
        shutil.rmtree(self.pm_scripts_dir)
        
        with patch('pathlib.Path.cwd', return_value=self.workspace_path):
            with patch('github_integration.cli.commands.GitHubService'):
                with patch('github_integration.cli.commands.GitHubCLIWrapper'):
                    with self.assertRaises(ValueError) as cm:
                        GitHubPMCommands(self.mock_config)
                    
                    self.assertIn("PM scripts directory not found", str(cm.exception))
    
    def test_init_not_authenticated(self):
        """Test initialization with unauthenticated GitHub CLI."""
        mock_cli_wrapper = Mock(spec=GitHubCLIWrapper)
        mock_cli_wrapper.is_authenticated.return_value = False
        
        with patch('pathlib.Path.cwd', return_value=self.workspace_path):
            with patch('github_integration.cli.commands.GitHubService'):
                with patch('github_integration.cli.commands.GitHubCLIWrapper') as mock_cli:
                    mock_cli.return_value = mock_cli_wrapper
                    
                    with self.assertRaises(ValueError) as cm:
                        GitHubPMCommands(self.mock_config)
                    
                    self.assertIn("GitHub CLI not authenticated", str(cm.exception))
    
    def test_task_to_pr_success(self):
        """Test successful task to PR conversion."""
        # Create test task
        task_file = self._create_test_task_file("123", "test-epic", branch="feature/test-123")
        
        # Mock GitHub service
        pr_result = PRCreationResult(
            success=True,
            pr_number=456,
            pr_url="https://github.com/owner/repo/pull/456",
            quality_gate_failures=[]
        )
        self.mock_github_service.create_autonomous_pr.return_value = pr_result
        
        # Mock CLI wrapper
        pr_info = PRInfo(
            number=456,
            title="Task #123: Test Task",
            state="open",
            author="testuser",
            base_ref="main",
            head_ref="feature/test-123",
            url="https://github.com/owner/repo/pull/456",
            draft=False,
            mergeable="MERGEABLE",
            created_at="2023-01-01T00:00:00Z",
            updated_at="2023-01-01T00:00:00Z"
        )
        self.mock_cli_wrapper.get_pull_request_info.return_value = pr_info
        
        # Execute
        result = self.pm_commands.task_to_pr("123", force_create=False, draft=False)
        
        # Verify
        self.assertIsNotNone(result)
        self.assertEqual(result.number, 456)
        self.assertEqual(result.title, "Task #123: Test Task")
        
        # Verify GitHub service was called correctly
        self.mock_github_service.create_autonomous_pr.assert_called_once()
        call_args = self.mock_github_service.create_autonomous_pr.call_args
        self.assertEqual(call_args[1]['title'], "Task #123: Test Task")
        self.assertEqual(call_args[1]['branch_name'], "feature/test-123")
    
    def test_task_to_pr_with_auto_branch_creation(self):
        """Test task to PR with automatic branch creation."""
        # Create test task without branch
        self._create_test_task_file("123", "test-epic")
        
        # Mock branch creation
        self.mock_github_service.create_feature_branch.return_value = (True, "feature/task-123-test-task")
        
        # Mock PR creation
        pr_result = PRCreationResult(success=True, pr_number=456, pr_url="https://github.com/owner/repo/pull/456")
        self.mock_github_service.create_autonomous_pr.return_value = pr_result
        
        # Mock PR info
        pr_info = Mock()
        self.mock_cli_wrapper.get_pull_request_info.return_value = pr_info
        
        # Mock file update
        with patch.object(self.pm_commands, '_update_task_branch_info'):
            with patch.object(self.pm_commands, '_update_task_pr_info'):
                result = self.pm_commands.task_to_pr("123", auto_branch=True)
        
        # Verify
        self.assertIsNotNone(result)
        self.mock_github_service.create_feature_branch.assert_called_once()
    
    def test_task_to_pr_task_not_found(self):
        """Test task to PR with non-existent task."""
        result = self.pm_commands.task_to_pr("999")
        
        self.assertIsNone(result)
    
    def test_task_to_pr_branch_creation_failure(self):
        """Test task to PR with branch creation failure."""
        self._create_test_task_file("123", "test-epic")
        
        # Mock branch creation failure
        self.mock_github_service.create_feature_branch.return_value = (False, "")
        
        result = self.pm_commands.task_to_pr("123", auto_branch=True)
        
        self.assertIsNone(result)
    
    def test_task_to_pr_pr_creation_failure(self):
        """Test task to PR with PR creation failure."""
        self._create_test_task_file("123", "test-epic", branch="feature/test")
        
        # Mock PR creation failure
        pr_result = PRCreationResult(success=False, error_message="PR creation failed")
        self.mock_github_service.create_autonomous_pr.return_value = pr_result
        
        result = self.pm_commands.task_to_pr("123")
        
        self.assertIsNone(result)
    
    def test_task_status_sync_single_task(self):
        """Test syncing status for a single task."""
        # Create test task with PR
        self._create_test_task_file("123", "test-epic", pr_number="456")
        
        # Mock PR info
        pr_info = PRInfo(
            number=456,
            title="Test PR",
            state="merged",
            author="testuser",
            base_ref="main",
            head_ref="feature/test",
            url="https://github.com/owner/repo/pull/456",
            draft=False,
            mergeable="MERGED",
            created_at="2023-01-01T00:00:00Z",
            updated_at="2023-01-01T00:00:00Z"
        )
        self.mock_cli_wrapper.get_pull_request_info.return_value = pr_info
        
        # Mock task status update
        with patch.object(self.pm_commands, '_update_task_status') as mock_update:
            result = self.pm_commands.task_status_sync("123")
        
        # Verify
        self.assertEqual(result["synced"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(len(result["updated"]), 1)
        
        update_info = result["updated"][0]
        self.assertEqual(update_info["task_id"], "123")
        self.assertEqual(update_info["new_status"], "completed")  # merged PR -> completed task
        
        mock_update.assert_called_once_with("123", "completed")
    
    def test_task_status_sync_all_tasks(self):
        """Test syncing status for all tasks with GitHub info."""
        # Create test tasks
        self._create_test_task_file("123", "epic1", pr_number="456")
        self._create_test_task_file("124", "epic1", github="https://github.com/owner/repo/issues/789")
        self._create_test_task_file("125", "epic2")  # No GitHub info, should be skipped
        
        # Mock PR info
        pr_info = Mock()
        pr_info.state = "closed"
        pr_info.draft = False
        self.mock_cli_wrapper.get_pull_request_info.return_value = pr_info
        
        # Mock issue info
        issue_info = Mock()
        issue_info.state = "closed"
        self.mock_cli_wrapper.get_issue_info.return_value = issue_info
        
        # Mock task status updates
        with patch.object(self.pm_commands, '_update_task_status'):
            result = self.pm_commands.task_status_sync()
        
        # Verify - should sync 2 tasks (123 and 124)
        self.assertEqual(result["synced"], 2)
        self.assertEqual(result["failed"], 0)
    
    def test_pr_quality_check_success(self):
        """Test successful PR quality check."""
        # Mock PR info
        pr_info = PRInfo(
            number=123,
            title="Test PR",
            state="open",
            author="testuser",
            base_ref="main", 
            head_ref="feature/test",
            url="https://github.com/owner/repo/pull/123",
            draft=True,
            mergeable="MERGEABLE",
            created_at="2023-01-01T00:00:00Z",
            updated_at="2023-01-01T00:00:00Z"
        )
        self.mock_cli_wrapper.get_pull_request_info.return_value = pr_info
        
        # Mock successful checkout
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0, stderr="")
            
            # Mock quality gates passing
            self.mock_github_service._run_quality_gates.return_value = []
            
            # Mock comment addition
            self.mock_cli_wrapper.add_pr_comment.return_value = True
            
            result = self.pm_commands.pr_quality_check(123)
        
        # Verify
        self.assertEqual(result["pr_number"], 123)
        self.assertEqual(result["branch"], "feature/test")
        self.assertTrue(result["passed"])
        self.assertEqual(result["failures"], [])
        
        # Verify comment was added
        self.mock_cli_wrapper.add_pr_comment.assert_called()
    
    def test_pr_quality_check_failures(self):
        """Test PR quality check with failures."""
        pr_info = Mock()
        pr_info.head_ref = "feature/test"
        pr_info.draft = False
        self.mock_cli_wrapper.get_pull_request_info.return_value = pr_info
        
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0, stderr="")
            
            # Mock quality gates failing
            self.mock_github_service._run_quality_gates.return_value = ["tests_pass", "lint_pass"]
            
            result = self.pm_commands.pr_quality_check(123)
        
        # Verify
        self.assertFalse(result["passed"])
        self.assertEqual(result["failures"], ["tests_pass", "lint_pass"])
    
    def test_pr_quality_check_checkout_failure(self):
        """Test PR quality check with checkout failure."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=1, stderr="checkout failed")
            
            result = self.pm_commands.pr_quality_check(123)
        
        # Verify
        self.assertIn("error", result)
        self.assertIn("Failed to checkout PR branch", result["error"])
    
    def test_standup_github_integration(self):
        """Test standup report with GitHub integration."""
        # Mock current user
        self.mock_cli_wrapper.get_current_user.return_value = "testuser"
        
        # Mock PR list
        mock_prs = [
            Mock(number=123, title="PR 1", draft=True, url="url1"),
            Mock(number=124, title="PR 2", draft=False, url="url2"),
            Mock(number=125, title="PR 3", draft=False, url="url3")
        ]
        self.mock_cli_wrapper.list_pull_requests.return_value = mock_prs
        
        result = self.pm_commands.standup_github_integration()
        
        # Verify
        self.assertEqual(result["user"], "testuser")
        self.assertEqual(result["pull_requests"]["authored"], 3)
        self.assertEqual(result["pull_requests"]["draft"], 1)
        self.assertEqual(result["pull_requests"]["ready_for_review"], 2)
        self.assertEqual(len(result["pull_requests"]["details"]), 3)
    
    def test_standup_github_integration_no_user(self):
        """Test standup report when current user cannot be determined."""
        self.mock_cli_wrapper.get_current_user.return_value = None
        
        result = self.pm_commands.standup_github_integration()
        
        self.assertIn("error", result)
        self.assertIn("Could not determine current GitHub user", result["error"])
    
    def test_bulk_pr_operations_close(self):
        """Test bulk PR close operations."""
        # Mock successful close operations
        mock_result = Mock(success=True, stdout="", stderr="", return_code=0)
        self.mock_cli_wrapper._run_gh_command.return_value = mock_result
        
        result = self.pm_commands.bulk_pr_operations("close", [123, 124, 125])
        
        # Verify
        self.assertEqual(result["operation"], "close")
        self.assertEqual(result["total"], 3)
        self.assertEqual(len(result["successful"]), 3)
        self.assertEqual(len(result["failed"]), 0)
        self.assertIn(123, result["successful"])
        self.assertIn(124, result["successful"])
        self.assertIn(125, result["successful"])
    
    def test_bulk_pr_operations_merge(self):
        """Test bulk PR merge operations."""
        self.mock_cli_wrapper.merge_pull_request.return_value = True
        
        result = self.pm_commands.bulk_pr_operations("merge", [123, 124])
        
        # Verify
        self.assertEqual(result["operation"], "merge")
        self.assertEqual(len(result["successful"]), 2)
        self.assertEqual(len(result["failed"]), 0)
    
    def test_bulk_pr_operations_add_label(self):
        """Test bulk PR add label operations."""
        self.mock_cli_wrapper.update_pull_request.return_value = True
        
        result = self.pm_commands.bulk_pr_operations("add-label:bug", [123, 124])
        
        # Verify
        self.assertEqual(result["operation"], "add-label:bug")
        self.assertEqual(len(result["successful"]), 2)
        
        # Verify update_pull_request was called with correct parameters
        self.mock_cli_wrapper.update_pull_request.assert_called()
    
    def test_bulk_pr_operations_unknown_operation(self):
        """Test bulk PR operations with unknown operation."""
        result = self.pm_commands.bulk_pr_operations("unknown-op", [123])
        
        # Verify
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("Unknown operation", result["errors"][0]["error"])
    
    def test_bulk_pr_operations_with_failures(self):
        """Test bulk PR operations with some failures."""
        # Mock mixed success/failure
        self.mock_cli_wrapper.merge_pull_request.side_effect = [True, False, True]
        
        result = self.pm_commands.bulk_pr_operations("merge", [123, 124, 125])
        
        # Verify
        self.assertEqual(len(result["successful"]), 2)
        self.assertEqual(len(result["failed"]), 1)
        self.assertIn(124, result["failed"])
    
    def test_parse_task_file(self):
        """Test parsing task file."""
        task_file = self._create_test_task_file(
            "123", "test-epic", 
            assignee="testuser",
            priority="high",
            depends_on="124,125",
            github="https://github.com/owner/repo/pull/456",
            pr_number="456",
            branch="feature/test"
        )
        
        task_info = self.pm_commands._parse_task_file(task_file, "test-epic")
        
        # Verify
        self.assertIsNotNone(task_info)
        self.assertEqual(task_info.id, "123")
        self.assertEqual(task_info.title, "Task 123: Test Task")
        self.assertEqual(task_info.status, "open")
        self.assertEqual(task_info.epic, "test-epic")
        self.assertEqual(task_info.assignee, "testuser")
        self.assertEqual(task_info.priority, "high")
        self.assertEqual(task_info.depends_on, ["124", "125"])
        self.assertEqual(task_info.github_url, "https://github.com/owner/repo/pull/456")
        self.assertEqual(task_info.pr_number, 456)
        self.assertEqual(task_info.branch_name, "feature/test")
    
    def test_load_task_info_success(self):
        """Test loading task info successfully."""
        self._create_test_task_file("123", "test-epic")
        
        task_info = self.pm_commands._load_task_info("123")
        
        self.assertIsNotNone(task_info)
        self.assertEqual(task_info.id, "123")
        self.assertEqual(task_info.epic, "test-epic")
    
    def test_load_task_info_not_found(self):
        """Test loading non-existent task info."""
        task_info = self.pm_commands._load_task_info("999")
        
        self.assertIsNone(task_info)
    
    def test_load_all_tasks_with_github_info(self):
        """Test loading all tasks with GitHub info."""
        # Create tasks with and without GitHub info
        self._create_test_task_file("123", "epic1", pr_number="456")
        self._create_test_task_file("124", "epic1", github="https://github.com/owner/repo/issues/789")
        self._create_test_task_file("125", "epic2")  # No GitHub info
        
        tasks = self.pm_commands._load_all_tasks_with_github_info()
        
        # Should only return tasks with GitHub info
        self.assertEqual(len(tasks), 2)
        task_ids = [task.id for task in tasks]
        self.assertIn("123", task_ids)
        self.assertIn("124", task_ids)
        self.assertNotIn("125", task_ids)
    
    def test_generate_task_pr_description(self):
        """Test generating PR description from task info."""
        task_info = TaskInfo(
            id="123",
            title="Test Task",
            status="in_progress",
            epic="test-epic",
            assignee="testuser",
            priority="high",
            depends_on=["124", "125"]
        )
        
        description = self.pm_commands._generate_task_pr_description(task_info)
        
        # Verify content
        self.assertIn("Task #123: Test Task", description)
        self.assertIn("Epic:** test-epic", description)
        self.assertIn("Status:** in_progress", description)
        self.assertIn("Assignee:** @testuser", description)
        self.assertIn("Priority:** high", description)
        self.assertIn("Dependencies:** 124, 125", description)
        self.assertIn("Test Plan", description)
        self.assertIn("Claude Flow", description)
    
    def test_map_pr_status_to_task_status(self):
        """Test mapping PR status to task status."""
        # Test various PR states
        self.assertEqual(
            self.pm_commands._map_pr_status_to_task_status("merged", False), 
            "completed"
        )
        self.assertEqual(
            self.pm_commands._map_pr_status_to_task_status("closed", False), 
            "cancelled"
        )
        self.assertEqual(
            self.pm_commands._map_pr_status_to_task_status("open", True), 
            "in_progress"
        )
        self.assertEqual(
            self.pm_commands._map_pr_status_to_task_status("open", False), 
            "in_review"
        )
    
    def test_map_issue_status_to_task_status(self):
        """Test mapping issue status to task status."""
        self.assertEqual(
            self.pm_commands._map_issue_status_to_task_status("closed"), 
            "completed"
        )
        self.assertEqual(
            self.pm_commands._map_issue_status_to_task_status("open"), 
            "open"
        )
    
    def test_format_quality_check_comment_passed(self):
        """Test formatting quality check comment for passed checks."""
        comment = self.pm_commands._format_quality_check_comment([], passed=True)
        
        self.assertIn("✅ **Quality Checks Passed**", comment)
        self.assertIn("All automated quality checks have passed", comment)
        self.assertIn("Claude Flow", comment)
    
    def test_format_quality_check_comment_failed(self):
        """Test formatting quality check comment for failed checks."""
        failures = ["tests_pass", "lint_pass", "build_success"]
        comment = self.pm_commands._format_quality_check_comment(failures, passed=False)
        
        self.assertIn("❌ **Quality Checks Failed**", comment)
        self.assertIn("❌ Tests Pass", comment)
        self.assertIn("❌ Lint Pass", comment)
        self.assertIn("❌ Build Success", comment)
        self.assertIn("Claude Flow", comment)


if __name__ == '__main__':
    unittest.main()