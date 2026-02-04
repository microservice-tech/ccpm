#!/usr/bin/env python3
"""
Branch Cleanup Manager for Claude Flow GitHub Integration

Provides automated cleanup of merged, stale, and orphaned branches with
configurable policies, safety checks, and comprehensive reporting.
"""

import json
import logging
import os
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass, asdict, field
from enum import Enum

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli.wrapper import GitHubCLIWrapper, GitHubCLIError, PRInfo
from .manager import BranchManager, BranchInfo, BranchStatus


class CleanupReason(Enum):
    """Reasons for branch cleanup."""
    MERGED = "merged"
    STALE = "stale"
    ORPHANED = "orphaned"
    DUPLICATE = "duplicate"
    INVALID_NAME = "invalid_name"
    NO_COMMITS = "no_commits"
    PR_CLOSED = "pr_closed"
    MANUAL = "manual"


class CleanupAction(Enum):
    """Available cleanup actions."""
    DELETE_LOCAL = "delete_local"
    DELETE_REMOTE = "delete_remote"
    DELETE_BOTH = "delete_both"
    ARCHIVE = "archive"
    SKIP = "skip"


@dataclass
class CleanupPolicy:
    """Configuration for branch cleanup policies."""
    # Time-based cleanup
    stale_threshold_days: int = 30
    merged_retention_days: int = 7
    closed_pr_retention_days: int = 3
    
    # Automatic cleanup settings
    auto_delete_merged: bool = True
    auto_delete_stale: bool = False
    auto_delete_orphaned: bool = True
    delete_remote_branches: bool = True
    
    # Safety settings
    protected_branches: Set[str] = field(default_factory=lambda: {"main", "master", "develop", "staging", "production"})
    protected_patterns: List[str] = field(default_factory=lambda: ["release/*", "hotfix/*"])
    require_pr_for_deletion: bool = False
    dry_run_first: bool = True
    
    # Cleanup limits
    max_deletions_per_run: int = 20
    confirm_before_delete: bool = True
    
    # Exclusions
    exclude_branches: Set[str] = field(default_factory=set)
    exclude_patterns: List[str] = field(default_factory=list)


@dataclass
class CleanupCandidate:
    """Branch identified for potential cleanup."""
    branch_info: BranchInfo
    reason: CleanupReason
    recommended_action: CleanupAction
    safety_score: float  # 0.0 = unsafe, 1.0 = completely safe
    details: str
    can_auto_cleanup: bool = False
    pr_info: Optional[PRInfo] = None
    last_activity_days: Optional[int] = None


@dataclass
class CleanupResult:
    """Result of cleanup operation."""
    success: bool
    branch_name: str
    action: CleanupAction
    reason: CleanupReason
    message: Optional[str] = None
    error: Optional[str] = None
    deleted_local: bool = False
    deleted_remote: bool = False


@dataclass
class BranchCleanupStats:
    """Statistics from branch cleanup operation."""
    total_candidates: int
    auto_cleaned: int
    manual_cleaned: int
    skipped: int
    failed: int
    bytes_saved: Optional[int] = None
    cleanup_duration: Optional[float] = None
    
    # Breakdown by reason
    merged_cleaned: int = 0
    stale_cleaned: int = 0
    orphaned_cleaned: int = 0
    pr_closed_cleaned: int = 0
    
    # Breakdown by action
    local_deleted: int = 0
    remote_deleted: int = 0
    archived: int = 0


