#!/bin/bash

# workflow-template.sh - Main workflow template for per-issue Claude Flow execution
# This script orchestrates the complete lifecycle of implementing a GitHub issue using Claude Flow

set -euo pipefail

# Script directory and utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTILS_DIR="$SCRIPT_DIR/utils"

# Source utility scripts
# shellcheck source=utils/workspace-manager.sh
source "$UTILS_DIR/workspace-manager.sh"
# shellcheck source=utils/git-operations.sh
source "$UTILS_DIR/git-operations.sh"

# Configuration
CLAUDE_FLOW_VERSION="alpha"
MAX_RETRY_ATTEMPTS=3
HIVE_MIND_TIMEOUT=3600  # 1 hour timeout for hive-mind execution

# Logging functions
log_info() {
    echo "[WORKFLOW] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2
}

log_warn() {
    echo "[WORKFLOW] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2
}

log_error() {
    echo "[WORKFLOW] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2
}

log_success() {
    echo "[WORKFLOW] $(date '+%Y-%m-%d %H:%M:%S') - ✓ $1" >&2
}

# Function to display usage information
usage() {
    cat << EOF
Usage: $0 <issue_id> <issue_title> <repo_url> [options]

Arguments:
  issue_id       GitHub issue ID (numeric)
  issue_title    GitHub issue title (quoted string)
  repo_url       Repository URL (https or ssh)

Options:
  --skip-cleanup    Skip workspace cleanup after completion
  --force          Force execution even if workspace exists
  --timeout <sec>  Timeout for hive-mind execution (default: 3600)
  --dry-run        Show what would be done without executing
  --verbose        Enable verbose logging
  --help           Show this help message

Examples:
  $0 123 "Fix login bug" "https://github.com/user/repo.git"
  $0 456 "Add new feature" "git@github.com:user/repo.git" --force
  $0 789 "Update documentation" "https://github.com/user/repo.git" --skip-cleanup

Environment Variables:
  ANTHROPIC_API_KEY    Required for Claude Flow (must be set)
  GITHUB_TOKEN         Required for PR creation (optional if using gh auth)
  CLAUDE_FLOW_VERSION  Claude Flow version to install (default: alpha)
EOF
}

# Function to validate prerequisites
validate_prerequisites() {
    log_info "Validating prerequisites..."
    
    local errors=0
    
    # Check required commands
    for cmd in git npm npx node; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            log_error "Required command not found: $cmd"
            errors=$((errors + 1))
        fi
    done
    
    # Check Node.js version
    if command -v node >/dev/null 2>&1; then
        local node_version
        node_version=$(node --version | sed 's/v//')
        local major_version
        major_version=$(echo "$node_version" | cut -d. -f1)
        
        if [[ "$major_version" -lt 18 ]]; then
            log_error "Node.js 18+ required, found: v$node_version"
            errors=$((errors + 1))
        fi
    fi
    
    # Check for Anthropic API key
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
        log_error "ANTHROPIC_API_KEY environment variable is required"
        errors=$((errors + 1))
    fi
    
    # Check disk space (need at least 1GB for workspace)
    local available_space
    available_space=$(df /tmp | tail -1 | awk '{print $4}')
    if [[ "$available_space" -lt 1048576 ]]; then  # 1GB in KB
        log_warn "Low disk space in /tmp: ${available_space}KB available"
    fi
    
    if [[ "$errors" -gt 0 ]]; then
        log_error "Prerequisites validation failed with $errors errors"
        return 1
    fi
    
    log_success "Prerequisites validation passed"
    return 0
}

# Function to validate input arguments
validate_arguments() {
    local issue_id="$1"
    local issue_title="$2"
    local repo_url="$3"
    
    log_info "Validating arguments..."
    
    # Validate issue ID
    if ! [[ "$issue_id" =~ ^[0-9]+$ ]]; then
        log_error "Issue ID must be numeric: $issue_id"
        return 1
    fi
    
    # Validate issue title
    if [[ -z "$issue_title" ]]; then
        log_error "Issue title cannot be empty"
        return 1
    fi
    
    # Validate repository URL
    if [[ -z "$repo_url" ]]; then
        log_error "Repository URL cannot be empty"
        return 1
    fi
    
    validate_repo_url "$repo_url"
    
    log_success "Arguments validation passed"
    return 0
}

