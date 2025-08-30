#!/usr/bin/env python3
"""
PR Template Manager for Claude Flow

Provides dynamic PR template handling and population including:
- Template discovery and loading from various locations
- Template selection based on change type and branch patterns
- Dynamic template population with context variables
- Support for multiple template formats (Markdown, Jinja2)
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass
from enum import Enum

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.config_manager import ConfigManager


class TemplateFormat(Enum):
    """Supported template formats."""
    MARKDOWN = "markdown"
    JINJA2 = "jinja2"
    PLAIN = "plain"


class TemplateType(Enum):
    """Types of PR templates."""
    DEFAULT = "default"
    FEATURE = "feature" 
    BUGFIX = "bugfix"
    HOTFIX = "hotfix"
    REFACTOR = "refactor"
    DOCS = "docs"
    TESTS = "tests"
    CI = "ci"
    CHORE = "chore"
    RELEASE = "release"


@dataclass
class PRTemplate:
    """PR template definition."""
    name: str
    type: TemplateType
    format: TemplateFormat
    content: str
    variables: Dict[str, Any]
    conditions: Dict[str, Any]  # Conditions for template selection
    priority: int = 0  # Higher priority templates are preferred
    file_path: Optional[Path] = None


@dataclass  
class TemplateContext:
    """Context variables for template population."""
    # PR metadata
    title: str
    branch_name: str
    base_branch: str
    change_type: str
    
    # Change statistics
    files_changed: int
    commits_count: int
    lines_added: int
    lines_removed: int
    
    # Analysis results
    risk_level: str
    breaking_changes: bool
    affects_api: bool
    affects_tests: bool
    affects_docs: bool
    
    # Repository context
    repo_name: str
    author: str
    
    # Time context
    created_at: str
    
    # Custom variables
    custom_vars: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for template rendering."""
        result = {
            'title': self.title,
            'branch_name': self.branch_name,
            'base_branch': self.base_branch,
            'change_type': self.change_type,
            'files_changed': self.files_changed,
            'commits_count': self.commits_count,
            'lines_added': self.lines_added,
            'lines_removed': self.lines_removed,
            'risk_level': self.risk_level,
            'breaking_changes': self.breaking_changes,
            'affects_api': self.affects_api,
            'affects_tests': self.affects_tests,
            'affects_docs': self.affects_docs,
            'repo_name': self.repo_name,
            'author': self.author,
            'created_at': self.created_at,
        }
        result.update(self.custom_vars)
        return result


