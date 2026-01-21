# Testing Worker Bundle

Specialized worker bundle for testing and QA tasks in the Foreman architecture.

## Overview

The Testing Worker Bundle provides a test-focused environment for validating code, running test suites, and ensuring quality. It's designed to be spawned by the Foreman orchestrator to handle testing tasks through an issue queue.

### Key Features

- **Test Execution**: Run tests via bash and analyze results
- **Restricted Write Access**: Can only create/modify test files (security boundary)
- **Code Quality**: Built-in Python linting and type checking
- **Coverage Analysis**: Identify test coverage gaps
- **Issue-Driven**: Receives work via issue queue, updates status when complete
- **Analytical**: Investigates failures and identifies root causes

## Installation

```bash
# Install from source
pip install -e .

# Or as dependency in foreman config
orchestrator:
  config:
    worker_pools:
      - name: testing-pool
        worker_bundle: git+https://github.com/your-org/amplifier-bundle-testing-worker@v1.0.0
```

## Usage

### As Part of Foreman System

This bundle is designed to be spawned by the Foreman orchestrator:

```yaml
# foreman-bundle.md
orchestrator:
  module: orchestrator-foreman
  config:
    worker_pools:
      - name: testing-pool
        worker_bundle: git+https://github.com/your-org/amplifier-bundle-testing-worker@v1.0.0
        max_concurrent: 2
        route_types: [testing, validation, qa, regression]
```

The foreman will:
1. Create issues for testing tasks
2. Spawn testing workers to handle them
3. Monitor issue queue for test results
4. Report findings to user

### Worker Session Flow

```
Foreman creates issue:
  "Add tests for email validation"
     ↓
Foreman spawns testing-worker with issue context
     ↓
Worker reads code, creates tests
     ↓
Worker runs tests, analyzes results
     ↓
Worker updates issue: "completed" with test results
     ↓
Session ends, foreman reports completion
```

### Issue Status Protocol

Workers communicate through issue status updates:

**Tests Pass**:
```python
{
  "status": "completed",
  "result": """
Testing Summary: Added comprehensive email validation tests

Tests Run:
• test_valid_email_formats - PASSED (3 cases)
• test_invalid_email_formats - PASSED (5 cases)
• Full suite: 45 passed, 0 failed

Coverage: 92% (up from 85%)

Files Modified:
• tests/test_user.py (added 8 new tests)
"""
}
```

**Tests Fail**:
```python
{
  "status": "completed",
  "result": """
Test Results: 3 failures found

Failures:
1. test_auth_timeout - line 45
   Error: AssertionError: Expected retry, got immediate fail
   Cause: Timeout handling not implemented
   
Recommendations:
• Route to coding-worker to fix timeout handling

Test command: pytest tests/test_auth.py -v
"""
}
```

## Capabilities

### Tools Available

| Tool | Purpose | Configuration |
|------|---------|---------------|
| `tool-filesystem` | Read/write files | Write restricted to `tests/**` only |
| `tool-bash` | Run commands | Tests, coverage, linters |
| `tool-issue` | Issue queue | Update status, query issues |
| `python-check` | Code quality | Linting, type checking, formatting |

### What Workers CAN Do

✅ Read any file in the project
✅ Write/edit files in `tests/**` directory
✅ Run bash commands (tests, coverage, checks)
✅ Analyze test results and failures
✅ Update issue status

### What Workers CANNOT Do

❌ Write to `src/**` (production code)
❌ Write to config files, docs, etc.
❌ Install packages
❌ Access the web
❌ Spawn other workers

## Security Model

The testing worker operates with test-only write access:

### Filesystem Boundaries

```yaml
tools:
  - module: tool-filesystem
    config:
      allowed_write_paths:
        - "tests/**"  # Can only write test files
```

This ensures workers cannot:
- Modify production code
- Change configuration files
- Alter documentation
- Edit dependencies

### Code Execution

Workers CAN run bash commands for:
- Running tests: `pytest tests/`
- Checking coverage: `pytest --cov`
- Code quality: `python-check`

