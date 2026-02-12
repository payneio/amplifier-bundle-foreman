# Custom Code Analysis: Why Not Foundation/Core?

This document analyzes every piece of custom code in the Foreman orchestrator, explaining what we use from `amplifier-core` and `amplifier-foundation`, what we had to write ourselves, and why.

---

## Executive Summary

The Foreman orchestrator follows Amplifier's philosophy correctly:

| Category | Count | Assessment |
|----------|-------|------------|
| **Using core/foundation correctly** | 11 uses | ✅ Leveraging existing code |
| **Custom code (required)** | 8 areas | ✅ Not available in core/foundation |
| **Reimplementing existing code** | 0 | ✅ No duplication |
| **Could use but didn't** | 1 area | ⚠️ Minor opportunity |

**Bottom line**: The custom code is justified. Core provides mechanisms (protocols, models, events), foundation provides bundle/session primitives, but orchestrators own their execution policy.

---

## Part 1: What We USE from Core/Foundation

### From amplifier-core

| Import | Location | How We Use It |
|--------|----------|---------------|
| `HookRegistry` | `orchestrator.py:14` | Passed to `execute()`, used for hook emission |
| `ToolSpec` | `orchestrator.py:14` | Building tool specifications for LLM |
| `ORCHESTRATOR_COMPLETE` | `orchestrator.py:15` | Canonical event name for completion |
| `PROMPT_SUBMIT` | `orchestrator.py:15` | Canonical event name for prompt submission |
| `ChatRequest` | `orchestrator.py:16` | Request model for provider.complete() |
| `Message` | `orchestrator.py:16` | Message model for ChatRequest |

**Usage in code**:

```python
# orchestrator.py:486-488 - Using core's message models
message_objects = [Message(**m) for m in messages]
request = ChatRequest(messages=message_objects, tools=tool_specs)
response = await provider.complete(request)

# orchestrator.py:428 - Using canonical events
await hooks.emit(PROMPT_SUBMIT, {"prompt": prompt})

# orchestrator.py:557-564 - Using canonical completion event
await hooks.emit(ORCHESTRATOR_COMPLETE, {
    "orchestrator": "foreman",
    "turn_count": iteration,
    "status": "success" if final_response else "incomplete",
})
```

**Verdict**: ✅ Correct usage of core's canonical models and events.

---

### From amplifier-foundation

| Import | Location | How We Use It |
|--------|----------|---------------|
| `load_bundle` | `orchestrator.py:835` | Loading worker bundles from URI |
| `generate_sub_session_id` | `orchestrator.py:894` | Creating traceable worker session IDs |

**Usage in code**:

```python
# orchestrator.py:835-847 - Using foundation's bundle loading
from amplifier_foundation import load_bundle

bundle = await load_bundle(worker_bundle_uri)
logger.info(f"Loaded bundle '{bundle.name}' for issue {issue_id}")

# orchestrator.py:894-905 - Using foundation's traceable session IDs
from amplifier_foundation import generate_sub_session_id

worker_session_id = generate_sub_session_id(
    agent_name=f"worker-{issue_id[:8]}",
    parent_session_id=parent_id,
    parent_trace_id=getattr(parent_session, "trace_id", parent_session.session_id)
    if parent_session
    else None,
)
```

**Verdict**: ✅ Correct usage of foundation's bundle and tracing primitives.

---

### From foundation: PreparedBundle API

```python
# orchestrator.py:868 - Using bundle.prepare()
prepared = await bundle.prepare()

# orchestrator.py:907-913 - Using prepared.create_session()
worker_session = await prepared.create_session(
    session_id=worker_session_id,
    parent_id=parent_id,
    approval_system=approval_system,
    display_system=display_system,
    session_cwd=Path(parent_working_dir) if parent_working_dir else None,
)
```

**Verdict**: ✅ Correct usage of foundation's session creation.

---

## Part 2: Custom Code We HAD to Write

### 2.1 Agent Loop Implementation

**Location**: `orchestrator.py:476-551`

**What it does**: The main LLM conversation loop that calls the provider, handles tool calls, and iterates until done.

```python
while iteration < self.max_iterations:
    iteration += 1
    
    # Call LLM
    message_objects = [Message(**m) for m in messages]
    request = ChatRequest(messages=message_objects, tools=tool_specs)
    response = await provider.complete(request)
    
    # Extract text content
    # ... custom extraction logic ...
    
    # Handle tool calls
    if response.tool_calls:
        # ... build assistant message ...
        tool_results = await self._execute_tools(...)
        # ... add tool results to messages ...
        continue
    
    break
```

**Why custom?**