class PRTemplateManager:
    """
    Manages PR templates for automated description generation.
    
    Features:
    - Template discovery from multiple locations
    - Dynamic template selection based on change patterns
    - Template population with analysis context
    - Support for conditional template logic
    """
    
    def __init__(self, 
                 config: Optional[ConfigManager] = None,
                 workspace_path: Optional[Path] = None):
        """
        Initialize template manager.
        
        Args:
            config: Configuration manager instance
            workspace_path: Working directory for template discovery
        """
        self.logger = logging.getLogger(__name__)
        self.config = config or ConfigManager()
        self.workspace_path = workspace_path or Path.cwd()
        
        # Template locations (in order of priority)
        self.template_locations = [
            self.workspace_path / ".github" / "pull_request_templates",
            self.workspace_path / ".github" / "PULL_REQUEST_TEMPLATE",
            self.workspace_path / ".github",
            self.workspace_path / "templates" / "pr",
            self.workspace_path / ".claude" / "templates" / "pr"
        ]
        
        # Configuration
        self.default_template_type = TemplateType(
            self.config.get("github.default_template_type", "default")
        )
        self.enable_jinja2 = self.config.get("github.enable_jinja2_templates", True)
        
        # Cache for loaded templates
        self._template_cache: Dict[str, PRTemplate] = {}
        self._cache_loaded = False
        
        self.logger.info("PR Template Manager initialized")
    
    def discover_templates(self) -> List[PRTemplate]:
        """
        Discover and load all available PR templates.
        
        Returns:
            List of discovered templates
        """
        if self._cache_loaded:
            return list(self._template_cache.values())
        
        templates = []
        
        for location in self.template_locations:
            if location.exists() and location.is_dir():
                templates.extend(self._load_templates_from_directory(location))
            elif location.exists() and location.is_file():
                template = self._load_template_file(location)
                if template:
                    templates.append(template)
        
        # Sort by priority (higher first)
        templates.sort(key=lambda t: t.priority, reverse=True)
        
        # Cache templates
        for template in templates:
            self._template_cache[template.name] = template
        
        self._cache_loaded = True
        
        self.logger.info(f"Discovered {len(templates)} PR templates")
        return templates
    
    def get_template_for_change_type(self, 
                                   change_type: str, 
                                   branch_name: Optional[str] = None,
                                   additional_context: Optional[Dict[str, Any]] = None) -> Optional[PRTemplate]:
        """
        Get the most appropriate template for a change type.
        
        Args:
            change_type: Type of change (feature, bugfix, etc.)
            branch_name: Name of the branch (for pattern matching)
            additional_context: Additional context for template selection
            
        Returns:
            Best matching template or None
        """
        templates = self.discover_templates()
        
        if not templates:
            return None
        
        # Try to find exact match first
        change_type_enum = self._parse_change_type(change_type)
        exact_matches = [t for t in templates if t.type == change_type_enum]
        
        if exact_matches:
            # Apply condition filtering
            filtered = self._filter_by_conditions(
                exact_matches, 
                branch_name, 
                additional_context or {}
            )
            if filtered:
                return filtered[0]  # Return highest priority match
        
        # Fall back to default template
        default_templates = [t for t in templates if t.type == TemplateType.DEFAULT]
        if default_templates:
            filtered = self._filter_by_conditions(
                default_templates, 
                branch_name, 
                additional_context or {}
            )
            if filtered:
                return filtered[0]
        
        # Return first available template as last resort
        return templates[0] if templates else None
    
    def populate_template(self, 
                         template: PRTemplate, 
                         context: Union[Dict[str, Any], TemplateContext]) -> str:
        """
        Populate template with context variables.
        
        Args:
            template: Template to populate
            context: Context variables for population
            
        Returns:
            Populated template content
        """
        try:
            # Convert context to dict if needed
            if isinstance(context, TemplateContext):
                context_dict = context.to_dict()
            else:
                context_dict = context
            
            # Add template variables
            context_dict.update(template.variables)
            
            # Populate based on format
            if template.format == TemplateFormat.JINJA2 and self.enable_jinja2:
                return self._populate_jinja2_template(template.content, context_dict)
            elif template.format == TemplateFormat.MARKDOWN:
                return self._populate_markdown_template(template.content, context_dict)
            else:
                return self._populate_plain_template(template.content, context_dict)
                
        except Exception as e:
            self.logger.error(f"Failed to populate template '{template.name}': {e}")
            return template.content  # Return unpopulated content as fallback
    
    def create_template_context(self, 
                              title: str,
                              branch_name: str, 
                              base_branch: str = "main",
                              **kwargs) -> TemplateContext:
        """
        Create template context from basic parameters.
        
        Args:
            title: PR title
            branch_name: Source branch name
            base_branch: Target branch name
            **kwargs: Additional context variables
            
        Returns:
            TemplateContext instance
        """
        return TemplateContext(
            title=title,
            branch_name=branch_name,
            base_branch=base_branch,
            change_type=kwargs.get('change_type', 'feature'),
            files_changed=kwargs.get('files_changed', 0),
            commits_count=kwargs.get('commits_count', 0),
            lines_added=kwargs.get('lines_added', 0),
            lines_removed=kwargs.get('lines_removed', 0),
            risk_level=kwargs.get('risk_level', 'low'),
            breaking_changes=kwargs.get('breaking_changes', False),
            affects_api=kwargs.get('affects_api', False),
            affects_tests=kwargs.get('affects_tests', False),
            affects_docs=kwargs.get('affects_docs', False),
            repo_name=kwargs.get('repo_name', self.workspace_path.name),
            author=kwargs.get('author', 'Unknown'),
            created_at=kwargs.get('created_at', datetime.now(timezone.utc).isoformat()),
            custom_vars=kwargs.get('custom_vars', {})
        )
    
    def create_default_template(self, template_type: TemplateType) -> PRTemplate:
        """
        Create a default template for a given type.
        
        Args:
            template_type: Type of template to create
            
        Returns:
            Default template instance
        """
        templates = {
            TemplateType.FEATURE: self._create_feature_template(),
            TemplateType.BUGFIX: self._create_bugfix_template(),
            TemplateType.HOTFIX: self._create_hotfix_template(),
            TemplateType.REFACTOR: self._create_refactor_template(),
            TemplateType.DOCS: self._create_docs_template(),
            TemplateType.TESTS: self._create_tests_template(),
            TemplateType.CI: self._create_ci_template(),
            TemplateType.CHORE: self._create_chore_template(),
            TemplateType.RELEASE: self._create_release_template(),
            TemplateType.DEFAULT: self._create_default_template_content()
        }
        
        return templates.get(template_type, templates[TemplateType.DEFAULT])
    
    def _load_templates_from_directory(self, directory: Path) -> List[PRTemplate]:
        """Load templates from a directory."""
        templates = []
        
        for file_path in directory.glob("*.md"):
            template = self._load_template_file(file_path)
            if template:
                templates.append(template)
        
        # Also check for .json metadata files
        for file_path in directory.glob("*.json"):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                
                # Look for corresponding .md file
                md_file = file_path.with_suffix('.md')
                if md_file.exists():
                    template = self._load_template_file(md_file, metadata)
                    if template:
                        templates.append(template)
                        
            except Exception as e:
                self.logger.warning(f"Failed to load template metadata from {file_path}: {e}")
        
        return templates
    
    def _load_template_file(self, 
                           file_path: Path, 
                           metadata: Optional[Dict[str, Any]] = None) -> Optional[PRTemplate]:
        """Load a single template file."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Extract metadata from content if not provided
            if not metadata:
                metadata = self._extract_metadata_from_content(content)
            
            # Determine template properties
            name = metadata.get('name', file_path.stem)
            template_type = self._parse_template_type(
                metadata.get('type', self._infer_type_from_filename(file_path.name))
            )
            template_format = self._parse_template_format(
                metadata.get('format', self._infer_format_from_content(content))
            )
            
            variables = metadata.get('variables', {})
            conditions = metadata.get('conditions', {})
            priority = metadata.get('priority', 0)
            
            return PRTemplate(
                name=name,
                type=template_type,
                format=template_format,
                content=content,
                variables=variables,
                conditions=conditions,
                priority=priority,
                file_path=file_path
            )
            
        except Exception as e:
            self.logger.error(f"Failed to load template from {file_path}: {e}")
            return None
    
    def _extract_metadata_from_content(self, content: str) -> Dict[str, Any]:
        """Extract metadata from template content frontmatter."""
        metadata = {}
        
        # Look for YAML frontmatter
        frontmatter_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
        if frontmatter_match:
            try:
                # Simple YAML parsing for basic metadata
                frontmatter_lines = frontmatter_match.group(1).split('\n')
                for line in frontmatter_lines:
                    if ':' in line:
                        key, value = line.split(':', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        # Try to parse value
                        if value.lower() in ['true', 'false']:
                            metadata[key] = value.lower() == 'true'
                        elif value.isdigit():
                            metadata[key] = int(value)
                        else:
                            metadata[key] = value
                            
            except Exception as e:
                self.logger.warning(f"Failed to parse frontmatter: {e}")
        
        return metadata
    
    def _filter_by_conditions(self, 
                            templates: List[PRTemplate], 
                            branch_name: Optional[str],
                            context: Dict[str, Any]) -> List[PRTemplate]:
        """Filter templates by their conditions."""
        filtered = []
        
        for template in templates:
            if self._check_conditions(template.conditions, branch_name, context):
                filtered.append(template)
        
        return filtered
    
    def _check_conditions(self, 
                         conditions: Dict[str, Any], 
                         branch_name: Optional[str],
                         context: Dict[str, Any]) -> bool:
        """Check if template conditions are met."""
        if not conditions:
            return True
        
        # Check branch pattern
        branch_pattern = conditions.get('branch_pattern')
        if branch_pattern and branch_name:
            if not re.match(branch_pattern, branch_name):
                return False
        
        # Check context conditions
        for key, expected_value in conditions.items():
            if key == 'branch_pattern':
                continue
                
            context_value = context.get(key)
            if context_value != expected_value:
                return False
        
        return True
    
    def _populate_jinja2_template(self, content: str, context: Dict[str, Any]) -> str:
        """Populate Jinja2 template (simplified version without actual Jinja2)."""
        # Simple variable substitution for now
        # In a full implementation, you'd use the Jinja2 library
        return self._populate_plain_template(content, context)
    
    def _populate_markdown_template(self, content: str, context: Dict[str, Any]) -> str:
        """Populate Markdown template with context variables."""
        return self._populate_plain_template(content, context)
    
    def _populate_plain_template(self, content: str, context: Dict[str, Any]) -> str:
        """Populate plain template using string formatting."""
        try:
            # Use both {variable} and {{variable}} patterns
            result = content
            
            for key, value in context.items():
                # Handle both single and double brace patterns
                result = result.replace(f"{{{key}}}", str(value))
                result = result.replace(f"{{{{{key}}}}}", str(value))
            
            return result
            
        except Exception as e:
            self.logger.error(f"Template population failed: {e}")
            return content
    
    def _parse_change_type(self, change_type: str) -> TemplateType:
        """Parse change type string to enum."""
        try:
            return TemplateType(change_type.lower())
        except ValueError:
            return TemplateType.DEFAULT
    
    def _parse_template_type(self, type_str: str) -> TemplateType:
        """Parse template type string to enum."""
        try:
            return TemplateType(type_str.lower())
        except ValueError:
            return TemplateType.DEFAULT
    
    def _parse_template_format(self, format_str: str) -> TemplateFormat:
        """Parse template format string to enum."""
        try:
            return TemplateFormat(format_str.lower())
        except ValueError:
            return TemplateFormat.MARKDOWN
    
    def _infer_type_from_filename(self, filename: str) -> str:
        """Infer template type from filename."""
        filename_lower = filename.lower()
        
        if 'feature' in filename_lower:
            return 'feature'
        elif 'bug' in filename_lower or 'fix' in filename_lower:
            return 'bugfix'
        elif 'hotfix' in filename_lower:
            return 'hotfix'
        elif 'refactor' in filename_lower:
            return 'refactor'
        elif 'doc' in filename_lower:
            return 'docs'
        elif 'test' in filename_lower:
            return 'tests'
        elif 'ci' in filename_lower or 'build' in filename_lower:
            return 'ci'
        elif 'chore' in filename_lower:
            return 'chore'
        elif 'release' in filename_lower:
            return 'release'
        else:
            return 'default'
    
    def _infer_format_from_content(self, content: str) -> str:
        """Infer template format from content."""
        if '{{' in content or '{%' in content:
            return 'jinja2'
        elif content.startswith('#') or '##' in content:
            return 'markdown'
        else:
            return 'plain'
    
    def _create_feature_template(self) -> PRTemplate:
        """Create default feature template."""
        content = """## 🚀 Feature Description