But CANNOT:
- Install packages (`pip install`)
- Make system changes
- Access privileged operations

## Testing Workflow

### Testing Process

1. **Understand Requirements**: Parse issue, identify what needs testing
2. **Analyze Code**: Read source code and existing tests
3. **Run Tests**: Execute test suites and analyze results
4. **Create/Update Tests**: Write new tests or fix existing ones
5. **Update Status**: Document results in issue

### Test Execution Patterns

```bash
# Run specific test file
pytest tests/test_auth.py -v

# Run with coverage
pytest tests/test_auth.py --cov=src.auth --cov-report=term-missing

# Stop on first failure
pytest tests/test_auth.py -x

# Run only failed tests from last run
pytest --lf tests/test_auth.py
```

## Example Use Cases

### Test New Feature

```
User: "Add tests for new caching layer"
     ↓
Foreman creates issue → spawns testing-worker
     ↓
Worker reads cache.py, creates tests
     ↓
Worker provides:
  • 12 new tests (get, set, expire, clear)
  • All tests passing
  • Coverage: 95%
```

### Validate Bug Fix

```
User: "Verify timeout fix works"
     ↓
Foreman creates issue → spawns testing-worker
     ↓
Worker creates regression test, runs full suite
     ↓
Worker provides:
  • Regression test passes (confirms fix)
  • Full suite: 23 passed, 0 failed
  • Analysis of fix behavior
```

### Investigate Test Failures

```
User: "Why are tests failing in CI?"
     ↓
Foreman creates issue → spawns testing-worker
     ↓
Worker runs tests, analyzes failures
     ↓
Worker provides:
  • Root cause: Missing DB setup in CI
  • Tests pass locally with DB
  • Recommendation: Add DB setup to CI workflow
```

### Coverage Analysis

```
User: "Analyze test coverage for payment module"
     ↓
Foreman creates issue → spawns testing-worker
     ↓
Worker runs coverage analysis
     ↓
Worker provides:
  • Current: 73%
  • Gaps: cancel(), validate_amount(), currency handling
  • Recommendation: Add 6-8 tests for uncovered paths
```

## Configuration Examples

### Basic Testing Pool

```yaml
worker_pools:
  - name: testing-pool
    worker_bundle: git+https://github.com/your-org/amplifier-bundle-testing-worker@v1.0.0
    max_concurrent: 2
    route_types: [testing, validation, qa]
```

### Specialized Testing Pools

```yaml
worker_pools:
  # Unit testing
  - name: unit-test-pool
    worker_bundle: git+https://github.com/your-org/amplifier-bundle-testing-worker@v1.0.0
    max_concurrent: 3
    route_types: [unit-test, test-creation]
  
  # Integration testing
  - name: integration-test-pool
    worker_bundle: git+https://github.com/your-org/amplifier-bundle-testing-worker@v1.0.0
    max_concurrent: 1  # May need DB/external resources
    route_types: [integration-test, e2e-test]
  
  # Regression testing
  - name: regression-pool
    worker_bundle: git+https://github.com/your-org/amplifier-bundle-testing-worker@v1.0.0
    max_concurrent: 2
    route_types: [regression-test, bug-verification]
```

## Extending This Bundle

### Custom Tool Configuration

Add project-specific testing tools:

```yaml
# testing-worker-custom.md
includes:
  - bundle: git+https://github.com/your-org/amplifier-bundle-testing-worker@v1.0.0

tools:
  # Add database for integration tests
  - module: tool-database
    config:
      connection_string: ${TEST_DATABASE_URL}
  
  # Add custom test runner
  - module: tool-custom-test-runner
    config:
      framework: custom
```

### Custom Instructions

Override or extend testing guidelines:

```yaml
# testing-worker-custom.md
includes:
  - bundle: amplifier-bundle-testing-worker

---

# Additional Testing Guidelines

For this project:
- Use pytest fixtures in conftest.py
- Mock external APIs using pytest-mock
- Run tests with: pytest -v --tb=short
- Target: 90% coverage minimum

@testing-worker:context/instructions.md
```

