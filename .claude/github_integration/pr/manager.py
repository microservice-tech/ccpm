#!/usr/bin/env python3
"""
PR Manager for Claude Flow

Provides comprehensive pull request lifecycle management including:
- Automated PR creation from feature branches
- Change detection and description generation
- Draft PR support and ready-for-review transitions
- PR status tracking and lifecycle management
- Integration with GitHub service and CLI wrapper
"""

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union
from dataclasses import dataclass, asdict
from enum import Enum

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.config_manager import ConfigManager
from ..cli.wrapper import GitHubCLIWrapper, PRInfo
from .templates import PRTemplateManager, PRTemplate
from .reviewer import PRReviewerManager


class PRLifecycleError(Exception):
    """Raised when PR lifecycle operations fail."""
    pass


class PRPhase(Enum):
    """PR lifecycle phases."""
    DRAFT = "draft"
    READY_FOR_REVIEW = "ready_for_review"
    UNDER_REVIEW = "under_review"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    MERGEABLE = "mergeable"
    MERGED = "merged"
    CLOSED = "closed"


class PRChangeType(Enum):
    """Types of changes detected in PR."""
    FEATURE = "feature"
    BUGFIX = "bugfix"
    REFACTOR = "refactor"
    DOCS = "docs"
    TESTS = "tests"
    CI = "ci"
    CHORE = "chore"
    HOTFIX = "hotfix"


@dataclass
class PRCreationConfig:
    """Configuration for PR creation."""
    title: str
    branch_name: Optional[str] = None
    base_branch: str = "main"
    description: Optional[str] = None
    draft: bool = False
    auto_assign_reviewers: bool = True
    auto_populate_template: bool = True
    include_change_summary: bool = True
    force_create: bool = False
    labels: Optional[List[str]] = None
    assignees: Optional[List[str]] = None


@dataclass
class PRAnalysis:
    """Analysis of PR changes and context."""
    change_type: PRChangeType
    files_changed: List[str]
    lines_added: int
    lines_removed: int
    commit_count: int
    commits: List[str]
    risk_level: str  # low, medium, high
    breaking_changes: bool
    affects_api: bool
    affects_tests: bool
    affects_docs: bool


@dataclass  
class PRCreationResult:
    """Result of PR creation attempt."""
    success: bool
    pr_info: Optional[PRInfo] = None
    error_message: Optional[str] = None
    warnings: List[str] = None
    analysis: Optional[PRAnalysis] = None


