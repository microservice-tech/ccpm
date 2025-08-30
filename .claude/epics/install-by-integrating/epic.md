---
name: install-by-integrating
status: backlog
created: 2025-08-30T19:27:40Z
progress: 25%
prd: .claude/prds/install-by-integrating.md
github: https://github.com/microservice-tech/ccpm/issues/19
---

# Epic: install-by-integrating

## Overview
Enhance the CCPM installation process with a new `--integrate` flag that enables non-destructive merging of CCPM components into existing projects. This approach leverages temporary directory cloning, intelligent file comparison, and configurable merge strategies to preserve existing .claude configurations while adding PM capabilities.

## Architecture Decisions
- **Temporary Directory Pattern**: Clone to temp, analyze, then selectively copy - minimizes risk and enables rollback
- **Shell Script Enhancement**: Extend existing ccpm.sh/ccpm.bat rather than creating new installers - maintains backward compatibility
- **File-based Conflict Detection**: Use simple file existence checks and diff comparisons - avoids complex parsing
- **Manifest-based Tracking**: Record all installation actions in .claude/.ccpm-manifest.json for rollback capability
- **Strategy Pattern**: Implement conflict resolution as pluggable strategies (skip, backup, overwrite) - flexible and extensible

## Technical Approach

### Shell Script Enhancement
- Add argument parsing for `--integrate`, `--strategy`, and `--mode` flags
- Implement temporary directory management using mktemp/TEMP
- Create file comparison and copying logic
- Add rollback functionality using manifest file

### Integration Logic Components
- **Directory Scanner**: Recursively identify existing .claude structure
- **Conflict Detector**: Compare source and target files
- **Merge Engine**: Apply selected strategy to resolve conflicts
- **Manifest Writer**: Track all operations for potential rollback

### Cross-Platform Support
- Bash implementation for Unix/Linux/macOS
- PowerShell/Batch implementation for Windows
- Shared logic patterns between implementations
- Platform-specific path handling

## Implementation Strategy
- Start with bash implementation as reference
- Port logic to Windows scripts
- Add comprehensive error handling
- Include verbose logging for troubleshooting
- Create validation suite for testing

## Task Breakdown Preview
High-level task categories that will be created:
- [ ] Task 1: Enhance bash script with argument parsing and temp directory logic
- [ ] Task 2: Implement file scanning and conflict detection logic
- [ ] Task 3: Create merge strategies (skip, backup, overwrite)
- [ ] Task 4: Add manifest generation and rollback capability
- [ ] Task 5: Port implementation to Windows (PowerShell/Batch)
- [ ] Task 6: Create integration tests for various scenarios
- [ ] Task 7: Update installation documentation and README
- [ ] Task 8: Add validation command to verify successful integration

## Dependencies
- Git CLI for repository cloning
- Standard Unix utilities (mktemp, cp, diff) for bash version
- PowerShell 3.0+ for Windows version
- File system permissions for reading/writing .claude directory

## Success Criteria (Technical)
- Zero data loss during integration (100% preservation of existing files)
- Installation completes in <30 seconds for typical projects
- Clear error messages and recovery instructions
- Idempotent operation (safe to run multiple times)
- Exit codes properly indicate success/failure for CI/CD integration

## Estimated Effort
- Overall timeline: 2-3 days of development
- Bash implementation: 4-6 hours
- Windows implementation: 4-6 hours
- Testing and validation: 4-6 hours
- Documentation: 2-3 hours
- Critical path: Bash implementation → Testing → Windows port

## Tasks Created
- [ ] #20 - Enhance bash script with argument parsing and temp directory logic (parallel: true)
- [ ] #21 - Implement file scanning and conflict detection logic (parallel: false)
- [ ] #22 - Create merge strategies (skip, backup, overwrite) (parallel: false)
- [ ] #23 - Add manifest generation and rollback capability (parallel: false)
- [ ] #24 - Port implementation to Windows (PowerShell/Batch) (parallel: false)
- [ ] #25 - Create integration tests for various scenarios (parallel: false)
- [ ] #26 - Update installation documentation and README (parallel: true)
- [ ] #27 - Add validation command to verify successful integration (parallel: false)

Total tasks: 8
Parallel tasks: 2
Sequential tasks: 6
Estimated total effort: 24-30 hours