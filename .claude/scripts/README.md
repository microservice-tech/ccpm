# Claude Flow Workflow Scripts

This directory contains the comprehensive workflow template system for automated GitHub issue resolution using Claude Flow.

## Overview

The workflow system provides complete automation for implementing GitHub issues in isolated environments using Claude Flow's hive-mind functionality. Each issue runs in its own isolated workspace to prevent state contamination between concurrent implementations.

## Architecture

```
.claude/scripts/
├── workflow-template.sh         # Main orchestration script
├── utils/
│   ├── workspace-manager.sh     # Isolated workspace management
│   └── git-operations.sh        # Git and PR automation
├── test-workflow.sh             # Test suite for validation
└── README.md                    # This documentation
```

## Core Components

### 1. Workflow Template (`workflow-template.sh`)

The main orchestration script that coordinates the entire issue implementation lifecycle:

- **Workspace Isolation**: Creates isolated workspaces in `/tmp/claude-flow-issues/issue-{id}/`
- **Repository Management**: Clones repository and creates feature branches
- **Claude Flow Integration**: Installs Claude Flow locally and executes hive-mind
- **Automation**: Handles commits, pushes, and PR creation
- **Error Handling**: Comprehensive error handling and cleanup
- **Logging**: Detailed logging throughout the process

#### Usage Examples:

```bash
# Basic usage
./workflow-template.sh 123 "Fix login bug" "https://github.com/user/repo.git"

# With options
./workflow-template.sh 456 "Add feature" "https://github.com/user/repo.git" --skip-cleanup --timeout 7200

# Dry run (testing)
./workflow-template.sh 789 "Update docs" "https://github.com/user/repo.git" --dry-run
```

### 2. Workspace Manager (`utils/workspace-manager.sh`)

Manages isolated workspaces for complete state isolation:

- **Isolation**: Each issue gets its own workspace directory
- **Locking**: Prevents concurrent access to the same issue
- **Environment Setup**: Creates proper Node.js/npm environment
- **Cleanup**: Complete workspace removal after completion

#### Key Functions:

```bash
# Create workspace
./workspace-manager.sh create 123

# Check if exists
./workspace-manager.sh exists 123

# List all workspaces
./workspace-manager.sh list

# Cleanup workspace
./workspace-manager.sh cleanup 123

# Emergency cleanup all
./workspace-manager.sh cleanup-all
```

### 3. Git Operations (`utils/git-operations.sh`)

Handles all Git-related operations and PR creation:

- **Repository Cloning**: Clone repos into isolated workspaces
- **Branch Management**: Create and manage feature branches
- **Commit Automation**: Automated commits with proper messages
- **PR Creation**: GitHub PR creation with proper formatting
- **Validation**: Git repository and setup validation

#### Key Functions:

```bash
# Clone repository
./git-operations.sh clone "https://github.com/user/repo.git" "/path/to/workspace" 123

# Create feature branch
./git-operations.sh create-branch 123 "/path/to/workspace"

# Commit changes
./git-operations.sh commit 123 "Issue title" "/path/to/workspace"

# Push branch
./git-operations.sh push "feature/issue-123" "/path/to/workspace"

# Create PR
./git-operations.sh create-pr 123 "Issue title" "/path/to/workspace"
```

## Workflow Process

The complete workflow follows these steps:

1. **Validation**: Validate prerequisites and arguments
2. **Workspace Creation**: Create isolated workspace with locking
3. **Repository Setup**: Clone repository and create feature branch
4. **Environment Setup**: Install Claude Flow locally in workspace
5. **Implementation**: Execute Claude Flow hive-mind with issue context
6. **Commit & Push**: Commit changes and push feature branch
7. **PR Creation**: Create pull request linking to original issue
8. **Cleanup**: Remove workspace and release locks

## Prerequisites

### Required Software:
- Node.js 18+ and npm
- Git (configured with user.name and user.email)
- GitHub CLI (`gh`) for PR creation
- Basic Unix utilities (bash, timeout, etc.)

### Required Environment Variables:
- `ANTHROPIC_API_KEY`: Claude API key (required)
- `GITHUB_TOKEN`: GitHub token (optional if using `gh auth`)

