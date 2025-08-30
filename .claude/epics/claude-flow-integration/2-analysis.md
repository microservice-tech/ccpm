---
issue: 2
title: Orchestrator Core - Build agent coordination and handoff logic
analyzed: 2025-08-30T20:32:16Z
estimated_hours: 16
parallelization_factor: 2.0
updated_reason: Simplified after removing state management and agent persona dependencies
---

# Parallel Work Analysis: Issue #2 (Updated)

## Overview
Build the central orchestrator system that integrates with the Service Foundation (Issue #6) and Workflow Template (Issue #18) to manage complete per-issue workflow execution. Since Claude Flow provides built-in state management and agent personas, the orchestrator focuses on workflow coordination, execution strategies, and integration with existing components.

## Parallel Streams

### Stream A: Core Orchestrator Integration
**Scope**: Main orchestrator class that integrates with Service Foundation and Workflow Template
**Files**:
- .claude/orchestrator/__init__.py
- .claude/orchestrator/orchestrator.py
- .claude/orchestrator/workflow_manager.py
- .claude/orchestrator/issue_handler.py
**Agent Type**: backend-specialist
**Can Start**: immediately
**Estimated Hours**: 6
**Dependencies**: none

### Stream B: Execution Strategy Patterns
**Scope**: Execution strategies for sequential, parallel, and priority-based processing
**Files**:
- .claude/orchestrator/strategies/__init__.py
- .claude/orchestrator/strategies/base.py
- .claude/orchestrator/strategies/sequential.py
- .claude/orchestrator/strategies/parallel.py
- .claude/orchestrator/strategies/priority.py
**Agent Type**: backend-specialist
**Can Start**: immediately
**Estimated Hours**: 4
**Dependencies**: none

### Stream C: Service Integration Layer
**Scope**: Integration with Service Foundation components and workflow executor
**Files**:
- .claude/orchestrator/service_bridge.py
- .claude/orchestrator/workflow_bridge.py
- .claude/orchestrator/monitoring.py
**Agent Type**: backend-specialist
**Can Start**: immediately
**Estimated Hours**: 3
**Dependencies**: none

### Stream D: Testing & Documentation
**Scope**: Unit tests, integration tests, and documentation
**Files**:
- tests/test_orchestrator.py
- tests/test_strategies.py
- tests/test_integration.py
- .claude/orchestrator/README.md
**Agent Type**: test-specialist
**Can Start**: after Streams A, B, C complete
**Estimated Hours**: 3
**Dependencies**: Streams A, B, C

## Coordination Points

### Shared Files
Minimal overlap due to clear module boundaries:
- `.claude/orchestrator/__init__.py` - All streams (export coordination)
- Integration points with existing services from Issues #6 and #18

### Sequential Requirements
1. Core interfaces must be defined before implementations
2. Base strategy classes before concrete implementations
3. All implementation streams before testing stream

## Conflict Risk Assessment
- **Low Risk**: Streams work on separate modules with clear boundaries
- Integration points are well-defined through existing services
- Claude Flow handles state and agent coordination natively

## Parallelization Strategy

**Recommended Approach**: hybrid

Launch Streams A, B, C simultaneously as they work on independent modules. Start Stream D (testing) once all implementation streams complete. This maximizes parallelization while ensuring tests have complete implementations to validate.

## Expected Timeline

With parallel execution:
- Wall time: 9 hours (6h for parallel streams + 3h for testing)
- Total work: 16 hours
- Efficiency gain: 44%

Without parallel execution:
- Wall time: 16 hours

## Notes
- Leverage existing Service Foundation (Issue #6) for service management
- Use Workflow Template (Issue #18) for actual workflow execution
- Claude Flow handles state management and agent personas natively
- Focus on orchestration logic rather than reimplementing existing capabilities
- Ensure seamless integration with systemd service from Issue #6
- Testing should validate integration with existing components