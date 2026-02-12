# Integrated Design: Validated Implementation Analysis

**Prepared**: 2026-02-03  
**Purpose**: Detailed code-level validation of `integrated-design.md` against actual implementation to identify what changes are truly needed

---

## Executive Summary

After deep analysis of the actual code in all four repositories, I've identified several **critical gaps in the design's understanding** of the current implementation and **specific code changes required**.

**Bottom line**: The design correctly identifies the problems but underestimates the complexity of unification. There are **three different spawning implementations** that handle different concerns, and merging them requires careful consideration of which features belong where.

---

## Part 1: The Three Spawning Implementations (Reality Check)

The design claims there's "code duplication" between CLI and Foreman. This is true, but the full picture is more complex.

### 1.1 CLI's `spawn_sub_session()` — The Complete Implementation

**Location**: `amplifier-app-cli/amplifier_app_cli/session_spawner.py:273-658`

**What it actually does** (step by step):

```
1. Agent resolution: agent_name → agent_configs[agent_name]
2. Config merging: merge_configs(parent.config, agent_config)
3. Tool filtering: _filter_tools() with allowlist/blocklist
4. Hook filtering: _filter_hooks() with allowlist/blocklist
5. Provider preferences: apply_provider_preferences() or legacy override
6. Orchestrator config merging: into session.orchestrator.config
7. Session ID generation: generate_sub_session_id()
8. AmplifierSession creation with:
   - parent_id linkage
   - approval_system inheritance
   - display_system inheritance
9. Module resolver mounting (BEFORE initialize)
10. sys.path sharing (BEFORE initialize)
11. Session initialize()
12. Cancellation token registration
13. Mention resolver inheritance
14. Mention deduplicator inheritance
15. Working directory inheritance
16. Self-delegation depth tracking
17. Nested spawn capability registration
18. Approval provider registration
19. System instruction injection
20. Execute instruction
21. Persist via SessionStore.save()
22. Cancellation token unregistration
23. Display system pop_nesting()
24. Session cleanup()
```

**Lines of code**: ~385 lines (273-658)

**Critical dependencies**:
- `merge_configs()` from `agent_config.py`
- `generate_sub_session_id()` from `amplifier_foundation`
- `SessionStore` class
- `AppMentionResolver` 
- `ContentDeduplicator`
- `CLIApprovalProvider`

### 1.2 Foundation's `PreparedBundle.spawn()` — The Minimal Implementation

**Location**: `amplifier-foundation/amplifier_foundation/bundle.py:1111-1289`

**What it actually does**:

```
1. Optionally compose child with parent bundle
2. Get mount plan from effective bundle
3. Merge orchestrator config if provided
4. Apply provider preferences
5. Create AmplifierSession with:
   - parent_id linkage
   - approval_system inheritance (via getattr chain)
   - display_system inheritance (via getattr chain)
6. Mount resolver
7. Register working_dir capability
8. Initialize session
9. Inject parent messages if provided
10. Register system prompt factory
11. Execute instruction
12. Cleanup session
```

**Lines of code**: ~180 lines

**What it DOESN'T do** (that CLI does):
- ❌ sys.path sharing
- ❌ Cancellation token propagation
- ❌ Mention resolver inheritance
- ❌ Mention deduplicator inheritance  
- ❌ Nested spawn capability registration
- ❌ Session persistence (no SessionStore)
- ❌ Approval provider registration
- ❌ Tool/hook filtering
- ❌ Display nesting management

### 1.3 Foreman's Manual Approach — The Workaround

**Location**: `amplifier-bundle-foreman/modules/orchestrator-foreman/...orchestrator.py:809-973`

**What it actually does**:

```
1. load_bundle(worker_bundle_uri)
2. Manual provider inheritance: bundle.providers = parent_providers
3. bundle.prepare()
4. Get UX systems from parent
5. Get working_dir from parent
6. generate_sub_session_id()
7. prepared.create_session() with session_cwd
8. Register working_dir capability (again)
9. Execute worker prompt
10. Custom _write_worker_session_state() for persistence
11. Cleanup session
```

**Lines duplicated from CLI**: ~100 lines for session state writing alone

**What it's missing**:
- ❌ sys.path sharing
- ❌ Cancellation propagation
- ❌ Mention resolver inheritance
- ❌ Tool/hook filtering options
- ❌ Nested spawn capability

