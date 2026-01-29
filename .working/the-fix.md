# The Fix: Foreman Pattern Session Spawning

## Executive Summary

The foreman orchestrator's session spawning mechanism is fundamentally broken because it attempts to directly instantiate `AmplifierSession` using capabilities that don't exist in the standard Amplifier architecture. This document provides a comprehensive analysis and two recommended fix paths.

**Root Cause**: The current implementation requests `bundle.load` and `session.AmplifierSession` as coordinator capabilities, but these are NOT standard kernel capabilities. The correct pattern is to use `session.spawn` (registered by the app layer) or `PreparedBundle.spawn()` (from amplifier-foundation).

---

## Current Implementation Analysis

### What the Code Does Now (orchestrator.py:470-542)

```python
# CURRENT BROKEN APPROACH
load_bundle = self._coordinator.get_capability("bundle.load")
AmplifierSession = self._coordinator.get_capability("session.AmplifierSession")

bundle = await load_bundle(worker_bundle_path)
worker_session = AmplifierSession(
    config=bundle.config,
    parent_id=parent_session_id,
    coordinator=self._coordinator,
)
await worker_session.initialize()
await worker_session.run(worker_prompt)
```

### Why This Is Wrong

| Issue | Problem | Impact |
|-------|---------|--------|
| `bundle.load` capability | Not a standard kernel capability | Returns `None`, spawn fails |
| `session.AmplifierSession` capability | Not a standard kernel capability | Returns `None`, spawn fails |
| `bundle.config` | Bundles don't have a `.config` attribute | Would raise `AttributeError` |
| `coordinator=self._coordinator` | Sharing coordinators violates session isolation | Cross-session state corruption |
| Missing `prepare()` | Bundles must be prepared before session creation | Module resolver not set up |
| `worker_session.run()` | Wrong method name | Should be `execute()` |

### How the Tests Hide This

The current tests mock `bundle.load` and `session.AmplifierSession` returning working values:

```python
# tests/test_orchestrator.py:238-241
mock_coordinator.get_capability.side_effect = lambda name: {
    "bundle.load": mock_load_bundle,
    "session.AmplifierSession": mock_amplifier_session,
}.get(name)
```

This makes tests pass but doesn't reflect reality - these capabilities are never registered by the real app layer.

---

## How Session Spawning Actually Works in Amplifier

### The Two-Layer Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      APP LAYER (Policy)                         │
│  - Registers session.spawn capability                           │
│  - Defines how agents are resolved                              │
│  - Controls provider preferences                                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ registers
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    KERNEL/FOUNDATION (Mechanism)                │
│  - coordinator.get_capability("session.spawn")                  │
│  - PreparedBundle.spawn() handles actual session creation       │
│  - AmplifierSession created with proper mount plan              │
└─────────────────────────────────────────────────────────────────┘
```

### What PreparedBundle.spawn() Does (bundle.py:1111-1289)

1. **Composes bundles** (line 1189-1192): `effective_bundle = self.bundle.compose(child_bundle)`
2. **Creates mount plan** (line 1194): `child_mount_plan = effective_bundle.to_mount_plan()`
3. **Applies provider preferences** (line 1210-1216)
4. **Creates AmplifierSession** (line 1218-1234) with proper `parent_id`, `approval_system`, `display_system`
5. **Mounts module-source-resolver** (line 1237)
6. **Registers working_dir capability** (line 1239-1254)
7. **Initializes session** (line 1256): `await child_session.initialize()`
8. **Injects parent messages** (line 1258-1264)
9. **Executes and cleans up** (line 1284-1289)

**The foreman's current code skips steps 1-7 and 9.**

### How task Tool Does It (tool-task/__init__.py:592-670)

```python
# Get the registered spawn capability
spawn_fn = self.coordinator.get_capability("session.spawn")
if spawn_fn is None:
    return ToolResult(success=False, error={"message": "Session spawning not available..."})

