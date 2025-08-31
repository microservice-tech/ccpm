#!/usr/bin/env python3
"""
Unit tests for GitHubService class.
"""

import os
import tempfile
import unittest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
import subprocess

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.config_manager import ConfigManager
from github_integration.github_service import (
    GitHubService, PRCreationResult, BranchInfo, PRQualityGate, PRStatus
)


class TestGitHubService(unittest.TestCase):
    """Test cases for GitHubService."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_path = Path(self.temp_dir)
        
        # Mock configuration
        self.mock_config = Mock(spec=ConfigManager)
        self.mock_config.get.side_effect = self._mock_config_get
        
        # Mock GitHub client
        self.mock_github_client = Mock()
        
        # Create test service
        with patch('github_integration.github_service.GitHubClient') as mock_gh_client:
            mock_gh_client.return_value = self.mock_github_client
            self.service = GitHubService(self.mock_config)
            self.service.workspace_root = self.workspace_path
    
    def _mock_config_get(self, key, default=None):
        """Mock configuration getter."""
        config_values = {
            "github.token": "test-token",
            "github.owner": "test-owner",
            "github.repo": "test-repo",
            "github.main_branch": "main",
            "github.quality_gates_enabled": True,
            "github.required_quality_gates": ["tests_pass", "lint_pass"],
            "github.pr_template_path": ".github/pull_request_template.md",
            "github.branch_naming_prefix": "feature/",
            "workspace.root": str(self.workspace_path)
        }
        return config_values.get(key, default)
    
    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_init_success(self):
        """Test successful initialization."""
        self.assertEqual(self.service.owner, "test-owner")
        self.assertEqual(self.service.repo, "test-repo")
        self.assertEqual(self.service.main_branch, "main")
        self.assertTrue(self.service.quality_gates_enabled)
        self.assertEqual(len(self.service.required_quality_gates), 2)
    
    def test_init_missing_token(self):
        """Test initialization with missing GitHub token."""
        mock_config = Mock(spec=ConfigManager)
        mock_config.get.return_value = None
        
        with self.assertRaises(ValueError) as cm:
            GitHubService(mock_config)
        
        self.assertIn("GitHub token not configured", str(cm.exception))
    
    def test_init_missing_repo_config(self):
        """Test initialization with missing repository configuration."""
        mock_config = Mock(spec=ConfigManager)
        
        def mock_get(key, default=None):
            if key == "github.token":
                return "test-token"
            elif key == "github.owner":
                return None
            return default
        
        mock_config.get.side_effect = mock_get
        
        with patch('github_integration.github_service.GitHubClient'):
            with self.assertRaises(ValueError) as cm:
                GitHubService(mock_config)
            
            self.assertIn("GitHub repository not configured", str(cm.exception))
    
    @patch('github_integration.github_service.GitHubService._get_current_branch')
    @patch('github_integration.github_service.GitHubService._run_quality_gates')
    @patch('github_integration.github_service.GitHubService._generate_pr_description')
    @patch('github_integration.github_service.GitHubService._push_branch_to_remote')
    def test_create_autonomous_pr_success(self, mock_push, mock_gen_desc, mock_quality, mock_branch):
        """Test successful autonomous PR creation."""
        # Setup mocks
        mock_branch.return_value = "feature/test-branch"
        mock_quality.return_value = []
        mock_gen_desc.return_value = "Test PR description"
        mock_push.return_value = True
        
        self.mock_github_client.create_pull_request.return_value = {
            "number": 123,
            "html_url": "https://github.com/test-owner/test-repo/pull/123"
        }
        
        # Execute
        result = self.service.create_autonomous_pr(
            title="Test PR",
            description=None,
            draft=False
        )
        
        # Verify
        self.assertTrue(result.success)
        self.assertEqual(result.pr_number, 123)
        self.assertEqual(result.pr_url, "https://github.com/test-owner/test-repo/pull/123")
        self.assertEqual(result.quality_gate_failures, [])
        
        # Verify GitHub client was called correctly
        self.mock_github_client.create_pull_request.assert_called_once_with(
            owner="test-owner",
            repo="test-repo",
            title="Test PR",
            head="feature/test-branch",
            base="main",
            body="Test PR description",
            draft=False
        )
    
    @patch('github_integration.github_service.GitHubService._get_current_branch')
    @patch('github_integration.github_service.GitHubService._run_quality_gates')
    def test_create_autonomous_pr_quality_gate_failure(self, mock_quality, mock_branch):
        """Test PR creation with quality gate failures."""
        # Setup mocks
        mock_branch.return_value = "feature/test-branch"
        mock_quality.return_value = ["tests_pass", "lint_pass"]
        
        # Execute
        result = self.service.create_autonomous_pr(
            title="Test PR",
            draft=False,
            force_create=False
        )
        
        # Verify
        self.assertFalse(result.success)
        self.assertEqual(result.error_message, "Quality gates failed")
        self.assertEqual(result.quality_gate_failures, ["tests_pass", "lint_pass"])
    
    @patch('github_integration.github_service.GitHubService._get_current_branch')
    def test_create_autonomous_pr_no_current_branch(self, mock_branch):
        """Test PR creation when current branch cannot be determined."""
        mock_branch.return_value = None
        
        result = self.service.create_autonomous_pr(title="Test PR")
        
        self.assertFalse(result.success)
        self.assertEqual(result.error_message, "Could not determine current branch")
    
    @patch('github_integration.github_service.GitHubService._run_git_command')
    def test_create_feature_branch_success(self, mock_git):
        """Test successful feature branch creation."""
        # Setup mocks for git commands
        mock_git.side_effect = [
            Mock(returncode=0),  # checkout main
            Mock(returncode=0),  # pull main
            Mock(returncode=0)   # checkout -b feature
        ]
        
        # Execute
        success, branch_name = self.service.create_feature_branch("test feature")
        
        # Verify
        self.assertTrue(success)
        self.assertEqual(branch_name, "feature/test-feature")
        
        # Verify git commands were called
        expected_calls = [
            unittest.mock.call(["git", "checkout", "main"]),
            unittest.mock.call(["git", "pull", "origin", "main"]),
            unittest.mock.call(["git", "checkout", "-b", "feature/test-feature"])
        ]
        mock_git.assert_has_calls(expected_calls)
    
    @patch('github_integration.github_service.GitHubService._run_git_command')
    def test_create_feature_branch_failure(self, mock_git):
        """Test feature branch creation failure."""
        # Setup mock for git command failure
        mock_git.return_value = Mock(returncode=1, stderr="Git error")
        
        # Execute
        success, branch_name = self.service.create_feature_branch("test feature")
        
        # Verify
        self.assertFalse(success)
        self.assertEqual(branch_name, "")
    
    @patch('github_integration.github_service.GitHubService._get_current_branch')
    @patch('github_integration.github_service.GitHubService._run_git_command')
    def test_sync_with_upstream_main_branch(self, mock_git, mock_branch):
        """Test syncing main branch with upstream."""
        mock_branch.return_value = "main"
        mock_git.side_effect = [
            Mock(returncode=0),  # git fetch
            Mock(returncode=0)   # git pull
        ]
        
        result = self.service.sync_with_upstream()
        
        self.assertTrue(result)
        expected_calls = [
            unittest.mock.call(["git", "fetch", "origin"]),
            unittest.mock.call(["git", "pull", "origin", "main"])
        ]
        mock_git.assert_has_calls(expected_calls)
    
    @patch('github_integration.github_service.GitHubService._get_current_branch')
    @patch('github_integration.github_service.GitHubService._run_git_command')
    def test_sync_with_upstream_feature_branch(self, mock_git, mock_branch):
        """Test syncing feature branch with upstream."""
        mock_branch.return_value = "feature/test"
        mock_git.side_effect = [
            Mock(returncode=0),  # git fetch
            Mock(returncode=0),  # checkout main
            Mock(returncode=0),  # pull main
            Mock(returncode=0),  # checkout feature
            Mock(returncode=0)   # rebase main
        ]
        
        result = self.service.sync_with_upstream()
        
        self.assertTrue(result)
    
    @patch('github_integration.github_service.GitHubService._run_git_command')
    def test_cleanup_merged_branches(self, mock_git):
        """Test cleanup of merged branches."""
        # Mock git branch --merged output
        mock_git.side_effect = [
            Mock(returncode=0, stdout="  feature/old-feature\n  feature/another-old\n"),
            Mock(returncode=0),  # delete first branch
            Mock(returncode=0)   # delete second branch
        ]
        
        deleted_branches = self.service.cleanup_merged_branches()
        
        self.assertEqual(len(deleted_branches), 2)
        self.assertIn("feature/old-feature", deleted_branches)
        self.assertIn("feature/another-old", deleted_branches)
    
    @patch('github_integration.github_service.GitHubService._run_git_command')
    def test_cleanup_merged_branches_dry_run(self, mock_git):
        """Test dry run cleanup of merged branches."""
        mock_git.return_value = Mock(returncode=0, stdout="  feature/old-feature\n")
        
        branches = self.service.cleanup_merged_branches(dry_run=True)
        
        self.assertEqual(branches, ["feature/old-feature"])
        # Should only call git branch --merged, not git branch -d
        mock_git.assert_called_once()
    
    @patch('github_integration.github_service.GitHubService._get_current_branch')
    @patch('github_integration.github_service.GitHubService._run_git_command')
    def test_get_branch_info(self, mock_git, mock_branch):
        """Test getting branch information."""
        mock_branch.return_value = "feature/test"
        mock_git.side_effect = [
            Mock(returncode=0, stdout="origin/feature/test"),  # upstream
            Mock(returncode=0, stdout="2\t3")  # ahead/behind counts
        ]
        
        branch_info = self.service.get_branch_info()
        
        self.assertIsNotNone(branch_info)
        self.assertEqual(branch_info.name, "feature/test")
        self.assertEqual(branch_info.upstream, "origin/feature/test")
        self.assertEqual(branch_info.ahead, 3)
        self.assertEqual(branch_info.behind, 2)
    
    @patch('github_integration.github_service.GitHubService._run_quality_gate')
    def test_run_quality_gates(self, mock_gate):
        """Test running quality gates."""
        # Setup mock to return failures for some gates
        mock_gate.side_effect = [True, False]  # tests pass, lint fails
        
        failures = self.service._run_quality_gates()
        
        self.assertEqual(len(failures), 1)
        self.assertIn("lint_pass", failures)
        
        # Verify both gates were checked
        self.assertEqual(mock_gate.call_count, 2)
    
    @patch('github_integration.github_service.GitHubService._command_exists')
    @patch('github_integration.github_service.GitHubService._run_git_command')
    def test_run_tests_npm_available(self, mock_git, mock_cmd_exists):
        """Test running tests with npm available."""
        mock_cmd_exists.return_value = True
        mock_git.return_value = Mock(returncode=0)
        
        result = self.service._run_tests()
        
        self.assertTrue(result)
        mock_git.assert_called_once_with(["npm", "test"])
    
    @patch('github_integration.github_service.GitHubService._command_exists')
    def test_run_tests_no_command_available(self, mock_cmd_exists):
        """Test running tests with no test command available."""
        mock_cmd_exists.return_value = False
        
        result = self.service._run_tests()
        
        # Should pass if no test command is found
        self.assertTrue(result)
    
    @patch('github_integration.github_service.GitHubService._run_git_command')
    def test_generate_pr_description(self, mock_git):
        """Test PR description generation."""
        # Mock git log and git diff outputs
        mock_git.side_effect = [
            Mock(returncode=0, stdout="abc123 Add feature\ndef456 Fix bug"),
            Mock(returncode=0, stdout="src/main.py\ntests/test_main.py")
        ]
        
        description = self.service._generate_pr_description("feature/test", "main")
        
        self.assertIn("## Summary", description)
        self.assertIn("## Changes", description)
        self.assertIn("Add feature", description)
        self.assertIn("src/main.py", description)
        self.assertIn("Claude Flow", description)
    
    @patch('github_integration.github_service.GitHubService._run_git_command')
    def test_push_branch_to_remote_success(self, mock_git):
        """Test successful branch push to remote."""
        mock_git.return_value = Mock(returncode=0)
        
        result = self.service._push_branch_to_remote("feature/test")
        
        self.assertTrue(result)
        mock_git.assert_called_once_with(["git", "push", "-u", "origin", "feature/test"])
    
    @patch('github_integration.github_service.GitHubService._run_git_command')
    def test_push_branch_to_remote_failure(self, mock_git):
        """Test failed branch push to remote."""
        mock_git.return_value = Mock(returncode=1)
        
        result = self.service._push_branch_to_remote("feature/test")
        
        self.assertFalse(result)
    
    @patch('github_integration.github_service.GitHubService._run_git_command')
    def test_get_current_branch_success(self, mock_git):
        """Test getting current branch name."""
        mock_git.return_value = Mock(returncode=0, stdout="feature/test")
        
        branch = self.service._get_current_branch()
        
        self.assertEqual(branch, "feature/test")
    
    @patch('github_integration.github_service.GitHubService._run_git_command')
    def test_get_current_branch_failure(self, mock_git):
        """Test getting current branch name failure."""
        mock_git.return_value = Mock(returncode=1)
        
        branch = self.service._get_current_branch()
        
        self.assertIsNone(branch)
    
    def test_run_git_command(self):
        """Test git command execution."""
        # This is a basic test - more comprehensive testing would require
        # a real git repository
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="output", stderr="")
            
            result = self.service._run_git_command(["git", "status"])
            
            self.assertEqual(result.returncode, 0)
            mock_run.assert_called_once_with(
                ["git", "status"],
                cwd=self.workspace_path,
                capture_output=True,
                text=True,
                timeout=300
            )
    
    @patch('subprocess.run')
    def test_command_exists_true(self, mock_run):
        """Test command existence check - command exists."""
        mock_run.return_value = Mock(returncode=0)
        
        result = self.service._command_exists("npm")
        
        self.assertTrue(result)
        mock_run.assert_called_once_with(
            ["which", "npm"], 
            capture_output=True, 
            check=True
        )
    
    @patch('subprocess.run')
    def test_command_exists_false(self, mock_run):
        """Test command existence check - command doesn't exist."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "which")
        
        result = self.service._command_exists("nonexistent")
        
        self.assertFalse(result)
    
    def test_transition_pr_to_ready_success(self):
        """Test successful PR transition to ready."""
        with patch.object(self.service, '_run_quality_gates') as mock_quality:
            with patch.object(self.service, '_add_quality_gate_comment') as mock_comment:
                mock_quality.return_value = []
                
                # Mock GitHub client response
                mock_response = Mock()
                mock_response.status_code = 200
                self.mock_github_client._make_request.return_value = mock_response
                
                result = self.service.transition_pr_to_ready(123)
                
                self.assertTrue(result)
                mock_quality.assert_called_once()
                self.mock_github_client.add_issue_comment.assert_called_once()
    
    def test_transition_pr_to_ready_quality_failure(self):
        """Test PR transition to ready with quality failures."""
        with patch.object(self.service, '_run_quality_gates') as mock_quality:
            with patch.object(self.service, '_add_quality_gate_comment') as mock_comment:
                mock_quality.return_value = ["tests_pass"]
                
                result = self.service.transition_pr_to_ready(123)
                
                self.assertFalse(result)
                mock_comment.assert_called_once_with(123, ["tests_pass"])


if __name__ == '__main__':
    unittest.main()