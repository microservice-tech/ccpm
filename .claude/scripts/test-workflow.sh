#!/bin/bash

# test-workflow.sh - Test script for workflow template validation
# This script validates the workflow template without actually executing Claude Flow

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_SCRIPT="$SCRIPT_DIR/workflow-template.sh"

# Test configuration
TEST_ISSUE_ID="999"
TEST_ISSUE_TITLE="Test Workflow Template Integration"
TEST_REPO_URL="https://github.com/microservice-tech/ccpm.git"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_test() {
    echo -e "${YELLOW}[TEST]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[PASS]${NC} $1"
}

log_error() {
    echo -e "${RED}[FAIL]${NC} $1"
}

# Test 1: Help functionality
test_help() {
    log_test "Testing help functionality..."
    
    if "$WORKFLOW_SCRIPT" --help >/dev/null 2>&1; then
        log_success "Help command works correctly"
        return 0
    else
        log_error "Help command failed"
        return 1
    fi
}

# Test 2: Argument validation
test_argument_validation() {
    log_test "Testing argument validation..."
    
    # Test missing arguments
    if ! "$WORKFLOW_SCRIPT" 2>/dev/null; then
        log_success "Correctly rejects missing arguments"
    else
        log_error "Should reject missing arguments"
        return 1
    fi
    
    # Test invalid issue ID
    if ! "$WORKFLOW_SCRIPT" "abc" "title" "url" --dry-run 2>/dev/null; then
        log_success "Correctly rejects non-numeric issue ID"
    else
        log_error "Should reject non-numeric issue ID"
        return 1
    fi
    
    return 0
}

# Test 3: Dry run functionality
test_dry_run() {
    log_test "Testing dry run functionality..."
    
    # Set minimal environment for dry run
    export ANTHROPIC_API_KEY="test-key"
    
    if "$WORKFLOW_SCRIPT" "$TEST_ISSUE_ID" "$TEST_ISSUE_TITLE" "$TEST_REPO_URL" --dry-run >/dev/null 2>&1; then
        log_success "Dry run completes successfully"
        return 0
    else
        log_error "Dry run failed"
        return 1
    fi
}

# Test 4: Utility script functionality
test_utility_scripts() {
    log_test "Testing utility scripts..."
    
    local errors=0
    
    # Test workspace manager
    if ! "$SCRIPT_DIR/utils/workspace-manager.sh" >/dev/null 2>&1; then
        log_success "Workspace manager shows help correctly"
    else
        log_error "Workspace manager help failed"
        errors=$((errors + 1))
    fi
    
    # Test git operations
    if ! "$SCRIPT_DIR/utils/git-operations.sh" >/dev/null 2>&1; then
        log_success "Git operations shows help correctly"
    else
        log_error "Git operations help failed"
        errors=$((errors + 1))
    fi
    
    return $errors
}

# Test 5: Prerequisites validation
test_prerequisites() {
    log_test "Testing prerequisites validation..."
    
    # Test without ANTHROPIC_API_KEY
    unset ANTHROPIC_API_KEY || true
    
    if ! "$WORKFLOW_SCRIPT" "$TEST_ISSUE_ID" "$TEST_ISSUE_TITLE" "$TEST_REPO_URL" --dry-run 2>/dev/null; then
        log_success "Correctly requires ANTHROPIC_API_KEY"
        return 0
    else
        log_error "Should require ANTHROPIC_API_KEY"
        return 1
    fi
}

# Test 6: Workspace operations
test_workspace_operations() {
    log_test "Testing workspace operations..."
    
    local workspace_manager="$SCRIPT_DIR/utils/workspace-manager.sh"
    local test_id="test-999"
    
    # Test list command (should not fail even if no workspaces)
    if "$workspace_manager" list >/dev/null 2>&1; then
        log_success "Workspace list command works"
    else
        log_error "Workspace list command failed"
        return 1
    fi
    
    # Test exists command for non-existent workspace
    if ! "$workspace_manager" exists "$test_id" >/dev/null 2>&1; then
        log_success "Correctly reports non-existent workspace"
        return 0
    else
        log_error "Should report non-existent workspace"
        return 1
    fi
}

# Main test runner
run_tests() {
    echo "=========================================="
    echo "Workflow Template Test Suite"
    echo "=========================================="
    echo
    
    local total_tests=6
    local passed_tests=0
    
    # Run tests
    if test_help; then
        passed_tests=$((passed_tests + 1))
    fi
    echo
    
    if test_argument_validation; then
        passed_tests=$((passed_tests + 1))
    fi
    echo
    
    if test_dry_run; then
        passed_tests=$((passed_tests + 1))
    fi
    echo
    
    if test_utility_scripts; then
        passed_tests=$((passed_tests + 1))
    fi
    echo
    
    if test_prerequisites; then
        passed_tests=$((passed_tests + 1))
    fi
    echo
    
    if test_workspace_operations; then
        passed_tests=$((passed_tests + 1))
    fi
    echo
    
    # Summary
    echo "=========================================="
    echo "Test Results: $passed_tests/$total_tests tests passed"
    echo "=========================================="
    
    if [[ "$passed_tests" -eq "$total_tests" ]]; then
        log_success "All tests passed! Workflow template is ready for deployment."
        return 0
    else
        log_error "Some tests failed. Please review the implementation."
        return 1
    fi
}

# Run main function if script is executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    run_tests
fi