## Comparison with Other Workers

| Worker Type | Focus | Tools | When to Use |
|-------------|-------|-------|-------------|
| **Testing Worker** | QA and validation | Test runners, checks | Testing, coverage, validation |
| Coding Worker | Implementation | Files (write), bash | Code changes, bug fixes |
| Research Worker | Information gathering | Web, read files | Investigation, analysis |
| Privileged Worker | Unrestricted tasks | All tools | Config changes, installs |

## Best Practices

### For Foreman Configuration

1. **Pool sizing**: 2-3 workers for unit tests, 1 for integration (resource-intensive)
2. **Type routing**: Separate unit vs integration testing
3. **Timeout**: Integration tests may take longer

### For Issue Creation

1. **Clear scope**: "Add tests for X" not just "Test everything"
2. **Test type**: Specify unit, integration, regression, or coverage
3. **Context**: Include what changed or what bug was fixed
4. **Criteria**: Define what "passing" means

### For Workers

1. **Run tests first**: Establish baseline before changes
2. **Isolate tests**: No shared state between tests
3. **Descriptive names**: `test_login_fails_with_invalid_password`
4. **Document failures**: Root cause, not just symptoms
5. **Check coverage**: Identify gaps, not just pass/fail

## Troubleshooting

### Worker Can't Run Tests

**Check**: Are test dependencies installed?
**Worker Response**: Updates issue to `blocked` with missing dependencies

### Tests Pass Locally, Fail in Worker

**Possible Causes**: Environment differences, missing fixtures
**Worker Response**: Documents environment-specific failures

### Worker Only Creates Tests, Doesn't Run

**Expected**: Workers ALWAYS run tests before marking complete
**If Happens**: Bug in worker - workers should verify tests pass

### Coverage Tool Not Working

**Check**: Is pytest-cov installed?
**Fix**: Add to requirements-dev.txt or pyproject.toml

## Testing

### Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# With coverage
pytest --cov
```

### Manual Testing

Test the worker with a mock issue:

```python
# Create test issue
issue = {
    "id": "test-1",
    "title": "Add tests for user authentication",
    "description": "Create unit tests for login, logout, session management",
    "metadata": {"type": "testing"}
}

# Spawn worker
amplifier task execute \
  agent="testing-worker" \
  instruction="Handle issue #test-1: Add tests for user authentication..."
```

## Quality Indicators

### Good Test Results

✅ Precise error messages with line numbers
✅ Root cause analysis for failures
✅ Coverage information included
✅ Specific recommendations for fixes
✅ All relevant tests run
✅ Clear next actions

### Poor Test Results

❌ Just "tests failed" without details
❌ No analysis of why
❌ Missing coverage information
❌ Generic recommendations
❌ Incomplete test execution

## Common Testing Scenarios

### New Feature Testing
Worker creates comprehensive tests for new functionality, runs them, reports coverage.

### Bug Fix Validation
Worker creates regression test for bug, runs full suite, confirms fix works.

### Coverage Improvement
Worker analyzes coverage gaps, creates tests for uncovered paths, re-runs coverage.

### Test Failure Investigation
Worker runs tests, analyzes failures, identifies root cause, documents fix recommendation.

## Contributing

Contributions welcome! Please:

1. Add tests for new features
2. Follow bundle conventions
3. Update documentation
4. Test with foreman integration

## License

MIT

## Related Projects

- [Foreman Bundle](https://github.com/your-org/amplifier-bundle-foreman) - Orchestrator
- [Coding Worker](https://github.com/your-org/amplifier-bundle-coding-worker) - Implementation
- [Research Worker](https://github.com/your-org/amplifier-bundle-research-worker) - Information gathering
- [Issue Bundle](https://github.com/your-org/amplifier-bundle-issues) - Issue queue

## Support

- GitHub Issues: [Report bugs](https://github.com/your-org/amplifier-bundle-testing-worker/issues)
- Documentation: See `context/` directory
- Examples: See foreman bundle examples
