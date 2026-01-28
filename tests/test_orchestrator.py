"""Tests for ForemanOrchestrator."""

import os
import pytest
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

# Use a relative import to the local module
from modules.orchestrator_foreman.amplifier_module_orchestrator_foreman.orchestrator import ForemanOrchestrator


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
                {
                    "id": "issue-1",
                    "title": "Completed Task",
                    "status": "completed"
                },
                {
                    "id": "issue-2",
                    "title": "Blocked Task",
                    "status": "pending_user_input",
                    "block_reason": "Need input"
                },
                {
                    "id": "issue-3",
                    "title": "In Progress Task",
                    "status": "in_progress"
                }
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
@patch('asyncio.create_task')
async def test_maybe_spawn_worker(mock_create_task, orchestrator, mock_tools):
    """Test worker spawning with direct bundle loading."""
    # Mock coordinator with session and capabilities
    mock_coordinator = MagicMock()
    mock_session = MagicMock()
    mock_session.id = "test-session-id"
    mock_coordinator.session = mock_session
    
    # Mock bundle loading
    mock_load_bundle = AsyncMock(return_value=MockBundle({"test": "config"}))
    
    # Mock AmplifierSession
    mock_amplifier_session = MagicMock(return_value=MockSession())
    
    # Setup capabilities
    mock_coordinator.get_capability.side_effect = lambda name: {
        "bundle.load": mock_load_bundle,
        "session.AmplifierSession": mock_amplifier_session
    }.get(name)
    
    # Set coordinator on orchestrator
    orchestrator._coordinator = mock_coordinator
    
    # Call spawn worker
    await orchestrator._maybe_spawn_worker(
        {"issue": {"id": "test-issue", "title": "Test Issue", "metadata": {"type": "coding"}}},
        mock_tools["issue_manager"]
    )
    
    # Verify bundle was loaded
    mock_load_bundle.assert_called_once_with("git+https://example.com/test-worker")
    
    # Verify session was created with correct parameters
    mock_amplifier_session.assert_called_once()
    assert mock_amplifier_session.call_args[1]["config"] == {"test": "config"}
    assert mock_amplifier_session.call_args[1]["parent_id"] == "test-session-id"
    
    # Verify issue was updated to in_progress
    assert any(
        call["operation"] == "update" and call["params"]["status"] == "in_progress" 
        for call in mock_tools["issue_manager"].calls
    )
    
    # Verify asyncio.create_task was called to run the worker
    assert mock_create_task.called


