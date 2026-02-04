#!/bin/bash

# git-operations.sh - Utility for Git operations in Claude Flow issue workflows
# This script provides functions for repository operations, branch management, and PR creation

set -euo pipefail

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

# Function to validate Git repository URL
validate_repo_url() {
    local repo_url="$1"
    
    if [[ -z "$repo_url" ]]; then
        log_error "Repository URL is required"
        return 1
    fi
    
    # Check if URL is valid format (GitHub, GitLab, etc.)
    if [[ ! "$repo_url" =~ ^https?://[^/]+/[^/]+/[^/]+\.git$ ]] && [[ ! "$repo_url" =~ ^git@[^:]+:[^/]+/[^/]+\.git$ ]]; then
        log_warn "Repository URL format might be invalid: $repo_url"
    fi
    
    log_info "Repository URL validated: $repo_url"
}

# Function to clone repository into workspace
clone_repository() {
    local repo_url="$1"
    local workspace_path="$2"
    local issue_id="$3"
    
    log_info "Cloning repository for issue #${issue_id}"
    
    validate_repo_url "$repo_url"
    
    # Ensure workspace directory exists
    if [[ ! -d "$workspace_path" ]]; then
        log_error "Workspace directory does not exist: $workspace_path"
        return 1
    fi
    
    cd "$workspace_path"
    
    # Clone the repository
    log_info "Cloning from: $repo_url"
    if ! git clone "$repo_url" . 2>&1; then
        log_error "Failed to clone repository"
        return 1
    fi
    
    # Configure Git user if not already set
    if ! git config user.name >/dev/null 2>&1; then
        git config user.name "Claude Flow Bot"
        log_info "Set Git user.name to 'Claude Flow Bot'"
    fi
    
    if ! git config user.email >/dev/null 2>&1; then
        git config user.email "claude-flow@microservice.tech"
        log_info "Set Git user.email to 'claude-flow@microservice.tech'"
    fi
    
    log_info "Repository cloned successfully"
}

# Function to create and switch to feature branch
create_feature_branch() {
    local issue_id="$1"
    local branch_name="feature/issue-${issue_id}"
    local workspace_path="$2"
    
    log_info "Creating feature branch: $branch_name"
    
    cd "$workspace_path"
    
    # Ensure we're in a git repository
    if ! git rev-parse --git-dir >/dev/null 2>&1; then
        log_error "Not in a Git repository"
        return 1
    fi
    
    # Fetch latest changes
    log_info "Fetching latest changes..."
    if ! git fetch origin; then
        log_warn "Failed to fetch from origin, continuing anyway..."
    fi
    
    # Get the default branch name
    local default_branch
    default_branch=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo "main")
    
    # If default branch doesn't exist, try common alternatives
    if ! git show-ref --verify --quiet "refs/remotes/origin/$default_branch"; then
        for branch in main master develop; do
            if git show-ref --verify --quiet "refs/remotes/origin/$branch"; then
                default_branch="$branch"
                break
            fi
        done
    fi
    
    log_info "Using base branch: $default_branch"
    
    # Ensure we're on the latest default branch
    if ! git checkout "$default_branch"; then
        log_error "Failed to checkout base branch: $default_branch"
        return 1
    fi
    
    if ! git pull origin "$default_branch"; then
        log_warn "Failed to pull latest changes from $default_branch"
    fi
    
    # Check if feature branch already exists
    if git show-ref --verify --quiet "refs/heads/$branch_name"; then
        log_warn "Branch $branch_name already exists, deleting it"
        git branch -D "$branch_name"
    fi
    
    # Create and switch to feature branch
    if ! git checkout -b "$branch_name"; then
        log_error "Failed to create feature branch: $branch_name"
        return 1
    fi
    
    log_info "Feature branch created and checked out: $branch_name"
    echo "$branch_name"
}

# Function to commit changes
commit_changes() {
    local issue_id="$1"
    local issue_title="$2"
    local workspace_path="$3"
    local commit_message="${4:-}"
    
    log_info "Committing changes for issue #${issue_id}"
    
    cd "$workspace_path"
    
    # Check if there are any changes to commit
    if git diff --quiet && git diff --cached --quiet; then
        log_warn "No changes to commit"
        return 0
    fi
    
    # Add all changes
    log_info "Adding all changes to staging area"
    git add -A
    
    # Generate commit message if not provided
    if [[ -z "$commit_message" ]]; then
        commit_message="feat: implement issue #${issue_id}

Automated implementation by Claude Flow hive-mind for:
${issue_title}

- Implementation completed according to issue requirements
- Tests included where applicable
- Documentation updated as needed"
    fi
    
    # Commit changes
    log_info "Committing changes with message: ${commit_message%%$'\n'*}..."
    if ! git commit -m "$commit_message"; then
        log_error "Failed to commit changes"
        return 1
    fi
    
    log_info "Changes committed successfully"
}

# Function to push branch to remote
push_branch() {
    local branch_name="$1"
    local workspace_path="$2"
    local force="${3:-false}"
    
    log_info "Pushing branch to remote: $branch_name"
    
    cd "$workspace_path"
    
    # Push branch to origin
    local push_args=("origin" "$branch_name")
    if [[ "$force" == "true" ]]; then
        push_args+=("--force")
        log_warn "Force pushing branch"
    fi
    
    if ! git push -u "${push_args[@]}"; then
        log_error "Failed to push branch to remote"
        return 1
    fi
    
    log_info "Branch pushed successfully: $branch_name"
}

# Function to create pull request
create_pull_request() {
    local issue_id="$1"
    local issue_title="$2"
    local workspace_path="$3"
    local pr_title="${4:-}"
    local pr_body="${5:-}"
    
    log_info "Creating pull request for issue #${issue_id}"
    
    cd "$workspace_path"
    
    # Check if gh CLI is available
    if ! command -v gh >/dev/null 2>&1; then
        log_error "GitHub CLI (gh) is not installed"
        log_info "Please install gh CLI: https://cli.github.com/"
        return 1
    fi
    
    # Check if gh is authenticated
    if ! gh auth status >/dev/null 2>&1; then
        log_error "GitHub CLI is not authenticated"
        log_info "Please run: gh auth login"
        return 1
    fi
    
    # Generate PR title if not provided
    if [[ -z "$pr_title" ]]; then
        pr_title="Fix #${issue_id}: ${issue_title}"
    fi
    
    # Generate PR body if not provided
    if [[ -z "$pr_body" ]]; then
        pr_body="## Summary
This PR implements the solution for issue #${issue_id}.

## Changes
- Automated implementation by Claude Flow hive-mind
- Solution addresses all requirements specified in the issue
- Tests included where applicable
- Documentation updated as needed

## Closes
Closes #${issue_id}

---
🤖 Generated with Claude Flow - Automated Issue Resolution"
    fi
    
    # Create the pull request
    log_info "Creating PR with title: $pr_title"
    local pr_url
    if ! pr_url=$(gh pr create --title "$pr_title" --body "$pr_body" 2>&1); then
        log_error "Failed to create pull request"
        log_error "GitHub CLI output: $pr_url"
        return 1
    fi
    
    log_info "Pull request created successfully: $pr_url"
    echo "$pr_url"
}

# Function to check repository status
check_repo_status() {
    local workspace_path="$1"
    
    cd "$workspace_path"
    
    # Check if in Git repo
    if ! git rev-parse --git-dir >/dev/null 2>&1; then
        echo "not_a_repository"
        return 1
    fi
    
    # Get current branch
    local current_branch
    current_branch=$(git branch --show-current 2>/dev/null || echo "unknown")
    
    # Check for uncommitted changes
    local has_changes="false"
    if ! git diff --quiet || ! git diff --cached --quiet; then
        has_changes="true"
    fi
    
    # Check for untracked files
    local has_untracked="false"
    if [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
        has_untracked="true"
    fi
    
    # Get remote status
    local remote_status="unknown"
    if git remote get-url origin >/dev/null 2>&1; then
        remote_status="configured"
        if git ls-remote --exit-code origin >/dev/null 2>&1; then
            remote_status="accessible"
        fi
    fi
    
    echo "branch:$current_branch"
    echo "changes:$has_changes"
    echo "untracked:$has_untracked"
    echo "remote:$remote_status"
}

# Function to validate workspace Git setup
validate_git_setup() {
    local workspace_path="$1"
    local repo_url="$2"
    
    log_info "Validating Git setup in workspace"
    
    cd "$workspace_path"
    
    # Check if Git is available
    if ! command -v git >/dev/null 2>&1; then
        log_error "Git is not installed"
        return 1
    fi
    
    # Check if in Git repository
    if ! git rev-parse --git-dir >/dev/null 2>&1; then
        log_error "Not in a Git repository"
        return 1
    fi
    
    # Check if origin remote exists and matches expected URL
    local origin_url
    if origin_url=$(git remote get-url origin 2>/dev/null); then
        if [[ "$origin_url" != "$repo_url" ]]; then
            log_warn "Origin URL mismatch. Expected: $repo_url, Got: $origin_url"
        fi
    else
        log_error "Origin remote not configured"
        return 1
    fi
    
    # Check if working directory is clean for critical operations
    if ! git diff --quiet || ! git diff --cached --quiet; then
        log_warn "Working directory has uncommitted changes"
    fi
    
    log_info "Git setup validation complete"
    return 0
}

# Main function for CLI usage
main() {
    case "${1:-}" in
        "clone")
            if [[ -z "${2:-}" ]] || [[ -z "${3:-}" ]] || [[ -z "${4:-}" ]]; then
                log_error "Usage: $0 clone <repo_url> <workspace_path> <issue_id>"
                exit 1
            fi
            clone_repository "$2" "$3" "$4"
            ;;
        "create-branch")
            if [[ -z "${2:-}" ]] || [[ -z "${3:-}" ]]; then
                log_error "Usage: $0 create-branch <issue_id> <workspace_path>"
                exit 1
            fi
            create_feature_branch "$2" "$3"
            ;;
        "commit")
            if [[ -z "${2:-}" ]] || [[ -z "${3:-}" ]] || [[ -z "${4:-}" ]]; then
                log_error "Usage: $0 commit <issue_id> <issue_title> <workspace_path> [commit_message]"
                exit 1
            fi
            commit_changes "$2" "$3" "$4" "${5:-}"
            ;;
        "push")
            if [[ -z "${2:-}" ]] || [[ -z "${3:-}" ]]; then
                log_error "Usage: $0 push <branch_name> <workspace_path> [force]"
                exit 1
            fi
            push_branch "$2" "$3" "${4:-false}"
            ;;
        "create-pr")
            if [[ -z "${2:-}" ]] || [[ -z "${3:-}" ]] || [[ -z "${4:-}" ]]; then
                log_error "Usage: $0 create-pr <issue_id> <issue_title> <workspace_path> [pr_title] [pr_body]"
                exit 1
            fi
            create_pull_request "$2" "$3" "$4" "${5:-}" "${6:-}"
            ;;
        "status")
            if [[ -z "${2:-}" ]]; then
                log_error "Usage: $0 status <workspace_path>"
                exit 1
            fi
            check_repo_status "$2"
            ;;
        "validate")
            if [[ -z "${2:-}" ]] || [[ -z "${3:-}" ]]; then
                log_error "Usage: $0 validate <workspace_path> <repo_url>"
                exit 1
            fi
            validate_git_setup "$2" "$3"
            ;;
        *)
            echo "Usage: $0 {clone|create-branch|commit|push|create-pr|status|validate} [args...]"
            echo ""
            echo "Commands:"
            echo "  clone <repo_url> <workspace_path> <issue_id>                     - Clone repository"
            echo "  create-branch <issue_id> <workspace_path>                        - Create feature branch"
            echo "  commit <issue_id> <issue_title> <workspace_path> [message]       - Commit changes"
            echo "  push <branch_name> <workspace_path> [force]                      - Push branch to remote"
            echo "  create-pr <issue_id> <issue_title> <workspace_path> [title] [body] - Create pull request"
            echo "  status <workspace_path>                                          - Check repository status"
            echo "  validate <workspace_path> <repo_url>                            - Validate Git setup"
            exit 1
            ;;
    esac
}

# Run main function if script is executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi