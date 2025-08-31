#!/usr/bin/env python3
"""
Branch Naming Conventions for Claude Flow GitHub Integration

Provides standardized branch naming conventions, validation, and generation
following industry best practices and team consistency requirements.
"""

import re
import logging
import string
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass
from enum import Enum


class BranchType(Enum):
    """Standard branch types with conventional prefixes."""
    FEATURE = "feature"
    BUGFIX = "bugfix"
    HOTFIX = "hotfix"
    RELEASE = "release"
    EXPERIMENTAL = "experimental"
    REFACTOR = "refactor"
    DOCS = "docs"
    CHORE = "chore"
    TEST = "test"


@dataclass
class NamingValidationResult:
    """Result of branch name validation."""
    valid: bool
    name: str
    error: Optional[str] = None
    warnings: List[str] = None
    suggestions: List[str] = None


@dataclass
class BranchNamingConfig:
    """Configuration for branch naming conventions."""
    max_length: int = 80
    min_length: int = 8
    separator: str = "/"
    word_separator: str = "-"
    allow_uppercase: bool = False
    allow_underscores: bool = False
    require_issue_prefix: bool = False
    issue_prefix_pattern: str = r"^\d+[-_]"
    forbidden_words: Set[str] = None
    required_prefixes: Dict[BranchType, str] = None
    
    def __post_init__(self):
        if self.forbidden_words is None:
            self.forbidden_words = {
                "test", "tmp", "temp", "debug", "hack", "fix", "wip", "temp"
            }
        
        if self.required_prefixes is None:
            self.required_prefixes = {
                BranchType.FEATURE: "feature/",
                BranchType.BUGFIX: "bugfix/",
                BranchType.HOTFIX: "hotfix/",
                BranchType.RELEASE: "release/",
                BranchType.EXPERIMENTAL: "experimental/",
                BranchType.REFACTOR: "refactor/",
                BranchType.DOCS: "docs/",
                BranchType.CHORE: "chore/",
                BranchType.TEST: "test/"
            }