# Function to execute hive-mind with proper error handling and monitoring
execute_hive_mind() {
    local issue_id="$1"
    local issue_title="$2"
    local workspace_path="$3"
    local timeout="$4"
    
    log_info "Executing Claude Flow hive-mind for issue #${issue_id}"
    
    cd "$workspace_path"
    
    # Prepare hive-mind command
    local hive_mind_prompt="Implement GitHub issue #${issue_id}: ${issue_title}

Please follow these steps:
1. Read the issue description and understand the requirements
2. Analyze the codebase to understand the current implementation
3. Design and implement the solution according to the requirements
4. Write appropriate tests for the implementation
5. Update documentation if necessary
6. Ensure the solution follows the project's coding standards
7. Prepare the implementation for pull request creation

Important: Focus on creating a complete, production-ready solution that addresses all aspects of the issue."

    # Create a temporary file for the command
    local cmd_file
    cmd_file=$(mktemp)
    
    cat > "$cmd_file" << EOF
#!/bin/bash
set -euo pipefail
cd "$workspace_path"
export NODE_PATH="$workspace_path/node_modules"
timeout "$timeout" npx claude-flow@$CLAUDE_FLOW_VERSION hive-mind spawn "\$1" --claude
EOF
    
    chmod +x "$cmd_file"
    
    # Execute hive-mind with timeout and monitoring
    local start_time
    start_time=$(date +%s)
    local exit_code=0
    
    log_info "Starting hive-mind execution (timeout: ${timeout}s)..."
    
    if ! "$cmd_file" "$hive_mind_prompt" 2>&1 | while IFS= read -r line; do
        echo "[HIVE-MIND] $line" >&2
    done; then
        exit_code=$?
        log_error "Hive-mind execution failed with exit code: $exit_code"
    fi
    
    local end_time
    end_time=$(date +%s)
    local execution_time=$((end_time - start_time))
    
    # Clean up temporary file
    rm -f "$cmd_file"
    
    log_info "Hive-mind execution completed in ${execution_time}s"
    
    if [[ "$exit_code" -ne 0 ]]; then
        if [[ "$exit_code" -eq 124 ]]; then
            log_error "Hive-mind execution timed out after ${timeout}s"
        fi
        return "$exit_code"
    fi
    
    # Verify that changes were made
    if git diff --quiet && git diff --cached --quiet && [[ -z "$(git ls-files --others --exclude-standard)" ]]; then
        log_warn "No changes detected after hive-mind execution"
        log_warn "This might indicate the hive-mind didn't implement anything"
        return 1
    fi
    
    log_success "Hive-mind execution completed successfully"
    return 0
}

# Function to monitor hive-mind execution
monitor_hive_mind() {
    local workspace_path="$1"
    local start_time="$2"
    local timeout="$3"
    
    while true; do
        local current_time
        current_time=$(date +%s)
        local elapsed=$((current_time - start_time))
        
        if [[ "$elapsed" -ge "$timeout" ]]; then
            log_error "Hive-mind execution timeout reached: ${timeout}s"
            return 1
        fi
        
        # Check if there are any .swarm directories (indicates activity)
        if find "$workspace_path" -name ".swarm" -type d 2>/dev/null | head -1 | grep -q .; then
            log_info "Hive-mind activity detected (swarm directories found)"
        fi
        
        sleep 30  # Check every 30 seconds
    done
}

