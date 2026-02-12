# Integrated Session Spawning and Event-Driven Orchestration

**Status**: Design Document  
**Authors**: Amplifier Team  
**Date**: 2025-02-02  
**Version**: 0.1.0

---

## Executive Summary

This document proposes a unified architecture for session spawning and event-driven orchestration in Amplifier. The core insights are:

1. **Bundle spawning is the fundamental primitive** - Agent spawning, self-delegation, and worker spawning are all variations of bundle spawning with different inheritance policies.

2. **Event-driven orchestration enables multi-session collaboration** - Instead of orchestrators only responding to user input, they can respond to file changes, issue updates, timer events, and inter-session events.

3. **Background sessions enable autonomous operation** - Long-running sessions can observe, work, and collaborate without blocking user interaction.

---

## Table of Contents

1. [Motivation](#1-motivation)
2. [Current State Analysis](#2-current-state-analysis)
3. [The Fundamental Insight: Bundles All The Way Down](#3-the-fundamental-insight-bundles-all-the-way-down)
4. [Core Primitive: spawn_bundle()](#4-core-primitive-spawn_bundle)
5. [Event-Driven Orchestration](#5-event-driven-orchestration)
6. [Background Sessions](#6-background-sessions)
7. [Session-to-Session Communication](#7-session-to-session-communication)
8. [Configuration Model](#8-configuration-model)
9. [Architecture Layers](#9-architecture-layers)
10. [Implementation Roadmap](#10-implementation-roadmap)
11. [Design Questions](#11-design-questions)
12. [Appendices](#appendices)

---

## 1. Motivation

### The Problem

Today, Amplifier has multiple implementations of session spawning:

| Component | How It Spawns | What It Can Spawn |
|-----------|--------------|-------------------|
| `amplifier-app-cli` | `spawn_sub_session()` | Agents from parent config |
| `tool-delegate` | Calls `session.spawn` capability | Agents from parent config |
| `orchestrator-foreman` | Direct `load_bundle()` | External bundles by URL |
| `hooks-observations` | Direct provider calls | Lightweight observer sessions |
| `tool-recipes` | Calls `session.spawn` capability | Agents from parent config |

This leads to:
- **Code duplication** - Each implementation handles inheritance, session state, working directory, etc.
- **Inconsistent behavior** - Different spawning methods handle edge cases differently
- **Limited capability** - Agent spawning can't load external bundles; bundle spawning lacks CLI conveniences

### The Vision

A unified primitive that enables:
- All current spawning patterns through one implementation
- Event-driven orchestration beyond user input
- Background sessions for autonomous observation and work
- Multi-session collaboration through events

---

## 2. Current State Analysis

### 2.1 Agent Spawning (CLI/DelegateTool)

**Flow:**
```
agent_name → lookup in agent_configs → overlay on parent config → create session
```

**Capabilities:**
- ✅ Config overlay merging
- ✅ Tool/hook inheritance filtering
- ✅ Provider preferences
- ✅ Module resolver inheritance
- ✅ sys.path sharing
- ✅ Mention resolver inheritance
- ✅ Cancellation propagation
- ✅ Session persistence via SessionStore
- ✅ Nested spawning (child can spawn grandchildren)
- ✅ Working directory inheritance
- ✅ Approval provider registration

**Limitations:**
- ❌ Cannot spawn external bundles by URL
- ❌ Agent must be defined in parent's config

### 2.2 Bundle Spawning (Foreman)

**Flow:**
```
bundle_uri → load_bundle() → bundle.prepare() → create_session()
```

**Capabilities:**
- ✅ Load arbitrary bundles by URL
- ✅ Provider inheritance (manual)
- ✅ Working directory inheritance (manual)
- ✅ UX system inheritance (manual)
- ✅ Session state writing (manual)

**Limitations:**
- ❌ No module resolver inheritance
- ❌ No sys.path sharing
- ❌ No mention resolver inheritance
- ❌ No cancellation propagation
- ❌ No nested spawning capability
- ❌ Manual SessionStore-like implementation
- ❌ No approval provider registration

### 2.3 The Gap

Foreman duplicates ~200 lines of CLI logic because there's no shared primitive. The CLI can't spawn external bundles. Both are doing variations of the same thing.

---

## 3. The Fundamental Insight: Bundles All The Way Down

### 3.1 The Inheritance Spectrum

All spawning scenarios exist on a spectrum of inheritance:

```
                    ┌─────────────────────────────────────────────────┐
                    │          INHERITANCE SPECTRUM                   │
                    ├─────────────────────────────────────────────────┤
Full Bundle         │ Define everything yourself                      │
(foreman workers)   │ Optionally inherit: providers                   │
                    │ Bundle defines: orchestrator, tools, hooks, etc │
                    ├─────────────────────────────────────────────────┤
Agent               │ Inherit most from parent                        │
(delegate tool)     │ Override: instruction, context, maybe tools     │
                    │ Parent provides: orchestrator, providers, hooks │
                    ├─────────────────────────────────────────────────┤
Self                │ Inherit everything from parent                  │
(self-delegation)   │ Same config, just a new session instance        │
                    └─────────────────────────────────────────────────┘
```

### 3.2 Agents ARE Bundles

An agent definition:
```yaml
agents:
  my-agent:
    description: "Does a thing"
    instruction: "You are an agent that..."
    tools:
      - module: tool-filesystem
    context:
      - path: context/agent-specific.md
```

This is equivalent to an **inline bundle definition** that inherits from parent:
```yaml
# Conceptually equivalent to:
bundle:
  name: my-agent
  inherits_from: parent
  instruction: "You are an agent that..."
  tools:
    - module: tool-filesystem
  context:
    - path: context/agent-specific.md
```

### 3.3 The Unifying Principle

**Bundle spawning is the fundamental primitive.** All other spawning patterns are syntactic sugar:

| Pattern | Equivalent To |
|---------|---------------|
| `spawn_agent("explorer")` | `spawn_bundle(agent_as_bundle, inherit_all=True)` |
| `spawn_agent("self")` | `spawn_bundle(parent_bundle, inherit_all=True)` |
| `spawn_worker(bundle_uri)` | `spawn_bundle(bundle_uri, inherit_providers=True)` |
| `spawn_observer(config)` | `spawn_bundle(observer_as_bundle, inherit_providers=True)` |

---

## 4. Core Primitive: spawn_bundle()

### 4.1 Function Signature

```python
# amplifier_foundation/spawn.py

from typing import Protocol, AsyncIterator
from dataclasses import dataclass
from amplifier_core import AmplifierSession


@dataclass
class SpawnResult:
    """Result of spawning a bundle."""
    output: str                    # Final response from session
    session_id: str                # For resumption
    turn_count: int                # Turns executed
    events_emitted: list[str]      # Events emitted during execution


class SessionStorage(Protocol):
    """App-layer persistence for spawned sessions."""
    
    async def save(
        self,
        session_id: str,
        transcript: list[dict],
        metadata: dict,
    ) -> None:
        """Persist session state for resumption."""
        ...
    
    async def load(
        self,
        session_id: str,
    ) -> tuple[list[dict], dict]:
        """Load session state. Returns (transcript, metadata)."""
        ...
    
    def exists(self, session_id: str) -> bool:
        """Check if session exists in storage."""
        ...


async def spawn_bundle(
    # === What to spawn ===
    bundle: "PreparedBundle | Bundle | str",
    # PreparedBundle: Already prepared, use directly
    # Bundle: Prepare it first
    # str: URI to load (git+https://..., file path, etc.)
    
    # === Execution ===
    instruction: str,
    # The prompt/instruction to execute in the spawned session
    
    # === Parent linkage ===
    parent_session: AmplifierSession,
    # Parent session for inheritance and lineage tracking
    
    # === Inheritance controls ===
    inherit_providers: bool = True,
    # If True and bundle has no providers, use parent's providers
    
    inherit_tools: bool | list[str] = False,
    # False: Use only bundle's tools
    # True: Merge parent's tools with bundle's (bundle wins on conflict)
    # list[str]: Inherit only these specific tools from parent
    
    inherit_hooks: bool | list[str] = False,
    # Same semantics as inherit_tools
    
    inherit_context_messages: "ContextInheritance" = ContextInheritance.NONE,
    # NONE: Start with empty context
    # RECENT: Include last N turns from parent
    # ALL: Include full parent context
    
    # === Session identity ===
    sub_session_id: str | None = None,
    # Explicit session ID, or generate one
    
    session_name: str | None = None,
    # Human-readable name for the session (used in ID generation)
    
    # === App-layer integration ===
    session_storage: SessionStorage | None = None,
    # If provided, persist session for resumption
    
    # === Execution control ===
    timeout: float | None = None,
    # Maximum execution time in seconds
    
    background: bool = False,
    # If True, spawn as background task (fire-and-forget)
    # Returns immediately with session_id, session runs in background
    
) -> SpawnResult:
    """
    Spawn a bundle as a sub-session.
    
    This is THE primitive for all session spawning in Amplifier. All other
    spawning patterns (agents, self-delegation, workers) are implemented
    in terms of this function.
    
    Args:
        bundle: What to spawn - PreparedBundle, Bundle, or URI string
        instruction: The prompt to execute
        parent_session: Parent for inheritance and lineage
        inherit_providers: Copy parent's providers if bundle has none
        inherit_tools: Which parent tools to include
        inherit_hooks: Which parent hooks to include
        inherit_context_messages: How much parent context to inherit
        sub_session_id: Explicit session ID (generated if None)
        session_name: Human-readable name for ID generation
        session_storage: App-layer persistence implementation
        timeout: Maximum execution time
        background: Run as fire-and-forget background task
        
    Returns:
        SpawnResult with output, session_id, and metadata
        
    Raises:
        BundleLoadError: If bundle URI cannot be loaded
        BundleValidationError: If bundle is invalid
        SessionError: If session creation or execution fails
        TimeoutError: If execution exceeds timeout
    """
```

### 4.2 Context Inheritance Enum

```python
from enum import Enum


class ContextInheritance(Enum):
    """How much context to inherit from parent session."""
    
    NONE = "none"
    # Start with empty context
    # Child sees only its own instruction
    
    RECENT = "recent"
    # Inherit last N turns from parent (configurable, default 5)
    # Useful for agents that need recent conversation context
    
    ALL = "all"
    # Inherit full parent context
    # Useful for self-delegation where continuity matters
    
    SUMMARY = "summary"
    # Generate a summary of parent context (future)
    # Useful for long conversations where full context is too large
```

### 4.3 Implementation Outline

```python
async def spawn_bundle(
    bundle: "PreparedBundle | Bundle | str",
    instruction: str,
    parent_session: AmplifierSession,
    inherit_providers: bool = True,
    inherit_tools: bool | list[str] = False,
    inherit_hooks: bool | list[str] = False,
    inherit_context_messages: ContextInheritance = ContextInheritance.NONE,
    sub_session_id: str | None = None,
    session_name: str | None = None,
    session_storage: SessionStorage | None = None,
    timeout: float | None = None,
    background: bool = False,
) -> SpawnResult:
    """Implementation of the unified spawn primitive."""
    
    # =========================================================================
    # PHASE 1: Bundle Resolution
    # =========================================================================
    
    if isinstance(bundle, str):
        # Load bundle from URI
        from amplifier_foundation import load_bundle
        bundle = await load_bundle(bundle)
    
    if not isinstance(bundle, PreparedBundle):
        # Prepare the bundle (activates modules, creates resolver)
        prepared = await bundle.prepare()
    else:
        prepared = bundle
    
    bundle_name = prepared.bundle.name
    
    # =========================================================================
    # PHASE 2: Configuration Inheritance
    # =========================================================================
    
    config = prepared.mount_plan.copy()
    
    # --- Provider inheritance ---
    if inherit_providers and not config.get("providers"):
        parent_providers = parent_session.config.get("providers", [])
        if parent_providers:
            config["providers"] = list(parent_providers)
            logger.info(f"Inherited {len(parent_providers)} providers from parent")
    
    # --- Tool inheritance ---
    if inherit_tools:
        parent_tools = parent_session.config.get("tools", [])
        bundle_tools = config.get("tools", [])
        
        if inherit_tools is True:
            # Merge all parent tools (bundle tools take precedence)
            config["tools"] = _merge_module_lists(parent_tools, bundle_tools)
        elif isinstance(inherit_tools, list):
            # Inherit only specified tools
            filtered_parent_tools = [
                t for t in parent_tools 
                if t.get("module") in inherit_tools
            ]
            config["tools"] = _merge_module_lists(filtered_parent_tools, bundle_tools)
    
    # --- Hook inheritance ---
    if inherit_hooks:
        parent_hooks = parent_session.config.get("hooks", [])
        bundle_hooks = config.get("hooks", [])
        
        if inherit_hooks is True:
            config["hooks"] = _merge_module_lists(parent_hooks, bundle_hooks)
        elif isinstance(inherit_hooks, list):
            filtered_parent_hooks = [
                h for h in parent_hooks 
                if h.get("module") in inherit_hooks
            ]
            config["hooks"] = _merge_module_lists(filtered_parent_hooks, bundle_hooks)
    
    # =========================================================================
    # PHASE 3: Session Identity
    # =========================================================================
    
    if not sub_session_id:
        sub_session_id = generate_sub_session_id(
            agent_name=session_name or bundle_name,
            parent_session_id=parent_session.session_id,
            parent_trace_id=getattr(parent_session, "trace_id", None),
        )
    
    # =========================================================================
    # PHASE 4: Session Creation
    # =========================================================================
    
    # Inherit UX systems from parent
    approval_system = parent_session.coordinator.approval_system
    display_system = parent_session.coordinator.display_system
    
    child_session = AmplifierSession(
        config=config,
        loader=None,  # Each session gets its own loader
        session_id=sub_session_id,
        parent_id=parent_session.session_id,
        approval_system=approval_system,
        display_system=display_system,
    )
    
    # =========================================================================
    # PHASE 5: Infrastructure Wiring
    # =========================================================================
    
    # --- Module resolver ---
    # Use bundle's resolver if it has one, otherwise inherit parent's
    if hasattr(prepared, 'resolver') and prepared.resolver:
        await child_session.coordinator.mount(
            "module-source-resolver", 
            prepared.resolver
        )
    else:
        parent_resolver = parent_session.coordinator.get("module-source-resolver")
        if parent_resolver:
            await child_session.coordinator.mount(
                "module-source-resolver", 
                parent_resolver
            )
    
    # --- sys.path sharing ---
    _share_sys_paths(parent_session, child_session)
    
    # --- Working directory ---
    parent_working_dir = parent_session.coordinator.get_capability(
        "session.working_dir"
    )
    if parent_working_dir:
        child_session.coordinator.register_capability(
            "session.working_dir", 
            parent_working_dir
        )
    
    # --- Cancellation propagation (skip for background sessions) ---
    if not background:
        parent_cancellation = parent_session.coordinator.cancellation
        child_cancellation = child_session.coordinator.cancellation
        parent_cancellation.register_child(child_cancellation)
    
    # --- Mention resolver ---
    parent_mention_resolver = parent_session.coordinator.get_capability(
        "mention_resolver"
    )
    if parent_mention_resolver:
        child_session.coordinator.register_capability(
            "mention_resolver", 
            parent_mention_resolver
        )
    
    # --- Nested spawning capability ---
    # Child sessions can spawn their own children
    child_session.coordinator.register_capability(
        "session.spawn",
        _create_spawn_capability(child_session, session_storage),
    )
    
    # =========================================================================
    # PHASE 6: Initialization
    # =========================================================================
    
    await child_session.initialize()
    
    # =========================================================================
    # PHASE 7: Context Inheritance
    # =========================================================================
    
    if inherit_context_messages != ContextInheritance.NONE:
        parent_context = parent_session.coordinator.get("context")
        child_context = child_session.coordinator.get("context")
        
        if parent_context and child_context:
            parent_messages = await parent_context.get_messages()
            
            if inherit_context_messages == ContextInheritance.RECENT:
                # Get last N turns (configurable, default 5)
                parent_messages = _extract_recent_turns(parent_messages, n=5)
            elif inherit_context_messages == ContextInheritance.ALL:
                pass  # Use all messages
            
            # Inject into child context
            for msg in parent_messages:
                await child_context.add_message(msg)
    
    # =========================================================================
    # PHASE 8: Execution
    # =========================================================================
    
    if background:
        # Fire-and-forget: spawn as asyncio task
        task = asyncio.create_task(
            _execute_background_session(
                child_session, 
                instruction, 
                session_storage,
                sub_session_id,
            )
        )
        # Return immediately with session ID
        return SpawnResult(
            output="[Background session started]",
            session_id=sub_session_id,
            turn_count=0,
            events_emitted=[],
        )
    
    # Foreground: execute and wait
    try:
        if timeout:
            response = await asyncio.wait_for(
                child_session.execute(instruction),
                timeout=timeout,
            )
        else:
            response = await child_session.execute(instruction)
    finally:
        # Cleanup cancellation registration
        if not background:
            parent_cancellation.unregister_child(child_cancellation)
    
    # =========================================================================
    # PHASE 9: Persistence
    # =========================================================================
    
    if session_storage:
        context = child_session.coordinator.get("context")
        transcript = await context.get_messages() if context else []
        
        metadata = {
            "session_id": sub_session_id,
            "parent_id": parent_session.session_id,
            "bundle_name": bundle_name,
            "created": datetime.now(UTC).isoformat(),
            "turn_count": 1,
            "config": config,
        }
        
        await session_storage.save(sub_session_id, transcript, metadata)
    
    # =========================================================================
    # PHASE 10: Cleanup
    # =========================================================================
    
    await child_session.cleanup()
    
    return SpawnResult(
        output=response,
        session_id=sub_session_id,
        turn_count=1,
        events_emitted=[],  # TODO: Collect from hooks
    )
```

### 4.4 Helper Functions

```python
def _merge_module_lists(
    base: list[dict], 
    overlay: list[dict],
) -> list[dict]:
    """Merge two module config lists, overlay wins on conflict."""
    result = list(base)
    overlay_modules = {m.get("module") for m in overlay}
    
    # Remove base modules that overlay overrides
    result = [m for m in result if m.get("module") not in overlay_modules]
    
    # Add all overlay modules
    result.extend(overlay)
    
    return result


def _share_sys_paths(
    parent: AmplifierSession, 
    child: AmplifierSession,
) -> None:
    """Share parent's sys.path additions with child."""
    import sys
    
    paths_to_share = []
    
    # From parent's loader
    if hasattr(parent, "loader") and parent.loader:
        parent_paths = getattr(parent.loader, "_added_paths", [])
        paths_to_share.extend(parent_paths)
    
    # From bundle package paths capability
    bundle_paths = parent.coordinator.get_capability("bundle_package_paths")
    if bundle_paths:
        paths_to_share.extend(bundle_paths)
    
    for path in paths_to_share:
        if path not in sys.path:
            sys.path.insert(0, path)


def _extract_recent_turns(
    messages: list[dict], 
    n: int,
) -> list[dict]:
    """Extract the last N user→assistant turns from messages."""
    turn_starts = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    
    if len(turn_starts) <= n:
        return messages
    
    start_index = turn_starts[-n]
    return messages[start_index:]


def _create_spawn_capability(
    session: AmplifierSession,
    storage: SessionStorage | None,
) -> Callable:
    """Create a session.spawn capability for nested spawning."""
    
    async def spawn_capability(
        bundle: "PreparedBundle | Bundle | str",
        instruction: str,
        **kwargs,
    ) -> SpawnResult:
        return await spawn_bundle(
            bundle=bundle,
            instruction=instruction,
            parent_session=session,
            session_storage=storage,
            **kwargs,
        )
    
    return spawn_capability
```

---

## 5. Event-Driven Orchestration

### 5.1 Current Model: Request-Response

```
User Input → Orchestrator → [spawn children] → Response → Wait
```

The orchestrator executes **only when the user provides input**. Everything must complete within a single turn.

### 5.2 Proposed Model: Event-Driven

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         EVENT SOURCES                                    │
├──────────┬──────────┬──────────┬──────────┬──────────┬─────────────────┤
│  User    │  File    │  Issue   │  Timer   │  Webhook │  Session        │
│  Input   │  Change  │  Update  │  (cron)  │  (HTTP)  │  Events         │
└────┬─────┴────┬─────┴────┬─────┴────┬─────┴────┬─────┴────────┬────────┘
     │          │          │          │          │              │
     └──────────┴──────────┴──────────┴──────────┴──────────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │    Event Router     │
                         │ (foundation layer)  │
                         └─────────┬───────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
     ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
     │  Main Session   │  │ Background      │  │ Background      │
     │  (interactive)  │  │ Observer        │  │ Worker Pool     │
     └─────────────────┘  └─────────────────┘  └─────────────────┘
```

### 5.3 Event Types

```python
from dataclasses import dataclass
from enum import Enum
from typing import Any


class EventType(Enum):
    """Categories of events that can trigger orchestration."""
    
    USER_INPUT = "user_input"
    # Traditional user prompt
    
    FILE_CHANGE = "file_change"
    # File created, modified, or deleted
    
    ISSUE_EVENT = "issue"
    # Issue created, updated, assigned, etc.
    
    TIMER = "timer"
    # Scheduled/cron-like triggers
    
    WEBHOOK = "webhook"
    # External HTTP trigger
    
    SESSION_EVENT = "session_event"
    # Event from another session (via hooks.emit)
    
    SYSTEM = "system"
    # System events (startup, shutdown, error)


@dataclass
class TriggerEvent:
    """An event that triggers orchestration."""
    
    type: EventType
    source: str              # Where the event came from
    timestamp: datetime
    data: dict[str, Any]     # Event-specific payload
    
    # For file events
    file_path: str | None = None
    change_type: str | None = None  # created, modified, deleted
    
    # For issue events
    issue_id: str | None = None
    issue_action: str | None = None  # created, updated, assigned, etc.
    
    # For session events
    source_session_id: str | None = None
    event_name: str | None = None


@dataclass
class SessionTrigger:
    """Configuration for what triggers a session."""
    
    type: EventType
    
    # For FILE_CHANGE
    patterns: list[str] | None = None     # Glob patterns to watch
    debounce_ms: int = 1000               # Debounce rapid changes
    
    # For ISSUE_EVENT
    issue_actions: list[str] | None = None  # [created, updated, ...]
    issue_filters: dict | None = None       # {type: [task, bug], status: open}
    
    # For TIMER
    cron: str | None = None               # Cron expression
    interval_seconds: int | None = None   # Simple interval
    
    # For SESSION_EVENT
    event_names: list[str] | None = None  # Events to subscribe to
    source_sessions: list[str] | None = None  # Specific sessions or "*"
    
    # For WEBHOOK
    path: str | None = None               # HTTP path to listen on
    methods: list[str] | None = None      # [POST, PUT, ...]
```

### 5.4 Trigger Sources (Foundation Layer)

```python
# amplifier_foundation/triggers.py

from abc import ABC, abstractmethod
from typing import AsyncIterator


class TriggerSource(ABC):
    """Base class for event trigger sources."""
    
    @abstractmethod
    async def watch(self) -> AsyncIterator[TriggerEvent]:
        """Yield events as they occur."""
        ...
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop watching for events."""
        ...


class FileChangeTrigger(TriggerSource):
    """Watch for file system changes."""
    
    def __init__(
        self, 
        patterns: list[str],
        debounce_ms: int = 1000,
        working_dir: Path | None = None,
    ):
        self.patterns = patterns
        self.debounce_ms = debounce_ms
        self.working_dir = working_dir or Path.cwd()
        self._watcher = None
    
    async def watch(self) -> AsyncIterator[TriggerEvent]:
        """Watch for file changes matching patterns."""
        # Implementation uses watchdog or similar
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
        
        # ... debounced file watching implementation ...
        
        async for event in self._event_queue:
            yield TriggerEvent(
                type=EventType.FILE_CHANGE,
                source="filesystem",
                timestamp=datetime.now(UTC),
                data={"path": event.src_path, "type": event.event_type},
                file_path=event.src_path,
                change_type=event.event_type,
            )


class IssueEventTrigger(TriggerSource):
    """Watch for issue events (via polling or webhook)."""
    
    def __init__(
        self,
        actions: list[str],
        filters: dict | None = None,
        poll_interval: int = 10,
    ):
        self.actions = actions
        self.filters = filters or {}
        self.poll_interval = poll_interval
    
    async def watch(self) -> AsyncIterator[TriggerEvent]:
        """Watch for issue events."""
        # Poll issue store for changes
        # Or receive via webhook callback
        ...


class SessionEventTrigger(TriggerSource):
    """Watch for events from other sessions."""
    
    def __init__(
        self,
        event_names: list[str],
        source_sessions: list[str] | None = None,
        event_router: "EventRouter" = None,
    ):
        self.event_names = event_names
        self.source_sessions = source_sessions
        self.event_router = event_router
    
    async def watch(self) -> AsyncIterator[TriggerEvent]:
        """Subscribe to events from other sessions."""
        async for event in self.event_router.subscribe(
            self.event_names,
            self.source_sessions,
        ):
            yield event


class TimerTrigger(TriggerSource):
    """Trigger on schedule."""
    
    def __init__(
        self,
        cron: str | None = None,
        interval_seconds: int | None = None,
    ):
        self.cron = cron
        self.interval_seconds = interval_seconds
    
    async def watch(self) -> AsyncIterator[TriggerEvent]:
        """Yield events on schedule."""
        if self.interval_seconds:
            while True:
                await asyncio.sleep(self.interval_seconds)
                yield TriggerEvent(
                    type=EventType.TIMER,
                    source="timer",
                    timestamp=datetime.now(UTC),
                    data={"interval": self.interval_seconds},
                )
        elif self.cron:
            # Use croniter for cron parsing
            ...
```

---

## 6. Background Sessions

### 6.1 Concept

A **background session** is a long-running session that:
- Watches for trigger events
- Executes when triggered
- Runs independently of user interaction
- Communicates via events

### 6.2 BackgroundSessionManager

```python
# amplifier_foundation/background.py

from dataclasses import dataclass
from typing import Callable


@dataclass
class BackgroundSessionConfig:
    """Configuration for a background session."""
    
    name: str
    bundle: str | PreparedBundle
    triggers: list[SessionTrigger]
    
    # Pool configuration (for worker-style sessions)
    pool_size: int = 1
    
    # Event emission on completion
    on_complete_emit: str | None = None
    on_error_emit: str | None = None
    
    # Lifecycle
    start_on_parent_start: bool = True
    stop_on_parent_stop: bool = True
    restart_on_failure: bool = True
    max_restarts: int = 3


class BackgroundSessionManager:
    """Manages background sessions for an orchestrator."""
    
    def __init__(
        self,
        parent_session: AmplifierSession,
        event_router: "EventRouter",
        session_storage: SessionStorage | None = None,
    ):
        self.parent_session = parent_session
        self.event_router = event_router
        self.session_storage = session_storage
        
        self._sessions: dict[str, BackgroundSession] = {}
        self._tasks: dict[str, asyncio.Task] = {}
    
    async def start(
        self, 
        config: BackgroundSessionConfig,
    ) -> str:
        """Start a background session, return its ID."""
        
        session = BackgroundSession(
            config=config,
            parent_session=self.parent_session,
            event_router=self.event_router,
            session_storage=self.session_storage,
        )
        
        session_id = f"bg-{config.name}-{uuid.uuid4().hex[:8]}"
        self._sessions[session_id] = session
        
        # Start as asyncio task
        task = asyncio.create_task(
            session.run(),
            name=f"background-{config.name}",
        )
        self._tasks[session_id] = task
        
        # Handle task completion/failure
        task.add_done_callback(
            lambda t: self._on_session_complete(session_id, t)
        )
        
        logger.info(f"Started background session: {session_id}")
        return session_id
    
    async def stop(self, session_id: str) -> None:
        """Stop a background session."""
        if session_id in self._tasks:
            self._tasks[session_id].cancel()
            try:
                await self._tasks[session_id]
            except asyncio.CancelledError:
                pass
            del self._tasks[session_id]
        
        if session_id in self._sessions:
            del self._sessions[session_id]
        
        logger.info(f"Stopped background session: {session_id}")
    
    async def stop_all(self) -> None:
        """Stop all background sessions."""
        for session_id in list(self._sessions.keys()):
            await self.stop(session_id)
    
    def get_status(self) -> dict[str, dict]:
        """Get status of all background sessions."""
        status = {}
        for session_id, session in self._sessions.items():
            task = self._tasks.get(session_id)
            status[session_id] = {
                "name": session.config.name,
                "running": task and not task.done(),
                "trigger_count": session.trigger_count,
                "last_trigger": session.last_trigger_time,
            }
        return status
    
    def _on_session_complete(
        self, 
        session_id: str, 
        task: asyncio.Task,
    ) -> None:
        """Handle background session task completion."""
        try:
            exc = task.exception()
            if exc:
                logger.error(
                    f"Background session {session_id} failed: {exc}"
                )
                # Maybe restart based on config
                session = self._sessions.get(session_id)
                if session and session.config.restart_on_failure:
                    asyncio.create_task(self._restart_session(session_id))
        except asyncio.CancelledError:
            logger.info(f"Background session {session_id} cancelled")


class BackgroundSession:
    """A single background session that responds to triggers."""
    
    def __init__(
        self,
        config: BackgroundSessionConfig,
        parent_session: AmplifierSession,
        event_router: "EventRouter",
        session_storage: SessionStorage | None = None,
    ):
        self.config = config
        self.parent_session = parent_session
        self.event_router = event_router
        self.session_storage = session_storage
        
        self.trigger_count = 0
        self.last_trigger_time: datetime | None = None
        
        self._trigger_sources: list[TriggerSource] = []
        self._prepared_bundle: PreparedBundle | None = None
    
    async def run(self) -> None:
        """Run the background session, responding to triggers."""
        
        # Prepare the bundle once
        if isinstance(self.config.bundle, str):
            bundle = await load_bundle(self.config.bundle)
            self._prepared_bundle = await bundle.prepare()
        else:
            self._prepared_bundle = self.config.bundle
        
        # Set up trigger sources
        self._trigger_sources = [
            self._create_trigger_source(t) 
            for t in self.config.triggers
        ]
        
        # Merge all trigger streams
        async for event in self._merged_triggers():
            await self._handle_trigger(event)
    
    async def _merged_triggers(self) -> AsyncIterator[TriggerEvent]:
        """Merge events from all trigger sources."""
        # Use asyncio.Queue to merge async iterators
        queue = asyncio.Queue()
        
        async def feed_queue(source: TriggerSource):
            async for event in source.watch():
                await queue.put(event)
        
        # Start all sources
        tasks = [
            asyncio.create_task(feed_queue(source))
            for source in self._trigger_sources
        ]
        
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            for task in tasks:
                task.cancel()
    
    async def _handle_trigger(self, event: TriggerEvent) -> None:
        """Handle a single trigger event."""
        self.trigger_count += 1
        self.last_trigger_time = datetime.now(UTC)
        
        logger.info(
            f"Background session '{self.config.name}' triggered by {event.type}"
        )
        
        # Build instruction from event
        instruction = self._build_instruction(event)
        
        # Spawn a child session to handle this event
        try:
            result = await spawn_bundle(
                bundle=self._prepared_bundle,
                instruction=instruction,
                parent_session=self.parent_session,
                inherit_providers=True,
                session_storage=self.session_storage,
                session_name=f"{self.config.name}-{self.trigger_count}",
            )
            
            # Emit completion event if configured
            if self.config.on_complete_emit:
                await self.event_router.emit(
                    self.config.on_complete_emit,
                    {
                        "session_name": self.config.name,
                        "trigger": event.data,
                        "output": result.output,
                        "session_id": result.session_id,
                    },
                )
                
        except Exception as e:
            logger.error(
                f"Background session '{self.config.name}' execution failed: {e}"
            )
            
            if self.config.on_error_emit:
                await self.event_router.emit(
                    self.config.on_error_emit,
                    {
                        "session_name": self.config.name,
                        "trigger": event.data,
                        "error": str(e),
                    },
                )
    
    def _build_instruction(self, event: TriggerEvent) -> str:
        """Build instruction from trigger event."""
        if event.type == EventType.FILE_CHANGE:
            return f"File changed: {event.file_path} ({event.change_type})"
        elif event.type == EventType.ISSUE_EVENT:
            return f"Issue event: {event.issue_action} on issue {event.issue_id}"
        elif event.type == EventType.SESSION_EVENT:
            return f"Event received: {event.event_name}\n\nData:\n{json.dumps(event.data, indent=2)}"
        else:
            return f"Triggered by {event.type}: {json.dumps(event.data)}"
    
    def _create_trigger_source(
        self, 
        trigger: SessionTrigger,
    ) -> TriggerSource:
        """Create a trigger source from configuration."""
        if trigger.type == EventType.FILE_CHANGE:
            return FileChangeTrigger(
                patterns=trigger.patterns or [],
                debounce_ms=trigger.debounce_ms,
            )
        elif trigger.type == EventType.ISSUE_EVENT:
            return IssueEventTrigger(
                actions=trigger.issue_actions or [],
                filters=trigger.issue_filters,
            )
        elif trigger.type == EventType.SESSION_EVENT:
            return SessionEventTrigger(
                event_names=trigger.event_names or [],
                source_sessions=trigger.source_sessions,
                event_router=self.event_router,
            )
        elif trigger.type == EventType.TIMER:
            return TimerTrigger(
                cron=trigger.cron,
                interval_seconds=trigger.interval_seconds,
            )
        else:
            raise ValueError(f"Unknown trigger type: {trigger.type}")
```

---

## 7. Session-to-Session Communication

### 7.1 The Event Router

The kernel's `HookRegistry` already provides `emit()`. We extend this to provide **cross-session event routing**:

```python
# amplifier_foundation/events.py

from collections import defaultdict
from typing import Callable, AsyncIterator


class EventRouter:
    """Routes events between sessions."""
    
    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._session_events: dict[str, list[str]] = defaultdict(list)
    
    async def emit(
        self,
        event_name: str,
        data: dict,
        source_session_id: str | None = None,
    ) -> None:
        """Emit an event to all subscribers."""
        event = TriggerEvent(
            type=EventType.SESSION_EVENT,
            source=source_session_id or "unknown",
            timestamp=datetime.now(UTC),
            data=data,
            source_session_id=source_session_id,
            event_name=event_name,
        )
        
        # Deliver to all subscribers of this event
        for queue in self._subscribers.get(event_name, []):
            await queue.put(event)
        
        # Also deliver to wildcard subscribers
        for queue in self._subscribers.get("*", []):
            await queue.put(event)
        
        logger.debug(
            f"Event '{event_name}' emitted to "
            f"{len(self._subscribers.get(event_name, []))} subscribers"
        )
    
    async def subscribe(
        self,
        event_names: list[str],
        source_sessions: list[str] | None = None,
    ) -> AsyncIterator[TriggerEvent]:
        """Subscribe to events, yielding them as they arrive."""
        queue = asyncio.Queue()
        
        # Register with each event name
        for name in event_names:
            self._subscribers[name].append(queue)
        
        try:
            while True:
                event = await queue.get()
                
                # Filter by source session if specified
                if source_sessions and source_sessions != ["*"]:
                    if event.source_session_id not in source_sessions:
                        continue
                
                yield event
        finally:
            # Unsubscribe on exit
            for name in event_names:
                if queue in self._subscribers[name]:
                    self._subscribers[name].remove(queue)
    
    def create_session_emitter(
        self, 
        session_id: str,
    ) -> Callable:
        """Create an emit function bound to a specific session."""
        async def emit(event_name: str, data: dict) -> None:
            await self.emit(event_name, data, source_session_id=session_id)
        return emit
```

### 7.2 Integration with HookRegistry

```python
# In spawn_bundle(), after session creation:

# Create event emitter for this session
session_emitter = event_router.create_session_emitter(sub_session_id)

# Register as capability so tools/hooks can emit
child_session.coordinator.register_capability(
    "event.emit",
    session_emitter,
)

# Also hook into hooks.emit to forward to event router
original_emit = hooks.emit

async def enhanced_emit(event_name: str, data: dict) -> Any:
    # Original hook behavior
    result = await original_emit(event_name, data)
    
    # Also forward to event router for cross-session visibility
    if event_name.startswith("session:") or event_name in forwarded_events:
        await event_router.emit(event_name, data, sub_session_id)
    
    return result
```

### 7.3 Event Naming Conventions

```
# Session lifecycle events (emitted by kernel)
session:start
session:fork
session:complete
session:error

# Work events (emitted by tools/orchestrators)
work:started
work:completed
work:blocked
work:failed

# Issue events (emitted by issue tool)
issue:created
issue:updated
issue:completed
issue:blocked

# Observation events (emitted by observers)
observation:created
observation:resolved

# Custom events (application-specific)
{namespace}:{event_name}
```

---

## 8. Configuration Model

### 8.1 Bundle Configuration with Background Sessions

```yaml
# foreman-enhanced/bundle.md
---
bundle:
  name: foreman-enhanced
  version: 1.0.0
  description: Enhanced foreman with background workers and observers

session:
  orchestrator:
    module: orchestrator-event-driven
    config:
      # What triggers the main orchestrator
      triggers:
        - type: user_input
        
        # Also respond to certain events
        - type: session_event
          event_names: ["work:blocked", "observation:critical"]
          
      # Background sessions to manage
      background_sessions:
        # Code quality observer
        - name: code-observer
          bundle: observers:code-quality
          triggers:
            - type: file_change
              patterns: ["**/*.py", "**/*.ts", "**/*.js"]
              debounce_ms: 2000
          on_complete_emit: observation:created
          
        # Coding worker pool
        - name: coding-workers
          bundle: foreman:coding-worker
          pool_size: 3
          triggers:
            - type: session_event
              event_names: ["issue:created", "issue:unblocked"]
              filters:
                type: [task, feature, bug]
          on_complete_emit: work:completed
          on_error_emit: work:failed
          
        # Test runner
        - name: test-runner
          bundle: foreman:testing-worker
          triggers:
            - type: session_event
              event_names: ["work:completed"]
            - type: file_change
              patterns: ["tests/**/*.py"]
              debounce_ms: 5000
          on_complete_emit: tests:completed
          
        # Research assistant (on-demand)
        - name: researcher
          bundle: foundation:explorer
          start_on_parent_start: false  # Started explicitly
          triggers:
            - type: session_event
              event_names: ["research:requested"]
          on_complete_emit: research:completed
      
      # How main orchestrator responds to events
      event_handlers:
        - event: observation:created
          condition: "data.severity in ['critical', 'high']"
          action: notify_user
          message_template: |
            ⚠️ Code observation ({data.severity}):
            {data.message}
            File: {data.file_path}
            
        - event: work:completed
          action: summarize_and_notify
          
        - event: work:blocked
          action: request_user_input
          message_template: |
            🚧 Worker blocked on issue #{data.issue_id}:
            {data.blocker_message}
            
        - event: tests:completed
          condition: "data.failed > 0"
          action: create_bug_issues
          
  context:
    module: context-simple
    
providers:
  - module: provider-anthropic
    config:
      model: claude-sonnet-4-20250514
---

# Foreman Enhanced

You coordinate work using issues and background workers...
```

### 8.2 Observer Configuration

```yaml
# observers/code-quality/bundle.md
---
bundle:
  name: code-quality-observer
  version: 1.0.0
  description: Watches code changes and reports quality issues

# Minimal bundle - inherits providers from parent
session:
  orchestrator:
    module: loop-basic
    config:
      max_turns: 1  # Single-turn observer
      
tools:
  - module: tool-filesystem
    config:
      read_only: true
  - module: tool-grep
  - module: tool-observations
---

# Code Quality Observer

You analyze code for quality issues. When triggered with file changes:

1. Read the changed files
2. Analyze for:
   - Code smells
   - Security issues  
   - Performance problems
   - Missing documentation
3. Create observations using the observations tool
4. Respond with a brief summary

Focus on actionable findings. Don't report style issues.
```

### 8.3 Worker Configuration

```yaml
# workers/coding-worker/bundle.md
---
bundle:
  name: coding-worker
  version: 1.0.0
  description: Autonomous coding worker for issues

session:
  orchestrator:
    module: loop-basic
    config:
      max_turns: 20
      
tools:
  - module: tool-filesystem
  - module: tool-bash
  - module: tool-grep
  - module: tool-issue
  - module: tool-python-check
---

# Coding Worker

You are an autonomous worker assigned to complete coding tasks.

## Workflow

1. **Claim the issue** - Update status to in_progress
2. **Understand the task** - Read relevant files, understand context
3. **Implement** - Write clean, tested code
4. **Verify** - Run tests, check types
5. **Complete** - Update issue with results

## Important

- Always claim before starting
- Always update status when done
- If blocked, update issue with specific questions
```

---

## 9. Architecture Layers

### 9.1 Layer Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           APP LAYER                                      │
│                                                                          │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐            │
│  │  CLI App       │  │  Web Server    │  │  Daemon        │            │
│  │                │  │                │  │                │            │
│  │ - user_input   │  │ - webhook      │  │ - timer        │            │
│  │   trigger      │  │   trigger      │  │   trigger      │            │
│  │ - CLI display  │  │ - HTTP API     │  │ - background   │            │
│  │ - SessionStore │  │ - WebSockets   │  │   processing   │            │
│  └────────────────┘  └────────────────┘  └────────────────┘            │
│                                                                          │
│  Provides: SessionStorage impl, app-specific triggers, UX systems       │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
┌──────────────────────────────────┴──────────────────────────────────────┐
│                        FOUNDATION LAYER                                  │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                     spawn_bundle()                               │   │
│  │  - Bundle loading and preparation                                │   │
│  │  - Configuration inheritance                                     │   │
│  │  - Session creation with proper wiring                          │   │
│  │  - Execution and cleanup                                        │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌────────────────────┐  ┌────────────────────┐                        │
│  │ BackgroundSession  │  │   EventRouter      │                        │
│  │    Manager         │  │                    │                        │
│  │                    │  │ - Cross-session    │                        │
│  │ - Lifecycle        │  │   pub/sub          │                        │
│  │ - Pool management  │  │ - Event filtering  │                        │
│  │ - Restart policy   │  │ - Subscriptions    │                        │
│  └────────────────────┘  └────────────────────┘                        │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │                    Trigger Sources                              │    │
│  │  FileChangeTrigger | IssueEventTrigger | SessionEventTrigger   │    │
│  │  TimerTrigger | WebhookTrigger                                 │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  Provides: spawn_bundle, triggers, event routing, background sessions   │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
┌──────────────────────────────────┴──────────────────────────────────────┐
│                         KERNEL LAYER                                     │
│                                                                          │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐            │
│  │ AmplifierSess. │  │ ModuleCoord.   │  │  HookRegistry  │            │
│  │                │  │                │  │                │            │
│  │ - config       │  │ - mount()      │  │ - emit()       │            │
│  │ - parent_id    │  │ - get()        │  │ - subscribe()  │            │
│  │ - execute()    │  │ - capabilities │  │ - lifecycle    │            │
│  └────────────────┘  └────────────────┘  └────────────────┘            │
│                                                                          │
│  Provides: Session lifecycle, module mounting, capability registry,     │
│            hook mechanism, cancellation                                 │
└─────────────────────────────────────────────────────────────────────────┘
```

### 9.2 Responsibility Matrix

| Capability | Kernel | Foundation | App |
|------------|--------|------------|-----|
| Session lifecycle | ✓ | | |
| Module loading | ✓ | | |
| Hook emission | ✓ | | |
| Capability registry | ✓ | | |
| Bundle loading | | ✓ | |
| spawn_bundle() | | ✓ | |
| Trigger sources | | ✓ | |
| Event routing | | ✓ | |
| Background sessions | | ✓ | |
| Session storage | | Protocol | ✓ Impl |
| User input trigger | | Protocol | ✓ Impl |
| Display system | | Protocol | ✓ Impl |
| Approval system | | Protocol | ✓ Impl |

---

## 10. Implementation Roadmap

### Phase 1: Unified spawn_bundle() Primitive

**Goal**: Single primitive for all spawning patterns

**Tasks**:
1. Implement `spawn_bundle()` in `amplifier_foundation/spawn.py`
2. Define `SessionStorage` protocol
3. Implement `CLISessionStorage` in amplifier-app-cli
4. Refactor `session_spawner.py` to use `spawn_bundle()`
5. Update `tool-delegate` to use new primitive
6. Refactor foreman orchestrator to use `spawn_bundle()`
7. Update tests

**Success Criteria**:
- All existing spawning works via `spawn_bundle()`
- Foreman no longer duplicates session state logic
- DelegateTool continues to work unchanged

### Phase 2: Event Router

**Goal**: Cross-session event communication

**Tasks**:
1. Implement `EventRouter` in `amplifier_foundation/events.py`
2. Integrate with `HookRegistry` for event forwarding
3. Add `event.emit` capability registration in `spawn_bundle()`
4. Add `event.subscribe` capability
5. Update foreman to emit events on worker completion
6. Add tests

**Success Criteria**:
- Sessions can emit events visible to other sessions
- Event subscriptions work with filtering
- Foreman workers emit completion events

### Phase 3: Trigger Sources

**Goal**: Foundation-layer trigger implementations

**Tasks**:
1. Implement `TriggerSource` protocol
2. Implement `FileChangeTrigger` (using watchdog)
3. Implement `SessionEventTrigger`
4. Implement `TimerTrigger`
5. Implement `IssueEventTrigger`
6. Add configuration parsing for triggers
7. Add tests

**Success Criteria**:
- File changes can trigger sessions
- Timer-based triggers work
- Issue events can trigger sessions

### Phase 4: Background Session Manager

**Goal**: Long-running background sessions

**Tasks**:
1. Implement `BackgroundSession` class
2. Implement `BackgroundSessionManager`
3. Add configuration parsing for `background_sessions`
4. Integrate with event router
5. Add lifecycle management (start/stop/restart)
6. Add status reporting
7. Add tests

**Success Criteria**:
- Background sessions start with parent
- File watcher observers work
- Worker pools spawn on events

### Phase 5: Event-Driven Orchestrator

**Goal**: Orchestrator that responds to multiple trigger types

**Tasks**:
1. Create `orchestrator-event-driven` module
2. Add trigger configuration support
3. Add event handler configuration
4. Integrate with BackgroundSessionManager
5. Add user notification for events
6. Update foreman to use event-driven orchestrator
7. Add tests

**Success Criteria**:
- Orchestrator responds to file changes
- Orchestrator responds to issue events
- Orchestrator manages background workers

---

## 11. Design Questions

### 11.1 Resolved Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| Where does spawn_bundle() live? | Foundation layer | Needs bundle primitives, shouldn't be in kernel |
| Is SessionStorage required? | Optional | Enables persistence but not all spawns need it |
| How do agents map to bundles? | Config overlay becomes inline bundle | Unifies the model |

### 11.2 Open Questions

#### Q1: Should background sessions share context with parent?

**Options**:
- A) Complete isolation (separate context)
- B) Read-only access to parent context
- C) Shared context module (read-write)

**Considerations**:
- Isolation is simpler and safer
- Shared context enables richer collaboration
- Shared context has concurrency concerns

**Current leaning**: Option A (isolation) with event-based communication

#### Q2: How should resource limits work?

**Concerns**:
- Runaway background sessions consuming tokens
- Too many concurrent sessions
- Memory pressure

**Potential approach**:
- Global session limit (configurable)
- Per-session token budget
- Automatic backpressure when limits approached

#### Q3: What happens to background sessions on error?

**Options**:
- A) Stop on first error
- B) Restart with exponential backoff
- C) Configurable per-session

**Current leaning**: Option C with sensible defaults

#### Q4: How do we handle session state for background sessions?

**Concerns**:
- Background sessions may run many times
- Each execution creates state
- Don't want unbounded storage growth

**Potential approach**:
- Keep last N executions per background session
- Or: transient by default, opt-in persistence

#### Q5: Should triggers be composable?

**Example**: "Trigger when file changes AND issue is assigned to me"

**Options**:
- A) Simple triggers only (OR semantics when multiple)
- B) Support AND/OR composition
- C) Support arbitrary filter expressions

**Current leaning**: Start with A, add B if needed

---

## Appendices

### A. Glossary

| Term | Definition |
|------|------------|
| **Bundle** | A package defining session configuration (orchestrator, tools, hooks, context) |
| **Agent** | A bundle (or inline bundle config) intended for delegation |
| **Spawn** | Creating a child session from a bundle |
| **Trigger** | An event that causes a session to execute |
| **Background Session** | A long-running session that responds to triggers |
| **Event Router** | System for cross-session event communication |

### B. Migration Guide

#### For DelegateTool Users

No changes required. The tool continues to use `session.spawn` capability, which now uses `spawn_bundle()` internally.

#### For Foreman

```python
# Before
bundle = await load_bundle(worker_bundle_uri)
prepared = await bundle.prepare()
# ... 100+ lines of manual wiring ...
await worker_session.execute(prompt)
# ... manual session state writing ...

# After
result = await spawn_bundle(
    bundle=worker_bundle_uri,
    instruction=prompt,
    parent_session=parent,
    inherit_providers=True,
    session_storage=cli_session_storage,
)
```

#### For Custom Orchestrators

If you were manually creating child sessions:

```python
# Before
child = AmplifierSession(config=merged_config, parent_id=parent.session_id)
# ... lots of manual setup ...

# After
result = await spawn_bundle(
    bundle=my_bundle,
    instruction=instruction,
    parent_session=parent,
    inherit_providers=True,
    inherit_tools=True,
)
```

### C. Related Documents

- [ARCHITECTURE.md](./ARCHITECTURE.md) - Current foreman architecture
- [amplifier-core session.py](../../../amplifier-core/amplifier_core/session.py) - Kernel session implementation
- [amplifier-app-cli session_spawner.py](../../../amplifier-app-cli/amplifier_app_cli/session_spawner.py) - Current CLI spawning
- [KERNEL_PHILOSOPHY.md](../../../amplifier-foundation/context/KERNEL_PHILOSOPHY.md) - Design principles

### D. References

- W3C Trace Context: https://www.w3.org/TR/trace-context/
- Watchdog (file system events): https://pythonhosted.org/watchdog/
- AsyncIO patterns: https://docs.python.org/3/library/asyncio.html
