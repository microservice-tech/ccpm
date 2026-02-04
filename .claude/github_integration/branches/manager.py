#!/usr/bin/env python3
"""
Branch Manager for Claude Flow GitHub Integration

Provides comprehensive branch lifecycle management including creation, 
synchronization, conflict detection, and integration with GitHub CLI.
"""

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Set
from dataclasses import dataclass, asdict
from enum import Enum

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli.wrapper import GitHubCLIWrapper, GitHubCLIError
from .naming import BranchNamingValidator, BranchType, generate_branch_name


class BranchStatus(Enum):
    """Branch status enumeration."""
    CURRENT = "current"
    ACTIVE = "active" 
    MERGED = "merged"
    STALE = "stale"
    PROTECTED = "protected"
    UNKNOWN = "unknown"


class ConflictType(Enum):
    """Types of branch conflicts."""
    MERGE_CONFLICT = "merge_conflict"
    REBASE_CONFLICT = "rebase_conflict" 
    UPSTREAM_DIVERGED = "upstream_diverged"
    FAST_FORWARD_BLOCKED = "fast_forward_blocked"


@dataclass
class BranchInfo:
    """Comprehensive branch information."""
    name: str
    status: BranchStatus
    current: bool
    remote: Optional[str]
    upstream: Optional[str]
    ahead: int = 0
    behind: int = 0
    last_commit_hash: Optional[str] = None
    last_commit_date: Optional[str] = None
    last_commit_author: Optional[str] = None
    last_activity: Optional[datetime] = None
    protected: bool = False
    has_pr: bool = False
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None


@dataclass
class BranchConflictInfo:
    """Information about branch conflicts."""
    conflict_type: ConflictType
    branch_name: str
    target_branch: str
    conflicted_files: List[str]
    conflict_details: str
    resolution_suggestions: List[str]
    can_auto_resolve: bool = False


@dataclass
class BranchOperationResult:
    """Result of branch operations."""
    success: bool
    branch_name: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None
    conflicts: Optional[BranchConflictInfo] = None
    created_pr: bool = False
    pr_url: Optional[str] = None


class BranchManager:
    """
    Comprehensive branch management for Claude Flow GitHub integration.
    
    Provides branch lifecycle operations including:
    - Feature branch creation with conventions
    - Branch synchronization with upstream
    - Conflict detection and resolution guidance
    - Protection rule enforcement
    - Integration with GitHub PR workflow
    """
    
    def __init__(self, 
                 workspace_path: Optional[Path] = None,
                 github_wrapper: Optional[GitHubCLIWrapper] = None,
                 naming_validator: Optional[BranchNamingValidator] = None):
        """
        Initialize branch manager.
        
        Args:
            workspace_path: Working directory for git operations
            github_wrapper: GitHub CLI wrapper instance
            naming_validator: Branch naming validator
        """
        self.logger = logging.getLogger(__name__)
        self.workspace_path = workspace_path or Path.cwd()
        self.github_wrapper = github_wrapper or GitHubCLIWrapper(self.workspace_path)
        self.naming_validator = naming_validator or BranchNamingValidator()
        
        # Configuration
        self.main_branch = self._get_main_branch()
        self.protected_branches = self._get_protected_branches()
        self.stale_threshold_days = 30
        
        self.logger.info(f"Branch manager initialized for workspace: {self.workspace_path}")
        self.logger.info(f"Main branch: {self.main_branch}")
    
    def create_feature_branch(self,
                            feature_name: str,
                            branch_type: BranchType = BranchType.FEATURE,
                            base_branch: Optional[str] = None,
                            sync_first: bool = True,
                            create_pr: bool = False) -> BranchOperationResult:
        """
        Create a new feature branch with proper naming conventions.
        
        Args:
            feature_name: Name/description of the feature
            branch_type: Type of branch to create
            base_branch: Branch to create from (main if None)
            sync_first: Sync base branch before creating
            create_pr: Create draft PR immediately
            
        Returns:
            BranchOperationResult with outcome details
        """
        try:
            self.logger.info(f"Creating {branch_type.value} branch for: {feature_name}")
            
            # Set default base branch
            base_branch = base_branch or self.main_branch
            
            # Generate branch name with naming conventions
            branch_name = generate_branch_name(feature_name, branch_type)
            
            # Validate branch name
            validation_result = self.naming_validator.validate(branch_name)
            if not validation_result.valid:
                return BranchOperationResult(
                    success=False,
                    error=f"Invalid branch name: {validation_result.error}"
                )
            
            # Check if branch already exists
            if self._branch_exists(branch_name):
                return BranchOperationResult(
                    success=False,
                    error=f"Branch '{branch_name}' already exists"
                )
            
            # Sync base branch if requested
            if sync_first:
                sync_result = self.sync_branch(base_branch)
                if not sync_result.success:
                    return BranchOperationResult(
                        success=False,
                        error=f"Failed to sync base branch '{base_branch}': {sync_result.error}"
                    )
            
            # Ensure we're on the base branch
            checkout_result = self._run_git_command(["git", "checkout", base_branch])
            if checkout_result.returncode != 0:
                return BranchOperationResult(
                    success=False,
                    error=f"Failed to checkout base branch: {checkout_result.stderr}"
                )
            
            # Create and checkout new branch
            create_result = self._run_git_command(["git", "checkout", "-b", branch_name])
            if create_result.returncode != 0:
                return BranchOperationResult(
                    success=False,
                    error=f"Failed to create branch: {create_result.stderr}"
                )
            
            # Set upstream tracking
            push_result = self._run_git_command(["git", "push", "-u", "origin", branch_name])
            push_success = push_result.returncode == 0
            
            if not push_success:
                self.logger.warning(f"Failed to set upstream tracking: {push_result.stderr}")
            
            self.logger.info(f"Successfully created branch: {branch_name}")
            
            result = BranchOperationResult(
                success=True,
                branch_name=branch_name,
                message=f"Created {branch_type.value} branch: {branch_name}"
            )
            
            # Create draft PR if requested
            if create_pr and push_success:
                pr_result = self._create_draft_pr(branch_name, base_branch, feature_name)
                if pr_result:
                    result.created_pr = True
                    result.pr_url = pr_result.url
                    result.message += f" with draft PR #{pr_result.number}"
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error creating feature branch: {e}")
            return BranchOperationResult(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )
    
    def sync_branch(self,
                   branch_name: Optional[str] = None,
                   target_branch: Optional[str] = None,
                   force_rebase: bool = False) -> BranchOperationResult:
        """
        Synchronize branch with upstream changes.
        
        Args:
            branch_name: Branch to sync (current branch if None)
            target_branch: Branch to sync with (main branch if None)
            force_rebase: Force rebase even if merge is safer
            
        Returns:
            BranchOperationResult with sync outcome
        """
        try:
            current_branch = branch_name or self._get_current_branch()
            if not current_branch:
                return BranchOperationResult(
                    success=False,
                    error="Could not determine current branch"
                )
            
            target_branch = target_branch or self.main_branch
            
            self.logger.info(f"Syncing branch '{current_branch}' with '{target_branch}'")
            
            # Fetch latest changes
            fetch_result = self._run_git_command(["git", "fetch", "origin"])
            if fetch_result.returncode != 0:
                return BranchOperationResult(
                    success=False,
                    error=f"Failed to fetch from origin: {fetch_result.stderr}"
                )
            
            # If syncing main branch, just pull
            if current_branch == self.main_branch:
                pull_result = self._run_git_command(["git", "pull", "origin", self.main_branch])
                return BranchOperationResult(
                    success=pull_result.returncode == 0,
                    branch_name=current_branch,
                    message=f"Synced {current_branch} with origin" if pull_result.returncode == 0 else None,
                    error=pull_result.stderr if pull_result.returncode != 0 else None
                )
            
            # For feature branches, check if rebase is safe
            branch_info = self.get_branch_info(current_branch)
            if not branch_info:
                return BranchOperationResult(
                    success=False,
                    error="Could not get branch information"
                )
            
            # Determine sync strategy
            use_rebase = force_rebase or (branch_info.behind > 0 and not self._has_merge_commits(current_branch))
            
            if use_rebase:
                # Rebase strategy
                rebase_result = self._run_git_command([
                    "git", "rebase", f"origin/{target_branch}"
                ])
                
                if rebase_result.returncode == 0:
                    return BranchOperationResult(
                        success=True,
                        branch_name=current_branch,
                        message=f"Successfully rebased {current_branch} on {target_branch}"
                    )
                else:
                    # Check for conflicts
                    conflicts = self._detect_rebase_conflicts()
                    if conflicts:
                        return BranchOperationResult(
                            success=False,
                            branch_name=current_branch,
                            error="Rebase conflicts detected",
                            conflicts=conflicts
                        )
                    else:
                        return BranchOperationResult(
                            success=False,
                            error=f"Rebase failed: {rebase_result.stderr}"
                        )
            else:
                # Merge strategy
                merge_result = self._run_git_command([
                    "git", "merge", f"origin/{target_branch}"
                ])
                
                if merge_result.returncode == 0:
                    return BranchOperationResult(
                        success=True,
                        branch_name=current_branch,
                        message=f"Successfully merged {target_branch} into {current_branch}"
                    )
                else:
                    # Check for conflicts
                    conflicts = self._detect_merge_conflicts(target_branch)
                    if conflicts:
                        return BranchOperationResult(
                            success=False,
                            branch_name=current_branch,
                            error="Merge conflicts detected",
                            conflicts=conflicts
                        )
                    else:
                        return BranchOperationResult(
                            success=False,
                            error=f"Merge failed: {merge_result.stderr}"
                        )
            
        except Exception as e:
            self.logger.error(f"Error syncing branch: {e}")
            return BranchOperationResult(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )
    
    def get_branch_info(self, branch_name: Optional[str] = None) -> Optional[BranchInfo]:
        """
        Get comprehensive information about a branch.
        
        Args:
            branch_name: Branch name (current branch if None)
            
        Returns:
            BranchInfo object or None if branch not found
        """
        try:
            branch_name = branch_name or self._get_current_branch()
            if not branch_name:
                return None
            
            # Get basic branch info
            current_branch = self._get_current_branch()
            is_current = branch_name == current_branch
            
            # Get remote tracking info
            upstream_result = self._run_git_command([
                "git", "rev-parse", "--abbrev-ref", f"{branch_name}@{{upstream}}"
            ])
            upstream = upstream_result.stdout.strip() if upstream_result.returncode == 0 else None
            
            # Get ahead/behind counts
            ahead, behind = 0, 0
            if upstream:
                count_result = self._run_git_command([
                    "git", "rev-list", "--left-right", "--count", f"{upstream}...{branch_name}"
                ])
                if count_result.returncode == 0:
                    counts = count_result.stdout.strip().split('\t')
                    if len(counts) == 2:
                        behind, ahead = int(counts[0]), int(counts[1])
            
            # Get last commit info
            commit_result = self._run_git_command([
                "git", "log", "-1", "--format=%H|%ci|%an", branch_name
            ])
            
            last_commit_hash = None
            last_commit_date = None
            last_commit_author = None
            last_activity = None
            
            if commit_result.returncode == 0 and commit_result.stdout.strip():
                parts = commit_result.stdout.strip().split('|')
                if len(parts) >= 3:
                    last_commit_hash = parts[0]
                    last_commit_date = parts[1]
                    last_commit_author = parts[2]
                    
                    # Parse activity date
                    try:
                        last_activity = datetime.fromisoformat(last_commit_date.replace(' ', 'T', 1))
                    except:
                        pass
            
            # Check if branch is protected
            is_protected = branch_name in self.protected_branches
            
            # Check for associated PR
            has_pr = False
            pr_number = None
            pr_url = None
            
            try:
                prs = self.github_wrapper.list_pull_requests(state="all", limit=100)
                for pr in prs:
                    if pr.head_ref == branch_name:
                        has_pr = True
                        pr_number = pr.number
                        pr_url = pr.url
                        break
            except:
                pass  # PR check is optional
            
            # Determine branch status
            status = BranchStatus.CURRENT if is_current else BranchStatus.ACTIVE
            
            if last_activity and last_activity < datetime.now(timezone.utc) - timedelta(days=self.stale_threshold_days):
                status = BranchStatus.STALE
            
            if is_protected:
                status = BranchStatus.PROTECTED
            
            # Check if merged
            if not is_current and not is_protected:
                merge_check = self._run_git_command([
                    "git", "branch", "--merged", self.main_branch
                ])
                if merge_check.returncode == 0 and branch_name in merge_check.stdout:
                    status = BranchStatus.MERGED
            
            return BranchInfo(
                name=branch_name,
                status=status,
                current=is_current,
                remote="origin" if upstream else None,
                upstream=upstream,
                ahead=ahead,
                behind=behind,
                last_commit_hash=last_commit_hash,
                last_commit_date=last_commit_date,
                last_commit_author=last_commit_author,
                last_activity=last_activity,
                protected=is_protected,
                has_pr=has_pr,
                pr_number=pr_number,
                pr_url=pr_url
            )
            
        except Exception as e:
            self.logger.error(f"Error getting branch info: {e}")
            return None
    
    def list_branches(self,
                     include_remote: bool = True,
                     include_merged: bool = False,
                     include_stale: bool = True) -> List[BranchInfo]:
        """
        List all branches with comprehensive information.
        
        Args:
            include_remote: Include remote-only branches
            include_merged: Include merged branches
            include_stale: Include stale branches
            
        Returns:
            List of BranchInfo objects
        """
        try:
            branches = []
            
            # Get local branches
            local_result = self._run_git_command(["git", "branch", "--format=%(refname:short)"])
            if local_result.returncode == 0:
                for branch_name in local_result.stdout.strip().split('\n'):
                    if branch_name.strip():
                        branch_info = self.get_branch_info(branch_name.strip())
                        if branch_info:
                            # Apply filters
                            if not include_merged and branch_info.status == BranchStatus.MERGED:
                                continue
                            if not include_stale and branch_info.status == BranchStatus.STALE:
                                continue
                            
                            branches.append(branch_info)
            
            # Get remote branches if requested
            if include_remote:
                remote_result = self._run_git_command([
                    "git", "branch", "-r", "--format=%(refname:short)"
                ])
                if remote_result.returncode == 0:
                    for remote_branch in remote_result.stdout.strip().split('\n'):
                        if remote_branch.strip() and not remote_branch.startswith('origin/HEAD'):
                            # Check if we already have local tracking branch
                            branch_name = remote_branch.replace('origin/', '')
                            if not any(b.name == branch_name for b in branches):
                                branch_info = self.get_branch_info(branch_name)
                                if branch_info:
                                    branches.append(branch_info)
            
            return sorted(branches, key=lambda b: (b.status.value, b.name))
            
        except Exception as e:
            self.logger.error(f"Error listing branches: {e}")
            return []
    
    def delete_branch(self,
                     branch_name: str,
                     force: bool = False,
                     delete_remote: bool = True) -> BranchOperationResult:
        """
        Delete a branch with safety checks.
        
        Args:
            branch_name: Branch to delete
            force: Force deletion even if not merged
            delete_remote: Also delete remote branch
            
        Returns:
            BranchOperationResult with deletion outcome
        """
        try:
            self.logger.info(f"Deleting branch: {branch_name}")
            
            # Safety checks
            if branch_name == self._get_current_branch():
                return BranchOperationResult(
                    success=False,
                    error="Cannot delete current branch"
                )
            
            if branch_name in self.protected_branches:
                return BranchOperationResult(
                    success=False,
                    error=f"Branch '{branch_name}' is protected"
                )
            
            # Check if branch exists
            if not self._branch_exists(branch_name):
                return BranchOperationResult(
                    success=False,
                    error=f"Branch '{branch_name}' does not exist"
                )
            
            # Delete local branch
            delete_flag = "-D" if force else "-d"
            local_result = self._run_git_command(["git", "branch", delete_flag, branch_name])
            
            if local_result.returncode != 0:
                return BranchOperationResult(
                    success=False,
                    error=f"Failed to delete local branch: {local_result.stderr}"
                )
            
            success_msg = f"Deleted local branch: {branch_name}"
            
            # Delete remote branch if requested
            if delete_remote:
                remote_result = self._run_git_command([
                    "git", "push", "origin", "--delete", branch_name
                ])
                if remote_result.returncode == 0:
                    success_msg += " and remote branch"
                else:
                    self.logger.warning(f"Failed to delete remote branch: {remote_result.stderr}")
                    success_msg += " (remote deletion failed)"
            
            self.logger.info(success_msg)
            
            return BranchOperationResult(
                success=True,
                branch_name=branch_name,
                message=success_msg
            )
            
        except Exception as e:
            self.logger.error(f"Error deleting branch: {e}")
            return BranchOperationResult(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )
    
    def protect_branch(self, branch_name: str) -> bool:
        """
        Add branch to protected branches list.
        
        Args:
            branch_name: Branch to protect
            
        Returns:
            True if successfully protected
        """
        if branch_name not in self.protected_branches:
            self.protected_branches.add(branch_name)
            self.logger.info(f"Branch '{branch_name}' added to protection list")
            return True
        return False
    
    def _create_draft_pr(self, branch_name: str, base_branch: str, feature_name: str):
        """Create a draft PR for the branch."""
        try:
            title = f"Draft: {feature_name}"
            body = f"This is a draft PR for feature: {feature_name}\n\nBranch: `{branch_name}`\nBase: `{base_branch}`"
            
            return self.github_wrapper.create_pull_request(
                title=title,
                body=body,
                head=branch_name,
                base=base_branch,
                draft=True
            )
        except Exception as e:
            self.logger.error(f"Failed to create draft PR: {e}")
            return None
    
    def _detect_merge_conflicts(self, target_branch: str) -> Optional[BranchConflictInfo]:
        """Detect and analyze merge conflicts."""
        try:
            # Get conflicted files
            status_result = self._run_git_command(["git", "status", "--porcelain"])
            if status_result.returncode != 0:
                return None
            
            conflicted_files = []
            for line in status_result.stdout.strip().split('\n'):
                if line.startswith('UU') or line.startswith('AA') or line.startswith('DD'):
                    conflicted_files.append(line[3:].strip())
            
            if not conflicted_files:
                return None
            
            # Generate resolution suggestions
            suggestions = [
                "Run 'git status' to see all conflicted files",
                "Edit conflicted files to resolve conflicts manually",
                "Use 'git add <file>' to stage resolved files",
                "Run 'git commit' to complete the merge",
                "Or use 'git merge --abort' to cancel the merge"
            ]
            
            return BranchConflictInfo(
                conflict_type=ConflictType.MERGE_CONFLICT,
                branch_name=self._get_current_branch() or "unknown",
                target_branch=target_branch,
                conflicted_files=conflicted_files,
                conflict_details=f"Merge conflicts in {len(conflicted_files)} files",
                resolution_suggestions=suggestions,
                can_auto_resolve=False
            )
            
        except Exception as e:
            self.logger.error(f"Error detecting merge conflicts: {e}")
            return None
    
    def _detect_rebase_conflicts(self) -> Optional[BranchConflictInfo]:
        """Detect and analyze rebase conflicts."""
        try:
            # Check if in rebase state
            rebase_dir = self.workspace_path / ".git" / "rebase-merge"
            if not rebase_dir.exists():
                rebase_dir = self.workspace_path / ".git" / "rebase-apply"
            
            if not rebase_dir.exists():
                return None
            
            # Get conflicted files
            status_result = self._run_git_command(["git", "status", "--porcelain"])
            if status_result.returncode != 0:
                return None
            
            conflicted_files = []
            for line in status_result.stdout.strip().split('\n'):
                if line.startswith('UU') or line.startswith('AA') or line.startswith('DD'):
                    conflicted_files.append(line[3:].strip())
            
            if not conflicted_files:
                return None
            
            # Generate resolution suggestions
            suggestions = [
                "Edit conflicted files to resolve conflicts manually",
                "Use 'git add <file>' to stage resolved files",
                "Run 'git rebase --continue' to continue the rebase",
                "Or use 'git rebase --abort' to cancel the rebase"
            ]
            
            return BranchConflictInfo(
                conflict_type=ConflictType.REBASE_CONFLICT,
                branch_name=self._get_current_branch() or "unknown",
                target_branch="upstream",
                conflicted_files=conflicted_files,
                conflict_details=f"Rebase conflicts in {len(conflicted_files)} files",
                resolution_suggestions=suggestions,
                can_auto_resolve=False
            )
            
        except Exception as e:
            self.logger.error(f"Error detecting rebase conflicts: {e}")
            return None
    
    def _branch_exists(self, branch_name: str) -> bool:
        """Check if branch exists locally."""
        result = self._run_git_command(["git", "rev-parse", "--verify", branch_name])
        return result.returncode == 0
    
    def _has_merge_commits(self, branch_name: str) -> bool:
        """Check if branch has merge commits."""
        result = self._run_git_command([
            "git", "log", "--merges", "--oneline", f"{self.main_branch}..{branch_name}"
        ])
        return result.returncode == 0 and bool(result.stdout.strip())
    
    def _get_current_branch(self) -> Optional[str]:
        """Get current branch name."""
        result = self._run_git_command(["git", "branch", "--show-current"])
        return result.stdout.strip() if result.returncode == 0 else None
    
    def _get_main_branch(self) -> str:
        """Determine the main branch name."""
        # Try to get default branch from remote
        result = self._run_git_command(["git", "symbolic-ref", "refs/remotes/origin/HEAD"])
        if result.returncode == 0:
            return result.stdout.strip().split('/')[-1]
        
        # Fallback to common names
        for branch in ["main", "master", "develop"]:
            if self._branch_exists(branch):
                return branch
        
        return "main"  # Default fallback
    
    def _get_protected_branches(self) -> Set[str]:
        """Get set of protected branch names."""
        # Default protected branches
        protected = {self.main_branch, "master", "develop", "staging", "production"}
        
        # TODO: Could be extended to read from GitHub API or config
        return protected
    
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
            cwd=self.workspace_path,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )


def main():
    """Command-line interface for branch manager testing."""
    import argparse
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    parser = argparse.ArgumentParser(description='Branch Manager for Claude Flow')
    parser.add_argument('command', choices=[
        'create', 'sync', 'info', 'list', 'delete', 'test'
    ], help='Command to run')
    parser.add_argument('--name', help='Branch name')
    parser.add_argument('--feature', help='Feature name for branch creation')
    parser.add_argument('--base', help='Base branch')
    parser.add_argument('--force', action='store_true', help='Force operation')
    parser.add_argument('--workspace', help='Workspace directory')
    
    args = parser.parse_args()
    
    try:
        workspace = Path(args.workspace) if args.workspace else Path.cwd()
        manager = BranchManager(workspace)
        
        if args.command == 'create':
            if not args.feature:
                print("--feature is required for create command")
                return
                
            result = manager.create_feature_branch(
                args.feature,
                base_branch=args.base,
                create_pr=True
            )
            
            if result.success:
                print(f"✅ {result.message}")
                if result.pr_url:
                    print(f"Draft PR: {result.pr_url}")
            else:
                print(f"❌ {result.error}")
        
        elif args.command == 'sync':
            result = manager.sync_branch(args.name, args.base)
            
            if result.success:
                print(f"✅ {result.message}")
            else:
                print(f"❌ {result.error}")
                if result.conflicts:
                    print(f"Conflicts: {result.conflicts.conflict_details}")
        
        elif args.command == 'info':
            branch_name = args.name or manager._get_current_branch()
            info = manager.get_branch_info(branch_name)
            
            if info:
                print(f"Branch: {info.name}")
                print(f"Status: {info.status.value}")
                print(f"Upstream: {info.upstream or 'None'}")
                print(f"Ahead: {info.ahead}, Behind: {info.behind}")
                print(f"Last Activity: {info.last_activity}")
                print(f"Protected: {info.protected}")
                if info.has_pr:
                    print(f"PR: #{info.pr_number} - {info.pr_url}")
            else:
                print("Branch not found")
        
        elif args.command == 'list':
            branches = manager.list_branches()
            
            print(f"Found {len(branches)} branches:")
            for branch in branches:
                status_icon = "🔒" if branch.protected else ("🔄" if branch.current else "📝")
                pr_info = f" (PR #{branch.pr_number})" if branch.has_pr else ""
                print(f"  {status_icon} {branch.name} [{branch.status.value}]{pr_info}")
        
        elif args.command == 'delete':
            if not args.name:
                print("--name is required for delete command")
                return
                
            result = manager.delete_branch(args.name, force=args.force)
            
            if result.success:
                print(f"✅ {result.message}")
            else:
                print(f"❌ {result.error}")
        
        elif args.command == 'test':
            print("Running branch manager tests...")
            
            # Test basic functionality
            current = manager._get_current_branch()
            print(f"✓ Current branch: {current}")
            
            main = manager.main_branch
            print(f"✓ Main branch: {main}")
            
            branches = manager.list_branches()
            print(f"✓ Found {len(branches)} branches")
            
            print("All tests completed!")
    
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()