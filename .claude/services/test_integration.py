#!/usr/bin/env python3
"""
Test script to verify the integration between the Claude Flow service and workflow-template.sh
"""

import os
import sys
import tempfile
from pathlib import Path

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from workflow_executor import WorkflowExecutor


def test_workflow_executor():
    """Test the workflow executor with dry-run mode."""
    print("Testing workflow executor integration...")
    
    # Path to workflow script
    script_path = Path("/home/nic/Documents/development/epic-claude-flow-integration/.claude/scripts/workflow-template.sh")
    
    if not script_path.exists():
        print(f"❌ Workflow script not found: {script_path}")
        return False
    
    print(f"✓ Workflow script found: {script_path}")
    
    # Create temporary log directory
    with tempfile.TemporaryDirectory() as temp_dir:
        log_dir = Path(temp_dir) / "logs"
        
        # Initialize workflow executor
        try:
            executor = WorkflowExecutor(
                script_path=script_path,
                log_directory=log_dir,
                timeout=60,  # Short timeout for testing
                environment={
                    'ANTHROPIC_API_KEY': 'test-key-not-real'
                }
            )
            print("✓ Workflow executor initialized")
        except Exception as e:
            print(f"❌ Failed to initialize workflow executor: {e}")
            return False
        
        # Test dry-run execution
        try:
            success, pr_url, error_msg = executor.execute_workflow(
                issue_id=999,
                issue_title="Test Issue for Integration",
                repo_url="https://github.com/test/repo.git",
                dry_run=True
            )
            
            if success:
                print("✓ Dry-run execution completed successfully")
                return True
            else:
                print(f"❌ Dry-run execution failed: {error_msg}")
                return False
                
        except Exception as e:
            print(f"❌ Exception during dry-run execution: {e}")
            return False


def test_service_configuration():
    """Test service configuration loading."""
    print("\nTesting service configuration...")
    
    try:
        # Test environment-based configuration
        os.environ['GITHUB_TOKEN'] = 'test-token'
        os.environ['REPOSITORIES'] = '[{"owner":"test","repo":"repo","url":"https://github.com/test/repo.git"}]'
        
        from claude_flow_service import ServiceConfiguration
        
        config = ServiceConfiguration.from_environment()
        print("✓ Configuration loaded from environment")
        
        # Validate some key fields
        if config.github_token == 'test-token':
            print("✓ GitHub token loaded correctly")
        else:
            print("❌ GitHub token not loaded correctly")
            return False
        
        if len(config.repositories) == 1:
            print("✓ Repository configuration loaded correctly")
        else:
            print("❌ Repository configuration not loaded correctly")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ Configuration test failed: {e}")
        return False
    
    finally:
        # Clean up environment
        os.environ.pop('GITHUB_TOKEN', None)
        os.environ.pop('REPOSITORIES', None)


def test_imports():
    """Test that all modules can be imported."""
    print("\nTesting module imports...")
    
    try:
        from github_client import GitHubClient
        print("✓ GitHubClient imported")
        
        from issue_processor import IssueProcessor
        print("✓ IssueProcessor imported")
        
        from workflow_executor import WorkflowExecutor
        print("✓ WorkflowExecutor imported")
        
        from claude_flow_service import ClaudeFlowService
        print("✓ ClaudeFlowService imported")
        
        return True
        
    except Exception as e:
        print(f"❌ Import test failed: {e}")
        return False


def main():
    """Run all integration tests."""
    print("Claude Flow Service Integration Test")
    print("=" * 50)
    
    tests = [
        ("Module Imports", test_imports),
        ("Service Configuration", test_service_configuration),
        ("Workflow Executor", test_workflow_executor),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ Test '{test_name}' crashed: {e}")
            results.append((test_name, False))
    
    print("\n" + "=" * 50)
    print("Test Results:")
    print("=" * 50)
    
    all_passed = True
    for test_name, passed in results:
        status = "✓ PASS" if passed else "❌ FAIL"
        print(f"{test_name}: {status}")
        if not passed:
            all_passed = False
    
    print("=" * 50)
    if all_passed:
        print("🎉 All tests passed!")
        sys.exit(0)
    else:
        print("❌ Some tests failed")
        sys.exit(1)


if __name__ == '__main__':
    main()