#!/usr/bin/env python3
"""
Test runner for GitHub Integration module with coverage reporting.
"""

import os
import sys
import unittest
import coverage
from pathlib import Path


def run_tests_with_coverage():
    """Run all tests with coverage reporting."""
    # Initialize coverage
    cov = coverage.Coverage(
        source=['github_integration'],
        omit=[
            '*/tests/*',
            '*/test_*',
            '*/__pycache__/*',
            '*/run_tests.py'
        ]
    )
    
    cov.start()
    
    try:
        # Discover and run tests
        test_dir = Path(__file__).parent / 'tests'
        loader = unittest.TestLoader()
        suite = loader.discover(str(test_dir), pattern='test_*.py')
        
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        
        # Stop coverage and generate report
        cov.stop()
        cov.save()
        
        print("\n" + "="*50)
        print("COVERAGE REPORT")
        print("="*50)
        
        # Console report
        cov.report()
        
        # HTML report
        html_dir = Path(__file__).parent / 'htmlcov'
        cov.html_report(directory=str(html_dir))
        print(f"\nHTML coverage report generated in: {html_dir}")
        
        # Coverage summary
        total_coverage = cov.report(show_missing=False)
        
        print(f"\nTotal Coverage: {total_coverage:.1f}%")
        
        # Check if coverage meets minimum threshold (90%)
        if total_coverage >= 90.0:
            print("✅ Coverage threshold met (90%+)")
            coverage_passed = True
        else:
            print("❌ Coverage below threshold (90%)")
            coverage_passed = False
        
        # Return combined result
        tests_passed = result.wasSuccessful()
        
        if tests_passed and coverage_passed:
            print("\n🎉 All tests passed with sufficient coverage!")
            return True
        else:
            if not tests_passed:
                print(f"\n❌ {len(result.failures)} test failures, {len(result.errors)} test errors")
            if not coverage_passed:
                print(f"\n❌ Coverage {total_coverage:.1f}% is below required 90%")
            return False
            
    except Exception as e:
        print(f"Error running tests: {e}")
        return False
    
    finally:
        cov.stop()


def run_specific_test(test_module):
    """Run a specific test module."""
    try:
        # Import the test module
        test_dir = Path(__file__).parent / 'tests'
        sys.path.insert(0, str(test_dir))
        
        module = __import__(test_module)
        
        # Create test suite
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromModule(module)
        
        # Run tests
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        
        return result.wasSuccessful()
        
    except ImportError as e:
        print(f"Could not import test module '{test_module}': {e}")
        return False
    except Exception as e:
        print(f"Error running test module '{test_module}': {e}")
        return False


def main():
    """Main test runner entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='GitHub Integration Test Runner')
    parser.add_argument(
        '--module', 
        help='Run specific test module (e.g., test_github_service)',
        default=None
    )
    parser.add_argument(
        '--no-coverage', 
        action='store_true',
        help='Run tests without coverage reporting'
    )
    
    args = parser.parse_args()
    
    # Set up paths
    current_dir = Path(__file__).parent
    parent_dir = current_dir.parent
    sys.path.insert(0, str(parent_dir))
    sys.path.insert(0, str(current_dir))
    
    print("GitHub Integration Test Runner")
    print("="*40)
    
    if args.module:
        print(f"Running specific test module: {args.module}")
        success = run_specific_test(args.module)
    else:
        print("Running all tests with coverage...")
        if args.no_coverage:
            # Run without coverage
            test_dir = current_dir / 'tests'
            loader = unittest.TestLoader()
            suite = loader.discover(str(test_dir), pattern='test_*.py')
            
            runner = unittest.TextTestRunner(verbosity=2)
            result = runner.run(suite)
            success = result.wasSuccessful()
        else:
            success = run_tests_with_coverage()
    
    if success:
        print("\n✅ All tests completed successfully!")
        sys.exit(0)
    else:
        print("\n❌ Tests failed!")
        sys.exit(1)


if __name__ == '__main__':
    main()