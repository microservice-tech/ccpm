"""
Unit tests for the main Orchestrator class.
"""

import asyncio
import pytest
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from orchestrator import Orchestrator, WorkflowStatus, IssueContext


class TestOrchestrator:
    """Test suite for the Orchestrator class"""
    
    @pytest.fixture
    def temp_workspace(self):
        """Create temporary workspace for tests"""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    @pytest.fixture
    def orchestrator(self, temp_workspace):
        """Create orchestrator instance for tests"""
        return Orchestrator(
            base_workspace_dir=temp_workspace,
            max_concurrent_issues=2,
            cleanup_on_completion=False  # Disable for testing
        )
    
    def test_orchestrator_initialization(self, orchestrator, temp_workspace):
        """Test orchestrator proper initialization"""
        assert orchestrator.base_workspace_dir == Path(temp_workspace)
        assert orchestrator.max_concurrent_issues == 2
        assert orchestrator.cleanup_on_completion == False
        assert len(orchestrator.active_issues) == 0
        assert len(orchestrator.completed_issues) == 0
        assert orchestrator.workflow_manager is not None
        assert orchestrator.issue_handler is not None
    
    def test_base_workspace_creation(self, temp_workspace):
        """Test base workspace directory creation"""
        workspace_path = Path(temp_workspace) / "test_workspace"
        orchestrator = Orchestrator(base_workspace_dir=str(workspace_path))
        
        assert workspace_path.exists()
        assert workspace_path.is_dir()
    
    @pytest.mark.asyncio
    async def test_process_issue_success(self, orchestrator):
        """Test successful issue processing"""
        # Mock workflow manager
        orchestrator.workflow_manager.execute_workflow = AsyncMock(return_value=[])
        
        issue_id = "123"
        title = "Test Issue"
        body = "Test description"
        repo_url = "https://github.com/test/repo.git"
        
        context = await orchestrator.process_issue(issue_id, title, body, repo_url)
        
        assert context.issue_id == issue_id
        assert context.title == title
        assert context.body == body
        assert context.repo_url == repo_url
        assert context.status == WorkflowStatus.COMPLETED
        assert issue_id in orchestrator.completed_issues
        assert issue_id not in orchestrator.active_issues
    
    @pytest.mark.asyncio
    async def test_process_issue_failure(self, orchestrator):
        """Test issue processing failure handling"""
        # Mock workflow manager to raise exception
        orchestrator.workflow_manager.execute_workflow = AsyncMock(
            side_effect=RuntimeError("Test error")
        )
        
        issue_id = "124"
        title = "Test Issue"
        body = "Test description"  
        repo_url = "https://github.com/test/repo.git"
        
        with pytest.raises(RuntimeError):
            await orchestrator.process_issue(issue_id, title, body, repo_url)
        
        # Issue should not be in completed list
        assert issue_id not in orchestrator.completed_issues
        assert issue_id not in orchestrator.active_issues
    
    @pytest.mark.asyncio
    async def test_concurrent_issue_limit(self, orchestrator):
        """Test concurrent issue processing limit"""
        # Mock workflow manager to simulate long-running process
        orchestrator.workflow_manager.execute_workflow = AsyncMock(
            side_effect=lambda ctx: asyncio.sleep(0.1)
        )
        
        # Start processing issues up to limit
        tasks = []
        for i in range(3):  # More than max_concurrent_issues (2)
            task = asyncio.create_task(
                orchestrator.process_issue(
                    str(i), f"Issue {i}", "Description", "https://github.com/test/repo.git"
                )
            )
            tasks.append(task)
            
            # Small delay to ensure tasks start in order
            await asyncio.sleep(0.01)
        
        # Third task should fail due to capacity
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # At least one should fail with capacity error
        capacity_errors = [r for r in results if isinstance(r, RuntimeError) and "Maximum concurrent" in str(r)]
        assert len(capacity_errors) > 0
    
    @pytest.mark.asyncio 
    async def test_process_issues_batch(self, orchestrator):
        """Test batch processing of issues"""
        # Mock workflow manager
        orchestrator.workflow_manager.execute_workflow = AsyncMock(return_value=[])
        
        issues = [
            {'id': '1', 'title': 'Issue 1', 'body': 'Desc 1', 'repo_url': 'https://github.com/test/repo.git'},
            {'id': '2', 'title': 'Issue 2', 'body': 'Desc 2', 'repo_url': 'https://github.com/test/repo.git'},
        ]
        
        results = await orchestrator.process_issues_batch(issues)
        
        assert len(results) == 2
        assert '1' in orchestrator.completed_issues
        assert '2' in orchestrator.completed_issues
    
    @pytest.mark.asyncio
    async def test_process_issues_batch_with_failure(self, orchestrator):
        """Test batch processing with some failures"""
        def mock_workflow(context):
            if context.issue_id == '2':
                raise RuntimeError("Test error")
            return AsyncMock(return_value=[])()
        
        orchestrator.workflow_manager.execute_workflow = AsyncMock(side_effect=mock_workflow)
        
        issues = [
            {'id': '1', 'title': 'Issue 1', 'body': 'Desc 1', 'repo_url': 'https://github.com/test/repo.git'},
            {'id': '2', 'title': 'Issue 2', 'body': 'Desc 2', 'repo_url': 'https://github.com/test/repo.git'},
        ]
        
        results = await orchestrator.process_issues_batch(issues)
        
        assert len(results) == 2
        # First issue should succeed
        assert results[0].status == WorkflowStatus.COMPLETED
        # Second issue should fail
        assert results[1].status == WorkflowStatus.FAILED
    
    def test_get_status(self, orchestrator):
        """Test status reporting"""
        # Add some mock data
        orchestrator.active_issues['1'] = Mock()
        orchestrator.completed_issues.extend(['2', '3'])
        
        status = orchestrator.get_status()
        
        assert status['active_issues'] == 1
        assert status['completed_issues'] == 2
        assert status['max_concurrent'] == 2
        assert '1' in status['active_issue_ids']
        assert '2' in status['completed_issue_ids']
        assert '3' in status['completed_issue_ids']
        assert 'timestamp' in status
    
    @pytest.mark.asyncio
    async def test_shutdown(self, orchestrator):
        """Test graceful shutdown"""
        # Add some mock active issues
        context1 = IssueContext(
            issue_id='1',
            title='Test',
            body='Test',
            repo_url='https://github.com/test/repo.git',
            branch_name='feature/issue-1',
            workspace_path='/tmp/test',
            created_at=datetime.now()
        )
        orchestrator.active_issues['1'] = context1
        
        await orchestrator.shutdown()
        
        assert len(orchestrator.active_issues) == 0
        assert context1.status == WorkflowStatus.CANCELLED
    
    @pytest.mark.asyncio
    async def test_health_check_healthy(self, orchestrator):
        """Test health check when system is healthy"""
        health = await orchestrator.health_check()
        
        assert health['status'] == 'healthy'
        assert health['checks']['workspace'] == 'ok'
        assert 'timestamp' in health
        assert 'metrics' in health
    
    @pytest.mark.asyncio
    async def test_health_check_workspace_missing(self, temp_workspace):
        """Test health check when workspace is missing"""
        # Create orchestrator with non-existent workspace
        bad_workspace = Path(temp_workspace) / "nonexistent"
        orchestrator = Orchestrator(base_workspace_dir=str(bad_workspace))
        
        # Remove the workspace after initialization
        shutil.rmtree(bad_workspace, ignore_errors=True)
        
        health = await orchestrator.health_check()
        
        assert health['status'] == 'unhealthy'
        assert health['checks']['workspace'] == 'error'
    
    @pytest.mark.asyncio
    async def test_cleanup_workspace(self, orchestrator, temp_workspace):
        """Test workspace cleanup"""
        # Create a test workspace
        test_workspace = Path(temp_workspace) / "issue-123"
        test_workspace.mkdir(parents=True)
        test_file = test_workspace / "test.txt"
        test_file.write_text("test content")
        
        context = IssueContext(
            issue_id='123',
            title='Test',
            body='Test',
            repo_url='https://github.com/test/repo.git',
            branch_name='feature/issue-123',
            workspace_path=str(test_workspace),
            created_at=datetime.now()
        )
        
        await orchestrator._cleanup_workspace(context)
        
        assert not test_workspace.exists()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])