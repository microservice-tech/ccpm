#!/bin/bash

# workspace-manager.sh - Utility for managing isolated workspaces for Claude Flow issues
# This script provides functions for creating, managing, and cleaning up isolated workspaces

set -euo pipefail

# Global configuration
WORKSPACE_BASE="/tmp/claude-flow-issues"
LOCKFILE_DIR="/tmp/claude-flow-locks"

# Logging functions
log_info() {
    echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2
}

log_warn() {
    echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2
}

log_error() {
    echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2
}

# Function to create an isolated workspace
create_workspace() {
    local issue_id="$1"
    local workspace_path="${WORKSPACE_BASE}/issue-${issue_id}"
    
    log_info "Creating isolated workspace for issue #${issue_id}"
    
    # Ensure base directory exists
    mkdir -p "$WORKSPACE_BASE"
    mkdir -p "$LOCKFILE_DIR"
    
    # Check if workspace already exists
    if [[ -d "$workspace_path" ]]; then
        log_warn "Workspace already exists at ${workspace_path}"
        log_info "Cleaning existing workspace..."
        cleanup_workspace "$issue_id"
    fi
    
    # Create the workspace
    mkdir -p "$workspace_path"
    
    # Create lockfile to prevent concurrent access
    local lockfile="${LOCKFILE_DIR}/issue-${issue_id}.lock"
    echo "$$" > "$lockfile"
    
    log_info "Workspace created successfully at: ${workspace_path}"
    echo "$workspace_path"
}

# Function to get workspace path
get_workspace_path() {
    local issue_id="$1"
    echo "${WORKSPACE_BASE}/issue-${issue_id}"
}

# Function to check if workspace exists
workspace_exists() {
    local issue_id="$1"
    local workspace_path="${WORKSPACE_BASE}/issue-${issue_id}"
    
    if [[ -d "$workspace_path" ]]; then
        return 0
    else
        return 1
    fi
}

# Function to acquire workspace lock
acquire_lock() {
    local issue_id="$1"
    local lockfile="${LOCKFILE_DIR}/issue-${issue_id}.lock"
    local timeout="${2:-300}" # 5 minutes default
    local elapsed=0
    
    log_info "Acquiring lock for issue #${issue_id}"
    
    while [[ -f "$lockfile" ]] && [[ $elapsed -lt $timeout ]]; do
        local lock_pid
        if lock_pid=$(cat "$lockfile" 2>/dev/null); then
            if ! kill -0 "$lock_pid" 2>/dev/null; then
                log_warn "Removing stale lock for issue #${issue_id}"
                rm -f "$lockfile"
                break
            fi
        else
            rm -f "$lockfile"
            break
        fi
        
        log_info "Waiting for lock... (${elapsed}s/${timeout}s)"
        sleep 5
        elapsed=$((elapsed + 5))
    done
    
    if [[ $elapsed -ge $timeout ]]; then
        log_error "Timeout waiting for lock for issue #${issue_id}"
        return 1
    fi
    
    echo "$$" > "$lockfile"
    log_info "Lock acquired for issue #${issue_id}"
}

# Function to release workspace lock
release_lock() {
    local issue_id="$1"
    local lockfile="${LOCKFILE_DIR}/issue-${issue_id}.lock"
    
    if [[ -f "$lockfile" ]]; then
        local lock_pid
        if lock_pid=$(cat "$lockfile" 2>/dev/null) && [[ "$lock_pid" == "$$" ]]; then
            rm -f "$lockfile"
            log_info "Lock released for issue #${issue_id}"
        else
            log_warn "Lock file exists but owned by different process"
        fi
    fi
}

# Function to setup workspace environment
setup_workspace_env() {
    local workspace_path="$1"
    local issue_id="$2"
    
    log_info "Setting up workspace environment"
    
    cd "$workspace_path"
    
    # Create basic package.json for npm dependencies
    cat > package.json << EOF
{
  "name": "claude-flow-issue-${issue_id}",
  "version": "1.0.0",
  "description": "Isolated workspace for GitHub issue #${issue_id}",
  "private": true,
  "scripts": {
    "start": "npx claude-flow@alpha",
    "hive-mind": "npx claude-flow@alpha hive-mind"
  }
}
EOF

    # Create .gitignore for workspace
    cat > .gitignore << EOF
# Node modules
node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*

# Claude Flow specific
.swarm/
.claude-flow/
*.claude-flow-temp

# Environment variables
.env
.env.local
.env.development.local
.env.test.local
.env.production.local

# Logs
logs/
*.log

# Temporary files
*.tmp
*.temp
EOF

    log_info "Workspace environment setup complete"
}

