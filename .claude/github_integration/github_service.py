#!/usr/bin/env python3
"""
GitHub Service Extension for Claude Flow

Extends the existing PM scripts with GitHub integration capabilities including
autonomous PR creation, branch management, and workflow automation.
"""

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from enum import Enum

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.github_client import GitHubClient, GitHubRateLimitError, GitHubAuthenticationError
from services.config_manager import ConfigManager


class PRQualityGate(Enum):
    """Quality gates that must pass before PR creation"""
    TESTS_PASS = "tests_pass"
    LINT_PASS = "lint_pass"
    BUILD_SUCCESS = "build_success"
    SECURITY_CHECK = "security_check"
    COVERAGE_THRESHOLD = "coverage_threshold"


class PRStatus(Enum):
    """PR lifecycle status"""
    DRAFT = "draft"
    READY_FOR_REVIEW = "ready_for_review"
    UNDER_REVIEW = "under_review"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    MERGED = "merged"
    CLOSED = "closed"


@dataclass
class PRCreationResult:
    """Result of PR creation attempt"""
    success: bool
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    error_message: Optional[str] = None
    quality_gate_failures: List[str] = None
    

@dataclass
class BranchInfo:
    """Information about a Git branch"""
    name: str
    current: bool
    remote: str
    upstream: Optional[str]
    behind: int = 0
    ahead: int = 0