class BranchCleanupManager:
    """
    Comprehensive branch cleanup automation for Claude Flow.
    
    Provides intelligent cleanup of branches based on configurable policies,
    with safety checks, comprehensive reporting, and integration with GitHub PR data.
    """
    
    def __init__(self,
                 workspace_path: Optional[Path] = None,
                 github_wrapper: Optional[GitHubCLIWrapper] = None,
                 branch_manager: Optional[BranchManager] = None,
                 policy: Optional[CleanupPolicy] = None):
        """
        Initialize branch cleanup manager.
        
        Args:
            workspace_path: Working directory for git operations
            github_wrapper: GitHub CLI wrapper instance
            branch_manager: Branch manager instance
            policy: Cleanup policy configuration
        """
        self.logger = logging.getLogger(__name__)
        self.workspace_path = workspace_path or Path.cwd()
        self.github_wrapper = github_wrapper or GitHubCLIWrapper(self.workspace_path)
        self.branch_manager = branch_manager or BranchManager(self.workspace_path, self.github_wrapper)
        self.policy = policy or CleanupPolicy()
        
        self.logger.info("Branch cleanup manager initialized")
    
    def identify_cleanup_candidates(self,
                                  include_local: bool = True,
                                  include_remote: bool = True) -> List[CleanupCandidate]:
        """
        Identify branches that are candidates for cleanup.
        
        Args:
            include_local: Include local branches in analysis
            include_remote: Include remote branches in analysis
            
        Returns:
            List of CleanupCandidate objects
        """
        try:
            self.logger.info("Identifying cleanup candidates...")
            
            candidates = []
            
            # Get all branches with comprehensive information
            branches = self.branch_manager.list_branches(
                include_remote=include_remote,
                include_merged=True,
                include_stale=True
            )
            
            # Get PR information for analysis
            try:
                all_prs = self.github_wrapper.list_pull_requests(state="all", limit=200)
                pr_by_branch = {pr.head_ref: pr for pr in all_prs}
            except Exception as e:
                self.logger.warning(f"Could not fetch PR data: {e}")
                pr_by_branch = {}
            
            # Analyze each branch
            for branch in branches:
                candidate = self._analyze_branch_for_cleanup(branch, pr_by_branch.get(branch.name))
                if candidate:
                    candidates.append(candidate)
            
            self.logger.info(f"Found {len(candidates)} cleanup candidates")
            
            # Sort by safety score (highest first) and last activity
            candidates.sort(key=lambda c: (c.safety_score, c.last_activity_days or 0), reverse=True)
            
            return candidates
            
        except Exception as e:
            self.logger.error(f"Error identifying cleanup candidates: {e}")
            return []
    
    def cleanup_branches(self,
                        candidates: Optional[List[CleanupCandidate]] = None,
                        dry_run: bool = False,
                        interactive: bool = False) -> Tuple[List[CleanupResult], BranchCleanupStats]:
        """
        Execute branch cleanup based on candidates and policy.
        
        Args:
            candidates: Specific candidates to clean (auto-detect if None)
            dry_run: Show what would be deleted without actual deletion
            interactive: Prompt for confirmation before each deletion
            
        Returns:
            Tuple of (cleanup results, cleanup statistics)
        """
        start_time = datetime.now()
        
        try:
            self.logger.info(f"Starting branch cleanup (dry_run={dry_run}, interactive={interactive})")
            
            # Get candidates if not provided
            if candidates is None:
                candidates = self.identify_cleanup_candidates()
            
            results = []
            stats = BranchCleanupStats(
                total_candidates=len(candidates),
                auto_cleaned=0,
                manual_cleaned=0,
                skipped=0,
                failed=0
            )
            
            # Apply cleanup limits
            if len(candidates) > self.policy.max_deletions_per_run:
                self.logger.warning(f"Limiting cleanup to {self.policy.max_deletions_per_run} branches")
                candidates = candidates[:self.policy.max_deletions_per_run]
            
            # Process each candidate
            for candidate in candidates:
                if self._should_skip_branch(candidate):
                    result = CleanupResult(
                        success=True,
                        branch_name=candidate.branch_info.name,
                        action=CleanupAction.SKIP,
                        reason=candidate.reason,
                        message="Skipped due to policy"
                    )
                    results.append(result)
                    stats.skipped += 1
                    continue
                
                # Interactive confirmation
                if interactive and not dry_run:
                    if not self._confirm_cleanup(candidate):
                        result = CleanupResult(
                            success=True,
                            branch_name=candidate.branch_info.name,
                            action=CleanupAction.SKIP,
                            reason=candidate.reason,
                            message="Skipped by user"
                        )
                        results.append(result)
                        stats.skipped += 1
                        continue
                
                # Execute cleanup
                result = self._execute_cleanup(candidate, dry_run)
                results.append(result)
                
                # Update statistics
                if result.success:
                    if candidate.can_auto_cleanup:
                        stats.auto_cleaned += 1
                    else:
                        stats.manual_cleaned += 1
                    
                    # Update reason breakdown
                    if candidate.reason == CleanupReason.MERGED:
                        stats.merged_cleaned += 1
                    elif candidate.reason == CleanupReason.STALE:
                        stats.stale_cleaned += 1
                    elif candidate.reason == CleanupReason.ORPHANED:
                        stats.orphaned_cleaned += 1
                    elif candidate.reason == CleanupReason.PR_CLOSED:
                        stats.pr_closed_cleaned += 1
                    
                    # Update action breakdown
                    if result.deleted_local:
                        stats.local_deleted += 1
                    if result.deleted_remote:
                        stats.remote_deleted += 1
                    if result.action == CleanupAction.ARCHIVE:
                        stats.archived += 1
                else:
                    stats.failed += 1
            
            # Calculate final statistics
            stats.cleanup_duration = (datetime.now() - start_time).total_seconds()
            
            self.logger.info(f"Cleanup completed: {stats.auto_cleaned + stats.manual_cleaned} cleaned, "
                           f"{stats.skipped} skipped, {stats.failed} failed")
            
            return results, stats
            
        except Exception as e:
            self.logger.error(f"Error during branch cleanup: {e}")
            stats = BranchCleanupStats(
                total_candidates=len(candidates) if candidates else 0,
                auto_cleaned=0,
                manual_cleaned=0,
                skipped=0,
                failed=1
            )
            return [], stats
    
    def generate_cleanup_report(self,
                               results: List[CleanupResult],
                               stats: BranchCleanupStats,
                               include_details: bool = True) -> str:
        """
        Generate comprehensive cleanup report.
        
        Args:
            results: Cleanup results
            stats: Cleanup statistics
            include_details: Include detailed results per branch
            
        Returns:
            Formatted cleanup report
        """
        report_lines = [
            "# Branch Cleanup Report",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## Summary",
            f"- Total candidates: {stats.total_candidates}",
            f"- Auto cleaned: {stats.auto_cleaned}",
            f"- Manual cleaned: {stats.manual_cleaned}",
            f"- Skipped: {stats.skipped}",
            f"- Failed: {stats.failed}",
            ""
        ]
        
        if stats.cleanup_duration:
            report_lines.extend([
                f"- Duration: {stats.cleanup_duration:.2f} seconds",
                ""
            ])
        
        # Breakdown by reason
        if any([stats.merged_cleaned, stats.stale_cleaned, stats.orphaned_cleaned, stats.pr_closed_cleaned]):
            report_lines.extend([
                "## Cleanup by Reason",
                f"- Merged branches: {stats.merged_cleaned}",
                f"- Stale branches: {stats.stale_cleaned}",
                f"- Orphaned branches: {stats.orphaned_cleaned}",
                f"- Closed PR branches: {stats.pr_closed_cleaned}",
                ""
            ])
        
        # Breakdown by action
        if any([stats.local_deleted, stats.remote_deleted, stats.archived]):
            report_lines.extend([
                "## Actions Taken",
                f"- Local branches deleted: {stats.local_deleted}",
                f"- Remote branches deleted: {stats.remote_deleted}",
                f"- Branches archived: {stats.archived}",
                ""
            ])
        
        # Detailed results
        if include_details and results:
            report_lines.extend([
                "## Detailed Results",
                ""
            ])
            
            # Group by action
            by_action = {}
            for result in results:
                action = result.action.value
                if action not in by_action:
                    by_action[action] = []
                by_action[action].append(result)
            
            for action, action_results in by_action.items():
                report_lines.append(f"### {action.title().replace('_', ' ')}")
                for result in action_results:
                    status = "✅" if result.success else "❌"
                    reason = result.reason.value.replace('_', ' ').title()
                    message = f" - {result.message}" if result.message else ""
                    error = f" - Error: {result.error}" if result.error else ""
                    
                    report_lines.append(f"{status} `{result.branch_name}` ({reason}){message}{error}")
                
                report_lines.append("")
        
        return "\n".join(report_lines)
    
    def _analyze_branch_for_cleanup(self,
                                  branch_info: BranchInfo,
                                  pr_info: Optional[PRInfo]) -> Optional[CleanupCandidate]:
        """Analyze a branch to determine if it's a cleanup candidate."""
        try:
            # Skip protected branches
            if self._is_protected_branch(branch_info.name):
                return None
            
            # Skip current branch
            if branch_info.current:
                return None
            
            # Calculate days since last activity
            days_since_activity = None
            if branch_info.last_activity:
                days_since_activity = (datetime.now(timezone.utc) - branch_info.last_activity).days
            
            # Analyze based on branch status and other factors
            reason = None
            safety_score = 0.0
            can_auto_cleanup = False
            details = ""
            recommended_action = CleanupAction.SKIP
            
            # Check for merged branches
            if branch_info.status == BranchStatus.MERGED:
                reason = CleanupReason.MERGED
                safety_score = 0.9
                can_auto_cleanup = self.policy.auto_delete_merged
                recommended_action = CleanupAction.DELETE_BOTH if self.policy.delete_remote_branches else CleanupAction.DELETE_LOCAL
                
                retention_days = self.policy.merged_retention_days
                if days_since_activity and days_since_activity > retention_days:
                    details = f"Merged {days_since_activity} days ago (>{retention_days} day retention)"
                    safety_score = 0.95
                else:
                    details = f"Recently merged ({days_since_activity} days ago)"
                    safety_score = 0.7
                    can_auto_cleanup = False
            
            # Check for stale branches
            elif branch_info.status == BranchStatus.STALE or (
                days_since_activity and days_since_activity > self.policy.stale_threshold_days
            ):
                reason = CleanupReason.STALE
                safety_score = 0.6
                can_auto_cleanup = self.policy.auto_delete_stale
                recommended_action = CleanupAction.DELETE_BOTH if self.policy.delete_remote_branches else CleanupAction.DELETE_LOCAL
                details = f"No activity for {days_since_activity} days (>{self.policy.stale_threshold_days} day threshold)"
                
                # Lower safety score if there's no PR or unmerged changes
                if not pr_info or branch_info.ahead > 0:
                    safety_score = 0.4
                    can_auto_cleanup = False
            
            # Check for orphaned branches (no commits unique to branch)
            elif branch_info.ahead == 0 and branch_info.behind > 0:
                reason = CleanupReason.ORPHANED
                safety_score = 0.8
                can_auto_cleanup = self.policy.auto_delete_orphaned
                recommended_action = CleanupAction.DELETE_BOTH if self.policy.delete_remote_branches else CleanupAction.DELETE_LOCAL
                details = f"No unique commits (behind by {branch_info.behind})"
            
            # Check for branches with closed PRs
            elif pr_info and pr_info.state == "closed" and not pr_info.draft:
                reason = CleanupReason.PR_CLOSED
                safety_score = 0.7
                recommended_action = CleanupAction.DELETE_BOTH if self.policy.delete_remote_branches else CleanupAction.DELETE_LOCAL
                
                # Check retention period for closed PRs
                try:
                    pr_closed_date = datetime.fromisoformat(pr_info.updated_at.replace('Z', '+00:00'))
                    days_since_closed = (datetime.now(timezone.utc) - pr_closed_date).days
                    
                    if days_since_closed > self.policy.closed_pr_retention_days:
                        details = f"PR closed {days_since_closed} days ago (>{self.policy.closed_pr_retention_days} day retention)"
                        can_auto_cleanup = True
                        safety_score = 0.85
                    else:
                        details = f"PR recently closed ({days_since_closed} days ago)"
                        can_auto_cleanup = False
                        safety_score = 0.5
                except:
                    details = "PR is closed"
                    can_auto_cleanup = False
            
            # Skip if no cleanup reason found
            if not reason:
                return None
            
            # Adjust safety score based on additional factors
            if pr_info and pr_info.state == "open":
                safety_score *= 0.3  # Much less safe if PR is still open
                can_auto_cleanup = False
                details += " (has open PR)"
            
            if branch_info.ahead > 0:
                safety_score *= 0.7  # Less safe if has unmerged commits
                details += f" ({branch_info.ahead} unmerged commits)"
            
            return CleanupCandidate(
                branch_info=branch_info,
                reason=reason,
                recommended_action=recommended_action,
                safety_score=safety_score,
                details=details,
                can_auto_cleanup=can_auto_cleanup,
                pr_info=pr_info,
                last_activity_days=days_since_activity
            )
            
        except Exception as e:
            self.logger.error(f"Error analyzing branch {branch_info.name}: {e}")
            return None
    
    def _should_skip_branch(self, candidate: CleanupCandidate) -> bool:
        """Determine if branch should be skipped based on policy."""
        branch_name = candidate.branch_info.name
        
        # Check exclusions
        if branch_name in self.policy.exclude_branches:
            return True
        
        # Check exclusion patterns
        for pattern in self.policy.exclude_patterns:
            if self._match_pattern(branch_name, pattern):
                return True
        
        # Check if auto cleanup is disabled for this reason
        if candidate.reason == CleanupReason.MERGED and not self.policy.auto_delete_merged:
            return not candidate.can_auto_cleanup
        elif candidate.reason == CleanupReason.STALE and not self.policy.auto_delete_stale:
            return not candidate.can_auto_cleanup
        elif candidate.reason == CleanupReason.ORPHANED and not self.policy.auto_delete_orphaned:
            return not candidate.can_auto_cleanup
        
        return False
    
    def _confirm_cleanup(self, candidate: CleanupCandidate) -> bool:
        """Get user confirmation for cleanup."""
        branch_name = candidate.branch_info.name
        reason = candidate.reason.value.replace('_', ' ')
        action = candidate.recommended_action.value.replace('_', ' ')
        
        print(f"\nCleanup candidate: {branch_name}")
        print(f"Reason: {reason}")
        print(f"Details: {candidate.details}")
        print(f"Recommended action: {action}")
        print(f"Safety score: {candidate.safety_score:.2f}")
        
        if candidate.pr_info:
            print(f"PR: #{candidate.pr_info.number} ({candidate.pr_info.state})")
        
        response = input("Proceed with cleanup? [y/N]: ").lower().strip()
        return response in ['y', 'yes']
    
    def _execute_cleanup(self, candidate: CleanupCandidate, dry_run: bool) -> CleanupResult:
        """Execute cleanup for a specific candidate."""
        branch_name = candidate.branch_info.name
        action = candidate.recommended_action
        
        if dry_run:
            return CleanupResult(
                success=True,
                branch_name=branch_name,
                action=action,
                reason=candidate.reason,
                message=f"Would {action.value.replace('_', ' ')} (dry run)"
            )
        
        try:
            deleted_local = False
            deleted_remote = False
            message_parts = []
            
            if action in [CleanupAction.DELETE_LOCAL, CleanupAction.DELETE_BOTH]:
                # Delete local branch
                result = self.branch_manager.delete_branch(branch_name, force=True, delete_remote=False)
                if result.success:
                    deleted_local = True
                    message_parts.append("local deleted")
                else:
                    return CleanupResult(
                        success=False,
                        branch_name=branch_name,
                        action=action,
                        reason=candidate.reason,
                        error=f"Failed to delete local branch: {result.error}"
                    )
            
            if action in [CleanupAction.DELETE_REMOTE, CleanupAction.DELETE_BOTH]:
                # Delete remote branch
                try:
                    delete_result = self._run_git_command([
                        "git", "push", "origin", "--delete", branch_name
                    ])
                    if delete_result.returncode == 0:
                        deleted_remote = True
                        message_parts.append("remote deleted")
                    else:
                        # Don't fail the whole operation if remote delete fails
                        message_parts.append("remote delete failed")
                except Exception as e:
                    message_parts.append(f"remote delete error: {str(e)}")
            
            if action == CleanupAction.ARCHIVE:
                # Archive branch (create tag)
                tag_name = f"archive/{branch_name}"
                tag_result = self._run_git_command([
                    "git", "tag", tag_name, branch_name
                ])
                if tag_result.returncode == 0:
                    message_parts.append("archived as tag")
                    # Then delete the branch
                    result = self.branch_manager.delete_branch(branch_name, force=True)
                    if result.success:
                        deleted_local = True
                        message_parts.append("branch deleted")
                else:
                    return CleanupResult(
                        success=False,
                        branch_name=branch_name,
                        action=action,
                        reason=candidate.reason,
                        error=f"Failed to create archive tag: {tag_result.stderr}"
                    )
            
            message = ", ".join(message_parts)
            
            self.logger.info(f"Cleaned up branch {branch_name}: {message}")
            
            return CleanupResult(
                success=True,
                branch_name=branch_name,
                action=action,
                reason=candidate.reason,
                message=message,
                deleted_local=deleted_local,
                deleted_remote=deleted_remote
            )
            
        except Exception as e:
            self.logger.error(f"Error executing cleanup for {branch_name}: {e}")
            return CleanupResult(
                success=False,
                branch_name=branch_name,
                action=action,
                reason=candidate.reason,
                error=str(e)
            )
    
    def _is_protected_branch(self, branch_name: str) -> bool:
        """Check if branch is protected."""
        if branch_name in self.policy.protected_branches:
            return True
        
        for pattern in self.policy.protected_patterns:
            if self._match_pattern(branch_name, pattern):
                return True
        
        return False
    
    def _match_pattern(self, branch_name: str, pattern: str) -> bool:
        """Check if branch name matches a pattern."""
        if '*' in pattern:
            # Simple glob pattern matching
            import fnmatch
            return fnmatch.fnmatch(branch_name, pattern)
        else:
            return branch_name == pattern
    
    def _run_git_command(self, command: List[str]) -> subprocess.CompletedProcess:
        """Run a Git command in the workspace directory."""
        return subprocess.run(
            command,
            cwd=self.workspace_path,
            capture_output=True,
            text=True,
            timeout=60
        )


