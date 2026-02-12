# Design Validation Report

**Document**: integrated-design.md (v0.2.0)  
**Validated Against**: amplifier-core, amplifier-foundation, amplifier-app-cli, amplifier-bundle-foreman  
**Date**: 2026-02-02

---

## Executive Summary

The design document is **well-aligned with existing implementations** but has several areas requiring attention:

| Area | Validation | Notes |
|------|------------|-------|
| Core insight (bundles as primitive) | ✅ Validated | Matches foundation's `PreparedBundle.spawn()` pattern |
| Inheritance model | ⚠️ Incomplete | Missing 3 inheritance items from CLI |
| SessionStorage protocol | ⚠️ Incomplete | Missing 2 methods from CLI's SessionStore |
| EventRouter concept | ✅ Validated | Complements existing HookRegistry |
| Foreman gap analysis | ✅ Accurate | Confirms duplication and missing inheritance |
| Foundation primitive exists | ⚠️ Not acknowledged | `PreparedBundle.spawn()` already exists |

**Recommendation**: Integrate findings into design, acknowledge existing `spawn()`, complete inheritance list.

---

## 1. spawn_bundle() vs CLI session_spawner

### CLI Implementation Reference
**File**: `amplifier-app-cli/amplifier_app_cli/session_spawner.py:273-658` (~385 lines)

### Inheritance Comparison

| Inheritance Item | Design Document | CLI Implementation | Status |
|------------------|-----------------|-------------------|--------|
| Providers | `inherit_providers: bool` | `session_spawner.py:357-367` | ✅ Match |
| Tools | `inherit_tools: bool \| list[str]` | `session_spawner.py:341-347` via `_filter_tools()` | ✅ Match |
| Hooks | `inherit_hooks: bool \| list[str]` | `session_spawner.py:349-355` via `_filter_hooks()` | ✅ Match |
| Module Resolver | Mentioned in implementation | `session_spawner.py:426-432` | ✅ Match |
| sys.path sharing | `_share_sys_paths()` helper | `session_spawner.py:441-465` | ✅ Match |
| Working Directory | Mentioned in implementation | `session_spawner.py:513-522` | ✅ Match |
| Cancellation Token | Mentioned in implementation | `session_spawner.py:477-483` | ✅ Match |
| Display/Approval Systems | Mentioned in implementation | `session_spawner.py:400, 406-407` | ✅ Match |
| Context Messages | `inherit_context_messages` enum | `session_spawner.py:591-597` | ✅ Match |
| **Mention Resolver** | ❌ Not mentioned | `session_spawner.py:486-497` | ⚠️ Missing |
| **Mention Deduplicator** | ❌ Not mentioned | `session_spawner.py:499-511` | ⚠️ Missing |
| **Self-Delegation Depth** | ❌ Not mentioned | `session_spawner.py:524-528` | ⚠️ Missing |

### Recommended Additions to Design

```python
# Add to spawn_bundle() signature:

mention_resolver: Any | None = None,
# If None, inherit from parent. Preserves @namespace:path resolution.

mention_deduplicator: Any | None = None, 
# If None, inherit from parent. Session-wide dedup state.

self_delegation_depth: int = 0,
# Tracks recursion for self-delegation limits.
```

### Session ID Generation
- **Design**: Uses `generate_sub_session_id()` from foundation
- **CLI**: Same - `session_spawner.py:387-392` uses `tracing.generate_sub_session_id()`
- **Status**: ✅ Match

---

## 2. spawn_bundle() vs Foreman Orchestrator

### Foreman Implementation Reference
**File**: `amplifier-bundle-foreman/modules/orchestrator-foreman/.../orchestrator.py:683-973`

### Design Accurately Captures the Problem

The design document states:
> "Foreman duplicates ~200 lines of CLI logic because there's no shared primitive."

**Validation**: ✅ Confirmed

| Foreman Duplicates | CLI Location | Foreman Location |
|-------------------|--------------|------------------|
| Sub-session ID generation | `session_spawner.py:387-392` | `orchestrator.py:899-905` |
| Provider inheritance | `session_spawner.py:357-367` | `orchestrator.py:861-865` |
| Session state writing | `SessionStore.save()` | `orchestrator.py:259-284` (custom) |
| Project slug derivation | `session_store.py:get_session_dir()` | `orchestrator.py:231-237` |
| Working dir registration | `session_spawner.py:527-530` | `orchestrator.py:925-930` |
| UX system inheritance | `session_spawner.py:400-407` | `orchestrator.py:878-883` |