# Function to handle errors and cleanup
handle_error() {
    local issue_id="$1"
    local workspace_path="$2"
    local error_msg="$3"
    local skip_cleanup="${4:-false}"
    
    log_error "Workflow failed: $error_msg"
    
    # Create error report
    local error_report="$workspace_path/error-report.txt"
    cat > "$error_report" << EOF
Workflow Error Report
Issue ID: $issue_id
Timestamp: $(date)
Error: $error_msg

Git Status:
$(cd "$workspace_path" && git status 2>/dev/null || echo "Git status unavailable")

Working Directory:
$(ls -la "$workspace_path" 2>/dev/null || echo "Directory listing unavailable")

Logs:
$(find "$workspace_path" -name "*.log" -exec echo "=== {} ===" \; -exec cat {} \; 2>/dev/null || echo "No logs found")
EOF
    
    log_info "Error report saved to: $error_report"
    
    # Cleanup unless specifically skipped
    if [[ "$skip_cleanup" != "true" ]]; then
        log_info "Cleaning up workspace due to error..."
        cleanup_workspace "$issue_id" || log_warn "Cleanup failed"
    else
        log_info "Skipping cleanup - workspace preserved for debugging"
    fi
    
    return 1
}

# Main workflow execution function
execute_workflow() {
    local issue_id="$1"
    local issue_title="$2"
    local repo_url="$3"
    local skip_cleanup="${4:-false}"
    local force="${5:-false}"
    local timeout="${6:-$HIVE_MIND_TIMEOUT}"
    local dry_run="${7:-false}"
    
    local workspace_path
    local branch_name
    local pr_url
    
    log_info "Starting workflow for issue #${issue_id}: ${issue_title}"
    
    if [[ "$dry_run" == "true" ]]; then
        log_info "[DRY RUN] Would execute workflow with following parameters:"
        echo "  Issue ID: $issue_id"
        echo "  Issue Title: $issue_title"
        echo "  Repository: $repo_url"
        echo "  Skip Cleanup: $skip_cleanup"
        echo "  Force: $force"
        echo "  Timeout: ${timeout}s"
        return 0
    fi
    
    # Step 1: Acquire lock and create workspace
    log_info "Step 1: Creating isolated workspace"
    if ! acquire_lock "$issue_id" 300; then
        handle_error "$issue_id" "" "Failed to acquire workspace lock" "$skip_cleanup"
        return 1
    fi
    
    if ! workspace_path=$(create_workspace "$issue_id"); then
        release_lock "$issue_id"
        handle_error "$issue_id" "" "Failed to create workspace" "$skip_cleanup"
        return 1
    fi
    
    # Set up error handling for the rest of the workflow
    trap 'handle_error "$issue_id" "$workspace_path" "Workflow interrupted" "$skip_cleanup"' INT TERM
    
    # Step 2: Clone repository
    log_info "Step 2: Cloning repository"
    if ! clone_repository "$repo_url" "$workspace_path" "$issue_id"; then
        handle_error "$issue_id" "$workspace_path" "Failed to clone repository" "$skip_cleanup"
        return 1
    fi
    
    # Step 3: Create feature branch
    log_info "Step 3: Creating feature branch"
    if ! branch_name=$(create_feature_branch "$issue_id" "$workspace_path"); then
        handle_error "$issue_id" "$workspace_path" "Failed to create feature branch" "$skip_cleanup"
        return 1
    fi
    
    # Step 4: Setup workspace environment and install Claude Flow
    log_info "Step 4: Setting up workspace and installing Claude Flow"
    if ! setup_workspace_env "$workspace_path" "$issue_id"; then
        handle_error "$issue_id" "$workspace_path" "Failed to setup workspace environment" "$skip_cleanup"
        return 1
    fi
    
    if ! install_claude_flow "$workspace_path" "$issue_id"; then
        handle_error "$issue_id" "$workspace_path" "Failed to install Claude Flow" "$skip_cleanup"
        return 1
    fi
    
    # Step 5: Execute hive-mind implementation
    log_info "Step 5: Executing Claude Flow hive-mind"
    local attempt=1
    while [[ "$attempt" -le "$MAX_RETRY_ATTEMPTS" ]]; do
        log_info "Hive-mind execution attempt $attempt/$MAX_RETRY_ATTEMPTS"
        
        if execute_hive_mind "$issue_id" "$issue_title" "$workspace_path" "$timeout"; then
            break
        else
            if [[ "$attempt" -eq "$MAX_RETRY_ATTEMPTS" ]]; then
                handle_error "$issue_id" "$workspace_path" "Hive-mind execution failed after $MAX_RETRY_ATTEMPTS attempts" "$skip_cleanup"
                return 1
            fi
            
            log_warn "Hive-mind attempt $attempt failed, retrying..."
            attempt=$((attempt + 1))
            sleep 10
        fi
    done
    
    # Step 6: Commit changes
    log_info "Step 6: Committing changes"
    if ! commit_changes "$issue_id" "$issue_title" "$workspace_path"; then
        handle_error "$issue_id" "$workspace_path" "Failed to commit changes" "$skip_cleanup"
        return 1
    fi
    
    # Step 7: Push branch
    log_info "Step 7: Pushing feature branch"
    if ! push_branch "$branch_name" "$workspace_path"; then
        handle_error "$issue_id" "$workspace_path" "Failed to push branch" "$skip_cleanup"
        return 1
    fi
    
    # Step 8: Create pull request
    log_info "Step 8: Creating pull request"
    if ! pr_url=$(create_pull_request "$issue_id" "$issue_title" "$workspace_path"); then
        handle_error "$issue_id" "$workspace_path" "Failed to create pull request" "$skip_cleanup"
        return 1
    fi
    
    # Step 9: Cleanup (unless skipped)
    log_info "Step 9: Workspace cleanup"
    if [[ "$skip_cleanup" != "true" ]]; then
        cleanup_workspace "$issue_id" || log_warn "Cleanup failed but workflow succeeded"
    else
        log_info "Skipping cleanup - workspace preserved at: $workspace_path"
        release_lock "$issue_id"
    fi
    
    # Clear trap
    trap - INT TERM
    
    log_success "Workflow completed successfully!"
    log_success "Pull Request: $pr_url"
    
    return 0
}