### What does this PR do?
{title}

### Changes Made
- [ ] Added new functionality in `{branch_name}`
- [ ] Updated {files_changed} files
- [ ] Added {commits_count} commits

### Testing
- [ ] Unit tests added/updated
- [ ] Integration tests pass
- [ ] Manual testing completed

### Impact Assessment
- **Risk Level**: {risk_level}
- **Breaking Changes**: {"Yes" if breaking_changes else "No"}
- **API Changes**: {"Yes" if affects_api else "No"}

### Checklist
- [ ] Code follows project style guidelines
- [ ] Self-review completed
- [ ] Documentation updated if needed
- [ ] No breaking changes or documented properly"""

        return PRTemplate(
            name="feature_default",
            type=TemplateType.FEATURE,
            format=TemplateFormat.MARKDOWN,
            content=content,
            variables={},
            conditions={},
            priority=1
        )
    
    def _create_bugfix_template(self) -> PRTemplate:
        """Create default bugfix template."""
        content = """## 🐛 Bug Fix Description

### Issue Fixed
{title}

### Root Cause
<!-- Describe what was causing the issue -->

### Solution
<!-- Describe how the fix works -->

### Changes Made
- Modified {files_changed} files in {commits_count} commits
- **Lines Changed**: +{lines_added}/-{lines_removed}