class BranchNamingValidator:
    """
    Validates branch names against naming conventions.
    
    Enforces consistent, readable, and meaningful branch names following
    established Git flow conventions and team standards.
    """
    
    def __init__(self, config: Optional[BranchNamingConfig] = None):
        """
        Initialize naming validator with configuration.
        
        Args:
            config: Naming configuration (uses defaults if None)
        """
        self.logger = logging.getLogger(__name__)
        self.config = config or BranchNamingConfig()
        
        # Compile regex patterns for efficiency
        self._compile_patterns()
    
    def _compile_patterns(self):
        """Compile regex patterns for validation."""
        # Valid characters pattern
        valid_chars = r"a-z0-9\-_"
        if self.config.allow_uppercase:
            valid_chars += "A-Z"
        
        self.valid_char_pattern = re.compile(f"^[{valid_chars}/]+$")
        
        # Issue prefix pattern
        if self.config.require_issue_prefix:
            self.issue_pattern = re.compile(self.config.issue_prefix_pattern)
        
        # Invalid patterns to avoid
        self.invalid_patterns = [
            re.compile(r"^-|-$"),  # Leading/trailing hyphens
            re.compile(r"^_|_$"),  # Leading/trailing underscores
            re.compile(r"//"),     # Double slashes
            re.compile(r"--"),     # Double hyphens
            re.compile(r"__"),     # Double underscores
            re.compile(r"^\d+$"),  # Only numbers
        ]
    
    def validate(self, branch_name: str) -> NamingValidationResult:
        """
        Validate a branch name against all naming conventions.
        
        Args:
            branch_name: Branch name to validate
            
        Returns:
            NamingValidationResult with validation outcome and details
        """
        warnings = []
        suggestions = []
        
        # Basic validation
        if not branch_name:
            return NamingValidationResult(
                valid=False,
                name=branch_name,
                error="Branch name cannot be empty"
            )
        
        # Length validation
        if len(branch_name) < self.config.min_length:
            return NamingValidationResult(
                valid=False,
                name=branch_name,
                error=f"Branch name too short (minimum {self.config.min_length} characters)"
            )
        
        if len(branch_name) > self.config.max_length:
            return NamingValidationResult(
                valid=False,
                name=branch_name,
                error=f"Branch name too long (maximum {self.config.max_length} characters)"
            )
        
        # Character validation
        if not self.valid_char_pattern.match(branch_name):
            invalid_chars = set(branch_name) - set(string.ascii_lowercase + string.digits + "-_/")
            if not self.config.allow_uppercase:
                invalid_chars -= set(string.ascii_uppercase)
            
            return NamingValidationResult(
                valid=False,
                name=branch_name,
                error=f"Invalid characters found: {', '.join(sorted(invalid_chars))}"
            )
        
        # Pattern validation
        for pattern in self.invalid_patterns:
            if pattern.search(branch_name):
                return NamingValidationResult(
                    valid=False,
                    name=branch_name,
                    error=f"Invalid pattern detected in branch name"
                )
        
        # Forbidden words check
        name_words = self._extract_words(branch_name)
        forbidden_found = [word for word in name_words if word in self.config.forbidden_words]
        if forbidden_found:
            warnings.append(f"Contains discouraged words: {', '.join(forbidden_found)}")
            suggestions.append(f"Consider replacing: {', '.join(forbidden_found)}")
        
        # Prefix validation
        branch_type = self._detect_branch_type(branch_name)
        if branch_type:
            expected_prefix = self.config.required_prefixes.get(branch_type)
            if expected_prefix and not branch_name.startswith(expected_prefix):
                warnings.append(f"Expected prefix '{expected_prefix}' for {branch_type.value} branch")
        else:
            warnings.append("No recognized branch type prefix found")
            suggestions.extend([
                f"Consider using prefixes like: {', '.join(self.config.required_prefixes.values())}"
            ])
        
        # Issue prefix validation
        if self.config.require_issue_prefix:
            name_after_prefix = self._remove_type_prefix(branch_name)
            if not self.issue_pattern.match(name_after_prefix):
                warnings.append("Branch should include issue number at the beginning")
                suggestions.append("Format: <type>/<issue-number>-<description>")
        
        # Descriptive validation
        if self._is_too_generic(branch_name):
            warnings.append("Branch name is too generic")
            suggestions.append("Use more descriptive names that explain the purpose")
        
        # Case consistency check
        if not self.config.allow_uppercase and any(c.isupper() for c in branch_name):
            warnings.append("Consider using lowercase for consistency")
            suggestions.append("Use lowercase with hyphens: my-feature-branch")
        
        return NamingValidationResult(
            valid=True,
            name=branch_name,
            warnings=warnings if warnings else None,
            suggestions=suggestions if suggestions else None
        )
    
    def suggest_improvements(self, branch_name: str) -> List[str]:
        """
        Suggest improvements for a branch name.
        
        Args:
            branch_name: Branch name to improve
            
        Returns:
            List of suggested improvements
        """
        suggestions = []
        
        # Normalize case
        normalized = branch_name.lower()
        if normalized != branch_name:
            suggestions.append(normalized)
        
        # Fix separators
        fixed_separators = re.sub(r'[_\s]+', self.config.word_separator, normalized)
        if fixed_separators != normalized:
            suggestions.append(fixed_separators)
        
        # Remove invalid characters
        cleaned = re.sub(r'[^a-z0-9\-_/]', '', fixed_separators)
        if cleaned != fixed_separators:
            suggestions.append(cleaned)
        
        # Add type prefix if missing
        branch_type = self._detect_branch_type(cleaned)
        if not branch_type and not cleaned.startswith(tuple(self.config.required_prefixes.values())):
            # Suggest most likely type based on words
            suggested_type = self._suggest_branch_type(cleaned)
            if suggested_type:
                prefix = self.config.required_prefixes[suggested_type]
                suggestions.append(f"{prefix}{cleaned}")
        
        return list(dict.fromkeys(suggestions))  # Remove duplicates while preserving order
    
    def _detect_branch_type(self, branch_name: str) -> Optional[BranchType]:
        """Detect branch type from name."""
        for branch_type, prefix in self.config.required_prefixes.items():
            if branch_name.startswith(prefix):
                return branch_type
        return None
    
    def _suggest_branch_type(self, branch_name: str) -> Optional[BranchType]:
        """Suggest branch type based on name content."""
        name_lower = branch_name.lower()
        
        # Keywords for different types
        type_keywords = {
            BranchType.FEATURE: ['feature', 'feat', 'add', 'implement', 'new'],
            BranchType.BUGFIX: ['bug', 'fix', 'issue', 'error', 'broken'],
            BranchType.HOTFIX: ['hotfix', 'urgent', 'critical', 'patch'],
            BranchType.REFACTOR: ['refactor', 'refact', 'cleanup', 'improve'],
            BranchType.DOCS: ['doc', 'docs', 'documentation', 'readme'],
            BranchType.TEST: ['test', 'testing', 'spec', 'unit'],
            BranchType.CHORE: ['chore', 'task', 'maintenance', 'update']
        }
        
        for branch_type, keywords in type_keywords.items():
            if any(keyword in name_lower for keyword in keywords):
                return branch_type
        
        # Default to feature if no specific type detected
        return BranchType.FEATURE
    
    def _remove_type_prefix(self, branch_name: str) -> str:
        """Remove type prefix from branch name."""
        for prefix in self.config.required_prefixes.values():
            if branch_name.startswith(prefix):
                return branch_name[len(prefix):]
        return branch_name
    
    def _extract_words(self, branch_name: str) -> List[str]:
        """Extract words from branch name."""
        # Remove prefixes and split on separators
        name_without_prefix = self._remove_type_prefix(branch_name)
        words = re.split(r'[-_/\s]+', name_without_prefix.lower())
        return [word for word in words if word and word.isalpha()]
    
    def _is_too_generic(self, branch_name: str) -> bool:
        """Check if branch name is too generic."""
        generic_patterns = [
            r'^(feature|bugfix|hotfix)/\d+$',  # Only type and number
            r'^(feature|bugfix|hotfix)/(test|tmp|temp|fix)$',  # Generic words
            r'^(feature|bugfix|hotfix)/update$',  # Too vague
        ]
        
        return any(re.match(pattern, branch_name.lower()) for pattern in generic_patterns)