### Optional Environment Variables:
- `CLAUDE_FLOW_VERSION`: Version to install (default: "alpha")

## Configuration

### Workspace Configuration:
- Base directory: `/tmp/claude-flow-issues/`
- Lock directory: `/tmp/claude-flow-locks/`
- Workspace pattern: `issue-{id}/`

### Timeouts and Limits:
- Default hive-mind timeout: 3600 seconds (1 hour)
- Lock acquisition timeout: 300 seconds (5 minutes)
- Maximum retry attempts: 3

## Error Handling

The system includes comprehensive error handling:

### Automatic Recovery:
- Stale lock detection and cleanup
- Retry mechanisms for transient failures
- Graceful degradation for non-critical failures

### Error Reporting:
- Detailed error logs in workspace
- Error reports with context and debugging info
- Proper cleanup even on failures

### Manual Recovery:
- Workspace preservation option for debugging
- Emergency cleanup commands
- Status checking utilities

## Testing

Run the comprehensive test suite:

```bash
./test-workflow.sh
```

The test suite validates:
- Help functionality and argument parsing
- Argument validation and error handling
- Dry-run functionality
- Prerequisites checking
- Utility script functionality
- Workspace operations

## Security Considerations

### Isolation:
- Complete filesystem isolation between issues
- Separate Node.js environments per workspace
- No shared state between concurrent executions

### Cleanup:
- Automatic workspace cleanup after completion
- Secure deletion of sensitive information
- Lock file cleanup to prevent resource leaks

### Access Control:
- Workspace permissions restricted to current user
- Temporary file cleanup
- No persistent credential storage

## Troubleshooting

### Common Issues:

1. **Permission Errors**:
   - Ensure `/tmp` is writable
   - Check script execution permissions

2. **API Key Issues**:
   - Verify `ANTHROPIC_API_KEY` is set
   - Check key validity and rate limits

3. **Git Authentication**:
   - Configure `gh auth login` for PR creation
   - Ensure Git user.name/user.email are set

4. **Node.js Issues**:
   - Verify Node.js 18+ is installed
   - Check npm functionality

### Debugging:

1. **Verbose Mode**:
   ```bash
   ./workflow-template.sh 123 "title" "repo" --verbose
   ```

2. **Dry Run**:
   ```bash
   ./workflow-template.sh 123 "title" "repo" --dry-run
   ```

3. **Preserve Workspace**:
   ```bash
   ./workflow-template.sh 123 "title" "repo" --skip-cleanup
   ```

4. **Manual Cleanup**:
   ```bash
   ./utils/workspace-manager.sh cleanup-all
   ```

## Integration

### Background Service Integration:

The workflow template is designed to be called by a background service:

```javascript
const { spawn } = require('child_process');

function executeIssueWorkflow(issueId, issueTitle, repoUrl) {
  return new Promise((resolve, reject) => {
    const child = spawn('./.claude/scripts/workflow-template.sh', [
      issueId,
      issueTitle,
      repoUrl
    ]);
    
    // Handle output and completion
    child.on('close', (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`Workflow failed with code ${code}`));
      }
    });
  });
}
```

### API Integration:

The scripts can also be integrated into REST APIs or webhook handlers for GitHub issue automation.

## Performance

### Concurrency:
- Multiple issues can run simultaneously
- Proper locking prevents conflicts
- Resource isolation prevents interference

### Resource Usage:
- Each workspace: ~100-500MB (depending on repository size)
- Automatic cleanup prevents resource leaks
- Configurable timeouts prevent runaway processes

### Scalability:
- Designed for high-throughput issue processing
- Minimal shared state for horizontal scaling
- Stateless design enables distributed execution

## Monitoring and Observability

### Logging:
- Structured logging with timestamps
- Separate log levels (INFO, WARN, ERROR)
- Context-aware log messages

### Status Tracking:
- Workspace status monitoring
- Progress indication during execution
- Error state preservation for debugging

### Metrics:
- Execution time tracking
- Success/failure rates
- Resource usage monitoring

---

For more information about Claude Flow integration and advanced usage, see the main project documentation.