---

## Part 2: What the Design Gets Wrong

### 2.1 Wrong: "spawn_bundle() is the fundamental primitive"

**Design claim**: A single `spawn_bundle()` function can replace all spawning patterns.

**Reality**: The CLI's `spawn_sub_session()` takes an `agent_name` string and resolves it against `agent_configs`. The foundation's `PreparedBundle.spawn()` takes a `Bundle` object. These are fundamentally different:

| Approach | Input | Resolution |
|----------|-------|------------|
| CLI | `agent_name: str` | Looks up in `agent_configs` dict |
| Foundation | `child_bundle: Bundle` | Already resolved |
| Design | `bundle: PreparedBundle \| Bundle \| str` | Magic resolution |

**The problem**: Agent name → config resolution is **app-layer policy**. It involves:
- Looking up agent in parent's `agent_configs`
- Applying `spawn.exclude_tools` policies from agent config
- Handling "self" delegation specially
- Handling bundle paths like "foundation:agents/explorer"

The design's `spawn_bundle()` can't do this without duplicating app-layer logic.

**Actual solution**: Two functions:
1. `spawn_bundle()` in foundation — takes Bundle, handles infrastructure wiring
2. CLI keeps its own `spawn_sub_session()` that resolves names then calls `spawn_bundle()`

### 2.2 Wrong: "session:completed always emits"

**Design claim**: Background sessions ALWAYS emit `session:completed` automatically.

**Reality**: The kernel has NO `session:completed` event. Checking `amplifier-core/amplifier_core/events.py`:

```python
# What exists:
SESSION_START = "session:start"
SESSION_END = "session:end"
SESSION_FORK = "session:fork"
SESSION_RESUME = "session:resume"

# What DOESN'T exist:
# session:completed  ← NOT DEFINED
# session:error      ← NOT DEFINED
```

**What needs to change**:
1. Add `SESSION_COMPLETED = "session:completed"` to `events.py`
2. Add `SESSION_ERROR = "session:error"` to `events.py`
3. Decide WHO emits them:
   - Option A: Kernel emits after `execute()` returns (mechanism)
   - Option B: `spawn_bundle()` emits after execution (policy)

### 2.3 Wrong: "ContextInheritance enum"

**Design claim**: Use a `ContextInheritance` enum with values NONE, RECENT, ALL.

**Reality**: tool-delegate already uses string parameters:

```python
# From tool-delegate/__init__.py:688-690
context_depth: str  # "none" | "recent" | "all"
context_scope: str  # "conversation" | "agents" | "full"  
context_turns: int  # default 5
```

**The problem**: The design's enum has only 3 values, but the actual system has **two independent parameters** creating 9 combinations:

| context_depth × context_scope | Result |
|------------------------------|--------|
| none × any | Empty context |
| recent × conversation | Last N user/assistant turns |
| recent × agents | + delegate results |
| recent × full | + all tool results |
| all × conversation | Full user/assistant history |
| all × agents | + all delegate results |
| all × full | + all tool results |

**Actual solution**: Either:
1. Keep string parameters (current, working)
2. Create TWO enums: `ContextDepth` and `ContextScope`

### 2.4 Wrong: "SessionStorage protocol matches SessionStore"

**Design proposes**:
```python
class SessionStorage(Protocol):
    async def save(self, session_id, transcript, metadata) -> None
    async def load(self, session_id) -> tuple[list, dict]
    def exists(self, session_id) -> bool
    async def delete(self, session_id) -> bool
    async def list_sessions(self, parent_id, bundle_name, limit) -> list[dict]
```

**SessionStore actually has**:
```python
class SessionStore:
    def save(self, session_id, transcript, metadata) -> None  # NOT async
    def load(self, session_id) -> tuple[list, dict]           # NOT async
    def exists(self, session_id) -> bool                      # ✓
    # NO delete() method
    def list_sessions(self, top_level_only=True) -> list[str]  # Different signature
    def update_metadata(self, session_id, updates) -> dict
    def get_metadata(self, session_id) -> dict
    def find_session(self, partial_id, top_level_only=True) -> str
    def save_config_snapshot(self, session_id, config) -> None
    def cleanup_old_sessions(self, days=30) -> int
```

**Differences**:
1. SessionStore methods are NOT async
2. No `delete()` method exists
3. `list_sessions()` has different parameters
4. SessionStore has extra methods the protocol doesn't define