def main():
    """Command-line interface for branch cleanup manager."""
    import argparse
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    parser = argparse.ArgumentParser(description='Branch Cleanup Manager')
    parser.add_argument('command', choices=['identify', 'cleanup', 'report'], 
                       help='Command to run')
    parser.add_argument('--workspace', help='Workspace directory')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')
    parser.add_argument('--interactive', action='store_true', help='Prompt for confirmation')
    parser.add_argument('--stale-days', type=int, default=30, help='Days threshold for stale branches')
    parser.add_argument('--no-remote', action='store_true', help='Skip remote branch operations')
    parser.add_argument('--output', help='Output file for report')
    
    args = parser.parse_args()
    
    try:
        workspace = Path(args.workspace) if args.workspace else Path.cwd()
        
        # Create policy
        policy = CleanupPolicy(
            stale_threshold_days=args.stale_days,
            delete_remote_branches=not args.no_remote
        )
        
        cleanup_manager = BranchCleanupManager(workspace, policy=policy)
        
        if args.command == 'identify':
            candidates = cleanup_manager.identify_cleanup_candidates()
            
            print(f"Found {len(candidates)} cleanup candidates:\n")
            
            for candidate in candidates:
                safety_icon = "🔒" if candidate.safety_score > 0.8 else ("⚠️" if candidate.safety_score > 0.5 else "🚨")
                auto_icon = "🤖" if candidate.can_auto_cleanup else "👤"
                
                print(f"{safety_icon} {auto_icon} {candidate.branch_info.name}")
                print(f"   Reason: {candidate.reason.value}")
                print(f"   Details: {candidate.details}")
                print(f"   Safety: {candidate.safety_score:.2f}")
                print(f"   Action: {candidate.recommended_action.value}")
                print()
        
        elif args.command == 'cleanup':
            candidates = cleanup_manager.identify_cleanup_candidates()
            results, stats = cleanup_manager.cleanup_branches(
                candidates,
                dry_run=args.dry_run,
                interactive=args.interactive
            )
            
            print(f"Cleanup completed!")
            print(f"- Processed: {stats.total_candidates}")
            print(f"- Cleaned: {stats.auto_cleaned + stats.manual_cleaned}")
            print(f"- Skipped: {stats.skipped}")
            print(f"- Failed: {stats.failed}")
            
            if args.dry_run:
                print("\nThis was a dry run - no actual changes were made.")
        
        elif args.command == 'report':
            candidates = cleanup_manager.identify_cleanup_candidates()
            results, stats = cleanup_manager.cleanup_branches(
                candidates,
                dry_run=True
            )
            
            report = cleanup_manager.generate_cleanup_report(results, stats)
            
            if args.output:
                Path(args.output).write_text(report)
                print(f"Report saved to: {args.output}")
            else:
                print(report)
    
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()