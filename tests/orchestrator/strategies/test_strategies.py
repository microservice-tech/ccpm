"""Unit tests for execution strategies."""

import asyncio
import pytest
import time
import tempfile
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from typing import List, Dict, Any

# Import strategies and base classes
import sys
sys.path.insert(0, '/home/nic/Documents/development/epic-claude-flow-integration')

from ...claude.orchestrator.strategies import (
    ExecutionStrategy,
    ExecutionStatus,
    IssueContext,
    ExecutionResult,
    SequentialExecutionStrategy,
    ParallelExecutionStrategy,
    PriorityExecutionStrategy,
    PriorityLevel,
    create_strategy,
    get_available_strategies,
    recommend_strategy
)


class MockResourceManager:
    """Mock resource manager for testing."""
    
    def __init__(self, capacity: Dict[str, Any] = None):
        self.capacity = capacity or {"available_slots": 5, "cpu_cores": 4, "memory_mb": 2048}
        self.allocations = {}
    
    async def acquire_resources(self, issue_id: str, requirements: Dict[str, Any]) -> bool:
        self.allocations[issue_id] = requirements
        return True
    
    async def release_resources(self, issue_id: str) -> None:
        self.allocations.pop(issue_id, None)
    
    def get_available_capacity(self) -> Dict[str, Any]:
        return self.capacity.copy()


class TestIssueContext:
    """Test IssueContext dataclass."""
    
    def test_issue_context_creation(self):
        issue = IssueContext(
            issue_id="123",
            title="Test Issue",
            body="Test body",
            priority=5
        )
        
        assert issue.issue_id == "123"
        assert issue.title == "Test Issue"
        assert issue.body == "Test body"
        assert issue.priority == 5
        assert issue.dependencies == []
        assert issue.status == ExecutionStatus.PENDING
        assert issue.created_at is not None
    
    def test_issue_context_with_dependencies(self):
        issue = IssueContext(
            issue_id="456",
            title="Dependent Issue",
            body="Depends on other issues",
            dependencies=["123", "789"]
        )
        
        assert issue.dependencies == ["123", "789"]


class TestExecutionResult:
    """Test ExecutionResult dataclass."""
    
    def test_execution_result_success(self):
        result = ExecutionResult(
            issue_id="123",
            status=ExecutionStatus.COMPLETED,
            success=True,
            message="Completed successfully",
            duration=10.5,
            pr_url="https://github.com/repo/pull/123"
        )
        
        assert result.issue_id == "123"
        assert result.status == ExecutionStatus.COMPLETED
        assert result.success is True
        assert result.message == "Completed successfully"
        assert result.duration == 10.5
        assert result.pr_url == "https://github.com/repo/pull/123"
        assert result.error_details == {}
    
    def test_execution_result_failure(self):
        result = ExecutionResult(
            issue_id="456",
            status=ExecutionStatus.FAILED,
            success=False,
            message="Failed to execute",
            error_details={"error": "Connection timeout", "type": "TimeoutError"}
        )
        
        assert result.success is False
        assert result.status == ExecutionStatus.FAILED
        assert result.error_details["error"] == "Connection timeout"


