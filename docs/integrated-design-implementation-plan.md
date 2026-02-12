# Integrated Design: Implementation Plan

**Version**: 1.1.0  
**Date**: 2026-02-04  
**Status**: Ready for Implementation  
**Prerequisites**: Review `integrated-design.md` and `integrated-design-code-analysis.md`

---

## Overview

This document provides the detailed implementation plan for the Integrated Session Spawning and Event-Driven Orchestration architecture. It incorporates learnings from the code analysis and specifies exact changes required across all repositories.

### Repositories Affected

| Repository | Changes |
|------------|---------|
| `amplifier-core` | Add new events |
| `amplifier-foundation` | New spawn module, EventRouter, triggers, background sessions |
| `amplifier-app-cli` | Refactor to use spawn_bundle() |
| `amplifier-bundle-foreman` | Refactor to use spawn_bundle(), adopt background_sessions config |

### Implementation Phases

| Phase | Name | Duration | Dependencies |
|-------|------|----------|--------------|
| 1A | Core spawn_bundle() | 1-2 weeks | None |
| 1B | Consumer Migration | 1 week | Phase 1A |
| 2 | EventRouter | 1 week | Phase 1A |
| 3 | Trigger Infrastructure | 1-2 weeks | Phase 2 |
| 4 | Background Session Manager | 1-2 weeks | Phase 2, 3 |
| 5 | Event-Driven Orchestrator | 1-2 weeks | Phase 4 |

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| SessionStorage protocol | **Sync** | Matches existing SessionStore; migrate to async in Phase 4 if needed |
| Agent name resolution | **App layer** | Agent resolution is app-layer policy; CLI resolves name→Bundle, then calls spawn_bundle() |
| Context inheritance params | **Strings** | Matches existing tool-delegate parameters (`"none"`, `"recent"`, `"all"`) for compatibility |
| session:completed emission | **spawn_bundle()** | spawn_bundle() is the coordination point for session lifecycle |

---

## Phase 1A: Core spawn_bundle() Primitive

**Goal**: Create the unified spawning primitive in foundation.

### 1A.1: Add Kernel Events

**Repository**: `amplifier-core`  
**File**: `amplifier_core/events.py`

**Changes**:

```python
# Add after line 64 (after CANCEL_COMPLETED)

# Session completion events (for spawn coordination)
SESSION_COMPLETED = "session:completed"
SESSION_ERROR = "session:error"
```

**Update ALL_EVENTS list** (around line 79):

```python
ALL_EVENTS = [
    # ... existing events ...
    CANCEL_REQUESTED,
    CANCEL_COMPLETED,
    SESSION_COMPLETED,  # Add
    SESSION_ERROR,      # Add
]
```

**Lines changed**: ~6

---

### 1A.2: Create Spawn Module

**Repository**: `amplifier-foundation`  
**File**: `amplifier_foundation/spawn.py` (NEW)

