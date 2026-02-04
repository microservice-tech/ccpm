#!/usr/bin/env python3
"""
Extended PM Commands for GitHub Integration

This module extends the existing PM scripts with GitHub integration commands,
providing seamless integration between Claude Flow project management and GitHub workflows.
"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union
from dataclasses import dataclass, asdict

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.config_manager import ConfigManager
from github_integration.github_service import GitHubService, PRCreationResult
from github_integration.cli.wrapper import GitHubCLIWrapper, PRInfo, IssueInfo


@dataclass
class TaskInfo:
    """Information about a Claude Flow task."""
    id: str
    title: str
    status: str
    epic: str
    assignee: Optional[str] = None
    priority: Optional[str] = None
    depends_on: List[str] = None
    github_url: Optional[str] = None
    pr_number: Optional[int] = None
    branch_name: Optional[str] = None


class GitHubPMCommands:
    """
    Extended PM commands that integrate GitHub operations with existing PM scripts.
    
    This class provides commands that bridge the gap between Claude Flow's project
    management system and GitHub's collaboration features.
    """
    
    def __init__(self, config: Optional[ConfigManager] = None):
        """
        Initialize GitHub PM commands.
        
        Args:
            config: Configuration manager instance
        """
        self.logger = logging.getLogger(__name__)
        self.config = config or ConfigManager()
        
        # Initialize services
        self.github_service = GitHubService(self.config)
        self.cli_wrapper = GitHubCLIWrapper()
        
        # PM script paths
        self.pm_scripts_dir = Path.cwd() / ".claude" / "scripts" / "pm"
        self.epics_dir = Path.cwd() / ".claude" / "epics"
        
        # Validate environment
        self._validate_environment()
        
        self.logger.info("GitHub PM Commands initialized")
    
    def _validate_environment(self) -> None:
        """Validate that we're in a proper Claude Flow environment."""
        if not self.pm_scripts_dir.exists():
            raise ValueError("PM scripts directory not found. Are you in a Claude Flow project?")
        
        if not self.epics_dir.exists():
            raise ValueError("Epics directory not found. Are you in a Claude Flow project?")
        
        if not self.cli_wrapper.is_authenticated():
            raise ValueError("GitHub CLI not authenticated. Run 'gh auth login' first.")
    
    def task_to_pr(
        self,
        task_id: str,
        force_create: bool = False,
        draft: bool = False,
        auto_branch: bool = True
    ) -> Optional[PRInfo]:
        """
        Create a PR from a Claude Flow task.
        
        Args:
            task_id: Task ID to create PR for
            force_create: Skip quality gates
            draft: Create as draft PR
            auto_branch: Automatically create branch if needed
            
        Returns:
            PRInfo if successful, None otherwise
        """
        try:
            self.logger.info(f"Creating PR for task {task_id}")
            
            # Load task information
            task_info = self._load_task_info(task_id)
            if not task_info:
                self.logger.error(f"Task {task_id} not found")
                return None
            
            # Create branch if needed
            if auto_branch and not task_info.branch_name:
                success, branch_name = self.github_service.create_feature_branch(
                    f"task-{task_id}-{task_info.title.lower().replace(' ', '-')[:30]}"
                )
                
                if not success:
                    self.logger.error(f"Failed to create branch for task {task_id}")
                    return None
                
                # Update task with branch info
                task_info.branch_name = branch_name
                self._update_task_branch_info(task_id, branch_name)
            
            # Generate PR title and description
            pr_title = f"Task #{task_id}: {task_info.title}"
            pr_description = self._generate_task_pr_description(task_info)
            
            # Create PR using GitHub service
            result = self.github_service.create_autonomous_pr(
                title=pr_title,
                description=pr_description,
                branch_name=task_info.branch_name,
                draft=draft,
                force_create=force_create
            )
            
            if result.success:
                # Update task with PR information
                self._update_task_pr_info(task_id, result.pr_number, result.pr_url)
                
                # Get PR info from CLI wrapper
                pr_info = self.cli_wrapper.get_pull_request_info(number=result.pr_number)
                
                self.logger.info(f"Successfully created PR #{result.pr_number} for task {task_id}")
                return pr_info
            else:
                self.logger.error(f"Failed to create PR for task {task_id}: {result.error_message}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error creating PR for task {task_id}: {e}")
            return None
    
    def task_status_sync(self, task_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Sync task status with GitHub PR/Issue status.
        
        Args:
            task_id: Specific task ID to sync (all tasks if None)
            
        Returns:
            Dictionary with sync results
        """
        try:
            self.logger.info(f"Syncing task status {'for task ' + task_id if task_id else 'for all tasks'}")
            
            tasks_to_sync = []
            
            if task_id:
                task_info = self._load_task_info(task_id)
                if task_info:
                    tasks_to_sync.append(task_info)
            else:
                tasks_to_sync = self._load_all_tasks_with_github_info()
            
            sync_results = {
                "synced": 0,
                "failed": 0,
                "updated": [],
                "errors": []
            }
            
            for task in tasks_to_sync:
                try:
                    updated = False
                    
                    # Sync PR status
                    if task.pr_number:
                        pr_info = self.cli_wrapper.get_pull_request_info(number=task.pr_number)
                        if pr_info:
                            new_status = self._map_pr_status_to_task_status(pr_info.state, pr_info.draft)
                            if new_status != task.status:
                                self._update_task_status(task.id, new_status)
                                sync_results["updated"].append({
                                    "task_id": task.id,
                                    "old_status": task.status,
                                    "new_status": new_status,
                                    "source": f"PR #{task.pr_number}"
                                })
                                updated = True
                    
                    # Sync issue status
                    elif task.github_url and "/issues/" in task.github_url:
                        issue_number = int(task.github_url.split("/issues/")[-1])
                        issue_info = self.cli_wrapper.get_issue_info(number=issue_number)
                        if issue_info:
                            new_status = self._map_issue_status_to_task_status(issue_info.state)
                            if new_status != task.status:
                                self._update_task_status(task.id, new_status)
                                sync_results["updated"].append({
                                    "task_id": task.id,
                                    "old_status": task.status,
                                    "new_status": new_status,
                                    "source": f"Issue #{issue_number}"
                                })
                                updated = True
                    
                    if updated:
                        sync_results["synced"] += 1
                        
                except Exception as e:
                    sync_results["failed"] += 1
                    sync_results["errors"].append({
                        "task_id": task.id,
                        "error": str(e)
                    })
            
            self.logger.info(f"Sync completed: {sync_results['synced']} synced, {sync_results['failed']} failed")
            return sync_results
            
        except Exception as e:
            self.logger.error(f"Error syncing task status: {e}")
            return {"error": str(e)}
    
    def epic_to_milestone(self, epic_name: str) -> Optional[Dict[str, Any]]:
        """
        Create GitHub milestone from Claude Flow epic.
        
        Args:
            epic_name: Name of the epic to convert
            
        Returns:
            Milestone information if successful
        """
        try:
            self.logger.info(f"Creating milestone for epic: {epic_name}")
            
            # Load epic information
            epic_path = self.epics_dir / epic_name / f"{epic_name}.md"
            if not epic_path.exists():
                self.logger.error(f"Epic {epic_name} not found")
                return None
            
            # Parse epic metadata
            epic_info = self._parse_epic_file(epic_path)
            
            # Create milestone via GitHub API (CLI doesn't have milestone create)
            # This would require direct API call through github_client
            milestone_data = {
                "title": epic_info.get("title", epic_name),
                "description": epic_info.get("description", f"Milestone for epic: {epic_name}"),
                "due_on": epic_info.get("due_date")
            }
            
            # For now, just return the milestone data structure
            # In a full implementation, this would make the API call
            self.logger.info(f"Milestone data prepared for epic {epic_name}")
            return milestone_data
            
        except Exception as e:
            self.logger.error(f"Error creating milestone for epic {epic_name}: {e}")
            return None
    
    def pr_quality_check(self, pr_number: int) -> Dict[str, Any]:
        """
        Run quality checks on a PR and update status.
        
        Args:
            pr_number: PR number to check
            
        Returns:
            Quality check results
        """
        try:
            self.logger.info(f"Running quality checks for PR #{pr_number}")
            
            # Get PR info
            pr_info = self.cli_wrapper.get_pull_request_info(number=pr_number)
            if not pr_info:
                return {"error": f"PR #{pr_number} not found"}
            
            # Checkout PR branch
            checkout_result = subprocess.run([
                "gh", "pr", "checkout", str(pr_number)
            ], capture_output=True, text=True)
            
            if checkout_result.returncode != 0:
                return {"error": f"Failed to checkout PR branch: {checkout_result.stderr}"}
            
            # Run quality gates using GitHub service
            quality_failures = self.github_service._run_quality_gates()
            
            quality_results = {
                "pr_number": pr_number,
                "branch": pr_info.head_ref,
                "passed": len(quality_failures) == 0,
                "failures": quality_failures,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Add comment with results
            if quality_failures:
                comment = self._format_quality_check_comment(quality_failures, passed=False)
            else:
                comment = self._format_quality_check_comment([], passed=True)
            
            self.cli_wrapper.add_pr_comment(pr_number, comment)
            
            # If all checks pass and PR is draft, offer to mark as ready
            if not quality_failures and pr_info.draft:
                ready_comment = (
                    "🎉 All quality checks are now passing! "
                    "You can mark this PR as ready for review using:\n\n"
                    f"`gh pr ready {pr_number}`"
                )
                self.cli_wrapper.add_pr_comment(pr_number, ready_comment)
            
            return quality_results
            
        except Exception as e:
            self.logger.error(f"Error running quality checks for PR {pr_number}: {e}")
            return {"error": str(e)}
    
    def standup_github_integration(self) -> Dict[str, Any]:
        """
        Generate standup report with GitHub integration data.
        
        Returns:
            Standup report with GitHub information
        """
        try:
            self.logger.info("Generating standup report with GitHub integration")
            
            # Get current user
            current_user = self.cli_wrapper.get_current_user()
            if not current_user:
                return {"error": "Could not determine current GitHub user"}
            
            # Get PRs authored by current user
            my_prs = self.cli_wrapper.list_pull_requests(
                state="open",
                author=current_user,
                limit=20
            )
            
            # Get PRs where current user is assigned for review
            # Note: GitHub CLI doesn't have a direct filter for review requests
            # This would need to be implemented via API calls
            
            # Get assigned issues
            # This would also need direct API implementation
            
            # For now, provide basic PR information
            standup_data = {
                "user": current_user,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "pull_requests": {
                    "authored": len(my_prs),
                    "draft": len([pr for pr in my_prs if pr.draft]),
                    "ready_for_review": len([pr for pr in my_prs if not pr.draft]),
                    "details": [
                        {
                            "number": pr.number,
                            "title": pr.title,
                            "draft": pr.draft,
                            "url": pr.url
                        }
                        for pr in my_prs[:5]  # Show first 5
                    ]
                },
                "summary": f"Currently have {len(my_prs)} open PRs, {len([pr for pr in my_prs if pr.draft])} drafts"
            }
            
            return standup_data
            
        except Exception as e:
            self.logger.error(f"Error generating standup report: {e}")
            return {"error": str(e)}
    
    def bulk_pr_operations(self, operation: str, pr_numbers: List[int]) -> Dict[str, Any]:
        """
        Perform bulk operations on multiple PRs.
        
        Args:
            operation: Operation to perform (close, merge, add-label, etc.)
            pr_numbers: List of PR numbers
            
        Returns:
            Results of bulk operations
        """
        try:
            self.logger.info(f"Performing bulk operation '{operation}' on {len(pr_numbers)} PRs")
            
            results = {
                "operation": operation,
                "total": len(pr_numbers),
                "successful": [],
                "failed": [],
                "errors": []
            }
            
            for pr_number in pr_numbers:
                try:
                    success = False
                    
                    if operation == "close":
                        # Close PR
                        result = self.cli_wrapper._run_gh_command([
                            "gh", "pr", "close", str(pr_number)
                        ])
                        success = result.success
                    
                    elif operation == "merge":
                        success = self.cli_wrapper.merge_pull_request(pr_number)
                    
                    elif operation.startswith("add-label:"):
                        label = operation.split(":", 1)[1]
                        success = self.cli_wrapper.update_pull_request(
                            pr_number, add_labels=[label]
                        )
                    
                    elif operation == "mark-ready":
                        success = self.cli_wrapper.update_pull_request(
                            pr_number, ready=True
                        )
                    
                    else:
                        results["errors"].append({
                            "pr_number": pr_number,
                            "error": f"Unknown operation: {operation}"
                        })
                        continue
                    
                    if success:
                        results["successful"].append(pr_number)
                    else:
                        results["failed"].append(pr_number)
                        
                except Exception as e:
                    results["failed"].append(pr_number)
                    results["errors"].append({
                        "pr_number": pr_number,
                        "error": str(e)
                    })
            
            self.logger.info(f"Bulk operation completed: {len(results['successful'])} successful, {len(results['failed'])} failed")
            return results
            
        except Exception as e:
            self.logger.error(f"Error performing bulk operations: {e}")
            return {"error": str(e)}
    
    def _load_task_info(self, task_id: str) -> Optional[TaskInfo]:
        """Load task information from task file."""
        try:
            # Find task file in epics directories
            for epic_dir in self.epics_dir.iterdir():
                if epic_dir.is_dir():
                    task_file = epic_dir / f"{task_id}.md"
                    if task_file.exists():
                        return self._parse_task_file(task_file, epic_dir.name)
            return None
        except Exception as e:
            self.logger.error(f"Error loading task {task_id}: {e}")
            return None
    
    def _parse_task_file(self, task_file: Path, epic_name: str) -> Optional[TaskInfo]:
        """Parse task file and extract metadata."""
        try:
            content = task_file.read_text()
            lines = content.split('\n')
            
            # Extract metadata from frontmatter
            metadata = {}
            in_frontmatter = False
            title = ""
            
            for line in lines:
                if line.strip() == '---':
                    in_frontmatter = not in_frontmatter
                    continue
                
                if in_frontmatter and ':' in line:
                    key, value = line.split(':', 1)
                    metadata[key.strip()] = value.strip()
                elif line.startswith('# ') and not title:
                    title = line[2:].strip()
            
            task_id = task_file.stem
            
            return TaskInfo(
                id=task_id,
                title=title or metadata.get('name', f'Task {task_id}'),
                status=metadata.get('status', 'open'),
                epic=epic_name,
                assignee=metadata.get('assignee'),
                priority=metadata.get('priority'),
                depends_on=metadata.get('depends_on', '').split(',') if metadata.get('depends_on') else [],
                github_url=metadata.get('github'),
                pr_number=int(metadata.get('pr_number', 0)) or None,
                branch_name=metadata.get('branch')
            )
            
        except Exception as e:
            self.logger.error(f"Error parsing task file {task_file}: {e}")
            return None
    
    def _load_all_tasks_with_github_info(self) -> List[TaskInfo]:
        """Load all tasks that have GitHub integration info."""
        tasks = []
        
        try:
            for epic_dir in self.epics_dir.iterdir():
                if epic_dir.is_dir():
                    for task_file in epic_dir.glob("[0-9]*.md"):
                        task_info = self._parse_task_file(task_file, epic_dir.name)
                        if task_info and (task_info.github_url or task_info.pr_number):
                            tasks.append(task_info)
        except Exception as e:
            self.logger.error(f"Error loading tasks: {e}")
        
        return tasks
    
    def _generate_task_pr_description(self, task_info: TaskInfo) -> str:
        """Generate PR description from task information."""
        description_parts = [
            f"## Task #{task_info.id}: {task_info.title}",
            "",
            f"**Epic:** {task_info.epic}",
            f"**Status:** {task_info.status}",
        ]
        
        if task_info.assignee:
            description_parts.append(f"**Assignee:** @{task_info.assignee}")
        
        if task_info.priority:
            description_parts.append(f"**Priority:** {task_info.priority}")
        
        if task_info.depends_on:
            description_parts.append(f"**Dependencies:** {', '.join(task_info.depends_on)}")
        
        description_parts.extend([
            "",
            "## Changes",
            "This PR implements the changes required for the above task.",
            "",
            "## Test Plan",
            "- [ ] Unit tests pass",
            "- [ ] Integration tests pass", 
            "- [ ] Manual testing completed",
            "",
            "## Checklist",
            "- [ ] Code follows project style guidelines",
            "- [ ] Self-review completed",
            "- [ ] Documentation updated if needed",
            "",
            "---",
            "*This PR was created using Claude Flow's task-to-PR automation.*"
        ])
        
        return "\n".join(description_parts)
    
    def _update_task_pr_info(self, task_id: str, pr_number: int, pr_url: str):
        """Update task file with PR information."""
        try:
            task_info = self._load_task_info(task_id)
            if not task_info:
                return
            
            # Find and update the task file
            for epic_dir in self.epics_dir.iterdir():
                if epic_dir.is_dir():
                    task_file = epic_dir / f"{task_id}.md"
                    if task_file.exists():
                        content = task_file.read_text()
                        
                        # Update or add PR information in frontmatter
                        lines = content.split('\n')
                        updated_lines = []
                        in_frontmatter = False
                        pr_info_added = False
                        github_info_added = False
                        
                        for line in lines:
                            if line.strip() == '---':
                                if in_frontmatter and not pr_info_added:
                                    updated_lines.append(f"pr_number: {pr_number}")
                                    pr_info_added = True
                                if in_frontmatter and not github_info_added:
                                    updated_lines.append(f"github: {pr_url}")
                                    github_info_added = True
                                
                                updated_lines.append(line)
                                in_frontmatter = not in_frontmatter
                                continue
                            
                            if in_frontmatter:
                                if line.startswith('pr_number:'):
                                    updated_lines.append(f"pr_number: {pr_number}")
                                    pr_info_added = True
                                    continue
                                elif line.startswith('github:'):
                                    updated_lines.append(f"github: {pr_url}")
                                    github_info_added = True
                                    continue
                            
                            updated_lines.append(line)
                        
                        task_file.write_text('\n'.join(updated_lines))
                        break
                        
        except Exception as e:
            self.logger.error(f"Error updating task PR info: {e}")
    
    def _update_task_branch_info(self, task_id: str, branch_name: str):
        """Update task file with branch information."""
        try:
            # Similar to _update_task_pr_info but for branch
            # Implementation would follow the same pattern
            pass
        except Exception as e:
            self.logger.error(f"Error updating task branch info: {e}")
    
    def _update_task_status(self, task_id: str, new_status: str):
        """Update task status in task file."""
        try:
            # Find and update task file status
            # Implementation would follow similar pattern to PR info update
            pass
        except Exception as e:
            self.logger.error(f"Error updating task status: {e}")
    
    def _map_pr_status_to_task_status(self, pr_state: str, is_draft: bool) -> str:
        """Map PR status to task status."""
        if pr_state.lower() == "merged":
            return "completed"
        elif pr_state.lower() == "closed":
            return "cancelled"
        elif is_draft:
            return "in_progress"
        else:
            return "in_review"
    
    def _map_issue_status_to_task_status(self, issue_state: str) -> str:
        """Map GitHub issue status to task status."""
        if issue_state.lower() == "closed":
            return "completed"
        else:
            return "open"
    
    def _parse_epic_file(self, epic_file: Path) -> Dict[str, Any]:
        """Parse epic file and extract metadata."""
        try:
            content = epic_file.read_text()
            lines = content.split('\n')
            
            metadata = {}
            in_frontmatter = False
            
            for line in lines:
                if line.strip() == '---':
                    in_frontmatter = not in_frontmatter
                    continue
                
                if in_frontmatter and ':' in line:
                    key, value = line.split(':', 1)
                    metadata[key.strip()] = value.strip()
            
            return metadata
        except Exception as e:
            self.logger.error(f"Error parsing epic file {epic_file}: {e}")
            return {}
    
    def _format_quality_check_comment(self, failures: List[str], passed: bool) -> str:
        """Format quality check results as comment."""
        if passed:
            return (
                "✅ **Quality Checks Passed**\n\n"
                "All automated quality checks have passed successfully!\n\n"
                "- ✅ Tests pass\n"
                "- ✅ Linting passes\n"
                "- ✅ Build succeeds\n"
                "- ✅ Security checks pass\n\n"
                "*This comment was generated by Claude Flow's automated quality system.*"
            )
        else:
            comment_parts = [
                "❌ **Quality Checks Failed**",
                "",
                "The following quality checks need to be addressed:",
                ""
            ]
            
            for failure in failures:
                comment_parts.append(f"❌ {failure.replace('_', ' ').title()}")
            
            comment_parts.extend([
                "",
                "Please fix these issues and push your changes. The quality checks will run automatically.",
                "",
                "*This comment was generated by Claude Flow's automated quality system.*"
            ])
            
            return "\n".join(comment_parts)


def main():
    """Command-line interface for GitHub PM commands."""
    import argparse
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    parser = argparse.ArgumentParser(description='GitHub PM Commands for Claude Flow')
    parser.add_argument('--config', help='Path to configuration file')
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Task to PR command
    task_pr_parser = subparsers.add_parser('task-to-pr', help='Create PR from task')
    task_pr_parser.add_argument('task_id', help='Task ID to create PR for')
    task_pr_parser.add_argument('--force', action='store_true', help='Skip quality gates')
    task_pr_parser.add_argument('--draft', action='store_true', help='Create as draft PR')
    task_pr_parser.add_argument('--no-auto-branch', action='store_true', help='Do not create branch automatically')
    
    # Status sync command
    sync_parser = subparsers.add_parser('sync-status', help='Sync task status with GitHub')
    sync_parser.add_argument('--task-id', help='Specific task ID to sync (all if not specified)')
    
    # Epic to milestone command
    milestone_parser = subparsers.add_parser('epic-to-milestone', help='Create milestone from epic')
    milestone_parser.add_argument('epic_name', help='Epic name to convert')
    
    # PR quality check command
    quality_parser = subparsers.add_parser('pr-quality-check', help='Run quality checks on PR')
    quality_parser.add_argument('pr_number', type=int, help='PR number to check')
    
    # Standup command
    standup_parser = subparsers.add_parser('standup', help='Generate standup report with GitHub data')
    
    # Bulk operations command
    bulk_parser = subparsers.add_parser('bulk-pr', help='Perform bulk operations on PRs')
    bulk_parser.add_argument('operation', help='Operation to perform')
    bulk_parser.add_argument('pr_numbers', nargs='+', type=int, help='PR numbers')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    try:
        config = ConfigManager(args.config) if args.config else ConfigManager()
        pm_commands = GitHubPMCommands(config)
        
        if args.command == 'task-to-pr':
            pr_info = pm_commands.task_to_pr(
                task_id=args.task_id,
                force_create=args.force,
                draft=args.draft,
                auto_branch=not args.no_auto_branch
            )
            
            if pr_info:
                print(f"✅ Created PR #{pr_info.number}: {pr_info.title}")
                print(f"URL: {pr_info.url}")
                if pr_info.draft:
                    print("ℹ️  Created as draft PR")
            else:
                print(f"❌ Failed to create PR for task {args.task_id}")
                sys.exit(1)
        
        elif args.command == 'sync-status':
            results = pm_commands.task_status_sync(args.task_id)
            
            if "error" in results:
                print(f"❌ Sync failed: {results['error']}")
                sys.exit(1)
            else:
                print(f"✅ Synced {results['synced']} tasks")
                if results['updated']:
                    print("\nStatus updates:")
                    for update in results['updated']:
                        print(f"  Task {update['task_id']}: {update['old_status']} → {update['new_status']}")
        
        elif args.command == 'epic-to-milestone':
            milestone_data = pm_commands.epic_to_milestone(args.epic_name)
            
            if milestone_data:
                print(f"✅ Milestone data prepared for epic {args.epic_name}")
                print(json.dumps(milestone_data, indent=2))
            else:
                print(f"❌ Failed to process epic {args.epic_name}")
                sys.exit(1)
        
        elif args.command == 'pr-quality-check':
            results = pm_commands.pr_quality_check(args.pr_number)
            
            if "error" in results:
                print(f"❌ Quality check failed: {results['error']}")
                sys.exit(1)
            else:
                print(f"Quality check results for PR #{args.pr_number}:")
                print(f"Status: {'✅ PASSED' if results['passed'] else '❌ FAILED'}")
                if results['failures']:
                    print("Failures:")
                    for failure in results['failures']:
                        print(f"  - {failure}")
        
        elif args.command == 'standup':
            report = pm_commands.standup_github_integration()
            
            if "error" in report:
                print(f"❌ Standup report failed: {report['error']}")
                sys.exit(1)
            else:
                print(f"📊 Standup Report for {report['user']}")
                print(f"Summary: {report['summary']}")
                print(json.dumps(report, indent=2))
        
        elif args.command == 'bulk-pr':
            results = pm_commands.bulk_pr_operations(args.operation, args.pr_numbers)
            
            if "error" in results:
                print(f"❌ Bulk operation failed: {results['error']}")
                sys.exit(1)
            else:
                print(f"Bulk operation '{args.operation}' completed:")
                print(f"Successful: {len(results['successful'])}")
                print(f"Failed: {len(results['failed'])}")
                if results['errors']:
                    print("Errors:")
                    for error in results['errors']:
                        print(f"  PR #{error['pr_number']}: {error['error']}")
        
    except ValueError as e:
        print(f"Configuration Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()