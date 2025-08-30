#!/usr/bin/env python3
"""
PR Reviewer Manager for Claude Flow

Provides intelligent reviewer assignment based on:
- Code ownership patterns and CODEOWNERS file
- Historical review patterns and expertise
- Team structure and availability 
- File change analysis and domain expertise
- Workload balancing across team members
"""

import json
import logging
import os
import re
import subprocess
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass
from enum import Enum

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.config_manager import ConfigManager


class ExpertiseLevel(Enum):
    """Levels of expertise for code areas."""
    EXPERT = "expert"
    EXPERIENCED = "experienced"
    FAMILIAR = "familiar"
    LEARNING = "learning"
    UNKNOWN = "unknown"


class ReviewerRole(Enum):
    """Roles reviewers can have."""
    OWNER = "owner"           # Code owner for the area
    MAINTAINER = "maintainer" # General maintainer
    EXPERT = "expert"         # Domain expert
    PEER = "peer"            # Peer reviewer
    JUNIOR = "junior"        # Junior developer (learning)


@dataclass
class ReviewerInfo:
    """Information about a potential reviewer."""
    username: str
    role: ReviewerRole
    expertise_level: ExpertiseLevel
    expertise_areas: List[str]
    current_pr_load: int
    last_review_date: Optional[str]
    average_review_time: float  # hours
    availability_score: float  # 0.0 - 1.0
    
    def get_score(self, file_patterns: List[str], priority_multiplier: float = 1.0) -> float:
        """Calculate reviewer suitability score."""
        base_score = 0.0
        
        # Expertise scoring
        if self.expertise_level == ExpertiseLevel.EXPERT:
            base_score += 10.0
        elif self.expertise_level == ExpertiseLevel.EXPERIENCED:
            base_score += 7.0
        elif self.expertise_level == ExpertiseLevel.FAMILIAR:
            base_score += 4.0
        
        # Role scoring
        if self.role == ReviewerRole.OWNER:
            base_score += 8.0
        elif self.role == ReviewerRole.MAINTAINER:
            base_score += 6.0
        elif self.role == ReviewerRole.EXPERT:
            base_score += 5.0
        elif self.role == ReviewerRole.PEER:
            base_score += 3.0
        
        # Pattern matching bonus
        pattern_match_bonus = 0.0
        for pattern in file_patterns:
            for area in self.expertise_areas:
                if area.lower() in pattern.lower():
                    pattern_match_bonus += 2.0
                    break
        
        # Availability penalty
        availability_penalty = (1.0 - self.availability_score) * 5.0
        
        # Current workload penalty  
        workload_penalty = min(self.current_pr_load * 1.5, 10.0)
        
        # Apply multipliers and penalties
        final_score = (base_score + pattern_match_bonus) * priority_multiplier
        final_score -= (availability_penalty + workload_penalty)
        
        return max(0.0, final_score)


@dataclass
class CodeOwnership:
    """Code ownership information."""
    patterns: List[str]
    owners: List[str]
    required_reviews: int = 1
    auto_assign: bool = True
    description: Optional[str] = None


@dataclass
class ReviewAssignment:
    """Review assignment recommendation."""
    reviewer: str
    score: float
    reason: str
    role: ReviewerRole
    confidence: float