```python
"""
Unified session spawning primitive.

This module provides the core spawn_bundle() function that all session
spawning in Amplifier should use. It handles:
- Bundle resolution (URI, Bundle, or PreparedBundle)
- Configuration inheritance (providers, tools, hooks)
- Infrastructure wiring (resolvers, sys.path, cancellation)
- Session persistence (optional)
- Background execution (optional)
"""

import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol

if TYPE_CHECKING:
    from amplifier_core import AmplifierSession
    from amplifier_foundation.bundle import Bundle, PreparedBundle

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class SpawnResult:
    """Result of spawning a bundle."""
    
    output: str
    """Final response from the spawned session."""
    
    session_id: str
    """Session ID for potential resumption."""
    
    turn_count: int
    """Number of turns executed."""


# =============================================================================
# Protocols
# =============================================================================

class SessionStorage(Protocol):
    """
    Protocol for session persistence.
    
    Implementations handle storing and retrieving session state for
    resumption. The CLI's SessionStore is the reference implementation.
    
    Note: Methods are sync to match existing SessionStore. If async
    storage is needed, wrap in run_in_executor.
    """
    
    def save(
        self,
        session_id: str,
        transcript: list[dict],
        metadata: dict,
    ) -> None:
        """Persist session state."""
        ...
    
    def load(
        self,
        session_id: str,
    ) -> tuple[list[dict], dict]:
        """Load session state. Returns (transcript, metadata)."""
        ...
    
    def exists(self, session_id: str) -> bool:
        """Check if session exists in storage."""
        ...


# =============================================================================
# Helper Functions
# =============================================================================

def _merge_module_lists(
    base: list[dict],
    overlay: list[dict],
) -> list[dict]:
    """
    Merge two module config lists. Overlay wins on conflict.
    
    Args:
        base: Base list (e.g., parent's tools)
        overlay: Overlay list (e.g., bundle's tools)
        
    Returns:
        Merged list with overlay taking precedence for same module names.
    """
    result = list(base)
    overlay_modules = {m.get("module") for m in overlay}
    
    # Remove base modules that overlay overrides
    result = [m for m in result if m.get("module") not in overlay_modules]
    
    # Add all overlay modules
    result.extend(overlay)
    
    return result


def _filter_modules(
    modules: list[dict],
    inherit: bool | list[str],
) -> list[dict]:
    """
    Filter module list based on inheritance policy.
    
    Args:
        modules: List of module configs
        inherit: 
            - False: Return empty list
            - True: Return all modules
            - list[str]: Return only modules with names in list
            
    Returns:
        Filtered module list.
    """
    if inherit is False:
        return []
    if inherit is True:
        return list(modules)
    # inherit is a list of module names
    return [m for m in modules if m.get("module") in inherit]


def _share_sys_paths(
    parent_session: "AmplifierSession",
) -> list[str]:
    """
    Get sys.path entries that should be shared with child sessions.
    
    Collects paths from:
    1. Parent loader's _added_paths
    2. bundle_package_paths capability
    
    Returns:
        List of paths to add to sys.path.
    """
    paths_to_share: list[str] = []
    
    # Source 1: Module paths from parent loader
    if hasattr(parent_session, "loader") and parent_session.loader is not None:
        parent_added_paths = getattr(parent_session.loader, "_added_paths", [])
        paths_to_share.extend(parent_added_paths)
    
    # Source 2: Bundle package paths capability
    bundle_package_paths = parent_session.coordinator.get_capability(
        "bundle_package_paths"
    )
    if bundle_package_paths:
        paths_to_share.extend(bundle_package_paths)
    
    return paths_to_share


def _extract_recent_turns(
    messages: list[dict],
    n: int,
) -> list[dict]:
    """
    Extract the last N user→assistant turns from messages.
    
    Args:
        messages: Full message list
        n: Number of turns to extract
        
    Returns:
        Last N turns worth of messages.
    """
    # Find indices where user messages start (turn boundaries)
    turn_starts = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    
    if len(turn_starts) <= n:
        return messages
    
    start_index = turn_starts[-n]
    return messages[start_index:]


def _build_context_messages(
    parent_session: "AmplifierSession",
    context_depth: str,
    context_scope: str,
    context_turns: int,
) -> list[dict]:
    """
    Build context messages to inherit from parent.
    
    Args:
        parent_session: Parent session to get context from
        context_depth: "none" | "recent" | "all"
        context_scope: "conversation" | "agents" | "full"
        context_turns: Number of turns for "recent" depth
        
    Returns:
        List of messages to inject into child context.
    """
    if context_depth == "none":
        return []
    
    context = parent_session.coordinator.get("context")
    if not context or not hasattr(context, "get_messages"):
        return []
    
    # This is sync in current implementation; may need async wrapper
    messages = asyncio.get_event_loop().run_until_complete(
        context.get_messages()
    ) if asyncio.iscoroutinefunction(context.get_messages) else context.get_messages()
    
    # Filter by scope
    if context_scope == "conversation":
        # Only user/assistant messages
        messages = [m for m in messages if m.get("role") in ("user", "assistant")]
    elif context_scope == "agents":
        # Include delegate tool results
        messages = [
            m for m in messages
            if m.get("role") in ("user", "assistant")
            or (m.get("role") == "tool" and "delegate" in str(m.get("name", "")))
        ]
    # "full" = all messages
    
    # Apply depth
    if context_depth == "recent":
        messages = _extract_recent_turns(messages, context_turns)
    
    return messages


# =============================================================================
# Main Function
# =============================================================================

async def spawn_bundle(
    # === What to spawn ===
    bundle: "PreparedBundle | Bundle | str",
    instruction: str,
    parent_session: "AmplifierSession",
    
    # === Inheritance controls ===
    inherit_providers: bool = True,
    inherit_tools: bool | list[str] = False,
    inherit_hooks: bool | list[str] = False,
    
    # === Context inheritance (matches tool-delegate) ===
    context_depth: str = "none",  # "none" | "recent" | "all"
    context_scope: str = "conversation",  # "conversation" | "agents" | "full"
    context_turns: int = 5,
    
    # === Session identity ===
    session_id: str | None = None,
    session_name: str | None = None,
    
    # === Persistence ===
    session_storage: SessionStorage | None = None,
    
    # === Execution control ===
    timeout: float | None = None,
    background: bool = False,
    
    # === Event routing (for background completion) ===
    event_router: Any | None = None,
) -> SpawnResult:
    """
    Spawn a bundle as a sub-session.
    
    This is THE primitive for all session spawning in Amplifier. All other
    spawning patterns (agents, self-delegation, workers) should use this
    function or build on it.
    
    Args:
        bundle: What to spawn - PreparedBundle, Bundle, or URI string
        instruction: The prompt/instruction to execute
        parent_session: Parent for inheritance and lineage
        inherit_providers: Copy parent's providers if bundle has none
        inherit_tools: Which parent tools to include (False/True/list)
        inherit_hooks: Which parent hooks to include (False/True/list)
        context_depth: How much parent context ("none"/"recent"/"all")
        context_scope: Which content ("conversation"/"agents"/"full")
        context_turns: Number of turns for "recent" depth
        session_id: Explicit session ID (generated if None)
        session_name: Human-readable name for ID generation
        session_storage: Persistence implementation (optional)
        timeout: Maximum execution time in seconds
        background: If True, return immediately, session runs in background
        event_router: EventRouter for background completion events
        
    Returns:
        SpawnResult with output, session_id, and turn_count
        
    Raises:
        BundleLoadError: If bundle URI cannot be loaded
        BundleValidationError: If bundle is invalid
        TimeoutError: If execution exceeds timeout
    """
    from amplifier_core import AmplifierSession
    from amplifier_foundation import generate_sub_session_id, load_bundle
    from amplifier_foundation.bundle import Bundle, PreparedBundle
    
    # =========================================================================
    # PHASE 1: Bundle Resolution
    # =========================================================================
    
    if isinstance(bundle, str):
        # Load bundle from URI
        loaded_bundle = await load_bundle(bundle)
        prepared = await loaded_bundle.prepare()
    elif isinstance(bundle, Bundle):
        prepared = await bundle.prepare()
    else:
        # Already a PreparedBundle
        prepared = bundle
    
    bundle_name = prepared.bundle.name or "unnamed"
    
    # =========================================================================
    # PHASE 2: Configuration Inheritance
    # =========================================================================
    
    config = dict(prepared.mount_plan)
    
    # --- Provider inheritance ---
    if inherit_providers and not config.get("providers"):
        parent_providers = parent_session.config.get("providers", [])
        if parent_providers:
            config["providers"] = list(parent_providers)
            logger.debug(f"Inherited {len(parent_providers)} providers from parent")
    
    # --- Tool inheritance ---
    if inherit_tools:
        parent_tools = parent_session.config.get("tools", [])
        bundle_tools = config.get("tools", [])
        filtered_parent = _filter_modules(parent_tools, inherit_tools)
        config["tools"] = _merge_module_lists(filtered_parent, bundle_tools)
        logger.debug(f"Inherited {len(filtered_parent)} tools from parent")
    
    # --- Hook inheritance ---
    if inherit_hooks:
        parent_hooks = parent_session.config.get("hooks", [])
        bundle_hooks = config.get("hooks", [])
        filtered_parent = _filter_modules(parent_hooks, inherit_hooks)
        config["hooks"] = _merge_module_lists(filtered_parent, bundle_hooks)
        logger.debug(f"Inherited {len(filtered_parent)} hooks from parent")
    
    # =========================================================================
    # PHASE 3: Session Identity
    # =========================================================================
    
    if not session_id:
        session_id = generate_sub_session_id(
            agent_name=session_name or bundle_name,
            parent_session_id=parent_session.session_id,
            parent_trace_id=getattr(parent_session, "trace_id", None),
        )
    
    # =========================================================================
    # PHASE 4: Session Creation
    # =========================================================================
    
    approval_system = parent_session.coordinator.approval_system
    display_system = parent_session.coordinator.display_system
    
    child_session = AmplifierSession(
        config=config,
        loader=None,  # Each session gets its own loader
        session_id=session_id,
        parent_id=parent_session.session_id,
        approval_system=approval_system,
        display_system=display_system,
    )
    
    # =========================================================================
    # PHASE 5: Infrastructure Wiring (BEFORE initialize)
    # =========================================================================
    
    # --- Module resolver ---
    parent_resolver = parent_session.coordinator.get("module-source-resolver")
    if parent_resolver:
        await child_session.coordinator.mount("module-source-resolver", parent_resolver)
    elif hasattr(prepared, "resolver") and prepared.resolver:
        await child_session.coordinator.mount("module-source-resolver", prepared.resolver)
    
    # --- sys.path sharing ---
    paths_to_share = _share_sys_paths(parent_session)
    for path in paths_to_share:
        if path not in sys.path:
            sys.path.insert(0, path)
    if paths_to_share:
        logger.debug(f"Shared {len(paths_to_share)} sys.path entries")
    
    # =========================================================================
    # PHASE 6: Initialization
    # =========================================================================
    
    await child_session.initialize()
    
    # =========================================================================
    # PHASE 7: Post-Initialize Wiring
    # =========================================================================
    
    # --- Cancellation propagation (skip for background) ---
    parent_cancellation = None
    child_cancellation = None
    if not background:
        parent_cancellation = parent_session.coordinator.cancellation
        child_cancellation = child_session.coordinator.cancellation
        parent_cancellation.register_child(child_cancellation)
        logger.debug(f"Registered child cancellation for {session_id}")
    
    # --- Mention resolver inheritance ---
    parent_mention_resolver = parent_session.coordinator.get_capability("mention_resolver")
    if parent_mention_resolver:
        child_session.coordinator.register_capability(
            "mention_resolver", parent_mention_resolver
        )
    
    # --- Mention deduplicator inheritance ---
    parent_deduplicator = parent_session.coordinator.get_capability("mention_deduplicator")
    if parent_deduplicator:
        child_session.coordinator.register_capability(
            "mention_deduplicator", parent_deduplicator
        )
    
    # --- Working directory inheritance ---
    parent_working_dir = parent_session.coordinator.get_capability("session.working_dir")
    if parent_working_dir:
        child_session.coordinator.register_capability(
            "session.working_dir", parent_working_dir
        )
    
    # --- Nested spawning capability ---
    async def child_spawn_capability(**kwargs) -> dict:
        result = await spawn_bundle(
            parent_session=child_session,
            session_storage=session_storage,
            event_router=event_router,
            **kwargs,
        )
        return {"output": result.output, "session_id": result.session_id}
    
    child_session.coordinator.register_capability("session.spawn", child_spawn_capability)
    
    # =========================================================================
    # PHASE 8: Context Inheritance
    # =========================================================================
    
    if context_depth != "none":
        parent_messages = _build_context_messages(
            parent_session, context_depth, context_scope, context_turns
        )
        if parent_messages:
            child_context = child_session.coordinator.get("context")
            if child_context and hasattr(child_context, "add_message"):
                for msg in parent_messages:
                    await child_context.add_message(msg)
                logger.debug(f"Inherited {len(parent_messages)} context messages")
    
    # =========================================================================
    # PHASE 9: Execution
    # =========================================================================
    
    if background:
        # Fire-and-forget: spawn as asyncio task
        task = asyncio.create_task(
            _execute_background_session(
                child_session,
                instruction,
                session_id,
                bundle_name,
                session_storage,
                event_router,
            )
        )
        # Return immediately
        return SpawnResult(
            output="[Background session started]",
            session_id=session_id,
            turn_count=0,
        )
    
    # Foreground execution
    try:
        if timeout:
            response = await asyncio.wait_for(
                child_session.execute(instruction),
                timeout=timeout,
            )
        else:
            response = await child_session.execute(instruction)
    finally:
        # Unregister cancellation BEFORE cleanup
        if parent_cancellation and child_cancellation:
            parent_cancellation.unregister_child(child_cancellation)
            logger.debug(f"Unregistered child cancellation for {session_id}")
    
    # =========================================================================
    # PHASE 10: Persistence
    # =========================================================================
    
    if session_storage:
        context = child_session.coordinator.get("context")
        transcript = []
        if context and hasattr(context, "get_messages"):
            transcript = await context.get_messages()
        
        metadata = {
            "session_id": session_id,
            "parent_id": parent_session.session_id,
            "bundle_name": bundle_name,
            "created": datetime.now(UTC).isoformat(),
            "turn_count": 1,
        }
        
        session_storage.save(session_id, transcript, metadata)
        logger.debug(f"Persisted session {session_id}")
    
    # =========================================================================
    # PHASE 11: Cleanup & Return
    # =========================================================================
    
    await child_session.cleanup()
    
    return SpawnResult(
        output=response,
        session_id=session_id,
        turn_count=1,
    )


async def _execute_background_session(
    session: "AmplifierSession",
    instruction: str,
    session_id: str,
    bundle_name: str,
    session_storage: SessionStorage | None,
    event_router: Any | None,
) -> None:
    """
    Execute a background session and emit completion event.
    
    This runs as an asyncio task for fire-and-forget spawning.
    """
    try:
        response = await session.execute(instruction)
        
        # Persist if storage provided
        if session_storage:
            context = session.coordinator.get("context")
            transcript = []
            if context and hasattr(context, "get_messages"):
                transcript = await context.get_messages()
            
            metadata = {
                "session_id": session_id,
                "bundle_name": bundle_name,
                "created": datetime.now(UTC).isoformat(),
                "turn_count": 1,
            }
            session_storage.save(session_id, transcript, metadata)
        
        # Emit completion event
        if event_router:
            await event_router.emit(
                "session:completed",
                {
                    "session_id": session_id,
                    "bundle_name": bundle_name,
                    "output": response,
                    "success": True,
                },
            )
        
    except Exception as e:
        logger.error(f"Background session {session_id} failed: {e}")
        
        # Emit error event
        if event_router:
            await event_router.emit(
                "session:error",
                {
                    "session_id": session_id,
                    "bundle_name": bundle_name,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
    finally:
        await session.cleanup()
```

**Lines**: ~450

---

### 1A.3: Export from Foundation

**Repository**: `amplifier-foundation`  
**File**: `amplifier_foundation/__init__.py`

**Add exports**:

```python
from amplifier_foundation.spawn import (
    SessionStorage,
    SpawnResult,
    spawn_bundle,
)
```

---

### 1A.4: Tests for spawn_bundle()

**Repository**: `amplifier-foundation`  
**File**: `tests/test_spawn.py` (NEW)

