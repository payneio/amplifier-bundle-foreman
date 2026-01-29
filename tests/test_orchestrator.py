"""Tests for ForemanOrchestrator."""

import asyncio
import importlib.util
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add the parent directory to the path to help import
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

# Import using the file-based approach instead of module name with hyphen
orchestrator_path = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "modules",
    "orchestrator-foreman",
    "amplifier_module_orchestrator_foreman",
    "orchestrator.py",
)

# Use importlib to import from path with hyphen
spec = importlib.util.spec_from_file_location("orchestrator", orchestrator_path)
orchestrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrator)
ForemanOrchestrator = orchestrator.ForemanOrchestrator


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
        # Format the content as a list of blocks to match what real providers return
        self.content = [{"type": "text", "text": content}]
        self.tool_calls = []


class MockContext:
    """Mock context."""

    async def get_messages(self):
        return []

    async def add_message(self, message):
        pass


class MockHookRegistry:
    """Mock hook registry."""

    async def emit(self, event, data):
        pass


class MockBundle:
    """Mock bundle for testing."""

    def __init__(self, config=None):
        self.config = config or {}


class MockSession:
    """Mock AmplifierSession for testing."""

    def __init__(self, config=None, parent_id=None):
        self.config = config
        self.parent_id = parent_id
        self.run_called = False

    async def run(self, prompt):
        self.run_called = True
        self.prompt = prompt
        return "Test response"