# Function to install Claude Flow locally
install_claude_flow() {
    local workspace_path="$1"
    local issue_id="$2"
    
    log_info "Installing Claude Flow locally in workspace"
    
    cd "$workspace_path"
    
    # Initialize npm if package.json doesn't exist
    if [[ ! -f "package.json" ]]; then
        setup_workspace_env "$workspace_path" "$issue_id"
    fi
    
    # Install Claude Flow
    log_info "Running npm install claude-flow@alpha..."
    if ! npm install claude-flow@alpha --no-audit --no-fund --silent; then
        log_error "Failed to install Claude Flow"
        return 1
    fi
    
    # Initialize Claude Flow
    log_info "Initializing Claude Flow..."
    if ! npx claude-flow init --force; then
        log_error "Failed to initialize Claude Flow"
        return 1
    fi
    
    log_info "Claude Flow installation complete"
}

# Function to cleanup workspace
cleanup_workspace() {
    local issue_id="$1"
    local workspace_path="${WORKSPACE_BASE}/issue-${issue_id}"
    
    log_info "Cleaning up workspace for issue #${issue_id}"
    
    # Release lock first
    release_lock "$issue_id"
    
    # Remove workspace directory
    if [[ -d "$workspace_path" ]]; then
        log_info "Removing workspace directory: ${workspace_path}"
        rm -rf "$workspace_path"
        
        # Verify deletion
        if [[ -d "$workspace_path" ]]; then
            log_error "Failed to remove workspace directory"
            return 1
        fi
        
        log_info "Workspace cleanup complete"
    else
        log_info "Workspace directory does not exist, nothing to clean"
    fi
}

# Function to cleanup all workspaces (emergency cleanup)
cleanup_all_workspaces() {
    log_info "Cleaning up all workspaces"
    
    if [[ -d "$WORKSPACE_BASE" ]]; then
        log_info "Removing all workspaces in: ${WORKSPACE_BASE}"
        rm -rf "${WORKSPACE_BASE:?}/"*
    fi
    
    if [[ -d "$LOCKFILE_DIR" ]]; then
        log_info "Removing all lock files in: ${LOCKFILE_DIR}"
        rm -rf "${LOCKFILE_DIR:?}/"*
    fi
    
    log_info "All workspaces cleaned up"
}

# Function to list active workspaces
list_workspaces() {
    log_info "Active workspaces:"
    
    if [[ -d "$WORKSPACE_BASE" ]]; then
        for workspace in "${WORKSPACE_BASE}"/issue-*/; do
            if [[ -d "$workspace" ]]; then
                local issue_id
                issue_id=$(basename "$workspace" | sed 's/issue-//')
                local lockfile="${LOCKFILE_DIR}/issue-${issue_id}.lock"
                local status="active"
                
                if [[ -f "$lockfile" ]]; then
                    local lock_pid
                    if lock_pid=$(cat "$lockfile" 2>/dev/null) && kill -0 "$lock_pid" 2>/dev/null; then
                        status="locked (PID: ${lock_pid})"
                    else
                        status="stale lock"
                    fi
                fi
                
                echo "  - Issue #${issue_id}: ${workspace} (${status})"
            fi
        done
    else
        echo "  No active workspaces found"
    fi
}

# Main function for CLI usage
main() {
    case "${1:-}" in
        "create")
            if [[ -z "${2:-}" ]]; then
                log_error "Usage: $0 create <issue_id>"
                exit 1
            fi
            create_workspace "$2"
            ;;
        "cleanup")
            if [[ -z "${2:-}" ]]; then
                log_error "Usage: $0 cleanup <issue_id>"
                exit 1
            fi
            cleanup_workspace "$2"
            ;;
        "cleanup-all")
            cleanup_all_workspaces
            ;;
        "list")
            list_workspaces
            ;;
        "exists")
            if [[ -z "${2:-}" ]]; then
                log_error "Usage: $0 exists <issue_id>"
                exit 1
            fi
            if workspace_exists "$2"; then
                echo "exists"
                exit 0
            else
                echo "not_found"
                exit 1
            fi
            ;;
        *)
            echo "Usage: $0 {create|cleanup|cleanup-all|list|exists} [issue_id]"
            echo ""
            echo "Commands:"
            echo "  create <issue_id>     - Create isolated workspace for issue"
            echo "  cleanup <issue_id>    - Clean up workspace for issue"
            echo "  cleanup-all          - Clean up all workspaces"
            echo "  list                 - List all active workspaces"
            echo "  exists <issue_id>    - Check if workspace exists"
            exit 1
            ;;
    esac
}

# Run main function if script is executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi