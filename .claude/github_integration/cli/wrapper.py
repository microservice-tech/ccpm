#!/usr/bin/env python3
"""
GitHub CLI Wrapper for Claude Flow

Provides a consistent wrapper around the GitHub CLI (gh) tool for reliable 
GitHub operations with error handling, rate limiting, and logging.
"""

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass
from enum import Enum


class GitHubCLIError(Exception):
    """Raised when GitHub CLI operations fail."""
    pass


class AuthenticationStatus(Enum):
    """GitHub CLI authentication status."""
    AUTHENTICATED = "authenticated"
    NOT_AUTHENTICATED = "not_authenticated"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass
class CLIResult:
    """Result of GitHub CLI command execution."""
    success: bool
    stdout: str
    stderr: str
    return_code: int
    command: List[str]
    execution_time: float


@dataclass
class PRInfo:
    """Pull request information from GitHub CLI."""
    number: int
    title: str
    state: str
    author: str
    base_ref: str
    head_ref: str
    url: str
    draft: bool
    mergeable: str
    created_at: str
    updated_at: str


@dataclass
class IssueInfo:
    """Issue information from GitHub CLI."""
    number: int
    title: str
    state: str
    author: str
    url: str
    labels: List[str]
    assignees: List[str]
    created_at: str
    updated_at: str