@pytest.mark.asyncio
async def test_maybe_spawn_worker_error_handling(orchestrator, mock_tools):
    """Test error handling during worker spawning."""
    # Mock coordinator with missing capabilities
    mock_coordinator = MagicMock()
    mock_coordinator.get_capability.return_value = None
    orchestrator._coordinator = mock_coordinator
    
    # Call spawn worker (should not raise exception)
    await orchestrator._maybe_spawn_worker(
        {"issue": {"id": "test-issue", "title": "Test Issue", "metadata": {"type": "coding"}}},
        mock_tools["issue_manager"]
    )
    
    # Verify issue was still updated to in_progress
    assert any(
        call["operation"] == "update" and call["params"]["status"] == "in_progress" 
        for call in mock_tools["issue_manager"].calls
    )


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
        mock_tools["issue_manager"]
    )
    
    # Verify no worker was spawned (function returned early)
    mock_coordinator.get_capability.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_spawn_worker_bundle_load_failure(orchestrator, mock_tools):
    """Test worker spawning when bundle loading fails."""
    # Mock coordinator with session and capabilities
    mock_coordinator = MagicMock()
    mock_session = MagicMock()
    mock_session.id = "test-session-id"
    mock_coordinator.session = mock_session
    
    # Mock bundle loading failure
    mock_load_bundle = AsyncMock(return_value=None)  # Load fails
    
    # Mock AmplifierSession
    mock_amplifier_session = MagicMock(return_value=MockSession())
    
    # Setup capabilities
    mock_coordinator.get_capability.side_effect = lambda name: {
        "bundle.load": mock_load_bundle,
        "session.AmplifierSession": mock_amplifier_session
    }.get(name)
    
    # Set coordinator on orchestrator
    orchestrator._coordinator = mock_coordinator
    
    # Call spawn worker
    await orchestrator._maybe_spawn_worker(
        {"issue": {"id": "test-issue", "title": "Test Issue", "metadata": {"type": "coding"}}},
        mock_tools["issue_manager"]
    )
    
    # Verify bundle load was attempted
    mock_load_bundle.assert_called_once_with("git+https://example.com/test-worker")
    
    # Verify session was NOT created (because bundle load failed)
    mock_amplifier_session.assert_not_called()


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
                        "route_types": ["task"]
                    }
                ],
                "routing": {"default_pool": "coding-pool"}
            }
            
            # Initialize orchestrator
            orchestrator = ForemanOrchestrator(config)
            
            # Mock coordinator with required capabilities
            mock_coordinator = MagicMock()
            mock_session = MagicMock(id="test-session-id")
            mock_coordinator.session = mock_session
            
            # Mock bundle loading
            mock_load_bundle = AsyncMock(return_value=MockBundle({"test": "config"}))
            mock_amplifier_session = MagicMock(return_value=MockSession())
            tools = {"issue_manager": MockTool()}
            
            # Add tools to coordinator for error handling
            mock_coordinator.tools = tools
            
            # Setup capabilities including repo root for absolute path resolution
            mock_coordinator.get_capability.side_effect = lambda name: {
                "bundle.load": mock_load_bundle,
                "session.AmplifierSession": mock_amplifier_session,
                "repo.root_path": original_dir  # Provide repo root path
            }.get(name)
            
            # Set coordinator on orchestrator
            orchestrator._coordinator = mock_coordinator
            
            # Patch asyncio.create_task to verify it's called
            with patch('asyncio.create_task') as mock_create_task:
                # Call spawn worker
                await orchestrator._maybe_spawn_worker(
                    {"issue": {"id": "test-subdir", "title": "Subdirectory Test", "metadata": {"type": "task"}}},
                    tools["issue_manager"]
                )
                
                # Verify bundle was loaded with the absolute URL
                mock_load_bundle.assert_called_once_with("git+https://github.com/example/worker-bundle@main")
                
                # Verify session was created with correct parameters
                mock_amplifier_session.assert_called_once()
                assert mock_amplifier_session.call_args[1]["config"] == {"test": "config"}
                assert mock_amplifier_session.call_args[1]["parent_id"] == "test-session-id"
                
                # Verify worker was spawned
                assert mock_create_task.called
                
                # Verify no spawn errors were recorded
                assert not hasattr(orchestrator, "_spawn_errors") or not orchestrator._spawn_errors
    
    finally:
        # Restore original working directory
        os.chdir(original_dir)


@pytest.mark.asyncio
async def test_relative_bundle_resolution():
    """Test that relative bundle paths are correctly resolved."""
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
                        "route_types": ["task"]
                    }
                ],
                "routing": {"default_pool": "coding-pool"}
            }
            
            # Initialize orchestrator
            orchestrator = ForemanOrchestrator(config)
            
            # Mock coordinator with required capabilities
            mock_coordinator = MagicMock()
            mock_session = MagicMock(id="test-session-id")
            mock_coordinator.session = mock_session
            
            # Mock bundle loading
            mock_load_bundle = AsyncMock(return_value=MockBundle({"test": "config"}))
            mock_amplifier_session = MagicMock(return_value=MockSession())
            tools = {"issue_manager": MockTool()}
            
            # Add tools to coordinator for error handling
            mock_coordinator.tools = tools
            
            # Setup capabilities including repo root for absolute path resolution
            mock_coordinator.get_capability.side_effect = lambda name: {
                "bundle.load": mock_load_bundle,
                "session.AmplifierSession": mock_amplifier_session,
                "repo.root_path": original_dir  # Provide repo root path
            }.get(name)
            
            # Set coordinator on orchestrator
            orchestrator._coordinator = mock_coordinator
            
            # Call spawn worker
            await orchestrator._maybe_spawn_worker(
                {"issue": {"id": "test-relative", "title": "Relative Path Test", "metadata": {"type": "task"}}},
                tools["issue_manager"]
            )
            
            # Verify bundle was loaded with the resolved path
            mock_load_bundle.assert_called_once()
            # The resolved path should be the absolute path formed by joining repo root and relative path
            expected_path = os.path.normpath(os.path.join(original_dir, "workers/amplifier-bundle-coding-worker"))
            assert mock_load_bundle.call_args[0][0] == expected_path
    
    finally:
        # Restore original working directory
        os.chdir(original_dir)