### Missing Inheritance in Foreman (Design is Accurate)

| Missing | Impact |
|---------|--------|
| Module Source Resolver | Workers may fail on `source:` directives |
| Bundle Package Paths | Shared modules won't be on `sys.path` |
| Tool Inheritance Filtering | Cannot exclude specific tools |
| Hook Inheritance Filtering | Cannot control which hooks propagate |
| Cancellation Token Chain | Parent cancel won't propagate to workers |
| Parent Message Context | Workers don't get parent's conversation history |

**Status**: ✅ Design accurately identifies the gap

---

## 3. PreparedBundle.spawn() Already Exists

### Critical Finding

**Foundation already has a spawn primitive** at `amplifier-foundation/amplifier_foundation/bundle.py:1111-1289`:

```python
async def spawn(
    self,
    child_bundle: Bundle,
    instruction: str,
    *,
    compose: bool = True,
    parent_session: Any = None,
    session_id: str | None = None,
    orchestrator_config: dict[str, Any] | None = None,
    parent_messages: list[dict[str, Any]] | None = None,
    session_cwd: Path | None = None,
    provider_preferences: list[ProviderPreference] | None = None,
) -> dict[str, Any]:
```

### What PreparedBundle.spawn() Handles

| Feature | Location | Covered? |
|---------|----------|----------|
| Bundle composition | `bundle.py:1190-1192` | ✅ |
| Provider preferences | `bundle.py:1207-1216` | ✅ |
| Working directory | `bundle.py:1241-1254` | ✅ |
| UX systems | `bundle.py:1224-1234` | ✅ |
| Parent messages | `bundle.py:1258-1264` | ✅ |
| Module resolver | Via `create_session()` | ✅ |
| **Cancellation propagation** | ❌ | Missing |
| **Tool/hook filtering** | ❌ | Missing |
| **Session persistence** | ❌ | Missing |
| **Mention resolver** | ❌ | Missing |

### Recommendation

The design should acknowledge `PreparedBundle.spawn()` and clarify:

1. **Option A**: Extend `PreparedBundle.spawn()` with missing features
2. **Option B**: Create `spawn_bundle()` as a higher-level wrapper that uses `PreparedBundle` internally

The design currently implies Option B. This should be explicit.

---

## 4. SessionStorage Protocol vs CLI SessionStore

### CLI SessionStore Reference
**File**: `amplifier-app-cli/amplifier_app_cli/session_store.py`

### Method Comparison

| Method | Design Protocol | CLI SessionStore | Status |
|--------|-----------------|------------------|--------|
| `save()` | ✅ | `session_store.py:102-130` | ✅ Match |
| `load()` | ✅ | `session_store.py:175-207` | ✅ Match |
| `exists()` | ✅ | `session_store.py:361-378` | ✅ Match |
| `delete()` | ✅ (added in feedback) | Not in CLI | ➕ New |
| `list_sessions()` | ✅ (added in feedback) | `session_store.py:422-454` | ✅ Match |
| **`find_session()`** | ❌ Not in design | `session_store.py:380-420` | ⚠️ Missing |
| **`cleanup_old_sessions()`** | ❌ Not in design | `session_store.py:490-528` | ⚠️ Missing |

### CLI Additional Features

```python
# session_store.py:380-420
def find_session(self, session_id_prefix: str) -> str | None:
    """Find session by partial ID prefix match."""
    # Enables user-friendly partial ID resumption

# session_store.py:490-528  
async def cleanup_old_sessions(self, days: int = 30) -> int:
    """Remove sessions older than N days."""
    # Housekeeping for storage management
```

### Recommended Additions to Protocol

```python
class SessionStorage(Protocol):
    # ... existing methods ...
    
    def find_session(self, session_id_prefix: str) -> str | None:
        """
        Find session by partial ID prefix.
        Returns full session_id if unique match, None otherwise.
        Enables user-friendly partial ID resumption (e.g., "abc" matches "abc123...").
        """
        ...
    
    async def cleanup(
        self,
        older_than_days: int = 30,
        parent_id: str | None = None,
    ) -> int:
        """
        Remove old sessions. Returns count deleted.
        Essential for background session housekeeping.
        """
        ...
```