### Testing
- [ ] Bug reproduction confirmed
- [ ] Fix verified locally
- [ ] Regression tests added
- [ ] Existing tests still pass

### Impact Assessment
- **Risk Level**: {risk_level}
- **Breaking Changes**: {"Yes" if breaking_changes else "No"}

### Verification Steps
1. <!-- Step to reproduce original bug -->
2. <!-- Step to verify fix -->
3. <!-- Additional verification steps -->

### Checklist
- [ ] Issue reproduced before fix
- [ ] Fix verified
- [ ] Tests added for bug scenario
- [ ] No unintended side effects"""

        return PRTemplate(
            name="bugfix_default", 
            type=TemplateType.BUGFIX,
            format=TemplateFormat.MARKDOWN,
            content=content,
            variables={},
            conditions={},
            priority=1
        )
    
    def _create_hotfix_template(self) -> PRTemplate:
        """Create default hotfix template."""
        content = """## 🚨 HOTFIX: {title}

### Critical Issue
<!-- Describe the critical issue this hotfix addresses -->

### Immediate Impact
<!-- What happens if this isn't fixed immediately -->

### Solution
<!-- Brief description of the fix -->

### Changes
- **Files Modified**: {files_changed}
- **Risk Level**: {risk_level}
- **Breaking Changes**: {"Yes" if breaking_changes else "No"}

