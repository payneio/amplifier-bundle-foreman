"""End-to-end integration tests for background_sessions in foreman orchestrator.

These tests verify the full lifecycle of background sessions with real triggers,
without mocking the BackgroundSessionManager internals.
"""

import asyncio
import importlib.util
import os
import sys
from unittest.mock import MagicMock

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
orch_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orch_module)
ForemanOrchestrator = orch_module.ForemanOrchestrator

# Skip all tests if amplifier-foundation is not available
pytestmark = pytest.mark.skipif(
    not orch_module.HAS_BACKGROUND_SESSIONS,
    reason="amplifier-foundation with BackgroundSessionManager not available",
)


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator with session."""
    mock_session = MagicMock()
    mock_session.session_id = "parent-e2e-test"

    coordinator = MagicMock()
    coordinator.session = mock_session
    return coordinator


@pytest.fixture
def timer_config():
    """Config with a timer-based background session."""
    return {
        "worker_pools": [],
        "background_sessions": [
            {
                "name": "timer-session",
                "bundle": "test:timer-bundle",
                "triggers": [{"type": "timer", "config": {"interval_seconds": 0.1}}],
                "instruction_template": "Process timer tick",
                "on_complete_emit": "timer:completed",
            }
        ],
    }


@pytest.fixture
def manual_config():
    """Config with a manual trigger background session."""
    return {
        "worker_pools": [],
        "background_sessions": [
            {
                "name": "manual-session",
                "bundle": "test:manual-bundle",
                "triggers": [{"type": "manual"}],
                "instruction_template": "Process manual trigger: {trigger.data}",
            }
        ],
    }


@pytest.fixture
def event_config():
    """Config with a session_event trigger background session."""
    return {
        "worker_pools": [],
        "background_sessions": [
            {
                "name": "event-responder",
                "bundle": "test:event-bundle",
                "triggers": [
                    {
                        "type": "session_event",
                        "config": {"event_names": ["work:completed", "work:failed"]},
                    }
                ],
                "instruction_template": "Handle event: {trigger.event_name}",
                "on_complete_emit": "event:handled",
            }
        ],
    }


@pytest.mark.asyncio
async def test_e2e_timer_trigger_lifecycle(timer_config, mock_coordinator):
    """Test full lifecycle with timer trigger - start, run, stop."""
    orchestrator = ForemanOrchestrator(timer_config)

    # Start background sessions
    await orchestrator._maybe_start_background_sessions(mock_coordinator)

    # Verify manager was created
    assert orchestrator._background_manager is not None
    assert orchestrator._event_router is not None

    # Let timer tick a few times
    await asyncio.sleep(0.3)

    # Check status - should be running
    status = orchestrator._background_manager.get_status()
    assert len(status) == 1

    session_id = list(status.keys())[0]
    assert status[session_id]["status"] in ("starting", "running")
    assert status[session_id]["name"] == "timer-session"

    # Stop background sessions
    await orchestrator._stop_background_sessions()

    # Verify stopped
    status = orchestrator._background_manager.get_status(session_id)
    assert status["status"] == "stopped"


@pytest.mark.asyncio
async def test_e2e_manual_trigger_fire(manual_config, mock_coordinator):
    """Test manual trigger can be fired programmatically."""
    orchestrator = ForemanOrchestrator(manual_config)

    await orchestrator._maybe_start_background_sessions(mock_coordinator)

    assert orchestrator._background_manager is not None

    # Get the session ID
    status = orchestrator._background_manager.get_status()
    session_id = list(status.keys())[0]

    # Fire manual trigger (won't actually spawn since no real bundle, but tests the path)
    # The fire_manual method should exist and be callable
    await orchestrator._background_manager.fire_manual(session_id, {"reason": "test"})

    # Cleanup
    await orchestrator._stop_background_sessions()


@pytest.mark.asyncio
async def test_e2e_event_router_integration(event_config, mock_coordinator):
    """Test that EventRouter is properly connected for session_event triggers."""
    orchestrator = ForemanOrchestrator(event_config)

    await orchestrator._maybe_start_background_sessions(mock_coordinator)

    assert orchestrator._event_router is not None
    assert orchestrator._background_manager is not None

    # Emit an event through the router
    await orchestrator._event_router.emit(
        "work:completed", {"result": "success"}, source_session_id="worker-123"
    )

    # Give time for event to propagate
    await asyncio.sleep(0.1)

    # The background session should have received the event
    # (won't spawn since no real bundle, but the trigger should have fired)

    # Cleanup
    await orchestrator._stop_background_sessions()


@pytest.mark.asyncio
async def test_e2e_multiple_background_sessions(mock_coordinator):
    """Test multiple background sessions can run concurrently."""
    config = {
        "worker_pools": [],
        "background_sessions": [
            {
                "name": "session-a",
                "bundle": "test:bundle-a",
                "triggers": [{"type": "timer", "config": {"interval_seconds": 0.5}}],
            },
            {
                "name": "session-b",
                "bundle": "test:bundle-b",
                "triggers": [{"type": "manual"}],
            },
            {
                "name": "session-c",
                "bundle": "test:bundle-c",
                "triggers": [{"type": "session_event", "config": {"event_names": ["test:event"]}}],
            },
        ],
    }

    orchestrator = ForemanOrchestrator(config)
    await orchestrator._maybe_start_background_sessions(mock_coordinator)

    # All three should be running
    status = orchestrator._background_manager.get_status()
    assert len(status) == 3

    names = {s["name"] for s in status.values()}
    assert names == {"session-a", "session-b", "session-c"}

    # All should be running
    for session_status in status.values():
        assert session_status["status"] in ("starting", "running")

    # Cleanup
    await orchestrator._stop_background_sessions()

    # All should be stopped
    for session_id in status.keys():
        s = orchestrator._background_manager.get_status(session_id)
        assert s["status"] == "stopped"


@pytest.mark.asyncio
async def test_e2e_stop_all_is_graceful(timer_config, mock_coordinator):
    """Test that stop_all gracefully stops all sessions without errors."""
    orchestrator = ForemanOrchestrator(timer_config)

    await orchestrator._maybe_start_background_sessions(mock_coordinator)

    # Let it run briefly
    await asyncio.sleep(0.1)

    # Stop should not raise
    await orchestrator._stop_background_sessions()

    # Calling stop again should be safe (idempotent)
    await orchestrator._stop_background_sessions()


@pytest.mark.asyncio
async def test_e2e_config_with_all_options(mock_coordinator):
    """Test background session with all configuration options."""
    config = {
        "worker_pools": [],
        "background_sessions": [
            {
                "name": "full-config-session",
                "bundle": "test:full-bundle",
                "triggers": [{"type": "timer", "config": {"interval_seconds": 1.0}}],
                "instruction_template": "Do work with {trigger.type}",
                "pool_size": 2,
                "on_complete_emit": "work:done",
                "on_error_emit": "work:error",
                "restart_on_failure": True,
                "max_restarts": 5,
            }
        ],
    }

    orchestrator = ForemanOrchestrator(config)
    await orchestrator._maybe_start_background_sessions(mock_coordinator)

    status = orchestrator._background_manager.get_status()
    assert len(status) == 1

    session_id = list(status.keys())[0]
    assert "full-config-session" in session_id

    await orchestrator._stop_background_sessions()
