---
name: install-by-integrating
description: Merge CCPM system into existing projects without overwriting existing .claude configurations
status: backlog
created: 2025-08-30T19:16:25Z
---

# PRD: install-by-integrating

## Executive Summary

The install-by-integrating feature provides a non-destructive installation method for CCPM (Claude Code Project Management) that merges the CCPM system into existing projects. Unlike the current clone-and-replace approach, this feature intelligently integrates CCPM's commands, agents, and folder structures while preserving any existing .claude configurations, preventing data loss and configuration conflicts in established projects.

## Problem Statement

### Current State
The existing CCPM installation process:
1. Clones the entire CCPM repository into the target directory
2. Removes the .git folder, .gitignore, and install directory
3. Completely replaces any existing content in the target directory

### Problems
- **Destructive Installation**: Cannot install CCPM into existing projects without losing current work
- **Configuration Conflicts**: Existing .claude folders with custom commands/agents are overwritten
- **Integration Barriers**: Teams with established Claude configurations cannot adopt CCPM without manual merging
- **Error-Prone Process**: Installation fails if target directory is not empty, requiring manual cleanup

### Why Now?
As CCPM adoption grows, more teams want to add its PM capabilities to existing projects that already have:
- Custom Claude commands and agents
- Established .claude folder structures
- Project-specific configurations
- Active development workflows

## User Stories

### Primary Persona: Project Maintainer

**Story 1: Adding CCPM to Existing Project**
- **As a** project maintainer with an existing .claude configuration
- **I want to** install CCPM without losing my custom commands and agents
- **So that** I can leverage CCPM's PM features alongside my existing setup

**Acceptance Criteria:**
- Existing .claude files are preserved
- CCPM components are merged intelligently
- Conflicts are detected and reported
- User can choose resolution strategy for conflicts

**Story 2: Selective Component Installation**
- **As a** team lead evaluating CCPM
- **I want to** install only the PM commands without the full system
- **So that** I can gradually adopt CCPM features

**Acceptance Criteria:**
- Can specify which components to install
- Dependencies are automatically resolved
- Installation report shows what was added

### Secondary Persona: DevOps Engineer

**Story 3: Automated Integration**
- **As a** DevOps engineer
- **I want to** integrate CCPM installation into CI/CD pipelines
- **So that** new projects automatically get PM capabilities

**Acceptance Criteria:**
- Non-interactive installation mode
- Configurable merge strategies
- Clear exit codes and logging
- Idempotent operation (safe to run multiple times)

## Requirements

### Functional Requirements

#### Core Integration Logic
1. **Smart Merging**
   - Clone CCPM to temporary directory
   - Scan existing .claude directory structure
   - Identify conflicts and overlaps
   - Merge non-conflicting files
   - Handle conflicts based on strategy

2. **Conflict Resolution Strategies**
   - `skip`: Keep existing files, skip CCPM versions
   - `overwrite`: Replace with CCPM versions
   - `backup`: Create .backup files before overwriting
   - `merge`: Intelligently merge content (for compatible files)
   - `interactive`: Prompt user for each conflict

3. **Component Detection**
   - Identify CCPM components:
     - Commands (`.claude/commands/pm/*`)
     - Scripts (`.claude/scripts/pm/*`)
     - Agents (`.claude/agents/*`)
     - Rules (`.claude/rules/*`)
     - Settings (`.claude/settings.local.json`)
   - Track what gets installed

4. **Installation Modes**
   - `full`: Install all CCPM components
   - `pm-only`: Install only PM-related commands and scripts
   - `minimal`: Install core structure only
   - `custom`: User-specified component list

5. **Rollback Capability**
   - Create installation manifest
   - Track all changes made
   - Provide uninstall/rollback command

### Non-Functional Requirements

#### Performance
- Installation completes within 30 seconds for typical projects
- Minimal disk space usage during temporary operations
- Efficient file comparison algorithms

#### Security
- Validate source repository integrity
- No execution of arbitrary code during installation
- Preserve file permissions
- Secure handling of sensitive configurations

