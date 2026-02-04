"""
Unit tests for the WorkflowManager class.
"""

import asyncio
import pytest
import tempfile
import shutil
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from workflow_manager import WorkflowManager, WorkflowStage, StageResult
from orchestrator import IssueContext, WorkflowStatus


class TestWorkflowManager:
    """Test suite for the WorkflowManager class"""
    
    @pytest.fixture
    def temp_workspace(self):
        """Create temporary workspace for tests"""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    @pytest.fixture
    def mock_orchestrator(self, temp_workspace):
        """Create mock orchestrator"""
        mock_orch = Mock()
        mock_orch.cleanup_on_completion = True
        mock_orch.base_workspace_dir = Path(temp_workspace)
        return mock_orch
    
    @pytest.fixture
    def workflow_manager(self, mock_orchestrator):
        """Create workflow manager instance"""
        return WorkflowManager(mock_orchestrator)
    
    @pytest.fixture
    def sample_context(self, temp_workspace):
        """Create sample issue context"""
        workspace_path = Path(temp_workspace) / "issue-123"
        return IssueContext(
            issue_id='123',
            title='Test Issue',
            body='Test description',
            repo_url='https://github.com/test/repo.git',
            branch_name='feature/issue-123',
            workspace_path=str(workspace_path),
            created_at=datetime.now()
        )
    
    def test_workflow_manager_initialization(self, workflow_manager, mock_orchestrator):
        """Test workflow manager initialization"""
        assert workflow_manager.orchestrator == mock_orchestrator
        assert len(workflow_manager.stage_timeouts) > 0
        assert len(workflow_manager.max_retries) > 0
        assert WorkflowStage.WORKSPACE_CREATION in workflow_manager.stage_timeouts
    
    @pytest.mark.asyncio
    async def test_create_workspace_success(self, workflow_manager, sample_context):
        """Test successful workspace creation"""
        result = await workflow_manager._create_workspace(sample_context)
        
        assert result['success'] == True
        assert 'Workspace created' in result['output']
        assert Path(sample_context.workspace_path).exists()
    
    @pytest.mark.asyncio 
    async def test_create_workspace_permission_error(self, workflow_manager, sample_context):
        """Test workspace creation with permission error"""
        # Use a path that will cause permission error
        sample_context.workspace_path = "/root/forbidden/path"
        
        result = await workflow_manager._create_workspace(sample_context)
        
        assert result['success'] == False
        assert 'Failed to create workspace' in result['error']
    
    @pytest.mark.asyncio
    async def test_clone_repository_success(self, workflow_manager, sample_context):
        """Test successful repository cloning"""
        # Create workspace first
        Path(sample_context.workspace_path).mkdir(parents=True, exist_ok=True)
        
        with patch('asyncio.create_subprocess_exec') as mock_subprocess:
            # Mock successful git clone
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b'', b'')
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process
            
            result = await workflow_manager._clone_repository(sample_context)
            
            assert result['success'] == True
            assert 'Repository cloned' in result['output']
            mock_subprocess.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_clone_repository_failure(self, workflow_manager, sample_context):
        """Test repository cloning failure"""
        # Create workspace first
        Path(sample_context.workspace_path).mkdir(parents=True, exist_ok=True)
        
        with patch('asyncio.create_subprocess_exec') as mock_subprocess:
            # Mock failed git clone
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b'', b'Authentication failed')
            mock_process.returncode = 1
            mock_subprocess.return_value = mock_process
            
            result = await workflow_manager._clone_repository(sample_context)
            
            assert result['success'] == False
            assert 'Git clone failed' in result['error']
    
    @pytest.mark.asyncio
    async def test_create_branch_success(self, workflow_manager, sample_context):
        """Test successful branch creation"""
        # Create workspace and repo directory
        workspace_path = Path(sample_context.workspace_path)
        repo_path = workspace_path / 'repo'
        repo_path.mkdir(parents=True, exist_ok=True)
        
        with patch('asyncio.create_subprocess_exec') as mock_subprocess:
            # Mock successful branch creation
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b'Switched to new branch', b'')
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process
            
            result = await workflow_manager._create_branch(sample_context)
            
            assert result['success'] == True
            assert 'Branch' in result['output']
            assert 'created' in result['output']
    
    @pytest.mark.asyncio
    async def test_install_claude_flow_success(self, workflow_manager, sample_context):
        """Test successful Claude Flow installation"""
        # Create workspace and repo directory
        workspace_path = Path(sample_context.workspace_path)
        repo_path = workspace_path / 'repo'
        repo_path.mkdir(parents=True, exist_ok=True)
        
        with patch('asyncio.create_subprocess_exec') as mock_subprocess:
            # Mock successful npm operations
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b'success', b'')
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process
            
            result = await workflow_manager._install_claude_flow(sample_context)
            
            assert result['success'] == True
            assert 'Claude Flow installed' in result['output']
            # Should be called for npm install and npx init
            assert mock_subprocess.call_count >= 2
    
    @pytest.mark.asyncio
    async def test_spawn_hive_mind_success(self, workflow_manager, sample_context):
        """Test successful hive-mind spawning"""
        # Create workspace and repo directory
        workspace_path = Path(sample_context.workspace_path)
        repo_path = workspace_path / 'repo'
        repo_path.mkdir(parents=True, exist_ok=True)
        
        with patch('asyncio.create_subprocess_exec') as mock_subprocess:
            # Mock successful hive-mind spawn
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b'Hive-mind spawned', b'')
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process
            
            result = await workflow_manager._spawn_hive_mind(sample_context)
            
            assert result['success'] == True
            assert 'Hive-mind spawned successfully' in result['output']
    
    @pytest.mark.asyncio
    async def test_monitor_implementation_completion(self, workflow_manager, sample_context):
        """Test implementation monitoring with completion"""
        # Create workspace structure
        workspace_path = Path(sample_context.workspace_path)
        repo_path = workspace_path / 'repo'
        swarm_path = repo_path / '.swarm'
        swarm_path.mkdir(parents=True, exist_ok=True)
        
        # Create session file with completed status
        session_file = swarm_path / 'session.json'
        session_data = {'status': 'completed'}
        with open(session_file, 'w') as f:
            json.dump(session_data, f)
        
        result = await workflow_manager._monitor_implementation(sample_context)
        
        assert result['success'] == True
        assert 'Implementation completed' in result['output']
    
    @pytest.mark.asyncio
    async def test_monitor_implementation_timeout(self, workflow_manager, sample_context):
        """Test implementation monitoring timeout"""
        # Create workspace structure without completion
        workspace_path = Path(sample_context.workspace_path)
        repo_path = workspace_path / 'repo'
        repo_path.mkdir(parents=True, exist_ok=True)
        
        # Reduce timeout for test
        workflow_manager.stage_timeouts[WorkflowStage.IMPLEMENTATION_MONITOR] = 0.1
        
        result = await workflow_manager._monitor_implementation(sample_context)
        
        assert result['success'] == False
        assert 'timed out' in result['error']
    
    @pytest.mark.asyncio
    async def test_create_pull_request_success(self, workflow_manager, sample_context):
        """Test successful PR creation"""
        # Create workspace and repo directory
        workspace_path = Path(sample_context.workspace_path)
        repo_path = workspace_path / 'repo'
        repo_path.mkdir(parents=True, exist_ok=True)
        
        with patch('asyncio.create_subprocess_exec') as mock_subprocess:
            # Mock successful git operations and PR creation
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b'success', b'')
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process
            
            result = await workflow_manager._create_pull_request(sample_context)
            
            assert result['success'] == True
            assert 'PR created successfully' in result['output']
            # Should be called for git add, commit, push, and gh pr create
            assert mock_subprocess.call_count == 4
    
    @pytest.mark.asyncio
    async def test_cleanup_workspace_success(self, workflow_manager, sample_context):
        """Test successful workspace cleanup"""
        # Create workspace with some files
        workspace_path = Path(sample_context.workspace_path)
        workspace_path.mkdir(parents=True, exist_ok=True)
        (workspace_path / 'test.txt').write_text('test')
        
        result = await workflow_manager._cleanup_workspace(sample_context)
        
        assert result['success'] == True
        assert 'Workspace cleaned up' in result['output']
        assert not workspace_path.exists()
    
    @pytest.mark.asyncio
    async def test_execute_stage_with_retry_success_first_attempt(self, workflow_manager, sample_context):
        """Test stage execution succeeding on first attempt"""
        async def mock_stage_func(context):
            return {'success': True, 'output': 'Success'}
        
        result = await workflow_manager._execute_stage_with_retry(
            WorkflowStage.WORKSPACE_CREATION, mock_stage_func, sample_context
        )
        
        assert result.success == True
        assert result.stage == WorkflowStage.WORKSPACE_CREATION
        assert 'Success' in result.output
    
    @pytest.mark.asyncio
    async def test_execute_stage_with_retry_success_after_retry(self, workflow_manager, sample_context):
        """Test stage execution succeeding after retry"""
        attempt_count = 0
        
        async def mock_stage_func(context):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count == 1:
                return {'success': False, 'error': 'Temporary failure'}
            return {'success': True, 'output': 'Success on retry'}
        
        result = await workflow_manager._execute_stage_with_retry(
            WorkflowStage.WORKSPACE_CREATION, mock_stage_func, sample_context
        )
        
        assert result.success == True
        assert attempt_count == 2
        assert 'Success on retry' in result.output
    
    @pytest.mark.asyncio
    async def test_execute_stage_with_retry_max_attempts(self, workflow_manager, sample_context):
        """Test stage execution failing after max attempts"""
        async def mock_stage_func(context):
            return {'success': False, 'error': 'Persistent failure'}
        
        result = await workflow_manager._execute_stage_with_retry(
            WorkflowStage.WORKSPACE_CREATION, mock_stage_func, sample_context
        )
        
        assert result.success == False
        assert 'Persistent failure' in result.error
    
    @pytest.mark.asyncio
    async def test_execute_stage_with_timeout(self, workflow_manager, sample_context):
        """Test stage execution timeout"""
        async def slow_stage_func(context):
            await asyncio.sleep(1)  # Longer than timeout
            return {'success': True, 'output': 'Should not reach here'}
        
        # Set very short timeout
        workflow_manager.stage_timeouts[WorkflowStage.WORKSPACE_CREATION] = 0.1
        
        result = await workflow_manager._execute_stage_with_retry(
            WorkflowStage.WORKSPACE_CREATION, slow_stage_func, sample_context
        )
        
        assert result.success == False
        assert 'timeout' in result.error
    
    @pytest.mark.asyncio
    async def test_execute_workflow_success(self, workflow_manager, sample_context):
        """Test complete workflow execution success"""
        # Mock all stage functions to succeed
        with patch.object(workflow_manager, '_create_workspace', return_value={'success': True, 'output': 'OK'}), \
             patch.object(workflow_manager, '_clone_repository', return_value={'success': True, 'output': 'OK'}), \
             patch.object(workflow_manager, '_create_branch', return_value={'success': True, 'output': 'OK'}), \
             patch.object(workflow_manager, '_install_claude_flow', return_value={'success': True, 'output': 'OK'}), \
             patch.object(workflow_manager, '_spawn_hive_mind', return_value={'success': True, 'output': 'OK'}), \
             patch.object(workflow_manager, '_monitor_implementation', return_value={'success': True, 'output': 'OK'}), \
             patch.object(workflow_manager, '_create_pull_request', return_value={'success': True, 'output': 'OK'}), \
             patch.object(workflow_manager, '_cleanup_workspace', return_value={'success': True, 'output': 'OK'}):
            
            results = await workflow_manager.execute_workflow(sample_context)
            
            # Should have 7 main stages + cleanup
            assert len(results) >= 7
            assert all(result.success for result in results[:-1])  # All except possibly cleanup
    
    @pytest.mark.asyncio
    async def test_execute_workflow_critical_failure(self, workflow_manager, sample_context):
        """Test workflow execution with critical stage failure"""
        # Mock workspace creation to fail (critical stage)
        with patch.object(workflow_manager, '_create_workspace', return_value={'success': False, 'error': 'Critical error'}):
            
            with pytest.raises(RuntimeError) as excinfo:
                await workflow_manager.execute_workflow(sample_context)
            
            assert 'Critical stage failed' in str(excinfo.value)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])