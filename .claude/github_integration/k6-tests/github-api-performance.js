/**
 * k6 Performance Tests for GitHub Integration
 * 
 * This test suite validates the performance characteristics of GitHub API
 * interactions through our GitHub integration components.
 */

import http from 'k6/http';
import { check, group } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const githubApiRate = new Rate('github_api_success_rate');
const githubApiDuration = new Trend('github_api_duration');
const rateLimitErrors = new Counter('rate_limit_errors');
const authErrors = new Counter('auth_errors');

// Test configuration
export const options = {
  scenarios: {
    // Scenario 1: Steady load testing
    steady_load: {
      executor: 'constant-vus',
      vus: 5,
      duration: '2m',
      tags: { scenario: 'steady_load' },
    },
    
    // Scenario 2: Spike testing for rate limiting
    spike_test: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '30s', target: 1 },
        { duration: '10s', target: 20 }, // Spike to trigger rate limiting
        { duration: '30s', target: 20 },
        { duration: '10s', target: 1 },
      ],
      tags: { scenario: 'spike_test' },
    },
    
    // Scenario 3: Load testing for bulk operations
    bulk_operations: {
      executor: 'per-vu-iterations',
      vus: 3,
      iterations: 10,
      maxDuration: '5m',
      tags: { scenario: 'bulk_operations' },
    },
  },
  
  thresholds: {
    // 95% of requests should complete within 2 seconds
    'http_req_duration': ['p(95)<2000'],
    
    // Success rate should be above 90% (accounting for rate limiting)
    'github_api_success_rate': ['rate>0.9'],
    
    // GitHub API specific thresholds
    'github_api_duration': ['p(95)<1500'],
    
    // Rate limit handling
    'rate_limit_errors': ['count<50'], // Should handle rate limits gracefully
  },
};

// Test configuration from environment
const GITHUB_TOKEN = __ENV.GITHUB_TOKEN || '';
const GITHUB_OWNER = __ENV.GITHUB_OWNER || 'microservice-tech';
const GITHUB_REPO = __ENV.GITHUB_REPO || 'ccpm';
const BASE_URL = 'https://api.github.com';

// Request headers
const headers = {
  'Authorization': `Bearer ${GITHUB_TOKEN}`,
  'Accept': 'application/vnd.github+json',
  'X-GitHub-Api-Version': '2022-11-28',
  'User-Agent': 'k6-performance-test/1.0',
};

export function setup() {
  console.log('Starting GitHub Integration Performance Tests');
  console.log(`Testing repository: ${GITHUB_OWNER}/${GITHUB_REPO}`);
  
  if (!GITHUB_TOKEN) {
    console.warn('WARNING: GITHUB_TOKEN not provided. Some tests may fail.');
  }
  
  return {
    owner: GITHUB_OWNER,
    repo: GITHUB_REPO,
    baseUrl: BASE_URL,
  };
}

export default function(data) {
  // Distribute test scenarios based on VU
  const vuId = __VU;
  const scenario = __ENV.K6_SCENARIO_NAME || 'steady_load';
  
  switch (scenario) {
    case 'spike_test':
      runSpikeTest(data);
      break;
    case 'bulk_operations':
      runBulkOperationsTest(data);
      break;
    default:
      runSteadyLoadTest(data);
  }
}

function runSteadyLoadTest(data) {
  group('Repository Information', () => {
    testRepositoryInfo(data);
  });
  
  group('Issue Operations', () => {
    testIssueOperations(data);
  });
  
  group('Pull Request Operations', () => {
    testPullRequestOperations(data);
  });
  
  group('Rate Limit Monitoring', () => {
    testRateLimitStatus(data);
  });
}

function runSpikeTest(data) {
  // Focus on testing rate limit handling under load
  group('Rate Limit Stress Test', () => {
    // Make multiple rapid requests to test rate limiting
    for (let i = 0; i < 5; i++) {
      testRepositoryInfo(data);
      testIssueOperations(data);
    }
    
    testRateLimitStatus(data);
  });
}

