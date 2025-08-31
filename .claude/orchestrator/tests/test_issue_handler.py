"""
Unit tests for the IssueHandler class.
"""

import pytest
import json
import tempfile
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, AsyncMock

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from issue_handler import IssueHandler, IssueMetadata, IssueState, IssuePriority


class TestIssueHandler:
    """Test suite for the IssueHandler class"""
    
    @pytest.fixture
    def mock_orchestrator(self):
        """Create mock orchestrator"""
        mock_orch = Mock()
        mock_orch.process_issues_batch = AsyncMock(return_value=[])
        return mock_orch
    
    @pytest.fixture
    def issue_handler(self, mock_orchestrator):
        """Create issue handler instance"""
        return IssueHandler(mock_orchestrator)
    
    @pytest.fixture
    def sample_github_issue(self):
        """Sample GitHub issue data"""
        return {
            'number': 123,
            'title': 'Test Issue',
            'body': 'This is a test issue description',
            'labels': [
                {'name': 'ready-for-implementation'},
                {'name': 'bug'},
                {'name': 'priority-high'}
            ],
            'assignees': [
                {'login': 'testuser'}
            ],
            'created_at': '2023-01-01T00:00:00Z',
            'updated_at': '2023-01-01T12:00:00Z',
            'html_url': 'https://github.com/test/repo/issues/123'
        }
    
    def test_issue_handler_initialization(self, issue_handler, mock_orchestrator):
        """Test issue handler initialization"""
        assert issue_handler.orchestrator == mock_orchestrator
        assert len(issue_handler.discovered_issues) == 0
        assert len(issue_handler.processed_issues) == 0
        assert len(issue_handler.failed_issues) == 0
        assert issue_handler.ready_label == "ready-for-implementation"
        assert issue_handler.max_processing_attempts == 3
    
    def test_parse_github_issue_success(self, issue_handler, sample_github_issue):
        """Test successful GitHub issue parsing"""
        metadata = issue_handler.parse_github_issue(sample_github_issue)
        
        assert metadata is not None
        assert metadata.issue_id == '123'
        assert metadata.title == 'Test Issue'
        assert metadata.body == 'This is a test issue description'
        assert 'ready-for-implementation' in metadata.labels
        assert 'bug' in metadata.labels
        assert 'priority-high' in metadata.labels
        assert 'testuser' in metadata.assignees
        assert metadata.priority == IssuePriority.HIGH
        assert metadata.state == IssueState.DISCOVERED
    
    def test_parse_github_issue_with_repository(self, issue_handler, sample_github_issue):
        """Test GitHub issue parsing with repository info"""
        sample_github_issue['repository'] = {
            'clone_url': 'https://github.com/test/repo.git'
        }
        
        metadata = issue_handler.parse_github_issue(sample_github_issue)
        
        assert metadata.repo_url == 'https://github.com/test/repo.git'
    
    def test_parse_github_issue_extract_repo_from_url(self, issue_handler, sample_github_issue):
        """Test repo URL extraction from issue URL"""
        metadata = issue_handler.parse_github_issue(sample_github_issue)
        
        assert metadata.repo_url == 'https://github.com/test/repo.git'
    
    def test_parse_github_issue_invalid_data(self, issue_handler):
        """Test parsing invalid GitHub issue data"""
        invalid_issue = {'invalid': 'data'}
        
        metadata = issue_handler.parse_github_issue(invalid_issue)
        
        assert metadata is None
    
    def test_determine_priority_from_labels(self, issue_handler):
        """Test priority determination from labels"""
        # Test explicit priority labels
        assert issue_handler._determine_priority(['priority-critical']) == IssuePriority.CRITICAL
        assert issue_handler._determine_priority(['priority-high']) == IssuePriority.HIGH
        assert issue_handler._determine_priority(['priority-low']) == IssuePriority.LOW
        
        # Test implicit priority from bug/security labels
        assert issue_handler._determine_priority(['bug']) == IssuePriority.HIGH
        assert issue_handler._determine_priority(['security']) == IssuePriority.HIGH
        assert issue_handler._determine_priority(['urgent']) == IssuePriority.CRITICAL
        
        # Test enhancement/feature labels
        assert issue_handler._determine_priority(['enhancement']) == IssuePriority.LOW
        assert issue_handler._determine_priority(['feature-request']) == IssuePriority.LOW
        
        # Test default priority
        assert issue_handler._determine_priority(['other']) == IssuePriority.NORMAL
    
    def test_estimate_effort_from_labels(self, issue_handler):
        """Test effort estimation from labels"""
        # Test explicit effort labels
        assert issue_handler._estimate_effort(['effort-s'], '') == 'S'
        assert issue_handler._estimate_effort(['effort-m'], '') == 'M'
        assert issue_handler._estimate_effort(['effort-l'], '') == 'L'
        assert issue_handler._estimate_effort(['effort-xl'], '') == 'XL'
        
        # Test size labels
        assert issue_handler._estimate_effort(['size-small'], '') == 'S'
        assert issue_handler._estimate_effort(['size-large'], '') == 'L'
    
    def test_estimate_effort_from_body(self, issue_handler):
        """Test effort estimation from issue body content"""
        # Test heuristic based on content length
        short_body = "Short description"
        medium_body = "A" * 800  # Medium length
        long_body = "A" * 2500   # Long content
        
        assert issue_handler._estimate_effort([], short_body) == 'S'
        assert issue_handler._estimate_effort([], medium_body) == 'M'
        assert issue_handler._estimate_effort([], long_body) == 'L'
    
    def test_is_ready_for_processing_success(self, issue_handler):
        """Test issue readiness check - success case"""
        metadata = IssueMetadata(
            issue_id='123',
            title='Test',
            body='Test',
            labels=['ready-for-implementation'],
            assignees=[],
            repo_url='https://github.com/test/repo.git',
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        
        assert issue_handler.is_ready_for_processing(metadata) == True
    
    def test_is_ready_for_processing_no_ready_label(self, issue_handler):
        """Test issue readiness check - missing ready label"""
        metadata = IssueMetadata(
            issue_id='123',
            title='Test',
            body='Test',
            labels=['bug'],
            assignees=[],
            repo_url='https://github.com/test/repo.git',
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        
        assert issue_handler.is_ready_for_processing(metadata) == False
    
    def test_is_ready_for_processing_already_processed(self, issue_handler):
        """Test issue readiness check - already processed"""
        metadata = IssueMetadata(
            issue_id='123',
            title='Test',
            body='Test',
            labels=['ready-for-implementation'],
            assignees=[],
            repo_url='https://github.com/test/repo.git',
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        
        issue_handler.processed_issues.add('123')
        
        assert issue_handler.is_ready_for_processing(metadata) == False
    
    def test_is_ready_for_processing_max_attempts_exceeded(self, issue_handler):
        """Test issue readiness check - max attempts exceeded"""
        metadata = IssueMetadata(
            issue_id='123',
            title='Test',
            body='Test',
            labels=['ready-for-implementation'],
            assignees=[],
            repo_url='https://github.com/test/repo.git',
            created_at=datetime.now(),
            updated_at=datetime.now(),
            processing_attempts=5
        )
        
        assert issue_handler.is_ready_for_processing(metadata) == False
    
    def test_is_ready_for_processing_retry_delay(self, issue_handler):
        """Test issue readiness check - retry delay not met"""
        metadata = IssueMetadata(
            issue_id='123',
            title='Test',
            body='Test',
            labels=['ready-for-implementation'],
            assignees=[],
            repo_url='https://github.com/test/repo.git',
            created_at=datetime.now(),
            updated_at=datetime.now(),
            state=IssueState.FAILED,
            last_processed=datetime.now() - timedelta(minutes=30)  # Less than retry_delay_hours
        )
        
        assert issue_handler.is_ready_for_processing(metadata) == False
    
    def test_is_ready_for_processing_no_repo_url(self, issue_handler):
        """Test issue readiness check - missing repo URL"""
        metadata = IssueMetadata(
            issue_id='123',
            title='Test',
            body='Test',
            labels=['ready-for-implementation'],
            assignees=[],
            repo_url=None,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        
        assert issue_handler.is_ready_for_processing(metadata) == False
    
    def test_filter_and_prioritize_issues(self, issue_handler):
        """Test issue filtering and prioritization"""
        issues = [
            IssueMetadata(
                issue_id='1',
                title='Low Priority',
                body='Test',
                labels=['ready-for-implementation'],
                assignees=[],
                repo_url='https://github.com/test/repo.git',
                created_at=datetime.now() - timedelta(days=2),
                updated_at=datetime.now(),
                priority=IssuePriority.LOW
            ),
            IssueMetadata(
                issue_id='2',
                title='High Priority',
                body='Test',
                labels=['ready-for-implementation'],
                assignees=[],
                repo_url='https://github.com/test/repo.git',
                created_at=datetime.now() - timedelta(days=1),
                updated_at=datetime.now(),
                priority=IssuePriority.HIGH
            ),
            IssueMetadata(
                issue_id='3',
                title='Not Ready',
                body='Test',
                labels=['bug'],  # Missing ready label
                assignees=[],
                repo_url='https://github.com/test/repo.git',
                created_at=datetime.now(),
                updated_at=datetime.now(),
                priority=IssuePriority.CRITICAL
            )
        ]
        
        filtered = issue_handler.filter_and_prioritize_issues(issues)
        
        assert len(filtered) == 2  # Issue 3 should be filtered out
        assert filtered[0].issue_id == '2'  # High priority first
        assert filtered[1].issue_id == '1'  # Low priority second
    
    @pytest.mark.asyncio
    async def test_process_issues_batch_success(self, issue_handler, sample_github_issue):
        """Test successful batch processing"""
        # Mock successful orchestrator processing
        mock_result = Mock()
        mock_result.status.value = 'completed'
        issue_handler.orchestrator.process_issues_batch = AsyncMock(return_value=[mock_result])
        
        github_issues = [sample_github_issue]
        
        results = await issue_handler.process_issues_batch(github_issues)
        
        assert len(results) == 1
        assert '123' in issue_handler.processed_issues
        assert '123' in issue_handler.discovered_issues
        assert issue_handler.discovered_issues['123'].state == IssueState.COMPLETED
    
    @pytest.mark.asyncio
    async def test_process_issues_batch_no_ready_issues(self, issue_handler, sample_github_issue):
        """Test batch processing with no ready issues"""
        # Remove ready label
        sample_github_issue['labels'] = [{'name': 'bug'}]
        
        github_issues = [sample_github_issue]
        
        results = await issue_handler.process_issues_batch(github_issues)
        
        assert len(results) == 0
        assert '123' not in issue_handler.processed_issues
    
    @pytest.mark.asyncio
    async def test_process_issues_batch_failure(self, issue_handler, sample_github_issue):
        """Test batch processing with orchestrator failure"""
        # Mock orchestrator to raise exception
        issue_handler.orchestrator.process_issues_batch = AsyncMock(
            side_effect=RuntimeError("Processing failed")
        )
        
        github_issues = [sample_github_issue]
        
        with pytest.raises(RuntimeError):
            await issue_handler.process_issues_batch(github_issues)
        
        assert '123' in issue_handler.failed_issues
        assert issue_handler.discovered_issues['123'].state == IssueState.FAILED
    
    def test_get_processing_statistics(self, issue_handler):
        """Test processing statistics generation"""
        # Add some mock data
        issue_handler.discovered_issues = {
            '1': IssueMetadata('1', 'Test', 'Test', [], [], 'url', datetime.now(), datetime.now(), 
                              priority=IssuePriority.HIGH, state=IssueState.COMPLETED, processing_attempts=1),
            '2': IssueMetadata('2', 'Test', 'Test', [], [], 'url', datetime.now(), datetime.now(),
                              priority=IssuePriority.LOW, state=IssueState.FAILED, processing_attempts=2)
        }
        issue_handler.processed_issues.add('1')
        issue_handler.failed_issues['2'] = issue_handler.discovered_issues['2']
        
        stats = issue_handler.get_processing_statistics()
        
        assert stats['total_discovered'] == 2
        assert stats['total_processed'] == 1
        assert stats['total_failed'] == 1
        assert stats['states']['completed'] == 1
        assert stats['states']['failed'] == 1
        assert stats['priorities']['high'] == 1
        assert stats['priorities']['low'] == 1
        assert stats['success_rate'] == 33.33  # 1/3 * 100
        assert stats['total_attempts'] == 3
    
    def test_get_failed_issues_report(self, issue_handler):
        """Test failed issues report generation"""
        failed_metadata = IssueMetadata(
            '1', 'Failed Issue', 'Test', [], [], 'url', 
            datetime.now(), datetime.now(),
            state=IssueState.FAILED,
            processing_attempts=1,
            last_processed=datetime.now() - timedelta(hours=2),
            error_message='Test error'
        )
        
        issue_handler.failed_issues['1'] = failed_metadata
        
        report = issue_handler.get_failed_issues_report()
        
        assert report['total_failed'] == 1
        assert 'Test error' in report['failed_by_error']
        assert len(report['failed_by_error']['Test error']) == 1
        assert '1' in report['retry_candidates']
    
    def test_reset_issue_state(self, issue_handler):
        """Test issue state reset for retry"""
        metadata = IssueMetadata(
            '1', 'Test', 'Test', [], [], 'url',
            datetime.now(), datetime.now(),
            state=IssueState.FAILED,
            processing_attempts=2,
            error_message='Test error'
        )
        
        issue_handler.discovered_issues['1'] = metadata
        issue_handler.failed_issues['1'] = metadata
        issue_handler.processed_issues.add('1')
        
        success = issue_handler.reset_issue_state('1')
        
        assert success == True
        assert metadata.state == IssueState.DISCOVERED
        assert metadata.processing_attempts == 0
        assert metadata.error_message is None
        assert '1' not in issue_handler.failed_issues
        assert '1' not in issue_handler.processed_issues
    
    def test_reset_issue_state_not_found(self, issue_handler):
        """Test issue state reset for non-existent issue"""
        success = issue_handler.reset_issue_state('nonexistent')
        
        assert success == False
    
    def test_export_issue_data(self, issue_handler):
        """Test issue data export"""
        # Add some test data
        metadata = IssueMetadata(
            '1', 'Test', 'Test', [], [], 'url',
            datetime.now(), datetime.now()
        )
        issue_handler.discovered_issues['1'] = metadata
        issue_handler.processed_issues.add('1')
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            temp_path = f.name
        
        try:
            issue_handler.export_issue_data(temp_path)
            
            # Verify export file was created and contains data
            with open(temp_path, 'r') as f:
                export_data = json.load(f)
            
            assert 'discovered_issues' in export_data
            assert 'processed_issues' in export_data
            assert 'statistics' in export_data
            assert '1' in export_data['discovered_issues']
            assert '1' in export_data['processed_issues']
        
        finally:
            os.unlink(temp_path)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])