def generate_branch_name(description: str,
                        branch_type: BranchType = BranchType.FEATURE,
                        issue_number: Optional[int] = None,
                        config: Optional[BranchNamingConfig] = None) -> str:
    """
    Generate a well-formed branch name from description.
    
    Args:
        description: Feature or issue description
        branch_type: Type of branch to create
        issue_number: Optional issue/ticket number
        config: Naming configuration
        
    Returns:
        Generated branch name following conventions
    """
    config = config or BranchNamingConfig()
    
    # Clean and normalize description
    clean_description = _normalize_description(description, config)
    
    # Build branch name components
    components = []
    
    # Add type prefix
    type_prefix = config.required_prefixes.get(branch_type, f"{branch_type.value}/")
    components.append(type_prefix.rstrip('/'))
    
    # Add issue number if provided
    if issue_number:
        components.append(str(issue_number))
    
    # Add description
    components.append(clean_description)
    
    # Join with appropriate separators
    if issue_number:
        # Use word separator between issue number and description
        branch_name = f"{components[0]}{config.separator}{components[1]}{config.word_separator}{components[2]}"
    else:
        branch_name = f"{components[0]}{config.separator}{components[1]}"
    
    # Ensure length constraints
    if len(branch_name) > config.max_length:
        # Truncate description while keeping type and issue number
        available_length = config.max_length - len(components[0]) - len(config.separator)
        if issue_number:
            available_length -= len(str(issue_number)) - len(config.word_separator)
        
        truncated_desc = clean_description[:available_length].rstrip(config.word_separator)
        if issue_number:
            branch_name = f"{components[0]}{config.separator}{components[1]}{config.word_separator}{truncated_desc}"
        else:
            branch_name = f"{components[0]}{config.separator}{truncated_desc}"
    
    return branch_name


def validate_branch_name(branch_name: str,
                        config: Optional[BranchNamingConfig] = None) -> NamingValidationResult:
    """
    Convenience function for validating a branch name.
    
    Args:
        branch_name: Branch name to validate
        config: Naming configuration
        
    Returns:
        NamingValidationResult with validation outcome
    """
    validator = BranchNamingValidator(config)
    return validator.validate(branch_name)