### Testing Done
- [ ] Hotfix tested in production-like environment
- [ ] No regression in critical paths
- [ ] Monitoring alerts configured

### Deployment Notes
- [ ] Deploy immediately after merge
- [ ] Monitor system metrics post-deployment
- [ ] Rollback plan ready if needed

### Post-Deployment Tasks
- [ ] Create follow-up issue for proper fix
- [ ] Update documentation
- [ ] Review incident post-mortem"""

        return PRTemplate(
            name="hotfix_default",
            type=TemplateType.HOTFIX,
            format=TemplateFormat.MARKDOWN,
            content=content,
            variables={},
            conditions={},
            priority=2
        )
    
    def _create_refactor_template(self) -> PRTemplate:
        """Create default refactor template."""
        content = """## ♻️ Refactoring: {title}

### Motivation
<!-- Why was this refactoring needed? -->

### Changes Made
- Refactored {files_changed} files
- Total changes: +{lines_added}/-{lines_removed} lines

### Improvements
- [ ] Code readability improved
- [ ] Performance optimized
- [ ] Code duplication reduced
- [ ] Architecture simplified

### Testing
- [ ] All existing tests pass
- [ ] No functional changes
- [ ] Performance benchmarks (if applicable)

### Impact Assessment  
- **Risk Level**: {risk_level}
- **Breaking Changes**: {"Yes" if breaking_changes else "No"}
- **API Changes**: {"Yes" if affects_api else "No"}