class CodeOwnershipManager:
    """
    Manages code ownership patterns and rules.
    
    Parses CODEOWNERS files and custom ownership configurations
    to determine who should review changes to specific files.
    """
    
    def __init__(self, workspace_path: Path):
        """
        Initialize code ownership manager.
        
        Args:
            workspace_path: Working directory to search for CODEOWNERS
        """
        self.logger = logging.getLogger(__name__)
        self.workspace_path = workspace_path
        
        # Possible locations for CODEOWNERS file
        self.codeowners_paths = [
            workspace_path / "CODEOWNERS",
            workspace_path / ".github" / "CODEOWNERS", 
            workspace_path / "docs" / "CODEOWNERS"
        ]
        
        # Cache for parsed ownership rules
        self._ownership_cache: Optional[List[CodeOwnership]] = None
        
        self.logger.info("Code Ownership Manager initialized")
    
    def get_owners_for_files(self, file_paths: List[str]) -> Dict[str, List[str]]:
        """
        Get owners for specific file paths.
        
        Args:
            file_paths: List of file paths to check ownership for
            
        Returns:
            Dictionary mapping file paths to list of owners
        """
        ownership_rules = self._parse_codeowners()
        file_owners = {}
        
        for file_path in file_paths:
            owners = []
            
            # Check each ownership rule (in reverse order for precedence)
            for rule in reversed(ownership_rules):
                if self._matches_pattern(file_path, rule.patterns):
                    owners.extend(rule.owners)
                    break  # First match wins (highest precedence)
            
            file_owners[file_path] = list(set(owners))  # Remove duplicates
        
        return file_owners
    
    def get_all_owners(self) -> Set[str]:
        """Get set of all code owners."""
        ownership_rules = self._parse_codeowners()
        all_owners = set()
        
        for rule in ownership_rules:
            all_owners.update(rule.owners)
        
        return all_owners
    
    def get_ownership_areas(self, owner: str) -> List[str]:
        """Get areas of ownership for a specific owner."""
        ownership_rules = self._parse_codeowners()
        areas = []
        
        for rule in ownership_rules:
            if owner in rule.owners:
                areas.extend(rule.patterns)
        
        return areas
    
    def _parse_codeowners(self) -> List[CodeOwnership]:
        """Parse CODEOWNERS file and return ownership rules."""
        if self._ownership_cache is not None:
            return self._ownership_cache
        
        ownership_rules = []
        
        # Find CODEOWNERS file
        codeowners_file = None
        for path in self.codeowners_paths:
            if path.exists():
                codeowners_file = path
                break
        
        if not codeowners_file:
            self.logger.info("No CODEOWNERS file found")
            self._ownership_cache = []
            return []
        
        try:
            with open(codeowners_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            for line_num, line in enumerate(lines, 1):
                line = line.strip()
                
                # Skip comments and empty lines
                if not line or line.startswith('#'):
                    continue
                
                # Parse ownership line
                parts = line.split()
                if len(parts) < 2:
                    continue
                
                pattern = parts[0]
                owners = [owner.lstrip('@') for owner in parts[1:]]
                
                # Create ownership rule
                ownership = CodeOwnership(
                    patterns=[pattern],
                    owners=owners,
                    description=f"Line {line_num} in CODEOWNERS"
                )
                
                ownership_rules.append(ownership)
                
        except Exception as e:
            self.logger.error(f"Failed to parse CODEOWNERS file: {e}")
        
        self._ownership_cache = ownership_rules
        return ownership_rules
    
    def _matches_pattern(self, file_path: str, patterns: List[str]) -> bool:
        """Check if file path matches any of the patterns."""
        for pattern in patterns:
            if self._match_codeowners_pattern(file_path, pattern):
                return True
        return False
    
    def _match_codeowners_pattern(self, file_path: str, pattern: str) -> bool:
        """Match file path against CODEOWNERS pattern."""
        # Convert CODEOWNERS pattern to regex
        # This is a simplified implementation
        
        # Handle exact matches
        if pattern == file_path:
            return True
        
        # Handle directory patterns
        if pattern.endswith('/'):
            return file_path.startswith(pattern) or file_path.startswith(pattern[:-1])
        
        # Handle wildcards
        if '*' in pattern:
            # Convert to regex pattern
            regex_pattern = pattern.replace('*', '.*')
            regex_pattern = f"^{regex_pattern}$"
            return bool(re.match(regex_pattern, file_path))
        
        # Handle prefix matches
        return file_path.startswith(pattern)


class PRReviewerManager:
    """
    Intelligent reviewer assignment system for pull requests.
    
    Uses multiple data sources and algorithms to suggest the most
    appropriate reviewers for a given set of changes.
    """
    
    def __init__(self, 
                 config: Optional[ConfigManager] = None,
                 workspace_path: Optional[Path] = None):
        """
        Initialize reviewer manager.
        
        Args:
            config: Configuration manager instance
            workspace_path: Working directory for git operations
        """
        self.logger = logging.getLogger(__name__)
        self.config = config or ConfigManager()
        self.workspace_path = workspace_path or Path.cwd()
        
        # Initialize code ownership manager
        self.ownership_manager = CodeOwnershipManager(self.workspace_path)
        
        # Configuration
        self.min_reviewers = self.config.get("github.min_reviewers", 1)
        self.max_reviewers = self.config.get("github.max_reviewers", 3)
        self.require_owner_review = self.config.get("github.require_owner_review", True)
        self.balance_workload = self.config.get("github.balance_reviewer_workload", True)
        
        # Team configuration
        self.team_config = self.config.get("github.team_config", {})
        self.reviewer_config = self.config.get("github.reviewers", {})
        
        # Cache for reviewer information
        self._reviewer_cache: Dict[str, ReviewerInfo] = {}
        
        self.logger.info("PR Reviewer Manager initialized")
    
    def suggest_reviewers(self, 
                         file_paths: List[str], 
                         branch_name: str,
                         pr_author: Optional[str] = None,
                         exclude_users: Optional[List[str]] = None) -> List[str]:
        """
        Suggest reviewers for a set of file changes.
        
        Args:
            file_paths: List of files that were changed
            branch_name: Name of the branch (for context)
            pr_author: Author of the PR (to exclude from suggestions)
            exclude_users: Additional users to exclude from suggestions
            
        Returns:
            List of suggested reviewer usernames
        """
        try:
            self.logger.info(f"Suggesting reviewers for {len(file_paths)} files")
            
            exclude_users = exclude_users or []
            if pr_author:
                exclude_users.append(pr_author)
            
            # Get code owners for changed files
            file_owners = self.ownership_manager.get_owners_for_files(file_paths)
            
            # Collect all potential reviewers
            potential_reviewers = self._collect_potential_reviewers(
                file_paths, file_owners, branch_name
            )
            
            # Filter out excluded users
            filtered_reviewers = [
                r for r in potential_reviewers 
                if r.username not in exclude_users
            ]
            
            if not filtered_reviewers:
                self.logger.warning("No eligible reviewers found")
                return []
            
            # Score and rank reviewers
            scored_assignments = self._score_reviewers(filtered_reviewers, file_paths)
            
            # Select final reviewers
            selected_reviewers = self._select_final_reviewers(
                scored_assignments, file_owners
            )
            
            reviewer_names = [assignment.reviewer for assignment in selected_reviewers]
            self.logger.info(f"Suggested reviewers: {', '.join(reviewer_names)}")
            
            return reviewer_names
            
        except Exception as e:
            self.logger.error(f"Failed to suggest reviewers: {e}")
            return []
    
    def get_reviewer_workload(self, username: str) -> int:
        """Get current PR review workload for a user."""
        try:
            # This would normally query GitHub API or database
            # For now, return from config or estimate
            workloads = self.config.get("github.reviewer_workloads", {})
            return workloads.get(username, 2)  # Default moderate workload
            
        except Exception as e:
            self.logger.error(f"Failed to get workload for {username}: {e}")
            return 5  # Conservative estimate
    
    def update_reviewer_activity(self, username: str, activity_data: Dict[str, Any]):
        """Update reviewer activity information."""
        try:
            # This would normally update a database or cache
            # For now, just log the activity
            self.logger.info(f"Updated activity for {username}: {activity_data}")
            
        except Exception as e:
            self.logger.error(f"Failed to update activity for {username}: {e}")
    
    def _collect_potential_reviewers(self, 
                                   file_paths: List[str],
                                   file_owners: Dict[str, List[str]], 
                                   branch_name: str) -> List[ReviewerInfo]:
        """Collect all potential reviewers from various sources."""
        potential_reviewers = {}  # Use dict to avoid duplicates
        
        # 1. Code owners
        all_owners = set()
        for owners in file_owners.values():
            all_owners.update(owners)
        
        for owner in all_owners:
            reviewer_info = self._get_reviewer_info(owner, ReviewerRole.OWNER, file_paths)
            potential_reviewers[owner] = reviewer_info
        
        # 2. Team members from config
        team_members = self.team_config.get("members", [])
        for member in team_members:
            if isinstance(member, str):
                username = member
                role = ReviewerRole.PEER
            else:
                username = member.get("username", "")
                role = ReviewerRole(member.get("role", "peer"))
            
            if username and username not in potential_reviewers:
                reviewer_info = self._get_reviewer_info(username, role, file_paths)
                potential_reviewers[username] = reviewer_info
        
        # 3. Explicit reviewer configurations
        for username, config in self.reviewer_config.items():
            if username not in potential_reviewers:
                role = ReviewerRole(config.get("role", "peer"))
                reviewer_info = self._get_reviewer_info(username, role, file_paths)
                
                # Override with config data
                if "expertise_areas" in config:
                    reviewer_info.expertise_areas = config["expertise_areas"]
                if "expertise_level" in config:
                    reviewer_info.expertise_level = ExpertiseLevel(config["expertise_level"])
                
                potential_reviewers[username] = reviewer_info
        
        # 4. Historical reviewers (if we had access to git history)
        historical_reviewers = self._get_historical_reviewers(file_paths)
        for username in historical_reviewers:
            if username not in potential_reviewers:
                reviewer_info = self._get_reviewer_info(username, ReviewerRole.PEER, file_paths)
                potential_reviewers[username] = reviewer_info
        
        return list(potential_reviewers.values())
    
    def _get_reviewer_info(self, 
                          username: str, 
                          role: ReviewerRole, 
                          file_paths: List[str]) -> ReviewerInfo:
        """Get or create reviewer information."""
        if username in self._reviewer_cache:
            cached_info = self._reviewer_cache[username]
            # Update role if this one has higher priority
            if role.value in ["owner", "maintainer"] and cached_info.role.value in ["peer", "junior"]:
                cached_info.role = role
            return cached_info
        
        # Determine expertise level
        expertise_level = self._determine_expertise_level(username, file_paths)
        
        # Get expertise areas
        expertise_areas = self._get_expertise_areas(username, file_paths)
        
        # Get current workload
        current_workload = self.get_reviewer_workload(username)
        
        # Create reviewer info
        reviewer_info = ReviewerInfo(
            username=username,
            role=role,
            expertise_level=expertise_level,
            expertise_areas=expertise_areas,
            current_pr_load=current_workload,
            last_review_date=None,  # Would be fetched from API/DB
            average_review_time=24.0,  # Default 24 hours
            availability_score=self._calculate_availability_score(username)
        )
        
        # Cache the reviewer info
        self._reviewer_cache[username] = reviewer_info
        
        return reviewer_info
    
    def _determine_expertise_level(self, username: str, file_paths: List[str]) -> ExpertiseLevel:
        """Determine expertise level for a reviewer."""
        # Check explicit configuration
        reviewer_config = self.reviewer_config.get(username, {})
        if "expertise_level" in reviewer_config:
            return ExpertiseLevel(reviewer_config["expertise_level"])
        
        # Check if they're code owners (implies expertise)
        ownership_areas = self.ownership_manager.get_ownership_areas(username)
        if ownership_areas:
            return ExpertiseLevel.EXPERT
        
        # Default based on role in team config
        team_members = self.team_config.get("members", [])
        for member in team_members:
            if isinstance(member, dict) and member.get("username") == username:
                role = member.get("role", "peer")
                if role in ["owner", "maintainer"]:
                    return ExpertiseLevel.EXPERIENCED
                elif role == "expert":
                    return ExpertiseLevel.EXPERT
        
        return ExpertiseLevel.FAMILIAR
    
    def _get_expertise_areas(self, username: str, file_paths: List[str]) -> List[str]:
        """Get expertise areas for a reviewer."""
        areas = []
        
        # From explicit configuration
        reviewer_config = self.reviewer_config.get(username, {})
        if "expertise_areas" in reviewer_config:
            areas.extend(reviewer_config["expertise_areas"])
        
        # From code ownership
        ownership_areas = self.ownership_manager.get_ownership_areas(username)
        areas.extend(ownership_areas)
        
        # Infer from file paths if no explicit areas
        if not areas:
            areas = self._infer_expertise_from_files(file_paths)
        
        return list(set(areas))  # Remove duplicates
    
    def _calculate_availability_score(self, username: str) -> float:
        """Calculate availability score for a reviewer."""
        # This would normally check calendar, time zones, etc.
        # For now, return a score based on current workload
        
        workload = self.get_reviewer_workload(username)
        
        if workload == 0:
            return 1.0
        elif workload <= 2:
            return 0.8
        elif workload <= 4:
            return 0.6
        elif workload <= 6:
            return 0.4
        else:
            return 0.2
    
    def _get_historical_reviewers(self, file_paths: List[str]) -> List[str]:
        """Get historical reviewers for similar files."""
        try:
            # This would normally analyze git history for review patterns
            # For now, return empty list
            return []
            
        except Exception as e:
            self.logger.error(f"Failed to get historical reviewers: {e}")
            return []
    
    def _score_reviewers(self, 
                        reviewers: List[ReviewerInfo], 
                        file_paths: List[str]) -> List[ReviewAssignment]:
        """Score all reviewers and create assignments."""
        assignments = []
        
        # Determine priority multiplier based on file types
        priority_multiplier = self._get_priority_multiplier(file_paths)
        
        for reviewer in reviewers:
            score = reviewer.get_score(file_paths, priority_multiplier)
            
            # Generate reason
            reason = self._generate_assignment_reason(reviewer, file_paths)
            
            # Calculate confidence
            confidence = self._calculate_confidence(reviewer, score)
            
            assignment = ReviewAssignment(
                reviewer=reviewer.username,
                score=score,
                reason=reason,
                role=reviewer.role,
                confidence=confidence
            )
            
            assignments.append(assignment)
        
        # Sort by score (highest first)
        assignments.sort(key=lambda a: a.score, reverse=True)
        
        return assignments
    
    def _select_final_reviewers(self, 
                               scored_assignments: List[ReviewAssignment],
                               file_owners: Dict[str, List[str]]) -> List[ReviewAssignment]:
        """Select final reviewers from scored assignments."""
        selected = []
        all_owners = set()
        
        for owners in file_owners.values():
            all_owners.update(owners)
        
        # First, ensure we have at least one owner if required
        if self.require_owner_review and all_owners:
            for assignment in scored_assignments:
                if (assignment.reviewer in all_owners and 
                    assignment.role == ReviewerRole.OWNER):
                    selected.append(assignment)
                    break
        
        # Then add additional reviewers based on score
        for assignment in scored_assignments:
            if assignment in selected:
                continue
                
            if len(selected) >= self.max_reviewers:
                break
                
            # Skip if confidence is too low
            if assignment.confidence < 0.3:
                continue
            
            selected.append(assignment)
        
        # Ensure minimum reviewers
        if len(selected) < self.min_reviewers:
            remaining = scored_assignments[len(selected):]
            needed = self.min_reviewers - len(selected)
            
            for assignment in remaining[:needed]:
                if assignment not in selected:
                    selected.append(assignment)
        
        return selected[:self.max_reviewers]
    
    def _get_priority_multiplier(self, file_paths: List[str]) -> float:
        """Get priority multiplier based on file criticality."""
        high_priority_patterns = [
            'security', 'auth', 'config', 'database', 'migration',
            'api', 'service', 'critical', 'prod', 'deploy'
        ]
        
        for file_path in file_paths:
            file_lower = file_path.lower()
            if any(pattern in file_lower for pattern in high_priority_patterns):
                return 1.5
        
        return 1.0
    
    def _generate_assignment_reason(self, 
                                  reviewer: ReviewerInfo, 
                                  file_paths: List[str]) -> str:
        """Generate human-readable reason for assignment."""
        reasons = []
        
        if reviewer.role == ReviewerRole.OWNER:
            reasons.append("Code owner")
        elif reviewer.role == ReviewerRole.MAINTAINER:
            reasons.append("Project maintainer")
        elif reviewer.role == ReviewerRole.EXPERT:
            reasons.append("Domain expert")
        
        if reviewer.expertise_level == ExpertiseLevel.EXPERT:
            reasons.append("Expert-level knowledge")
        elif reviewer.expertise_level == ExpertiseLevel.EXPERIENCED:
            reasons.append("Experienced with codebase")
        
        if reviewer.expertise_areas:
            matching_areas = []
            for area in reviewer.expertise_areas:
                for file_path in file_paths:
                    if area.lower() in file_path.lower():
                        matching_areas.append(area)
                        break
            
            if matching_areas:
                reasons.append(f"Expertise in {', '.join(matching_areas[:2])}")
        
        if reviewer.current_pr_load <= 1:
            reasons.append("Low current workload")
        
        return "; ".join(reasons) if reasons else "General reviewer"
    
    def _calculate_confidence(self, reviewer: ReviewerInfo, score: float) -> float:
        """Calculate confidence in the reviewer assignment."""
        # Normalize score to 0-1 range (assuming max score around 20)
        normalized_score = min(score / 20.0, 1.0)
        
        # Factor in role confidence
        role_confidence = {
            ReviewerRole.OWNER: 0.9,
            ReviewerRole.MAINTAINER: 0.8,
            ReviewerRole.EXPERT: 0.8,
            ReviewerRole.PEER: 0.6,
            ReviewerRole.JUNIOR: 0.4
        }.get(reviewer.role, 0.5)
        
        # Factor in expertise confidence
        expertise_confidence = {
            ExpertiseLevel.EXPERT: 0.9,
            ExpertiseLevel.EXPERIENCED: 0.7,
            ExpertiseLevel.FAMILIAR: 0.5,
            ExpertiseLevel.LEARNING: 0.3,
            ExpertiseLevel.UNKNOWN: 0.2
        }.get(reviewer.expertise_level, 0.3)
        
        # Combine factors
        final_confidence = (
            normalized_score * 0.4 + 
            role_confidence * 0.3 + 
            expertise_confidence * 0.3
        )
        
        return min(final_confidence, 1.0)
    
    def _infer_expertise_from_files(self, file_paths: List[str]) -> List[str]:
        """Infer expertise areas from file paths."""
        areas = set()
        
        for file_path in file_paths:
            path_parts = file_path.lower().split('/')
            
            # Common area patterns
            if any('frontend' in part or 'ui' in part or 'client' in part for part in path_parts):
                areas.add('frontend')
            
            if any('backend' in part or 'api' in part or 'server' in part for part in path_parts):
                areas.add('backend')
            
            if any('database' in part or 'db' in part or 'migration' in part for part in path_parts):
                areas.add('database')
            
            if any('test' in part or 'spec' in part for part in path_parts):
                areas.add('testing')
            
            if any('doc' in part for part in path_parts):
                areas.add('documentation')
            
            if any('config' in part or 'settings' in part for part in path_parts):
                areas.add('configuration')
            
            # Add top-level directories as areas
            if path_parts:
                areas.add(path_parts[0])
        
        return list(areas)


def main():
    """Command-line interface for reviewer manager operations."""
    import argparse
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    parser = argparse.ArgumentParser(description='PR Reviewer Manager for Claude Flow')
    parser.add_argument('--config', help='Path to configuration file')
    parser.add_argument('--workspace', help='Workspace directory')
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Suggest reviewers command
    suggest_parser = subparsers.add_parser('suggest', help='Suggest reviewers for files')
    suggest_parser.add_argument('files', nargs='+', help='File paths to analyze')
    suggest_parser.add_argument('--branch', help='Branch name for context')
    suggest_parser.add_argument('--author', help='PR author to exclude')
    suggest_parser.add_argument('--exclude', nargs='+', help='Users to exclude')
    
    # Show owners command
    owners_parser = subparsers.add_parser('owners', help='Show code owners for files')
    owners_parser.add_argument('files', nargs='+', help='File paths to check')
    
    # Show reviewer info command
    info_parser = subparsers.add_parser('info', help='Show reviewer information')
    info_parser.add_argument('username', help='Username to show info for')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    try:
        config = ConfigManager(args.config) if args.config else ConfigManager()
        workspace = Path(args.workspace) if args.workspace else Path.cwd()
        manager = PRReviewerManager(config, workspace)
        
        if args.command == 'suggest':
            reviewers = manager.suggest_reviewers(
                file_paths=args.files,
                branch_name=args.branch or 'unknown',
                pr_author=args.author,
                exclude_users=args.exclude
            )
            
            print(f"Suggested reviewers for {len(args.files)} files:")
            for i, reviewer in enumerate(reviewers, 1):
                print(f"  {i}. @{reviewer}")
        
        elif args.command == 'owners':
            file_owners = manager.ownership_manager.get_owners_for_files(args.files)
            
            print("Code ownership:")
            for file_path, owners in file_owners.items():
                if owners:
                    print(f"  {file_path}: {', '.join(f'@{owner}' for owner in owners)}")
                else:
                    print(f"  {file_path}: No owners")
        
        elif args.command == 'info':
            # This would show detailed reviewer info
            # For now, just show basic info
            reviewer_info = manager._get_reviewer_info(
                args.username, 
                ReviewerRole.PEER, 
                ['example.py']
            )
            
            print(f"Reviewer: @{reviewer_info.username}")
            print(f"Role: {reviewer_info.role.value}")
            print(f"Expertise Level: {reviewer_info.expertise_level.value}")
            print(f"Expertise Areas: {', '.join(reviewer_info.expertise_areas) if reviewer_info.expertise_areas else 'None specified'}")
            print(f"Current PR Load: {reviewer_info.current_pr_load}")
            print(f"Availability Score: {reviewer_info.availability_score:.2f}")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()