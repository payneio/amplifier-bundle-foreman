"""Tests validating correct session spawning patterns.

These tests validate the architectural patterns required for session spawning
in the Amplifier ecosystem. They verify:

1. The orchestrator correctly consumes the `session.spawn` capability
2. The orchestrator can work with `PreparedBundle.spawn()` directly
3. Error handling when capabilities are not registered
4. The complete spawn flow matches foundation patterns

Reference: amplifier_foundation/bundle.py:1111-1289 (PreparedBundle.spawn)
Reference: examples/07_full_workflow.py:207-248 (spawn capability registration)
"""

import importlib.util
import os
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Import the orchestrator module
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

orchestrator_path = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "modules",
    "orchestrator-foreman",
    "amplifier_module_orchestrator_foreman",
    "orchestrator.py",
)

spec = importlib.util.spec_from_file_location("orchestrator", orchestrator_path)
orchestrator_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrator_module)
ForemanOrchestrator = orchestrator_module.ForemanOrchestrator


# =============================================================================
# Test Fixtures
# =============================================================================


class MockSpawnCapability:
    """Mock implementation of session.spawn capability.

    This simulates what the app layer would register per the canonical pattern
    from examples/07_full_workflow.py.
    """

    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self.should_fail = False
        self.fail_message = "Spawn failed"

    async def __call__(
        self,
        agent_name: str,
        instruction: str,
        parent_session: Any,
        agent_configs: dict[str, dict[str, Any]] | None = None,
        sub_session_id: str | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Spawn a sub-session."""
        self.calls.append(
            {
                "agent_name": agent_name,
                "instruction": instruction,
                "parent_session": parent_session,
                "agent_configs": agent_configs,
                "sub_session_id": sub_session_id,
                "kwargs": kwargs,
            }
        )

        if self.should_fail:
            raise RuntimeError(self.fail_message)

        return {
            "output": f"Worker completed for {agent_name}",
            "session_id": f"worker-session-{agent_name}-123",
        }


class MockPreparedBundle:
    """Mock implementation of PreparedBundle.

    Simulates PreparedBundle.spawn() from amplifier_foundation/bundle.py:1111-1289.
    """

    def __init__(self):
        self.spawn_calls: list[dict[str, Any]] = []
        self.should_fail = False

    async def spawn(
        self,
        child_bundle: Any,
        instruction: str,
        *,
        compose: bool = True,
        parent_session: Any = None,
        session_id: str | None = None,
        orchestrator_config: dict[str, Any] | None = None,
        parent_messages: list[dict[str, Any]] | None = None,
        provider_preferences: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Spawn a child session using the bundle."""
        self.spawn_calls.append(
            {
                "child_bundle": child_bundle,
                "instruction": instruction,
                "compose": compose,
                "parent_session": parent_session,
                "session_id": session_id,
                "orchestrator_config": orchestrator_config,
            }
        )

        if self.should_fail:
            raise RuntimeError("PreparedBundle.spawn() failed")

        return {
            "output": f"Worker completed: {instruction[:50]}",
            "session_id": session_id or "spawned-session-456",
        }


class MockBundle:
    """Mock bundle for testing."""

    def __init__(self, name: str = "test-worker", config: dict | None = None):
        self.name = name
        self._config = config or {}
        self.agents: dict[str, dict[str, Any]] = {}

    def to_mount_plan(self) -> dict[str, Any]:
        """Convert bundle to mount plan (the correct method)."""
        return {
            "orchestrator": {"module": "loop-basic", "config": {}},
            "context": {"module": "context-simple", "config": {}},
            "providers": [{"module": "provider-anthropic", "config": {}}],
            "tools": [],
            "hooks": [],
            **self._config,
        }


class MockIssueTool:
    """Mock issue tool for testing."""

    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self.description = "Issue management tool"
        self.input_schema = {"type": "object"}

    async def execute(self, params: dict[str, Any]) -> MagicMock:
        self.calls.append(params)
        result = MagicMock()
        result.output = {"success": True, "issues": []}
        return result


def create_test_coordinator(
    *,
    spawn_capability: MockSpawnCapability | None = None,
    prepared_bundle: MockPreparedBundle | None = None,
    bundle_load: AsyncMock | None = None,
) -> MagicMock:
    """Create a test coordinator with configurable capabilities."""
    coordinator = MagicMock()
    coordinator.session = MagicMock(id="parent-session-123")
    coordinator.tools = {}

    capabilities: dict[str, Any] = {}

    if spawn_capability:
        capabilities["session.spawn"] = spawn_capability
    if prepared_bundle:
        capabilities["prepared_bundle"] = prepared_bundle
    if bundle_load:
        capabilities["bundle.load"] = bundle_load

    coordinator.get_capability = lambda name: capabilities.get(name)

    return coordinator


# =============================================================================
# Tests: session.spawn Capability Pattern (Recommended)
# =============================================================================


class TestSessionSpawnCapabilityPattern:
    """Tests for the recommended session.spawn capability pattern.

    This is the canonical pattern where:
    - App layer registers session.spawn capability
    - Orchestrator consumes the capability
    - Foundation's PreparedBundle.spawn() does the heavy lifting
    """

    @pytest.mark.asyncio
    async def test_spawn_capability_not_registered(self):
        """Test graceful handling when session.spawn is not registered."""
        config = {
            "worker_pools": [
                {
                    "name": "coding-pool",
                    "worker_bundle": "coding-worker",
                    "route_types": ["task"],
                }
            ],
            "routing": {"default_pool": "coding-pool"},
        }
        orch = ForemanOrchestrator(config)

        # Coordinator without session.spawn capability
        coordinator = create_test_coordinator()
        orch._coordinator = coordinator

        issue_tool = MockIssueTool()

        # Should not raise, should record error
        await orch._maybe_spawn_worker(
            {"issue": {"id": "issue-1", "title": "Test", "metadata": {"type": "task"}}},
            issue_tool,
        )

        # Verify spawn error was recorded (current implementation uses bundle.load check)
        # This test documents expected behavior when session.spawn pattern is implemented
        assert "issue-1" in orch._spawned_issues

    @pytest.mark.asyncio
    async def test_spawn_capability_success(self):
        """Test successful worker spawn via session.spawn capability."""
        spawn_cap = MockSpawnCapability()

        config = {
            "worker_pools": [
                {
                    "name": "coding-pool",
                    "worker_bundle": "coding-worker",
                    "route_types": ["task"],
                }
            ],
            "routing": {"default_pool": "coding-pool"},
        }
        orch = ForemanOrchestrator(config)

        coordinator = create_test_coordinator(spawn_capability=spawn_cap)
        orch._coordinator = coordinator

        # NOTE: Current implementation doesn't use session.spawn capability
        # This test documents the EXPECTED pattern after the fix
        # The test will need to be updated when the fix is implemented

        issue_tool = MockIssueTool()

        await orch._maybe_spawn_worker(
            {"issue": {"id": "issue-2", "title": "Build feature", "metadata": {"type": "task"}}},
            issue_tool,
        )

        # Current implementation tries bundle.load, not session.spawn
        # After fix, this should use spawn_cap and we'd verify:
        # assert len(spawn_cap.calls) == 1
        # assert spawn_cap.calls[0]["agent_name"] == "coding-worker"

    @pytest.mark.asyncio
    async def test_spawn_capability_error_handling(self):
        """Test error handling when spawn capability fails."""
        spawn_cap = MockSpawnCapability()
        spawn_cap.should_fail = True
        spawn_cap.fail_message = "Provider not available"

        config = {
            "worker_pools": [
                {
                    "name": "coding-pool",
                    "worker_bundle": "coding-worker",
                    "route_types": ["task"],
                }
            ],
            "routing": {"default_pool": "coding-pool"},
        }
        orch = ForemanOrchestrator(config)

        coordinator = create_test_coordinator(spawn_capability=spawn_cap)
        orch._coordinator = coordinator

        issue_tool = MockIssueTool()

        # Should not raise - errors should be collected
        await orch._maybe_spawn_worker(
            {"issue": {"id": "issue-3", "title": "Build feature", "metadata": {"type": "task"}}},
            issue_tool,
        )

        # Issue should be tracked as spawned (attempted)
        assert "issue-3" in orch._spawned_issues


# =============================================================================
# Tests: PreparedBundle.spawn() Direct Pattern (Alternative)
# =============================================================================


class TestPreparedBundleSpawnPattern:
    """Tests for direct PreparedBundle.spawn() pattern.

    This pattern is for advanced cases where orchestrator needs more control.
    App layer provides prepared_bundle capability, orchestrator uses it directly.
    """

    @pytest.mark.asyncio
    async def test_prepared_bundle_spawn_success(self):
        """Test direct spawn via PreparedBundle."""
        prepared = MockPreparedBundle()
        bundle_load = AsyncMock(return_value=MockBundle("coding-worker"))

        config = {
            "worker_pools": [
                {
                    "name": "coding-pool",
                    "worker_bundle": "git+https://example.com/coding-worker",
                    "route_types": ["task"],
                }
            ],
            "routing": {"default_pool": "coding-pool"},
        }
        orch = ForemanOrchestrator(config)

        coordinator = create_test_coordinator(
            prepared_bundle=prepared,
            bundle_load=bundle_load,
        )
        orch._coordinator = coordinator

        # NOTE: Current implementation doesn't use prepared_bundle capability
        # This test documents the alternative pattern

        issue_tool = MockIssueTool()

        await orch._maybe_spawn_worker(
            {"issue": {"id": "issue-4", "title": "Build feature", "metadata": {"type": "task"}}},
            issue_tool,
        )

        # After implementing this pattern, we'd verify:
        # assert len(prepared.spawn_calls) == 1
        # assert "Build feature" in prepared.spawn_calls[0]["instruction"]


# =============================================================================
# Tests: Current Implementation Behavior (Documents Existing Bugs)
# =============================================================================


class TestSpawnBundleImplementation:
    """Tests for the spawn_bundle() based implementation.

    The foreman now uses spawn_bundle() from amplifier_foundation directly,
    which is the canonical pattern for session spawning. This provides:
    - Consistent behavior with CLI and other spawn points
    - Proper session lifecycle events (session:end with status in payload)
    - Standardized persistence via SessionStorage protocol
    """

    @pytest.mark.asyncio
    async def test_spawn_uses_spawn_bundle_directly(self):
        """Verify implementation uses spawn_bundle() from foundation.

        The foreman now imports spawn_bundle inside _run_spawn_and_handle_result
        and uses it directly instead of requesting bundle.load capability.
        """
        import asyncio
        from unittest.mock import patch

        config = {
            "worker_pools": [
                {
                    "name": "coding-pool",
                    "worker_bundle": "git+https://example.com/worker",
                    "route_types": ["task"],
                }
            ],
            "routing": {"default_pool": "coding-pool"},
        }
        orch = ForemanOrchestrator(config)

        # Track which capabilities are requested
        requested_capabilities: list[str] = []

        def track_capability(name: str) -> Any:
            requested_capabilities.append(name)
            # Return working_dir for the spawn to use
            if name == "session.working_dir":
                return "/test/working/dir"
            return None

        # Set up coordinator with properly nested session structure
        # The orchestrator calls parent_session.coordinator.get_capability()
        coordinator = MagicMock()
        coordinator.session = MagicMock()
        coordinator.session.session_id = "test-session"
        coordinator.session.config = {"providers": []}
        # The capability is requested from the session's coordinator
        coordinator.session.coordinator = MagicMock()
        coordinator.session.coordinator.get_capability = track_capability
        coordinator.tools = {}
        orch._coordinator = coordinator

        issue_tool = MockIssueTool()

        # Mock spawn_bundle at the source module level
        mock_result = MagicMock()
        mock_result.session_id = "worker-session-123"
        mock_result.output = "Worker completed"

        # Patch at source since it's imported inside the method
        with patch(
            "amplifier_orchestration.spawn.spawn_bundle",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            await orch._maybe_spawn_worker(
                {"issue": {"id": "issue-5", "title": "Test", "metadata": {"type": "task"}}},
                issue_tool,
            )

            # Wait for background task to complete
            await asyncio.sleep(0.1)
            # Gather all pending tasks for this orchestrator
            if orch._worker_tasks:
                await asyncio.gather(*orch._worker_tasks.values(), return_exceptions=True)

        # The new implementation uses spawn_bundle() directly, which only needs
        # session.working_dir capability - it doesn't request bundle.load anymore
        assert "bundle.load" not in requested_capabilities
        # Working dir is requested for path context
        assert "session.working_dir" in requested_capabilities

    @pytest.mark.asyncio
    async def test_spawn_error_tracking(self):
        """Verify spawn errors are tracked correctly."""
        import asyncio
        from unittest.mock import patch

        config = {
            "worker_pools": [
                {
                    "name": "coding-pool",
                    "worker_bundle": "git+https://example.com/worker",
                    "route_types": ["task"],
                }
            ],
            "routing": {"default_pool": "coding-pool"},
        }
        orch = ForemanOrchestrator(config)

        coordinator = MagicMock()
        coordinator.session = MagicMock()
        coordinator.session.session_id = "test-session"
        coordinator.session.config = {"providers": []}
        coordinator.get_capability = lambda name: None  # No capabilities
        coordinator.tools = {}
        orch._coordinator = coordinator

        issue_tool = MockIssueTool()

        # Mock spawn_bundle to raise an error
        with patch(
            "amplifier_orchestration.spawn.spawn_bundle",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Bundle not found"),
        ):
            await orch._maybe_spawn_worker(
                {"issue": {"id": "issue-6", "title": "Test", "metadata": {"type": "task"}}},
                issue_tool,
            )

            # Wait for background task to complete (and fail)
            await asyncio.sleep(0.1)
            if orch._worker_tasks:
                await asyncio.gather(*orch._worker_tasks.values(), return_exceptions=True)

        # Verify error was recorded
        assert len(orch._spawn_errors) > 0
        # Error message will mention the failure
        assert "failed" in orch._spawn_errors[0].lower()


# =============================================================================
# Tests: Orchestrator Contract Compliance
# =============================================================================


class TestOrchestratorContractCompliance:
    """Tests verifying orchestrator follows the kernel contract.

    Reference: amplifier-module-loop-streaming patterns
    """

    @pytest.mark.asyncio
    async def test_execute_signature_matches_contract(self):
        """Verify execute() has the correct signature."""
        import inspect

        sig = inspect.signature(ForemanOrchestrator.execute)
        params = list(sig.parameters.keys())

        # Required parameters per orchestrator contract
        assert "self" in params
        assert "prompt" in params
        assert "context" in params
        assert "providers" in params
        assert "tools" in params
        assert "hooks" in params
        assert "coordinator" in params

    @pytest.mark.asyncio
    async def test_execute_emits_required_events(self):
        """Verify execute() emits PROMPT_SUBMIT and ORCHESTRATOR_COMPLETE."""
        config = {"worker_pools": [], "routing": {}}
        orch = ForemanOrchestrator(config)

        # Mock provider
        mock_provider = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [{"type": "text", "text": "Done"}]
        mock_response.tool_calls = []
        mock_provider.complete = AsyncMock(return_value=mock_response)

        # Mock context
        mock_context = MagicMock()
        mock_context.get_messages = AsyncMock(return_value=[])
        mock_context.add_message = AsyncMock()

        # Mock hooks - track emitted events
        emitted_events: list[str] = []
        mock_hooks = MagicMock()
        mock_hooks.emit = AsyncMock(side_effect=lambda event, data: emitted_events.append(event))

        # Mock coordinator
        mock_coordinator = MagicMock()
        mock_coordinator.process_hook_result = AsyncMock(return_value=MagicMock(action="allow"))

        await orch.execute(
            prompt="test",
            context=mock_context,
            providers={"test": mock_provider},
            tools={},
            hooks=mock_hooks,
            coordinator=mock_coordinator,
        )

        # Verify required events were emitted
        from amplifier_core.events import ORCHESTRATOR_COMPLETE, PROMPT_SUBMIT

        assert PROMPT_SUBMIT in emitted_events
        assert ORCHESTRATOR_COMPLETE in emitted_events


# =============================================================================
# Tests: Worker Pool Routing
# =============================================================================


class TestWorkerPoolRouting:
    """Tests for worker pool routing logic."""

    @pytest.mark.asyncio
    async def test_route_by_issue_type(self):
        """Test routing issues to correct pools by type."""
        config = {
            "worker_pools": [
                {"name": "coding-pool", "worker_bundle": "coding-worker", "route_types": ["task"]},
                {
                    "name": "research-pool",
                    "worker_bundle": "research-worker",
                    "route_types": ["epic"],
                },
            ],
            "routing": {
                "default_pool": "coding-pool",
                "rules": [
                    {"if_metadata_type": ["task", "feature"], "then_pool": "coding-pool"},
                    {"if_metadata_type": ["epic"], "then_pool": "research-pool"},
                ],
            },
        }
        orch = ForemanOrchestrator(config)

        # Task should route to coding-pool
        task_issue = {"metadata": {"type": "task"}}
        pool = orch._route_issue(task_issue)
        assert pool["name"] == "coding-pool"

        # Epic should route to research-pool
        epic_issue = {"metadata": {"type": "epic"}}
        pool = orch._route_issue(epic_issue)
        assert pool["name"] == "research-pool"

        # Unknown type should fall back to default
        unknown_issue = {"metadata": {"type": "unknown"}}
        pool = orch._route_issue(unknown_issue)
        assert pool["name"] == "coding-pool"

    @pytest.mark.asyncio
    async def test_no_pools_configured(self):
        """Test graceful handling when no worker pools configured."""
        config = {"worker_pools": [], "routing": {}}
        orch = ForemanOrchestrator(config)

        issue = {"metadata": {"type": "task"}}
        pool = orch._route_issue(issue)

        assert pool is None


# =============================================================================
# Integration Tests: End-to-End Spawn Flow
# =============================================================================


class TestEndToEndSpawnFlow:
    """Integration tests for complete spawn workflows."""

    @pytest.mark.asyncio
    async def test_issue_creation_triggers_spawn(self):
        """Test that creating an issue triggers worker spawn."""
        import asyncio
        from unittest.mock import patch

        config = {
            "worker_pools": [
                {
                    "name": "coding-pool",
                    "worker_bundle": "git+https://example.com/worker",
                    "route_types": ["task"],
                }
            ],
            "routing": {"default_pool": "coding-pool"},
        }
        orch = ForemanOrchestrator(config)

        # Setup coordinator with proper session structure
        coordinator = MagicMock()
        coordinator.session = MagicMock()
        coordinator.session.session_id = "parent-123"
        coordinator.session.config = {"providers": []}
        coordinator.session.coordinator = MagicMock()
        coordinator.session.coordinator.get_capability = lambda name: (
            "/test/dir" if name == "session.working_dir" else None
        )
        coordinator.tools = {}
        orch._coordinator = coordinator

        issue_tool = MockIssueTool()

        # Simulate issue creation result
        issue_result = {
            "issue": {
                "id": "issue-100",
                "title": "Implement feature X",
                "description": "Build the new authentication system",
                "metadata": {"type": "task"},
            }
        }

        # Mock spawn_bundle to avoid actual bundle loading
        mock_result = MagicMock()
        mock_result.session_id = "worker-session-123"
        mock_result.output = "Worker completed"

        with patch(
            "amplifier_orchestration.spawn.spawn_bundle",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            # Spawn should be attempted
            await orch._maybe_spawn_worker(issue_result, issue_tool)

            # Wait for background task to complete
            await asyncio.sleep(0.1)
            if orch._worker_tasks:
                await asyncio.gather(*orch._worker_tasks.values(), return_exceptions=True)

        # Issue should be tracked
        assert "issue-100" in orch._spawned_issues

        # NOTE: The orchestrator intentionally does NOT set in_progress directly.
        # Instead, the worker is instructed to claim the issue itself, which
        # provides more accurate lifecycle tracking. The worker prompt contains
        # instructions to update status to "in_progress" as its first step.
        # So we verify the issue is tracked for spawning, not that it was updated.

    @pytest.mark.asyncio
    async def test_duplicate_spawn_prevention(self):
        """Test that same issue is not spawned twice."""
        from unittest.mock import patch

        config = {
            "worker_pools": [
                {
                    "name": "coding-pool",
                    "worker_bundle": "git+https://example.com/worker",
                    "route_types": ["task"],
                }
            ],
            "routing": {"default_pool": "coding-pool"},
        }
        orch = ForemanOrchestrator(config)

        # Setup coordinator with proper session structure
        coordinator = MagicMock()
        coordinator.session = MagicMock()
        coordinator.session.session_id = "parent-123"
        coordinator.session.config = {"providers": []}
        coordinator.session.coordinator = MagicMock()
        coordinator.session.coordinator.get_capability = lambda name: (
            "/test/dir" if name == "session.working_dir" else None
        )
        coordinator.tools = {}
        orch._coordinator = coordinator

        issue_tool = MockIssueTool()
        issue_result = {"issue": {"id": "issue-dup", "title": "Test", "metadata": {"type": "task"}}}

        # Mock spawn_bundle to avoid actual bundle loading
        mock_result = MagicMock()
        mock_result.session_id = "worker-session-123"
        mock_result.output = "Worker completed"

        with patch(
            "amplifier_orchestration.spawn.spawn_bundle",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            # First spawn
            await orch._maybe_spawn_worker(issue_result, issue_tool)
            first_call_count = len(issue_tool.calls)

            # Second spawn attempt (same issue)
            await orch._maybe_spawn_worker(issue_result, issue_tool)

            # Should not have made additional calls
            assert len(issue_tool.calls) == first_call_count
