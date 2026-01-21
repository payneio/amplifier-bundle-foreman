# Foreman Bundle Design Specification v2

## Overview

The Foreman Bundle provides **conversational autonomous work orchestration**. It acts as a proxy for the user, distributing work across multiple specialized worker bundles, coordinating their execution through a shared issue queue, and keeping the user informed with minimal interruption.

### Core Capabilities

- **Conversational interaction**: Immediate responses, add work anytime, ask for status anytime
- **Background worker coordination**: Workers run as separate sessions via spawn tool
- **Proactive progress updates**: Reports completions and blockers automatically
- **Specialized worker bundles**: Self-contained, reusable worker types
- **Issue-based coordination**: Shared issue queue as coordination primitive
- **Multiple worker pools**: Parallel execution across different worker types
- **Intelligent routing**: Route work to appropriate worker bundles

### Use Cases

- Multi-file refactoring with parallel workers
- Research → implement → test workflows
- Complex multi-step implementations
- Parallel feature development
- Long-running background tasks with periodic updates
- Projects requiring different worker specializations

---

## User Experience

### Conversational Flow

```
User: "Refactor the authentication system"

Foreman: 📋 Analyzing work request...

         Created 5 issues:
           • Issue #1: Split auth.py into modules
           • Issue #2: Update imports across codebase
           • Issue #3: Update unit tests
           • Issue #4: Add integration tests
           • Issue #5: Update documentation

         🚀 Spawned 5 workers to handle these issues.
         I'll keep you posted on progress!

[Workers run in background as separate sessions]

User: "Also add rate limiting to the API"

Foreman: ✅ **Completed (2)**:
           • Split auth.py into modules
           • Update unit tests

         📋 Analyzing work request...

         Created 3 issues:
           • Issue #6: Design rate limiting strategy
           • Issue #7: Implement rate limiter
           • Issue #8: Add rate limiting tests

         🚀 Spawned 3 workers.

User: "What's the status?"

Foreman: ✅ **Completed (1)**:
           • Update imports across codebase

         ⚠️  **Need Your Input (1)**:
           • Design rate limiting strategy
             → Should we use token bucket or sliding window?

         📊 **Current Status**

         ⏳ **In Progress** (4):
           • Add integration tests
           • Update documentation
           • Implement rate limiter
           • Add rate limiting tests

         ✅ **Completed** (3)

User: "Use token bucket, 100 requests per minute"

Foreman: ✅ Got it! Resuming work on **Design rate limiting strategy**.

[Worker continues with context]

User: "status"

Foreman: ✅ **Completed (5)**:
           • Design rate limiting strategy
           • Implement rate limiter
           • Add integration tests
           • Add rate limiting tests
           • Update documentation

         All clear - no active work!
```

### Key Characteristics

1. **Immediate responses**: Foreman acknowledges requests and creates issues right away
2. **Add work anytime**: Don't wait for workers to finish
3. **Ask for status anytime**: Get instant progress report
4. **Proactive updates**: Completions and blockers reported automatically on every turn
5. **Natural conversation**: Treat foreman like a team member

---

## Architecture

### High-Level Structure

```
┌─────────────────────────────────────────────────────────────┐
│                     Foreman Session                         │
│                 (Conversational Orchestrator)               │
│                                                              │
│  Every execute() call:                                      │
│  1. Check issue queue for updates                           │
│  2. Report completions/blockers                             │
│  3. Process user request                                    │
│  4. Spawn workers if needed                                 │
│  5. Return quickly                                          │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ spawns via spawn tool
                              │
            ┌─────────────────┼─────────────────┐
            ↓                 ↓                  ↓
    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
    │ Coding       │  │ Research     │  │ Testing      │
    │ Worker       │  │ Worker       │  │ Worker       │
    │ Bundle       │  │ Bundle       │  │ Bundle       │
    │              │  │              │  │              │
    │ (separate    │  │ (separate    │  │ (separate    │
    │  session)    │  │  session)    │  │  session)    │
    └──────────────┘  └──────────────┘  └──────────────┘
            │                 │                  │
            └─────────────────┼──────────────────┘
                              │
                    ┌─────────▼──────────┐
                    │   Issue Queue      │
                    │   (Shared State)   │
                    │                    │
                    │  - open            │
                    │  - in_progress     │
                    │  - completed       │
                    │  - blocked         │
                    │  - pending_input   │
                    └────────────────────┘
```

