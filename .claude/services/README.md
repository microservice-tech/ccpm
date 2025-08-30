# Claude Flow Services

This directory contains the core Python services for the Claude Flow automation system that polls GitHub for issues and automatically implements them using Claude Flow.

## Architecture

The service is composed of four main components:

1. **GitHubClient** (`github_client.py`) - Handles all GitHub API interactions
2. **IssueProcessor** (`issue_processor.py`) - Manages concurrent issue processing  
3. **WorkflowExecutor** (`workflow_executor.py`) - Executes workflow scripts
4. **ClaudeFlowService** (`claude_flow_service.py`) - Main orchestration service

## Quick Start

### Prerequisites

- Python 3.7+
- Required packages: `pip install -r requirements.txt`
- GitHub personal access token with repository permissions
- Anthropic API key for Claude access
- Access to the workflow-template.sh script

### Basic Usage

1. Set required environment variables:
```bash
export GITHUB_TOKEN="ghp_your_github_token_here"
export ANTHROPIC_API_KEY="your_anthropic_api_key_here"
export REPOSITORIES='[{"owner":"your-org","repo":"your-repo","url":"https://github.com/your-org/your-repo.git"}]'
```

2. Run the service:
```bash
python3 claude_flow_service.py
```

### Using Configuration File

1. Copy the example configuration:
```bash
cp config-example.json config.json
```

2. Edit `config.json` with your settings

3. Run with configuration file:
```bash
python3 claude_flow_service.py --config config.json
```

## Configuration

The service can be configured via environment variables or JSON file.

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GITHUB_TOKEN` | GitHub personal access token | Required |
| `REPOSITORIES` | JSON array of repository configurations | Required |
| `ANTHROPIC_API_KEY` | Anthropic API key (passed to workflows) | Required |
| `POLLING_INTERVAL` | Polling interval in seconds | 300 |
| `MAX_CONCURRENT_WORKERS` | Maximum concurrent issue processors | 3 |
| `LOG_LEVEL` | Logging level (DEBUG/INFO/WARNING/ERROR) | INFO |
| `LOG_FILE` | Log file path (optional) | None |
| `ISSUE_LABEL` | GitHub label to filter issues | ready-for-implementation |
| `EXCLUDE_LABELS` | Comma-separated labels to exclude | claude-flow-completed,claude-flow-processing |

### JSON Configuration

See `config-example.json` for a complete example configuration file.

## Repository Configuration

Each repository is configured with:
- `owner`: GitHub repository owner
- `repo`: Repository name  
- `url`: Clone URL (https or ssh)

Example:
```json
{
  "repositories": [
    {
      "owner": "myorg",
      "repo": "myproject", 
      "url": "https://github.com/myorg/myproject.git"
    }
  ]
}
```

## Service Management

### Running as Daemon

The service includes basic daemon support:
```bash
python3 claude_flow_service.py --daemon
```

### Checking Status

Get current service status:
```bash
python3 claude_flow_service.py --status
```

### Signal Handling

The service responds to standard Unix signals:
- `SIGINT`/`SIGTERM`: Graceful shutdown
- `SIGHUP`: Configuration reload (future feature)

## Issue Processing Workflow

1. Service polls configured repositories for issues with the target label
2. Issues are filtered to exclude those already processed or with exclude labels
3. Eligible issues are submitted to the issue processor
4. Issue processor manages concurrent workflow execution
5. Workflow executor calls the workflow-template.sh script
6. Progress is tracked and GitHub is updated with comments
7. Pull requests are created for successful implementations

## Monitoring

### Logs

The service provides comprehensive logging:
- Service lifecycle events
- GitHub API interactions
- Issue processing status
- Workflow execution progress
- Error conditions and recovery

### State Management

Processing state is persisted to disk to handle service restarts:
- Issue processing status
- Retry attempts
- Success/failure tracking

## Testing

Run the integration tests:
```bash
python3 test_integration.py
```

This will verify:
- Module imports
- Configuration loading
- Workflow script integration

## Dependencies

Core dependencies (see requirements.txt):
- `requests` - HTTP client for GitHub API
- `psutil` - Process management utilities

Optional dependencies:
- `coloredlogs` - Enhanced logging
- `orjson` - Faster JSON processing

## Error Handling

The service includes robust error handling for:
- GitHub API rate limits (automatic backoff)
- Network connectivity issues
- Workflow execution failures
- Process management errors
- Configuration validation

## Security Considerations

- Store sensitive tokens in environment variables, not config files
- Use minimal GitHub token permissions required
- Configure log file permissions appropriately
- Run service with minimal required system privileges

## Troubleshooting

### Common Issues

1. **Authentication errors**: Verify GITHUB_TOKEN has required permissions
2. **Rate limiting**: Service will automatically handle rate limits with backoff
3. **Workflow failures**: Check workflow logs in configured log directory
4. **Import errors**: Ensure all dependencies are installed

### Debug Mode

Enable debug logging:
```bash
export LOG_LEVEL=DEBUG
python3 claude_flow_service.py
```

### Log Analysis

Workflow execution logs are stored separately for each issue:
- `issue-{id}-{timestamp}.log` - Combined output
- `issue-{id}-{timestamp}.out` - Stdout only  
- `issue-{id}-{timestamp}.err` - Stderr only

## Contributing

When modifying the services:
1. Maintain backward compatibility
2. Add appropriate logging
3. Update tests
4. Update documentation
5. Follow existing code style

## License

This software is part of the Claude Flow integration system and follows the project's licensing terms.