### Verification
- [ ] Functionality unchanged
- [ ] Performance not degraded
- [ ] No new warnings or errors"""

        return PRTemplate(
            name="refactor_default",
            type=TemplateType.REFACTOR, 
            format=TemplateFormat.MARKDOWN,
            content=content,
            variables={},
            conditions={},
            priority=1
        )
    
    def _create_docs_template(self) -> PRTemplate:
        """Create default documentation template."""
        content = """## 📚 Documentation Update: {title}

### Documentation Changes
- Updated {files_changed} documentation files

### What's Updated
- [ ] API documentation
- [ ] User guides
- [ ] Code comments
- [ ] README files
- [ ] Contributing guidelines

### Validation
- [ ] Links tested and working
- [ ] Examples verified
- [ ] Spelling and grammar checked
- [ ] Formatting consistent

### Additional Notes
<!-- Any additional context about the documentation changes -->"""

        return PRTemplate(
            name="docs_default",
            type=TemplateType.DOCS,
            format=TemplateFormat.MARKDOWN,
            content=content,
            variables={},
            conditions={},
            priority=1
        )
    
    def _create_tests_template(self) -> PRTemplate:
        """Create default tests template."""
        content = """## 🧪 Test Updates: {title}

### Test Changes
- Modified {files_changed} test files
- Added {commits_count} commits

### Tests Added/Updated
- [ ] Unit tests
- [ ] Integration tests
- [ ] End-to-end tests
- [ ] Performance tests

### Coverage
- [ ] Maintains or improves test coverage
- [ ] No critical paths left untested

### Validation
- [ ] All tests pass locally
- [ ] CI pipeline passes
- [ ] No flaky tests introduced"""

        return PRTemplate(
            name="tests_default",
            type=TemplateType.TESTS,
            format=TemplateFormat.MARKDOWN,
            content=content,
            variables={},
            conditions={},
            priority=1
        )
    
    def _create_ci_template(self) -> PRTemplate:
        """Create default CI/CD template."""
        content = """## ⚙️ CI/CD Update: {title}

### Changes Made
- Modified {files_changed} CI/CD files

### Updates Include
- [ ] Build configuration
- [ ] Deployment scripts
- [ ] Testing pipeline
- [ ] Security scanning
- [ ] Dependencies

### Testing
- [ ] Pipeline tested in development
- [ ] No disruption to existing workflows
- [ ] Rollback plan prepared

### Impact
- **Risk Level**: {risk_level}
- **Affects Deployments**: {"Yes" if affects_api else "No"}"""

        return PRTemplate(
            name="ci_default",
            type=TemplateType.CI,
            format=TemplateFormat.MARKDOWN,
            content=content,
            variables={},
            conditions={},
            priority=1
        )
    
    def _create_chore_template(self) -> PRTemplate:
        """Create default chore template."""
        content = """## 🧹 Chore: {title}

### Changes Made
- Updated {files_changed} files
- {commits_count} commits

### Type of Changes
- [ ] Dependency updates
- [ ] Configuration changes
- [ ] Code cleanup
- [ ] Build process improvements
- [ ] Other maintenance tasks

### Impact
- **Risk Level**: {risk_level}
- **Breaking Changes**: {"Yes" if breaking_changes else "No"}"""

        return PRTemplate(
            name="chore_default",
            type=TemplateType.CHORE,
            format=TemplateFormat.MARKDOWN,
            content=content,
            variables={},
            conditions={},
            priority=1
        )
    
    def _create_release_template(self) -> PRTemplate:
        """Create default release template."""
        content = """## 🎉 Release: {title}

### Release Notes
<!-- Summary of changes in this release -->