| Question | Answer |
|----------|--------|
| Does core provide this? | **No.** Core provides the `Orchestrator` protocol but no implementation. |
| Does foundation provide this? | **No.** Foundation provides bundle/session management, not agent loops. |
| Do standard orchestrator modules exist? | **Yes** (`loop-basic`, etc.), but they're full orchestrators, not reusable utilities. |

**Justification**: The agent loop IS the orchestrator's core responsibility. Amplifier's philosophy is "mechanism, not policy" - the kernel provides mechanisms (protocols, events), orchestrators implement policy (how the loop works). The Foreman's loop is necessarily custom because:

1. It needs to intercept issue creation to spawn workers
2. It injects foreman-specific system prompts
3. It checks worker progress before each turn

**Verdict**: ✅ Required - this is what orchestrators DO.

---

### 2.2 Tool Execution with Hook Events

**Location**: `orchestrator.py:612-681`

**What it does**: Executes tool calls while emitting proper hook events.

```python
async def _execute_tools(self, tool_calls, tools, issue_tool, hooks):
    results = []
    for tc in tool_calls:
        tool = tools.get(tc.name)
        
        # Emit tool:pre event
        await hooks.emit("tool:pre", {"tool_name": tc.name, "arguments": tc.arguments})
        
        try:
            result = await tool.execute(tc.arguments)
            
            # Intercept issue creation for worker spawning
            if tc.name == "issue_manager" and tc.arguments.get("operation") == "create":
                await self._maybe_spawn_worker(result.output, issue_tool)
            
            # Emit tool:post event
            await hooks.emit("tool:post", {"tool_name": tc.name, "result": output})
        except Exception as e:
            # ... error handling with tool:post ...
```

**Why custom?**

| Question | Answer |
|----------|--------|
| Does core provide `execute_tool()` helper? | **No.** Core provides `Tool.execute()` but no wrapper. |
| Does core provide hook emission logic? | **No.** Core provides `HookRegistry` but no standard tool execution wrapper. |

**Justification**: Tool execution patterns vary by orchestrator strategy:

- Some orchestrators batch tool calls
- Some need to intercept specific tools (like we do for issue creation)
- Some need custom error handling strategies
- Some need to process hook results (deny, modify, inject_context)

Our implementation is minimal but custom because we need to intercept `issue_manager` calls to trigger worker spawning.

**Verdict**: ✅ Required - orchestrator-specific tool interception.

---

### 2.3 Message Building Logic

**Location**: `orchestrator.py:494-545`

**What it does**: Converts provider responses to message dictionaries for context.

```python
# Extract text content from response content blocks
if response.content:
    text_parts = []
    for block in response.content:
        if hasattr(block, "text"):
            text_parts.append(block.text)
        elif isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(block.get("text", ""))
    if text_parts:
        final_response = "\n".join(text_parts)

# Build assistant message with tool_calls
assistant_msg = {
    "role": "assistant",
    "content": assistant_content if assistant_content else "",
    "tool_calls": [
        {"id": tc.id, "tool": tc.name, "arguments": tc.arguments}
        for tc in response.tool_calls
    ],
}
```

**Why custom?**

| Question | Answer |
|----------|--------|
| Does core provide message builders? | **No.** Core provides `Message` model but no response-to-message conversion. |
| Does foundation provide this? | **No.** |

**Justification**: Message format requirements vary:

- Different providers return different content block types
- Some orchestrators need to filter thinking blocks
- Some need to preserve tool call structure differently
- Context modules have their own message format expectations

**Verdict**: ✅ Required - no standard helper exists.

---

### 2.4 Worker Spawning Logic

**Location**: `orchestrator.py:683-753`, `orchestrator.py:809-973`

**What it does**: Routes issues to worker pools and spawns worker sessions.

```python
async def _maybe_spawn_worker(self, issue_result, issue_tool):
    """Spawn a worker for a newly created issue."""
    issue_id = issue_result.get("issue", {}).get("id")
    
    # Check if already spawned
    if issue_id in self._spawned_issues:
        task = self._worker_tasks.get(issue_id)
        if task and not task.done():
            return
    
    # Route to appropriate pool
    pool_config = self._route_issue(issue)
    worker_bundle = pool_config.get("worker_bundle")
    
    # Spawn as asyncio task
    self._spawn_worker_task(
        issue_id,
        self._run_spawn_and_handle_result(worker_bundle, worker_prompt, issue_id),
    )
```

**Why custom?**

| Question | Answer |
|----------|--------|
| Does foundation provide worker spawning? | **Partially.** `PreparedBundle.spawn()` exists but... |
| Why not use `spawn()`? | It's designed for agents defined in parent config, not external bundles by URL. |

**Justification**: The foreman pattern requires:

1. **Spawning full external bundles by URL** - not agents from parent config
2. **Fire-and-forget execution** - `spawn()` awaits completion
3. **Custom routing logic** - matching issues to worker pools
4. **Duplicate spawn prevention** - tracking what's already spawned
5. **Asyncio task tracking** - for recovery and status