class TestSequentialExecutionStrategy:
    """Test sequential execution strategy."""
    
    @pytest.fixture
    def strategy(self):
        return SequentialExecutionStrategy(
            repository_url="https://github.com/test/repo.git"
        )
    
    @pytest.fixture
    def mock_issues(self):
        return [
            IssueContext(issue_id="1", title="First Issue", body="First", created_at=1.0),
            IssueContext(issue_id="2", title="Second Issue", body="Second", created_at=2.0),
            IssueContext(issue_id="3", title="Third Issue", body="Third", created_at=3.0)
        ]
    
    def test_get_execution_order(self, strategy, mock_issues):
        ordered = strategy.get_execution_order(mock_issues)
        
        # Should be ordered by creation time (FIFO)
        assert len(ordered) == 3
        assert ordered[0].issue_id == "1"
        assert ordered[1].issue_id == "2" 
        assert ordered[2].issue_id == "3"
    
    @pytest.mark.asyncio
    async def test_select_next_issues_single(self, strategy, mock_issues):
        selected = await strategy.select_next_issues(mock_issues, current_capacity=1)
        
        # Sequential should only select one issue
        assert len(selected) == 1
        assert selected[0].issue_id == "1"  # First in order
    
    @pytest.mark.asyncio
    async def test_select_next_issues_capacity_zero(self, strategy, mock_issues):
        selected = await strategy.select_next_issues(mock_issues, current_capacity=0)
        assert len(selected) == 0
    
    @pytest.mark.asyncio
    async def test_add_and_get_status(self, strategy):
        issue = IssueContext(issue_id="test", title="Test", body="Test body")
        
        await strategy.add_issue(issue)
        status = await strategy.get_status("test")
        
        assert status == ExecutionStatus.PENDING
    
    @pytest.mark.asyncio 
    async def test_remove_issue(self, strategy):
        issue = IssueContext(issue_id="remove_test", title="Test", body="Test body")
        
        await strategy.add_issue(issue)
        removed = await strategy.remove_issue("remove_test")
        
        assert removed is True
        
        status = await strategy.get_status("remove_test")
        assert status is None  # Should not be found
    
    def test_format_issue_prompt(self, strategy):
        issue = IssueContext(
            issue_id="prompt_test",
            title="Prompt Test Issue", 
            body="This is a test issue for prompt formatting",
            priority=7,
            dependencies=["dep1", "dep2"]
        )
        
        prompt = strategy._format_issue_prompt(issue)
        
        assert "Issue #prompt_test: Prompt Test Issue" in prompt
        assert "This is a test issue for prompt formatting" in prompt
        assert "Priority Level" in prompt
        assert "7" in prompt
        assert "dep1, dep2" in prompt
    
    def test_metrics(self, strategy):
        metrics = strategy.get_metrics()
        
        assert "total_issues" in metrics
        assert "completed" in metrics
        assert "failed" in metrics
        assert "running" in metrics
        assert "pending" in metrics
        assert "success_rate" in metrics


class TestParallelExecutionStrategy:
    """Test parallel execution strategy."""
    
    @pytest.fixture
    def strategy(self):
        return ParallelExecutionStrategy(
            max_concurrent=3,
            repository_url="https://github.com/test/repo.git"
        )
    
    @pytest.fixture
    def mock_issues(self):
        return [
            IssueContext(issue_id="p1", title="Parallel 1", body="First", priority=5),
            IssueContext(issue_id="p2", title="Parallel 2", body="Second", priority=3),
            IssueContext(issue_id="p3", title="Parallel 3", body="Third", priority=8),
            IssueContext(issue_id="p4", title="Parallel 4", body="Fourth", priority=6)
        ]
    
    def test_get_execution_order_priority(self, strategy, mock_issues):
        ordered = strategy.get_execution_order(mock_issues)
        
        # Should be ordered by priority (descending), then creation time
        assert len(ordered) == 4
        assert ordered[0].issue_id == "p3"  # Highest priority (8)
        assert ordered[1].issue_id == "p4"  # Priority 6
        assert ordered[2].issue_id == "p1"  # Priority 5
        assert ordered[3].issue_id == "p2"  # Priority 3
    
    @pytest.mark.asyncio
    async def test_select_next_issues_multiple(self, strategy, mock_issues):
        selected = await strategy.select_next_issues(mock_issues, current_capacity=3)
        
        # Should select up to capacity
        assert len(selected) <= 3
        assert len(selected) > 0
    
    @pytest.mark.asyncio
    async def test_dependency_resolution(self, strategy):
        # Create issues with dependencies
        issues = [
            IssueContext(issue_id="dep1", title="Independent", body="No deps", dependencies=[]),
            IssueContext(issue_id="dep2", title="Dependent", body="Has deps", dependencies=["dep1"])
        ]
        
        # Build dependency graph
        await strategy._build_dependency_graph(issues)
        
        # dep1 should be ready (no dependencies)
        ready1 = await strategy._are_dependencies_satisfied(issues[0])
        assert ready1 is True
        
        # dep2 should not be ready (depends on dep1)
        ready2 = await strategy._are_dependencies_satisfied(issues[1])
        assert ready2 is False
    
    @pytest.mark.asyncio
    async def test_parallel_metrics(self, strategy):
        metrics = await strategy.get_parallel_metrics()
        
        assert "max_concurrent" in metrics
        assert "currently_running" in metrics
        assert "dependencies_satisfied" in metrics
        assert "semaphore_available" in metrics
        assert "resource_utilization" in metrics


