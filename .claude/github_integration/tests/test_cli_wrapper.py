#!/usr/bin/env python3
"""
Unit tests for GitHubCLIWrapper class.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
import subprocess

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from github_integration.cli.wrapper import (
    GitHubCLIWrapper, GitHubCLIError, AuthenticationStatus, CLIResult, PRInfo, IssueInfo
)


class TestGitHubCLIWrapper(unittest.TestCase):
    """Test cases for GitHubCLIWrapper."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_path = Path(self.temp_dir)
        
        # Mock the gh CLI verification and auth check
        with patch.object(GitHubCLIWrapper, '_verify_gh_cli'):
            with patch.object(GitHubCLIWrapper, '_check_auth_status') as mock_auth:
                mock_auth.return_value = AuthenticationStatus.AUTHENTICATED
                self.wrapper = GitHubCLIWrapper(self.workspace_path)
    
    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    @patch('subprocess.run')
    def test_verify_gh_cli_success(self, mock_run):
        """Test successful gh CLI verification."""
        mock_run.return_value = Mock(returncode=0, stdout="gh version 2.20.2")
        
        # This should not raise an exception
        wrapper = GitHubCLIWrapper.__new__(GitHubCLIWrapper)
        wrapper.logger = Mock()
        wrapper._verify_gh_cli()
        
        mock_run.assert_called_once_with(
            ["gh", "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
    
    @patch('subprocess.run')
    def test_verify_gh_cli_not_found(self, mock_run):
        """Test gh CLI not found."""
        mock_run.side_effect = FileNotFoundError()
        
        wrapper = GitHubCLIWrapper.__new__(GitHubCLIWrapper)
        wrapper.logger = Mock()
        
        with self.assertRaises(GitHubCLIError) as cm:
            wrapper._verify_gh_cli()
        
        self.assertIn("GitHub CLI (gh) not found", str(cm.exception))
    
    @patch('subprocess.run')
    def test_verify_gh_cli_failure(self, mock_run):
        """Test gh CLI verification failure."""
        mock_run.return_value = Mock(returncode=1)
        
        wrapper = GitHubCLIWrapper.__new__(GitHubCLIWrapper)
        wrapper.logger = Mock()
        
        with self.assertRaises(GitHubCLIError) as cm:
            wrapper._verify_gh_cli()
        
        self.assertIn("GitHub CLI is not properly installed", str(cm.exception))
    
    def test_check_auth_status_authenticated(self):
        """Test checking authentication status - authenticated."""
        with patch.object(self.wrapper, '_run_gh_command') as mock_run:
            mock_run.return_value = CLIResult(
                success=True,
                stdout="",
                stderr="Logged in to github.com as testuser",
                return_code=0,
                command=[],
                execution_time=0.1
            )
            
            status = self.wrapper._check_auth_status()
            
            self.assertEqual(status, AuthenticationStatus.AUTHENTICATED)
    
    def test_check_auth_status_not_authenticated(self):
        """Test checking authentication status - not authenticated."""
        with patch.object(self.wrapper, '_run_gh_command') as mock_run:
            mock_run.return_value = CLIResult(
                success=False,
                stdout="",
                stderr="not logged in",
                return_code=1,
                command=[],
                execution_time=0.1
            )
            
            status = self.wrapper._check_auth_status()
            
            self.assertEqual(status, AuthenticationStatus.NOT_AUTHENTICATED)
    
    def test_check_auth_status_expired(self):
        """Test checking authentication status - expired."""
        with patch.object(self.wrapper, '_run_gh_command') as mock_run:
            mock_run.return_value = CLIResult(
                success=False,
                stdout="",
                stderr="authentication failed",
                return_code=1,
                command=[],
                execution_time=0.1
            )
            
            status = self.wrapper._check_auth_status()
            
            self.assertEqual(status, AuthenticationStatus.EXPIRED)
    
    @patch('subprocess.run')
    def test_run_gh_command_success(self, mock_run):
        """Test successful gh command execution."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="command output",
            stderr=""
        )
        
        result = self.wrapper._run_gh_command(["gh", "repo", "view"])
        
        self.assertTrue(result.success)
        self.assertEqual(result.stdout, "command output")
        self.assertEqual(result.return_code, 0)
        
        mock_run.assert_called_once_with(
            ["gh", "repo", "view"],
            cwd=self.workspace_path,
            input=None,
            capture_output=True,
            text=True,
            timeout=60
        )
    
    @patch('subprocess.run')
    def test_run_gh_command_failure(self, mock_run):
        """Test failed gh command execution."""
        mock_run.return_value = Mock(
            returncode=1,
            stdout="",
            stderr="command failed"
        )
        
        result = self.wrapper._run_gh_command(["gh", "repo", "view"])
        
        self.assertFalse(result.success)
        self.assertEqual(result.stderr, "command failed")
        self.assertEqual(result.return_code, 1)
    
    @patch('subprocess.run')
    def test_run_gh_command_timeout(self, mock_run):
        """Test gh command timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired(["gh", "repo", "view"], 60)
        
        result = self.wrapper._run_gh_command(["gh", "repo", "view"])
        
        self.assertFalse(result.success)
        self.assertIn("timed out", result.stderr)
        self.assertEqual(result.return_code, -1)
    
    @patch('subprocess.run')
    def test_run_gh_command_with_retries(self, mock_run):
        """Test gh command with retries."""
        # First two calls fail, third succeeds
        mock_run.side_effect = [
            Mock(returncode=1, stdout="", stderr="network error"),
            Mock(returncode=1, stdout="", stderr="network error"),
            Mock(returncode=0, stdout="success", stderr="")
        ]
        
        with patch('time.sleep'):  # Speed up test by mocking sleep
            result = self.wrapper._run_gh_command(["gh", "repo", "view"], retries=3)
        
        self.assertTrue(result.success)
        self.assertEqual(result.stdout, "success")
        self.assertEqual(mock_run.call_count, 3)
    
    @patch('subprocess.run')
    def test_run_gh_command_auth_failure_no_retry(self, mock_run):
        """Test that auth failures don't trigger retries."""
        mock_run.return_value = Mock(
            returncode=1,
            stdout="",
            stderr="authentication failed"
        )
        
        result = self.wrapper._run_gh_command(["gh", "repo", "view"], retries=3)
        
        self.assertFalse(result.success)
        self.assertEqual(mock_run.call_count, 1)  # Should not retry
        self.assertEqual(self.wrapper.auth_status, AuthenticationStatus.EXPIRED)
    
    def test_create_pull_request_success(self):
        """Test successful PR creation."""
        pr_url = "https://github.com/owner/repo/pull/123"
        
        with patch.object(self.wrapper, '_run_gh_command') as mock_run:
            with patch.object(self.wrapper, 'get_pull_request_info') as mock_get_pr:
                # Mock successful PR creation
                mock_run.return_value = CLIResult(
                    success=True,
                    stdout=pr_url,
                    stderr="",
                    return_code=0,
                    command=[],
                    execution_time=0.5
                )
                
                # Mock PR info retrieval
                mock_pr_info = PRInfo(
                    number=123,
                    title="Test PR",
                    state="open",
                    author="testuser",
                    base_ref="main",
                    head_ref="feature/test",
                    url=pr_url,
                    draft=False,
                    mergeable="MERGEABLE",
                    created_at="2023-01-01T00:00:00Z",
                    updated_at="2023-01-01T00:00:00Z"
                )
                mock_get_pr.return_value = mock_pr_info
                
                result = self.wrapper.create_pull_request(
                    title="Test PR",
                    body="Test description",
                    base="main",
                    draft=False
                )
                
                self.assertIsNotNone(result)
                self.assertEqual(result.number, 123)
                self.assertEqual(result.title, "Test PR")
                
                # Verify command was called correctly
                expected_command = [
                    "gh", "pr", "create",
                    "--title", "Test PR",
                    "--base", "main",
                    "--body", "Test description"
                ]
                mock_run.assert_called_once_with(expected_command)
    
    def test_create_pull_request_with_options(self):
        """Test PR creation with all options."""
        with patch.object(self.wrapper, '_run_gh_command') as mock_run:
            with patch.object(self.wrapper, 'get_pull_request_info') as mock_get_pr:
                mock_run.return_value = CLIResult(
                    success=True, stdout="https://github.com/owner/repo/pull/123",
                    stderr="", return_code=0, command=[], execution_time=0.5
                )
                mock_get_pr.return_value = Mock()
                
                self.wrapper.create_pull_request(
                    title="Test PR",
                    body="Test description",
                    head="feature/test",
                    base="develop",
                    draft=True,
                    assignee="testuser",
                    labels=["bug", "high-priority"],
                    reviewers=["reviewer1", "reviewer2"]
                )
                
                expected_command = [
                    "gh", "pr", "create",
                    "--title", "Test PR",
                    "--base", "develop",
                    "--body", "Test description",
                    "--head", "feature/test",
                    "--draft",
                    "--assignee", "testuser",
                    "--label", "bug,high-priority",
                    "--reviewer", "reviewer1,reviewer2"
                ]
                mock_run.assert_called_once_with(expected_command)
    
    def test_get_pull_request_info_by_number(self):
        """Test getting PR info by number."""
        pr_data = {
            "number": 123,
            "title": "Test PR",
            "state": "OPEN",
            "author": {"login": "testuser"},
            "baseRefName": "main",
            "headRefName": "feature/test",
            "url": "https://github.com/owner/repo/pull/123",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "createdAt": "2023-01-01T00:00:00Z",
            "updatedAt": "2023-01-01T00:00:00Z"
        }
        
        with patch.object(self.wrapper, '_run_gh_command') as mock_run:
            mock_run.return_value = CLIResult(
                success=True,
                stdout=json.dumps(pr_data),
                stderr="",
                return_code=0,
                command=[],
                execution_time=0.3
            )
            
            result = self.wrapper.get_pull_request_info(number=123)
            
            self.assertIsNotNone(result)
            self.assertEqual(result.number, 123)
            self.assertEqual(result.title, "Test PR")
            self.assertEqual(result.author, "testuser")
            self.assertFalse(result.draft)
    
    def test_get_pull_request_info_by_url(self):
        """Test getting PR info by URL."""
        pr_url = "https://github.com/owner/repo/pull/123"
        pr_data = {
            "number": 123,
            "title": "Test PR",
            "state": "OPEN",
            "author": {"login": "testuser"},
            "baseRefName": "main",
            "headRefName": "feature/test",
            "url": pr_url,
            "isDraft": True,
            "mergeable": "MERGEABLE",
            "createdAt": "2023-01-01T00:00:00Z",
            "updatedAt": "2023-01-01T00:00:00Z"
        }
        
        with patch.object(self.wrapper, '_run_gh_command') as mock_run:
            mock_run.return_value = CLIResult(
                success=True,
                stdout=json.dumps(pr_data),
                stderr="",
                return_code=0,
                command=[],
                execution_time=0.3
            )
            
            result = self.wrapper.get_pull_request_info(url=pr_url)
            
            self.assertIsNotNone(result)
            self.assertEqual(result.number, 123)
            self.assertTrue(result.draft)
    
    def test_get_pull_request_info_missing_params(self):
        """Test getting PR info with missing parameters."""
        with self.assertRaises(ValueError) as cm:
            self.wrapper.get_pull_request_info()
        
        self.assertIn("Either PR number or URL must be provided", str(cm.exception))
    
    def test_update_pull_request_success(self):
        """Test successful PR update."""
        with patch.object(self.wrapper, '_run_gh_command') as mock_run:
            mock_run.return_value = CLIResult(
                success=True, stdout="", stderr="", return_code=0,
                command=[], execution_time=0.2
            )
            
            result = self.wrapper.update_pull_request(
                number=123,
                title="Updated Title",
                body="Updated Description",
                ready=True,
                add_labels=["enhancement"],
                remove_labels=["bug"]
            )
            
            self.assertTrue(result)
            
            # Verify multiple commands were called
            self.assertEqual(mock_run.call_count, 4)  # edit, ready, add-label, remove-label
    
    def test_add_pr_comment_success(self):
        """Test successful PR comment addition."""
        with patch.object(self.wrapper, '_run_gh_command') as mock_run:
            mock_run.return_value = CLIResult(
                success=True, stdout="", stderr="", return_code=0,
                command=[], execution_time=0.2
            )
            
            result = self.wrapper.add_pr_comment(123, "Test comment")
            
            self.assertTrue(result)
            
            expected_command = [
                "gh", "pr", "comment", "123", "--body", "Test comment"
            ]
            mock_run.assert_called_once_with(expected_command)
    
    def test_list_pull_requests_success(self):
        """Test successful PR listing."""
        pr_list_data = [
            {
                "number": 123,
                "title": "Test PR 1",
                "state": "OPEN",
                "author": {"login": "user1"},
                "baseRefName": "main",
                "headRefName": "feature/test1",
                "url": "https://github.com/owner/repo/pull/123",
                "isDraft": False,
                "mergeable": "MERGEABLE",
                "createdAt": "2023-01-01T00:00:00Z",
                "updatedAt": "2023-01-01T00:00:00Z"
            },
            {
                "number": 124,
                "title": "Test PR 2",
                "state": "OPEN",
                "author": {"login": "user2"},
                "baseRefName": "main",
                "headRefName": "feature/test2",
                "url": "https://github.com/owner/repo/pull/124",
                "isDraft": True,
                "mergeable": "MERGEABLE",
                "createdAt": "2023-01-02T00:00:00Z",
                "updatedAt": "2023-01-02T00:00:00Z"
            }
        ]
        
        with patch.object(self.wrapper, '_run_gh_command') as mock_run:
            mock_run.return_value = CLIResult(
                success=True,
                stdout=json.dumps(pr_list_data),
                stderr="",
                return_code=0,
                command=[],
                execution_time=0.5
            )
            
            result = self.wrapper.list_pull_requests(
                state="open",
                author="testuser",
                limit=10
            )
            
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0].number, 123)
            self.assertEqual(result[1].number, 124)
            self.assertFalse(result[0].draft)
            self.assertTrue(result[1].draft)
    
    def test_merge_pull_request_success(self):
        """Test successful PR merge."""
        with patch.object(self.wrapper, '_run_gh_command') as mock_run:
            mock_run.return_value = CLIResult(
                success=True, stdout="", stderr="", return_code=0,
                command=[], execution_time=1.0
            )
            
            result = self.wrapper.merge_pull_request(
                number=123,
                merge_method="squash",
                delete_branch=True
            )
            
            self.assertTrue(result)
            
            expected_command = [
                "gh", "pr", "merge", "123", "--squash", "--delete-branch"
            ]
            mock_run.assert_called_once_with(expected_command)
    
    def test_create_issue_success(self):
        """Test successful issue creation."""
        issue_url = "https://github.com/owner/repo/issues/456"
        
        with patch.object(self.wrapper, '_run_gh_command') as mock_run:
            with patch.object(self.wrapper, 'get_issue_info') as mock_get_issue:
                mock_run.return_value = CLIResult(
                    success=True, stdout=issue_url, stderr="", return_code=0,
                    command=[], execution_time=0.4
                )
                
                mock_issue_info = IssueInfo(
                    number=456,
                    title="Test Issue",
                    state="open",
                    author="testuser",
                    url=issue_url,
                    labels=["bug"],
                    assignees=["assignee1"],
                    created_at="2023-01-01T00:00:00Z",
                    updated_at="2023-01-01T00:00:00Z"
                )
                mock_get_issue.return_value = mock_issue_info
                
                result = self.wrapper.create_issue(
                    title="Test Issue",
                    body="Test description",
                    assignees=["assignee1"],
                    labels=["bug"],
                    milestone="v1.0"
                )
                
                self.assertIsNotNone(result)
                self.assertEqual(result.number, 456)
                self.assertEqual(result.title, "Test Issue")
    
    def test_get_repository_info_success(self):
        """Test successful repository info retrieval."""
        repo_data = {
            "owner": {"login": "testowner"},
            "name": "testrepo",
            "defaultBranch": "main",
            "isPrivate": False,
            "url": "https://github.com/testowner/testrepo",
            "description": "Test repository",
            "createdAt": "2023-01-01T00:00:00Z",
            "updatedAt": "2023-01-01T00:00:00Z"
        }
        
        with patch.object(self.wrapper, '_run_gh_command') as mock_run:
            mock_run.return_value = CLIResult(
                success=True,
                stdout=json.dumps(repo_data),
                stderr="",
                return_code=0,
                command=[],
                execution_time=0.3
            )
            
            result = self.wrapper.get_repository_info()
            
            self.assertIsNotNone(result)
            self.assertEqual(result["name"], "testrepo")
            self.assertEqual(result["owner"]["login"], "testowner")
            self.assertFalse(result["isPrivate"])
    
    def test_check_repository_access_success(self):
        """Test successful repository access check."""
        with patch.object(self.wrapper, '_run_gh_command') as mock_run:
            mock_run.return_value = CLIResult(
                success=True, stdout='{"name": "testrepo"}', stderr="",
                return_code=0, command=[], execution_time=0.2
            )
            
            result = self.wrapper.check_repository_access()
            
            self.assertTrue(result)
    
    def test_check_repository_access_failure(self):
        """Test failed repository access check."""
        with patch.object(self.wrapper, '_run_gh_command') as mock_run:
            mock_run.return_value = CLIResult(
                success=False, stdout="", stderr="access denied",
                return_code=1, command=[], execution_time=0.2
            )
            
            result = self.wrapper.check_repository_access()
            
            self.assertFalse(result)
    
    def test_is_authenticated(self):
        """Test authentication status check."""
        self.wrapper.auth_status = AuthenticationStatus.AUTHENTICATED
        self.assertTrue(self.wrapper.is_authenticated())
        
        self.wrapper.auth_status = AuthenticationStatus.NOT_AUTHENTICATED
        self.assertFalse(self.wrapper.is_authenticated())
    
    def test_get_current_user_success(self):
        """Test successful current user retrieval."""
        with patch.object(self.wrapper, '_run_gh_command') as mock_run:
            mock_run.return_value = CLIResult(
                success=True, stdout="testuser", stderr="", return_code=0,
                command=[], execution_time=0.2
            )
            
            result = self.wrapper.get_current_user()
            
            self.assertEqual(result, "testuser")
    
    def test_get_current_user_failure(self):
        """Test failed current user retrieval."""
        with patch.object(self.wrapper, '_run_gh_command') as mock_run:
            mock_run.return_value = CLIResult(
                success=False, stdout="", stderr="auth error", return_code=1,
                command=[], execution_time=0.2
            )
            
            result = self.wrapper.get_current_user()
            
            self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()