# Call it with agent info
result = await spawn_fn(
    agent_name=agent_name,
    instruction=effective_instruction,
    parent_session=parent_session,
    agent_configs=agents,
    sub_session_id=sub_session_id,
)
```

The task tool **consumes** the capability - it doesn't create sessions directly.

---

## Recommended Fix: Option A (session.spawn Capability)

This is the **canonical pattern** matching how task tool works.

### Step 1: Require App Layer to Register Capability

The app layer (amplifier-app-cli or custom app) must register `session.spawn`:

```python
# In app.py or equivalent
async def main():
    bundle = await load_bundle("./bundle.md")
    prepared = await bundle.prepare()
    session = await prepared.create_session()
    
    # Register spawn capability
    async def spawn_capability(
        agent_name: str,
        instruction: str,
        parent_session: Any,
        agent_configs: dict[str, dict[str, Any]] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        # Load worker bundle
        worker_bundle = await load_bundle(agent_name)
        
        return await prepared.spawn(
            child_bundle=worker_bundle,
            instruction=instruction,
            parent_session=parent_session,
            **kwargs,
        )
    
    session.coordinator.register_capability("session.spawn", spawn_capability)
```

### Step 2: Update Orchestrator to Use Capability

```python
# In orchestrator.py - replace _maybe_spawn_worker implementation
async def _maybe_spawn_worker(self, issue_result: dict[str, Any], issue_tool: Any) -> None:
    """Spawn a worker using the registered session.spawn capability."""
    issue = issue_result.get("issue", {})
    issue_id = issue.get("id")

    if not issue_id or issue_id in self._spawned_issues:
        return

    self._spawned_issues.add(issue_id)

    # Route to appropriate worker pool
    pool_config = self._route_issue(issue)
    if not pool_config:
        self._append_spawn_error(issue_id, f"No worker pool found for issue")
        return

    worker_bundle = pool_config.get("worker_bundle")
    if not worker_bundle:
        self._append_spawn_error(issue_id, f"No worker_bundle in pool config")
        return

    # Get the spawn capability (registered by app layer)
    spawn = self._coordinator.get_capability("session.spawn")
    if not spawn:
        error = "Required capability 'session.spawn' not registered. " \
                "App layer must register this capability for worker spawning."
        logger.error(error)
        self._append_spawn_error(issue_id, error)
        return

    # Mark issue as in_progress
    try:
        await issue_tool.execute({
            "operation": "update",
            "params": {"issue_id": issue_id, "status": "in_progress"},
        })
    except Exception as e:
        logger.error(f"Failed to update issue status: {e}")

    # Build worker prompt
    worker_prompt = self._build_worker_prompt(issue)

    # Spawn worker in background
    try:
        logger.info(f"Spawning worker for issue {issue_id} via session.spawn")
        
        # Fire-and-forget spawn
        asyncio.create_task(
            self._run_spawn_and_handle_result(spawn, worker_bundle, worker_prompt, issue_id)
        )
        
        logger.info(f"Successfully initiated worker spawn for issue {issue_id}")
    except Exception as e:
        error = f"Failed to spawn worker: {e}"
        logger.error(error, exc_info=True)
        self._append_spawn_error(issue_id, error)

async def _run_spawn_and_handle_result(
    self, 
    spawn: Callable, 
    worker_bundle: str, 
    worker_prompt: str, 
    issue_id: str
) -> None:
    """Run spawn capability and handle result/errors."""
    try:
        result = await spawn(
            agent_name=worker_bundle,
            instruction=worker_prompt,
            parent_session=self._coordinator.session,
            agent_configs={},  # Workers are full bundles, not agent configs
        )
        logger.info(f"Worker completed for issue {issue_id}: {result.get('session_id')}")
    except Exception as e:
        error = f"Worker execution failed for issue {issue_id}: {e}"
        logger.error(error, exc_info=True)
        self._append_spawn_error(issue_id, error)

def _build_worker_prompt(self, issue: dict[str, Any]) -> str:
    """Build the instruction prompt for a worker."""
    issue_id = issue.get("id", "unknown")
    return f"""You are a worker assigned to complete this issue:

## Issue #{issue_id}: {issue.get("title", "Untitled")}

{issue.get("description", "No description")}

## Your Task
1. Complete the work described above
2. When done, use the issue_manager tool to update the issue:
   - operation: "update"
   - issue_id: "{issue_id}"
   - status: "completed"
   - Include a summary of what you did in the comment

If you need clarification, update the issue with status "pending_user_input".
"""
```

### Pros/Cons of Option A

| Pros | Cons |
|------|------|
| Matches canonical Amplifier patterns | Requires app layer setup |
| Clean separation of concerns | More moving parts |
| Works with any app that registers capability | Can't work standalone |
| Reuses foundation's tested spawn logic | |

---

## Alternative Fix: Option B (Direct PreparedBundle.spawn)

For cases where you need the orchestrator to work more standalone.

### Step 1: App Layer Provides PreparedBundle

```python
# In app.py
session.coordinator.register_capability("prepared_bundle", prepared)
session.coordinator.register_capability("bundle.load", load_bundle)
```

### Step 2: Orchestrator Uses PreparedBundle Directly

```python
async def _maybe_spawn_worker(self, issue_result: dict[str, Any], issue_tool: Any) -> None:
    """Spawn a worker using PreparedBundle.spawn() directly."""
    issue = issue_result.get("issue", {})
    issue_id = issue.get("id")

    if not issue_id or issue_id in self._spawned_issues:
        return

    self._spawned_issues.add(issue_id)

    pool_config = self._route_issue(issue)
    if not pool_config:
        return

    worker_bundle_path = pool_config.get("worker_bundle")
    if not worker_bundle_path:
        return

    # Get required capabilities
    prepared = self._coordinator.get_capability("prepared_bundle")
    load_bundle = self._coordinator.get_capability("bundle.load")
    
    if not prepared or not load_bundle:
        error = "Required capabilities 'prepared_bundle' and 'bundle.load' not available"
        self._append_spawn_error(issue_id, error)
        return

    # Mark issue as in_progress
    try:
        await issue_tool.execute({
            "operation": "update",
            "params": {"issue_id": issue_id, "status": "in_progress"},
        })
    except Exception as e:
        logger.error(f"Failed to update issue status: {e}")

    # Build worker prompt
    worker_prompt = self._build_worker_prompt(issue)

    # Spawn using PreparedBundle
    try:
        logger.info(f"Loading worker bundle: {worker_bundle_path}")
        worker_bundle = await load_bundle(worker_bundle_path)
        
        if not worker_bundle:
            self._append_spawn_error(issue_id, f"Failed to load bundle: {worker_bundle_path}")
            return

        logger.info(f"Spawning worker for issue {issue_id}")
        
        # Fire-and-forget spawn
        asyncio.create_task(
            self._run_prepared_spawn(prepared, worker_bundle, worker_prompt, issue_id)
        )
        
    except Exception as e:
        error = f"Failed to spawn worker: {e}"
        logger.error(error, exc_info=True)
        self._append_spawn_error(issue_id, error)

async def _run_prepared_spawn(
    self,
    prepared: Any,  # PreparedBundle
    worker_bundle: Any,  # Bundle
    worker_prompt: str,
    issue_id: str,
) -> None:
    """Run PreparedBundle.spawn() and handle result."""
    try:
        result = await prepared.spawn(
            child_bundle=worker_bundle,
            instruction=worker_prompt,
            compose=True,  # Inherit parent's providers/tools
            parent_session=self._coordinator.session,
        )
        logger.info(f"Worker completed for issue {issue_id}: {result.get('session_id')}")
    except Exception as e:
        error = f"Worker execution failed: {e}"
        logger.error(error, exc_info=True)
        self._append_spawn_error(issue_id, error)
```

### Pros/Cons of Option B

| Pros | Cons |
|------|------|
| More control over spawn process | More coupled to foundation internals |
| Can customize composition behavior | Still requires app layer to provide capabilities |
| Explicit bundle loading | More code in orchestrator |

---

## Capabilities That Must Be Registered

Regardless of which option you choose, the app layer must register certain capabilities:

| Capability | Option A | Option B | Purpose |
|------------|----------|----------|---------|
| `session.spawn` | **Required** | Not needed | Canonical spawn function |
| `prepared_bundle` | Not needed | **Required** | PreparedBundle instance |
| `bundle.load` | Not needed | **Required** | Bundle loading function |
| `session.instance` | Helpful | Helpful | Reference to current session |

### Example App Setup

```python
from amplifier_foundation import load_bundle

async def run_foreman():
    # Load and prepare the foreman bundle
    bundle = await load_bundle("./bundle.md")
    prepared = await bundle.prepare()
    session = await prepared.create_session()
    
    # === CRITICAL: Register capabilities for worker spawning ===
    
    # Option A: Register session.spawn
    async def spawn_capability(agent_name, instruction, parent_session, **kwargs):
        worker_bundle = await load_bundle(agent_name)
        return await prepared.spawn(
            child_bundle=worker_bundle,
            instruction=instruction,
            parent_session=parent_session,
        )
    session.coordinator.register_capability("session.spawn", spawn_capability)
    
    # OR Option B: Register prepared_bundle and bundle.load
    # session.coordinator.register_capability("prepared_bundle", prepared)
    # session.coordinator.register_capability("bundle.load", load_bundle)
    
    # Run the session
    async with session:
        response = await session.execute("Build me a calculator app")
        print(response)
```

---

## Test Updates Required

The existing tests mock capabilities that don't exist. Here's how to fix them:

### Current Test Pattern (WRONG)

```python
mock_coordinator.get_capability.side_effect = lambda name: {
    "bundle.load": mock_load_bundle,
    "session.AmplifierSession": mock_amplifier_session,  # WRONG
}.get(name)
```

### Fixed Test Pattern (Option A)

```python
mock_spawn_capability = AsyncMock(return_value={
    "output": "Worker completed",
    "session_id": "worker-123",
})

mock_coordinator.get_capability.side_effect = lambda name: {
    "session.spawn": mock_spawn_capability,
}.get(name)

# Verify spawn was called correctly
mock_spawn_capability.assert_called_once()
assert mock_spawn_capability.call_args.kwargs["agent_name"] == "git+https://..."
```

### Fixed Test Pattern (Option B)

```python
mock_prepared = MagicMock()
mock_prepared.spawn = AsyncMock(return_value={
    "output": "Worker completed",
    "session_id": "worker-123",
})
mock_load_bundle = AsyncMock(return_value=MockBundle())

mock_coordinator.get_capability.side_effect = lambda name: {
    "prepared_bundle": mock_prepared,
    "bundle.load": mock_load_bundle,
}.get(name)

# Verify spawn was called via prepared bundle
mock_prepared.spawn.assert_called_once()
```

See `tests/test_spawn_patterns.py` for comprehensive test patterns.

---

## Migration Checklist

### Phase 1: Update Orchestrator

- [ ] Choose Option A or Option B
- [ ] Remove references to `session.AmplifierSession` capability
- [ ] Update `_maybe_spawn_worker()` per chosen option
- [ ] Add `_build_worker_prompt()` helper method
- [ ] Add proper error handling for missing capabilities

### Phase 2: Update Tests

- [ ] Update mocks to use correct capabilities
- [ ] Add tests for missing capability errors
- [ ] Add tests for spawn success/failure paths
- [ ] Verify routing still works correctly

### Phase 3: Update Documentation

- [ ] Document required app layer setup
- [ ] Add example app.py showing capability registration
- [ ] Update ARCHITECTURE.md with spawn flow

### Phase 4: Integration Testing

- [ ] Test with real amplifier-app-cli
- [ ] Test worker bundle loading
- [ ] Test issue status updates
- [ ] Test error handling and recovery

---

## Quick Reference: Key File Locations

| Component | File | Key Lines |
|-----------|------|-----------|
| AmplifierSession | `amplifier_core/session.py` | 35-470 |
| ModuleCoordinator | `amplifier_core/coordinator.py` | 40-588 |
| PreparedBundle.spawn() | `amplifier_foundation/bundle.py` | 1111-1289 |
| task tool spawn | `tool-task/__init__.py` | 592-670 |
| Capability registration example | `examples/07_full_workflow.py` | 207-276 |
| Current foreman implementation | `orchestrator.py` | 470-570 |

---

## Conclusion

The foreman pattern is architecturally sound, but the implementation bypasses Amplifier's session spawning infrastructure. The fix requires:

1. **Stop trying to create AmplifierSession directly** - this bypasses critical setup
2. **Use the registered `session.spawn` capability** (Option A) or **PreparedBundle.spawn()** (Option B)
3. **Update tests to mock the correct capabilities**
4. **Ensure app layer registers required capabilities**

Option A (session.spawn capability) is recommended as it follows the canonical Amplifier pattern and provides the cleanest separation of concerns. Option B is viable for cases needing more control over the spawn process.