def suggest_branch_name(description: str,
                       existing_names: Optional[List[str]] = None,
                       branch_type: BranchType = BranchType.FEATURE,
                       issue_number: Optional[int] = None) -> List[str]:
    """
    Suggest multiple branch name variations.
    
    Args:
        description: Feature or issue description
        existing_names: List of existing branch names to avoid conflicts
        branch_type: Type of branch
        issue_number: Optional issue number
        
    Returns:
        List of suggested branch names
    """
    existing_names = existing_names or []
    suggestions = []
    
    # Generate primary suggestion
    primary = generate_branch_name(description, branch_type, issue_number)
    if primary not in existing_names:
        suggestions.append(primary)
    
    # Generate variations
    variations = [
        # Different word separations
        _normalize_description(description, BranchNamingConfig(word_separator="-")),
        _normalize_description(description, BranchNamingConfig(word_separator="_")),
        
        # Shortened versions
        _abbreviate_description(description),
        
        # With timestamp for uniqueness
        f"{_normalize_description(description, BranchNamingConfig())}-{datetime.now().strftime('%m%d')}"
    ]
    
    for variation in variations:
        branch_name = generate_branch_name(variation, branch_type, issue_number)
        if branch_name not in existing_names and branch_name not in suggestions:
            suggestions.append(branch_name)
    
    return suggestions[:5]  # Limit to 5 suggestions


def _normalize_description(description: str, config: BranchNamingConfig) -> str:
    """Normalize description for use in branch names."""
    # Convert to lowercase
    normalized = description.lower()
    
    # Remove special characters
    normalized = re.sub(r'[^\w\s-]', '', normalized)
    
    # Replace spaces and underscores with configured separator
    normalized = re.sub(r'[\s_]+', config.word_separator, normalized)
    
    # Remove leading/trailing separators
    normalized = normalized.strip(config.word_separator)
    
    # Collapse multiple separators
    normalized = re.sub(f'{re.escape(config.word_separator)}+', config.word_separator, normalized)
    
    return normalized


def _abbreviate_description(description: str, max_words: int = 3) -> str:
    """Create abbreviated version of description."""
    words = description.split()
    
    if len(words) <= max_words:
        return description
    
    # Take first few words and important words
    important_words = []
    common_words = {'a', 'an', 'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by'}
    
    for word in words:
        if word.lower() not in common_words:
            important_words.append(word)
            if len(important_words) >= max_words:
                break
    
    return ' '.join(important_words[:max_words])


def main():
    """Command-line interface for testing branch naming utilities."""
    import argparse
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    parser = argparse.ArgumentParser(description='Branch Naming Utilities')
    parser.add_argument('command', choices=['validate', 'generate', 'suggest'], 
                       help='Command to run')
    parser.add_argument('--name', help='Branch name to validate')
    parser.add_argument('--description', help='Description for branch generation')
    parser.add_argument('--type', choices=[t.value for t in BranchType],
                       default='feature', help='Branch type')
    parser.add_argument('--issue', type=int, help='Issue number')
    
    args = parser.parse_args()
    
    try:
        if args.command == 'validate':
            if not args.name:
                print("--name is required for validate command")
                sys.exit(1)
                
            result = validate_branch_name(args.name)
            
            if result.valid:
                print(f"✅ Branch name '{result.name}' is valid")
                if result.warnings:
                    print("Warnings:")
                    for warning in result.warnings:
                        print(f"  ⚠️  {warning}")
                if result.suggestions:
                    print("Suggestions:")
                    for suggestion in result.suggestions:
                        print(f"  💡 {suggestion}")
            else:
                print(f"❌ Branch name '{result.name}' is invalid")
                print(f"Error: {result.error}")
        
        elif args.command == 'generate':
            if not args.description:
                print("--description is required for generate command")
                sys.exit(1)
                
            branch_type = BranchType(args.type)
            branch_name = generate_branch_name(
                args.description,
                branch_type,
                args.issue
            )
            
            print(f"Generated branch name: {branch_name}")
            
            # Validate the generated name
            result = validate_branch_name(branch_name)
            if result.warnings:
                print("Note: Generated name has warnings:")
                for warning in result.warnings:
                    print(f"  ⚠️  {warning}")
        
        elif args.command == 'suggest':
            if not args.description:
                print("--description is required for suggest command")
                sys.exit(1)
                
            branch_type = BranchType(args.type)
            suggestions = suggest_branch_name(
                args.description,
                branch_type=branch_type,
                issue_number=args.issue
            )
            
            print(f"Branch name suggestions for '{args.description}':")
            for i, suggestion in enumerate(suggestions, 1):
                print(f"  {i}. {suggestion}")
    
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()