class TestPriorityExecutionStrategy:
    """Test priority-based execution strategy."""
    
    @pytest.fixture
    def strategy(self):
        return PriorityExecutionStrategy(
            max_concurrent=2,
            repository_url="https://github.com/test/repo.git",
            priority_boost_threshold=1.0,  # 1 second for testing
            starvation_prevention=True
        )
    
    @pytest.fixture
    def mock_priority_issues(self):
        current_time = time.time()
        return [
            IssueContext(issue_id="pr1", title="Low Priority", body="Low", priority=2, created_at=current_time - 2.0),
            IssueContext(issue_id="pr2", title="High Priority", body="High", priority=8, created_at=current_time),
            IssueContext(issue_id="pr3", title="Critical", body="Critical", priority=10, created_at=current_time),
            IssueContext(issue_id="pr4", title="Old Low", body="Old", priority=1, created_at=current_time - 10.0)
        ]
    
    def test_priority_levels(self):
        assert PriorityLevel.CRITICAL.value == 10
        assert PriorityLevel.HIGH.value == 8
        assert PriorityLevel.MEDIUM.value == 5
        assert PriorityLevel.LOW.value == 2
        assert PriorityLevel.DEFERRED.value == 0
    
    def test_priority_boost(self, strategy):
        current_time = time.time()
        old_issue = IssueContext(
            issue_id="old",
            title="Old Issue",
            body="Very old issue",
            priority=2,
            created_at=current_time - 5.0  # 5 seconds ago
        )
        
        boosted = strategy._apply_priority_boost(old_issue)
        assert boosted > old_issue.priority  # Should be boosted
    
    def test_get_priority_name(self, strategy):
        assert strategy._get_priority_name(10) == "critical"
        assert strategy._get_priority_name(8) == "high"
        assert strategy._get_priority_name(5) == "medium"
        assert strategy._get_priority_name(2) == "low"
        assert strategy._get_priority_name(0) == "deferred"
    
    @pytest.mark.asyncio
    async def test_priority_queue_ordering(self, strategy, mock_priority_issues):
        # Add all issues to priority queue
        for issue in mock_priority_issues:
            await strategy.add_issue(issue)
        
        # The priority queue should order by priority (high first)
        selected = await strategy.select_next_issues(mock_priority_issues, current_capacity=2)
        
        assert len(selected) <= 2
        if len(selected) > 1:
            # First should be higher or equal priority than second
            assert selected[0].priority >= selected[1].priority
    
    def test_get_priority_resource_requirements(self, strategy):
        critical_issue = IssueContext(issue_id="crit", title="Critical", body="Critical", priority=10)
        low_issue = IssueContext(issue_id="low", title="Low", body="Low", priority=1)
        
        critical_req = strategy._get_priority_resource_requirements(critical_issue)
        low_req = strategy._get_priority_resource_requirements(low_issue)
        
        # Critical should get more resources
        assert critical_req["cpu_cores"] >= low_req["cpu_cores"]
        assert critical_req["memory_mb"] >= low_req["memory_mb"]
        assert "fast_storage" in critical_req
    
    def test_format_priority_issue_prompt(self, strategy):
        issue = IssueContext(
            issue_id="priority_test",
            title="Priority Test Issue",
            body="Test priority formatting",
            priority=8,
            dependencies=["dep1"]
        )
        
        prompt = strategy._format_priority_issue_prompt(issue)
        
        assert "HIGH PRIORITY" in prompt
        assert "Priority: HIGH" in prompt
        assert "Level 8" in prompt
        assert "⚡" in prompt  # High priority emoji
        assert "Priority-based scheduling: ENABLED" in prompt
    
    @pytest.mark.asyncio
    async def test_priority_metrics(self, strategy):
        metrics = await strategy.get_priority_metrics()
        
        assert "priority_distribution" in metrics
        assert "high_priority_slots_used" in metrics
        assert "high_priority_slots_reserved" in metrics
        assert "starvation_prevention" in metrics
        assert "priority_boost_threshold" in metrics
        assert "queue_length" in metrics


