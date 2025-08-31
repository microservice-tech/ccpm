#!/bin/bash

# k6 Performance Tests Runner for GitHub Integration
# This script runs all k6 performance tests for the GitHub integration components

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
GITHUB_OWNER="${GITHUB_OWNER:-microservice-tech}"
GITHUB_REPO="${GITHUB_REPO:-ccpm}"
TEST_WORKSPACE="${TEST_WORKSPACE:-$(pwd)}"
K6_OUTPUT_DIR="${K6_OUTPUT_DIR:-./k6-results}"

# Create output directory
mkdir -p "$K6_OUTPUT_DIR"

echo -e "${BLUE}🚀 Starting GitHub Integration Performance Tests${NC}"
echo -e "${BLUE}===============================================${NC}"
echo ""
echo -e "Repository: ${YELLOW}${GITHUB_OWNER}/${GITHUB_REPO}${NC}"
echo -e "Output Directory: ${YELLOW}${K6_OUTPUT_DIR}${NC}"
echo ""

# Check if k6 is installed
if ! command -v k6 &> /dev/null; then
    echo -e "${RED}❌ k6 is not installed. Please install k6 first.${NC}"
    echo -e "Installation instructions: https://k6.io/docs/getting-started/installation/"
    exit 1
fi

# Check GitHub token (optional but recommended)
if [ -z "$GITHUB_TOKEN" ]; then
    echo -e "${YELLOW}⚠️  WARNING: GITHUB_TOKEN not set. Some tests may be limited by rate limiting.${NC}"
    echo -e "To set token: export GITHUB_TOKEN=your_token_here"
    echo ""
fi

# Function to run a k6 test
run_k6_test() {
    local test_file="$1"
    local test_name="$2"
    local scenario="${3:-}"
    
    echo -e "${BLUE}Running: ${test_name}${NC}"
    echo -e "File: ${test_file}"
    
    local output_file="${K6_OUTPUT_DIR}/${test_name}-results.json"
    local html_output="${K6_OUTPUT_DIR}/${test_name}-report.html"
    
    local k6_cmd="k6 run"
    
    # Add scenario-specific environment variables
    if [ -n "$scenario" ]; then
        k6_cmd="$k6_cmd --env K6_SCENARIO_NAME=$scenario"
    fi
    
    # Add output options
    k6_cmd="$k6_cmd --out json=$output_file"
    
    # Add environment variables
    k6_cmd="GITHUB_TOKEN='$GITHUB_TOKEN' GITHUB_OWNER='$GITHUB_OWNER' GITHUB_REPO='$GITHUB_REPO' TEST_WORKSPACE='$TEST_WORKSPACE' $k6_cmd"
    
    # Add test file
    k6_cmd="$k6_cmd $test_file"
    
    echo "Command: $k6_cmd"
    echo ""
    
    if eval "$k6_cmd"; then
        echo -e "${GREEN}✅ ${test_name} completed successfully${NC}"
        
        # Generate HTML report if possible
        if command -v k6-html-reporter &> /dev/null; then
            echo "Generating HTML report..."
            k6-html-reporter --input "$output_file" --output "$html_output"
            echo -e "HTML report: ${html_output}"
        fi
    else
        echo -e "${RED}❌ ${test_name} failed${NC}"
        return 1
    fi
    
    echo ""
}

# Test execution
echo -e "${BLUE}Test 1: GitHub API Performance Tests${NC}"
echo "------------------------------------"
run_k6_test "github-api-performance.js" "github-api-performance"

echo -e "${BLUE}Test 2: GitHub CLI Performance Tests${NC}"
echo "------------------------------------"
run_k6_test "github-cli-performance.js" "github-cli-performance"

# Run scenario-specific tests
echo -e "${BLUE}Test 3: GitHub API Spike Test${NC}"
echo "------------------------------"
run_k6_test "github-api-performance.js" "github-api-spike-test" "spike_test"

echo -e "${BLUE}Test 4: Bulk Operations Test${NC}"
echo "-----------------------------"
run_k6_test "github-api-performance.js" "github-api-bulk-test" "bulk_operations"

echo -e "${BLUE}Test 5: Concurrent CLI Test${NC}"
echo "----------------------------"
run_k6_test "github-cli-performance.js" "github-cli-concurrent-test" "concurrent_cli"

# Summary
echo -e "${GREEN}🎉 All performance tests completed!${NC}"
echo -e "${GREEN}=====================================${NC}"
echo ""
echo -e "Results are available in: ${YELLOW}${K6_OUTPUT_DIR}${NC}"
echo ""
echo "Files generated:"
ls -la "$K6_OUTPUT_DIR"
echo ""

# Performance summary
echo -e "${BLUE}Performance Test Summary:${NC}"
echo "------------------------"
echo "• GitHub API Performance: Basic API operations under normal load"
echo "• GitHub CLI Performance: CLI wrapper operations and response times" 
echo "• Spike Test: API behavior under sudden load increases (rate limiting)"
echo "• Bulk Operations: Performance of batch operations"
echo "• Concurrent CLI: CLI wrapper performance under concurrent usage"
echo ""

# Recommendations
echo -e "${BLUE}Next Steps:${NC}"
echo "-----------"
echo "1. Review the JSON results for detailed performance metrics"
echo "2. Check for any rate limiting issues in the spike test"
echo "3. Verify CLI operation response times meet requirements"
echo "4. Monitor for any authentication or connection errors"
echo "5. Use results to optimize GitHub integration performance"
echo ""

if [ -n "$GITHUB_TOKEN" ]; then
    echo -e "${GREEN}✅ Tests run with GitHub token - full API access${NC}"
else
    echo -e "${YELLOW}⚠️  Tests run without GitHub token - may be rate limited${NC}"
fi

echo -e "${BLUE}Performance testing complete! 🚀${NC}"