Foundation's `spawn()` is designed for the `delegate` tool pattern where agents are pre-defined. The foreman spawns arbitrary bundles by URL, which requires using lower-level primitives (`load_bundle()` + `create_session()`).

**Verdict**: ✅ Required - different pattern than foundation's spawn().

---

### 2.5 Session State Persistence

**Location**: `orchestrator.py:206-303`

**What it does**: Writes `metadata.json` and `transcript.jsonl` for worker sessions.

```python
async def _write_worker_session_state(self, worker_session, bundle_name, issue_id, working_dir):
    """Write metadata.json and transcript.jsonl for a worker session."""
    
    # Build session directory path
    session_dir = Path.home() / ".amplifier" / "projects" / slug / "sessions" / worker_session.session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    
    # Write metadata.json
    metadata = {
        "session_id": worker_session.session_id,
        "parent_id": parent_id,
        "created": datetime.now(timezone.utc).isoformat(),
        "bundle": f"bundle:{bundle_name}",
        "model": model,
        "turn_count": 1,
        "issue_id": issue_id,
        "incremental": True,
    }
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)
    
    # Write transcript.jsonl
    context = worker_session.coordinator.get("context")
    if context and hasattr(context, "get_messages"):
        messages = await context.get_messages()
        with open(transcript_path, "w") as f:
            for msg in messages:
                f.write(json.dumps(msg) + "\n")
```

**Why custom?**

| Question | Answer |
|----------|--------|
| Does core provide session persistence? | **No.** Core manages session lifecycle, not storage. |
| Does foundation provide this? | **No.** Example 14 shows a pattern but no utility. |
| Does the CLI provide this? | **Yes**, but via `SessionStore` which workers bypass. |

**Justification**: Workers spawned via `PreparedBundle.create_session()` bypass the CLI's `SessionStore`. Without this custom code, worker sessions:

- Don't appear in `amplifier session list`
- Can't be resumed
- Have no transcript for debugging

This is **app-layer policy** that the foreman must implement because it's spawning sessions outside the normal CLI flow.

**Verdict**: ✅ Required - workers bypass CLI's SessionStore.

---

### 2.6 Issue Routing Logic

**Location**: `orchestrator.py:975-1000`

**What it does**: Matches issues to appropriate worker pools based on type/metadata.

```python
def _route_issue(self, issue):
    """Route issue to appropriate worker pool based on metadata."""
    issue_type = issue.get("issue_type") or issue.get("metadata", {}).get("type", "general")
    
    # Check routing rules
    for rule in self.routing_config.get("rules", []):
        if "if_metadata_type" in rule:
            if issue_type in rule["if_metadata_type"]:
                return self._get_pool_by_name(rule["then_pool"])
    
    # Fall back to default pool
    return self._get_pool_by_name(self.routing_config.get("default_pool"))
```

**Why custom?**

| Question | Answer |
|----------|--------|
| Does core/foundation provide routing? | **No.** This is foreman-specific business logic. |

**Justification**: Routing logic is entirely domain-specific to the foreman pattern:

- Issue types are foreman's concept
- Worker pools are foreman's concept
- Routing rules are foreman's configuration

No generic router would help here.

**Verdict**: ✅ Required - domain-specific business logic.

---

### 2.7 Recovery Mechanism

**Location**: `orchestrator.py:328-405`

**What it does**: Detects orphaned issues from crashed sessions.

```python
async def _maybe_recover_orphaned_issues(self, issue_tool):
    """Check for incomplete issues and respawn workers."""
    if self._recovery_done:
        return 0
    
    self._recovery_done = True
    
    # Find "open" issues (never had a worker)
    open_result = await issue_tool.execute({"operation": "list", "params": {"status": "open"}})
    
    # Find "in_progress" issues (worker may have died)
    in_progress_result = await issue_tool.execute({"operation": "list", "params": {"status": "in_progress"}})
    
    # Report to user (passive recovery)
    self._orphaned_issues = issues_to_recover
```

**Why custom?**

| Question | Answer |
|----------|--------|
| Does core provide recovery? | **No.** Core provides session resume, not issue queue recovery. |

**Justification**: Recovery is specific to the foreman's issue-based coordination model:

- Only foreman knows about issues and their statuses
- Only foreman knows which issues should have workers
- Recovery strategy (passive reporting vs auto-respawn) is policy

**Verdict**: ✅ Required - foreman-specific recovery logic.

---

### 2.8 Progress Checking

**Location**: `orchestrator.py:1097-1158`

**What it does**: Queries issue queue for completions and blockers.

```python
async def _check_worker_progress(self, issue_tool):
    """Check for completed or blocked issues from workers."""
    parts = []
    
    # Check completed issues
    completed_result = await issue_tool.execute({
        "operation": "list",
        "params": {"status": "completed"},
    })
    
    # Check blocked issues
    blocked_result = await issue_tool.execute({
        "operation": "list",
        "params": {"status": "pending_user_input"},
    })
    
    # Format report
    if completed:
        parts.append(f"✅ {len(completed)} issue(s) completed by workers")
```

**Why custom?**

| Question | Answer |
|----------|--------|
| Does core/foundation provide progress checking? | **No.** This is foreman-specific UX. |

**Justification**: Progress checking and reporting is the foreman's unique value:

- Queries the issue queue (foreman's coordination mechanism)
- Formats reports for user consumption
- Tracks what's been reported to avoid duplicates

**Verdict**: ✅ Required - core foreman UX logic.

---

## Part 3: Potential Improvement Opportunity

### 3.1 Hook Result Processing

**Current code** (`orchestrator.py:429-434`):

```python
prompt_submit_result = await hooks.emit(PROMPT_SUBMIT, {"prompt": prompt})
if coordinator:
    prompt_submit_result = await coordinator.process_hook_result(
        prompt_submit_result, "prompt:submit", "orchestrator"
    )
    if prompt_submit_result.action == "deny":
        return f"Operation denied: {prompt_submit_result.reason}"
```

**Assessment**: We correctly delegate hook result processing to the coordinator, but we don't fully handle all hook actions (modify, inject_context, ask_user) in tool execution.

**In `_execute_tools()`** (`orchestrator.py:634-639`):

```python
# Emit tool:pre event
if hooks:
    await hooks.emit(
        "tool:pre",
        {"tool_name": tc.name, "arguments": tc.arguments},
    )
```

**Missing**: We emit `tool:pre` but don't process the hook result for deny/modify actions.

**Impact**: Low. The foreman only has one tool (`issue_manager`) which is unlikely to be blocked by hooks.

**Recommendation**: For completeness, we could add:

```python
pre_result = await hooks.emit("tool:pre", {...})
if coordinator:
    pre_result = await coordinator.process_hook_result(pre_result, "tool:pre", tc.name)
    if pre_result.action == "deny":
        results.append({"tool_call_id": tc.id, "content": f"Denied: {pre_result.reason}"})
        continue
```

**Verdict**: ⚠️ Minor improvement opportunity, not a bug.

---

## Summary Table

| Custom Code Area | Lines | Why Not Core/Foundation? | Verdict |
|------------------|-------|--------------------------|---------|
| Agent loop | 75 | Orchestrators implement their own loops | ✅ Required |
| Tool execution | 70 | Need custom interception for issue_manager | ✅ Required |
| Message building | 50 | No standard response-to-message helper | ✅ Required |
| Worker spawning | 170 | Different pattern than foundation's spawn() | ✅ Required |
| Session state | 100 | Workers bypass CLI's SessionStore | ✅ Required |
| Issue routing | 25 | Domain-specific business logic | ✅ Required |
| Recovery | 80 | Foreman-specific issue queue recovery | ✅ Required |
| Progress checking | 60 | Core foreman UX functionality | ✅ Required |
| Hook result processing | - | Could be more complete | ⚠️ Minor opportunity |

---

## Alignment with Amplifier Philosophy

### "Mechanism, Not Policy"

The foreman correctly uses core's **mechanisms**:
- `HookRegistry` for event emission
- `ChatRequest/Message` for provider communication
- Canonical event names (`PROMPT_SUBMIT`, `ORCHESTRATOR_COMPLETE`)

And implements its own **policy**:
- How the agent loop works
- When and how to spawn workers
- How to route issues
- How to report progress

### "Orchestrator is THE Control Surface"

From the kernel philosophy: "The orchestrator controls the entire execution loop. Swapping orchestrators can radically change how an agent behaves."

The foreman's custom code IS the custom behavior:
- Standard orchestrator: LLM does work directly
- Foreman orchestrator: LLM coordinates, workers execute

This is the intended use of the orchestrator module type.

### "Bricks & Studs"

The foreman is a self-contained brick:
- Uses stable interfaces (protocols, events)
- Can be swapped for a different orchestrator
- Doesn't modify core or foundation
- Workers are separate bricks with their own interfaces

---

## Conclusion

**All custom code is justified.** The Foreman orchestrator:

1. ✅ Uses core's models and events correctly
2. ✅ Uses foundation's bundle/session primitives correctly
3. ✅ Writes custom code only where core/foundation don't provide utilities
4. ✅ Follows Amplifier's "mechanism not policy" philosophy
5. ⚠️ Has one minor opportunity for improvement (hook result processing)

The ~630 lines of custom code (excluding the system prompt) implement the foreman's unique execution policy. This is exactly what orchestrators are meant to do.