**What needs to change**: Either:
1. Add async wrappers to SessionStore
2. Make the protocol sync (simpler)
3. SessionStore adds `delete()` method

### 2.5 Wrong: Design doesn't mention mention resolvers

The CLI's `spawn_sub_session()` does this (lines 485-511):

```python
# Mention resolver - inherit from parent to preserve bundle_override context
parent_mention_resolver = parent_session.coordinator.get_capability("mention_resolver")
if parent_mention_resolver:
    child_session.coordinator.register_capability("mention_resolver", parent_mention_resolver)

# Mention deduplicator - inherit for session-wide deduplication state  
parent_deduplicator = parent_session.coordinator.get_capability("mention_deduplicator")
if parent_deduplicator:
    child_session.coordinator.register_capability("mention_deduplicator", parent_deduplicator)
```

**Why this matters**: Without mention resolver inheritance, @namespace:path references in child sessions won't resolve correctly because they lose the bundle context.

**The design's `spawn_bundle()` implementation (Section 4.3) doesn't mention this at all.**

### 2.6 Wrong: Cancellation unregistration is missing

The design shows (Section 4.3, line 611):
```python
# Cleanup cancellation registration
if not background:
    parent_cancellation.unregister_child(child_cancellation)
```

But this is in the `finally` block AFTER execution. The CLI does it AFTER cleanup:

```python
# From session_spawner.py:644-648
# Unregister child cancellation token before cleanup
parent_cancellation.unregister_child(child_cancellation)

# Cleanup child session  
await child_session.cleanup()
```

**Order matters**: If cleanup fails, the unregistration never happens with the design's approach.

---

## Part 3: What Actually Needs to Be Built

### 3.1 Phase 1: Unified spawn_bundle() (Foundation Layer)

**Create**: `amplifier_foundation/spawn.py`

```python
# Actual implementation requirements:

@dataclass
class SpawnResult:
    output: str
    session_id: str
    turn_count: int

class SessionStorage(Protocol):
    """Matches CLI's SessionStore (sync, not async)"""
    def save(self, session_id: str, transcript: list, metadata: dict) -> None: ...
    def load(self, session_id: str) -> tuple[list, dict]: ...
    def exists(self, session_id: str) -> bool: ...

async def spawn_bundle(
    # What to spawn
    bundle: PreparedBundle | Bundle | str,
    instruction: str,
    parent_session: AmplifierSession,
    
    # Inheritance controls
    inherit_providers: bool = True,
    inherit_tools: bool | list[str] = False,  # False/True/allowlist
    inherit_hooks: bool | list[str] = False,
    
    # Context inheritance (use strings to match existing tool-delegate)
    context_depth: str = "none",  # "none" | "recent" | "all"
    context_scope: str = "conversation",  # "conversation" | "agents" | "full"
    context_turns: int = 5,
    
    # Session identity
    session_id: str | None = None,
    session_name: str | None = None,
    
    # Persistence
    session_storage: SessionStorage | None = None,
    
    # Execution
    timeout: float | None = None,
    background: bool = False,
) -> SpawnResult:
```

**Implementation must include** (from CLI analysis):

1. Bundle resolution (if str passed)
2. Config merging for tool/hook inheritance
3. Provider inheritance logic
4. Session ID generation
5. AmplifierSession creation with parent linkage
6. **Module resolver mounting** (before initialize)
7. **sys.path sharing** (before initialize)
8. Session initialization
9. **Cancellation token registration**
10. **Mention resolver inheritance**
11. **Mention deduplicator inheritance**
12. **Working directory inheritance**
13. Nested spawn capability registration
14. Context message inheritance (based on depth/scope)
15. Execution (with timeout support)
16. Session persistence (if storage provided)
17. **Cancellation token unregistration** (correct order)
18. Session cleanup
19. Result return