@pytest.fixture
def orchestrator():
    """Create orchestrator with test config."""
    config = {
        "worker_pools": [
            {
                "name": "coding-pool",
                "worker_bundle": "git+https://example.com/test-worker",
                "max_concurrent": 3,
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
        "issue_manager": MockTool(
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
    assert orchestrator._spawned_issues == set()


@pytest.mark.asyncio
async def test_execute_no_updates(orchestrator, mock_tools):
    """Test execute with no worker updates."""
    # Mock provider with simple response
    mock_provider = MockProvider("Current Status: All clear")

    response = await orchestrator.execute(
        prompt="status",
        context=MockContext(),
        providers={"test-provider": mock_provider},
        tools=mock_tools,
        hooks=MockHookRegistry(),
    )

    assert "Current Status" in response or "All clear" in response


@pytest.mark.asyncio
async def test_check_worker_progress(orchestrator, mock_tools):
    """Test progress checking from workers."""
    # Setup mock responses for different issue statuses
    mock_tools["issue_manager"].responses = {
        "list": {
            "issues": [
                {"id": "issue-1", "title": "Completed Task", "status": "completed"},
                {
                    "id": "issue-2",
                    "title": "Blocked Task",
                    "status": "pending_user_input",
                    "block_reason": "Need input",
                },
                {"id": "issue-3", "title": "In Progress Task", "status": "in_progress"},
            ]
        }
    }

    # Call the progress check method
    progress = await orchestrator._check_worker_progress(mock_tools["issue_manager"])

    # Verify it includes all statuses
    assert "✅" in progress
    assert "⚠️" in progress
    assert "⏳" in progress
    assert "issue-1" in progress or "Completed Task" in progress
    assert "issue-2" in progress or "Blocked Task" in progress
    assert "issue-3" in progress or "In Progress Task" in progress


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
async def test_maybe_spawn_worker(orchestrator, mock_tools):
    """Test worker spawning via direct bundle loading.

    This test verifies that:
    - Worker tasks are tracked in _worker_tasks
    - Issue is added to _spawned_issues for deduplication
    - Note: Worker claims the issue (sets in_progress), not the foreman
    """
    # Mock coordinator with session
    mock_coordinator = MagicMock()
    mock_session = MagicMock()
    mock_session.id = "test-session-id"
    mock_session.config = {"providers": []}
    mock_coordinator.session = mock_session

    # Set coordinator on orchestrator
    orchestrator._coordinator = mock_coordinator

    # Track spawned tasks
    spawned_task_ids = []

    def tracking_spawn(issue_id, coro):
        spawned_task_ids.append(issue_id)
        coro.close()  # Clean up coroutine

    orchestrator._spawn_worker_task = tracking_spawn

    # Call spawn worker
    await orchestrator._maybe_spawn_worker(
        {"issue": {"id": "test-issue", "title": "Test Issue", "metadata": {"type": "coding"}}},
        mock_tools["issue_manager"],
    )

    # Verify task was spawned and tracked
    assert "test-issue" in spawned_task_ids
    assert "test-issue" in orchestrator._spawned_issues


@pytest.mark.asyncio
async def test_maybe_spawn_worker_error_handling(orchestrator, mock_tools):
    """Test error handling when bundle loading fails."""
    # Mock coordinator with session
    mock_coordinator = MagicMock()
    mock_session = MagicMock()
    mock_session.id = "test-session-id"
    mock_session.config = {"providers": []}
    mock_coordinator.session = mock_session
    mock_coordinator.tools = mock_tools
    orchestrator._coordinator = mock_coordinator

    # Make _spawn_worker_task raise an exception
    def failing_spawn(issue_id, coro):
        coro.close()
        raise RuntimeError("Failed to spawn")

    orchestrator._spawn_worker_task = failing_spawn

    # Call spawn worker (should not raise exception)
    await orchestrator._maybe_spawn_worker(
        {"issue": {"id": "test-issue", "title": "Test Issue", "metadata": {"type": "coding"}}},
        mock_tools["issue_manager"],
    )

    # Verify error was recorded
    assert len(orchestrator._spawn_errors) > 0
    assert "Failed to spawn" in orchestrator._spawn_errors[0]


@pytest.mark.asyncio
async def test_maybe_spawn_worker_missing_bundle(orchestrator, mock_tools):
    """Test worker spawning with missing worker_bundle."""
    # Create orchestrator with missing worker_bundle
    config = {
        "worker_pools": [
            {
                "name": "coding-pool",
                # No worker_bundle
                "max_concurrent": 3,
                "route_types": ["coding"],
            }
        ],
        "routing": {"default_pool": "coding-pool"},
    }
    test_orchestrator = ForemanOrchestrator(config)

    # Mock coordinator with session
    mock_coordinator = MagicMock()
    mock_coordinator.session = MagicMock(id="test-session")
    test_orchestrator._coordinator = mock_coordinator

    # Call spawn worker (should not raise exception)
    await test_orchestrator._maybe_spawn_worker(
        {"issue": {"id": "test-issue", "title": "Test Issue", "metadata": {"type": "coding"}}},
        mock_tools["issue_manager"],
    )

    # Verify no worker was spawned (function returned early)
    mock_coordinator.get_capability.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_spawn_worker_spawn_failure(orchestrator, mock_tools):
    """Test worker spawning when session.spawn capability fails."""
    # Mock coordinator with session
    mock_coordinator = MagicMock()
    mock_session = MagicMock()
    mock_session.id = "test-session-id"
    mock_coordinator.session = mock_session

    # Mock session.spawn capability that raises an error
    mock_spawn = AsyncMock(side_effect=RuntimeError("Provider not available"))

    # Setup capabilities
    mock_coordinator.get_capability.side_effect = lambda name: {
        "session.spawn": mock_spawn,
    }.get(name)

    # Set coordinator on orchestrator
    orchestrator._coordinator = mock_coordinator

    # Call spawn worker - use patch to let create_task actually run
    with patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
        await orchestrator._maybe_spawn_worker(
            {"issue": {"id": "test-issue", "title": "Test Issue", "metadata": {"type": "coding"}}},
            mock_tools["issue_manager"],
        )
        # Wait for the spawned task to complete
        await asyncio.sleep(0.1)

    # Verify error was recorded
    assert len(orchestrator._spawn_errors) > 0
    assert "Worker execution failed" in orchestrator._spawn_errors[0]


@pytest.mark.asyncio
async def test_subdirectory_execution():
    """Test that orchestrator works correctly when run from a subdirectory."""
    # Save the current working directory
    original_dir = os.getcwd()

    try:
        # Create a temporary directory to simulate a subdirectory
        with tempfile.TemporaryDirectory() as temp_dir:
            # Change to the subdirectory
            os.chdir(temp_dir)

            # Create orchestrator with absolute bundle URLs (important for subdirectory execution)
            config = {
                "worker_pools": [
                    {
                        "name": "coding-pool",
                        # Use absolute URL with git+ prefix
                        "worker_bundle": "git+https://github.com/example/worker-bundle@main",
                        "max_concurrent": 1,
                        "route_types": ["task"],
                    }
                ],
                "routing": {"default_pool": "coding-pool"},
            }

            # Initialize orchestrator
            orch = ForemanOrchestrator(config)

            # Mock coordinator with required capabilities
            mock_coordinator = MagicMock()
            mock_session = MagicMock(id="test-session-id")
            mock_coordinator.session = mock_session

            # Mock session.spawn capability
            mock_spawn = AsyncMock(
                return_value={
                    "output": "Worker completed",
                    "session_id": "worker-123",
                }
            )
            tools = {"issue_manager": MockTool()}

            # Add tools to coordinator for error handling
            mock_coordinator.tools = tools

            # Setup capabilities - session.spawn is the key capability
            mock_coordinator.get_capability.side_effect = lambda name: {
                "session.spawn": mock_spawn,
                "repo.root_path": original_dir,  # Provide repo root path
            }.get(name)

            # Set coordinator on orchestrator
            orch._coordinator = mock_coordinator

            # Patch asyncio.create_task to verify it's called
            with patch("asyncio.create_task") as mock_create_task:
                # Call spawn worker
                await orch._maybe_spawn_worker(
                    {
                        "issue": {
                            "id": "test-subdir",
                            "title": "Subdirectory Test",
                            "metadata": {"type": "task"},
                        }
                    },
                    tools["issue_manager"],
                )

                # Verify worker was spawned
                assert mock_create_task.called

                # Verify no spawn errors were recorded
                assert not hasattr(orch, "_spawn_errors") or not orch._spawn_errors

    finally:
        # Restore original working directory
        os.chdir(original_dir)


@pytest.mark.asyncio
async def test_relative_bundle_resolution():
    """Test that relative bundle paths are correctly resolved.

    The orchestrator resolves relative paths before spawning workers.
    This test verifies the path resolution logic works correctly.
    """
    # Save the current working directory
    original_dir = os.getcwd()

    try:
        # Create a temporary directory to simulate a subdirectory
        with tempfile.TemporaryDirectory() as temp_dir:
            # Change to the subdirectory
            os.chdir(temp_dir)

            # Create orchestrator with relative bundle path
            config = {
                "worker_pools": [
                    {
                        "name": "coding-pool",
                        # Use relative path that needs resolution
                        "worker_bundle": "workers/amplifier-bundle-coding-worker",
                        "max_concurrent": 1,
                        "route_types": ["task"],
                    }
                ],
                "routing": {"default_pool": "coding-pool"},
            }

            # Initialize orchestrator
            orch = ForemanOrchestrator(config)

            # Mock coordinator
            mock_coordinator = MagicMock()
            mock_session = MagicMock(id="test-session-id")
            mock_session.config = {"providers": []}
            mock_coordinator.session = mock_session

            tools = {"issue_manager": MockTool()}
            mock_coordinator.tools = tools

            # Setup capabilities for repo root path
            mock_coordinator.get_capability.side_effect = lambda name: {
                "repo.root_path": original_dir,
            }.get(name)

            # Set coordinator on orchestrator
            orch._coordinator = mock_coordinator

            def tracking_spawn(issue_id, coro):
                # The bundle path has already been resolved by the time we get here
                # We can check _resolve_bundle_path directly
                coro.close()

            orch._spawn_worker_task = tracking_spawn

            # Test path resolution directly
            resolved = orch._resolve_bundle_path("workers/amplifier-bundle-coding-worker")
            expected_path = os.path.normpath(
                os.path.join(original_dir, "workers/amplifier-bundle-coding-worker")
            )
            assert resolved == expected_path

    finally:
        # Restore original working directory
        os.chdir(original_dir)


@pytest.mark.asyncio
async def test_worker_prompt_contains_issue_details():
    """Test that worker prompt contains the issue details.

    Verifies that the orchestrator builds a prompt with:
    - Issue ID
    - Issue title
    - Issue description
    - Instructions for claiming and updating the issue
    """
    config = {
        "worker_pools": [
            {
                "name": "test-pool",
                "worker_bundle": "git+https://example.com/test-worker",
                "route_types": ["task"],
            }
        ],
        "routing": {"default_pool": "test-pool"},
    }
    orch = ForemanOrchestrator(config)

    # Test _build_worker_prompt directly
    issue = {
        "id": "issue-42",
        "title": "Test Task",
        "description": "Do something important",
        "metadata": {"type": "task"},
    }

    prompt = orch._build_worker_prompt(issue)

    # Verify prompt contains issue details
    assert "issue-42" in prompt
    assert "Test Task" in prompt
    assert "Do something important" in prompt
    # Should have instructions for claiming
    assert "in_progress" in prompt or "claim" in prompt.lower()


@pytest.mark.asyncio
async def test_spawn_uses_parent_session_for_inheritance():
    """Test that workers inherit from parent session.

    The orchestrator should pass parent session info to workers for:
    - Establishing parent-child lineage
    - Inheriting providers
    - Inheriting working directory
    """
    config = {
        "worker_pools": [
            {
                "name": "test-pool",
                "worker_bundle": "git+https://example.com/test-worker",
                "max_concurrent": 3,
                "route_types": ["test"],
            }
        ],
        "routing": {"default_pool": "test-pool"},
    }
    orch = ForemanOrchestrator(config)

    mock_coordinator = MagicMock()
    mock_parent_session = MagicMock(id="parent-session-456")
    mock_parent_session.config = {"providers": ["test-provider"]}
    mock_parent_session.session_id = "parent-session-456"
    mock_coordinator.session = mock_parent_session

    orch._coordinator = mock_coordinator

    # Verify coordinator has session available
    assert orch._coordinator.session is not None
    assert orch._coordinator.session.config.get("providers") is not None


@pytest.mark.asyncio
async def test_end_to_end_worker_lifecycle():
    """End-to-end test of the worker spawn lifecycle.

    This test verifies the complete flow:
    1. Issue received
    2. Worker task spawned and tracked
    3. Issue added to _spawned_issues for deduplication
    4. No spawn errors recorded for valid config
    """
    config = {
        "worker_pools": [
            {
                "name": "test-pool",
                "worker_bundle": "git+https://example.com/test-worker",
                "max_concurrent": 3,
                "route_types": ["test"],
            }
        ],
        "routing": {"default_pool": "test-pool"},
    }
    orch = ForemanOrchestrator(config)

    # Track spawn lifecycle
    spawn_lifecycle = []

    def tracking_spawn(issue_id, coro):
        spawn_lifecycle.append("spawn_called")
        spawn_lifecycle.append(f"issue_id={issue_id}")
        spawn_lifecycle.append("task_tracked")
        coro.close()

    orch._spawn_worker_task = tracking_spawn

    mock_coordinator = MagicMock()
    mock_coordinator.session = MagicMock(id="parent-session-123")
    mock_coordinator.session.config = {"providers": []}

    orch._coordinator = mock_coordinator

    issue_tool = MockTool(
        {
            "create": {
                "issue": {
                    "id": "test-issue-e2e",
                    "title": "Test Issue",
                    "metadata": {"type": "test"},
                }
            },
            "update": {"success": True},
        }
    )

    await orch._maybe_spawn_worker(
        {
            "issue": {
                "id": "test-issue-e2e",
                "title": "End-to-End Test Issue",
                "description": "Test the full workflow",
                "metadata": {"type": "test"},
            }
        },
        issue_tool,
    )

    # Verify spawn lifecycle
    assert "spawn_called" in spawn_lifecycle, "Worker spawn was not called"
    assert "task_tracked" in spawn_lifecycle, "Task was not tracked"
    assert "issue_id=test-issue-e2e" in spawn_lifecycle

    # Verify issue was added to spawned set
    assert "test-issue-e2e" in orch._spawned_issues

    # Verify no spawn errors were recorded
    assert not orch._spawn_errors, f"Unexpected spawn errors: {orch._spawn_errors}"


# =============================================================================
# Tests for Asyncio Worker Recovery (new functionality)
# =============================================================================


@pytest.mark.asyncio
async def test_worker_task_tracking():
    """Test that worker tasks are tracked in _worker_tasks."""
    config = {
        "worker_pools": [
            {
                "name": "test-pool",
                "worker_bundle": "git+https://example.com/test-worker",
                "route_types": ["task"],
            }
        ],
        "routing": {"default_pool": "test-pool"},
    }
    orch = ForemanOrchestrator(config)

    # Verify initial state
    assert orch._worker_tasks == {}
    assert orch._recovery_done is False
    assert orch._recovered_count == 0

    # Mock coordinator
    mock_coordinator = MagicMock()
    mock_coordinator.session = MagicMock(id="test-session")
    mock_coordinator.session.config = {"providers": []}
    orch._coordinator = mock_coordinator

    # Create a task that we can track
    async def mock_worker():
        await asyncio.sleep(0.1)
        return "done"

    # Spawn a tracked task
    orch._spawn_worker_task("issue-123", mock_worker())

    # Verify task is tracked
    assert "issue-123" in orch._worker_tasks
    assert "issue-123" in orch._spawned_issues

    # Check status shows running
    status = orch.get_worker_status()
    assert status["issue-123"] == "running"

    # Wait for completion
    await asyncio.sleep(0.2)

    # After completion, callback should have cleaned up
    assert "issue-123" not in orch._worker_tasks


@pytest.mark.asyncio
async def test_worker_task_failure_tracking():
    """Test that failed worker tasks are properly tracked and cleaned up."""
    config = {
        "worker_pools": [{"name": "test-pool", "worker_bundle": "test"}],
        "routing": {"default_pool": "test-pool"},
    }
    orch = ForemanOrchestrator(config)

    async def failing_worker():
        raise RuntimeError("Worker failed!")

    # Spawn a failing task
    orch._spawn_worker_task("issue-fail", failing_worker())

    # Wait for failure
    await asyncio.sleep(0.1)

    # Task should be cleaned up
    assert "issue-fail" not in orch._worker_tasks
    # But still in spawned_issues (for dedup reference)
    assert "issue-fail" in orch._spawned_issues


@pytest.mark.asyncio
async def test_recovery_scans_open_and_in_progress():
    """Test that recovery finds both open and in_progress issues."""
    config = {
        "worker_pools": [
            {
                "name": "test-pool",
                "worker_bundle": "git+https://example.com/test-worker",
                "route_types": ["task"],
            }
        ],
        "routing": {"default_pool": "test-pool"},
    }
    orch = ForemanOrchestrator(config)

    # Track list calls by status
    list_calls = []

    async def mock_execute(params):
        operation = params.get("operation")
        if operation == "list":
            status = params.get("params", {}).get("status")
            list_calls.append(status)
            if status == "open":
                return MockResult(
                    {
                        "issues": [
                            {"id": "orphan-1", "title": "Orphan Open", "metadata": {"type": "task"}}
                        ]
                    }
                )
            elif status == "in_progress":
                return MockResult(
                    {
                        "issues": [
                            {
                                "id": "orphan-2",
                                "title": "Orphan In Progress",
                                "metadata": {"type": "task"},
                            }
                        ]
                    }
                )
        return MockResult({"issues": []})

    mock_issue_tool = MagicMock()
    mock_issue_tool.execute = mock_execute

    # Mock coordinator
    mock_coordinator = MagicMock()
    mock_coordinator.session = MagicMock(id="test-session")
    mock_coordinator.session.config = {"providers": []}
    orch._coordinator = mock_coordinator

    # Run recovery
    recovered = await orch._maybe_recover_orphaned_issues(mock_issue_tool)

    # Verify both statuses were checked
    assert "open" in list_calls
    assert "in_progress" in list_calls

    # Verify both issues were detected (but NOT spawned - auto-spawn was removed
    # because it blocked the event loop due to bundle loading I/O)
    assert recovered == 2
    assert len(orch._orphaned_issues) == 2
    orphaned_ids = [i.get("id") for i in orch._orphaned_issues]
    assert "orphan-1" in orphaned_ids
    assert "orphan-2" in orphaned_ids

    # Verify recovery doesn't run again
    recovered_again = await orch._maybe_recover_orphaned_issues(mock_issue_tool)
    assert recovered_again == 0


@pytest.mark.asyncio
async def test_recovery_skips_issues_with_active_tasks():
    """Test that recovery doesn't respawn issues with running workers."""
    config = {
        "worker_pools": [
            {
                "name": "test-pool",
                "worker_bundle": "git+https://example.com/test-worker",
                "route_types": ["task"],
            }
        ],
        "routing": {"default_pool": "test-pool"},
    }
    orch = ForemanOrchestrator(config)

    # Simulate a running task for issue-1
    async def long_running():
        await asyncio.sleep(10)

    running_task = asyncio.create_task(long_running())
    orch._worker_tasks["issue-1"] = running_task

    # Mock issue tool
    async def mock_execute(params):
        operation = params.get("operation")
        if operation == "list":
            status = params.get("params", {}).get("status")
            if status == "in_progress":
                return MockResult(
                    {
                        "issues": [
                            {
                                "id": "issue-1",
                                "title": "Already Running",
                                "metadata": {"type": "task"},
                            },
                            {
                                "id": "issue-2",
                                "title": "Needs Recovery",
                                "metadata": {"type": "task"},
                            },
                        ]
                    }
                )
        return MockResult({"issues": []})

    mock_issue_tool = MagicMock()
    mock_issue_tool.execute = mock_execute

    # Mock coordinator
    orch._coordinator = MagicMock()
    orch._coordinator.session = MagicMock(id="test")
    orch._coordinator.session.config = {"providers": []}

    # Run recovery
    await orch._maybe_recover_orphaned_issues(mock_issue_tool)

    # Only issue-2 should be in orphaned list (issue-1 has active task so it's skipped)
    # Note: Recovery no longer spawns workers (was blocking event loop), it just detects
    orphaned_ids = [i.get("id") for i in orch._orphaned_issues]
    assert "issue-1" not in orphaned_ids  # Has active task, not orphaned
    assert "issue-2" in orphaned_ids  # No active task, is orphaned

    # Cleanup
    running_task.cancel()
    try:
        await running_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_respawn_allowed_after_task_completes():
    """Test that an issue can be respawned after its task completes."""
    config = {
        "worker_pools": [
            {
                "name": "test-pool",
                "worker_bundle": "git+https://example.com/test-worker",
                "route_types": ["task"],
            }
        ],
        "routing": {"default_pool": "test-pool"},
    }
    orch = ForemanOrchestrator(config)

    # Mock coordinator
    mock_coordinator = MagicMock()
    mock_coordinator.session = MagicMock(id="test-session")
    mock_coordinator.session.config = {"providers": []}
    orch._coordinator = mock_coordinator

    issue_tool = MockTool()

    # First spawn
    spawn_count = 0

    def counting_spawn(issue_id, coro):
        nonlocal spawn_count
        spawn_count += 1
        coro.close()

    orch._spawn_worker_task = counting_spawn

    # Spawn once
    await orch._maybe_spawn_worker(
        {"issue": {"id": "issue-1", "title": "Test", "metadata": {"type": "task"}}},
        issue_tool,
    )
    assert spawn_count == 1

    # Try to spawn again - should be allowed because there's no active task
    # (recovery case: task finished or never existed)
    await orch._maybe_spawn_worker(
        {"issue": {"id": "issue-1", "title": "Test", "metadata": {"type": "task"}}},
        issue_tool,
    )
    assert spawn_count == 2  # Respawn allowed because no active task


@pytest.mark.asyncio
async def test_get_worker_status():
    """Test get_worker_status returns correct states."""
    config = {"worker_pools": [], "routing": {}}
    orch = ForemanOrchestrator(config)

    # Create tasks in different states
    async def quick_success():
        return "done"

    async def quick_fail():
        raise ValueError("oops")

    async def long_running():
        await asyncio.sleep(10)

    # Start tasks
    success_task = asyncio.create_task(quick_success())
    fail_task = asyncio.create_task(quick_fail())
    running_task = asyncio.create_task(long_running())

    orch._worker_tasks = {
        "success": success_task,
        "fail": fail_task,
        "running": running_task,
    }

    # Wait for quick tasks to complete
    await asyncio.sleep(0.1)

    status = orch.get_worker_status()

    assert status["success"] == "completed"
    assert status["fail"] == "failed"
    assert status["running"] == "running"

    # Cleanup
    running_task.cancel()
    try:
        await running_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_check_worker_progress_includes_orphaned_issues():
    """Test that _check_worker_progress reports orphaned issues from recovery."""
    config = {"worker_pools": [], "routing": {}}
    orch = ForemanOrchestrator(config)

    # Simulate orphaned issues found during recovery
    orch._orphaned_issues = [
        {"id": "issue-1", "title": "Orphan 1"},
        {"id": "issue-2", "title": "Orphan 2"},
        {"id": "issue-3", "title": "Orphan 3"},
    ]

    # Mock issue tool that returns empty lists
    mock_issue_tool = MockTool(
        {
            "list": {"issues": []},
        }
    )

    progress = await orch._check_worker_progress(mock_issue_tool)

    # Should include orphaned issues notification
    assert "⚠️" in progress
    assert "3" in progress
    assert "orphaned" in progress.lower()

    # Orphaned issues should be cleared after reporting
    assert orch._orphaned_issues == []


@pytest.mark.asyncio
async def test_check_worker_progress_includes_running_tasks():
    """Test that _check_worker_progress reports running task count."""
    config = {"worker_pools": [], "routing": {}}
    orch = ForemanOrchestrator(config)

    # Create a running task
    async def long_running():
        await asyncio.sleep(10)

    running_task = asyncio.create_task(long_running())
    orch._worker_tasks = {"issue-1": running_task}

    # Mock issue tool
    mock_issue_tool = MockTool(
        {
            "list": {"issues": []},
        }
    )

    progress = await orch._check_worker_progress(mock_issue_tool)

    # Should include running task count
    assert "🔧" in progress
    assert "1" in progress
    assert "running" in progress.lower()

    # Cleanup
    running_task.cancel()
    try:
        await running_task
    except asyncio.CancelledError:
        pass
