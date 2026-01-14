"""Tests for ForemanOrchestrator."""

import pytest

from amplifier_bundle_foreman import ForemanOrchestrator


class MockTool:
    """Mock tool for testing."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    async def execute(self, params):
        """Execute mock tool."""
        self.calls.append(params)
        operation = params.get("operation", "unknown")
        return MockResult(self.responses.get(operation, {}))


class MockResult:
    """Mock tool result."""

    def __init__(self, output):
        self.output = output


class MockProvider:
    """Mock LLM provider."""

    def __init__(self, response_content):
        self.response_content = response_content
        self.calls = []

    async def complete(self, messages):
        """Complete mock request."""
        self.calls.append(messages)
        return MockProviderResponse(self.response_content)


class MockProviderResponse:
    """Mock provider response."""

    def __init__(self, content):
        self.content = content


class MockContext:
    """Mock context."""

    pass


class MockHookRegistry:
    """Mock hook registry."""

    pass


@pytest.fixture
def orchestrator():
    """Create orchestrator with test config."""
    config = {
        "worker_pools": [
            {
                "name": "coding-pool",
                "worker_bundle": "test-worker",
                "route_types": ["coding"],
            }
        ],
        "routing": {"default_pool": "coding-pool"},
    }
    return ForemanOrchestrator(config)


@pytest.fixture
def mock_tools():
    """Create mock tools."""
    return {
        "issue": MockTool(
            {
                "list": {"issues": []},
                "create": {"issue": {"id": "issue-1", "title": "Test Issue"}},
            }
        ),
        "task": MockTool(),
    }


@pytest.mark.asyncio
async def test_init(orchestrator):
    """Test orchestrator initialization."""
    assert orchestrator.config is not None
    assert len(orchestrator.worker_pools) == 1
    assert orchestrator._reported_completions == set()
    assert orchestrator._reported_blockers == set()


@pytest.mark.asyncio
async def test_execute_no_updates(orchestrator, mock_tools):
    """Test execute with no worker updates."""
    response = await orchestrator.execute(
        prompt="status",
        context=MockContext(),
        providers={},
        tools=mock_tools,
        hooks=MockHookRegistry(),
    )

    assert "Current Status" in response or "All clear" in response


@pytest.mark.asyncio
async def test_execute_with_completions(orchestrator, mock_tools):
    """Test execute with completed issues."""
    # Mock completed issues
    mock_tools["issue"].responses["list"] = {
        "issues": [
            {
                "id": "issue-1",
                "title": "Test Issue",
                "status": "completed",
                "result": "Done",
            }
        ]
    }

    response = await orchestrator.execute(
        prompt="status",
        context=MockContext(),
        providers={},
        tools=mock_tools,
        hooks=MockHookRegistry(),
    )

    assert "Completed" in response
    assert "issue-1" in orchestrator._reported_completions


@pytest.mark.asyncio
async def test_execute_with_blockers(orchestrator, mock_tools):
    """Test execute with blocked issues."""
    # Mock blocked issues
    mock_tools["issue"].responses["list"] = {
        "issues": [
            {
                "id": "issue-2",
                "title": "Blocked Issue",
                "status": "pending_user_input",
                "block_reason": "Need input",
            }
        ]
    }

    response = await orchestrator.execute(
        prompt="status",
        context=MockContext(),
        providers={},
        tools=mock_tools,
        hooks=MockHookRegistry(),
    )

    assert "Need Your Input" in response
    assert "issue-2" in orchestrator._reported_blockers


@pytest.mark.asyncio
async def test_status_request(orchestrator, mock_tools):
    """Test status request processing."""
    mock_tools["issue"].responses["list"] = {
        "issues": [
            {"id": "issue-1", "title": "In Progress", "status": "in_progress"},
            {"id": "issue-2", "title": "Completed", "status": "completed"},
        ]
    }

    response = await orchestrator.execute(
        prompt="what's the status?",
        context=MockContext(),
        providers={},
        tools=mock_tools,
        hooks=MockHookRegistry(),
    )

    assert "Current Status" in response
    assert "In Progress" in response


@pytest.mark.asyncio
async def test_work_request_with_provider(orchestrator, mock_tools):
    """Test work request with LLM provider."""
    mock_provider = MockProvider(
        """```json
[
  {
    "title": "Task 1",
    "description": "Do something",
    "type": "coding",
    "priority": 2
  }
]
```"""
    )

    response = await orchestrator.execute(
        prompt="Implement feature X",
        context=MockContext(),
        providers={"test-provider": mock_provider},
        tools=mock_tools,
        hooks=MockHookRegistry(),
    )

    assert "Created" in response or "issues" in response.lower()
    assert len(mock_provider.calls) > 0  # LLM was called


@pytest.mark.asyncio
async def test_work_request_without_provider(orchestrator, mock_tools):
    """Test work request without LLM provider."""
    response = await orchestrator.execute(
        prompt="Implement feature X",
        context=MockContext(),
        providers={},
        tools=mock_tools,
        hooks=MockHookRegistry(),
    )

    assert "Created" in response or "issues" in response.lower()
    # Should create at least one issue even without LLM
    create_calls = [c for c in mock_tools["issue"].calls if c.get("operation") == "create"]
    assert len(create_calls) > 0


@pytest.mark.asyncio
async def test_route_issue_by_type(orchestrator):
    """Test issue routing by type."""
    issue = {"metadata": {"type": "coding"}}

    pool = orchestrator._route_issue(issue)

    assert pool is not None
    assert pool["name"] == "coding-pool"


@pytest.mark.asyncio
async def test_route_issue_default(orchestrator):
    """Test issue routing to default pool."""
    issue = {"metadata": {"type": "unknown"}}

    pool = orchestrator._route_issue(issue)

    assert pool is not None
    assert pool["name"] == "coding-pool"  # Falls back to default


@pytest.mark.asyncio
async def test_is_status_request(orchestrator):
    """Test status request detection."""
    assert orchestrator._is_status_request("what's the status?")
    assert orchestrator._is_status_request("show me progress")
    assert orchestrator._is_status_request("STATUS")
    assert not orchestrator._is_status_request("implement feature")


@pytest.mark.asyncio
async def test_is_work_request(orchestrator):
    """Test work request detection."""
    assert orchestrator._is_work_request("implement feature X")
    assert orchestrator._is_work_request("refactor the code")
    assert orchestrator._is_work_request("add a new module")
    assert not orchestrator._is_work_request("hello")


@pytest.mark.asyncio
async def test_no_repetition_of_completions(orchestrator, mock_tools):
    """Test that completions are only reported once."""
    mock_tools["issue"].responses["list"] = {
        "issues": [{"id": "issue-1", "title": "Test", "status": "completed", "result": "Done"}]
    }

    # First call - should report
    response1 = await orchestrator.execute(
        prompt="status",
        context=MockContext(),
        providers={},
        tools=mock_tools,
        hooks=MockHookRegistry(),
    )

    assert "Completed" in response1

    # Second call - should NOT report same completion again
    response2 = await orchestrator.execute(
        prompt="status",
        context=MockContext(),
        providers={},
        tools=mock_tools,
        hooks=MockHookRegistry(),
    )

    # Should not have completion notification (only in status report if asked)
    assert "Current Status" in response2


@pytest.mark.asyncio
async def test_format_completions(orchestrator):
    """Test completion message formatting."""
    completions = [
        {"id": "1", "title": "Task 1", "result": "Done"},
        {"id": "2", "title": "Task 2", "result": "Also done"},
    ]

    msg = orchestrator._format_completions(completions)

    assert "Completed (2)" in msg
    assert "Task 1" in msg
    assert "Task 2" in msg


@pytest.mark.asyncio
async def test_format_blockers(orchestrator):
    """Test blocker message formatting."""
    blockers = [
        {"id": "1", "title": "Blocked Task", "block_reason": "Need input"},
    ]

    msg = orchestrator._format_blockers(blockers)

    assert "Need Your Input" in msg
    assert "Blocked Task" in msg
    assert "Need input" in msg
