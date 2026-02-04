#!/usr/bin/env python3
"""
Unit tests for branch management functionality.

Comprehensive test suite covering branch manager, naming conventions,
and cleanup functionality with 90%+ coverage requirement.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock, call
import subprocess
import tempfile
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from branches.manager import (
    BranchManager, BranchInfo, BranchStatus, BranchOperationResult,
    BranchConflictInfo, ConflictType
)
from branches.naming import (
    BranchNamingValidator, BranchNamingConfig, BranchType,
    NamingValidationResult, generate_branch_name, validate_branch_name,
    suggest_branch_name
)
from branches.cleanup import (
    BranchCleanupManager, CleanupPolicy, CleanupCandidate,
    CleanupReason, CleanupAction, CleanupResult, BranchCleanupStats
)
from cli.wrapper import GitHubCLIWrapper, PRInfo


class TestBranchNaming(unittest.TestCase):
    """Test branch naming conventions and validation."""
    
    def setUp(self):
        self.validator = BranchNamingValidator()
        self.config = BranchNamingConfig()
    
    def test_valid_branch_names(self):
        """Test validation of valid branch names."""
        valid_names = [
            "feature/user-authentication",
            "bugfix/123-login-error",
            "hotfix/critical-security-fix",
            "feature/api-refactor",
            "docs/update-readme"
        ]
        
        for name in valid_names:
            result = self.validator.validate(name)
            self.assertTrue(result.valid, f"Branch name '{name}' should be valid")
    
    def test_invalid_branch_names(self):
        """Test validation of invalid branch names."""
        invalid_names = [
            "",  # Empty
            "a",  # Too short
            "x" * 100,  # Too long
            "feature/User-Auth",  # Uppercase without config
            "feature//double-slash",  # Double slash
            "feature/--double-dash",  # Double dash
            "-leading-dash",  # Leading dash
            "trailing-dash-",  # Trailing dash
            "feature/test!@#",  # Special characters
        ]
        
        for name in invalid_names:
            result = self.validator.validate(name)
            self.assertFalse(result.valid, f"Branch name '{name}' should be invalid")
            self.assertIsNotNone(result.error)
    
    def test_branch_name_warnings(self):
        """Test warning detection in branch names."""
        # Test forbidden words
        result = self.validator.validate("feature/test-implementation")
        self.assertTrue(result.valid)
        self.assertIsNotNone(result.warnings)
        self.assertTrue(any("discouraged words" in w for w in result.warnings))
        
        # Test missing prefix
        result = self.validator.validate("user-authentication-feature")
        self.assertTrue(result.valid)
        self.assertIsNotNone(result.warnings)
        self.assertTrue(any("branch type prefix" in w for w in result.warnings))
    
    def test_branch_name_generation(self):
        """Test automatic branch name generation."""
        # Basic generation
        name = generate_branch_name("User Authentication Feature", BranchType.FEATURE)
        self.assertTrue(name.startswith("feature/"))
        self.assertIn("user-authentication", name)
        
        # With issue number
        name = generate_branch_name("Fix Login Bug", BranchType.BUGFIX, issue_number=123)
        self.assertTrue(name.startswith("bugfix/"))
        self.assertIn("123", name)
        self.assertIn("fix-login", name)
        
        # Length constraints
        long_description = "Very Long Feature Description That Exceeds Normal Length Limits"
        name = generate_branch_name(long_description, BranchType.FEATURE)
        self.assertLessEqual(len(name), self.config.max_length)
    
    def test_branch_name_suggestions(self):
        """Test branch name suggestions."""
        suggestions = suggest_branch_name("User Auth Feature", branch_type=BranchType.FEATURE)
        self.assertGreater(len(suggestions), 0)
        self.assertTrue(all(s.startswith("feature/") for s in suggestions))
        
        # With existing names to avoid conflicts
        existing = ["feature/user-auth-feature"]
        suggestions = suggest_branch_name("User Auth Feature", existing_names=existing)
        self.assertNotIn("feature/user-auth-feature", suggestions)
    
    def test_custom_naming_config(self):
        """Test custom naming configuration."""
        config = BranchNamingConfig(
            allow_uppercase=True,
            word_separator="_",
            require_issue_prefix=True
        )
        validator = BranchNamingValidator(config)
        
        # Should allow uppercase
        result = validator.validate("feature/User_Auth")
        self.assertTrue(result.valid)
        
        # Should require issue prefix
        result = validator.validate("feature/user_auth")
        self.assertTrue(result.valid)
        # Should have warning about missing issue prefix
        self.assertIsNotNone(result.warnings)


class TestBranchManager(unittest.TestCase):
    """Test branch management functionality."""
    
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.mock_github = Mock(spec=GitHubCLIWrapper)
        self.manager = BranchManager(
            workspace_path=self.temp_dir,
            github_wrapper=self.mock_github
        )
    
    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    @patch('branches.manager.subprocess.run')
    def test_create_feature_branch_success(self, mock_run):
        """Test successful feature branch creation."""
        # Mock git commands
        mock_run.side_effect = [
            # git checkout main
            subprocess.CompletedProcess([], 0, "", ""),
            # git pull origin main
            subprocess.CompletedProcess([], 0, "", ""),
            # git checkout -b feature/test-feature
            subprocess.CompletedProcess([], 0, "", ""),
            # git push -u origin feature/test-feature
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        
        result = self.manager.create_feature_branch("Test Feature")
        
        self.assertTrue(result.success)
        self.assertIsNotNone(result.branch_name)
        self.assertTrue(result.branch_name.startswith("feature/"))
        self.assertIn("test-feature", result.branch_name)
    
    @patch('branches.manager.subprocess.run')
    def test_create_feature_branch_failure(self, mock_run):
        """Test feature branch creation failure."""
        # Mock git command failure
        mock_run.side_effect = [
            # git checkout main - success
            subprocess.CompletedProcess([], 0, "", ""),
            # git pull origin main - success
            subprocess.CompletedProcess([], 0, "", ""),
            # git checkout -b feature/test-feature - failure
            subprocess.CompletedProcess([], 1, "", "Branch already exists"),
        ]
        
        result = self.manager.create_feature_branch("Test Feature")
        
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)
    
    @patch('branches.manager.subprocess.run')
    def test_sync_branch_success(self, mock_run):
        """Test successful branch synchronization."""
        # Mock successful rebase
        mock_run.side_effect = [
            # git fetch origin
            subprocess.CompletedProcess([], 0, "", ""),
            # git rebase origin/main
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        
        result = self.manager.sync_branch("feature/test-branch")
        
        self.assertTrue(result.success)
        self.assertIn("rebased", result.message.lower())
    
    @patch('branches.manager.subprocess.run')
    def test_sync_branch_with_conflicts(self, mock_run):
        """Test branch synchronization with conflicts."""
        # Mock rebase conflict
        mock_run.side_effect = [
            # git fetch origin
            subprocess.CompletedProcess([], 0, "", ""),
            # git rebase origin/main - conflict
            subprocess.CompletedProcess([], 1, "", "CONFLICT: Merge conflict"),
            # git status --porcelain
            subprocess.CompletedProcess([], 0, "UU conflicted_file.py\n", ""),
        ]
        
        # Mock conflict detection
        with patch.object(self.manager, '_detect_rebase_conflicts') as mock_conflicts:
            mock_conflicts.return_value = BranchConflictInfo(
                conflict_type=ConflictType.REBASE_CONFLICT,
                branch_name="feature/test",
                target_branch="main",
                conflicted_files=["conflicted_file.py"],
                conflict_details="Rebase conflicts detected",
                resolution_suggestions=["Edit files", "git add", "git rebase --continue"],
                can_auto_resolve=False
            )
            
            result = self.manager.sync_branch("feature/test-branch")
        
        self.assertFalse(result.success)
        self.assertIsNotNone(result.conflicts)
        self.assertEqual(result.conflicts.conflict_type, ConflictType.REBASE_CONFLICT)
    
    @patch('branches.manager.subprocess.run')
    def test_get_branch_info(self, mock_run):
        """Test getting branch information."""
        # Mock git commands for branch info
        mock_run.side_effect = [
            # git branch --show-current
            subprocess.CompletedProcess([], 0, "feature/test-branch", ""),
            # git rev-parse --abbrev-ref feature/test-branch@{upstream}
            subprocess.CompletedProcess([], 0, "origin/feature/test-branch", ""),
            # git rev-list --left-right --count
            subprocess.CompletedProcess([], 0, "2\t3", ""),  # behind:2, ahead:3
            # git log -1 --format
            subprocess.CompletedProcess([], 0, "abc123|2024-01-15 10:30:00 +0000|John Doe", ""),
            # git branch --merged main
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        
        # Mock PR check
        self.mock_github.list_pull_requests.return_value = [
            PRInfo(
                number=123,
                title="Test PR",
                state="open",
                author="testuser",
                base_ref="main",
                head_ref="feature/test-branch",
                url="https://github.com/test/repo/pull/123",
                draft=False,
                mergeable="MERGEABLE",
                created_at="2024-01-15T10:00:00Z",
                updated_at="2024-01-15T10:30:00Z"
            )
        ]
        
        info = self.manager.get_branch_info("feature/test-branch")
        
        self.assertIsNotNone(info)
        self.assertEqual(info.name, "feature/test-branch")
        self.assertEqual(info.ahead, 3)
        self.assertEqual(info.behind, 2)
        self.assertTrue(info.has_pr)
        self.assertEqual(info.pr_number, 123)
    
    @patch('branches.manager.subprocess.run')
    def test_delete_branch(self, mock_run):
        """Test branch deletion."""
        # Mock git commands
        mock_run.side_effect = [
            # git branch --show-current (not on the branch to delete)
            subprocess.CompletedProcess([], 0, "main", ""),
            # git rev-parse --verify feature/test-branch
            subprocess.CompletedProcess([], 0, "", ""),
            # git branch -d feature/test-branch
            subprocess.CompletedProcess([], 0, "", ""),
            # git push origin --delete feature/test-branch
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        
        result = self.manager.delete_branch("feature/test-branch")
        
        self.assertTrue(result.success)
        self.assertIn("deleted", result.message.lower())
    
    def test_delete_protected_branch(self):
        """Test deletion of protected branch fails."""
        result = self.manager.delete_branch("main")
        
        self.assertFalse(result.success)
        self.assertIn("protected", result.error.lower())
    
    @patch('branches.manager.subprocess.run')
    def test_list_branches(self, mock_run):
        """Test listing branches."""
        # Mock git branch command
        mock_run.side_effect = [
            # git branch --format
            subprocess.CompletedProcess([], 0, "main\nfeature/branch1\nfeature/branch2", ""),
            # git branch -r --format (for remote branches)
            subprocess.CompletedProcess([], 0, "origin/main\norigin/feature/branch3", ""),
        ]
        
        # Mock branch info calls
        with patch.object(self.manager, 'get_branch_info') as mock_info:
            mock_info.side_effect = [
                BranchInfo("main", BranchStatus.PROTECTED, False, "origin", "origin/main"),
                BranchInfo("feature/branch1", BranchStatus.ACTIVE, True, "origin", "origin/feature/branch1"),
                BranchInfo("feature/branch2", BranchStatus.STALE, False, "origin", "origin/feature/branch2"),
                BranchInfo("feature/branch3", BranchStatus.ACTIVE, False, "origin", "origin/feature/branch3"),
            ]
            
            branches = self.manager.list_branches()
        
        self.assertEqual(len(branches), 4)
        self.assertTrue(any(b.name == "main" for b in branches))
        self.assertTrue(any(b.name == "feature/branch1" for b in branches))


class TestBranchCleanup(unittest.TestCase):
    """Test branch cleanup functionality."""
    
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.mock_github = Mock(spec=GitHubCLIWrapper)
        self.mock_branch_manager = Mock(spec=BranchManager)
        self.policy = CleanupPolicy()
        self.cleanup_manager = BranchCleanupManager(
            workspace_path=self.temp_dir,
            github_wrapper=self.mock_github,
            branch_manager=self.mock_branch_manager,
            policy=self.policy
        )
    
    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_identify_merged_branch_candidates(self):
        """Test identification of merged branches for cleanup."""
        # Mock merged branch
        merged_branch = BranchInfo(
            name="feature/completed-feature",
            status=BranchStatus.MERGED,
            current=False,
            remote="origin",
            upstream="origin/feature/completed-feature",
            last_activity=datetime.now(timezone.utc) - timedelta(days=10)
        )
        
        self.mock_branch_manager.list_branches.return_value = [merged_branch]
        self.mock_github.list_pull_requests.return_value = []
        
        candidates = self.cleanup_manager.identify_cleanup_candidates()
        
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].reason, CleanupReason.MERGED)
        self.assertTrue(candidates[0].can_auto_cleanup)
        self.assertGreater(candidates[0].safety_score, 0.8)
    
    def test_identify_stale_branch_candidates(self):
        """Test identification of stale branches for cleanup."""
        # Mock stale branch
        stale_branch = BranchInfo(
            name="feature/old-feature",
            status=BranchStatus.STALE,
            current=False,
            remote="origin",
            upstream="origin/feature/old-feature",
            ahead=2,
            last_activity=datetime.now(timezone.utc) - timedelta(days=45)
        )
        
        self.mock_branch_manager.list_branches.return_value = [stale_branch]
        self.mock_github.list_pull_requests.return_value = []
        
        candidates = self.cleanup_manager.identify_cleanup_candidates()
        
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].reason, CleanupReason.STALE)
        # Should not auto-cleanup due to unmerged commits
        self.assertFalse(candidates[0].can_auto_cleanup)
        self.assertLess(candidates[0].safety_score, 0.6)
    
    def test_identify_closed_pr_candidates(self):
        """Test identification of branches with closed PRs."""
        branch_with_closed_pr = BranchInfo(
            name="feature/closed-pr-branch",
            status=BranchStatus.ACTIVE,
            current=False,
            remote="origin",
            upstream="origin/feature/closed-pr-branch",
            last_activity=datetime.now(timezone.utc) - timedelta(days=5)
        )
        
        closed_pr = PRInfo(
            number=456,
            title="Closed PR",
            state="closed",
            author="testuser",
            base_ref="main",
            head_ref="feature/closed-pr-branch",
            url="https://github.com/test/repo/pull/456",
            draft=False,
            mergeable="MERGEABLE",
            created_at="2024-01-10T10:00:00Z",
            updated_at=(datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        )
        
        self.mock_branch_manager.list_branches.return_value = [branch_with_closed_pr]
        self.mock_github.list_pull_requests.return_value = [closed_pr]
        
        candidates = self.cleanup_manager.identify_cleanup_candidates()
        
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].reason, CleanupReason.PR_CLOSED)
        self.assertTrue(candidates[0].can_auto_cleanup)
    
    def test_cleanup_execution_dry_run(self):
        """Test cleanup execution in dry run mode."""
        candidate = CleanupCandidate(
            branch_info=BranchInfo("feature/test", BranchStatus.MERGED, False, "origin", None),
            reason=CleanupReason.MERGED,
            recommended_action=CleanupAction.DELETE_BOTH,
            safety_score=0.9,
            details="Merged 10 days ago",
            can_auto_cleanup=True
        )
        
        results, stats = self.cleanup_manager.cleanup_branches([candidate], dry_run=True)
        
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)
        self.assertIn("dry run", results[0].message.lower())
        self.assertEqual(stats.total_candidates, 1)
        self.assertEqual(stats.auto_cleaned, 1)
    
    def test_cleanup_execution_with_deletion(self):
        """Test actual cleanup execution with branch deletion."""
        candidate = CleanupCandidate(
            branch_info=BranchInfo("feature/test", BranchStatus.MERGED, False, "origin", None),
            reason=CleanupReason.MERGED,
            recommended_action=CleanupAction.DELETE_LOCAL,
            safety_score=0.9,
            details="Merged 10 days ago",
            can_auto_cleanup=True
        )
        
        # Mock successful deletion
        delete_result = BranchOperationResult(success=True, message="Branch deleted")
        self.mock_branch_manager.delete_branch.return_value = delete_result
        
        results, stats = self.cleanup_manager.cleanup_branches([candidate], dry_run=False)
        
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)
        self.assertTrue(results[0].deleted_local)
        self.assertEqual(stats.auto_cleaned, 1)
    
    def test_protected_branch_skipping(self):
        """Test that protected branches are not identified for cleanup."""
        protected_branch = BranchInfo(
            name="main",
            status=BranchStatus.PROTECTED,
            current=False,
            remote="origin",
            upstream="origin/main"
        )
        
        self.mock_branch_manager.list_branches.return_value = [protected_branch]
        self.mock_github.list_pull_requests.return_value = []
        
        candidates = self.cleanup_manager.identify_cleanup_candidates()
        
        self.assertEqual(len(candidates), 0)
    
    def test_current_branch_skipping(self):
        """Test that current branch is not identified for cleanup."""
        current_branch = BranchInfo(
            name="feature/current",
            status=BranchStatus.CURRENT,
            current=True,
            remote="origin",
            upstream="origin/feature/current"
        )
        
        self.mock_branch_manager.list_branches.return_value = [current_branch]
        self.mock_github.list_pull_requests.return_value = []
        
        candidates = self.cleanup_manager.identify_cleanup_candidates()
        
        self.assertEqual(len(candidates), 0)
    
    def test_cleanup_report_generation(self):
        """Test cleanup report generation."""
        results = [
            CleanupResult(
                success=True,
                branch_name="feature/merged1",
                action=CleanupAction.DELETE_BOTH,
                reason=CleanupReason.MERGED,
                message="local deleted, remote deleted",
                deleted_local=True,
                deleted_remote=True
            ),
            CleanupResult(
                success=True,
                branch_name="feature/stale1",
                action=CleanupAction.SKIP,
                reason=CleanupReason.STALE,
                message="Skipped due to policy"
            ),
            CleanupResult(
                success=False,
                branch_name="feature/failed1",
                action=CleanupAction.DELETE_LOCAL,
                reason=CleanupReason.MERGED,
                error="Permission denied"
            )
        ]
        
        stats = BranchCleanupStats(
            total_candidates=3,
            auto_cleaned=1,
            manual_cleaned=0,
            skipped=1,
            failed=1,
            merged_cleaned=2,
            local_deleted=1,
            remote_deleted=1,
            cleanup_duration=1.5
        )
        
        report = self.cleanup_manager.generate_cleanup_report(results, stats)
        
        self.assertIn("Branch Cleanup Report", report)
        self.assertIn("Total candidates: 3", report)
        self.assertIn("Auto cleaned: 1", report)
        self.assertIn("Failed: 1", report)
        self.assertIn("Duration: 1.50 seconds", report)
        self.assertIn("feature/merged1", report)
        self.assertIn("feature/failed1", report)
    
    def test_custom_cleanup_policy(self):
        """Test cleanup with custom policy settings."""
        custom_policy = CleanupPolicy(
            stale_threshold_days=15,
            auto_delete_merged=False,
            auto_delete_stale=True,
            protected_branches={"main", "develop", "custom-protected"}
        )
        
        cleanup_manager = BranchCleanupManager(
            workspace_path=self.temp_dir,
            policy=custom_policy
        )
        
        # Test that custom settings are applied
        self.assertEqual(cleanup_manager.policy.stale_threshold_days, 15)
        self.assertFalse(cleanup_manager.policy.auto_delete_merged)
        self.assertTrue(cleanup_manager.policy.auto_delete_stale)
        self.assertIn("custom-protected", cleanup_manager.policy.protected_branches)


class TestIntegration(unittest.TestCase):
    """Integration tests for branch management components."""
    
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        # Note: These are integration tests that would normally require a real git repo
        # In a real implementation, you'd use a test git repository
    
    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_end_to_end_branch_workflow(self):
        """Test complete branch workflow from creation to cleanup."""
        # This would test:
        # 1. Creating a branch with proper naming
        # 2. Making commits
        # 3. Creating a PR
        # 4. Merging the PR
        # 5. Identifying the branch for cleanup
        # 6. Cleaning up the branch
        
        # Mock components for this integration test
        mock_github = Mock(spec=GitHubCLIWrapper)
        validator = BranchNamingValidator()
        
        # Test naming validation
        feature_name = "User Authentication System"
        branch_name = generate_branch_name(feature_name, BranchType.FEATURE)
        
        validation_result = validator.validate(branch_name)
        self.assertTrue(validation_result.valid)
        
        # Mock branch manager operations
        with patch('branches.manager.subprocess.run') as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess([], 0, "", ""),  # checkout main
                subprocess.CompletedProcess([], 0, "", ""),  # pull
                subprocess.CompletedProcess([], 0, "", ""),  # create branch
                subprocess.CompletedProcess([], 0, "", ""),  # push
            ]
            
            branch_manager = BranchManager(self.temp_dir, mock_github)
            result = branch_manager.create_feature_branch(feature_name)
            
            self.assertTrue(result.success)
            self.assertEqual(result.branch_name, branch_name)


def run_coverage_analysis():
    """Run coverage analysis and ensure 90%+ coverage."""
    try:
        import coverage
        
        cov = coverage.Coverage()
        cov.start()
        
        # Run all tests
        suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        
        cov.stop()
        cov.save()
        
        # Generate coverage report
        print("\n" + "="*50)
        print("COVERAGE ANALYSIS")
        print("="*50)
        
        cov.report(show_missing=True)
        
        # Get coverage percentage
        total_coverage = cov.report()
        
        if total_coverage >= 90:
            print(f"\n✅ Coverage requirement met: {total_coverage:.1f}%")
            return True
        else:
            print(f"\n❌ Coverage requirement not met: {total_coverage:.1f}% < 90%")
            return False
            
    except ImportError:
        print("Coverage package not available. Install with: pip install coverage")
        return False


if __name__ == '__main__':
    # Run tests
    print("Running branch management tests...")
    
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestBranchNaming))
    suite.addTests(loader.loadTestsFromTestCase(TestBranchManager))
    suite.addTests(loader.loadTestsFromTestCase(TestBranchCleanup))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegration))
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Print summary
    print(f"\n{'='*50}")
    print(f"TESTS SUMMARY")
    print(f"{'='*50}")
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped) if hasattr(result, 'skipped') else 0}")
    
    if result.failures:
        print(f"\n❌ FAILURES:")
        for test, traceback in result.failures:
            print(f"  - {test}: {traceback.split(chr(10))[-2]}")
    
    if result.errors:
        print(f"\n❌ ERRORS:")
        for test, traceback in result.errors:
            print(f"  - {test}: {traceback.split(chr(10))[-2]}")
    
    success = len(result.failures) == 0 and len(result.errors) == 0
    
    if success:
        print(f"\n✅ All tests passed!")
    else:
        print(f"\n❌ Some tests failed!")
    
    # Run coverage analysis if available
    print(f"\nAttempting coverage analysis...")
    coverage_ok = run_coverage_analysis()
    
    # Exit with appropriate code
    exit_code = 0 if success and coverage_ok else 1
    exit(exit_code)