Create comprehensive tests covering:
- Basic spawning with Bundle
- Spawning with URI string
- Provider inheritance
- Tool inheritance (True, False, list)
- Hook inheritance
- Context inheritance (all depth/scope combinations)
- Session persistence
- Timeout handling
- Background execution (mock EventRouter)
- Cancellation propagation
- Nested spawning capability

**Estimated lines**: ~400

---

### 1A.5: Testing & Validation

**Unit Tests** (`amplifier-foundation/tests/test_spawn.py`):

| Test Case | Validates |
|-----------|-----------|
| `test_spawn_with_bundle_object` | Basic spawning with Bundle instance |
| `test_spawn_with_uri_string` | Bundle resolution from URI |
| `test_spawn_with_prepared_bundle` | PreparedBundle passthrough |
| `test_provider_inheritance_when_empty` | Providers copied when bundle has none |
| `test_provider_inheritance_when_defined` | Bundle providers NOT overwritten |
| `test_tool_inheritance_false` | No parent tools inherited |
| `test_tool_inheritance_true` | All parent tools inherited |
| `test_tool_inheritance_list` | Only specified tools inherited |
| `test_hook_inheritance_modes` | Same patterns as tool inheritance |
| `test_context_depth_none` | No context inherited |
| `test_context_depth_recent` | Only recent N turns inherited |
| `test_context_depth_all` | Full context inherited |
| `test_context_scope_conversation` | Only user/assistant messages |
| `test_context_scope_agents` | Includes delegate results |
| `test_context_scope_full` | All tool results included |
| `test_session_persistence` | SessionStorage.save() called with correct data |
| `test_timeout_handling` | TimeoutError raised when exceeded |
| `test_background_execution` | Returns immediately, task runs async |
| `test_cancellation_propagation` | Child cancelled when parent cancelled |
| `test_nested_spawn_capability` | Child can spawn grandchildren |

**Validation Criteria** (all must pass to complete Phase 1A):

| Criterion | How to Verify |
|-----------|---------------|
| Events exported from core | `from amplifier_core.events import SESSION_COMPLETED, SESSION_ERROR` succeeds |
| spawn_bundle importable | `from amplifier_foundation import spawn_bundle, SpawnResult, SessionStorage` succeeds |
| All unit tests pass | `pytest tests/test_spawn.py -v` passes |
| Type checking passes | `pyright amplifier_foundation/spawn.py` reports no errors |
| Basic smoke test | spawn_bundle() successfully spawns a minimal bundle and returns SpawnResult |

**Smoke Test Script**:
```python
# Run in amplifier-foundation repo
import asyncio
from amplifier_foundation import spawn_bundle, SpawnResult
from amplifier_foundation.bundle import Bundle

async def smoke_test():
    # Create minimal parent session mock
    parent = MockParentSession()
    
    # Create minimal bundle
    bundle = Bundle(name="test", instruction="Say hello")
    
    # Spawn and verify
    result = await spawn_bundle(
        bundle=bundle,
        instruction="Test prompt",
        parent_session=parent,
    )
    
    assert isinstance(result, SpawnResult)
    assert result.session_id is not None
    assert result.output is not None
    print("✓ Phase 1A smoke test passed")

asyncio.run(smoke_test())
```

---

## Phase 1B: Consumer Migration

**Goal**: Refactor CLI and Foreman to use spawn_bundle().

### 1B.1: Refactor CLI spawn_sub_session()

**Repository**: `amplifier-app-cli`  
**File**: `amplifier_app_cli/session_spawner.py`

**Strategy**: Keep `spawn_sub_session()` as the app-layer function that:
1. Resolves agent names to configs (app policy)
2. Applies tool/hook filtering policies (app policy)
3. Calls `spawn_bundle()` (foundation mechanism)

**Changes**:

```python
# Replace current implementation (lines 273-658) with:

async def spawn_sub_session(
    agent_name: str,
    instruction: str,
    parent_session: AmplifierSession,
    agent_configs: dict[str, dict],
    sub_session_id: str | None = None,
    tool_inheritance: dict[str, list[str]] | None = None,
    hook_inheritance: dict[str, list[str]] | None = None,
    orchestrator_config: dict | None = None,
    parent_messages: list[dict] | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
    provider_preferences: list | None = None,
    self_delegation_depth: int = 0,
) -> dict:
    """
    Spawn sub-session with agent configuration overlay.
    
    This is the app-layer wrapper that handles:
    - Agent name → config resolution (app policy)
    - Tool/hook filtering policies (app policy)
    - Self-delegation special handling (app policy)
    
    Then delegates to spawn_bundle() for the actual spawning.
    """
    from amplifier_foundation import spawn_bundle
    from amplifier_foundation.bundle import Bundle
    
    from .session_store import SessionStore
    
    # =======================================================================
    # APP POLICY: Agent Resolution
    # =======================================================================
    
    if agent_name == "self":
        # Self-delegation: use parent's config
        agent_config = {}
        bundle_name = "self"
    elif agent_name not in agent_configs:
        raise ValueError(f"Agent '{agent_name}' not found in configuration")
    else:
        agent_config = agent_configs[agent_name]
        bundle_name = agent_name
    
    # =======================================================================
    # APP POLICY: Config Merging with Filtering
    # =======================================================================
    
    merged_config = merge_configs(parent_session.config, agent_config)
    
    # Apply tool filtering (app policy)
    if tool_inheritance:
        agent_tool_modules = [t.get("module") for t in agent_config.get("tools", [])]
        merged_config = _filter_tools(merged_config, tool_inheritance, agent_tool_modules)
    
    # Apply hook filtering (app policy)
    if hook_inheritance:
        agent_hook_modules = [h.get("module") for h in agent_config.get("hooks", [])]
        merged_config = _filter_hooks(merged_config, hook_inheritance, agent_hook_modules)
    
    # Apply provider preferences
    if provider_preferences:
        from amplifier_foundation import apply_provider_preferences
        merged_config = apply_provider_preferences(merged_config, provider_preferences)
    elif provider_override or model_override:
        merged_config = _apply_provider_override(merged_config, provider_override, model_override)
    
    # Apply orchestrator config
    if orchestrator_config:
        if "session" not in merged_config:
            merged_config["session"] = {}
        if "orchestrator" not in merged_config["session"]:
            merged_config["session"]["orchestrator"] = {}
        if "config" not in merged_config["session"]["orchestrator"]:
            merged_config["session"]["orchestrator"]["config"] = {}
        merged_config["session"]["orchestrator"]["config"].update(orchestrator_config)
    
    # =======================================================================
    # APP POLICY: Create inline Bundle from merged config
    # =======================================================================
    
    # Create a Bundle object from the merged config
    inline_bundle = Bundle(
        name=bundle_name,
        instruction=agent_config.get("instruction"),
        providers=merged_config.get("providers", []),
        tools=merged_config.get("tools", []),
        hooks=merged_config.get("hooks", []),
        # ... other fields from merged_config
    )
    
    # =======================================================================
    # DELEGATE TO FOUNDATION: spawn_bundle()
    # =======================================================================
    
    # Notify display system
    display_system = parent_session.coordinator.display_system
    if hasattr(display_system, "push_nesting"):
        display_system.push_nesting()
    
    try:
        result = await spawn_bundle(
            bundle=inline_bundle,
            instruction=instruction,
            parent_session=parent_session,
            inherit_providers=True,  # Already merged above
            inherit_tools=False,     # Already filtered above
            inherit_hooks=False,     # Already filtered above
            session_id=sub_session_id,
            session_name=bundle_name,
            session_storage=SessionStore(),
        )
    finally:
        if hasattr(display_system, "pop_nesting"):
            display_system.pop_nesting()
    
    # Register self_delegation_depth on child (for next level)
    # Note: This may need adjustment based on how spawn_bundle handles capabilities
    
    return {"output": result.output, "session_id": result.session_id}
```

**Note**: This is a simplified version. The actual implementation needs to handle:
- System instruction injection
- Bundle context extraction for resume
- Self-delegation depth tracking
- Approval provider registration

**Lines changed**: ~300 (refactor), may need to keep some utility code

---

### 1B.2: Refactor Foreman Worker Spawning

**Repository**: `amplifier-bundle-foreman`  
**File**: `modules/orchestrator-foreman/amplifier_module_orchestrator_foreman/orchestrator.py`

**Strategy**: Replace manual spawning with spawn_bundle().

**Create ForemanSessionStorage** (add to orchestrator.py):

```python
class ForemanSessionStorage:
    """
    SessionStorage implementation for foreman workers.
    
    Writes to the same location as CLI's SessionStore so worker
    sessions appear in `amplifier session list`.
    """
    
    def __init__(self, working_dir: str | None = None):
        self.working_dir = working_dir
    
    def _get_session_dir(self, session_id: str) -> Path:
        """Get session directory path."""
        if self.working_dir:
            cwd = Path(self.working_dir).resolve()
        else:
            cwd = Path.cwd().resolve()
        
        slug = str(cwd).replace("/", "-").replace("\\", "-").replace(":", "")
        if not slug.startswith("-"):
            slug = "-" + slug
        
        return (
            Path.home()
            / ".amplifier"
            / "projects"
            / slug
            / "sessions"
            / session_id
        )
    
    def save(
        self,
        session_id: str,
        transcript: list[dict],
        metadata: dict,
    ) -> None:
        """Save session state."""
        import json
        
        session_dir = self._get_session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        
        # Write metadata
        with open(session_dir / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)
        
        # Write transcript
        with open(session_dir / "transcript.jsonl", "w") as f:
            for msg in transcript:
                f.write(json.dumps(msg) + "\n")
    
    def load(self, session_id: str) -> tuple[list[dict], dict]:
        """Load session state."""
        import json
        
        session_dir = self._get_session_dir(session_id)
        
        with open(session_dir / "metadata.json") as f:
            metadata = json.load(f)
        
        transcript = []
        transcript_path = session_dir / "transcript.jsonl"
        if transcript_path.exists():
            with open(transcript_path) as f:
                for line in f:
                    if line.strip():
                        transcript.append(json.loads(line))
        
        return transcript, metadata
    
    def exists(self, session_id: str) -> bool:
        """Check if session exists."""
        return (self._get_session_dir(session_id) / "metadata.json").exists()
```

**Replace _run_spawn_and_handle_result()** (lines 809-973):

```python
async def _run_spawn_and_handle_result(
    self,
    worker_bundle_uri: str,
    worker_prompt: str,
    issue_id: str,
) -> None:
    """
    Spawn worker session using spawn_bundle().
    
    This replaces the previous manual bundle loading and session creation.
    """
    from amplifier_foundation import spawn_bundle
    
    await self._emit_diagnostic(
        "foreman:worker:task_started",
        {"issue_id": issue_id, "bundle_uri": worker_bundle_uri},
    )
    
    parent_session = getattr(self._coordinator, "session", None)
    if not parent_session:
        self._append_spawn_error(issue_id, "No parent session available")
        return
    
    # Get working directory for storage path
    parent_working_dir = parent_session.coordinator.get_capability("session.working_dir")
    
    try:
        result = await spawn_bundle(
            bundle=worker_bundle_uri,
            instruction=worker_prompt,
            parent_session=parent_session,
            inherit_providers=True,
            session_name=f"worker-{issue_id[:8]}",
            session_storage=ForemanSessionStorage(parent_working_dir),
            background=False,  # We're already in a task
            event_router=self._event_router,  # If available
        )
        
        logger.info(f"Worker completed for issue {issue_id}, session: {result.session_id}")
        
        await self._emit_diagnostic(
            "foreman:worker:execution_completed",
            {"issue_id": issue_id, "worker_session_id": result.session_id},
        )
        
    except Exception as e:
        logger.error(f"Worker failed for issue {issue_id}: {e}", exc_info=True)
        self._append_spawn_error(issue_id, f"Worker execution failed: {e}")
        
        await self._emit_diagnostic(
            "foreman:worker:execution_failed",
            {"issue_id": issue_id, "error": str(e)},
        )
```

**Delete**:
- `_write_worker_session_state()` method (lines 206-303) — now handled by spawn_bundle + ForemanSessionStorage

**Lines changed**: ~150 deleted, ~100 added

---

### 1B.3: Testing & Validation

**Unit Tests**:

| Repository | Test Case | Validates |
|------------|-----------|-----------|
| `amplifier-app-cli` | `test_spawn_sub_session_calls_spawn_bundle` | spawn_bundle() is called with correct params |
| `amplifier-app-cli` | `test_agent_resolution_app_layer` | Agent name → config resolution works |
| `amplifier-app-cli` | `test_self_delegation_handling` | "self" agent uses parent config |
| `amplifier-app-cli` | `test_tool_filtering_policy` | Tool inheritance filtering applied |
| `amplifier-app-cli` | `test_provider_preferences_applied` | Provider preferences passed through |
| `amplifier-bundle-foreman` | `test_worker_spawn_uses_spawn_bundle` | spawn_bundle() called for workers |
| `amplifier-bundle-foreman` | `test_foreman_session_storage_save` | Sessions saved to correct path |
| `amplifier-bundle-foreman` | `test_foreman_session_storage_load` | Sessions loadable after save |

**Integration Tests**:

| Test | Description |
|------|-------------|
| CLI agent delegation | `amplifier run` with agent delegation works as before |
| CLI session resumption | Resumed sessions have correct context |
| Foreman worker spawning | Workers spawn and complete tasks |
| Worker session visibility | Worker sessions appear in `amplifier session list` |

**Validation Criteria** (all must pass to complete Phase 1B):

| Criterion | How to Verify |
|-----------|---------------|
| CLI behavior unchanged | Existing agent delegation tests pass |
| Foreman behavior unchanged | Foreman can spawn workers and they complete |
| No regressions | Full test suite passes in both repos |
| Session storage compatible | CLI's SessionStore satisfies SessionStorage protocol |

**Regression Test Script** (CLI):
```bash
# In amplifier-app-cli repo
pytest tests/test_session_spawner.py -v

# Manual verification
amplifier run --bundle foundation:bundles/chat "delegate to explorer to list files"
# Should spawn explorer agent and return results
```

**Regression Test Script** (Foreman):
```bash
# In amplifier-bundle-foreman repo
pytest tests/test_orchestrator.py -v

# Manual verification with foreman bundle
amplifier run --bundle foreman:bundle "create an issue to add a README"
# Should spawn worker and process the issue
```

---

## Phase 2: EventRouter

**Goal**: Enable cross-session event communication.

### 2.1: Create EventRouter

**Repository**: `amplifier-foundation`  
**File**: `amplifier_foundation/events.py` (NEW)

```python
"""
Cross-session event routing.

Provides pub/sub infrastructure for sessions to communicate via events.
"""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


@dataclass
class SessionEvent:
    """An event emitted by a session."""
    
    name: str
    """Event name (e.g., 'session:completed', 'work:done')."""
    
    data: dict[str, Any]
    """Event payload."""
    
    source_session_id: str | None
    """Session that emitted the event."""
    
    timestamp: datetime
    """When the event was emitted."""


class EventRouter:
    """
    Routes events between sessions.
    
    Provides a simple pub/sub mechanism for cross-session communication.
    Sessions can emit events and subscribe to events from other sessions.
    
    Example:
        router = EventRouter()
        
        # Subscribe to events
        async for event in router.subscribe(["work:completed"]):
            print(f"Work done: {event.data}")
        
        # Emit an event
        await router.emit("work:completed", {"task_id": "123"}, session_id="abc")
    """
    
    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._lock = asyncio.Lock()
    
    async def emit(
        self,
        event_name: str,
        data: dict[str, Any],
        source_session_id: str | None = None,
    ) -> None:
        """
        Emit an event to all subscribers.
        
        Args:
            event_name: Name of the event
            data: Event payload
            source_session_id: Session emitting the event
        """
        event = SessionEvent(
            name=event_name,
            data=data,
            source_session_id=source_session_id,
            timestamp=datetime.now(UTC),
        )
        
        async with self._lock:
            # Deliver to specific event subscribers
            for queue in self._subscribers.get(event_name, []):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning(f"Event queue full for {event_name}")
            
            # Deliver to wildcard subscribers
            for queue in self._subscribers.get("*", []):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning(f"Wildcard event queue full")
        
        logger.debug(
            f"Event '{event_name}' emitted to "
            f"{len(self._subscribers.get(event_name, []))} subscribers"
        )
    
    async def subscribe(
        self,
        event_names: list[str],
        source_sessions: list[str] | None = None,
        queue_size: int = 100,
    ) -> AsyncIterator[SessionEvent]:
        """
        Subscribe to events.
        
        Args:
            event_names: Events to subscribe to (use ["*"] for all)
            source_sessions: Filter by source session (None = all)
            queue_size: Maximum queued events
            
        Yields:
            SessionEvent objects as they arrive.
        """
        queue: asyncio.Queue[SessionEvent] = asyncio.Queue(maxsize=queue_size)
        
        # Register with each event name
        async with self._lock:
            for name in event_names:
                self._subscribers[name].append(queue)
        
        try:
            while True:
                event = await queue.get()
                
                # Filter by source session if specified
                if source_sessions and event.source_session_id not in source_sessions:
                    continue
                
                yield event
        finally:
            # Unsubscribe
            async with self._lock:
                for name in event_names:
                    if queue in self._subscribers[name]:
                        self._subscribers[name].remove(queue)
    
    def create_session_emitter(
        self,
        session_id: str,
    ) -> "SessionEmitter":
        """
        Create an emitter bound to a specific session.
        
        Args:
            session_id: Session to bind to
            
        Returns:
            SessionEmitter that auto-fills source_session_id
        """
        return SessionEmitter(self, session_id)


class SessionEmitter:
    """Event emitter bound to a specific session."""
    
    def __init__(self, router: EventRouter, session_id: str):
        self._router = router
        self._session_id = session_id
    
    async def emit(self, event_name: str, data: dict[str, Any]) -> None:
        """Emit an event from this session."""
        await self._router.emit(event_name, data, self._session_id)
```

**Lines**: ~150

---

### 2.2: Integrate EventRouter with spawn_bundle()

**Repository**: `amplifier-foundation`  
**File**: `amplifier_foundation/spawn.py`

**Add** (in Phase 7 post-initialize wiring):

```python
# --- Event emitter capability ---
if event_router:
    session_emitter = event_router.create_session_emitter(session_id)
    child_session.coordinator.register_capability("event.emit", session_emitter.emit)
```

---

### 2.3: Export EventRouter

**Repository**: `amplifier-foundation`  
**File**: `amplifier_foundation/__init__.py`

```python
from amplifier_orchestration import EventRouter, SessionEvent, SessionEmitter
```

---

### 2.4: Testing & Validation

**Unit Tests** (`amplifier-foundation/tests/test_events.py`):

| Test Case | Validates |
|-----------|-----------|
| `test_emit_to_single_subscriber` | Basic emit/receive works |
| `test_emit_to_multiple_subscribers` | Multiple subscribers all receive |
| `test_wildcard_subscription` | `["*"]` receives all events |
| `test_source_session_filtering` | Events filtered by source_session_id |
| `test_queue_full_handling` | Full queue logs warning, doesn't block |
| `test_subscriber_cleanup` | Unsubscribe removes queue from router |
| `test_session_emitter_binds_id` | SessionEmitter auto-fills source_session_id |
| `test_concurrent_emit_subscribe` | Thread-safe under concurrent access |
| `test_event_timestamp_populated` | SessionEvent.timestamp is set |

**Integration Tests**:

| Test | Description |
|------|-------------|
| spawn_bundle + EventRouter | Background session emits event on completion |
| Cross-session communication | Session A emits, Session B receives via EventRouter |

**Validation Criteria** (all must pass to complete Phase 2):

| Criterion | How to Verify |
|-----------|---------------|
| EventRouter importable | `from amplifier_orchestration import EventRouter, SessionEvent` succeeds |
| All unit tests pass | `pytest tests/test_events.py -v` passes |
| Integration with spawn_bundle | event_router parameter wires correctly |
| Type checking passes | `pyright amplifier_foundation/events.py` reports no errors |

**Smoke Test Script**:
```python
import asyncio
from amplifier_orchestration import EventRouter

async def smoke_test():
    router = EventRouter()
    received = []
    
    async def collector():
        async for event in router.subscribe(["test:event"]):
            received.append(event)
            if len(received) >= 2:
                break
    
    # Start collector
    task = asyncio.create_task(collector())
    await asyncio.sleep(0.1)  # Let subscriber register
    
    # Emit events
    await router.emit("test:event", {"n": 1}, source_session_id="session-1")
    await router.emit("test:event", {"n": 2}, source_session_id="session-2")
    
    await asyncio.wait_for(task, timeout=1.0)
    
    assert len(received) == 2
    assert received[0].data["n"] == 1
    assert received[1].source_session_id == "session-2"
    print("✓ Phase 2 smoke test passed")

asyncio.run(smoke_test())
```

---

## Phase 3: Trigger Infrastructure

**Goal**: Create trigger sources for event-driven orchestration.

### 3.1: Create Trigger Protocol and Types

**Repository**: `amplifier-foundation`  
**File**: `amplifier_foundation/triggers.py` (NEW)

```python
"""
Trigger source infrastructure.

Provides the protocol and base types for event triggers that can
activate sessions based on file changes, timers, or other events.
"""

from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, Protocol


class TriggerType(Enum):
    """Categories of trigger events."""
    
    FILE_CHANGE = "file_change"
    TIMER = "timer"
    SESSION_EVENT = "session_event"
    WEBHOOK = "webhook"
    ISSUE_EVENT = "issue_event"


@dataclass
class TriggerEvent:
    """An event that triggers session activation."""
    
    type: TriggerType
    """Type of trigger."""
    
    source: str
    """Where the event came from."""
    
    timestamp: datetime
    """When the event occurred."""
    
    data: dict[str, Any]
    """Event-specific payload."""
    
    # File change specific
    file_path: str | None = None
    change_type: str | None = None  # "created", "modified", "deleted"
    
    # Session event specific
    source_session_id: str | None = None
    event_name: str | None = None


class TriggerSource(Protocol):
    """
    Protocol for trigger sources.
    
    Implementations watch for specific types of events and yield
    TriggerEvent objects when they occur.
    
    Example implementation:
        class TimerTrigger:
            def configure(self, config):
                self.interval = config.get("interval_seconds", 60)
            
            async def watch(self):
                while True:
                    await asyncio.sleep(self.interval)
                    yield TriggerEvent(type=TriggerType.TIMER, ...)
            
            async def stop(self):
                self._running = False
    """
    
    def configure(self, config: dict[str, Any]) -> None:
        """Configure the trigger from bundle config."""
        ...
    
    @abstractmethod
    async def watch(self) -> AsyncIterator[TriggerEvent]:
        """Yield events as they occur."""
        ...
    
    async def stop(self) -> None:
        """Stop watching for events."""
        ...
```

**Lines**: ~80

---

### 3.2: Create trigger-file-watcher Module

**Repository**: `amplifier-foundation`  
**File**: `modules/trigger-file-watcher/amplifier_module_trigger_file_watcher/__init__.py` (NEW)

```python
"""
File system change trigger.

Watches for file changes and emits trigger events.
Uses watchdog library for cross-platform file system events.
"""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from amplifier_orchestration import TriggerEvent, TriggerSource, TriggerType

logger = logging.getLogger(__name__)


class FileChangeTrigger(TriggerSource):
    """
    Trigger source for file system changes.
    
    Configuration:
        patterns: List of glob patterns to watch (e.g., ["**/*.py"])
        debounce_ms: Debounce rapid changes (default: 1000)
        path: Base path to watch (default: cwd)
    """
    
    def __init__(self):
        self.patterns: list[str] = ["**/*"]
        self.debounce_ms: int = 1000
        self.base_path: Path = Path.cwd()
        self._observer: Observer | None = None
        self._queue: asyncio.Queue[TriggerEvent] | None = None
        self._running = False
    
    def configure(self, config: dict[str, Any]) -> None:
        """Configure from bundle config."""
        self.patterns = config.get("patterns", ["**/*"])
        self.debounce_ms = config.get("debounce_ms", 1000)
        if "path" in config:
            self.base_path = Path(config["path"])
    
    async def watch(self) -> AsyncIterator[TriggerEvent]:
        """Watch for file changes."""
        self._queue = asyncio.Queue()
        self._running = True
        
        # Create event handler
        handler = _AsyncEventHandler(self._queue, self.patterns, self.base_path)
        
        # Start observer
        self._observer = Observer()
        self._observer.schedule(handler, str(self.base_path), recursive=True)
        self._observer.start()
        
        logger.info(f"Watching {self.base_path} for changes to {self.patterns}")
        
        try:
            # Debounce tracking
            last_event_time: dict[str, float] = {}
            
            while self._running:
                try:
                    event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                    
                    # Debounce
                    key = f"{event.file_path}:{event.change_type}"
                    now = datetime.now(UTC).timestamp()
                    if key in last_event_time:
                        if (now - last_event_time[key]) * 1000 < self.debounce_ms:
                            continue
                    last_event_time[key] = now
                    
                    yield event
                    
                except asyncio.TimeoutError:
                    continue
        finally:
            await self.stop()
    
    async def stop(self) -> None:
        """Stop watching."""
        self._running = False
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None


class _AsyncEventHandler(FileSystemEventHandler):
    """Watchdog handler that puts events on an asyncio queue."""
    
    def __init__(
        self,
        queue: asyncio.Queue,
        patterns: list[str],
        base_path: Path,
    ):
        self.queue = queue
        self.patterns = patterns
        self.base_path = base_path
        self._loop = asyncio.get_event_loop()
    
    def _matches_pattern(self, path: str) -> bool:
        """Check if path matches any pattern."""
        from fnmatch import fnmatch
        rel_path = Path(path).relative_to(self.base_path)
        return any(fnmatch(str(rel_path), p) for p in self.patterns)
    
    def _handle_event(self, event: FileSystemEvent, change_type: str) -> None:
        """Handle a watchdog event."""
        if event.is_directory:
            return
        if not self._matches_pattern(event.src_path):
            return
        
        trigger_event = TriggerEvent(
            type=TriggerType.FILE_CHANGE,
            source="file-watcher",
            timestamp=datetime.now(UTC),
            data={"src_path": event.src_path},
            file_path=event.src_path,
            change_type=change_type,
        )
        
        self._loop.call_soon_threadsafe(
            self.queue.put_nowait, trigger_event
        )
    
    def on_created(self, event: FileSystemEvent) -> None:
        self._handle_event(event, "created")
    
    def on_modified(self, event: FileSystemEvent) -> None:
        self._handle_event(event, "modified")
    
    def on_deleted(self, event: FileSystemEvent) -> None:
        self._handle_event(event, "deleted")


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the file change trigger."""
    trigger = FileChangeTrigger()
    if config:
        trigger.configure(config)
    await coordinator.mount("trigger", trigger)
```

**Lines**: ~150

---

### 3.3: Create trigger-timer Module

**Repository**: `amplifier-foundation`  
**File**: `modules/trigger-timer/amplifier_module_trigger_timer/__init__.py` (NEW)

```python
"""
Timer-based trigger.

Emits events on intervals or cron schedules.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, AsyncIterator

from amplifier_orchestration import TriggerEvent, TriggerSource, TriggerType

logger = logging.getLogger(__name__)


class TimerTrigger(TriggerSource):
    """
    Trigger source for time-based events.
    
    Configuration:
        interval_seconds: Seconds between triggers
        cron: Cron expression (not implemented in v1)
    """
    
    def __init__(self):
        self.interval_seconds: int = 60
        self._running = False
    
    def configure(self, config: dict[str, Any]) -> None:
        """Configure from bundle config."""
        self.interval_seconds = config.get("interval_seconds", 60)
        if "cron" in config:
            logger.warning("Cron expressions not yet supported")
    
    async def watch(self) -> AsyncIterator[TriggerEvent]:
        """Emit events on interval."""
        self._running = True
        tick = 0
        
        logger.info(f"Timer trigger started: {self.interval_seconds}s interval")
        
        while self._running:
            await asyncio.sleep(self.interval_seconds)
            tick += 1
            
            yield TriggerEvent(
                type=TriggerType.TIMER,
                source="timer",
                timestamp=datetime.now(UTC),
                data={"tick": tick, "interval": self.interval_seconds},
            )
    
    async def stop(self) -> None:
        """Stop the timer."""
        self._running = False


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the timer trigger."""
    trigger = TimerTrigger()
    if config:
        trigger.configure(config)
    await coordinator.mount("trigger", trigger)
```

**Lines**: ~80

---

### 3.4: Create trigger-session-event Module

**Repository**: `amplifier-foundation`  
**File**: `modules/trigger-session-event/amplifier_module_trigger_session_event/__init__.py` (NEW)

```python
"""
Session event trigger.

Triggers on events from the EventRouter.
"""

import logging
from datetime import UTC, datetime
from typing import Any, AsyncIterator

from amplifier_orchestration import EventRouter
from amplifier_orchestration import TriggerEvent, TriggerSource, TriggerType

logger = logging.getLogger(__name__)


class SessionEventTrigger(TriggerSource):
    """
    Trigger source for session events.
    
    Configuration:
        event_names: List of event names to trigger on
        source_sessions: Optional list of session IDs to filter by
    """
    
    def __init__(self):
        self.event_names: list[str] = []
        self.source_sessions: list[str] | None = None
        self._event_router: EventRouter | None = None
        self._running = False
    
    def configure(self, config: dict[str, Any]) -> None:
        """Configure from bundle config."""
        self.event_names = config.get("event_names", [])
        self.source_sessions = config.get("source_sessions")
    
    def set_event_router(self, router: EventRouter) -> None:
        """Inject the event router."""
        self._event_router = router
    
    async def watch(self) -> AsyncIterator[TriggerEvent]:
        """Watch for session events."""
        if not self._event_router:
            raise RuntimeError("EventRouter not set. Call set_event_router() first.")
        
        if not self.event_names:
            raise RuntimeError("No event_names configured")
        
        self._running = True
        logger.info(f"Watching for session events: {self.event_names}")
        
        async for session_event in self._event_router.subscribe(
            self.event_names,
            self.source_sessions,
        ):
            if not self._running:
                break
            
            yield TriggerEvent(
                type=TriggerType.SESSION_EVENT,
                source="session-event",
                timestamp=datetime.now(UTC),
                data=session_event.data,
                source_session_id=session_event.source_session_id,
                event_name=session_event.name,
            )
    
    async def stop(self) -> None:
        """Stop watching."""
        self._running = False


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the session event trigger."""
    trigger = SessionEventTrigger()
    if config:
        trigger.configure(config)
    
    # Inject event router if available
    event_router = coordinator.get_capability("event_router")
    if event_router:
        trigger.set_event_router(event_router)
    
    await coordinator.mount("trigger", trigger)
```

**Lines**: ~100

---

### 3.5: Testing & Validation

**Unit Tests** (`amplifier-foundation/tests/test_triggers.py`):

| Test Case | Validates |
|-----------|-----------|
| **TriggerSource Protocol** | |
| `test_trigger_protocol_conformance` | All triggers implement TriggerSource |
| **FileChangeTrigger** | |
| `test_file_watcher_configure` | Config sets patterns, debounce_ms, path |
| `test_file_watcher_emits_on_create` | TriggerEvent emitted when file created |
| `test_file_watcher_emits_on_modify` | TriggerEvent emitted when file modified |
| `test_file_watcher_emits_on_delete` | TriggerEvent emitted when file deleted |
| `test_file_watcher_pattern_filtering` | Only matching patterns trigger events |
| `test_file_watcher_debounce` | Rapid changes debounced correctly |
| `test_file_watcher_stop` | Observer stops cleanly |
| **TimerTrigger** | |
| `test_timer_configure` | interval_seconds set from config |
| `test_timer_emits_on_interval` | Events emitted at configured interval |
| `test_timer_tick_increments` | tick count increases each emission |
| `test_timer_stop` | Timer stops cleanly |
| **SessionEventTrigger** | |
| `test_session_event_configure` | event_names and source_sessions set |
| `test_session_event_requires_router` | RuntimeError if no EventRouter |
| `test_session_event_receives_events` | TriggerEvent emitted when SessionEvent received |
| `test_session_event_filters_by_name` | Only configured event_names trigger |
| `test_session_event_stop` | Trigger stops cleanly |

**Integration Tests**:

| Test | Description |
|------|-------------|
| File trigger + spawn | File change triggers session spawn |
| Timer trigger + spawn | Timer tick triggers session spawn |
| Session event chain | Session A completes → triggers Session B |

**Validation Criteria** (all must pass to complete Phase 3):

| Criterion | How to Verify |
|-----------|---------------|
| Trigger types importable | `from amplifier_orchestration import TriggerSource, TriggerEvent, TriggerType` succeeds |
| All trigger modules loadable | Each module's `mount()` function works |
| All unit tests pass | `pytest tests/test_triggers.py -v` passes |
| Type checking passes | `pyright amplifier_foundation/triggers.py` reports no errors |
| watchdog dependency added | `watchdog` in pyproject.toml dependencies |

**Smoke Test Script** (FileChangeTrigger):
```python
import asyncio
import tempfile
from pathlib import Path
from amplifier_orchestration import TriggerType

async def smoke_test():
    from amplifier_module_trigger_file_watcher import FileChangeTrigger
    
    with tempfile.TemporaryDirectory() as tmpdir:
        trigger = FileChangeTrigger()
        trigger.configure({"path": tmpdir, "patterns": ["*.txt"], "debounce_ms": 100})
        
        received = []
        async def collect():
            async for event in trigger.watch():
                received.append(event)
                if len(received) >= 1:
                    await trigger.stop()
                    break
        
        task = asyncio.create_task(collect())
        await asyncio.sleep(0.5)  # Let watcher start
        
        # Create a file
        Path(tmpdir, "test.txt").write_text("hello")
        
        await asyncio.wait_for(task, timeout=5.0)
        
        assert len(received) == 1
        assert received[0].type == TriggerType.FILE_CHANGE
        assert "test.txt" in received[0].file_path
        print("✓ Phase 3 smoke test passed")

asyncio.run(smoke_test())
```

---

## Phase 4: Background Session Manager

**Goal**: Enable long-running background sessions that respond to triggers.

### 4.1: Create Background Session Manager

**Repository**: `amplifier-foundation`  
**File**: `amplifier_foundation/background.py` (NEW)

```python
"""
Background session management.

Provides infrastructure for long-running sessions that respond to triggers.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable

from amplifier_orchestration import EventRouter
from amplifier_foundation.spawn import SessionStorage, spawn_bundle
from amplifier_orchestration import TriggerEvent, TriggerSource

logger = logging.getLogger(__name__)


@dataclass
class BackgroundSessionConfig:
    """Configuration for a background session."""
    
    name: str
    """Human-readable name for the session."""
    
    bundle: str
    """Bundle URI to spawn."""
    
    triggers: list[dict[str, Any]]
    """List of trigger module configurations."""
    
    pool_size: int = 1
    """Number of concurrent instances (for worker pools)."""
    
    on_complete_emit: str | None = None
    """Optional custom event to emit on completion (in addition to session:completed)."""
    
    on_error_emit: str | None = None
    """Optional custom event to emit on error (in addition to session:error)."""
    
    start_on_parent_start: bool = True
    """Whether to start when parent session starts."""
    
    stop_on_parent_stop: bool = True
    """Whether to stop when parent session stops."""
    
    restart_on_failure: bool = True
    """Whether to restart on failure."""
    
    max_restarts: int = 3
    """Maximum restart attempts."""


@dataclass
class BackgroundSessionState:
    """Runtime state for a background session."""
    
    config: BackgroundSessionConfig
    task: asyncio.Task | None = None
    trigger_count: int = 0
    last_trigger_time: datetime | None = None
    restart_count: int = 0
    status: str = "stopped"  # "stopped", "starting", "running", "failed"


class BackgroundSessionManager:
    """
    Manages background sessions for an orchestrator.
    
    Handles:
    - Starting/stopping background sessions
    - Trigger source management
    - Pool size enforcement
    - Restart policies
    - Status reporting
    
    Example:
        manager = BackgroundSessionManager(parent_session, event_router)
        
        session_id = await manager.start(BackgroundSessionConfig(
            name="code-observer",
            bundle="observers:code-quality",
            triggers=[{"module": "trigger-file-watcher", "config": {...}}],
        ))
        
        status = manager.get_status()
        await manager.stop(session_id)
    """
    
    def __init__(
        self,
        parent_session: Any,
        event_router: EventRouter,
        session_storage: SessionStorage | None = None,
        trigger_loader: Callable[[dict], TriggerSource] | None = None,
    ):
        self.parent_session = parent_session
        self.event_router = event_router
        self.session_storage = session_storage
        self.trigger_loader = trigger_loader or self._default_trigger_loader
        
        self._sessions: dict[str, BackgroundSessionState] = {}
        self._next_id = 1
    
    def _default_trigger_loader(self, config: dict) -> TriggerSource:
        """Load a trigger module from config."""
        # This is a placeholder - actual implementation needs module loading
        module_name = config.get("module")
        raise NotImplementedError(
            f"Trigger module loading not implemented. Module: {module_name}"
        )
    
    async def start(self, config: BackgroundSessionConfig) -> str:
        """
        Start a background session.
        
        Args:
            config: Session configuration
            
        Returns:
            Session ID for management.
        """
        session_id = f"bg-{config.name}-{self._next_id:04d}"
        self._next_id += 1
        
        state = BackgroundSessionState(config=config, status="starting")
        self._sessions[session_id] = state
        
        # Start the background task
        state.task = asyncio.create_task(
            self._run_background_session(session_id, state),
            name=f"background-{config.name}",
        )
        
        state.task.add_done_callback(
            lambda t: self._on_task_complete(session_id, t)
        )
        
        logger.info(f"Started background session: {session_id}")
        return session_id
    
    async def _run_background_session(
        self,
        session_id: str,
        state: BackgroundSessionState,
    ) -> None:
        """Run a background session, responding to triggers."""
        config = state.config
        state.status = "running"
        
        # Load trigger sources
        triggers: list[TriggerSource] = []
        for trigger_config in config.triggers:
            trigger = self.trigger_loader(trigger_config)
            trigger.configure(trigger_config.get("config", {}))
            
            # Inject event router for session-event triggers
            if hasattr(trigger, "set_event_router"):
                trigger.set_event_router(self.event_router)
            
            triggers.append(trigger)
        
        # Merge trigger streams
        async for event in self._merge_triggers(triggers):
            await self._handle_trigger(session_id, state, event)
    
    async def _merge_triggers(
        self,
        triggers: list[TriggerSource],
    ):
        """Merge multiple trigger sources into a single stream."""
        queue: asyncio.Queue[TriggerEvent] = asyncio.Queue()
        
        async def feed_queue(trigger: TriggerSource):
            async for event in trigger.watch():
                await queue.put(event)
        
        # Start all triggers
        tasks = [asyncio.create_task(feed_queue(t)) for t in triggers]
        
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            for task in tasks:
                task.cancel()
            for trigger in triggers:
                await trigger.stop()
    
    async def _handle_trigger(
        self,
        session_id: str,
        state: BackgroundSessionState,
        event: TriggerEvent,
    ) -> None:
        """Handle a trigger event by spawning a session."""
        config = state.config
        state.trigger_count += 1
        state.last_trigger_time = datetime.now(UTC)
        
        logger.info(f"Background session '{config.name}' triggered by {event.type}")
        
        # Build instruction from event
        instruction = self._build_instruction(event)
        
        try:
            result = await spawn_bundle(
                bundle=config.bundle,
                instruction=instruction,
                parent_session=self.parent_session,
                inherit_providers=True,
                session_name=f"{config.name}-{state.trigger_count}",
                session_storage=self.session_storage,
                event_router=self.event_router,
            )
            
            # Always emit session:completed
            await self.event_router.emit(
                "session:completed",
                {
                    "session_name": config.name,
                    "session_id": result.session_id,
                    "trigger": event.data,
                    "output": result.output,
                    "turn_count": result.turn_count,
                },
                source_session_id=session_id,
            )
            
            # Optionally emit custom event
            if config.on_complete_emit:
                await self.event_router.emit(
                    config.on_complete_emit,
                    {
                        "session_name": config.name,
                        "trigger": event.data,
                        "output": result.output,
                    },
                    source_session_id=session_id,
                )
                
        except Exception as e:
            logger.error(f"Background session '{config.name}' failed: {e}")
            
            # Always emit session:error
            await self.event_router.emit(
                "session:error",
                {
                    "session_name": config.name,
                    "trigger": event.data,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                source_session_id=session_id,
            )
            
            # Optionally emit custom error event
            if config.on_error_emit:
                await self.event_router.emit(
                    config.on_error_emit,
                    {
                        "session_name": config.name,
                        "trigger": event.data,
                        "error": str(e),
                    },
                    source_session_id=session_id,
                )
    
    def _build_instruction(self, event: TriggerEvent) -> str:
        """Build instruction from trigger event."""
        import json
        
        if event.type.value == "file_change":
            return f"File changed: {event.file_path} ({event.change_type})"
        elif event.type.value == "session_event":
            return f"Event received: {event.event_name}\n\nData:\n{json.dumps(event.data, indent=2)}"
        elif event.type.value == "timer":
            return f"Timer triggered (tick {event.data.get('tick', '?')})"
        else:
            return f"Triggered by {event.type.value}: {json.dumps(event.data)}"
    
    def _on_task_complete(self, session_id: str, task: asyncio.Task) -> None:
        """Handle background task completion."""
        state = self._sessions.get(session_id)
        if not state:
            return
        
        try:
            exc = task.exception()
            if exc:
                logger.error(f"Background session {session_id} failed: {exc}")
                state.status = "failed"
                
                # Maybe restart
                if state.config.restart_on_failure:
                    if state.restart_count < state.config.max_restarts:
                        state.restart_count += 1
                        asyncio.create_task(self._restart_session(session_id))
                    else:
                        logger.error(f"Max restarts exceeded for {session_id}")
        except asyncio.CancelledError:
            logger.info(f"Background session {session_id} cancelled")
            state.status = "stopped"
    
    async def _restart_session(self, session_id: str) -> None:
        """Restart a failed session."""
        state = self._sessions.get(session_id)
        if not state:
            return
        
        logger.info(f"Restarting background session {session_id} (attempt {state.restart_count})")
        
        state.task = asyncio.create_task(
            self._run_background_session(session_id, state),
            name=f"background-{state.config.name}-restart",
        )
        state.task.add_done_callback(
            lambda t: self._on_task_complete(session_id, t)
        )
    
    async def stop(self, session_id: str) -> None:
        """Stop a background session."""
        state = self._sessions.get(session_id)
        if not state:
            return
        
        if state.task and not state.task.done():
            state.task.cancel()
            try:
                await state.task
            except asyncio.CancelledError:
                pass
        
        state.status = "stopped"
        logger.info(f"Stopped background session: {session_id}")
    
    async def stop_all(self) -> None:
        """Stop all background sessions."""
        for session_id in list(self._sessions.keys()):
            await self.stop(session_id)
    
    def get_status(self) -> dict[str, dict]:
        """Get status of all background sessions."""
        return {
            session_id: {
                "name": state.config.name,
                "status": state.status,
                "trigger_count": state.trigger_count,
                "last_trigger": state.last_trigger_time.isoformat() if state.last_trigger_time else None,
                "restart_count": state.restart_count,
            }
            for session_id, state in self._sessions.items()
        }
```

**Lines**: ~350

---

### 4.2: Add background_sessions Config Parsing

**Repository**: `amplifier-foundation`  
**File**: `amplifier_foundation/bundle.py`

**Add to Bundle class** (around line 200):

```python
@dataclass
class Bundle:
    # ... existing fields ...
    
    background_sessions: list[dict[str, Any]] = field(default_factory=list)
    """Background session configurations."""
```

**Add to YAML parsing** (in _parse_yaml_frontmatter or similar):

```python
# Parse background_sessions
if "background_sessions" in yaml_data:
    bundle.background_sessions = yaml_data["background_sessions"]
```

**Lines changed**: ~20

---

### 4.3: Export Background Session Manager

**Repository**: `amplifier-foundation`  
**File**: `amplifier_foundation/__init__.py`

```python
from amplifier_orchestration import (
    BackgroundSessionConfig,
    BackgroundSessionManager,
    BackgroundSessionState,
)
```

---

### 4.4: Testing & Validation

**Unit Tests** (`amplifier-foundation/tests/test_background.py`):

| Test Case | Validates |
|-----------|-----------|
| **BackgroundSessionConfig** | |
| `test_config_defaults` | Default values for pool_size, restart_on_failure, etc. |
| `test_config_custom_values` | Custom config values preserved |
| **BackgroundSessionManager** | |
| `test_start_creates_task` | start() creates asyncio task |
| `test_start_returns_session_id` | Session ID returned matches pattern |
| `test_stop_cancels_task` | stop() cancels the running task |
| `test_stop_all_stops_all` | stop_all() stops all sessions |
| `test_get_status_returns_all` | Status dict includes all sessions |
| `test_status_tracks_trigger_count` | trigger_count increments |
| `test_status_tracks_last_trigger` | last_trigger_time updated |
| **Trigger Handling** | |
| `test_trigger_spawns_session` | TriggerEvent causes spawn_bundle() call |
| `test_trigger_emits_completion` | session:completed emitted on success |
| `test_trigger_emits_error` | session:error emitted on failure |
| `test_custom_complete_event` | on_complete_emit event emitted |
| `test_custom_error_event` | on_error_emit event emitted |
| **Restart Logic** | |
| `test_restart_on_failure` | Failed session restarts automatically |
| `test_max_restarts_honored` | No restart after max_restarts exceeded |
| `test_restart_count_increments` | restart_count tracked correctly |
| **Trigger Merging** | |
| `test_merge_multiple_triggers` | Events from all triggers received |

**Integration Tests**:

| Test | Description |
|------|-------------|
| End-to-end background session | Config → start → trigger → spawn → complete |
| File trigger + background | File change triggers background session spawn |
| Event chain | Session A completes → emits → triggers Session B |
| Restart recovery | Session fails, restarts, succeeds |

**Validation Criteria** (all must pass to complete Phase 4):

| Criterion | How to Verify |
|-----------|---------------|
| BackgroundSessionManager importable | `from amplifier_orchestration import BackgroundSessionManager` succeeds |
| Bundle parses background_sessions | Bundle with `background_sessions:` config loads correctly |
| All unit tests pass | `pytest tests/test_background.py -v` passes |
| Type checking passes | `pyright amplifier_foundation/background.py` reports no errors |

**Smoke Test Script**:
```python
import asyncio
from amplifier_orchestration import EventRouter
from amplifier_orchestration import (
    BackgroundSessionConfig,
    BackgroundSessionManager,
)

async def smoke_test():
    # Create mock parent session
    parent = MockParentSession()
    router = EventRouter()
    
    # Create manager
    manager = BackgroundSessionManager(
        parent_session=parent,
        event_router=router,
        trigger_loader=mock_trigger_loader,  # Returns TimerTrigger
    )
    
    # Configure a timer-triggered background session
    config = BackgroundSessionConfig(
        name="test-bg",
        bundle="test:bundle",
        triggers=[{"module": "trigger-timer", "config": {"interval_seconds": 1}}],
    )
    
    # Start
    session_id = await manager.start(config)
    assert session_id.startswith("bg-test-bg-")
    
    # Check status
    status = manager.get_status()
    assert session_id in status
    assert status[session_id]["status"] == "running"
    
    # Wait for a trigger
    await asyncio.sleep(1.5)
    assert status[session_id]["trigger_count"] >= 1
    
    # Stop
    await manager.stop(session_id)
    assert manager.get_status()[session_id]["status"] == "stopped"
    
    print("✓ Phase 4 smoke test passed")

asyncio.run(smoke_test())
```

---

## Phase 5: Foreman Integration

**Goal**: Convert foreman to use declarative background_sessions config.

### 5.1: Update Foreman Bundle Configuration

**Repository**: `amplifier-bundle-foreman`  
**File**: `bundle.md`

**Add background_sessions section**:

```yaml
---
bundle:
  name: foreman
  version: 2.0.0

# ... existing config ...

background_sessions:
  - name: coding-workers
    bundle: foreman:workers/coding-worker
    pool_size: 3
    triggers:
      - module: trigger-session-event
        config:
          event_names: ["issue:created", "issue:unblocked"]
    on_complete_emit: work:completed
    on_error_emit: work:failed
    
  - name: test-runner
    bundle: foreman:workers/testing-worker
    triggers:
      - module: trigger-session-event
        config:
          event_names: ["work:completed"]
      - module: trigger-file-watcher
        config:
          patterns: ["tests/**/*.py"]
          debounce_ms: 5000
    on_complete_emit: tests:completed
---
```

---

### 5.2: Update Foreman Orchestrator to Use BackgroundSessionManager

**Repository**: `amplifier-bundle-foreman`  
**File**: `modules/orchestrator-foreman/amplifier_module_orchestrator_foreman/orchestrator.py`

**Replace manual worker management** with:

```python
class ForemanOrchestrator:
    def __init__(self, config: dict[str, Any]):
        # ... existing init ...
        
        self._background_manager: BackgroundSessionManager | None = None
        self._event_router: EventRouter | None = None
    
    async def _initialize_background_sessions(self) -> None:
        """Initialize background session manager from config."""
        from amplifier_orchestration import EventRouter
        from amplifier_orchestration import (
            BackgroundSessionConfig,
            BackgroundSessionManager,
        )
        
        # Get background_sessions from bundle config
        bg_configs = self._coordinator.config.get("background_sessions", [])
        if not bg_configs:
            return
        
        # Create event router
        self._event_router = EventRouter()
        self._coordinator.register_capability("event_router", self._event_router)
        
        # Create manager
        parent_session = self._coordinator.session
        self._background_manager = BackgroundSessionManager(
            parent_session=parent_session,
            event_router=self._event_router,
            session_storage=ForemanSessionStorage(
                parent_session.coordinator.get_capability("session.working_dir")
            ),
        )
        
        # Start configured background sessions
        for bg_config in bg_configs:
            config = BackgroundSessionConfig(**bg_config)
            if config.start_on_parent_start:
                await self._background_manager.start(config)
    
    async def execute(self, prompt: str, ...) -> str:
        # Initialize background sessions on first execute
        if self._background_manager is None:
            await self._initialize_background_sessions()
        
        # ... rest of execute ...
```

**Delete**:
- `_spawn_worker_task()` method
- `_on_worker_complete()` method
- `_worker_tasks` dictionary
- `_spawned_issues` set
- Manual worker tracking logic

**Lines changed**: ~200 deleted, ~100 added

---

### 5.3: Testing & Validation

**Unit Tests** (`amplifier-bundle-foreman/tests/`):

| Test Case | Validates |
|-----------|-----------|
| `test_bundle_parses_background_sessions` | background_sessions config parsed from bundle.md |
| `test_orchestrator_initializes_manager` | BackgroundSessionManager created on first execute |
| `test_orchestrator_starts_configured_sessions` | All start_on_parent_start sessions started |
| `test_orchestrator_stops_on_cleanup` | Background sessions stopped when orchestrator stops |
| `test_event_router_registered` | event_router capability available |
| `test_legacy_worker_code_removed` | Old _spawn_worker_task() etc. no longer exist |

**Integration Tests**:

| Test | Description |
|------|-------------|
| Foreman end-to-end | Issue created → worker triggered → issue processed |
| Event-driven pipeline | coding-worker completes → test-runner triggered |
| Multi-worker pool | Multiple workers process issues concurrently |
| File trigger + worker | File change triggers worker session |

**Validation Criteria** (all must pass to complete Phase 5):

| Criterion | How to Verify |
|-----------|---------------|
| bundle.md parses correctly | `amplifier bundle validate foreman:bundle` passes |
| Legacy code removed | grep for `_spawn_worker_task`, `_worker_tasks` returns nothing |
| Background sessions start | Foreman logs show "Started background session" |
| Workers process issues | Create issue, observe worker processes it |
| Event chain works | work:completed triggers test-runner |
| All tests pass | `pytest tests/ -v` passes in foreman repo |

**End-to-End Test Script**:
```bash
#!/bin/bash
# Run from amplifier-bundle-foreman repo

echo "=== Phase 5 End-to-End Validation ==="

# 1. Validate bundle structure
echo "1. Validating bundle..."
amplifier bundle validate foreman:bundle || exit 1
echo "   ✓ Bundle valid"

# 2. Start foreman session
echo "2. Starting foreman session..."
amplifier run --bundle foreman:bundle "Create an issue: Add unit tests for spawn module" &
FOREMAN_PID=$!
sleep 5

# 3. Check background sessions started
echo "3. Checking background sessions..."
# Look for log output indicating sessions started
if grep -q "Started background session" ~/.amplifier/logs/latest.log; then
    echo "   ✓ Background sessions started"
else
    echo "   ✗ Background sessions not started"
    kill $FOREMAN_PID 2>/dev/null
    exit 1
fi

# 4. Wait for worker to process
echo "4. Waiting for worker processing..."
sleep 30

# 5. Check for work:completed event
echo "5. Checking event emission..."
if grep -q "work:completed" ~/.amplifier/logs/latest.log; then
    echo "   ✓ work:completed event emitted"
else
    echo "   ✗ work:completed event not found"
fi

# 6. Cleanup
kill $FOREMAN_PID 2>/dev/null
echo "=== Phase 5 validation complete ==="
```

**Rollback Criteria**:

If Phase 5 validation fails:
1. Re-enable legacy worker spawning code (revert orchestrator.py)
2. Remove background_sessions from bundle.md
3. Debug with Phase 4 components in isolation

---

## Summary: Total Changes

### By Repository

| Repository | Files Changed | Lines Added | Lines Removed |
|------------|---------------|-------------|---------------|
| amplifier-core | 1 | 6 | 0 |
| amplifier-foundation | 8 (5 new) | ~1,400 | ~20 |
| amplifier-app-cli | 1 | ~150 | ~250 |
| amplifier-bundle-foreman | 2 | ~150 | ~300 |

### By Phase

| Phase | Est. Duration | Files | Net Lines |
|-------|---------------|-------|-----------|
| 1A | 1-2 weeks | 3 | +460 |
| 1B | 1 week | 2 | -100 |
| 2 | 1 week | 2 | +160 |
| 3 | 1-2 weeks | 4 | +410 |
| 4 | 1-2 weeks | 3 | +380 |
| 5 | 1-2 weeks | 2 | -100 |

**Total**: 6-10 weeks, ~1,200 net new lines

---

## Testing Strategy

### Unit Tests Required

| Component | Test File | Coverage |
|-----------|-----------|----------|
| spawn_bundle() | `test_spawn.py` | All inheritance modes, timeout, background |
| EventRouter | `test_events.py` | Emit, subscribe, filtering |
| TriggerSource | `test_triggers.py` | Each trigger module |
| BackgroundSessionManager | `test_background.py` | Start, stop, restart, status |

### Integration Tests Required

| Scenario | Description |
|----------|-------------|
| CLI spawn → spawn_bundle | Verify CLI refactor maintains behavior |
| Foreman → spawn_bundle | Verify workers still spawn correctly |
| Background + triggers | End-to-end background session with file trigger |
| Cross-session events | Session A emits, Session B receives |

### Migration Tests

| Test | Purpose |
|------|---------|
| Existing agent configs | Ensure backward compatibility |
| SessionStore compatibility | Verify SessionStorage protocol works |
| Event backward compat | Existing event handlers still work |

---

## Rollout Plan

### Week 1-2: Phase 1A
- Implement spawn_bundle() in foundation
- Add kernel events
- Unit tests

### Week 3: Phase 1B
- Refactor CLI (behind feature flag)
- Refactor foreman (behind feature flag)
- Integration tests

### Week 4: Phase 2
- Implement EventRouter
- Integration with spawn_bundle
- Tests

### Week 5-6: Phase 3
- Implement trigger modules
- Tests for each trigger type

### Week 7-8: Phase 4
- Implement BackgroundSessionManager
- Bundle config parsing
- Integration tests

### Week 9-10: Phase 5
- Convert foreman to declarative config
- Remove legacy worker management
- End-to-end tests

### Week 11: Stabilization
- Remove feature flags
- Documentation
- Release

---

## Appendix: File Inventory

### New Files

| Path | Lines | Phase |
|------|-------|-------|
| `amplifier_foundation/spawn.py` | 450 | 1A |
| `amplifier_foundation/events.py` | 150 | 2 |
| `amplifier_foundation/triggers.py` | 80 | 3 |
| `amplifier_foundation/background.py` | 350 | 4 |
| `amplifier_foundation/modules/trigger-file-watcher/...` | 150 | 3 |
| `amplifier_foundation/modules/trigger-timer/...` | 80 | 3 |
| `amplifier_foundation/modules/trigger-session-event/...` | 100 | 3 |
| `amplifier_foundation/tests/test_spawn.py` | 400 | 1A |
| `amplifier_foundation/tests/test_events.py` | 150 | 2 |
| `amplifier_foundation/tests/test_triggers.py` | 200 | 3 |
| `amplifier_foundation/tests/test_background.py` | 200 | 4 |

### Modified Files

| Path | Changes | Phase |
|------|---------|-------|
| `amplifier_core/events.py` | +6 lines | 1A |
| `amplifier_foundation/__init__.py` | +15 lines | 1A-4 |
| `amplifier_foundation/bundle.py` | +20 lines | 4 |
| `amplifier_app_cli/session_spawner.py` | Refactor | 1B |
| `amplifier_bundle_foreman/orchestrator.py` | Refactor | 1B, 5 |
| `amplifier_bundle_foreman/bundle.md` | +30 lines | 5 |