**Estimated lines**: 350-400 (not the design's optimistic 300)

### 3.2 Phase 1 Continued: Kernel Events

**Modify**: `amplifier-core/amplifier_core/events.py`

Add:
```python
# Session completion events
SESSION_COMPLETED = "session:completed"
SESSION_ERROR = "session:error"
```

Add to `ALL_EVENTS` list.

**Decide**: Who emits these? Recommendation: `spawn_bundle()` emits after execution (foundation policy, not kernel mechanism).

### 3.3 Phase 1 Continued: Refactor Consumers

**Modify**: `amplifier-app-cli/amplifier_app_cli/session_spawner.py`

Refactor `spawn_sub_session()` to:
1. Resolve agent_name → Bundle (app-layer policy)
2. Apply tool/hook filtering policies
3. Call `spawn_bundle()` (foundation mechanism)

**This keeps**:
- Agent name resolution logic in app layer
- `spawn.exclude_tools` policy handling in app layer
- Self-delegation special handling in app layer

**Modify**: `amplifier-bundle-foreman/modules/orchestrator-foreman/.../orchestrator.py`

Replace `_run_spawn_and_handle_result()` with:
```python
result = await spawn_bundle(
    bundle=worker_bundle_uri,
    instruction=worker_prompt,
    parent_session=parent_session,
    inherit_providers=True,
    session_storage=foreman_session_storage,  # New: implements SessionStorage
    background=True,  # Fire-and-forget
)
```

**Delete**: `_write_worker_session_state()` method (~100 lines)

### 3.4 Phase 2: EventRouter

**Create**: `amplifier_foundation/events.py`

```python
class EventRouter:
    """Cross-session event routing."""
    
    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
    
    async def emit(
        self,
        event_name: str,
        data: dict,
        source_session_id: str | None = None,
    ) -> None:
        """Emit event to all subscribers."""
        ...
    
    def subscribe(
        self,
        event_names: list[str],
    ) -> AsyncIterator[TriggerEvent]:
        """Subscribe to events."""
        ...
    
    def create_session_emitter(self, session_id: str) -> Callable:
        """Create emit function bound to session."""
        ...
```

**Integration point**: `spawn_bundle()` registers `event.emit` capability on child sessions.

**Estimated lines**: ~150

### 3.5 Phase 3: Trigger Infrastructure

**Create**: `amplifier_foundation/triggers.py`

```python
class TriggerSource(Protocol):
    """Protocol for event trigger sources."""
    async def watch(self) -> AsyncIterator[TriggerEvent]: ...
    async def stop(self) -> None: ...
    def configure(self, config: dict) -> None: ...

@dataclass
class TriggerEvent:
    type: EventType
    source: str
    timestamp: datetime
    data: dict[str, Any]
    # Event-specific fields...
```

**Create modules**:
- `trigger-file-watcher` (~150 lines, uses watchdog)
- `trigger-timer` (~80 lines)
- `trigger-session-event` (~100 lines, uses EventRouter.subscribe)

### 3.6 Phase 4: Background Session Manager

**Create**: `amplifier_foundation/background.py`

```python
@dataclass
class BackgroundSessionConfig:
    name: str
    bundle: str | PreparedBundle
    triggers: list[dict]  # Trigger module configs
    pool_size: int = 1
    restart_on_failure: bool = True
    max_restarts: int = 3

class BackgroundSessionManager:
    """Manages background sessions for an orchestrator."""
    
    async def start(self, config: BackgroundSessionConfig) -> str: ...
    async def stop(self, session_id: str) -> None: ...
    async def stop_all(self) -> None: ...
    def get_status(self) -> dict[str, dict]: ...
```

**Estimated lines**: ~450 for manager + session

### 3.7 Phase 5: Bundle Config Parsing

**Modify**: Bundle loading in `amplifier_foundation/bundle.py`

Add parsing for:
```yaml
background_sessions:
  - name: worker-pool
    bundle: foreman:workers/coding-worker
    pool_size: 3
    triggers:
      - module: trigger-session-event
        config:
          event_names: ["issue:created"]
```

---

## Part 4: Corrected Implementation Roadmap

### Phase 1A: Core spawn_bundle() (Week 1-2)

| Task | File | Lines Changed |
|------|------|---------------|
| Create `spawn_bundle()` | `amplifier_foundation/spawn.py` | +400 new |
| Create `SpawnResult`, `SessionStorage` | `amplifier_foundation/spawn.py` | +30 new |
| Add session events | `amplifier_core/events.py` | +5 |
| Export from foundation | `amplifier_foundation/__init__.py` | +5 |

### Phase 1B: Consumer Migration (Week 2-3)

| Task | File | Lines Changed |
|------|------|---------------|
| Refactor `spawn_sub_session()` | `amplifier_app_cli/session_spawner.py` | -200, +100 |
| Refactor foreman spawning | `orchestrator.py` | -150, +30 |
| Create `ForemanSessionStorage` | `orchestrator.py` | +50 new |
| Tests | Various | +300 new |

### Phase 2: EventRouter (Week 3-4)

| Task | File | Lines Changed |
|------|------|---------------|
| Create EventRouter | `amplifier_foundation/events.py` | +150 new |
| Integrate with spawn_bundle() | `amplifier_foundation/spawn.py` | +30 |
| Tests | `tests/test_events.py` | +100 new |

### Phase 3: Triggers (Week 4-5)

| Task | File | Lines Changed |
|------|------|---------------|
| Create TriggerSource protocol | `amplifier_foundation/triggers.py` | +80 new |
| trigger-file-watcher module | New module | +150 new |
| trigger-timer module | New module | +80 new |
| trigger-session-event module | New module | +100 new |

### Phase 4: Background Sessions (Week 5-6)

| Task | File | Lines Changed |
|------|------|---------------|
| BackgroundSessionManager | `amplifier_foundation/background.py` | +450 new |
| Bundle config parsing | `amplifier_foundation/bundle.py` | +100 |
| Tests | `tests/test_background.py` | +200 new |

### Phase 5: Foreman Integration (Week 6-7)

| Task | File | Lines Changed |
|------|------|---------------|
| Convert foreman to use background_sessions | `orchestrator.py` | -300, +100 |
| Create foreman bundle config | `bundle.md` | +50 |
| Integration tests | `tests/` | +200 new |

---

## Part 5: Critical Decisions Needed Before Implementation

### Decision 1: sync vs async SessionStorage

**Option A**: Keep SessionStore sync, protocol is sync
- Pro: No changes to SessionStore
- Con: Background sessions may block on I/O

**Option B**: Make SessionStore async, protocol is async
- Pro: Better for concurrent background sessions
- Con: Breaking change, needs migration

**Recommendation**: Option A for Phase 1, migrate to B in Phase 4 when background sessions need it.

### Decision 2: Where does agent name resolution live?

**Option A**: In `spawn_bundle()` (pass agent_configs)
- Pro: Single function does everything
- Con: Foundation knows about app-layer agent configs

**Option B**: In CLI, CLI calls `spawn_bundle()` with Bundle
- Pro: Clean separation
- Con: Two-step process

**Recommendation**: Option B. Agent resolution is app policy.

### Decision 3: ContextInheritance enum vs strings

**Option A**: Create enum, migrate tool-delegate
- Pro: Type safety
- Con: Breaking change

**Option B**: Keep strings, match existing tool-delegate
- Pro: No breaking changes
- Con: Less type safety

**Recommendation**: Option B for Phase 1. Can add enum later as optional.

### Decision 4: Who emits session:completed?

**Option A**: Kernel emits after execute() returns
- Pro: Always emitted, mechanism
- Con: Kernel knows about spawn concept

**Option B**: spawn_bundle() emits after execution
- Pro: Policy in foundation
- Con: Direct session.execute() doesn't emit

**Recommendation**: Option B. spawn_bundle() is the coordination point.

---

## Appendix: Code Reference

| Repository | File | Key Functions/Classes |
|------------|------|----------------------|
| amplifier-core | `session.py:35-470` | `AmplifierSession.__init__()`, `.execute()`, `.cleanup()` |
| amplifier-core | `coordinator.py:155-274` | `.mount()`, `.get()`, `.register_capability()` |
| amplifier-core | `cancellation.py:137-156` | `.register_child()`, `.unregister_child()` |
| amplifier-core | `events.py` | Event constants (need to add SESSION_COMPLETED) |
| amplifier-foundation | `bundle.py:846-1289` | `PreparedBundle`, `.create_session()`, `.spawn()` |
| amplifier-foundation | `registry.py:1205` | `load_bundle()` |
| amplifier-app-cli | `session_spawner.py:273-658` | `spawn_sub_session()` — **reference implementation** |
| amplifier-app-cli | `session_store.py:75-529` | `SessionStore` — **persistence implementation** |
| amplifier-bundle-foreman | `orchestrator.py:206-303` | `_write_worker_session_state()` — **to be deleted** |
| amplifier-bundle-foreman | `orchestrator.py:809-973` | `_run_spawn_and_handle_result()` — **to be refactored** |