class TestStrategyFactory:
    """Test strategy factory functions."""
    
    def test_create_strategy_sequential(self):
        strategy = create_strategy('sequential', repository_url="https://test.com")
        assert isinstance(strategy, SequentialExecutionStrategy)
        assert strategy.repository_url == "https://test.com"
    
    def test_create_strategy_parallel(self):
        strategy = create_strategy('parallel', max_concurrent=10)
        assert isinstance(strategy, ParallelExecutionStrategy)
        assert strategy.max_concurrent == 10
    
    def test_create_strategy_priority(self):
        strategy = create_strategy('priority', starvation_prevention=False)
        assert isinstance(strategy, PriorityExecutionStrategy)
        assert strategy.starvation_prevention is False
    
    def test_create_strategy_invalid(self):
        with pytest.raises(ValueError, match="Unknown strategy type"):
            create_strategy('invalid_strategy')
    
    def test_get_available_strategies(self):
        strategies = get_available_strategies()
        
        assert 'sequential' in strategies
        assert 'parallel' in strategies
        assert 'priority' in strategies
        
        for strategy_info in strategies.values():
            assert 'name' in strategy_info
            assert 'description' in strategy_info
            assert 'features' in strategy_info
            assert 'best_for' in strategy_info
    
    def test_recommend_strategy(self):
        # Should recommend priority for priority workloads
        assert recommend_strategy(10, has_priorities=True) == 'priority'
        
        # Should recommend sequential for small counts
        assert recommend_strategy(2, has_priorities=False) == 'sequential'
        
        # Should recommend sequential for limited resources
        assert recommend_strategy(10, has_priorities=False, max_resources=1) == 'sequential'
        
        # Should recommend parallel for everything else
        assert recommend_strategy(10, has_priorities=False, max_resources=5) == 'parallel'


if __name__ == "__main__":
    # Run basic tests
    import asyncio
    
    async def run_basic_tests():
        print("Running basic strategy tests...")
        
        # Test sequential strategy
        seq_strategy = SequentialExecutionStrategy(repository_url="https://test.com")
        issues = [
            IssueContext(issue_id="1", title="First", body="First issue", created_at=1.0),
            IssueContext(issue_id="2", title="Second", body="Second issue", created_at=2.0)
        ]
        
        ordered = seq_strategy.get_execution_order(issues)
        assert len(ordered) == 2
        assert ordered[0].issue_id == "1"
        print("✓ Sequential strategy ordering works")
        
        selected = await seq_strategy.select_next_issues(issues, 1)
        assert len(selected) == 1
        assert selected[0].issue_id == "1"
        print("✓ Sequential strategy selection works")
        
        # Test parallel strategy
        par_strategy = ParallelExecutionStrategy(max_concurrent=3)
        priority_issues = [
            IssueContext(issue_id="p1", title="Low", body="Low", priority=3),
            IssueContext(issue_id="p2", title="High", body="High", priority=8)
        ]
        
        ordered = par_strategy.get_execution_order(priority_issues)
        assert ordered[0].issue_id == "p2"  # High priority first
        print("✓ Parallel strategy priority ordering works")
        
        # Test priority strategy
        pri_strategy = PriorityExecutionStrategy()
        assert pri_strategy._get_priority_name(10) == "critical"
        assert pri_strategy._get_priority_name(2) == "low"
        print("✓ Priority strategy naming works")
        
        # Test factory
        created = create_strategy('sequential')
        assert isinstance(created, SequentialExecutionStrategy)
        print("✓ Strategy factory works")
        
        strategies = get_available_strategies()
        assert len(strategies) == 3
        print("✓ Available strategies listing works")
        
        print("\nAll basic tests passed! ✓")
    
    asyncio.run(run_basic_tests())