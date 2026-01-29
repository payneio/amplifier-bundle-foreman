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
@patch("asyncio.create_task")
async def test_maybe_spawn_worker(mock_create_task, orchestrator, mock_tools):
    """Test worker spawning via session.spawn capability.

    This test verifies the canonical Amplifier pattern where:
    - App layer registers session.spawn capability
    - Orchestrator consumes the capability to spawn workers
    """
    # Mock coordinator with session
    mock_coordinator = MagicMock()
    mock_session = MagicMock()
    mock_session.id = "test-session-id"
    mock_coordinator.session = mock_session

    # Mock session.spawn capability (registered by app layer)
    mock_spawn = AsyncMock(
        return_value={
            "output": "Worker completed successfully",
            "session_id": "worker-session-123",
        }
    )

    # Setup capabilities - session.spawn is the only required capability
    mock_coordinator.get_capability.side_effect = lambda name: {
        "session.spawn": mock_spawn,
    }.get(name)

    # Set coordinator on orchestrator
    orchestrator._coordinator = mock_coordinator

    # Call spawn worker
    await orchestrator._maybe_spawn_worker(
        {"issue": {"id": "test-issue", "title": "Test Issue", "metadata": {"type": "coding"}}},
        mock_tools["issue_manager"],
    )

    # Verify issue was updated to in_progress
    assert any(
        call["operation"] == "update" and call["params"]["status"] == "in_progress"
        for call in mock_tools["issue_manager"].calls
    )

    # Verify asyncio.create_task was called to run the spawn
    assert mock_create_task.called


@pytest.mark.asyncio
async def test_maybe_spawn_worker_error_handling(orchestrator, mock_tools):
    """Test error handling when session.spawn capability is missing."""
    # Mock coordinator with missing session.spawn capability
    mock_coordinator = MagicMock()
    mock_coordinator.get_capability.return_value = None
    orchestrator._coordinator = mock_coordinator

    # Call spawn worker (should not raise exception)
    await orchestrator._maybe_spawn_worker(
        {"issue": {"id": "test-issue", "title": "Test Issue", "metadata": {"type": "coding"}}},
        mock_tools["issue_manager"],
    )

    # Verify error was recorded (capability missing)
    assert len(orchestrator._spawn_errors) > 0
    assert "session.spawn" in orchestrator._spawn_errors[0]

    # Issue should NOT be updated to in_progress when spawn fails early
    # (capability check happens before status update in the new implementation)


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

    The orchestrator resolves relative paths before passing to session.spawn.
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

            # Mock coordinator with session.spawn capability
            mock_coordinator = MagicMock()
            mock_session = MagicMock(id="test-session-id")
            mock_coordinator.session = mock_session

            # Track what agent_name is passed to spawn
            spawn_calls = []

            async def mock_spawn(agent_name, instruction, parent_session, **kwargs):
                spawn_calls.append(agent_name)
                return {"output": "done", "session_id": "worker-123"}

            tools = {"issue_manager": MockTool()}
            mock_coordinator.tools = tools

            # Setup capabilities
            mock_coordinator.get_capability.side_effect = lambda name: {
                "session.spawn": mock_spawn,
                "repo.root_path": original_dir,  # Provide repo root path
            }.get(name)

            # Set coordinator on orchestrator
            orch._coordinator = mock_coordinator

            # Use patch to let create_task run the spawn
            with patch(
                "asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)
            ):
                await orch._maybe_spawn_worker(
                    {
                        "issue": {
                            "id": "test-relative",
                            "title": "Relative Path Test",
                            "metadata": {"type": "task"},
                        }
                    },
                    tools["issue_manager"],
                )
                await asyncio.sleep(0.1)

            # Verify spawn was called with resolved path
            assert len(spawn_calls) == 1
            expected_path = os.path.normpath(
                os.path.join(original_dir, "workers/amplifier-bundle-coding-worker")
            )
            assert spawn_calls[0] == expected_path

    finally:
        # Restore original working directory
        os.chdir(original_dir)