class GitHubCLIWrapper:
    """
    Wrapper around GitHub CLI (gh) for consistent GitHub operations.
    
    This wrapper provides:
    - Consistent error handling and logging
    - Rate limiting and retry logic
    - Authentication status checking
    - Type-safe result handling
    - Integration with Claude Flow configuration
    """
    
    def __init__(self, workspace_path: Optional[Path] = None):
        """
        Initialize GitHub CLI wrapper.
        
        Args:
            workspace_path: Working directory for git operations
        """
        self.logger = logging.getLogger(__name__)
        self.workspace_path = workspace_path or Path.cwd()
        
        # Check if gh CLI is available
        self._verify_gh_cli()
        
        # Check authentication status
        self.auth_status = self._check_auth_status()
        
        if self.auth_status != AuthenticationStatus.AUTHENTICATED:
            self.logger.warning(f"GitHub CLI authentication status: {self.auth_status.value}")
    
    def _verify_gh_cli(self) -> None:
        """Verify that GitHub CLI is installed and accessible."""
        try:
            result = subprocess.run(
                ["gh", "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                raise GitHubCLIError("GitHub CLI is not properly installed")
                
            version_info = result.stdout.strip()
            self.logger.info(f"GitHub CLI available: {version_info.split()[2] if len(version_info.split()) > 2 else 'unknown version'}")
            
        except FileNotFoundError:
            raise GitHubCLIError(
                "GitHub CLI (gh) not found. Please install it from https://cli.github.com/"
            )
        except subprocess.TimeoutExpired:
            raise GitHubCLIError("GitHub CLI command timed out")
    
    def _check_auth_status(self) -> AuthenticationStatus:
        """Check GitHub CLI authentication status."""
        try:
            result = self._run_gh_command(["gh", "auth", "status"])
            
            if result.return_code == 0:
                if "Logged in to github.com" in result.stderr:
                    return AuthenticationStatus.AUTHENTICATED
                else:
                    return AuthenticationStatus.NOT_AUTHENTICATED
            else:
                if "authentication failed" in result.stderr.lower():
                    return AuthenticationStatus.EXPIRED
                else:
                    return AuthenticationStatus.NOT_AUTHENTICATED
                    
        except Exception as e:
            self.logger.error(f"Failed to check auth status: {e}")
            return AuthenticationStatus.UNKNOWN
    
    def _run_gh_command(
        self,
        command: List[str],
        input_data: Optional[str] = None,
        timeout: int = 60,
        retries: int = 3
    ) -> CLIResult:
        """
        Execute GitHub CLI command with error handling and retries.
        
        Args:
            command: Command and arguments to execute
            input_data: Optional stdin data
            timeout: Command timeout in seconds
            retries: Number of retry attempts
            
        Returns:
            CLIResult with execution details
        """
        start_time = time.time()
        last_error = None
        
        for attempt in range(retries + 1):
            try:
                if attempt > 0:
                    self.logger.info(f"Retrying command (attempt {attempt + 1}/{retries + 1}): {' '.join(command)}")
                    time.sleep(2 ** attempt)  # Exponential backoff
                
                result = subprocess.run(
                    command,
                    cwd=self.workspace_path,
                    input=input_data,
                    capture_output=True,
                    text=True,
                    timeout=timeout
                )
                
                execution_time = time.time() - start_time
                
                cli_result = CLIResult(
                    success=result.returncode == 0,
                    stdout=result.stdout.strip(),
                    stderr=result.stderr.strip(),
                    return_code=result.returncode,
                    command=command,
                    execution_time=execution_time
                )
                
                if cli_result.success:
                    self.logger.debug(f"Command succeeded: {' '.join(command)}")
                    return cli_result
                else:
                    self.logger.warning(f"Command failed (attempt {attempt + 1}): {' '.join(command)}")
                    self.logger.warning(f"Error: {cli_result.stderr}")
                    
                    # Don't retry for authentication errors
                    if "authentication failed" in cli_result.stderr.lower():
                        self.auth_status = AuthenticationStatus.EXPIRED
                        break
                    
                    last_error = cli_result
                    
            except subprocess.TimeoutExpired:
                error_msg = f"Command timed out after {timeout}s: {' '.join(command)}"
                self.logger.error(error_msg)
                last_error = CLIResult(
                    success=False,
                    stdout="",
                    stderr=error_msg,
                    return_code=-1,
                    command=command,
                    execution_time=timeout
                )
                
            except Exception as e:
                error_msg = f"Command execution failed: {e}"
                self.logger.error(error_msg)
                last_error = CLIResult(
                    success=False,
                    stdout="",
                    stderr=error_msg,
                    return_code=-1,
                    command=command,
                    execution_time=time.time() - start_time
                )
        
        return last_error or CLIResult(
            success=False,
            stdout="",
            stderr="All retry attempts failed",
            return_code=-1,
            command=command,
            execution_time=time.time() - start_time
        )
    
    def create_pull_request(
        self,
        title: str,
        body: str = "",
        head: Optional[str] = None,
        base: str = "main",
        draft: bool = False,
        assignee: Optional[str] = None,
        labels: Optional[List[str]] = None,
        reviewers: Optional[List[str]] = None
    ) -> Optional[PRInfo]:
        """
        Create a pull request using GitHub CLI.
        
        Args:
            title: PR title
            body: PR description
            head: Source branch (current branch if None)
            base: Target branch
            draft: Create as draft PR
            assignee: Assignee username
            labels: List of labels to add
            reviewers: List of reviewer usernames
            
        Returns:
            PRInfo object if successful, None otherwise
        """
        try:
            self.logger.info(f"Creating pull request: {title}")
            
            command = ["gh", "pr", "create", "--title", title, "--base", base]
            
            if body:
                command.extend(["--body", body])
            
            if head:
                command.extend(["--head", head])
            
            if draft:
                command.append("--draft")
            
            if assignee:
                command.extend(["--assignee", assignee])
            
            if labels:
                command.extend(["--label", ",".join(labels)])
            
            if reviewers:
                command.extend(["--reviewer", ",".join(reviewers)])
            
            result = self._run_gh_command(command)
            
            if result.success:
                # Extract PR URL from output
                pr_url = result.stdout.strip()
                
                # Get PR info
                return self.get_pull_request_info(url=pr_url)
            else:
                self.logger.error(f"Failed to create PR: {result.stderr}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error creating pull request: {e}")
            return None
    
    def get_pull_request_info(
        self,
        number: Optional[int] = None,
        url: Optional[str] = None
    ) -> Optional[PRInfo]:
        """
        Get pull request information.
        
        Args:
            number: PR number (if known)
            url: PR URL (alternative to number)
            
        Returns:
            PRInfo object if found, None otherwise
        """
        try:
            if number:
                command = ["gh", "pr", "view", str(number), "--json", "number,title,state,author,baseRefName,headRefName,url,isDraft,mergeable,createdAt,updatedAt"]
            elif url:
                command = ["gh", "pr", "view", url, "--json", "number,title,state,author,baseRefName,headRefName,url,isDraft,mergeable,createdAt,updatedAt"]
            else:
                raise ValueError("Either PR number or URL must be provided")
            
            result = self._run_gh_command(command)
            
            if result.success:
                data = json.loads(result.stdout)
                
                return PRInfo(
                    number=data["number"],
                    title=data["title"],
                    state=data["state"],
                    author=data["author"]["login"],
                    base_ref=data["baseRefName"],
                    head_ref=data["headRefName"],
                    url=data["url"],
                    draft=data["isDraft"],
                    mergeable=data["mergeable"],
                    created_at=data["createdAt"],
                    updated_at=data["updatedAt"]
                )
            else:
                self.logger.error(f"Failed to get PR info: {result.stderr}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error getting pull request info: {e}")
            return None
    
    def update_pull_request(
        self,
        number: int,
        title: Optional[str] = None,
        body: Optional[str] = None,
        ready: Optional[bool] = None,
        add_labels: Optional[List[str]] = None,
        remove_labels: Optional[List[str]] = None
    ) -> bool:
        """
        Update pull request properties.
        
        Args:
            number: PR number
            title: New title
            body: New description
            ready: Mark as ready for review (convert from draft)
            add_labels: Labels to add
            remove_labels: Labels to remove
            
        Returns:
            True if update successful
        """
        try:
            success = True
            
            # Update title and/or body
            if title or body:
                command = ["gh", "pr", "edit", str(number)]
                
                if title:
                    command.extend(["--title", title])
                
                if body:
                    command.extend(["--body", body])
                
                result = self._run_gh_command(command)
                success = success and result.success
                
                if not result.success:
                    self.logger.error(f"Failed to update PR {number}: {result.stderr}")
            
            # Mark as ready for review
            if ready is True:
                result = self._run_gh_command(["gh", "pr", "ready", str(number)])
                success = success and result.success
                
                if not result.success:
                    self.logger.error(f"Failed to mark PR {number} as ready: {result.stderr}")
            
            # Add labels
            if add_labels:
                for label in add_labels:
                    result = self._run_gh_command(["gh", "pr", "edit", str(number), "--add-label", label])
                    if not result.success:
                        self.logger.warning(f"Failed to add label '{label}' to PR {number}")
            
            # Remove labels
            if remove_labels:
                for label in remove_labels:
                    result = self._run_gh_command(["gh", "pr", "edit", str(number), "--remove-label", label])
                    if not result.success:
                        self.logger.warning(f"Failed to remove label '{label}' from PR {number}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error updating pull request: {e}")
            return False
    
    def add_pr_comment(self, number: int, body: str) -> bool:
        """
        Add comment to pull request.
        
        Args:
            number: PR number
            body: Comment body
            
        Returns:
            True if comment added successfully
        """
        try:
            result = self._run_gh_command(
                ["gh", "pr", "comment", str(number), "--body", body]
            )
            
            if result.success:
                self.logger.info(f"Added comment to PR #{number}")
                return True
            else:
                self.logger.error(f"Failed to add comment to PR {number}: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error adding PR comment: {e}")
            return False
    
    def list_pull_requests(
        self,
        state: str = "open",
        author: Optional[str] = None,
        assignee: Optional[str] = None,
        label: Optional[str] = None,
        limit: int = 30
    ) -> List[PRInfo]:
        """
        List pull requests with filters.
        
        Args:
            state: PR state (open, closed, merged, all)
            author: Filter by author
            assignee: Filter by assignee
            label: Filter by label
            limit: Maximum number of PRs to return
            
        Returns:
            List of PRInfo objects
        """
        try:
            command = [
                "gh", "pr", "list",
                "--state", state,
                "--limit", str(limit),
                "--json", "number,title,state,author,baseRefName,headRefName,url,isDraft,mergeable,createdAt,updatedAt"
            ]
            
            if author:
                command.extend(["--author", author])
            
            if assignee:
                command.extend(["--assignee", assignee])
            
            if label:
                command.extend(["--label", label])
            
            result = self._run_gh_command(command)
            
            if result.success:
                data = json.loads(result.stdout)
                
                return [
                    PRInfo(
                        number=pr["number"],
                        title=pr["title"],
                        state=pr["state"],
                        author=pr["author"]["login"],
                        base_ref=pr["baseRefName"],
                        head_ref=pr["headRefName"],
                        url=pr["url"],
                        draft=pr["isDraft"],
                        mergeable=pr["mergeable"],
                        created_at=pr["createdAt"],
                        updated_at=pr["updatedAt"]
                    )
                    for pr in data
                ]
            else:
                self.logger.error(f"Failed to list PRs: {result.stderr}")
                return []
                
        except Exception as e:
            self.logger.error(f"Error listing pull requests: {e}")
            return []
    
    def merge_pull_request(
        self,
        number: int,
        merge_method: str = "merge",
        delete_branch: bool = True
    ) -> bool:
        """
        Merge a pull request.
        
        Args:
            number: PR number
            merge_method: Merge method (merge, squash, rebase)
            delete_branch: Delete head branch after merge
            
        Returns:
            True if merge successful
        """
        try:
            command = ["gh", "pr", "merge", str(number)]
            
            if merge_method == "squash":
                command.append("--squash")
            elif merge_method == "rebase":
                command.append("--rebase")
            else:
                command.append("--merge")
            
            if delete_branch:
                command.append("--delete-branch")
            
            result = self._run_gh_command(command)
            
            if result.success:
                self.logger.info(f"PR #{number} merged successfully")
                return True
            else:
                self.logger.error(f"Failed to merge PR {number}: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error merging pull request: {e}")
            return False
    
    def create_issue(
        self,
        title: str,
        body: str = "",
        assignees: Optional[List[str]] = None,
        labels: Optional[List[str]] = None,
        milestone: Optional[str] = None
    ) -> Optional[IssueInfo]:
        """
        Create a new issue.
        
        Args:
            title: Issue title
            body: Issue description
            assignees: List of assignee usernames
            labels: List of labels to add
            milestone: Milestone name
            
        Returns:
            IssueInfo object if successful, None otherwise
        """
        try:
            command = ["gh", "issue", "create", "--title", title]
            
            if body:
                command.extend(["--body", body])
            
            if assignees:
                command.extend(["--assignee", ",".join(assignees)])
            
            if labels:
                command.extend(["--label", ",".join(labels)])
            
            if milestone:
                command.extend(["--milestone", milestone])
            
            result = self._run_gh_command(command)
            
            if result.success:
                # Extract issue URL from output
                issue_url = result.stdout.strip()
                
                # Get issue info
                return self.get_issue_info(url=issue_url)
            else:
                self.logger.error(f"Failed to create issue: {result.stderr}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error creating issue: {e}")
            return None
    
    def get_issue_info(
        self,
        number: Optional[int] = None,
        url: Optional[str] = None
    ) -> Optional[IssueInfo]:
        """
        Get issue information.
        
        Args:
            number: Issue number (if known)
            url: Issue URL (alternative to number)
            
        Returns:
            IssueInfo object if found, None otherwise
        """
        try:
            if number:
                command = ["gh", "issue", "view", str(number), "--json", "number,title,state,author,url,labels,assignees,createdAt,updatedAt"]
            elif url:
                command = ["gh", "issue", "view", url, "--json", "number,title,state,author,url,labels,assignees,createdAt,updatedAt"]
            else:
                raise ValueError("Either issue number or URL must be provided")
            
            result = self._run_gh_command(command)
            
            if result.success:
                data = json.loads(result.stdout)
                
                return IssueInfo(
                    number=data["number"],
                    title=data["title"],
                    state=data["state"],
                    author=data["author"]["login"],
                    url=data["url"],
                    labels=[label["name"] for label in data.get("labels", [])],
                    assignees=[assignee["login"] for assignee in data.get("assignees", [])],
                    created_at=data["createdAt"],
                    updated_at=data["updatedAt"]
                )
            else:
                self.logger.error(f"Failed to get issue info: {result.stderr}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error getting issue info: {e}")
            return None
    
    def get_repository_info(self) -> Optional[Dict[str, Any]]:
        """
        Get repository information.
        
        Returns:
            Repository info dictionary if successful, None otherwise
        """
        try:
            result = self._run_gh_command([
                "gh", "repo", "view", 
                "--json", "owner,name,defaultBranch,isPrivate,url,description,createdAt,updatedAt"
            ])
            
            if result.success:
                return json.loads(result.stdout)
            else:
                self.logger.error(f"Failed to get repository info: {result.stderr}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error getting repository info: {e}")
            return None
    
    def check_repository_access(self) -> bool:
        """
        Check if we have access to the current repository.
        
        Returns:
            True if we have access
        """
        try:
            result = self._run_gh_command(["gh", "repo", "view", "--json", "name"])
            return result.success
        except Exception:
            return False
    
    def is_authenticated(self) -> bool:
        """Check if GitHub CLI is authenticated."""
        return self.auth_status == AuthenticationStatus.AUTHENTICATED
    
    def get_current_user(self) -> Optional[str]:
        """
        Get current authenticated user.
        
        Returns:
            Username if authenticated, None otherwise
        """
        try:
            result = self._run_gh_command(["gh", "api", "user", "--jq", ".login"])
            
            if result.success:
                return result.stdout.strip()
            else:
                return None
                
        except Exception as e:
            self.logger.error(f"Error getting current user: {e}")
            return None


def main():
    """Command-line interface for GitHub CLI wrapper testing."""
    import argparse
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    parser = argparse.ArgumentParser(description='GitHub CLI Wrapper')
    parser.add_argument('command', choices=['auth-status', 'repo-info', 'list-prs', 'test'], 
                       help='Command to run')
    parser.add_argument('--workspace', help='Workspace directory')
    
    args = parser.parse_args()
    
    try:
        workspace = Path(args.workspace) if args.workspace else Path.cwd()
        wrapper = GitHubCLIWrapper(workspace)
        
        if args.command == 'auth-status':
            status = wrapper.auth_status.value
            user = wrapper.get_current_user()
            print(f"Authentication Status: {status}")
            if user:
                print(f"Current User: {user}")
        
        elif args.command == 'repo-info':
            info = wrapper.get_repository_info()
            if info:
                print(f"Repository: {info['owner']['login']}/{info['name']}")
                print(f"Private: {info['isPrivate']}")
                print(f"Default Branch: {info['defaultBranch']}")
                print(f"URL: {info['url']}")
            else:
                print("Failed to get repository info")
        
        elif args.command == 'list-prs':
            prs = wrapper.list_pull_requests(limit=10)
            print(f"Found {len(prs)} pull requests:")
            for pr in prs:
                print(f"  #{pr.number}: {pr.title} ({pr.state})")
        
        elif args.command == 'test':
            print("Running basic functionality tests...")
            
            # Test authentication
            print(f"✓ Authentication: {wrapper.is_authenticated()}")
            
            # Test repository access
            print(f"✓ Repository Access: {wrapper.check_repository_access()}")
            
            # Test PR listing
            prs = wrapper.list_pull_requests(limit=5)
            print(f"✓ PR Listing: Found {len(prs)} PRs")
            
            print("All tests completed!")
        
    except GitHubCLIError as e:
        print(f"GitHub CLI Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()