function runBulkOperationsTest(data) {
  group('Bulk Issue Operations', () => {
    // Simulate bulk operations like listing many issues
    testBulkIssuesQuery(data);
    testBulkPullRequestsQuery(data);
  });
}

function testRepositoryInfo(data) {
  const response = http.get(`${data.baseUrl}/repos/${data.owner}/${data.repo}`, {
    headers: headers,
    tags: { operation: 'repo_info' },
  });
  
  const success = check(response, {
    'Repository info status is 200': (r) => r.status === 200,
    'Repository info response time < 1s': (r) => r.timings.duration < 1000,
    'Repository has required fields': (r) => {
      if (r.status !== 200) return false;
      const body = JSON.parse(r.body);
      return body.name && body.owner && body.default_branch;
    },
  });
  
  githubApiRate.add(success);
  githubApiDuration.add(response.timings.duration);
  
  if (response.status === 403 && response.body.includes('rate limit')) {
    rateLimitErrors.add(1);
  }
  
  if (response.status === 401) {
    authErrors.add(1);
  }
}

function testIssueOperations(data) {
  // Test listing issues
  const listResponse = http.get(
    `${data.baseUrl}/repos/${data.owner}/${data.repo}/issues?state=open&per_page=10`,
    {
      headers: headers,
      tags: { operation: 'list_issues' },
    }
  );
  
  const listSuccess = check(listResponse, {
    'Issue list status is 200 or 404': (r) => r.status === 200 || r.status === 404,
    'Issue list response time < 1.5s': (r) => r.timings.duration < 1500,
    'Issue list returns array': (r) => {
      if (r.status !== 200) return true; // Skip check if not successful
      try {
        const body = JSON.parse(r.body);
        return Array.isArray(body);
      } catch (e) {
        return false;
      }
    },
  });
  
  githubApiRate.add(listSuccess);
  githubApiDuration.add(listResponse.timings.duration);
  
  if (listResponse.status === 403 && listResponse.body.includes('rate limit')) {
    rateLimitErrors.add(1);
  }
  
  // Test getting a specific issue (if issues exist)
  if (listResponse.status === 200) {
    try {
      const issues = JSON.parse(listResponse.body);
      if (issues.length > 0) {
        const issue = issues[0];
        const getResponse = http.get(
          `${data.baseUrl}/repos/${data.owner}/${data.repo}/issues/${issue.number}`,
          {
            headers: headers,
            tags: { operation: 'get_issue' },
          }
        );
        
        const getSuccess = check(getResponse, {
          'Get issue status is 200': (r) => r.status === 200,
          'Get issue response time < 1s': (r) => r.timings.duration < 1000,
          'Issue has required fields': (r) => {
            if (r.status !== 200) return false;
            const body = JSON.parse(r.body);
            return body.number && body.title && body.state;
          },
        });
        
        githubApiRate.add(getSuccess);
        githubApiDuration.add(getResponse.timings.duration);
      }
    } catch (e) {
      console.warn('Error parsing issues response:', e);
    }
  }
}

function testPullRequestOperations(data) {
  // Test listing pull requests
  const listResponse = http.get(
    `${data.baseUrl}/repos/${data.owner}/${data.repo}/pulls?state=open&per_page=5`,
    {
      headers: headers,
      tags: { operation: 'list_prs' },
    }
  );
  
  const listSuccess = check(listResponse, {
    'PR list status is 200': (r) => r.status === 200,
    'PR list response time < 1.5s': (r) => r.timings.duration < 1500,
    'PR list returns array': (r) => {
      if (r.status !== 200) return true;
      try {
        const body = JSON.parse(r.body);
        return Array.isArray(body);
      } catch (e) {
        return false;
      }
    },
  });
  
  githubApiRate.add(listSuccess);
  githubApiDuration.add(listResponse.timings.duration);
  
  if (listResponse.status === 403 && listResponse.body.includes('rate limit')) {
    rateLimitErrors.add(1);
  }
  
  // Test getting PR details if PRs exist
  if (listResponse.status === 200) {
    try {
      const prs = JSON.parse(listResponse.body);
      if (prs.length > 0) {
        const pr = prs[0];
        const getResponse = http.get(
          `${data.baseUrl}/repos/${data.owner}/${data.repo}/pulls/${pr.number}`,
          {
            headers: headers,
            tags: { operation: 'get_pr' },
          }
        );
        
        const getSuccess = check(getResponse, {
          'Get PR status is 200': (r) => r.status === 200,
          'Get PR response time < 1s': (r) => r.timings.duration < 1000,
          'PR has required fields': (r) => {
            if (r.status !== 200) return false;
            const body = JSON.parse(r.body);
            return body.number && body.title && body.state && body.head && body.base;
          },
        });
        
        githubApiRate.add(getSuccess);
        githubApiDuration.add(getResponse.timings.duration);
      }
    } catch (e) {
      console.warn('Error parsing PRs response:', e);
    }
  }
}