### Changes Included
- {files_changed} files modified
- {commits_count} commits since last release

### Features Added
- [ ] List new features

### Bug Fixes
- [ ] List bug fixes

### Breaking Changes
{"⚠️ This release contains breaking changes" if breaking_changes else "✅ No breaking changes"}

### Deployment Checklist
- [ ] Database migrations prepared
- [ ] Configuration updates documented
- [ ] Monitoring alerts updated
- [ ] Documentation updated
- [ ] Release notes published"""

        return PRTemplate(
            name="release_default",
            type=TemplateType.RELEASE,
            format=TemplateFormat.MARKDOWN,
            content=content,
            variables={},
            conditions={},
            priority=2
        )
    
    def _create_default_template_content(self) -> PRTemplate:
        """Create default template."""
        content = """## Summary
{title}

### Changes
- Modified {files_changed} files in {commits_count} commits
- **Risk Level**: {risk_level}
- **Breaking Changes**: {"Yes" if breaking_changes else "No"}

### Testing
- [ ] Tests added/updated
- [ ] All tests pass
- [ ] Manual testing completed

### Checklist
- [ ] Code review completed
- [ ] Documentation updated
- [ ] Ready for deployment"""

        return PRTemplate(
            name="default",
            type=TemplateType.DEFAULT,
            format=TemplateFormat.MARKDOWN,
            content=content,
            variables={},
            conditions={},
            priority=0
        )


def main():
    """Command-line interface for template manager operations."""
    import argparse
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    parser = argparse.ArgumentParser(description='PR Template Manager for Claude Flow')
    parser.add_argument('--config', help='Path to configuration file')
    parser.add_argument('--workspace', help='Workspace directory')
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Discover templates command
    discover_parser = subparsers.add_parser('discover', help='Discover available templates')
    
    # Show template command
    show_parser = subparsers.add_parser('show', help='Show template content')
    show_parser.add_argument('template_name', help='Template name to show')
    
    # Populate template command
    populate_parser = subparsers.add_parser('populate', help='Populate template with test data')
    populate_parser.add_argument('template_name', help='Template name to populate')
    populate_parser.add_argument('--title', default='Test PR Title', help='PR title')
    populate_parser.add_argument('--branch', default='feature/test', help='Branch name')
    populate_parser.add_argument('--change-type', default='feature', help='Change type')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    try:
        config = ConfigManager(args.config) if args.config else ConfigManager()
        workspace = Path(args.workspace) if args.workspace else Path.cwd()
        manager = PRTemplateManager(config, workspace)
        
        if args.command == 'discover':
            templates = manager.discover_templates()
            
            print(f"Found {len(templates)} templates:")
            for template in templates:
                print(f"  - {template.name} ({template.type.value}, priority: {template.priority})")
                if template.file_path:
                    print(f"    Path: {template.file_path}")
        
        elif args.command == 'show':
            templates = manager.discover_templates()
            template = next((t for t in templates if t.name == args.template_name), None)
            
            if template:
                print(f"Template: {template.name}")
                print(f"Type: {template.type.value}")
                print(f"Format: {template.format.value}")
                print("Content:")
                print("=" * 50)
                print(template.content)
            else:
                print(f"Template '{args.template_name}' not found")
                sys.exit(1)
        
        elif args.command == 'populate':
            templates = manager.discover_templates()
            template = next((t for t in templates if t.name == args.template_name), None)
            
            if not template:
                print(f"Template '{args.template_name}' not found")
                sys.exit(1)
            
            context = manager.create_template_context(
                title=args.title,
                branch_name=args.branch,
                change_type=args.change_type,
                files_changed=5,
                commits_count=3,
                lines_added=100,
                lines_removed=25,
                risk_level='medium'
            )
            
            populated = manager.populate_template(template, context)
            
            print(f"Populated template '{template.name}':")
            print("=" * 50)
            print(populated)
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()