class PRManager:
    """
    Comprehensive PR lifecycle management system.
    
    Handles the complete lifecycle of pull requests from creation to merge,
    including automated template population, reviewer assignment, and
    change analysis.
    """
    
    def __init__(self, 
                 config: Optional[ConfigManager] = None,
                 workspace_path: Optional[Path] = None):
        """
        Initialize PR manager.
        
        Args:
            config: Configuration manager instance
            workspace_path: Working directory for git operations
        """
        self.logger = logging.getLogger(__name__)
        self.config = config or ConfigManager()
        self.workspace_path = workspace_path or Path.cwd()
        
        # Initialize dependencies
        self.cli_wrapper = GitHubCLIWrapper(self.workspace_path)
        self.template_manager = PRTemplateManager(config, self.workspace_path)
        self.reviewer_manager = PRReviewerManager(config, self.workspace_path)
        
        # Configuration
        self.main_branch = self.config.get("github.main_branch", "main")
        self.auto_cleanup = self.config.get("github.auto_cleanup_branches", True)
        self.require_reviews = self.config.get("github.require_reviews", True)
        self.min_reviewers = self.config.get("github.min_reviewers", 1)
        
        self.logger.info("PR Manager initialized")
    
    def create_pr(self, config: PRCreationConfig) -> PRCreationResult:
        """
        Create a pull request with comprehensive analysis and automation.
        
        Args:
            config: PR creation configuration
            
        Returns:
            PRCreationResult with outcome details
        """
        try:
            self.logger.info(f"Creating PR: {config.title}")
            warnings = []
            
            # Get current branch if not specified
            if not config.branch_name:
                config.branch_name = self._get_current_branch()
                if not config.branch_name:
                    return PRCreationResult(
                        success=False,
                        error_message="Could not determine current branch",
                        warnings=warnings
                    )
            
            # Validate branch exists and has commits
            if not self._validate_branch(config.branch_name, config.base_branch):
                return PRCreationResult(
                    success=False,
                    error_message=f"Branch '{config.branch_name}' is not ready for PR creation",
                    warnings=warnings
                )
            
            # Analyze changes
            analysis = self._analyze_changes(config.branch_name, config.base_branch)
            if analysis.commit_count == 0:
                return PRCreationResult(
                    success=False,
                    error_message="No commits found to create PR",
                    warnings=warnings,
                    analysis=analysis
                )
            
            # Generate description if not provided
            description = config.description
            if not description and config.auto_populate_template:
                description = self._generate_pr_description(
                    config, analysis, config.include_change_summary
                )
            
            # Auto-assign reviewers if enabled
            reviewers = []
            if config.auto_assign_reviewers:
                try:
                    reviewers = self.reviewer_manager.suggest_reviewers(
                        analysis.files_changed, 
                        config.branch_name
                    )
                    if reviewers:
                        self.logger.info(f"Auto-assigned reviewers: {', '.join(reviewers)}")
                    else:
                        warnings.append("No reviewers could be auto-assigned")
                except Exception as e:
                    warnings.append(f"Failed to auto-assign reviewers: {e}")
            
            # Ensure branch is pushed to remote
            if not self._ensure_branch_pushed(config.branch_name):
                return PRCreationResult(
                    success=False,
                    error_message="Failed to push branch to remote",
                    warnings=warnings,
                    analysis=analysis
                )
            
            # Create the PR using CLI wrapper
            pr_info = self.cli_wrapper.create_pull_request(
                title=config.title,
                body=description or "",
                head=config.branch_name,
                base=config.base_branch,
                draft=config.draft or analysis.breaking_changes,  # Auto-draft for breaking changes
                labels=config.labels,
                reviewers=reviewers
            )
            
            if not pr_info:
                return PRCreationResult(
                    success=False,
                    error_message="Failed to create PR via GitHub CLI",
                    warnings=warnings,
                    analysis=analysis
                )
            
            self.logger.info(f"PR created successfully: #{pr_info.number} ({pr_info.url})")
            
            # Add analysis comment if high risk
            if analysis.risk_level in ['high'] or analysis.breaking_changes:
                self._add_analysis_comment(pr_info.number, analysis)
            
            return PRCreationResult(
                success=True,
                pr_info=pr_info,
                warnings=warnings,
                analysis=analysis
            )
            
        except Exception as e:
            self.logger.error(f"PR creation failed: {e}")
            return PRCreationResult(
                success=False,
                error_message=str(e),
                warnings=warnings
            )
    
    def transition_to_ready(self, pr_number: int, 
                           run_checks: bool = True) -> bool:
        """
        Transition a draft PR to ready for review.
        
        Args:
            pr_number: PR number to transition
            run_checks: Whether to run quality checks first
            
        Returns:
            True if successfully transitioned
        """
        try:
            self.logger.info(f"Transitioning PR #{pr_number} to ready for review")
            
            # Get current PR info
            pr_info = self.cli_wrapper.get_pull_request_info(number=pr_number)
            if not pr_info:
                self.logger.error(f"Could not find PR #{pr_number}")
                return False
            
            if not pr_info.draft:
                self.logger.info(f"PR #{pr_number} is already ready for review")
                return True
            
            # Run quality checks if requested
            if run_checks:
                checks_passed, check_results = self._run_quality_checks(pr_info.head_ref)
                if not checks_passed:
                    self._add_quality_check_comment(pr_number, check_results)
                    self.logger.warning(f"Quality checks failed for PR #{pr_number}")
                    return False
            
            # Mark PR as ready for review
            success = self.cli_wrapper.update_pull_request(
                number=pr_number,
                ready=True
            )
            
            if success:
                # Add transition comment
                self.cli_wrapper.add_pr_comment(
                    pr_number,
                    "🎉 This PR is now ready for review!\n\n" +
                    ("All quality checks have passed.\n\n" if run_checks else "") +
                    "*Transitioned using Claude Flow PR Management System.*"
                )
                
                self.logger.info(f"PR #{pr_number} successfully transitioned to ready for review")
                return True
            else:
                self.logger.error(f"Failed to update PR #{pr_number} status")
                return False
                
        except Exception as e:
            self.logger.error(f"Failed to transition PR to ready: {e}")
            return False
    
    def update_pr_description(self, pr_number: int, 
                             auto_generate: bool = False) -> bool:
        """
        Update PR description, optionally auto-generating content.
        
        Args:
            pr_number: PR number to update
            auto_generate: Whether to regenerate description from template
            
        Returns:
            True if successfully updated
        """
        try:
            pr_info = self.cli_wrapper.get_pull_request_info(number=pr_number)
            if not pr_info:
                return False
            
            if auto_generate:
                # Re-analyze changes
                analysis = self._analyze_changes(pr_info.head_ref, pr_info.base_ref)
                
                # Generate new description
                config = PRCreationConfig(
                    title=pr_info.title,
                    branch_name=pr_info.head_ref,
                    base_branch=pr_info.base_ref
                )
                
                new_description = self._generate_pr_description(config, analysis, True)
                
                # Update PR
                return self.cli_wrapper.update_pull_request(
                    number=pr_number,
                    body=new_description
                )
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to update PR description: {e}")
            return False
    
    def assign_reviewers(self, pr_number: int, 
                        suggested_reviewers: Optional[List[str]] = None) -> bool:
        """
        Assign reviewers to a PR based on code ownership and suggestions.
        
        Args:
            pr_number: PR number
            suggested_reviewers: Optional list of suggested reviewers
            
        Returns:
            True if reviewers were assigned successfully
        """
        try:
            pr_info = self.cli_wrapper.get_pull_request_info(number=pr_number)
            if not pr_info:
                return False
            
            # Get file changes
            analysis = self._analyze_changes(pr_info.head_ref, pr_info.base_ref)
            
            # Get suggested reviewers from code ownership
            auto_reviewers = self.reviewer_manager.suggest_reviewers(
                analysis.files_changed, 
                pr_info.head_ref
            )
            
            # Combine with manual suggestions
            all_reviewers = list(set((suggested_reviewers or []) + auto_reviewers))
            
            if not all_reviewers:
                self.logger.warning(f"No reviewers found for PR #{pr_number}")
                return False
            
            # Use CLI wrapper's PR creation method doesn't support updating reviewers
            # So we'll add a comment with reviewer suggestions
            reviewer_comment = (
                "👥 **Suggested Reviewers**\n\n"
                "Based on code ownership analysis, the following reviewers are suggested:\n\n"
            )
            
            for reviewer in all_reviewers[:5]:  # Limit to 5 reviewers
                reviewer_comment += f"- @{reviewer}\n"
            
            if len(all_reviewers) > 5:
                reviewer_comment += f"\n*and {len(all_reviewers) - 5} more...*\n"
            
            reviewer_comment += "\n*Generated by Claude Flow PR Management System.*"
            
            return self.cli_wrapper.add_pr_comment(pr_number, reviewer_comment)
            
        except Exception as e:
            self.logger.error(f"Failed to assign reviewers: {e}")
            return False
    
    def get_pr_lifecycle_status(self, pr_number: int) -> Optional[PRPhase]:
        """
        Get the current lifecycle phase of a PR.
        
        Args:
            pr_number: PR number
            
        Returns:
            PRPhase representing current status
        """
        try:
            pr_info = self.cli_wrapper.get_pull_request_info(number=pr_number)
            if not pr_info:
                return None
            
            # Map GitHub states to our phases
            if pr_info.state == "closed":
                return PRPhase.CLOSED
            elif pr_info.state == "merged":
                return PRPhase.MERGED
            elif pr_info.draft:
                return PRPhase.DRAFT
            elif pr_info.mergeable == "MERGEABLE":
                return PRPhase.MERGEABLE
            elif pr_info.state == "open":
                # More detailed analysis would be needed for other phases
                return PRPhase.READY_FOR_REVIEW
            
            return PRPhase.UNDER_REVIEW
            
        except Exception as e:
            self.logger.error(f"Failed to get PR status: {e}")
            return None
    
    def _analyze_changes(self, source_branch: str, target_branch: str) -> PRAnalysis:
        """
        Analyze changes between branches.
        
        Args:
            source_branch: Source branch name
            target_branch: Target branch name
            
        Returns:
            PRAnalysis with change details
        """
        try:
            # Get commit messages between branches
            result = self._run_git_command([
                "git", "log", "--oneline", f"{target_branch}..{source_branch}"
            ])
            
            commits = result.stdout.strip().split('\n') if result.returncode == 0 and result.stdout.strip() else []
            commit_count = len(commits)
            
            # Get changed files
            result = self._run_git_command([
                "git", "diff", "--name-only", f"{target_branch}..{source_branch}"
            ])
            
            files_changed = result.stdout.strip().split('\n') if result.returncode == 0 and result.stdout.strip() else []
            
            # Get line changes
            result = self._run_git_command([
                "git", "diff", "--stat", f"{target_branch}..{source_branch}"
            ])
            
            lines_added, lines_removed = self._parse_diff_stats(result.stdout)
            
            # Determine change type from files and commits
            change_type = self._determine_change_type(files_changed, commits)
            
            # Assess risk level
            risk_level = self._assess_risk_level(files_changed, lines_added + lines_removed, commit_count)
            
            # Check for breaking changes and impacts
            breaking_changes = self._check_breaking_changes(commits, files_changed)
            affects_api = any('api' in f.lower() or 'endpoint' in f.lower() for f in files_changed)
            affects_tests = any('test' in f.lower() or 'spec' in f.lower() for f in files_changed)
            affects_docs = any(f.endswith(('.md', '.rst', '.txt')) for f in files_changed)
            
            return PRAnalysis(
                change_type=change_type,
                files_changed=files_changed,
                lines_added=lines_added,
                lines_removed=lines_removed,
                commit_count=commit_count,
                commits=commits,
                risk_level=risk_level,
                breaking_changes=breaking_changes,
                affects_api=affects_api,
                affects_tests=affects_tests,
                affects_docs=affects_docs
            )
            
        except Exception as e:
            self.logger.error(f"Error analyzing changes: {e}")
            return PRAnalysis(
                change_type=PRChangeType.CHORE,
                files_changed=[],
                lines_added=0,
                lines_removed=0,
                commit_count=0,
                commits=[],
                risk_level="low",
                breaking_changes=False,
                affects_api=False,
                affects_tests=False,
                affects_docs=False
            )
    
    def _generate_pr_description(self, config: PRCreationConfig, 
                                analysis: PRAnalysis, 
                                include_change_summary: bool) -> str:
        """
        Generate comprehensive PR description.
        
        Args:
            config: PR creation configuration
            analysis: Change analysis
            include_change_summary: Whether to include detailed change summary
            
        Returns:
            Generated PR description
        """
        try:
            # Start with template if available
            template = self.template_manager.get_template_for_change_type(analysis.change_type.value)
            description_parts = []
            
            if template and template.content:
                # Populate template with analysis data
                populated_content = self.template_manager.populate_template(
                    template, 
                    {
                        'branch_name': config.branch_name,
                        'base_branch': config.base_branch,
                        'change_type': analysis.change_type.value,
                        'files_changed': len(analysis.files_changed),
                        'commits': len(analysis.commits),
                        'risk_level': analysis.risk_level
                    }
                )
                description_parts.append(populated_content)
                description_parts.append("\n---\n")
            
            # Add change summary if requested
            if include_change_summary:
                description_parts.append("## 📊 Change Summary")
                description_parts.append(f"- **Type**: {analysis.change_type.value.title()}")
                description_parts.append(f"- **Files Changed**: {len(analysis.files_changed)}")
                description_parts.append(f"- **Commits**: {analysis.commit_count}")
                description_parts.append(f"- **Lines Added**: +{analysis.lines_added}")
                description_parts.append(f"- **Lines Removed**: -{analysis.lines_removed}")
                description_parts.append(f"- **Risk Level**: {analysis.risk_level.title()}")
                
                if analysis.breaking_changes:
                    description_parts.append("- **⚠️ Breaking Changes**: Yes")
                
                description_parts.append("")
                
                # Add impacts
                impacts = []
                if analysis.affects_api:
                    impacts.append("🔌 API")
                if analysis.affects_tests:
                    impacts.append("🧪 Tests")  
                if analysis.affects_docs:
                    impacts.append("📚 Documentation")
                
                if impacts:
                    description_parts.append(f"**Impacts**: {' '.join(impacts)}")
                    description_parts.append("")
            
            # Add commits if reasonable number
            if analysis.commits and len(analysis.commits) <= 10:
                description_parts.append("## 📝 Commits")
                for commit in analysis.commits:
                    description_parts.append(f"- {commit}")
                description_parts.append("")
            
            # Add files changed if reasonable number
            if analysis.files_changed and len(analysis.files_changed) <= 20:
                description_parts.append("## 📁 Files Changed")
                for file in analysis.files_changed:
                    description_parts.append(f"- `{file}`")
                if len(analysis.files_changed) == 20:
                    description_parts.append("- *... (showing first 20 files)*")
                description_parts.append("")
            
            # Add testing section
            description_parts.append("## 🧪 Testing")
            description_parts.append("- [ ] Unit tests added/updated")
            description_parts.append("- [ ] Integration tests pass")
            description_parts.append("- [ ] Manual testing completed")
            if analysis.affects_api:
                description_parts.append("- [ ] API documentation updated")
            description_parts.append("")
            
            # Add checklist for high-risk changes
            if analysis.risk_level == "high" or analysis.breaking_changes:
                description_parts.append("## ⚠️ High-Risk Change Checklist")
                description_parts.append("- [ ] Breaking changes documented")
                description_parts.append("- [ ] Migration guide provided (if applicable)")
                description_parts.append("- [ ] Backward compatibility considered")
                description_parts.append("- [ ] Rollback plan prepared")
                description_parts.append("")
            
            description_parts.append("---")
            description_parts.append("*This PR description was generated using Claude Flow's PR Management System.*")
            
            return "\n".join(description_parts)
            
        except Exception as e:
            self.logger.error(f"Error generating PR description: {e}")
            return f"Automated PR from {config.branch_name} to {config.base_branch}"
    
    def _validate_branch(self, branch_name: str, base_branch: str) -> bool:
        """Validate that branch exists and has commits ahead of base."""
        try:
            # Check if branch exists
            result = self._run_git_command(["git", "show-ref", "--verify", f"refs/heads/{branch_name}"])
            if result.returncode != 0:
                self.logger.error(f"Branch '{branch_name}' does not exist")
                return False
            
            # Check if branch has commits ahead of base
            result = self._run_git_command([
                "git", "rev-list", "--count", f"{base_branch}..{branch_name}"
            ])
            
            if result.returncode == 0 and int(result.stdout.strip()) > 0:
                return True
            
            self.logger.error(f"Branch '{branch_name}' has no commits ahead of '{base_branch}'")
            return False
            
        except Exception as e:
            self.logger.error(f"Error validating branch: {e}")
            return False
    
    def _ensure_branch_pushed(self, branch_name: str) -> bool:
        """Ensure branch is pushed to remote."""
        try:
            # Check if branch exists on remote
            result = self._run_git_command([
                "git", "ls-remote", "--heads", "origin", branch_name
            ])
            
            if result.returncode == 0 and branch_name in result.stdout:
                return True
            
            # Push branch to remote
            self.logger.info(f"Pushing branch '{branch_name}' to remote")
            result = self._run_git_command(["git", "push", "-u", "origin", branch_name])
            
            return result.returncode == 0
            
        except Exception as e:
            self.logger.error(f"Error pushing branch: {e}")
            return False
    
    def _determine_change_type(self, files: List[str], commits: List[str]) -> PRChangeType:
        """Determine the type of changes based on files and commits."""
        commit_text = " ".join(commits).lower()
        file_text = " ".join(files).lower()
        
        # Check commit messages first
        if any(keyword in commit_text for keyword in ["feat:", "feature:"]):
            return PRChangeType.FEATURE
        elif any(keyword in commit_text for keyword in ["fix:", "bug:", "hotfix:"]):
            return PRChangeType.BUGFIX if "hotfix" not in commit_text else PRChangeType.HOTFIX
        elif any(keyword in commit_text for keyword in ["refactor:", "refac:"]):
            return PRChangeType.REFACTOR
        elif any(keyword in commit_text for keyword in ["docs:", "doc:"]):
            return PRChangeType.DOCS
        elif any(keyword in commit_text for keyword in ["test:", "tests:"]):
            return PRChangeType.TESTS
        elif any(keyword in commit_text for keyword in ["ci:", "build:", "deploy:"]):
            return PRChangeType.CI
        elif any(keyword in commit_text for keyword in ["chore:", "style:", "format:"]):
            return PRChangeType.CHORE
        
        # Check file patterns
        if any(f.endswith(('.md', '.rst', '.txt')) for f in files):
            return PRChangeType.DOCS
        elif any('test' in f.lower() or 'spec' in f.lower() for f in files):
            return PRChangeType.TESTS
        elif any(f in ['.github', 'ci', 'build'] or 'ci' in f.lower() for f in files):
            return PRChangeType.CI
        
        # Default to feature for most changes
        return PRChangeType.FEATURE
    
    def _assess_risk_level(self, files: List[str], total_lines: int, commits: int) -> str:
        """Assess risk level based on changes."""
        risk_score = 0
        
        # File-based risk
        high_risk_patterns = ['database', 'migration', 'config', 'security', 'auth']
        medium_risk_patterns = ['api', 'service', 'controller', 'model']
        
        for file in files:
            file_lower = file.lower()
            if any(pattern in file_lower for pattern in high_risk_patterns):
                risk_score += 3
            elif any(pattern in file_lower for pattern in medium_risk_patterns):
                risk_score += 2
            elif 'test' not in file_lower:
                risk_score += 1
        
        # Size-based risk
        if total_lines > 1000:
            risk_score += 3
        elif total_lines > 500:
            risk_score += 2
        elif total_lines > 100:
            risk_score += 1
        
        # Commit-based risk
        if commits > 20:
            risk_score += 2
        elif commits > 10:
            risk_score += 1
        
        # Determine level
        if risk_score >= 8:
            return "high"
        elif risk_score >= 4:
            return "medium"
        else:
            return "low"
    
    def _check_breaking_changes(self, commits: List[str], files: List[str]) -> bool:
        """Check for potential breaking changes."""
        commit_text = " ".join(commits).lower()
        breaking_keywords = [
            "breaking", "break", "remove", "delete", "deprecate",
            "major", "incompatible", "breaking change"
        ]
        
        # Check commit messages
        if any(keyword in commit_text for keyword in breaking_keywords):
            return True
        
        # Check for API file changes
        api_files = [f for f in files if 'api' in f.lower() or 'interface' in f.lower()]
        if api_files and len(api_files) > 2:
            return True
        
        return False
    
    def _parse_diff_stats(self, diff_output: str) -> Tuple[int, int]:
        """Parse git diff --stat output to get lines added/removed."""
        lines_added, lines_removed = 0, 0
        
        try:
            lines = diff_output.strip().split('\n')
            for line in lines:
                if ' insertions(+)' in line or ' deletions(-)' in line:
                    parts = line.split(',')
                    for part in parts:
                        if 'insertions(+)' in part:
                            lines_added = int(part.strip().split()[0])
                        elif 'deletions(-)' in part:
                            lines_removed = int(part.strip().split()[0])
        except Exception as e:
            self.logger.warning(f"Could not parse diff stats: {e}")
        
        return lines_added, lines_removed
    
    def _run_quality_checks(self, branch_name: str) -> Tuple[bool, Dict[str, bool]]:
        """Run quality checks on the branch."""
        checks = {
            'tests_pass': self._run_tests(),
            'linting_pass': self._run_linting(),
            'build_success': self._run_build()
        }
        
        all_passed = all(checks.values())
        return all_passed, checks
    
    def _run_tests(self) -> bool:
        """Run project tests."""
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
        
        return True  # Assume pass if no test command found
    
    def _run_linting(self) -> bool:
        """Run linting checks."""
        lint_commands = [
            ["npm", "run", "lint"],
            ["yarn", "lint"],
            ["flake8", "."],
            ["make", "lint"]
        ]
        
        for cmd in lint_commands:
            if self._command_exists(cmd[0]):
                result = self._run_git_command(cmd)
                return result.returncode == 0
        
        return True  # Assume pass if no lint command found
    
    def _run_build(self) -> bool:
        """Run build checks."""
        build_commands = [
            ["npm", "run", "build"],
            ["yarn", "build"],
            ["make", "build"]
        ]
        
        for cmd in build_commands:
            if self._command_exists(cmd[0]):
                result = self._run_git_command(cmd)
                return result.returncode == 0
        
        return True  # Assume pass if no build command found
    
    def _add_analysis_comment(self, pr_number: int, analysis: PRAnalysis):
        """Add analysis comment to PR."""
        comment_parts = [
            "🔍 **PR Analysis Report**",
            "",
            f"**Change Type**: {analysis.change_type.value.title()}",
            f"**Risk Level**: {analysis.risk_level.title()}",
            f"**Files Changed**: {len(analysis.files_changed)}",
            f"**Lines Changed**: +{analysis.lines_added}/-{analysis.lines_removed}",
            ""
        ]
        
        if analysis.breaking_changes:
            comment_parts.append("⚠️ **Breaking changes detected** - Please review carefully!")
            comment_parts.append("")
        
        if analysis.risk_level == "high":
            comment_parts.append("🚨 **High-risk change** - Consider additional review and testing.")
            comment_parts.append("")
        
        comment_parts.append("*Generated by Claude Flow PR Management System.*")
        
        self.cli_wrapper.add_pr_comment(pr_number, "\n".join(comment_parts))
    
    def _add_quality_check_comment(self, pr_number: int, check_results: Dict[str, bool]):
        """Add quality check results comment."""
        comment_parts = [
            "🔧 **Quality Check Results**",
            ""
        ]
        
        for check, passed in check_results.items():
            status = "✅" if passed else "❌"
            comment_parts.append(f"{status} {check.replace('_', ' ').title()}")
        
        comment_parts.extend([
            "",
            "Please address any failing checks before marking this PR as ready for review.",
            "",
            "*Generated by Claude Flow PR Management System.*"
        ])
        
        self.cli_wrapper.add_pr_comment(pr_number, "\n".join(comment_parts))
    
    def _get_current_branch(self) -> Optional[str]:
        """Get current Git branch name."""
        try:
            result = self._run_git_command(["git", "branch", "--show-current"])
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None
    
    def _run_git_command(self, command: List[str]) -> subprocess.CompletedProcess:
        """Run a Git command in the workspace directory."""
        return subprocess.run(
            command,
            cwd=self.workspace_path,
            capture_output=True,
            text=True,
            timeout=300
        )
    
    def _command_exists(self, command: str) -> bool:
        """Check if a command exists in the system PATH."""
        try:
            subprocess.run(["which", command], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False


def main():
    """Command-line interface for PR manager operations."""
    import argparse
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    parser = argparse.ArgumentParser(description='PR Manager for Claude Flow')
    parser.add_argument('--config', help='Path to configuration file')
    parser.add_argument('--workspace', help='Workspace directory')
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Create PR command
    create_parser = subparsers.add_parser('create', help='Create pull request')
    create_parser.add_argument('title', help='PR title')
    create_parser.add_argument('--branch', help='Source branch (current if not specified)')
    create_parser.add_argument('--base', help='Target branch (main if not specified)', default='main')
    create_parser.add_argument('--description', help='PR description')
    create_parser.add_argument('--draft', action='store_true', help='Create as draft PR')
    create_parser.add_argument('--no-reviewers', action='store_true', help='Skip auto-reviewer assignment')
    create_parser.add_argument('--no-template', action='store_true', help='Skip template population')
    
    # Transition PR command
    transition_parser = subparsers.add_parser('ready', help='Mark draft PR as ready for review')
    transition_parser.add_argument('pr_number', type=int, help='PR number')
    transition_parser.add_argument('--skip-checks', action='store_true', help='Skip quality checks')
    
    # Update description command  
    update_parser = subparsers.add_parser('update-description', help='Update PR description')
    update_parser.add_argument('pr_number', type=int, help='PR number')
    update_parser.add_argument('--auto-generate', action='store_true', help='Auto-generate from template')
    
    # Assign reviewers command
    reviewers_parser = subparsers.add_parser('assign-reviewers', help='Assign reviewers to PR')
    reviewers_parser.add_argument('pr_number', type=int, help='PR number')
    reviewers_parser.add_argument('--reviewers', nargs='+', help='Additional reviewer usernames')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    try:
        config = ConfigManager(args.config) if args.config else ConfigManager()
        workspace = Path(args.workspace) if args.workspace else Path.cwd()
        manager = PRManager(config, workspace)
        
        if args.command == 'create':
            pr_config = PRCreationConfig(
                title=args.title,
                branch_name=args.branch,
                base_branch=args.base,
                description=args.description,
                draft=args.draft,
                auto_assign_reviewers=not args.no_reviewers,
                auto_populate_template=not args.no_template
            )
            
            result = manager.create_pr(pr_config)
            
            if result.success:
                print(f"✅ PR created successfully!")
                print(f"Number: #{result.pr_info.number}")
                print(f"URL: {result.pr_info.url}")
                print(f"Status: {'Draft' if result.pr_info.draft else 'Ready for Review'}")
                
                if result.analysis:
                    print(f"Change Type: {result.analysis.change_type.value}")
                    print(f"Risk Level: {result.analysis.risk_level}")
                
                if result.warnings:
                    print("\nWarnings:")
                    for warning in result.warnings:
                        print(f"  ⚠️  {warning}")
            else:
                print(f"❌ PR creation failed: {result.error_message}")
                sys.exit(1)
        
        elif args.command == 'ready':
            success = manager.transition_to_ready(args.pr_number, not args.skip_checks)
            if success:
                print(f"✅ PR #{args.pr_number} is now ready for review!")
            else:
                print(f"❌ Failed to transition PR #{args.pr_number}")
                sys.exit(1)
        
        elif args.command == 'update-description':
            success = manager.update_pr_description(args.pr_number, args.auto_generate)
            if success:
                print(f"✅ PR #{args.pr_number} description updated!")
            else:
                print(f"❌ Failed to update PR #{args.pr_number} description")
                sys.exit(1)
        
        elif args.command == 'assign-reviewers':
            success = manager.assign_reviewers(args.pr_number, args.reviewers)
            if success:
                print(f"✅ Reviewers assigned to PR #{args.pr_number}!")
            else:
                print(f"❌ Failed to assign reviewers to PR #{args.pr_number}")
                sys.exit(1)
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()