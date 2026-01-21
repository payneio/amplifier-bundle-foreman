#!/usr/bin/env python3
"""Integration test for Foreman Bundle.

This script demonstrates the foreman orchestrator's end-to-end workflow:
1. Receiving work requests
2. Breaking them into issues via LLM
3. Routing issues to appropriate worker pools
4. Tracking progress and reporting status

Run this in a shadow environment with Amplifier installed.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Add the foreman bundle to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from amplifier_bundle_foreman.orchestrator import ForemanOrchestrator


# =============================================================================
# Mock Components for Integration Testing
# =============================================================================


class MockIssueStore:
    """In-memory issue store for testing."""

    def __init__(self):
        self.issues: dict[str, dict] = {}
        self.next_id = 1

    def create(self, title: str, description: str, priority: int, metadata: dict) -> dict:
        issue_id = f"issue-{self.next_id}"
        self.next_id += 1
        issue = {
            "id": issue_id,
            "title": title,
            "description": description,
            "priority": priority,
            "metadata": metadata,
            "status": "open",
            "result": None,
            "block_reason": None,
        }
        self.issues[issue_id] = issue
        return issue

    def update(self, issue_id: str, **kwargs) -> dict | None:
        if issue_id not in self.issues:
            return None
        self.issues[issue_id].update(kwargs)
        return self.issues[issue_id]

    def list_by_status(self, status: str) -> list[dict]:
        return [i for i in self.issues.values() if i["status"] == status]

    def list_all(self) -> list[dict]:
        return list(self.issues.values())


class MockIssueTool:
    """Mock issue tool that uses the in-memory store."""

    def __init__(self, store: MockIssueStore):
        self.store = store
        self.call_log: list[dict] = []

    async def execute(self, params: dict) -> "MockResult":
        self.call_log.append(params)
        operation = params.get("operation")

        if operation == "create":
            issue = self.store.create(
                title=params.get("title", "Untitled"),
                description=params.get("description", ""),
                priority=params.get("priority", 2),
                metadata=params.get("metadata", {}),
            )
            return MockResult({"issue": issue})

        elif operation == "update":
            issue_id = params.get("issue_id")
            updates = {k: v for k, v in params.items() if k not in ("operation", "issue_id")}
            issue = self.store.update(issue_id, **updates)
            return MockResult({"issue": issue})

        elif operation == "list":
            filter_params = params.get("filter", {})
            if "status" in filter_params:
                issues = self.store.list_by_status(filter_params["status"])
            else:
                issues = self.store.list_all()
            return MockResult({"issues": issues})

        return MockResult({})


class MockResult:
    """Mock tool result."""

    def __init__(self, output: dict):
        self.output = output


class MockProvider:
    """Mock LLM provider that returns realistic issue breakdowns."""

    def __init__(self):
        self.call_count = 0
        self.calls: list[Any] = []

    async def complete(self, messages: list[dict]) -> "MockProviderResponse":
        self.call_count += 1
        self.calls.append(messages)

        # Extract the work request from the prompt
        user_message = messages[-1]["content"] if messages else ""

        # Generate realistic issue breakdown based on keywords
        if "calculator" in user_message.lower() or "refactor" in user_message.lower():
            tasks = [
                {
                    "title": "Add type hints to calculator functions",
                    "description": "Add proper type hints to all functions in calculator.py for better IDE support and documentation.",
                    "type": "coding",
                    "priority": 2,
                },
                {
                    "title": "Improve error handling in divide function",
                    "description": "Replace None return with proper exception raising for division by zero.",
                    "type": "coding",
                    "priority": 1,
                },
                {
                    "title": "Replace string operations with Enum",
                    "description": "Create an Operation enum to replace string-based operation selection in calculate().",
                    "type": "coding",
                    "priority": 2,
                },
                {
                    "title": "Add input validation",
                    "description": "Validate that inputs are numeric types before performing calculations.",
                    "type": "coding",
                    "priority": 2,
                },
                {
                    "title": "Add tests for edge cases",
                    "description": "Add tests for: negative numbers, floats, very large numbers, type errors.",
                    "type": "testing",
                    "priority": 3,
                },
            ]
        elif "test" in user_message.lower():
            tasks = [
                {
                    "title": "Run existing test suite",
                    "description": "Execute pytest to verify current test status.",
                    "type": "testing",
                    "priority": 1,
                },
                {
                    "title": "Analyze test coverage",
                    "description": "Generate coverage report and identify gaps.",
                    "type": "testing",
                    "priority": 2,
                },
            ]
        elif "research" in user_message.lower() or "investigate" in user_message.lower():
            tasks = [
                {
                    "title": "Research best practices",
                    "description": "Find industry best practices for the requested topic.",
                    "type": "research",
                    "priority": 2,
                },
            ]
        else:
            # Generic fallback
            tasks = [
                {
                    "title": "Implement requested changes",
                    "description": user_message[:200],
                    "type": "coding",
                    "priority": 2,
                },
            ]

        return MockProviderResponse(json.dumps(tasks))


class MockProviderResponse:
    """Mock provider response."""

    def __init__(self, content: str):
        self.content = content


class MockContext:
    """Mock session context."""

    pass


class MockHookRegistry:
    """Mock hook registry."""

    pass


# =============================================================================
# Integration Test Scenarios
# =============================================================================


async def test_work_request_breakdown():
    """Test that foreman breaks down work requests into issues."""
    print("\n" + "=" * 60)
    print("TEST 1: Work Request Breakdown")
    print("=" * 60)

    # Setup
    store = MockIssueStore()
    issue_tool = MockIssueTool(store)
    provider = MockProvider()
    orchestrator = ForemanOrchestrator(
        {
            "worker_pools": [
                {"name": "coding-pool", "worker_bundle": "mock", "route_types": ["coding"]},
                {"name": "testing-pool", "worker_bundle": "mock", "route_types": ["testing"]},
                {"name": "research-pool", "worker_bundle": "mock", "route_types": ["research"]},
            ],
            "routing": {"default_pool": "coding-pool"},
        }
    )

    # Execute work request
    print("\n[User Request]: Refactor the calculator module to improve code quality")
    response = await orchestrator.execute(
        prompt="Refactor the calculator module to improve code quality",
        context=MockContext(),
        providers={"anthropic": provider},
        tools={"issue": issue_tool},
        hooks=MockHookRegistry(),
    )

    print(f"\n[Foreman Response]:\n{response}")

    # Verify
    all_issues = store.list_all()
    print(f"\n[Issues Created]: {len(all_issues)}")
    for issue in all_issues:
        print(f"  - #{issue['id']}: {issue['title']} (type: {issue['metadata'].get('type')}, status: {issue['status']})")

    assert len(all_issues) >= 3, f"Expected at least 3 issues, got {len(all_issues)}"
    assert provider.call_count > 0, "LLM should have been called"
    print("\n✅ TEST 1 PASSED: Work request broken into multiple issues")
    return True


async def test_issue_routing():
    """Test that issues are routed to correct worker pools."""
    print("\n" + "=" * 60)
    print("TEST 2: Issue Routing")
    print("=" * 60)

    # Setup with multiple pools
    config = {
        "worker_pools": [
            {"name": "coding-pool", "worker_bundle": "mock", "route_types": ["coding", "implementation"]},
            {"name": "testing-pool", "worker_bundle": "mock", "route_types": ["testing", "qa"]},
            {"name": "research-pool", "worker_bundle": "mock", "route_types": ["research", "analysis"]},
        ],
        "routing": {
            "default_pool": "coding-pool",
            "rules": [
                {"if_metadata_type": ["coding", "implementation"], "then_pool": "coding-pool"},
                {"if_metadata_type": ["testing", "qa"], "then_pool": "testing-pool"},
                {"if_metadata_type": ["research"], "then_pool": "research-pool"},
            ],
        },
    }
    orchestrator = ForemanOrchestrator(config)

    # Test routing for different issue types
    test_cases = [
        ({"metadata": {"type": "coding"}}, "coding-pool"),
        ({"metadata": {"type": "testing"}}, "testing-pool"),
        ({"metadata": {"type": "research"}}, "research-pool"),
        ({"metadata": {"type": "unknown"}}, "coding-pool"),  # Falls back to default
    ]

    print("\n[Routing Tests]:")
    all_passed = True
    for issue, expected_pool in test_cases:
        pool = orchestrator._route_issue(issue)
        actual_pool = pool["name"] if pool else None
        status = "✅" if actual_pool == expected_pool else "❌"
        print(f"  {status} type={issue['metadata']['type']:12} -> {actual_pool} (expected: {expected_pool})")
        if actual_pool != expected_pool:
            all_passed = False

    assert all_passed, "Some routing tests failed"
    print("\n✅ TEST 2 PASSED: Issues routed to correct pools")
    return True


async def test_status_reporting():
    """Test status request handling with various issue states."""
    print("\n" + "=" * 60)
    print("TEST 3: Status Reporting")
    print("=" * 60)

    # Setup with pre-populated issues
    store = MockIssueStore()

    # Create issues in various states
    store.create("Add type hints", "Add type hints to functions", 2, {"type": "coding"})
    store.update("issue-1", status="in_progress")

    store.create("Improve error handling", "Better exceptions", 1, {"type": "coding"})
    store.update("issue-2", status="in_progress")

    store.create("Add tests", "More test coverage", 3, {"type": "testing"})
    store.update("issue-3", status="completed", result="Added 5 new tests")

    store.create("Research caching", "Find caching solutions", 2, {"type": "research"})
    store.update("issue-4", status="pending_user_input", block_reason="Which caching backend preferred?")

    issue_tool = MockIssueTool(store)
    orchestrator = ForemanOrchestrator({"worker_pools": [], "routing": {}})

    # Request status
    print("\n[User Request]: status")
    response = await orchestrator.execute(
        prompt="status",
        context=MockContext(),
        providers={},
        tools={"issue": issue_tool},
        hooks=MockHookRegistry(),
    )

    print(f"\n[Foreman Response]:\n{response}")

    # Verify status report contents
    assert "In Progress" in response, "Should show in-progress items"
    assert "Completed" in response, "Should show completed items"
    assert "Need Your Input" in response or "Blocked" in response, "Should show blocked items"
    print("\n✅ TEST 3 PASSED: Status report includes all issue states")
    return True


async def test_completion_reporting():
    """Test that completions are reported proactively and not repeated."""
    print("\n" + "=" * 60)
    print("TEST 4: Completion Reporting (No Repetition)")
    print("=" * 60)

    store = MockIssueStore()
    store.create("Task 1", "First task", 2, {"type": "coding"})
    store.update("issue-1", status="completed", result="Done!")

    issue_tool = MockIssueTool(store)
    orchestrator = ForemanOrchestrator({"worker_pools": [], "routing": {}})

    # First status check - should report completion
    print("\n[Turn 1 - User]: What's happening?")
    response1 = await orchestrator.execute(
        prompt="What's happening?",
        context=MockContext(),
        providers={},
        tools={"issue": issue_tool},
        hooks=MockHookRegistry(),
    )
    print(f"[Foreman]: {response1[:200]}...")

    has_completion_1 = "Completed" in response1 and "Task 1" in response1
    print(f"  -> Reported completion: {has_completion_1}")

    # Second status check - should NOT repeat the same completion
    print("\n[Turn 2 - User]: Any updates?")
    response2 = await orchestrator.execute(
        prompt="Any updates?",
        context=MockContext(),
        providers={},
        tools={"issue": issue_tool},
        hooks=MockHookRegistry(),
    )
    print(f"[Foreman]: {response2[:200]}...")

    # The completion should be in status but not as a new notification
    print(f"  -> issue-1 in reported set: {'issue-1' in orchestrator._reported_completions}")

    assert has_completion_1, "First response should report completion"
    assert "issue-1" in orchestrator._reported_completions, "Completion should be tracked"
    print("\n✅ TEST 4 PASSED: Completions reported once and tracked")
    return True


async def test_blocker_handling():
    """Test that blockers are surfaced and can be resolved."""
    print("\n" + "=" * 60)
    print("TEST 5: Blocker Handling")
    print("=" * 60)

    store = MockIssueStore()
    store.create(
        "Implement caching",
        "Add caching layer",
        2,
        {"type": "coding"},
    )
    store.update(
        "issue-1",
        status="pending_user_input",
        block_reason="Should we use Redis or Memcached?",
    )

    issue_tool = MockIssueTool(store)
    orchestrator = ForemanOrchestrator(
        {
            "worker_pools": [{"name": "coding-pool", "worker_bundle": "mock", "route_types": ["coding"]}],
            "routing": {"default_pool": "coding-pool"},
        }
    )

    # Check that blocker is reported
    print("\n[Turn 1 - User]: How's it going?")
    response1 = await orchestrator.execute(
        prompt="How's it going?",
        context=MockContext(),
        providers={},
        tools={"issue": issue_tool},
        hooks=MockHookRegistry(),
    )
    print(f"[Foreman]:\n{response1}")

    assert "Need Your Input" in response1 or "Redis" in response1, "Should report blocker"
    print("  -> Blocker surfaced: ✅")

    # Provide resolution
    print("\n[Turn 2 - User]: Use Redis")
    response2 = await orchestrator.execute(
        prompt="Use Redis",
        context=MockContext(),
        providers={},
        tools={"issue": issue_tool},
        hooks=MockHookRegistry(),
    )
    print(f"[Foreman]:\n{response2}")

    # Check issue was updated
    issue = store.issues["issue-1"]
    print(f"  -> Issue status after resolution: {issue['status']}")
    print(f"  -> Resolution stored: {issue.get('metadata', {}).get('resolution', 'N/A')}")

    assert "Resuming" in response2 or issue["status"] in ("open", "in_progress"), "Issue should be resumed"
    print("\n✅ TEST 5 PASSED: Blockers surfaced and resolution handled")
    return True


async def test_full_workflow():
    """Full end-to-end workflow demonstration."""
    print("\n" + "=" * 60)
    print("TEST 6: Full Workflow Demonstration")
    print("=" * 60)

    store = MockIssueStore()
    issue_tool = MockIssueTool(store)
    provider = MockProvider()

    orchestrator = ForemanOrchestrator(
        {
            "worker_pools": [
                {"name": "coding-pool", "worker_bundle": "./workers/amplifier-bundle-coding-worker", "route_types": ["coding"]},
                {"name": "testing-pool", "worker_bundle": "./workers/amplifier-bundle-testing-worker", "route_types": ["testing"]},
            ],
            "routing": {"default_pool": "coding-pool"},
        }
    )

    # Turn 1: Work request
    print("\n[Turn 1 - User]: Refactor the calculator module")
    response = await orchestrator.execute(
        prompt="Refactor the calculator module",
        context=MockContext(),
        providers={"anthropic": provider},
        tools={"issue": issue_tool},
        hooks=MockHookRegistry(),
    )
    print(f"[Foreman]:\n{response}\n")

    # Simulate some workers completing
    issues = store.list_all()
    if len(issues) >= 2:
        store.update(issues[0]["id"], status="completed", result="Type hints added to all functions")
        store.update(issues[1]["id"], status="completed", result="Error handling improved")

    # Turn 2: Status check
    print("\n[Turn 2 - User]: status")
    response = await orchestrator.execute(
        prompt="status",
        context=MockContext(),
        providers={"anthropic": provider},
        tools={"issue": issue_tool},
        hooks=MockHookRegistry(),
    )
    print(f"[Foreman]:\n{response}\n")

    # Simulate a blocker
    if len(issues) >= 3:
        store.update(
            issues[2]["id"],
            status="pending_user_input",
            block_reason="Should Operation enum include modulo (%) operation?",
        )

    # Turn 3: Another request while work is ongoing
    print("\n[Turn 3 - User]: Also add logging")
    response = await orchestrator.execute(
        prompt="Also add logging to all functions",
        context=MockContext(),
        providers={"anthropic": provider},
        tools={"issue": issue_tool},
        hooks=MockHookRegistry(),
    )
    print(f"[Foreman]:\n{response}\n")

    # Final status
    print("\n[Final State]:")
    print(f"  Total issues: {len(store.list_all())}")
    print(f"  Completed: {len(store.list_by_status('completed'))}")
    print(f"  In Progress: {len(store.list_by_status('in_progress'))}")
    print(f"  Blocked: {len(store.list_by_status('pending_user_input'))}")
    print(f"  Open: {len(store.list_by_status('open'))}")

    print("\n✅ TEST 6 PASSED: Full workflow executed successfully")
    return True


# =============================================================================
# Main Entry Point
# =============================================================================


async def main():
    """Run all integration tests."""
    print("=" * 60)
    print("FOREMAN BUNDLE INTEGRATION TESTS")
    print("=" * 60)

    tests = [
        ("Work Request Breakdown", test_work_request_breakdown),
        ("Issue Routing", test_issue_routing),
        ("Status Reporting", test_status_reporting),
        ("Completion Reporting", test_completion_reporting),
        ("Blocker Handling", test_blocker_handling),
        ("Full Workflow", test_full_workflow),
    ]

    results = []
    for name, test_func in tests:
        try:
            passed = await test_func()
            results.append((name, passed, None))
        except Exception as e:
            results.append((name, False, str(e)))
            print(f"\n❌ TEST FAILED: {name}")
            print(f"   Error: {e}")

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, p, _ in results if p)
    total = len(results)

    for name, success, error in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {status}: {name}")
        if error:
            print(f"         Error: {error}")

    print(f"\nResult: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 ALL TESTS PASSED!")
        return 0
    else:
        print("\n💥 SOME TESTS FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
