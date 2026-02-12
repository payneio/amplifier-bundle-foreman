# Foreman Orchestrator: Current Implementation

This document provides comprehensive documentation of the Foreman orchestrator's design, implementation, and relationship to the Amplifier ecosystem.

---

## Executive Summary

The Foreman orchestrator is a **custom Amplifier orchestrator module** that implements a conversational work coordination pattern. Instead of executing tasks directly, it acts as a "foreman" that:

1. **Decomposes user requests** into discrete issues
2. **Spawns specialized worker bundles** to handle each issue
3. **Coordinates work** through a shared issue queue
4. **Reports progress proactively** on every conversation turn

This enables parallel execution, background work, and a responsive user experience where the foreman returns quickly while workers continue in the background.

---

## Design Intent and Philosophy

### The Problem Being Solved

Traditional AI agent patterns have the agent do all work sequentially in a single session. This creates several limitations:

- **Blocking execution**: User waits while agent completes all work
- **Context exhaustion**: Long tasks consume the entire context window
- **No parallelism**: Tasks execute one at a time
- **No specialization**: One agent handles everything

### The Foreman Solution

The foreman pattern separates **coordination** from **execution**:

```
┌─────────────────────────────────────────────────────────────────┐
│  FOREMAN (Coordinator)                                          │
│  - Understands user intent                                      │
│  - Breaks down work into issues                                 │
│  - Routes issues to appropriate workers                         │
│  - Reports progress to user                                     │
│  - Handles blockers and clarifications                          │
│  - Returns QUICKLY (sub-second responses)                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ spawns
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  WORKERS (Executors)                                            │
│  - Receive single issue context                                 │
│  - Have specialized tools and instructions                      │
│  - Execute independently in background                          │
│  - Update issue queue when done/blocked                         │
│  - Run in parallel                                              │
└─────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Workers as bundles** | Self-contained with tools, instructions, and security boundaries |
| **Issue queue coordination** | Decoupled communication; workers never talk to foreman directly |
| **Fire-and-forget spawning** | Foreman doesn't wait; checks progress on next turn |
| **Direct bundle loading** | Uses foundation primitives, no custom spawn tool needed |
| **LLM-guided decomposition** | Foreman uses LLM to intelligently break down requests |

---

## Relationship to Amplifier Ecosystem

### Module Type: Orchestrator

The foreman is an **orchestrator module** - one of the five kernel module types in Amplifier:

| Module Type | Purpose | Foreman's Use |
|-------------|---------|---------------|
| **Orchestrator** | Controls the LLM → tool → response loop | **This is the foreman** |
| Provider | LLM backends | Inherited from parent session |
| Tool | Agent capabilities | `issue_manager` for coordination |
| Hook | Lifecycle observers | Standard hook events emitted |
| Context | Memory management | Uses context-simple |

### Orchestrator Contract Compliance

The foreman implements the standard Amplifier orchestrator contract:

```python
class ForemanOrchestrator:
    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize with configuration only."""
        
    async def execute(
        self,
        prompt: str,           # User's message
        context: Any,          # Conversation context
        providers: dict,       # Available LLM providers
        tools: dict,           # Available tools
        hooks: HookRegistry,   # Lifecycle hooks
        coordinator: Any,      # Session coordinator
    ) -> str:
        """Main entry point - called once per user message."""
```

### How It Differs from Standard Orchestrators

| Standard Orchestrator | Foreman Orchestrator |
|-----------------------|----------------------|
| Executes work directly | Delegates to workers |
| Uses all available tools | Uses only `issue_manager` |
| Single session execution | Spawns child sessions |
| Returns when work complete | Returns immediately, work continues in background |
| Sequential processing | Parallel worker execution |

### Foundation Library Integration

The foreman uses `amplifier_foundation` for worker spawning:

```python
from amplifier_foundation import load_bundle, generate_sub_session_id

# Load worker bundle from URL
bundle = await load_bundle(worker_bundle_uri)

# Prepare bundle (activates modules)
prepared = await bundle.prepare()

# Create worker session with parent relationship
worker_session = await prepared.create_session(
    session_id=generate_sub_session_id(agent_name, parent_id, trace_id),
    parent_id=parent_session.session_id,
    approval_system=parent_approval_system,
    display_system=parent_display_system,
    session_cwd=parent_working_dir,
)

# Execute worker
await worker_session.execute(worker_prompt)
```

This approach:
- Uses standard foundation primitives (no custom tool needed)
- Maintains parent-child session relationship
- Inherits providers from parent session
- Shares working directory for consistent file operations

---

## Architecture Overview

### High-Level Data Flow

```
User Request
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│  Foreman Orchestrator execute()                                 │
│                                                                 │
│  1. Emit PROMPT_SUBMIT hook event                               │
│  2. Add user message to context                                 │
│  3. Check for orphaned issues (recovery)                        │
│  4. Check worker progress (completions, blockers)               │
│  5. Build messages with foreman system prompt                   │
│  6. Run LLM agent loop:                                         │
│     - Call provider.complete()                                  │
│     - Handle tool calls (issue_manager)                         │
│     - Auto-spawn workers for created issues                     │
│     - Loop until no tool calls                                  │
│  7. Update context with response                                │
│  8. Emit ORCHESTRATOR_COMPLETE hook event                       │
│  9. Return response string                                      │
└─────────────────────────────────────────────────────────────────┘
     │
     │ for each created issue
     ▼
┌─────────────────────────────────────────────────────────────────┐
│  Worker Spawning (asyncio task, fire-and-forget)                │
│                                                                 │
│  1. Route issue to worker pool                                  │
│  2. Load worker bundle via load_bundle()                        │
│  3. Inherit providers from parent session                       │
│  4. Prepare bundle and create worker session                    │
│  5. Execute worker with issue prompt                            │
│  6. Write session state (metadata.json, transcript.jsonl)       │
│  7. Cleanup worker session                                      │
└─────────────────────────────────────────────────────────────────┘
     │
     │ workers update
     ▼
┌─────────────────────────────────────────────────────────────────┐
│  Issue Queue (shared state via issue_manager tool)              │
│                                                                 │
│  Statuses: open → in_progress → completed                       │
│                             ↘→ blocked                          │
│                             ↘→ pending_user_input               │
└─────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

#### 1. ForemanOrchestrator (orchestrator.py)

**Purpose**: Coordinate work by guiding LLM to create issues and reporting progress.

**Key Methods**:

| Method | Responsibility |
|--------|----------------|
| `execute()` | Main entry point, runs agent loop |
| `_build_messages()` | Construct LLM context with system prompt |
| `_execute_tools()` | Handle tool calls, intercept issue creation |
| `_maybe_spawn_worker()` | Route and spawn worker for new issue |
| `_check_worker_progress()` | Query issue queue for updates |
| `_route_issue()` | Match issue to appropriate worker pool |

**State Tracked**:

| State | Purpose |
|-------|---------|
| `_spawned_issues` | Prevent duplicate worker spawning |
| `_worker_tasks` | Track active asyncio tasks |
| `_spawn_errors` | Collect errors for user reporting |
| `_recovery_done` | Ensure recovery runs only once |
| `_orphaned_issues` | Issues found during recovery |

#### 2. Worker Bundles (workers/*)

**Purpose**: Specialized execution environments for specific task types.

**Included Workers**:

| Worker | Route Types | Capabilities |
|--------|-------------|--------------|
| `coding-worker` | task, feature, bug | File ops, bash, python_check |
| `research-worker` | epic | Web search, documentation |
| `testing-worker` | chore | Test execution, validation |

**Worker Lifecycle**:

```
1. Receive prompt with issue context
2. Claim issue (set status: in_progress)
3. Execute work using available tools
4. Update issue with result:
   - completed: with summary
   - blocked: with explanation
   - pending_user_input: with question
```

#### 3. Issue Queue (via amplifier-bundle-issues)

**Purpose**: Shared coordination state between foreman and workers.

**Issue Schema**:

```python
{
    "id": "abc123-def456",
    "title": "Implement feature X",
    "description": "Detailed requirements...",
    "issue_type": "task",  # task|feature|bug|epic|chore
    "status": "open",      # open|in_progress|completed|blocked|pending_user_input
    "priority": 1,
    "assignee": None,      # Worker session ID when claimed
    "creator": "foreman-session-id",
    "dependencies": [],
    "metadata": {},
    "result": None,
    "block_reason": None,
}
```

---

## Implementation Details

### The Foreman System Prompt

The foreman's behavior is guided by a system prompt injected at the start of each turn:

```python
FOREMAN_SYSTEM_PROMPT = """You are a FOREMAN - a work coordinator who delegates tasks to specialized workers.

