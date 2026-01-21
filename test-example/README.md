# Foreman Bundle Integration Tests

This directory contains integration tests that demonstrate the foreman bundle's end-to-end workflow.

## Contents

```
test-example/
├── README.md              # This file
├── integration_test.py    # Main integration test script
└── sample-project/        # Sample project for foreman to work on
    ├── src/
    │   └── calculator.py  # Code with room for improvement
    ├── tests/
    │   └── test_calculator.py
    └── pyproject.toml
```

## What the Tests Demonstrate

### Test 1: Work Request Breakdown
Shows the foreman receiving a work request ("Refactor the calculator module") and breaking it down into discrete, actionable issues using LLM analysis.

### Test 2: Issue Routing
Verifies that issues are routed to the correct worker pools based on their type (coding → coding-pool, testing → testing-pool, etc.).

### Test 3: Status Reporting
Tests the foreman's ability to generate comprehensive status reports showing issues in various states (in-progress, completed, blocked).

### Test 4: Completion Reporting (No Repetition)
Verifies that completions are reported proactively but not repeated on subsequent turns.

### Test 5: Blocker Handling
Shows the foreman surfacing blocked issues that need user input and handling resolution when the user provides it.

### Test 6: Full Workflow Demonstration
End-to-end simulation of a realistic workflow with multiple turns, work requests, status checks, and concurrent progress.

## Running the Tests

### In a Shadow Environment (Recommended)

```bash
# Create shadow environment with foreman bundle
amplifier shadow create --local-source /path/to/amplifier-bundle-foreman:microsoft/amplifier-bundle-foreman

# Install dependencies
amplifier shadow exec -- uv pip install "amplifier-core @ git+https://github.com/microsoft/amplifier-core"
amplifier shadow exec -- uv pip install -e "/workspace/amplifier-bundle-foreman[dev]"

# Run integration tests
amplifier shadow exec -- python /workspace/amplifier-bundle-foreman/test-example/integration_test.py
```

### Locally (if amplifier-core is available)

```bash
cd /path/to/amplifier-bundle-foreman
pip install -e ".[dev]"
python test-example/integration_test.py
```

## Expected Output

```
============================================================
FOREMAN BUNDLE INTEGRATION TESTS
============================================================

============================================================
TEST 1: Work Request Breakdown
============================================================

[User Request]: Refactor the calculator module to improve code quality

[Foreman Response]:
📋 Analyzing work request...

Created 5 issues:
  • Issue #issue-1: Add type hints to calculator functions
  • Issue #issue-2: Improve error handling in divide function
  ...

🚀 Spawned 5 workers to handle these issues.
I'll keep you posted on progress!

[Issues Created]: 5
  - #issue-1: Add type hints to calculator functions (type: coding, status: in_progress)
  ...

✅ TEST 1 PASSED: Work request broken into multiple issues

... (more tests)

============================================================
TEST SUMMARY
============================================================
  ✅ PASS: Work Request Breakdown
  ✅ PASS: Issue Routing
  ✅ PASS: Status Reporting
  ✅ PASS: Completion Reporting
  ✅ PASS: Blocker Handling
  ✅ PASS: Full Workflow

Result: 6/6 tests passed

🎉 ALL TESTS PASSED!
```

## Sample Project

The `sample-project/` directory contains a simple calculator module that intentionally has room for improvement:

- No type hints
- Poor error handling (returns `None` instead of raising exceptions)
- String-based operation selection (should use Enum)
- No input validation
- Global state for history

This makes it a realistic target for the foreman to analyze and create improvement tasks for.
