---
issue: 22
title: Create merge strategies (skip, backup, overwrite)
analyzed: 2025-08-30T20:43:00Z
estimated_hours: 3-4
parallelization_factor: 3.0
---

# Parallel Work Analysis: Issue #22

## Overview
Implement three distinct merge strategies (skip, backup, overwrite) for handling file conflicts during CCPM installation. Each strategy requires its own implementation logic, testing, and integration with the existing conflict detection system from Issue #21.

## Parallel Streams

### Stream A: Core Strategy Functions
**Scope**: Implement the three strategy functions and core merge engine
**Files**:
- `install/ccpm.sh` - Add merge strategy functions (merge_skip, merge_backup, merge_overwrite, apply_merge_strategy)
**Agent Type**: backend-specialist
**Can Start**: immediately
**Estimated Hours**: 1.5
**Dependencies**: none (can use Issue #21's conflict detection output)
**Work Items**:
- Implement `merge_skip()` - preserve existing files
- Implement `merge_backup()` - create timestamped backups
- Implement `merge_overwrite()` - direct replacement
- Create `apply_merge_strategy()` orchestrator function
- Handle edge cases (permissions, symlinks, readonly files)

### Stream B: Interactive Mode & User Experience
**Scope**: Build interactive conflict resolution and dry-run capabilities
**Files**:
- `install/ccpm.sh` - Add interactive functions (prompt_strategy, interactive_resolution, dry_run_mode)
**Agent Type**: fullstack-specialist
**Can Start**: immediately
**Estimated Hours**: 1.5
**Dependencies**: none (can develop with mock conflicts initially)
**Work Items**:
- Implement per-file strategy prompts
- Create interactive conflict resolution menu
- Build dry-run mode with preview output
- Add colored output for better UX
- Implement batch operations (apply to all similar)

### Stream C: Logging, Testing & Integration
**Scope**: Comprehensive logging system and test suite
**Files**:
- `install/ccpm.sh` - Add logging functions (init_merge_log, log_action, generate_merge_report)
- `test_merge_strategies.sh` - New comprehensive test file
**Agent Type**: backend-specialist
**Can Start**: after Streams A & B complete core functions
**Estimated Hours**: 1
**Dependencies**: Streams A & B (needs actual functions to test)
**Work Items**:
- Create detailed logging system with audit trail
- Build merge action report generator
- Write comprehensive test scenarios
- Test all edge cases and error conditions
- Validate integration with conflict detection

## Coordination Points

### Shared Files
The main coordination point is `install/ccpm.sh`:
- Stream A adds strategy functions (lines ~1000-1400)
- Stream B adds interactive functions (lines ~1400-1700)
- Stream C adds logging functions (lines ~1700-1900)
- Clear function naming prevents conflicts

### Sequential Requirements
1. Core strategy functions (Stream A) must work standalone
2. Interactive mode (Stream B) can use mock data initially
3. Testing (Stream C) requires both A & B to be functional
4. Final integration test validates all components together

## Conflict Risk Assessment
- **Low Risk**: Each stream works on distinct function sets
- **Mitigation**: Defined line ranges in ccpm.sh for each stream
- **Coordination**: Use function prefixes (merge_, interactive_, log_)

## Parallelization Strategy

**Recommended Approach**: Parallel with convergence

1. Launch Streams A & B simultaneously (independent development)
2. Each stream can test their functions in isolation
3. When both complete (~2.5 hours), launch Stream C
4. Stream C performs integration testing and logging

## Expected Timeline

With parallel execution:
- Wall time: 2.5 hours
- Total work: 4 hours
- Efficiency gain: 37.5%

Without parallel execution:
- Wall time: 4 hours

## Implementation Notes

### Stream A (Core Strategies) Focus:
```bash
# Skip Strategy
- Detect existing file
- Log skip action
- Continue to next file

# Backup Strategy
- Create .claude-backup/ directory
- Generate timestamp
- Copy original to backup
- Apply new file
- Log backup location

# Overwrite Strategy
- Direct file replacement
- Handle permissions properly
- Log overwrite action
```

### Stream B (Interactive Mode) Focus:
```bash
# Interactive Resolution
- Show file diff
- Present strategy options
- Remember user preferences
- Apply to similar conflicts

# Dry Run Mode
- Simulate all operations
- Show what would happen
- No actual file changes
- Generate preview report
```

### Stream C (Logging & Testing) Focus:
```bash
# Logging System
- Initialize log file
- Track every operation
- Generate summary report
- Support verbose/quiet modes

# Test Coverage
- Test each strategy individually
- Test interactive mode flows
- Test dry-run accuracy
- Test error conditions
```

## Testing Strategy

Each stream should include:
1. Unit tests for individual functions
2. Integration tests with conflict detection
3. Edge case handling (permissions, disk full, etc.)
4. User experience validation

## Notes
- Leverage existing conflict detection from Issue #21
- Maintain backward compatibility with simple mode
- Ensure all strategies are idempotent (safe to retry)
- Consider future extensibility for custom strategies
- Repository URL should use: https://github.com/microservice-tech/ccpm.git