## YOUR ROLE

You DO NOT do the work yourself. Instead, you:
1. **Break down requests** into discrete issues using the `issue_manager` tool
2. **Let workers handle** the actual implementation
3. **Report progress** from workers to the user
4. **Handle blockers** when workers need clarification

## WORKFLOW

### When user requests work:
1. Acknowledge the request briefly
2. Use `issue_manager` with operation="create" to create issues
3. Report what issues were created
4. Workers will be spawned automatically

## CRITICAL RULES

- **NEVER use bash, write_file, or other tools directly**
- **ALWAYS create issues** for work that needs to be done
- **Keep responses brief** - workers do the heavy lifting

## ISSUE TYPES

Valid issue types: `task`, `feature`, `bug`, `epic`, `chore`
"""
```

### Worker Spawning Mechanism

When the foreman creates an issue, the orchestrator intercepts the tool call and spawns a worker:

```python
async def _execute_tools(self, tool_calls, tools, issue_tool, hooks):
    for tc in tool_calls:
        result = await tool.execute(tc.arguments)
        
        # Intercept issue creation
        if tc.name == "issue_manager" and tc.arguments.get("operation") == "create":
            await self._maybe_spawn_worker(result.output, issue_tool)
        
        # ... continue with results
```

The spawning is done as an asyncio task:

```python
def _spawn_worker_task(self, issue_id: str, coro) -> None:
    """Spawn worker as asyncio task (fire-and-forget)."""
    task = asyncio.create_task(coro)
    self._worker_tasks[issue_id] = task
    self._spawned_issues.add(issue_id)
    task.add_done_callback(lambda t: self._on_worker_complete(issue_id, t))
```

### Issue Routing

Issues are routed to worker pools based on configuration:

```python
def _route_issue(self, issue: dict) -> dict | None:
    """Route issue to appropriate worker pool."""
    issue_type = issue.get("issue_type") or issue.get("metadata", {}).get("type")
    
    # Check routing rules
    for rule in self.routing_config.get("rules", []):
        if "if_metadata_type" in rule:
            if issue_type in rule["if_metadata_type"]:
                return self._get_pool_by_name(rule["then_pool"])
    
    # Fall back to default pool
    return self._get_pool_by_name(self.routing_config.get("default_pool"))
```

### Session State Persistence

Workers bypass the CLI's SessionStore, so the orchestrator writes session state manually:

```python
async def _write_worker_session_state(self, worker_session, bundle_name, issue_id, working_dir):
    """Write metadata.json and transcript.jsonl for worker session."""
    session_dir = Path.home() / ".amplifier" / "projects" / slug / "sessions" / worker_session.session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    
    # Write metadata.json
    metadata = {
        "session_id": worker_session.session_id,
        "parent_id": parent_id,
        "bundle": f"bundle:{bundle_name}",
        "issue_id": issue_id,
        # ...
    }
    
    # Write transcript.jsonl from context messages
    messages = await context.get_messages()
    # ... write to transcript.jsonl
```

### Recovery Mechanism

On first execute, the orchestrator checks for orphaned issues from crashed sessions:

```python
async def _maybe_recover_orphaned_issues(self, issue_tool):
    """Check for incomplete issues and report them."""
    
    # Find "open" issues (never had a worker)
    open_issues = await issue_tool.execute({"operation": "list", "params": {"status": "open"}})
    
    # Find "in_progress" issues (worker may have died)
    in_progress_issues = await issue_tool.execute({"operation": "list", "params": {"status": "in_progress"}})
    
    # Report to user (don't auto-respawn to avoid blocking)
    self._orphaned_issues = issues_to_recover
```

### Diagnostic Events

The orchestrator emits diagnostic events throughout the worker lifecycle:

| Event | When Emitted |
|-------|--------------|
| `foreman:worker:task_created` | Worker asyncio task created |
| `foreman:worker:task_started` | Task begins executing |
| `foreman:worker:bundle_loading` | Loading worker bundle |
| `foreman:worker:bundle_loaded` | Bundle loaded successfully |
| `foreman:worker:bundle_prepared` | Bundle prepared |
| `foreman:worker:session_created` | Worker session created |
| `foreman:worker:execution_starting` | Worker execution starting |
| `foreman:worker:execution_completed` | Worker finished |
| `foreman:worker:execution_failed` | Worker failed |
| `foreman:worker:session_state_written` | Session state persisted |

---

## Configuration Reference

### Bundle Configuration (bundle.md)

```yaml
session:
  orchestrator:
    module: orchestrator-foreman
    source: git+https://github.com/payneio/amplifier-bundle-foreman@main#subdirectory=modules/orchestrator-foreman
    config:
      # Worker pools
      worker_pools:
        - name: coding-pool
          worker_bundle: git+https://github.com/.../coding-worker
          max_concurrent: 3          # Not yet enforced
          route_types: [task, feature, bug]
        
        - name: research-pool
          worker_bundle: git+https://github.com/.../research-worker
          max_concurrent: 2
          route_types: [epic]
      
      # Routing configuration
      routing:
        default_pool: coding-pool
        rules:
          - if_metadata_type: [task, feature, bug]
            then_pool: coding-pool
          - if_metadata_type: [epic]
            then_pool: research-pool
      
      # General options
      max_iterations: 20           # Max LLM turns per execute()
      extended_thinking: true
```

### Worker Pool Options

| Option | Type | Description |
|--------|------|-------------|
| `name` | string | Pool identifier for routing |
| `worker_bundle` | string | Bundle URL (git+https://, file://, or path) |
| `max_concurrent` | int | Max parallel workers (not yet enforced) |
| `route_types` | list | Issue types to route to this pool |

### Routing Rules

| Field | Description |
|-------|-------------|
| `if_metadata_type` | Match issue types |
| `if_status` | Match issue status |
| `and_retry_count_gte` | Require minimum retry count |
| `then_pool` | Target pool name |

---

## Current Limitations

| Limitation | Description | Future Enhancement |
|------------|-------------|-------------------|
| **No concurrency limits** | All workers spawn regardless of `max_concurrent` | Enforce pool limits |
| **No worker timeouts** | Stuck workers stay in_progress forever | Detect and recover |
| **No dependency tracking** | Can't enforce "B after A completes" | Dependency-aware scheduling |
| **No worker context sharing** | Workers start fresh each time | Optional context inheritance |
| **Recovery is passive** | Reports orphaned issues, doesn't auto-respawn | Optional auto-recovery |

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `modules/orchestrator-foreman/amplifier_module_orchestrator_foreman/orchestrator.py` | Main orchestrator implementation (~1200 lines) |
| `modules/orchestrator-foreman/amplifier_module_orchestrator_foreman/__init__.py` | Module exports and type declaration |
| `bundle.md` | Bundle definition with orchestrator config |
| `context/instructions.md` | Foreman behavior instructions |
| `workers/amplifier-bundle-coding-worker/bundle.md` | Coding worker bundle |
| `workers/amplifier-bundle-research-worker/bundle.md` | Research worker bundle |
| `workers/amplifier-bundle-testing-worker/bundle.md` | Testing worker bundle |

---

## Summary

The Foreman orchestrator represents a **coordination-over-execution** pattern in the Amplifier ecosystem. By separating the LLM that understands user intent (foreman) from the LLMs that execute work (workers), it enables:

1. **Responsive UX**: Foreman returns quickly while work continues
2. **Parallel execution**: Multiple workers run simultaneously
3. **Specialized workers**: Right tools for the right job
4. **Clean separation**: Coordination logic isolated from execution
5. **Scalable pattern**: Add worker types without changing foreman

The implementation leverages standard Amplifier primitives (`load_bundle()`, `PreparedBundle.create_session()`) rather than custom infrastructure, demonstrating how complex patterns can be built on the kernel's simple mechanisms.