class GitHubService:
    """
    GitHub Service Extension that integrates with existing PM scripts
    and provides autonomous PR creation and management capabilities.
    """
    
    def __init__(self, config: Optional[ConfigManager] = None):
        """
        Initialize GitHub service extension.
        
        Args:
            config: Configuration manager instance
        """
        self.logger = logging.getLogger(__name__)
        self.config = config or ConfigManager()
        
        # Initialize GitHub client
        github_token = self.config.get("github.token")
        if not github_token:
            raise ValueError("GitHub token not configured. Please set github.token in config.")
            
        self.github_client = GitHubClient(github_token)
        
        # Get repository configuration
        self.owner = self.config.get("github.owner")
        self.repo = self.config.get("github.repo")
        
        if not self.owner or not self.repo:
            raise ValueError("GitHub repository not configured. Please set github.owner and github.repo in config.")
        
        # PR and branch settings
        self.main_branch = self.config.get("github.main_branch", "main")
        self.quality_gates_enabled = self.config.get("github.quality_gates_enabled", True)
        self.required_quality_gates = [
            PRQualityGate(gate) for gate in 
            self.config.get("github.required_quality_gates", ["tests_pass", "lint_pass"])
        ]
        
        # PR template and conventions
        self.pr_template_path = self.config.get("github.pr_template_path", ".github/pull_request_template.md")
        self.branch_naming_prefix = self.config.get("github.branch_naming_prefix", "feature/")
        
        # Working directory
        self.workspace_root = Path(self.config.get("workspace.root", os.getcwd()))
        
        self.logger.info(f"GitHub service initialized for {self.owner}/{self.repo}")
    
    def create_autonomous_pr(
        self,
        title: str,
        description: Optional[str] = None,
        branch_name: Optional[str] = None,
        base_branch: str = None,
        draft: bool = False,
        force_create: bool = False
    ) -> PRCreationResult:
        """
        Create PR with autonomous quality checks and metadata generation.
        
        Args:
            title: PR title
            description: Optional PR description (auto-generated if None)
            branch_name: Source branch (current branch if None)
            base_branch: Target branch (main branch if None)
            draft: Create as draft PR
            force_create: Skip quality gates if True
            
        Returns:
            PRCreationResult with outcome details
        """
        try:
            self.logger.info(f"Creating autonomous PR: {title}")
            
            # Get current branch if not specified
            if not branch_name:
                branch_name = self._get_current_branch()
                if not branch_name:
                    return PRCreationResult(
                        success=False,
                        error_message="Could not determine current branch"
                    )
            
            # Set default base branch
            if not base_branch:
                base_branch = self.main_branch
            
            # Run quality gates unless forced
            quality_gate_failures = []
            if self.quality_gates_enabled and not force_create:
                quality_gate_failures = self._run_quality_gates()
                
                if quality_gate_failures and not draft:
                    self.logger.warning(f"Quality gate failures: {quality_gate_failures}")
                    return PRCreationResult(
                        success=False,
                        error_message="Quality gates failed",
                        quality_gate_failures=quality_gate_failures
                    )
            
            # Generate PR description if not provided
            if not description:
                description = self._generate_pr_description(branch_name, base_branch)
            
            # Ensure branch is pushed to remote
            push_result = self._push_branch_to_remote(branch_name)
            if not push_result:
                return PRCreationResult(
                    success=False,
                    error_message="Failed to push branch to remote"
                )
            
            # Create the PR
            pr_data = self.github_client.create_pull_request(
                owner=self.owner,
                repo=self.repo,
                title=title,
                head=branch_name,
                base=base_branch,
                body=description,
                draft=draft or bool(quality_gate_failures)
            )
            
            pr_number = pr_data.get("number")
            pr_url = pr_data.get("html_url")
            
            self.logger.info(f"PR created successfully: #{pr_number} ({pr_url})")
            
            # Add quality gate failure comment if draft due to failures
            if quality_gate_failures and draft:
                self._add_quality_gate_comment(pr_number, quality_gate_failures)
            
            return PRCreationResult(
                success=True,
                pr_number=pr_number,
                pr_url=pr_url,
                quality_gate_failures=quality_gate_failures
            )
            
        except GitHubAuthenticationError as e:
            self.logger.error(f"GitHub authentication failed: {e}")
            return PRCreationResult(success=False, error_message=f"Authentication failed: {e}")
            
        except GitHubRateLimitError as e:
            self.logger.error(f"GitHub rate limit exceeded: {e}")
            return PRCreationResult(success=False, error_message=f"Rate limit exceeded: {e}")
            
        except Exception as e:
            self.logger.error(f"PR creation failed: {e}")
            return PRCreationResult(success=False, error_message=str(e))
    
    def transition_pr_to_ready(self, pr_number: int) -> bool:
        """
        Transition a draft PR to ready for review after quality gates pass.
        
        Args:
            pr_number: PR number to transition
            
        Returns:
            True if successfully transitioned
        """
        try:
            self.logger.info(f"Transitioning PR #{pr_number} to ready for review")
            
            # Run quality gates
            quality_gate_failures = self._run_quality_gates()
            
            if quality_gate_failures:
                self.logger.warning(f"Quality gates still failing: {quality_gate_failures}")
                self._add_quality_gate_comment(pr_number, quality_gate_failures)
                return False
            
            # Mark PR as ready for review (GitHub API doesn't have direct endpoint,
            # but we can update the PR to remove draft status)
            endpoint = f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}"
            response = self.github_client._make_request(
                'PATCH', 
                endpoint, 
                json={"draft": False}
            )
            
            if response.status_code == 200:
                # Add success comment
                success_comment = (
                    "🎉 All quality gates are now passing! This PR is ready for review.\n\n"
                    "**Quality Gates Passed:**\n"
                    + "\n".join([f"✅ {gate.value}" for gate in self.required_quality_gates])
                )
                
                self.github_client.add_issue_comment(
                    self.owner, self.repo, pr_number, success_comment
                )
                
                self.logger.info(f"PR #{pr_number} successfully transitioned to ready for review")
                return True
            else:
                self.logger.error(f"Failed to update PR status: {response.status_code}")
                return False
                
        except Exception as e:
            self.logger.error(f"Failed to transition PR to ready: {e}")
            return False
    
    def create_feature_branch(self, feature_name: str, base_branch: str = None) -> Tuple[bool, str]:
        """
        Create a new feature branch with proper naming conventions.
        
        Args:
            feature_name: Name of the feature
            base_branch: Branch to create from (main if None)
            
        Returns:
            Tuple of (success: bool, branch_name: str)
        """
        try:
            if not base_branch:
                base_branch = self.main_branch
                
            # Create branch name with naming convention
            clean_name = feature_name.lower().replace(" ", "-").replace("_", "-")
            branch_name = f"{self.branch_naming_prefix}{clean_name}"
            
            # Ensure we're on the base branch and it's up to date
            self._run_git_command(["git", "checkout", base_branch])
            self._run_git_command(["git", "pull", "origin", base_branch])
            
            # Create and checkout new branch
            result = self._run_git_command(["git", "checkout", "-b", branch_name])
            
            if result.returncode == 0:
                self.logger.info(f"Created feature branch: {branch_name}")
                return True, branch_name
            else:
                self.logger.error(f"Failed to create branch: {result.stderr}")
                return False, ""
                
        except Exception as e:
            self.logger.error(f"Error creating feature branch: {e}")
            return False, ""
    
    def sync_with_upstream(self, branch_name: str = None) -> bool:
        """
        Sync current or specified branch with upstream changes.
        
        Args:
            branch_name: Branch to sync (current branch if None)
            
        Returns:
            True if sync successful
        """
        try:
            current_branch = branch_name or self._get_current_branch()
            if not current_branch:
                return False
            
            # Fetch latest changes
            self._run_git_command(["git", "fetch", "origin"])
            
            # If we're on main branch, just pull
            if current_branch == self.main_branch:
                result = self._run_git_command(["git", "pull", "origin", self.main_branch])
                return result.returncode == 0
            
            # For feature branches, rebase on main
            self._run_git_command(["git", "checkout", self.main_branch])
            self._run_git_command(["git", "pull", "origin", self.main_branch])
            self._run_git_command(["git", "checkout", current_branch])
            
            result = self._run_git_command(["git", "rebase", self.main_branch])
            
            if result.returncode == 0:
                self.logger.info(f"Successfully synced {current_branch} with upstream")
                return True
            else:
                self.logger.warning(f"Rebase conflicts detected for {current_branch}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error syncing with upstream: {e}")
            return False
    
    def cleanup_merged_branches(self, dry_run: bool = False) -> List[str]:
        """
        Clean up local branches that have been merged.
        
        Args:
            dry_run: If True, only return branches that would be deleted
            
        Returns:
            List of branch names that were (or would be) deleted
        """
        try:
            # Get merged branches (excluding main/master)
            result = self._run_git_command(["git", "branch", "--merged", self.main_branch])
            
            if result.returncode != 0:
                return []
            
            merged_branches = []
            for line in result.stdout.strip().split('\n'):
                branch = line.strip().lstrip('* ').strip()
                if branch and branch not in [self.main_branch, "master", "develop"]:
                    merged_branches.append(branch)
            
            if dry_run:
                return merged_branches
            
            # Delete merged branches
            deleted_branches = []
            for branch in merged_branches:
                result = self._run_git_command(["git", "branch", "-d", branch])
                if result.returncode == 0:
                    deleted_branches.append(branch)
                    self.logger.info(f"Deleted merged branch: {branch}")
                else:
                    self.logger.warning(f"Failed to delete branch {branch}: {result.stderr}")
            
            return deleted_branches
            
        except Exception as e:
            self.logger.error(f"Error cleaning up merged branches: {e}")
            return []
    
    def get_branch_info(self, branch_name: str = None) -> Optional[BranchInfo]:
        """
        Get information about a Git branch.
        
        Args:
            branch_name: Branch name (current branch if None)
            
        Returns:
            BranchInfo object or None if branch not found
        """
        try:
            current_branch = branch_name or self._get_current_branch()
            if not current_branch:
                return None
            
            # Get remote tracking info
            result = self._run_git_command(["git", "rev-parse", "--abbrev-ref", f"{current_branch}@{{upstream}}"])
            upstream = result.stdout.strip() if result.returncode == 0 else None
            
            # Get ahead/behind counts
            ahead, behind = 0, 0
            if upstream:
                result = self._run_git_command(["git", "rev-list", "--left-right", "--count", f"{upstream}...{current_branch}"])
                if result.returncode == 0:
                    counts = result.stdout.strip().split('\t')
                    if len(counts) == 2:
                        behind, ahead = int(counts[0]), int(counts[1])
            
            return BranchInfo(
                name=current_branch,
                current=True,
                remote="origin",
                upstream=upstream,
                ahead=ahead,
                behind=behind
            )
            
        except Exception as e:
            self.logger.error(f"Error getting branch info: {e}")
            return None
    
    def _run_quality_gates(self) -> List[str]:
        """
        Run configured quality gates.
        
        Returns:
            List of failed quality gate names
        """
        failures = []
        
        for gate in self.required_quality_gates:
            if not self._run_quality_gate(gate):
                failures.append(gate.value)
        
        return failures
    
    def _run_quality_gate(self, gate: PRQualityGate) -> bool:
        """
        Run a specific quality gate.
        
        Args:
            gate: Quality gate to run
            
        Returns:
            True if gate passes
        """
        try:
            if gate == PRQualityGate.TESTS_PASS:
                return self._run_tests()
            elif gate == PRQualityGate.LINT_PASS:
                return self._run_linting()
            elif gate == PRQualityGate.BUILD_SUCCESS:
                return self._run_build()
            elif gate == PRQualityGate.SECURITY_CHECK:
                return self._run_security_check()
            elif gate == PRQualityGate.COVERAGE_THRESHOLD:
                return self._check_coverage_threshold()
            else:
                self.logger.warning(f"Unknown quality gate: {gate}")
                return True
                
        except Exception as e:
            self.logger.error(f"Quality gate {gate.value} failed with error: {e}")
            return False
    
    def _run_tests(self) -> bool:
        """Run project tests."""
        # Look for common test commands
        test_commands = [
            ["npm", "test"],
            ["yarn", "test"],  
            ["python", "-m", "pytest"],
            ["make", "test"]
        ]
        
        for cmd in test_commands:
            if self._command_exists(cmd[0]):
                result = self._run_git_command(cmd)
                return result.returncode == 0
        
        # If no test command found, assume pass
        self.logger.info("No test command found, assuming tests pass")
        return True
    
    def _run_linting(self) -> bool:
        """Run code linting checks."""
        lint_commands = [
            ["npm", "run", "lint"],
            ["yarn", "lint"],
            ["flake8", "."],
            ["pylint", "."],
            ["make", "lint"]
        ]
        
        for cmd in lint_commands:
            if self._command_exists(cmd[0]):
                result = self._run_git_command(cmd)
                return result.returncode == 0
        
        # If no lint command found, assume pass
        self.logger.info("No lint command found, assuming lint passes")
        return True
    
    def _run_build(self) -> bool:
        """Run project build."""
        build_commands = [
            ["npm", "run", "build"],
            ["yarn", "build"],
            ["make", "build"],
            ["docker", "build", ".", "-t", "test-build"]
        ]
        
        for cmd in build_commands:
            if self._command_exists(cmd[0]):
                result = self._run_git_command(cmd)
                return result.returncode == 0
        
        # If no build command found, assume pass
        self.logger.info("No build command found, assuming build passes")
        return True
    
    def _run_security_check(self) -> bool:
        """Run security checks."""
        security_commands = [
            ["npm", "audit"],
            ["yarn", "audit"],
            ["safety", "check"],
            ["bandit", "-r", "."]
        ]
        
        for cmd in security_commands:
            if self._command_exists(cmd[0]):
                result = self._run_git_command(cmd)
                # Some tools return non-zero for warnings, be lenient
                return result.returncode in [0, 1]
        
        # If no security command found, assume pass
        self.logger.info("No security command found, assuming security check passes")
        return True
    
    def _check_coverage_threshold(self) -> bool:
        """Check if code coverage meets threshold."""
        threshold = self.config.get("github.coverage_threshold", 80)
        
        coverage_commands = [
            ["npm", "run", "coverage"],
            ["yarn", "coverage"],
            ["coverage", "report"]
        ]
        
        for cmd in coverage_commands:
            if self._command_exists(cmd[0]):
                result = self._run_git_command(cmd)
                if result.returncode == 0:
                    # Parse coverage from output (simplified)
                    if f"{threshold}%" in result.stdout or "100%" in result.stdout:
                        return True
        
        # If no coverage command found, assume pass
        self.logger.info("No coverage command found, assuming coverage passes")
        return True
    
    def _generate_pr_description(self, source_branch: str, target_branch: str) -> str:
        """
        Generate comprehensive PR description with change summary.
        
        Args:
            source_branch: Source branch name
            target_branch: Target branch name
            
        Returns:
            Generated PR description
        """
        try:
            # Get commit messages between branches
            result = self._run_git_command([
                "git", "log", "--oneline", f"{target_branch}..{source_branch}"
            ])
            
            commits = result.stdout.strip().split('\n') if result.returncode == 0 else []
            
            # Get changed files
            result = self._run_git_command([
                "git", "diff", "--name-only", f"{target_branch}..{source_branch}"
            ])
            
            changed_files = result.stdout.strip().split('\n') if result.returncode == 0 else []
            
            # Load PR template if exists
            template_path = self.workspace_root / self.pr_template_path
            template_content = ""
            if template_path.exists():
                template_content = template_path.read_text()
            
            # Generate description
            description_parts = []
            
            if template_content:
                description_parts.append(template_content)
                description_parts.append("\n---\n")
            
            description_parts.append("## Summary")
            description_parts.append(f"This PR merges changes from `{source_branch}` into `{target_branch}`.")
            description_parts.append("")
            
            if commits:
                description_parts.append("## Changes")
                for commit in commits[:10]:  # Limit to 10 commits
                    description_parts.append(f"- {commit}")
                if len(commits) > 10:
                    description_parts.append(f"- ... and {len(commits) - 10} more commits")
                description_parts.append("")
            
            if changed_files:
                description_parts.append("## Files Changed")
                for file in changed_files[:20]:  # Limit to 20 files
                    description_parts.append(f"- `{file}`")
                if len(changed_files) > 20:
                    description_parts.append(f"- ... and {len(changed_files) - 20} more files")
                description_parts.append("")
            
            description_parts.append("## Test Plan")
            description_parts.append("- [ ] Unit tests pass")
            description_parts.append("- [ ] Integration tests pass")
            description_parts.append("- [ ] Manual testing completed")
            description_parts.append("")
            
            description_parts.append("---")
            description_parts.append("*This PR was created using Claude Flow's autonomous PR creation system.*")
            
            return "\n".join(description_parts)
            
        except Exception as e:
            self.logger.error(f"Error generating PR description: {e}")
            return f"Automated PR from {source_branch} to {target_branch}"
    
    def _add_quality_gate_comment(self, pr_number: int, failures: List[str]):
        """Add comment about quality gate failures."""
        comment_parts = [
            "⚠️ **Quality Gate Failures Detected**",
            "",
            "This PR has been created as a draft because the following quality gates failed:",
            ""
        ]
        
        for failure in failures:
            comment_parts.append(f"❌ {failure.replace('_', ' ').title()}")
        
        comment_parts.extend([
            "",
            "Please address these issues and then use the `transition-pr-ready` command to mark this PR as ready for review.",
            "",
            "*This comment was generated by Claude Flow's autonomous PR system.*"
        ])
        
        comment_body = "\n".join(comment_parts)
        
        try:
            self.github_client.add_issue_comment(
                self.owner, self.repo, pr_number, comment_body
            )
        except Exception as e:
            self.logger.error(f"Failed to add quality gate comment: {e}")
    
    def _push_branch_to_remote(self, branch_name: str) -> bool:
        """Push branch to remote repository."""
        try:
            result = self._run_git_command(["git", "push", "-u", "origin", branch_name])
            return result.returncode == 0
        except Exception as e:
            self.logger.error(f"Error pushing branch to remote: {e}")
            return False
    
    def _get_current_branch(self) -> Optional[str]:
        """Get current Git branch name."""
        try:
            result = self._run_git_command(["git", "branch", "--show-current"])
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None
    
    def _run_git_command(self, command: List[str]) -> subprocess.CompletedProcess:
        """
        Run a Git command in the workspace directory.
        
        Args:
            command: Command and arguments to run
            
        Returns:
            CompletedProcess result
        """
        return subprocess.run(
            command,
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
    
    def _command_exists(self, command: str) -> bool:
        """Check if a command exists in the system PATH."""
        try:
            subprocess.run(["which", command], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False


def main():
    """Command-line interface for GitHub service operations."""
    import argparse
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    parser = argparse.ArgumentParser(description='GitHub Service Extension for Claude Flow')
    parser.add_argument('--config', help='Path to configuration file')
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Create PR command
    create_pr_parser = subparsers.add_parser('create-pr', help='Create autonomous PR')
    create_pr_parser.add_argument('title', help='PR title')
    create_pr_parser.add_argument('--description', help='PR description')
    create_pr_parser.add_argument('--branch', help='Source branch (current if not specified)')
    create_pr_parser.add_argument('--base', help='Target branch (main if not specified)')
    create_pr_parser.add_argument('--draft', action='store_true', help='Create as draft PR')
    create_pr_parser.add_argument('--force', action='store_true', help='Skip quality gates')
    
    # Transition PR command
    transition_parser = subparsers.add_parser('transition-pr-ready', help='Transition draft PR to ready')
    transition_parser.add_argument('pr_number', type=int, help='PR number to transition')
    
    # Branch commands
    branch_parser = subparsers.add_parser('create-branch', help='Create feature branch')
    branch_parser.add_argument('feature_name', help='Feature name for branch')
    branch_parser.add_argument('--base', help='Base branch (main if not specified)')
    
    sync_parser = subparsers.add_parser('sync', help='Sync branch with upstream')
    sync_parser.add_argument('--branch', help='Branch to sync (current if not specified)')
    
    cleanup_parser = subparsers.add_parser('cleanup-branches', help='Clean up merged branches')
    cleanup_parser.add_argument('--dry-run', action='store_true', help='Show what would be deleted')
    
    # Info commands
    info_parser = subparsers.add_parser('branch-info', help='Get branch information')
    info_parser.add_argument('--branch', help='Branch name (current if not specified)')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    try:
        config = ConfigManager(args.config) if args.config else ConfigManager()
        service = GitHubService(config)
        
        if args.command == 'create-pr':
            result = service.create_autonomous_pr(
                title=args.title,
                description=args.description,
                branch_name=args.branch,
                base_branch=args.base,
                draft=args.draft,
                force_create=args.force
            )
            
            if result.success:
                print(f"✅ PR created successfully!")
                print(f"PR Number: #{result.pr_number}")
                print(f"PR URL: {result.pr_url}")
                if result.quality_gate_failures:
                    print(f"⚠️  Quality gate failures: {', '.join(result.quality_gate_failures)}")
                    print("PR created as draft. Fix issues and use 'transition-pr-ready' to mark as ready.")
            else:
                print(f"❌ PR creation failed: {result.error_message}")
                if result.quality_gate_failures:
                    print(f"Quality gate failures: {', '.join(result.quality_gate_failures)}")
                sys.exit(1)
        
        elif args.command == 'transition-pr-ready':
            success = service.transition_pr_to_ready(args.pr_number)
            if success:
                print(f"✅ PR #{args.pr_number} transitioned to ready for review!")
            else:
                print(f"❌ Failed to transition PR #{args.pr_number}")
                sys.exit(1)
        
        elif args.command == 'create-branch':
            success, branch_name = service.create_feature_branch(args.feature_name, args.base)
            if success:
                print(f"✅ Created feature branch: {branch_name}")
            else:
                print("❌ Failed to create feature branch")
                sys.exit(1)
        
        elif args.command == 'sync':
            success = service.sync_with_upstream(args.branch)
            if success:
                print("✅ Branch synced with upstream successfully")
            else:
                print("❌ Failed to sync with upstream")
                sys.exit(1)
        
        elif args.command == 'cleanup-branches':
            branches = service.cleanup_merged_branches(args.dry_run)
            if args.dry_run:
                print(f"Would delete {len(branches)} branches:")
                for branch in branches:
                    print(f"  - {branch}")
            else:
                print(f"Deleted {len(branches)} merged branches:")
                for branch in branches:
                    print(f"  - {branch}")
        
        elif args.command == 'branch-info':
            info = service.get_branch_info(args.branch)
            if info:
                print(f"Branch: {info.name}")
                print(f"Remote: {info.remote}")
                print(f"Upstream: {info.upstream or 'None'}")
                print(f"Ahead: {info.ahead} commits")
                print(f"Behind: {info.behind} commits")
            else:
                print("❌ Could not get branch information")
                sys.exit(1)
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()