# Main function
main() {
    local issue_id=""
    local issue_title=""
    local repo_url=""
    local skip_cleanup="false"
    local force="false"
    local timeout="$HIVE_MIND_TIMEOUT"
    local dry_run="false"
    local verbose="false"
    
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --skip-cleanup)
                skip_cleanup="true"
                shift
                ;;
            --force)
                force="true"
                shift
                ;;
            --timeout)
                timeout="$2"
                shift 2
                ;;
            --dry-run)
                dry_run="true"
                shift
                ;;
            --verbose)
                verbose="true"
                set -x
                shift
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            -*)
                log_error "Unknown option: $1"
                usage
                exit 1
                ;;
            *)
                if [[ -z "$issue_id" ]]; then
                    issue_id="$1"
                elif [[ -z "$issue_title" ]]; then
                    issue_title="$1"
                elif [[ -z "$repo_url" ]]; then
                    repo_url="$1"
                else
                    log_error "Too many positional arguments"
                    usage
                    exit 1
                fi
                shift
                ;;
        esac
    done
    
    # Validate required arguments
    if [[ -z "$issue_id" ]] || [[ -z "$issue_title" ]] || [[ -z "$repo_url" ]]; then
        log_error "Missing required arguments"
        usage
        exit 1
    fi
    
    # Validate prerequisites
    if ! validate_prerequisites; then
        exit 1
    fi
    
    # Validate arguments
    if ! validate_arguments "$issue_id" "$issue_title" "$repo_url"; then
        exit 1
    fi
    
    # Execute workflow
    if execute_workflow "$issue_id" "$issue_title" "$repo_url" "$skip_cleanup" "$force" "$timeout" "$dry_run"; then
        log_success "Issue #${issue_id} workflow completed successfully"
        exit 0
    else
        log_error "Issue #${issue_id} workflow failed"
        exit 1
    fi
}

# Run main function if script is executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi