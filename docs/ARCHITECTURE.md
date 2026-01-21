# Foreman Bundle Architecture

## Overview

The Foreman bundle provides conversational autonomous work orchestration through:
- **Issue queue** for work tracking
- **Custom orchestrator** that spawns worker bundles directly
- **Background worker execution** via asyncio tasks
- **Proactive progress updates** on every turn

## Components

### 1. Issue Queue (from amplifier-bundle-issues)

Persistent issue tracking with statuses:
- `open` - Ready for assignment
- `in_progress` - Worker actively handling
- `pending_user_input` - Blocked, needs user
- `completed` - Done
- `blocked` - Failed, needs escalation

### 2. Foreman Orchestrator

Custom orchestrator that:
- Analyzes user requests into discrete issues
- Routes issues to appropriate worker pools
- **Spawns worker bundles directly** (no tool needed)
- Checks queue on every turn for updates
- Reports completions/blockers proactively

#### Direct Bundle Spawning

The orchestrator spawns workers directly using kernel/foundation primitives:

```python
# Load worker bundle
bundle = await load_bundle(worker_bundle_path)

# Create new session with worker config
worker_session = AmplifierSession(config=bundle.config)

# Execute in background (fire-and-forget)
asyncio.create_task(worker_session.run(worker_prompt))
```

**Why this works:**
- Orchestrator runs in a session with access to `AmplifierSession`
- `load_bundle()` is a foundation utility callable from anywhere
- `asyncio.create_task()` provides fire-and-forget execution
- No app-layer or CLI modifications required

**Why this is better than a tool:**
- Simpler: No custom spawn tool needed
- Direct: Orchestrator controls spawn logic
- Flexible: Can customize session config per worker
- Cleaner: Uses existing kernel/foundation APIs

### 3. Worker Bundles

Specialized bundles that:
- Receive issue context in their prompt
- Have access to issue management tool
- Update issue status when complete/blocked
- Run in background, don't block foreman

**Worker types:**
- **coding-worker**: File operations, code tools
- **research-worker**: Web search, documentation
- **testing-worker**: Test execution, validation
- **privileged-worker**: Escalated tasks (future)

## Data Flow

```
User Request
     ↓
Foreman analyzes → Creates issues
     ↓
Route issues → Spawn workers (via load_bundle + AmplifierSession)
     ↓
Workers run in background
     ↓
Workers update issue queue
     ↓
Foreman checks queue on next turn → Reports updates
```

## Worker Spawning Detail

```
┌─────────────────────────────────────────────────────┐
│ Foreman Orchestrator                                 │
│                                                      │
│  1. Get worker_bundle path from config              │
│  2. await load_bundle(worker_bundle_path)           │
│  3. AmplifierSession(config=bundle.config)          │
│  4. asyncio.create_task(session.run(prompt))        │
│                                                      │
│  → Worker runs in background                        │
│  → Foreman returns immediately to user              │
└─────────────────────────────────────────────────────┘
```

## Configuration

Worker pools defined in bundle.md:

```yaml
orchestrator:
  module: orchestrator-foreman
  config:
    worker_pools:
      - name: coding-pool
        worker_bundle: git+https://github.com/.../coding-worker
        max_concurrent: 3
        route_types: [coding, implementation]
    
    routing:
      default_pool: coding-pool
      rules:
        - if_metadata_type: [coding]
          then_pool: coding-pool
```

## Issue Routing

Foreman routes issues to pools based on:
1. **Explicit rules**: Match `if_metadata_type`, `if_status`, `and_retry_count_gte`
2. **Pool route_types**: Match issue type to pool's handled types
3. **Default pool**: Fallback when no rules match

## Lifecycle

### Turn Flow

Every foreman turn:
1. **Check queue** for updates (completions, blockers)
2. **Report updates** proactively if any found
3. **Process user request** (new work, status, resolution)
4. **Spawn workers** if needed
5. **Return quickly** - workers continue in background

### Worker Lifecycle

```
Issue created (open)
     ↓
Foreman spawns worker (→ in_progress)
     ↓
Worker session runs in background
     ↓
Worker completes/blocks → Updates issue status
     ↓
Foreman detects on next turn → Reports to user
```

## Implementation Details

### No Custom Spawn Tool Required

Original design considered a custom `tool-spawn`, but **this isn't needed**:

**Why not needed:**
- Orchestrator can directly use `load_bundle()` + `AmplifierSession`
- These are standard kernel/foundation APIs
- Cleaner separation: orchestration logic stays in orchestrator
- Avoids tool/orchestrator coordination complexity

### Session Independence

Workers run as independent sessions:
- Own context (don't share foreman's context)
- Own LLM calls (parallel execution)
- Own tool access (configured per worker bundle)
- Fire-and-forget (foreman doesn't wait)

### Queue as Coordination Point

The issue queue is the **only** coordination mechanism:
- Workers update issue status
- Foreman polls queue on each turn
- No direct communication between foreman/workers
- Scalable: Add workers without changing foreman

## Future Enhancements

- **Concurrency limits**: Respect `max_concurrent` per pool
- **Worker timeouts**: Detect stuck workers, re-spawn
- **Priority queuing**: High-priority issues jump queue
- **Worker health**: Track success rates per pool
- **Dynamic routing**: ML-based pool selection