@pytest.mark.asyncio
async def test_spawn_capability_receives_correct_arguments():
    """Test that session.spawn capability receives correct arguments.

    Verifies that the orchestrator passes the right parameters to the
    spawn capability, including the resolved bundle path and instruction.
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

    # Track spawn arguments
    spawn_kwargs = {}

    async def mock_spawn(**kwargs):
        spawn_kwargs.update(kwargs)
        return {"output": "done", "session_id": "worker-123"}

    mock_coordinator = MagicMock()
    mock_coordinator.session = MagicMock(id="parent-session-123")
    mock_coordinator.get_capability.side_effect = lambda name: {
        "session.spawn": mock_spawn,
    }.get(name)

    orch._coordinator = mock_coordinator

    issue_tool = MockTool()

    with patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
        await orch._maybe_spawn_worker(
            {
                "issue": {
                    "id": "issue-42",
                    "title": "Test Task",
                    "description": "Do something important",
                    "metadata": {"type": "task"},
                }
            },
            issue_tool,
        )
        await asyncio.sleep(0.1)

    # Verify spawn was called with correct arguments
    assert spawn_kwargs.get("agent_name") == "git+https://example.com/test-worker"
    assert "issue-42" in spawn_kwargs.get("instruction", "")
    assert "Test Task" in spawn_kwargs.get("instruction", "")
    assert spawn_kwargs.get("parent_session") is not None


@pytest.mark.asyncio
async def test_spawn_passes_parent_session():
    """Test that session.spawn receives the parent session reference.

    The session.spawn capability needs the parent session to:
    - Establish parent-child lineage
    - Inherit approval/display systems
    - Enable context inheritance if needed
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

    # Track what parent_session is passed
    received_parent_session = None

    async def mock_spawn(agent_name, instruction, parent_session, **kwargs):
        nonlocal received_parent_session
        received_parent_session = parent_session
        return {"output": "done", "session_id": "worker-123"}

    mock_coordinator = MagicMock()
    mock_parent_session = MagicMock(id="parent-session-456")
    mock_coordinator.session = mock_parent_session
    mock_coordinator.get_capability.side_effect = lambda name: {
        "session.spawn": mock_spawn,
    }.get(name)

    orch._coordinator = mock_coordinator

    test_issue_tool = MockTool()

    with patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
        await orch._maybe_spawn_worker(
            {
                "issue": {
                    "id": "test-parent",
                    "title": "Parent Session Test",
                    "metadata": {"type": "test"},
                }
            },
            test_issue_tool,
        )
        await asyncio.sleep(0.1)

    # Verify parent session was passed to spawn
    assert received_parent_session is mock_parent_session, (
        "Parent session not passed to session.spawn"
    )


@pytest.mark.asyncio
async def test_end_to_end_worker_lifecycle():
    """End-to-end test of the entire worker spawn lifecycle.

    This test verifies the complete flow using session.spawn capability:
    1. Issue received
    2. Issue status updated to in_progress
    3. session.spawn capability called with correct arguments
    4. Worker result handled appropriately
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

    async def mock_spawn(agent_name, instruction, parent_session, **kwargs):
        spawn_lifecycle.append("spawn_called")
        spawn_lifecycle.append(f"agent_name={agent_name}")
        spawn_lifecycle.append(f"has_instruction={bool(instruction)}")
        spawn_lifecycle.append(f"has_parent={parent_session is not None}")
        # Simulate successful worker execution
        spawn_lifecycle.append("spawn_completed")
        return {
            "output": "Worker completed successfully",
            "session_id": "worker-session-e2e-123",
        }

    mock_coordinator = MagicMock()
    mock_coordinator.session = MagicMock(id="parent-session-123")
    mock_coordinator.get_capability.side_effect = lambda name: {
        "session.spawn": mock_spawn,
    }.get(name)

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

    # Use patch to let create_task run the spawn
    with patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
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
        await asyncio.sleep(0.1)

    # Verify spawn lifecycle
    assert "spawn_called" in spawn_lifecycle, "session.spawn was not called"
    assert "spawn_completed" in spawn_lifecycle, "session.spawn did not complete"
    assert "agent_name=git+https://example.com/test-worker" in spawn_lifecycle
    assert "has_instruction=True" in spawn_lifecycle
    assert "has_parent=True" in spawn_lifecycle

    # Verify issue was updated to in_progress
    assert any(
        call["operation"] == "update" and call["params"]["status"] == "in_progress"
        for call in issue_tool.calls
    ), "Issue not updated to in_progress"

    # Verify no spawn errors were recorded
    assert not orch._spawn_errors, f"Unexpected spawn errors: {orch._spawn_errors}"
