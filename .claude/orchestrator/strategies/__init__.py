"""Execution strategies for the Claude Flow orchestrator.

This package provides different execution strategies for processing issues:

- Sequential: Process issues one at a time in order
- Parallel: Process multiple issues concurrently 
- Priority: Process issues based on priority with smart scheduling

Each strategy implements the ExecutionStrategy interface and provides:
- Issue selection logic
- Execution flow control
- Resource allocation
- Progress tracking
"""

from .base import (
    ExecutionStrategy,
    ExecutionStatus, 
    IssueContext,
    ExecutionResult,
    ProgressCallback,
    ResourceManager
)

from .sequential import SequentialExecutionStrategy
from .parallel import ParallelExecutionStrategy  
from .priority import PriorityExecutionStrategy, PriorityLevel


__all__ = [
    # Base classes and interfaces
    "ExecutionStrategy",
    "ExecutionStatus",
    "IssueContext", 
    "ExecutionResult",
    "ProgressCallback",
    "ResourceManager",
    
    # Strategy implementations
    "SequentialExecutionStrategy",
    "ParallelExecutionStrategy", 
    "PriorityExecutionStrategy",
    
    # Priority-specific classes
    "PriorityLevel",
    
    # Strategy factory functions
    "create_strategy",
    "get_available_strategies"
]


def create_strategy(strategy_type: str, **kwargs) -> ExecutionStrategy:
    """Factory function to create execution strategies.
    
    Args:
        strategy_type: Type of strategy ('sequential', 'parallel', 'priority')
        **kwargs: Strategy-specific configuration parameters
        
    Returns:
        Configured execution strategy instance
        
    Raises:
        ValueError: If strategy_type is not supported
    """
    strategy_map = {
        'sequential': SequentialExecutionStrategy,
        'parallel': ParallelExecutionStrategy,
        'priority': PriorityExecutionStrategy
    }
    
    if strategy_type not in strategy_map:
        available = ', '.join(strategy_map.keys())
        raise ValueError(f"Unknown strategy type '{strategy_type}'. Available: {available}")
    
    strategy_class = strategy_map[strategy_type]
    return strategy_class(**kwargs)


def get_available_strategies() -> dict:
    """Get information about available execution strategies.
    
    Returns:
        Dictionary mapping strategy names to their descriptions
    """
    return {
        'sequential': {
            'name': 'Sequential Execution Strategy',
            'description': 'Processes issues one at a time in creation order (FIFO)',
            'concurrency': 1,
            'features': [
                'Complete isolation between issues',
                'Predictable execution order',
                'Minimal resource usage',
                'Simple progress tracking'
            ],
            'best_for': [
                'Single-threaded environments',
                'Resource-constrained systems', 
                'Issues with strict ordering requirements',
                'Debugging and development'
            ]
        },
        'parallel': {
            'name': 'Parallel Execution Strategy',
            'description': 'Processes multiple issues concurrently with dependency management',
            'concurrency': 'configurable (default: 5)',
            'features': [
                'Concurrent issue processing',
                'Dependency resolution',
                'Resource management',
                'Workspace isolation',
                'Anti-interference mechanisms'
            ],
            'best_for': [
                'High-throughput processing',
                'Independent issues',
                'Multi-core systems',
                'Production environments'
            ]
        },
        'priority': {
            'name': 'Priority-Based Execution Strategy', 
            'description': 'Smart scheduling based on issue priority with anti-starvation',
            'concurrency': 'configurable with reserved slots',
            'features': [
                'Priority-based scheduling',
                'Anti-starvation protection',
                'Resource scaling by priority',
                'Reserved slots for high-priority issues',
                'Aging-based priority boost'
            ],
            'best_for': [
                'Mixed priority workloads',
                'SLA-sensitive environments',
                'Critical issue handling',
                'Enterprise deployments'
            ]
        }
    }


# Strategy selection helpers
def recommend_strategy(issue_count: int, 
                      has_priorities: bool = False,
                      has_dependencies: bool = False,
                      max_resources: int = None) -> str:
    """Recommend the best strategy for given constraints.
    
    Args:
        issue_count: Number of issues to process
        has_priorities: Whether issues have different priorities
        has_dependencies: Whether issues have dependencies
        max_resources: Maximum available processing resources
        
    Returns:
        Recommended strategy name
    """
    # Priority strategy for mixed priority workloads
    if has_priorities:
        return 'priority'
    
    # Sequential for small counts or limited resources
    if issue_count <= 3 or (max_resources and max_resources <= 1):
        return 'sequential'
    
    # Parallel for everything else
    return 'parallel'


# Configuration helpers
def get_default_config(strategy_type: str) -> dict:
    """Get default configuration for a strategy type.
    
    Args:
        strategy_type: Strategy type to get defaults for
        
    Returns:
        Dictionary of default configuration values
    """
    defaults = {
        'sequential': {
            'max_concurrent': 1,
            'repository_url': None,
            'claude_flow_install_command': 'npm install -g @anthropic-ai/claude-flow'
        },
        'parallel': {
            'max_concurrent': 5,
            'repository_url': None,
            'claude_flow_install_command': 'npm install -g @anthropic-ai/claude-flow'
        },
        'priority': {
            'max_concurrent': 3,
            'repository_url': None,
            'claude_flow_install_command': 'npm install -g @anthropic-ai/claude-flow',
            'priority_boost_threshold': 300.0,  # 5 minutes
            'starvation_prevention': True
        }
    }
    
    return defaults.get(strategy_type, {})


# Version info
__version__ = "1.0.0"
__author__ = "Claude Flow Integration Team"