---

## 5. EventRouter vs HookRegistry

### Kernel HookRegistry Reference
**File**: `amplifier-core/amplifier_core/hooks.py:32-289`

### Architecture Validation

The design correctly positions EventRouter as **complementary** to HookRegistry:

| Aspect | HookRegistry (Kernel) | EventRouter (Foundation) |
|--------|----------------------|--------------------------|
| Scope | Single session | Cross-session |
| Subscribers | Same-session hooks | Multiple session observers |
| Actions | deny/modify/inject_context/ask_user | Pub/sub delivery only |
| Ordering | Priority-based deterministic | Fan-out to all subscribers |
| Purpose | Lifecycle participation | Session collaboration |

### Design Integration Point

The design proposes integrating with HookRegistry:

```python
# From design: enhanced_emit wrapping
original_emit = hooks.emit

async def enhanced_emit(event_name: str, data: dict) -> Any:
    result = await original_emit(event_name, data)
    if event_name.startswith("session:"):
        await event_router.emit(event_name, data, sub_session_id)
    return result
```

**Validation**: ✅ This approach is sound - leverages existing mechanism, adds cross-session forwarding.

### Existing Event Types (for reference)
**File**: `amplifier-core/amplifier_core/events.py:1-127`

The design's proposed events align with existing patterns:
- `session:start`, `session:end`, `session:fork` already exist
- Adding `session:completed`, `session:error` for background sessions is consistent

**Status**: ✅ Design is well-aligned

---

## 6. Trigger Module Pattern Validation

### No Existing Trigger System

Foundation does not currently have a trigger system. The design introduces this as a new capability.

### Module Pattern Validation

The design proposes triggers as pluggable modules following existing patterns:

| Module Type | Protocol Location | Loader Pattern |
|-------------|-------------------|----------------|
| Provider | `core/validation/provider_protocol.py` | Module loader |
| Tool | `core/validation/tool_protocol.py` | Module loader |
| Hook | `core/validation/hook_protocol.py` | Module loader |
| **Trigger** | *New in design* | Same pattern |

**Validation**: ✅ The module pattern is consistent with existing architecture.

### Reference Implementation Location

The design proposes `foundation:modules/trigger-*`. This aligns with foundation's module structure:
- `amplifier-foundation/modules/tool-delegate/`
- `amplifier-foundation/modules/tool-recipes/`

**Status**: ✅ Consistent with existing structure

---

## 7. Background Sessions Configuration

### No Existing Background Session System

Neither CLI nor foundation currently supports background sessions. This is a new capability.

### Configuration Location Validation

The design (post-feedback) places `background_sessions` at bundle top-level:

```yaml
bundle:
  name: example
  
background_sessions:  # Top-level, parallel to providers/tools/hooks
  - name: observer
    bundle: ...
```

This is consistent with existing bundle structure where major configuration sections are top-level.

**Status**: ✅ Appropriate placement

---

## Summary of Required Design Updates

### Must Fix (Completeness Issues)

1. **Add missing inheritance items** to spawn_bundle():
   - `mention_resolver`
   - `mention_deduplicator` 
   - `self_delegation_depth`

2. **Add missing SessionStorage methods**:
   - `find_session()` for partial ID matching
   - `cleanup()` for housekeeping

3. **Acknowledge PreparedBundle.spawn()** exists and clarify relationship

### Should Fix (Clarity)

4. **Document PreparedBundle.create_session()** as the foundation primitive that spawn_bundle() builds on

5. **Add note about resume capability** - CLI has `session.resume` alongside `session.spawn`

### Consider (Enhancements)

6. **Background session resumption** - What happens if the parent session restarts? Should background session state persist?

7. **Session limits** - CLI has no global session limits. Design mentions this in open questions but doesn't resolve.

---

## Appendix: File References

| Repository | Key Files |
|------------|-----------|
| amplifier-core | `session.py`, `coordinator.py`, `hooks.py`, `cancellation.py`, `events.py` |
| amplifier-foundation | `bundle.py:1111-1289` (spawn), `registry.py:1205-1223` (load_bundle), `spawn_utils.py` |
| amplifier-app-cli | `session_spawner.py:273-658`, `session_store.py`, `session_runner.py:340-396` |
| amplifier-bundle-foreman | `orchestrator.py:683-973` (worker spawning) |