function testRateLimitStatus(data) {
  const response = http.get(`${data.baseUrl}/rate_limit`, {
    headers: headers,
    tags: { operation: 'rate_limit_status' },
  });
  
  const success = check(response, {
    'Rate limit status is 200': (r) => r.status === 200,
    'Rate limit response time < 500ms': (r) => r.timings.duration < 500,
    'Rate limit has core info': (r) => {
      if (r.status !== 200) return false;
      const body = JSON.parse(r.body);
      return body.resources && body.resources.core;
    },
  });
  
  githubApiRate.add(success);
  githubApiDuration.add(response.timings.duration);
  
  if (response.status === 200) {
    try {
      const rateLimit = JSON.parse(response.body);
      const core = rateLimit.resources.core;
      
      console.log(`Rate limit status - Remaining: ${core.remaining}/${core.limit}, Reset: ${new Date(core.reset * 1000)}`);
      
      // Add custom metric for rate limit monitoring
      check(core, {
        'Rate limit not exhausted': (rl) => rl.remaining > 10,
        'Rate limit reset time is future': (rl) => rl.reset > Date.now() / 1000,
      });
    } catch (e) {
      console.warn('Error parsing rate limit response:', e);
    }
  }
}

function testBulkIssuesQuery(data) {
  // Test fetching multiple pages of issues
  const pages = 3;
  const perPage = 30;
  
  for (let page = 1; page <= pages; page++) {
    const response = http.get(
      `${data.baseUrl}/repos/${data.owner}/${data.repo}/issues?state=all&per_page=${perPage}&page=${page}`,
      {
        headers: headers,
        tags: { operation: 'bulk_issues', page: page },
      }
    );
    
    const success = check(response, {
      [`Bulk issues page ${page} status is 200`]: (r) => r.status === 200,
      [`Bulk issues page ${page} response time < 2s`]: (r) => r.timings.duration < 2000,
    });
    
    githubApiRate.add(success);
    githubApiDuration.add(response.timings.duration);
    
    if (response.status === 403 && response.body.includes('rate limit')) {
      rateLimitErrors.add(1);
      console.warn(`Rate limit hit on page ${page}`);
      break; // Stop if we hit rate limit
    }
    
    // Small delay between requests to be respectful
    if (page < pages) {
      k6.sleep(0.1);
    }
  }
}

function testBulkPullRequestsQuery(data) {
  // Test fetching multiple states of PRs
  const states = ['open', 'closed'];
  
  states.forEach(state => {
    const response = http.get(
      `${data.baseUrl}/repos/${data.owner}/${data.repo}/pulls?state=${state}&per_page=20`,
      {
        headers: headers,
        tags: { operation: 'bulk_prs', state: state },
      }
    );
    
    const success = check(response, {
      [`Bulk PRs ${state} status is 200`]: (r) => r.status === 200,
      [`Bulk PRs ${state} response time < 2s`]: (r) => r.timings.duration < 2000,
    });
    
    githubApiRate.add(success);
    githubApiDuration.add(response.timings.duration);
    
    if (response.status === 403 && response.body.includes('rate limit')) {
      rateLimitErrors.add(1);
    }
  });
}

export function teardown(data) {
  console.log('GitHub Integration Performance Tests Completed');
}