#### Reliability
- Atomic operations (all-or-nothing installation)
- Graceful failure handling
- Clear error messages
- Recovery from interrupted installations

#### Compatibility
- Support Windows (PowerShell/CMD) and Unix-like systems
- Work with various git configurations
- Handle different .claude folder structures
- Support projects with symlinks

## Success Criteria

### Measurable Outcomes
1. **Zero Data Loss**: 100% of existing .claude configurations preserved
2. **Installation Success Rate**: >95% successful installations without manual intervention
3. **Time Reduction**: 80% faster than manual merging for complex projects
4. **Adoption Rate**: 50% of users with existing .claude folders choose integration method

### Key Metrics
- Number of successful integrations
- Average installation time
- Conflict resolution distribution
- Rollback frequency
- User satisfaction scores

### Validation Tests
- Install into empty directory
- Install into project with existing .claude
- Install with conflicting commands
- Install with network interruption
- Multiple consecutive installations

## Constraints & Assumptions

### Constraints
- Must maintain backward compatibility with existing install scripts
- Cannot modify core CCPM repository structure
- Must work without elevated privileges
- Limited to git-based installation (no package managers yet)

### Assumptions
- Users have git installed and configured
- Target projects use standard .claude folder structure
- Network connectivity available for repository cloning
- Users understand basic PM concepts
- Bash/PowerShell available on target systems

## Out of Scope

The following items are explicitly NOT included in this feature:
- Package manager distribution (npm, pip, etc.)
- GUI installation wizard
- Automatic migration of incompatible configurations
- Integration with non-Claude AI assistants
- Custom .claude folder locations
- Multi-repository CCPM installations
- Version management/updates of CCPM
- Integration with Claude Cloud settings

## Dependencies

### External Dependencies
- Git CLI (version 2.0+)
- GitHub API (for repository access)
- Operating system shell (Bash/PowerShell)
- File system with standard permissions

### Internal Dependencies
- CCPM repository structure stability
- Command naming conventions
- Script compatibility requirements
- Settings file format consistency

### Team Dependencies
- Documentation team: Update installation guides
- QA team: Test various integration scenarios
- Support team: Handle integration-related issues
- Community: Feedback on beta releases

## Implementation Approach

### Phase 1: Core Integration Engine
- Develop temporary directory management
- Implement file comparison logic
- Create merge strategies
- Build conflict detection

### Phase 2: Installation Scripts
- Update ccpm.sh with integration option
- Update ccpm.bat with integration option
- Add strategy configuration flags
- Implement rollback functionality

### Phase 3: Testing & Refinement
- Test with various project configurations
- Optimize performance
- Improve error messages
- Document edge cases

### Phase 4: Release
- Update installation documentation
- Create migration guide
- Release as optional feature
- Gather user feedback

## Risk Mitigation

### Technical Risks
- **Risk**: File permission conflicts
  - **Mitigation**: Check permissions before operations, provide clear guidance

- **Risk**: Partial installation state
  - **Mitigation**: Implement transaction-like operations with rollback

- **Risk**: Version incompatibilities
  - **Mitigation**: Version checking and compatibility matrix

### User Experience Risks
- **Risk**: Confusion about merge strategies
  - **Mitigation**: Clear documentation and sensible defaults

- **Risk**: Unexpected behavior after integration
  - **Mitigation**: Detailed installation report and validation

## Appendix

### Example Installation Flow

```bash
# New integration command
curl -sSL https://raw.githubusercontent.com/automazeio/ccpm/main/install.sh | bash -s -- --integrate --strategy=backup

# Or with specific mode
./install.sh --integrate --mode=pm-only --strategy=skip
```

### Proposed File Structure After Integration

```
.claude/
├── commands/
│   ├── pm/           # NEW: CCPM PM commands
│   ├── custom/       # EXISTING: User's custom commands
│   └── ...
├── scripts/
│   ├── pm/           # NEW: CCPM PM scripts
│   └── ...
├── agents/           # MERGED: Both CCPM and existing agents
├── settings.local.json # MERGED: Combined settings
└── settings.local.json.backup # BACKUP: Original settings
```