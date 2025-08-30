#!/usr/bin/env python3
"""
GitHub API client with authentication and rate limiting.
Handles all GitHub API interactions for the Claude Flow service.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class GitHubRateLimitError(Exception):
    """Raised when GitHub rate limit is exceeded."""
    pass


class GitHubAuthenticationError(Exception):
    """Raised when GitHub authentication fails."""
    pass


class GitHubClient:
    """GitHub API client with proper authentication and rate limiting."""
    
    def __init__(self, token: str, base_url: str = "https://api.github.com"):
        """
        Initialize GitHub client.
        
        Args:
            token: GitHub personal access token or App token
            base_url: GitHub API base URL (for GitHub Enterprise)
        """
        self.token = token
        self.base_url = base_url.rstrip('/')
        self.logger = logging.getLogger(__name__)
        
        # Setup session with retries
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # Set headers
        self.session.headers.update({
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'claude-flow-service/1.0'
        })
        
        # Rate limiting state
        self.rate_limit_remaining = 5000
        self.rate_limit_reset = time.time()
        self.rate_limit_used = 0
    
    def _make_request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """
        Make authenticated request to GitHub API with rate limiting.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (without base URL)
            **kwargs: Additional arguments for requests
            
        Returns:
            Response object
            
        Raises:
            GitHubRateLimitError: When rate limit is exceeded
            GitHubAuthenticationError: When authentication fails
        """
        url = urljoin(self.base_url + '/', endpoint.lstrip('/'))
        
        # Check rate limit before making request
        self._check_rate_limit()
        
        # Make request
        try:
            response = self.session.request(method, url, **kwargs)
            
            # Update rate limit info from headers
            self._update_rate_limit(response)
            
            # Handle authentication errors
            if response.status_code == 401:
                raise GitHubAuthenticationError(f"Authentication failed: {response.text}")
            
            # Handle rate limit errors
            if response.status_code == 403 and 'rate limit' in response.text.lower():
                reset_time = int(response.headers.get('X-RateLimit-Reset', time.time()))
                wait_time = reset_time - time.time()
                raise GitHubRateLimitError(
                    f"Rate limit exceeded. Reset in {wait_time:.0f} seconds"
                )
            
            # Raise for other HTTP errors
            response.raise_for_status()
            
            return response
            
        except requests.RequestException as e:
            self.logger.error(f"GitHub API request failed: {e}")
            raise
    
    def _check_rate_limit(self) -> None:
        """Check if we're within rate limits, wait if necessary."""
        current_time = time.time()
        
        # If rate limit has reset, restore full quota
        if current_time >= self.rate_limit_reset:
            self.rate_limit_remaining = 5000
            self.rate_limit_used = 0
        
        # If we're running low on requests, wait
        if self.rate_limit_remaining < 10:
            wait_time = self.rate_limit_reset - current_time
            if wait_time > 0:
                self.logger.warning(
                    f"Rate limit nearly exhausted. Waiting {wait_time:.0f} seconds"
                )
                time.sleep(wait_time + 1)  # Add 1 second buffer
    
    def _update_rate_limit(self, response: requests.Response) -> None:
        """Update rate limit tracking from response headers."""
        try:
            self.rate_limit_remaining = int(
                response.headers.get('X-RateLimit-Remaining', self.rate_limit_remaining)
            )
            self.rate_limit_reset = int(
                response.headers.get('X-RateLimit-Reset', self.rate_limit_reset)
            )
            self.rate_limit_used = int(
                response.headers.get('X-RateLimit-Used', self.rate_limit_used)
            )
            
            self.logger.debug(
                f"Rate limit: {self.rate_limit_remaining} remaining, "
                f"resets at {datetime.fromtimestamp(self.rate_limit_reset, timezone.utc)}"
            )
        except (ValueError, TypeError):
            self.logger.warning("Failed to parse rate limit headers")
    
    def get_issues(
        self, 
        owner: str, 
        repo: str, 
        labels: Optional[List[str]] = None,
        state: str = 'open',
        since: Optional[str] = None
    ) -> List[Dict]:
        """
        Get issues from a repository.
        
        Args:
            owner: Repository owner
            repo: Repository name
            labels: List of labels to filter by
            state: Issue state ('open', 'closed', 'all')
            since: ISO 8601 timestamp to filter issues updated since
            
        Returns:
            List of issue dictionaries
        """
        endpoint = f"/repos/{owner}/{repo}/issues"
        params = {
            'state': state,
            'per_page': 100
        }
        
        if labels:
            params['labels'] = ','.join(labels)
        
        if since:
            params['since'] = since
        
        issues = []
        page = 1
        
        while True:
            params['page'] = page
            response = self._make_request('GET', endpoint, params=params)
            page_issues = response.json()
            
            if not page_issues:
                break
                
            issues.extend(page_issues)
            
            # Check if there are more pages
            if len(page_issues) < params['per_page']:
                break
                
            page += 1
        
        self.logger.info(f"Retrieved {len(issues)} issues from {owner}/{repo}")
        return issues
    
    def get_issue(self, owner: str, repo: str, issue_number: int) -> Dict:
        """
        Get a specific issue.
        
        Args:
            owner: Repository owner
            repo: Repository name
            issue_number: Issue number
            
        Returns:
            Issue dictionary
        """
        endpoint = f"/repos/{owner}/{repo}/issues/{issue_number}"
        response = self._make_request('GET', endpoint)
        return response.json()
    
    def update_issue_labels(
        self, 
        owner: str, 
        repo: str, 
        issue_number: int, 
        labels: List[str]
    ) -> Dict:
        """
        Update issue labels.
        
        Args:
            owner: Repository owner
            repo: Repository name
            issue_number: Issue number
            labels: List of label names
            
        Returns:
            Updated issue dictionary
        """
        endpoint = f"/repos/{owner}/{repo}/issues/{issue_number}"
        data = {'labels': labels}
        response = self._make_request('PATCH', endpoint, json=data)
        return response.json()
    
    def add_issue_comment(
        self, 
        owner: str, 
        repo: str, 
        issue_number: int, 
        body: str
    ) -> Dict:
        """
        Add comment to an issue.
        
        Args:
            owner: Repository owner
            repo: Repository name
            issue_number: Issue number
            body: Comment body
            
        Returns:
            Comment dictionary
        """
        endpoint = f"/repos/{owner}/{repo}/issues/{issue_number}/comments"
        data = {'body': body}
        response = self._make_request('POST', endpoint, json=data)
        return response.json()
    
    def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str,
        draft: bool = False
    ) -> Dict:
        """
        Create a pull request.
        
        Args:
            owner: Repository owner
            repo: Repository name
            title: PR title
            head: Branch to merge from
            base: Branch to merge into
            body: PR description
            draft: Whether to create as draft PR
            
        Returns:
            Pull request dictionary
        """
        endpoint = f"/repos/{owner}/{repo}/pulls"
        data = {
            'title': title,
            'head': head,
            'base': base,
            'body': body,
            'draft': draft
        }
        response = self._make_request('POST', endpoint, json=data)
        return response.json()
    
    def get_repository(self, owner: str, repo: str) -> Dict:
        """
        Get repository information.
        
        Args:
            owner: Repository owner
            repo: Repository name
            
        Returns:
            Repository dictionary
        """
        endpoint = f"/repos/{owner}/{repo}"
        response = self._make_request('GET', endpoint)
        return response.json()
    
    def get_rate_limit_status(self) -> Dict:
        """
        Get current rate limit status.
        
        Returns:
            Dictionary with rate limit information
        """
        endpoint = "/rate_limit"
        response = self._make_request('GET', endpoint)
        return response.json()
    
    def test_authentication(self) -> Tuple[bool, str]:
        """
        Test GitHub authentication.
        
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            endpoint = "/user"
            response = self._make_request('GET', endpoint)
            user_data = response.json()
            username = user_data.get('login', 'unknown')
            return True, f"Authenticated as {username}"
        except GitHubAuthenticationError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Authentication test failed: {e}"
    
    def extract_repo_info(self, repo_url: str) -> Tuple[str, str]:
        """
        Extract owner and repo name from GitHub repository URL.
        
        Args:
            repo_url: GitHub repository URL
            
        Returns:
            Tuple of (owner, repo)
            
        Raises:
            ValueError: If URL is not a valid GitHub repository URL
        """
        import re
        
        # Handle various GitHub URL formats
        patterns = [
            r'https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$',
            r'git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$',
            r'github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$'
        ]
        
        for pattern in patterns:
            match = re.match(pattern, repo_url)
            if match:
                owner, repo = match.groups()
                return owner, repo
        
        raise ValueError(f"Invalid GitHub repository URL: {repo_url}")