### Component Responsibilities

#### 1. Foreman Orchestrator (Core)
- Implements standard orchestrator contract (`execute()`)
- Checks issue queue on every turn for updates
- Reports completions/blockers proactively
- Routes issues to appropriate worker bundles
- Spawns workers via spawn tool (fire-and-forget)
- Returns quickly (sub-second response time)

#### 2. Worker Bundles (Separate Repos)
- Self-contained bundles with:
  - Specialized instructions
  - Specific tool access
  - Security boundaries
  - Version control
- Run as independent sessions
- Update issue queue when complete/blocked
- Focus on single issue at a time

#### 3. Issue Queue (Shared State)
- JSON-based work items
- Status tracking (open, in_progress, completed, blocked, pending_user_input)
- Metadata for routing
- Dependency relationships
- Results storage

#### 4. Spawn Tool (Integration Point)
- Custom tool for spawning worker bundles
- Accepts bundle paths/URLs (local or git+https://)
- Loads bundles dynamically
- Passes issue context to workers
- Returns immediately (non-blocking)
- Workers run independently in background

---

## Orchestrator Implementation

### Contract Compliance

The foreman implements the standard Amplifier orchestrator contract:

```python
from amplifier_core import HookRegistry
from typing import Any


class ForemanOrchestrator:
    """Conversational orchestrator that coordinates background workers."""
    
    def __init__(self, config: dict[str, Any]):
        """Initialize with configuration only (no session)."""
        self.config = config
        self.worker_pools = config.get("worker_pools", [])
        
        # Track what we've reported to avoid repetition
        self._reported_completions = set()
        self._reported_blockers = set()
    
    async def execute(
        self,
        prompt: str,
        context,
        providers: dict[str, Any],
        tools: dict[str, Any],
        hooks: HookRegistry,
    ) -> str:
        """
        Main orchestrator entry point.
        
        Called once per user message. Returns quickly after:
        1. Checking for worker updates
        2. Reporting updates
        3. Processing current request
        4. Spawning workers if needed
        """
        
        issue_tool = tools["issue"]
        task_tool = tools["task"]
        
        response_parts = []
        
        # Step 1: Check issue queue for updates from background workers
        updates = await self._check_worker_updates(issue_tool)
        
        # Step 2: Report completions
        if updates["completions"]:
            msg = self._format_completions(updates["completions"])
            response_parts.append(msg)
        
        # Step 3: Report blockers
        if updates["blockers"]:
            msg = self._format_blockers(updates["blockers"])
            response_parts.append(msg)
        
        # Step 4: Process user's current request
        request_response = await self._process_request(
            prompt, issue_tool, task_tool, context
        )
        if request_response:
            response_parts.append(request_response)
        
        # Step 5: Return quickly
        if not response_parts:
            response_parts.append("All systems running. Let me know if you need anything!")
        
        return "\n\n".join(response_parts)
```

### Key Implementation Points

**1. No `self.session`**: Orchestrators don't have session access
- ✅ Use `tools` parameter: `tools["issue"]`
- ❌ Don't use: `self.session.call_tool()`

**2. Standard `execute()` signature**: Not custom `run()` loop
- ✅ `async def execute(self, prompt, context, providers, tools, hooks) -> str`
- ❌ `async def run(self)`

**3. Quick returns**: Not long-running loops
- ✅ Check queue, spawn workers, return
- ❌ `while True: await asyncio.sleep()`

**4. Tool usage pattern**:
```python
# Correct
issue_tool = tools["issue"]
result = await issue_tool.execute({"operation": "list", ...})

# Incorrect
await self.session.call_tool("issue", {...})
```

---

## Worker Bundle Architecture

### Worker Bundles are Self-Contained

Each worker type is a separate bundle repository:

```
amplifier-bundle-coding-worker/
├── bundle.md                    # Bundle definition
├── context/
│   └── coding-instructions.md  # Specialized instructions
├── pyproject.toml
└── README.md
```

### Example: Coding Worker Bundle

**File**: `amplifier-bundle-coding-worker/bundle.md`

```yaml
---
bundle:
  name: coding-worker
  version: 1.0.0
  description: Specialized worker for coding tasks
---

# Coding Worker

You are a coding specialist handling implementation tasks.

## Your Responsibilities

- Implement features from issue descriptions
- Write clean, tested code
- Follow project coding standards
- Update issue status when complete
- Ask for help if requirements unclear

## Your Capabilities

You have access to:
- **File operations**: Read, write, edit (src/ and tests/ only)
- **Code execution**: Run tests via bash
- **Code checking**: Python type checking and linting
- **Issue management**: Update status, add results

You DO NOT have:
- Web access (not your job)
- Task spawning (can't create more workers)
- Privileged operations

## Workflow

1. Read issue description carefully
2. Read relevant existing code
3. Implement the feature
4. Run tests to verify
5. Update issue status:
   - `completed` with summary if done
   - `blocked` with reason if stuck
   - `pending_user_input` if need clarification

@coding-worker:context/coding-instructions.md

---

tools:
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
    config:
      allowed_write_paths: ["src/**", "tests/**"]
      # Cannot write to config, .env, etc.
  
  - module: tool-bash
    source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
  
  - module: tool-issue
    source: git+https://github.com/microsoft/amplifier-bundle-issues@main
  
  - module: python-check
    source: git+https://github.com/microsoft/amplifier-bundle-python-dev@main

# Note: No web tools, no spawn tool
```

### Example: Research Worker Bundle

**File**: `amplifier-bundle-research-worker/bundle.md`

```yaml
---
bundle:
  name: research-worker
  version: 1.0.0
  description: Specialized worker for research tasks
---

# Research Worker

You are a research specialist handling information gathering tasks.

## Your Responsibilities

- Search for relevant information
- Analyze and synthesize findings
- Document results clearly
- Update issue status when complete

## Your Capabilities

You have access to:
- **Web access**: Search and fetch web content
- **File operations**: Read existing docs, write research findings
- **Issue management**: Update status, add results

You DO NOT have:
- Code editing (not your job)
- Bash execution (not needed)
- Task spawning

@research-worker:context/research-instructions.md

---

tools:
  - module: tool-web-search
    source: git+https://github.com/microsoft/amplifier-module-tool-web@main
  
  - module: tool-web-fetch
    source: git+https://github.com/microsoft/amplifier-module-tool-web@main
  
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
    config:
      allowed_write_paths: ["research/**", "docs/**"]
      # Can document findings, not modify code
  
  - module: tool-issue
    source: git+https://github.com/microsoft/amplifier-bundle-issues@main

# Note: No bash, no code editing tools
```

### Worker Bundle Benefits

1. **Self-contained**: Tools + instructions + security in one place
2. **Reusable**: Different foremans can use same worker bundles
3. **Versionable**: Update workers independently (`@v1.0.0`, `@v2.0.0`)
4. **Distributable**: Workers can live in separate repos
5. **Clear capabilities**: Look at bundle to know exactly what worker can do
6. **Security boundaries**: Tool restrictions defined in worker, not foreman

---

## Foreman Bundle Configuration

### Complete Foreman Bundle

**File**: `amplifier-bundle-foreman/bundle.md`

```yaml
---
bundle:
  name: foreman
  version: 1.0.0
  description: Autonomous work orchestration bundle
---

# Foreman Orchestrator

You are a foreman orchestrating work on behalf of the user.

@foreman:context/foreman-instructions.md

---

tools:
  - module: tool-task
    source: git+https://github.com/microsoft/amplifier-module-tool-task@main
  
  - module: tool-issue
    source: git+https://github.com/microsoft/amplifier-bundle-issues@main
  
  - module: tool-todo
    source: git+https://github.com/microsoft/amplifier-module-tool-todo@main

orchestrator:
  module: orchestrator-foreman
  source: git+https://github.com/microsoft/amplifier-module-orchestrator-foreman@main
  config:
    # Worker pool configuration
    worker_pools:
      # Coding tasks
      - name: coding-pool
        worker_bundle: git+https://github.com/org/amplifier-bundle-coding-worker@v1.0.0
        max_concurrent: 3
        route_types: [coding, implementation, bugfix, refactor]
      
      # Research tasks
      - name: research-pool
        worker_bundle: git+https://github.com/org/amplifier-bundle-research-worker@v1.0.0
        max_concurrent: 2
        route_types: [research, analysis, investigation]
      
      # Testing tasks
      - name: testing-pool
        worker_bundle: git+https://github.com/org/amplifier-bundle-testing-worker@v1.0.0
        max_concurrent: 2
        route_types: [testing, qa, verification]
      
      # Privileged tasks (use sparingly)
      - name: privileged-pool
        worker_bundle: git+https://github.com/org/amplifier-bundle-privileged-worker@v1.0.0
        max_concurrent: 1
        route_types: [blocked, escalated]
        # Only used when other workers fail
    
    # Routing configuration
    routing:
      default_pool: coding-pool
      
      # Metadata-based routing
      rules:
        - if_metadata_type: [coding, implementation, bugfix]
          then_pool: coding-pool
        
        - if_metadata_type: [research, analysis]
          then_pool: research-pool
        
        - if_metadata_type: [testing, qa]
          then_pool: testing-pool
        
        - if_status: blocked
          and_retry_count_gte: 2
          then_pool: privileged-pool
```

---

## Issue-Based Coordination

### Issue Schema

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class Issue:
    """Work item for coordination."""
    
    id: str                      # Unique identifier
    title: str                   # Short description
    description: str             # Detailed description
    status: str                  # State
    priority: int                # 0 (highest) to 4 (lowest)
    assignee: str | None         # Worker session ID if assigned
    creator: str                 # Session that created issue
    created_at: datetime
    updated_at: datetime
    dependencies: list[str]      # Issue IDs this depends on
    metadata: dict[str, Any]     # Routing hints and custom data
    result: str | None           # Result when completed
    block_reason: str | None     # Why blocked (if blocked)
    retry_count: int            # Number of retry attempts


# Status values
STATUS_OPEN = "open"                      # Ready for work
STATUS_IN_PROGRESS = "in_progress"        # Worker assigned
STATUS_COMPLETED = "completed"            # Successfully done
STATUS_BLOCKED = "blocked"                # Worker stuck
STATUS_PENDING_USER_INPUT = "pending_user_input"  # Need user clarification
```

### Issue Status Flow

```
open
  │
  ├─→ in_progress (worker assigned)
  │     │
  │     ├─→ completed (success)
  │     │
  │     ├─→ blocked (needs resolution)
  │     │     │
  │     │     ├─→ in_progress (resolver spawned)
  │     │     │
  │     │     └─→ pending_user_input (escalated)
  │     │
  │     └─→ open (worker failed, back to queue)
  │
  └─→ pending_user_input (requires clarification)
```

### Metadata-Based Routing

Issues include metadata for intelligent routing:

```python
# Coding task
{
    "id": "issue-123",
    "title": "Implement user authentication",
    "description": "Add login/logout functionality...",
    "status": "open",
    "metadata": {
        "type": "coding",
        "language": "python",
        "complexity": "medium",
        "files": ["src/auth.py", "src/user.py"]
    }
}

# Research task
{
    "id": "issue-124",
    "title": "Research OAuth providers",
    "description": "Compare OAuth implementations...",
    "status": "open",
    "metadata": {
        "type": "research",
        "topic": "oauth",
        "deliverable": "comparison_doc"
    }
}

# Testing task
{
    "id": "issue-125",
    "title": "Write authentication tests",
    "description": "Add unit tests for auth...",
    "status": "open",
    "metadata": {
        "type": "testing",
        "test_framework": "pytest",
        "depends_on": ["issue-123"]
    }
}
```

---

## Worker Spawning

### How Foreman Spawns Workers

```python
async def _spawn_worker(
    self,
    issue: dict,
    pool_config: dict,
    spawn_tool,
    issue_tool
) -> None:
    """Spawn worker for issue using configured worker bundle."""
    
    # Mark issue as in_progress
    await issue_tool.execute({
        "operation": "update",
        "issue_id": issue["id"],
        "status": "in_progress"
    })
    
    # Build worker prompt with issue context
    worker_prompt = f"""You are handling issue #{issue['id']}.

## Issue Details
Title: {issue['title']}
Description: {issue['description']}
Priority: {issue['priority']}

## Your Task
Complete this work. When done:
- Update issue to 'completed' with results
- If blocked, update to 'blocked' with reason
- If need user input, update to 'pending_user_input' with question

Focus on this specific issue.
"""
    
    # Extract worker bundle path/URL from pool config
    worker_bundle = pool_config["worker_bundle"]
    # Example: "../amplifier-bundle-coding-worker" (local)
    # Or: "git+https://github.com/org/amplifier-bundle-coding-worker@v1.0.0" (git)
    
    # Spawn worker (fire-and-forget)
    await spawn_tool.execute({
        "worker_bundle": worker_bundle,  # Bundle path or URL
        "instruction": worker_prompt,
        "issue_id": issue["id"],  # Pass issue context
    })
    
    # Returns immediately - worker runs in background
```

### Worker Execution

When spawn tool spawns a worker:

1. **Load worker bundle**: Parse bundle path/URL, load bundle.md
2. **Create session**: New AmplifierSession with worker bundle config
3. **Mount tools**: Worker bundle's tools mounted (e.g., filesystem, bash, issue)
4. **Inject context**: Worker bundle's instructions loaded
5. **Execute**: Worker runs instruction
6. **Update issue**: Worker updates status when done/blocked
7. **Session ends**: Worker session terminates

### Parallel Worker Spawning

Spawn multiple workers by making multiple spawn calls in single turn:

```python
async def _spawn_workers_parallel(
    self,
    issues: list[dict],
    spawn_tool,
    issue_tool
) -> None:
    """Spawn multiple workers in parallel."""
    
    spawn_tasks = []
    
    for issue in issues:
        # Route issue to appropriate pool
        pool_config = self._route_issue(issue)
        
        # Create spawn task
        task = self._spawn_worker(issue, pool_config, spawn_tool, issue_tool)
        spawn_tasks.append(task)
    
    # All spawn in parallel (all within same orchestrator turn)
    await asyncio.gather(*spawn_tasks)
```

---

## Progress Reporting

### Automatic Update Detection

On every `execute()` call, foreman checks for updates:

```python
async def _check_worker_updates(self, issue_tool) -> dict:
    """Check issue queue for updates from background workers."""
    
    # Get recently completed issues
    completed_result = await issue_tool.execute({
        "operation": "list",
        "filter": {"status": "completed"}
    })
    completed = completed_result.output.get("issues", [])
    
    # Filter to NEW completions (not already reported)
    new_completions = [
        issue for issue in completed
        if issue["id"] not in self._reported_completions
    ]
    
    # Mark as reported
    for issue in new_completions:
        self._reported_completions.add(issue["id"])
    
    # Get blocked issues
    blocked_result = await issue_tool.execute({
        "operation": "list",
        "filter": {"status": "pending_user_input"}
    })
    blocked = blocked_result.output.get("issues", [])
    
    # Filter to NEW blockers
    new_blockers = [
        issue for issue in blocked
        if issue["id"] not in self._reported_blockers
    ]
    
    # Mark as reported
    for issue in new_blockers:
        self._reported_blockers.add(issue["id"])
    
    return {
        "completions": new_completions,
        "blockers": new_blockers
    }
```

### Status Command

User can ask for status anytime:

```python
async def _get_full_status(self, issue_tool) -> str:
    """Generate comprehensive status report."""
    
    all_result = await issue_tool.execute({"operation": "list"})
    all_issues = all_result.output.get("issues", [])
    
    # Categorize
    open_issues = [i for i in all_issues if i["status"] == "open"]
    in_progress = [i for i in all_issues if i["status"] == "in_progress"]
    completed = [i for i in all_issues if i["status"] == "completed"]
    blocked = [i for i in all_issues if i["status"] == "pending_user_input"]
    
    # Format report
    status = "📊 **Current Status**\n\n"
    
    if in_progress:
        status += f"⏳ **In Progress** ({len(in_progress)}):\n"
        for issue in in_progress[:5]:
            status += f"  • {issue['title']}\n"
        if len(in_progress) > 5:
            status += f"  ... and {len(in_progress) - 5} more\n"
    
    if blocked:
        status += f"\n⚠️  **Blocked** ({len(blocked)}):\n"
        for issue in blocked:
            status += f"  • {issue['title']}\n"
    
    if completed:
        status += f"\n✅ **Completed** ({len(completed)})\n"
    
    return status
```

---

## Configuration Patterns

### Pattern 1: Simple Parallel Work

Single user request broken into parallel subtasks:

```yaml
orchestrator:
  module: orchestrator-foreman
  config:
    worker_pools:
      - name: coding-pool
        worker_bundle: coding-worker@v1.0.0
        max_concurrent: 5
```

**Example workflow:**
```
User: "Refactor authentication system"
  ↓
Foreman: Creates 5 issues
  ↓
Spawns 5 coding workers in parallel
  ↓
Reports completions as they finish
```

### Pattern 2: Sequential Pipeline

Research → implement → test workflow:

```yaml
orchestrator:
  module: orchestrator-foreman
  config:
    worker_pools:
      - name: research-pool
        worker_bundle: research-worker@v1.0.0
        route_types: [research]
      
      - name: coding-pool
        worker_bundle: coding-worker@v1.0.0
        route_types: [coding]
      
      - name: testing-pool
        worker_bundle: testing-worker@v1.0.0
        route_types: [testing]
```

**Example workflow:**
```
User: "Add OAuth support"
  ↓
Issue 1: Research OAuth (research-pool)
  ↓ (completes)
Issue 2: Implement OAuth (coding-pool, depends on #1)
  ↓ (completes)
Issue 3: Test OAuth (testing-pool, depends on #2)
  ↓
Report complete
```

### Pattern 3: Error Recovery

Automatic retry with escalation:

```yaml
orchestrator:
  module: orchestrator-foreman
  config:
    worker_pools:
      - name: basic-pool
        worker_bundle: coding-worker@v1.0.0
        max_concurrent: 3
      
      - name: privileged-pool
        worker_bundle: privileged-worker@v1.0.0
        max_concurrent: 1
    
    routing:
      rules:
        - if_status: blocked
          and_retry_count_gte: 2
          then_pool: privileged-pool
```

**Example workflow:**
```
Worker fails with error
  ↓
Foreman respawns in basic-pool (retry 1)
  ↓
Worker fails again
  ↓
Foreman respawns in basic-pool (retry 2)
  ↓
Worker fails third time
  ↓
Foreman escalates to privileged-pool
  ↓
If still fails: pending_user_input
```

---

## Integration & Composition

### Composition with Observer Bundle

Combine foreman with observers for quality feedback:

```yaml
name: orchestrated-development

includes:
  # Work distribution
  - bundle: foreman-bundle
  
  # Quality feedback
  - bundle: observer-bundle
    config:
      observers:
        - name: code-quality
          watches: [files]
          creates_issues: [feedback]

# Workflow:
# 1. Foreman spawns workers
# 2. Workers produce code
# 3. Observers create feedback issues
# 4. Foreman spawns workers to address feedback
```

---

## Implementation Checklist

### Foreman Orchestrator Module

- [ ] Implement `ForemanOrchestrator` class
- [ ] Implement standard `execute()` method
- [ ] Implement `_check_worker_updates()`
- [ ] Implement `_process_request()` (work/status/resolution)
- [ ] Implement `_spawn_worker()` with worker bundle loading
- [ ] Implement `_route_issue()` based on metadata
- [ ] Implement progress reporting
- [ ] Implement status command
- [ ] Add mount function

### Worker Bundles (Examples)

- [ ] Create `amplifier-bundle-coding-worker`
- [ ] Create `amplifier-bundle-research-worker`
- [ ] Create `amplifier-bundle-testing-worker`
- [ ] Create `amplifier-bundle-privileged-worker`

### Testing

- [ ] Test conversational flow (add work, status, resolution)
- [ ] Test parallel worker spawning
- [ ] Test progress reporting (no duplicates)
- [ ] Test issue routing
- [ ] Test worker bundle loading
- [ ] Test tool restrictions in worker bundles
- [ ] Integration test: complete workflow end-to-end

### Documentation

- [ ] User guide for foreman usage
- [ ] Worker bundle creation guide
- [ ] Configuration reference
- [ ] Example configurations

---

## Summary

The Foreman Bundle provides conversational autonomous work orchestration through:

- **Conversational pattern**: Immediate responses, add work anytime, proactive updates
- **Workers as bundles**: Self-contained, reusable worker types with tools + instructions
- **Issue-based coordination**: Shared queue as coordination primitive
- **Background execution**: Workers run as separate sessions via spawn tool
- **Proper orchestrator contract**: Implements `execute()`, uses `tools` parameter
- **Multiple worker pools**: Parallel execution across specialized workers
- **Intelligent routing**: Metadata-based worker selection
- **Progress transparency**: Automatic completion/blocker reporting
- **Escalation logic**: Knows when to ask for help

### Key Architectural Decisions

1. ✅ **Workers are bundles**: Not just agent names with dynamic config
2. ✅ **Conversational pattern**: Not long-running daemon
3. ✅ **Background workers**: Fire-and-forget via spawn tool
4. ✅ **Issue queue**: Single source of truth for coordination
5. ✅ **Proactive reporting**: Check and report on every turn
6. ✅ **Standard orchestrator**: Implements proper contract
