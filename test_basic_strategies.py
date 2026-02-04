"""Basic test runner for execution strategies."""

import asyncio
import sys
from pathlib import Path

# Add the current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / ".claude"))

# Import from the orchestrator module  
from orchestrator.strategies import (
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


async def test_issue_context():
    """Test IssueContext creation and properties."""
    print("Testing IssueContext...")
    
    issue = IssueContext(
        issue_id="test123",
        title="Test Issue",
        body="This is a test issue",
        priority=5,
        dependencies=["dep1", "dep2"]
    )
    
    assert issue.issue_id == "test123"
    assert issue.title == "Test Issue"
    assert issue.priority == 5
    assert issue.dependencies == ["dep1", "dep2"]
    assert issue.status == ExecutionStatus.PENDING
    assert issue.created_at is not None
    
    print("✓ IssueContext tests passed")


async def test_execution_result():
    """Test ExecutionResult creation."""
    print("Testing ExecutionResult...")
    
    result = ExecutionResult(
        issue_id="result123",
        status=ExecutionStatus.COMPLETED,
        success=True,
        message="Test completed successfully",
        duration=10.5
    )
    
    assert result.issue_id == "result123"
    assert result.status == ExecutionStatus.COMPLETED
    assert result.success is True
    assert result.duration == 10.5
    assert result.error_details == {}
    
    print("✓ ExecutionResult tests passed")


async def test_sequential_strategy():
    """Test SequentialExecutionStrategy basic functionality."""
    print("Testing SequentialExecutionStrategy...")
    
    strategy = SequentialExecutionStrategy(
        repository_url="https://github.com/test/repo.git"
    )
    
    # Test execution ordering
    issues = [
        IssueContext(issue_id="seq1", title="First", body="First issue", created_at=1.0),
        IssueContext(issue_id="seq2", title="Second", body="Second issue", created_at=2.0),
        IssueContext(issue_id="seq3", title="Third", body="Third issue", created_at=3.0)
    ]
    
    ordered = strategy.get_execution_order(issues)
    assert len(ordered) == 3
    assert ordered[0].issue_id == "seq1"  # Oldest first
    assert ordered[1].issue_id == "seq2"
    assert ordered[2].issue_id == "seq3"
    
    # Test issue selection (should only select one)
    selected = await strategy.select_next_issues(issues, current_capacity=5)
    assert len(selected) == 1
    assert selected[0].issue_id == "seq1"
    
    # Test add/remove issue
    test_issue = IssueContext(issue_id="test_add", title="Test Add", body="Test")
    await strategy.add_issue(test_issue)
    
    status = await strategy.get_status("test_add")
    assert status == ExecutionStatus.PENDING
    
    removed = await strategy.remove_issue("test_add")
    assert removed is True
    
    # Test metrics
    metrics = strategy.get_metrics()
    assert "total_issues" in metrics
    assert "success_rate" in metrics
    
    print("✓ SequentialExecutionStrategy tests passed")


async def test_parallel_strategy():
    """Test ParallelExecutionStrategy basic functionality."""
    print("Testing ParallelExecutionStrategy...")
    
    strategy = ParallelExecutionStrategy(
        max_concurrent=3,
        repository_url="https://github.com/test/repo.git"
    )
    
    # Test priority-based ordering
    issues = [
        IssueContext(issue_id="par1", title="Low", body="Low priority", priority=2, created_at=1.0),
        IssueContext(issue_id="par2", title="High", body="High priority", priority=8, created_at=2.0),
        IssueContext(issue_id="par3", title="Medium", body="Medium priority", priority=5, created_at=3.0)
    ]
    
    ordered = strategy.get_execution_order(issues)
    assert len(ordered) == 3
    assert ordered[0].issue_id == "par2"  # Highest priority first (8)
    assert ordered[1].issue_id == "par3"  # Medium priority (5)
    assert ordered[2].issue_id == "par1"  # Lowest priority (2)
    
    # Test multiple issue selection
    selected = await strategy.select_next_issues(issues, current_capacity=2)
    assert len(selected) <= 2
    assert len(selected) > 0
    
    # Test dependency graph building
    dep_issues = [
        IssueContext(issue_id="dep1", title="Independent", body="No deps", dependencies=[]),
        IssueContext(issue_id="dep2", title="Dependent", body="Has deps", dependencies=["dep1"])
    ]
    
    await strategy._build_dependency_graph(dep_issues)
    
    # dep1 should be ready (no dependencies)
    ready1 = await strategy._are_dependencies_satisfied(dep_issues[0])
    assert ready1 is True
    
    # dep2 should not be ready initially
    ready2 = await strategy._are_dependencies_satisfied(dep_issues[1])
    assert ready2 is False
    
    # Test parallel metrics
    metrics = await strategy.get_parallel_metrics()
    assert "max_concurrent" in metrics
    assert "resource_utilization" in metrics
    
    print("✓ ParallelExecutionStrategy tests passed")


async def test_priority_strategy():
    """Test PriorityExecutionStrategy basic functionality."""
    print("Testing PriorityExecutionStrategy...")
    
    strategy = PriorityExecutionStrategy(
        max_concurrent=2,
        starvation_prevention=True,
        priority_boost_threshold=1.0  # 1 second for testing
    )
    
    # Test priority levels
    assert PriorityLevel.CRITICAL.value == 10
    assert PriorityLevel.HIGH.value == 8
    assert PriorityLevel.MEDIUM.value == 5
    assert PriorityLevel.LOW.value == 2
    
    # Test priority name mapping
    assert strategy._get_priority_name(10) == "critical"
    assert strategy._get_priority_name(8) == "high"
    assert strategy._get_priority_name(5) == "medium"
    assert strategy._get_priority_name(2) == "low"
    assert strategy._get_priority_name(0) == "deferred"
    
    # Test resource requirements scaling
    critical_issue = IssueContext(issue_id="crit", title="Critical", body="Critical", priority=10)
    low_issue = IssueContext(issue_id="low", title="Low", body="Low", priority=1)
    
    critical_req = strategy._get_priority_resource_requirements(critical_issue)
    low_req = strategy._get_priority_resource_requirements(low_issue)
    
    assert critical_req["cpu_cores"] >= low_req["cpu_cores"]
    assert critical_req["memory_mb"] >= low_req["memory_mb"]
    assert "fast_storage" in critical_req
    
    # Test priority boost for old issues
    import time
    old_time = time.time() - 5.0  # 5 seconds ago
    old_issue = IssueContext(
        issue_id="old", 
        title="Old Issue", 
        body="Old", 
        priority=2, 
        created_at=old_time
    )
    
    boosted = strategy._apply_priority_boost(old_issue)
    assert boosted > old_issue.priority
    
    # Test priority metrics
    metrics = await strategy.get_priority_metrics()
    assert "priority_distribution" in metrics
    assert "starvation_prevention" in metrics
    
    print("✓ PriorityExecutionStrategy tests passed")


def test_strategy_factory():
    """Test strategy factory functions."""
    print("Testing strategy factory...")
    
    # Test creating different strategies
    seq_strategy = create_strategy('sequential', repository_url="https://test.com")
    assert isinstance(seq_strategy, SequentialExecutionStrategy)
    assert seq_strategy.repository_url == "https://test.com"
    
    par_strategy = create_strategy('parallel', max_concurrent=10)
    assert isinstance(par_strategy, ParallelExecutionStrategy)
    assert par_strategy.max_concurrent == 10
    
    pri_strategy = create_strategy('priority', starvation_prevention=False)
    assert isinstance(pri_strategy, PriorityExecutionStrategy)
    assert pri_strategy.starvation_prevention is False
    
    # Test invalid strategy
    try:
        create_strategy('invalid_strategy')
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Unknown strategy type" in str(e)
    
    # Test getting available strategies
    strategies = get_available_strategies()
    assert len(strategies) == 3
    assert 'sequential' in strategies
    assert 'parallel' in strategies
    assert 'priority' in strategies
    
    for strategy_info in strategies.values():
        assert 'name' in strategy_info
        assert 'description' in strategy_info
        assert 'features' in strategy_info
        assert 'best_for' in strategy_info
    
    # Test strategy recommendations
    assert recommend_strategy(10, has_priorities=True) == 'priority'
    assert recommend_strategy(2, has_priorities=False) == 'sequential'
    assert recommend_strategy(10, has_priorities=False, max_resources=1) == 'sequential'
    assert recommend_strategy(10, has_priorities=False, max_resources=5) == 'parallel'
    
    print("✓ Strategy factory tests passed")


async def run_all_tests():
    """Run all basic tests."""
    print("Running basic execution strategy tests...\n")
    
    try:
        await test_issue_context()
        await test_execution_result()
        await test_sequential_strategy()
        await test_parallel_strategy()
        await test_priority_strategy()
        test_strategy_factory()
        
        print(f"\n🎉 All tests passed successfully!")
        return True
        
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)