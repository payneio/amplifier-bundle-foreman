"""Tests for background_sessions integration in foreman orchestrator."""

import importlib.util
import os
import sys
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
orch_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orch_module)
ForemanOrchestrator = orch_module.ForemanOrchestrator


@pytest.mark.asyncio
async def test_background_sessions_config_parsing():
    """Test that background_sessions config is parsed correctly."""
    config = {
        "worker_pools": [],
        "background_sessions": [
            {
                "name": "file-watcher",
                "bundle": "test:watcher-bundle",
                "triggers": [{"type": "timer", "config": {"interval_seconds": 60}}],
                "on_complete_emit": "watcher:done",
            },
            {
                "name": "event-responder",
                "bundle": "test:responder-bundle",
                "triggers": [
                    {"type": "session_event", "config": {"event_names": ["work:completed"]}}
                ],
            },
        ],
    }

    orchestrator = ForemanOrchestrator(config)

    assert len(orchestrator._background_sessions_config) == 2
    assert orchestrator._background_sessions_config[0]["name"] == "file-watcher"
    assert orchestrator._background_sessions_config[1]["name"] == "event-responder"


@pytest.mark.asyncio
async def test_background_sessions_not_started_without_config():
    """Test that no background sessions start when not configured."""
    config = {"worker_pools": []}
    orchestrator = ForemanOrchestrator(config)

    # Mock coordinator
    mock_coordinator = MagicMock()
    mock_coordinator.session = MagicMock()
    mock_coordinator.session.session_id = "parent-123"

    # Call _maybe_start_background_sessions
    await orchestrator._maybe_start_background_sessions(mock_coordinator)

    # No manager should be created
    assert orchestrator._background_manager is None
    assert orchestrator._event_router is None


@pytest.mark.asyncio
async def test_background_sessions_require_foundation():
    """Test graceful handling when amplifier-foundation is not available."""
    # Save original value
    original_has = orch_module.HAS_BACKGROUND_SESSIONS

    try:
        # Simulate foundation not available
        orch_module.HAS_BACKGROUND_SESSIONS = False

        config = {
            "worker_pools": [],
            "background_sessions": [{"name": "test", "bundle": "test:bundle", "triggers": []}],
        }
        orchestrator = ForemanOrchestrator(config)

        mock_coordinator = MagicMock()
        mock_coordinator.session = MagicMock()

        # Should return early without error
        await orchestrator._maybe_start_background_sessions(mock_coordinator)

        assert orchestrator._background_manager is None
    finally:
        # Restore
        orch_module.HAS_BACKGROUND_SESSIONS = original_has


@pytest.mark.asyncio
async def test_background_sessions_require_parent_session():
    """Test that background sessions require a parent session."""
    if not orch_module.HAS_BACKGROUND_SESSIONS:
        pytest.skip("amplifier-foundation not available")

    config = {
        "worker_pools": [],
        "background_sessions": [{"name": "test", "bundle": "test:bundle", "triggers": []}],
    }
    orchestrator = ForemanOrchestrator(config)

    # Coordinator without session
    mock_coordinator = MagicMock()
    mock_coordinator.session = None

    await orchestrator._maybe_start_background_sessions(mock_coordinator)

    # Should not create manager without parent session
    assert orchestrator._background_manager is None


@pytest.mark.asyncio
async def test_background_sessions_start_with_valid_config():
    """Test that background sessions start when properly configured."""
    if not orch_module.HAS_BACKGROUND_SESSIONS:
        pytest.skip("amplifier-foundation not available")

    config = {
        "worker_pools": [],
        "background_sessions": [
            {
                "name": "timer-test",
                "bundle": "test:bundle",
                "triggers": [{"type": "timer", "config": {"interval_seconds": 60}}],
            }
        ],
    }
    orchestrator = ForemanOrchestrator(config)

    # Mock coordinator with session
    mock_session = MagicMock()
    mock_session.session_id = "parent-123"

    mock_coordinator = MagicMock()
    mock_coordinator.session = mock_session

    # Mock the BackgroundSessionManager
    mock_manager = MagicMock()
    mock_manager.start = AsyncMock(return_value="bg-timer-test-0001")

    with patch.object(orch_module, "BackgroundSessionManager", return_value=mock_manager):
        await orchestrator._maybe_start_background_sessions(mock_coordinator)

    # Manager should be created
    assert orchestrator._background_manager is not None
    assert orchestrator._event_router is not None

    # Start should have been called
    mock_manager.start.assert_called_once()


@pytest.mark.asyncio
async def test_stop_background_sessions():
    """Test that background sessions can be stopped."""
    if not orch_module.HAS_BACKGROUND_SESSIONS:
        pytest.skip("amplifier-foundation not available")

    config = {"worker_pools": []}
    orchestrator = ForemanOrchestrator(config)

    # Create mock manager
    mock_manager = MagicMock()
    mock_manager.stop_all = AsyncMock()
    orchestrator._background_manager = mock_manager

    await orchestrator._stop_background_sessions()

    mock_manager.stop_all.assert_called_once()


@pytest.mark.asyncio
async def test_stop_background_sessions_handles_none():
    """Test that stop handles case where no manager exists."""
    config = {"worker_pools": []}
    orchestrator = ForemanOrchestrator(config)

    # Should not raise
    await orchestrator._stop_background_sessions()
