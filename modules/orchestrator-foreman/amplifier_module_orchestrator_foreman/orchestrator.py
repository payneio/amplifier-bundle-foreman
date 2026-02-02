"""
Foreman Orchestrator - LLM-driven work coordination through issues and workers.

This orchestrator runs a standard agent loop but guides the LLM to coordinate work
through issues and background workers rather than doing everything directly.
"""

import asyncio
import json
import logging
import os
from typing import Any

from amplifier_core import HookRegistry, ToolSpec
from amplifier_core.events import ORCHESTRATOR_COMPLETE, PROMPT_SUBMIT
from amplifier_core.message_models import ChatRequest, Message

logger = logging.getLogger(__name__)

# System prompt that makes the LLM act as a foreman
FOREMAN_SYSTEM_PROMPT = """You are a FOREMAN - a work coordinator who delegates tasks to specialized workers.

## YOUR ROLE

You DO NOT do the work yourself. Instead, you:
1. **Break down requests** into discrete issues using the `issue_manager` tool
2. **Let workers handle** the actual implementation
3. **Report progress** from workers to the user
4. **Handle blockers** when workers need clarification

## WORKFLOW

### When user requests work (build, create, implement, fix, etc.):
1. Acknowledge the request briefly
2. Use `issue_manager` with operation="create" to create issues for each task
3. Report what issues were created
4. Workers will be spawned automatically for each issue

### When user asks for status:
1. Use `issue_manager` with operation="list" to get current issues
2. Summarize: in_progress, completed, blocked

### When user provides clarification for a blocked issue:
1. Use `issue_manager` with operation="update" to add the clarification
2. Worker will resume automatically

## CRITICAL RULES

- **NEVER use bash, write_file, or other tools directly for work requests**
- **ALWAYS create issues** for work that needs to be done
- **Keep responses brief** - workers do the heavy lifting
- **Use emojis** for status: 📋 new work, ✅ completed, ⏳ in progress, ⚠️ blocked

## ISSUE TYPES

Valid issue types are: `task`, `feature`, `bug`, `epic`, `chore`
- Use `task` for implementation work
- Use `feature` for new functionality
- Use `bug` for fixes
- Use `chore` for maintenance/setup

## EXAMPLE

User: "Build me a calculator app"

You should respond:
"📋 Creating issues for calculator app...

Created 3 issues:
- Issue #1: Set up project structure (task)
- Issue #2: Implement calculator logic (feature)
- Issue #3: Create CLI interface (task)

🚀 Workers are being dispatched. I'll keep you posted on progress!"

Then CREATE those issues using the issue_manager tool with the correct issue_type.
"""


class ForemanOrchestrator:
    """Orchestrator that guides the LLM to coordinate work through issues and workers."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize the foreman orchestrator with configuration validation."""
        self.config = config
        self.worker_pools = config.get("worker_pools", [])
        self.routing_config = config.get("routing", {})
        self.max_iterations = config.get("max_iterations", 20)

        # Track spawned workers to avoid duplicates
        self._spawned_issues: set[str] = set()

        # Track worker spawn errors for reporting to user
        self._spawn_errors: list[str] = []

        # Store coordinator for worker spawning
        self._coordinator: Any = None

        # Track active worker tasks for recovery
        self._worker_tasks: dict[str, asyncio.Task] = {}

        # Recovery state - have we checked for orphaned issues?
        self._recovery_done: bool = False

        # Count of recovered workers (for user notification)
        self._recovered_count: int = 0

        # Orphaned issues found during recovery (for reporting)
        self._orphaned_issues: list[dict] = []

        # Store hooks reference for diagnostic events in async tasks
        self._hooks: Any = None

        # Validate configuration
        self._validate_config()

    def _validate_config(self) -> None:
        """Validate orchestrator configuration and log warnings for issues."""
        # Check if worker pools are configured
        if not self.worker_pools:
            logger.warning(
                "No worker pools configured - the foreman will not be able to spawn workers"
            )
            return

        # Validate each pool configuration
        for i, pool in enumerate(self.worker_pools):
            pool_name = pool.get("name", f"pool-{i}")

            # Check for worker_bundle
            if not pool.get("worker_bundle"):
                logger.warning(f"Worker pool '{pool_name}' is missing worker_bundle configuration")

            # Check if worker_bundle is a full URL (recommended)
            worker_bundle = pool.get("worker_bundle", "")
            if worker_bundle and not (
                worker_bundle.startswith("git+")
                or worker_bundle.startswith("http")
                or worker_bundle.startswith("file:")
                or worker_bundle.startswith("/")
            ):
                logger.warning(
                    f"Worker pool '{pool_name}' uses a relative worker_bundle path '{worker_bundle}'. "
                    "This may cause issues when running from different directories. "
                    "Consider using full URLs like 'git+https://...'"
                )

            # Check for name (required for routing)
            if not pool.get("name"):
                logger.warning(f"Worker pool #{i} is missing a name - routing may fail")

    def _spawn_worker_task(self, issue_id: str, coro) -> None:
        """Spawn a worker as an asyncio task and track it.

        Args:
            issue_id: The issue ID this worker is handling
            coro: The coroutine to run (from _run_spawn_and_handle_result)
        """
        task = asyncio.create_task(coro)
        self._worker_tasks[issue_id] = task
        self._spawned_issues.add(issue_id)

        # Add completion callback to clean up
        task.add_done_callback(lambda t: self._on_worker_complete(issue_id, t))

        logger.debug(f"Spawned and tracking worker task for issue {issue_id}")

        # Emit diagnostic event for task creation (shows in events.jsonl)
        if self._hooks:
            asyncio.create_task(
                self._hooks.emit(
                    "foreman:worker:task_created",
                    {"issue_id": issue_id, "task_id": id(task)},
                )
            )

    def _on_worker_complete(self, issue_id: str, task: asyncio.Task) -> None:
        """Called when a worker task completes (success or failure).

        This is a synchronous callback invoked by asyncio when the task finishes.
        It handles cleanup and logs any exceptions.

        Args:
            issue_id: The issue ID the worker was handling
            task: The completed asyncio.Task
        """
        try:
            # Check for exceptions (this re-raises if there was one)
            exc = task.exception()
            if exc:
                logger.error(f"Worker for issue {issue_id} failed with exception: {exc}")
                # Note: Issue status update to "blocked" is handled in _run_spawn_and_handle_result
        except asyncio.CancelledError:
            logger.warning(f"Worker for issue {issue_id} was cancelled (likely CLI exit)")
        except asyncio.InvalidStateError:
            # Task not done yet - shouldn't happen in done callback
            logger.warning(f"Worker task for issue {issue_id} in unexpected state")

        # Remove from active tasks (keep in _spawned_issues for reference)
        self._worker_tasks.pop(issue_id, None)
        logger.debug(
            f"Worker task for issue {issue_id} cleaned up, "
            f"{len(self._worker_tasks)} tasks remaining"
        )

    async def _write_worker_session_state(
        self,
        worker_session: Any,
        bundle_name: str,
        issue_id: str,
        working_dir: str | None,
    ) -> None:
        """Write metadata.json and transcript.jsonl for a worker session.

        Workers spawned via PreparedBundle.create_session() bypass the CLI's
        SessionStore, so we need to write these files manually for the sessions
        to appear in session listings and be resumable.

        Args:
            worker_session: The completed worker AmplifierSession
            bundle_name: Name of the worker bundle
            issue_id: Issue ID this worker handled (for logging)
            working_dir: Parent's working directory for project slug derivation
        """
        import json
        from datetime import datetime, timezone
        from pathlib import Path

        try:
            # Derive project slug from working directory (same logic as hooks-logging)
            if working_dir:
                cwd = Path(working_dir).resolve()
            else:
                cwd = Path.cwd().resolve()
            slug = str(cwd).replace("/", "-").replace("\\", "-").replace(":", "")
            if not slug.startswith("-"):
                slug = "-" + slug

            # Build session directory path
            session_dir = (
                Path.home()
                / ".amplifier"
                / "projects"
                / slug
                / "sessions"
                / worker_session.session_id
            )
            session_dir.mkdir(parents=True, exist_ok=True)

            # Get model from session config
            model = "unknown"
            if hasattr(worker_session, "config") and worker_session.config:
                providers = worker_session.config.get("providers", [])
                if providers and isinstance(providers, list) and len(providers) > 0:
                    first_provider = providers[0]
                    if isinstance(first_provider, dict):
                        model = first_provider.get("config", {}).get("model", "unknown")

            # Write metadata.json
            metadata = {
                "session_id": worker_session.session_id,
                "parent_id": getattr(worker_session, "parent_id", None),
                "created": datetime.now(timezone.utc).isoformat(),
                "bundle": f"bundle:{bundle_name}",
                "model": model,
                "turn_count": 1,
                "issue_id": issue_id,  # Extra field for foreman tracking
                "incremental": True,
            }
            metadata_path = session_dir / "metadata.json"
            with open(metadata_path, "w") as f:
                json.dump(metadata, f, indent=2)

            # Write transcript.jsonl from context messages
            transcript_path = session_dir / "transcript.jsonl"
            context = worker_session.coordinator.get("context")
            if context and hasattr(context, "get_messages"):
                messages = await context.get_messages()
                with open(transcript_path, "w") as f:
                    for msg in messages:
                        # Add timestamp if not present
                        if "timestamp" not in msg:
                            msg = {**msg, "timestamp": datetime.now(timezone.utc).isoformat()}
                        f.write(json.dumps(msg) + "\n")

            logger.info(
                f"Wrote session state for worker {worker_session.session_id} "
                f"(issue {issue_id}) to {session_dir}"
            )

            # Diagnostic: Session state written
            await self._emit_diagnostic(
                "foreman:worker:session_state_written",
                {
                    "issue_id": issue_id,
                    "worker_session_id": worker_session.session_id,
                    "session_dir": str(session_dir),
                },
            )

        except Exception as e:
            logger.error(f"Failed to write worker session state for issue {issue_id}: {e}")
            # Don't raise - this is non-critical, worker already completed

    def get_worker_status(self) -> dict[str, str]:
        """Get status of all tracked worker tasks.

        Returns:
            Dict mapping issue_id to status string:
            - "running": Task is still executing
            - "completed": Task finished successfully
            - "failed": Task raised an exception
            - "cancelled": Task was cancelled
        """
        status = {}
        for issue_id, task in self._worker_tasks.items():
            if task.done():
                if task.cancelled():
                    status[issue_id] = "cancelled"
                elif task.exception():
                    status[issue_id] = "failed"
                else:
                    status[issue_id] = "completed"
            else:
                status[issue_id] = "running"
        return status

    async def _maybe_recover_orphaned_issues(self, issue_tool: Any) -> int:
        """Check for incomplete issues and respawn workers.

        Called on first execute() to recover from previous session crash.
        Scans for issues that are "open" or "in_progress" without active
        worker tasks and respawns workers for them.

        Args:
            issue_tool: The issue manager tool for querying issues

        Returns:
            Number of workers recovered/respawned
        """
        if self._recovery_done:
            return 0

        self._recovery_done = True
        recovered = 0

        try:
            # Find issues that may need workers
            issues_to_recover = []
            seen_ids: set[str] = set()

            # 1. "open" issues - never had a worker (or worker died before claiming)
            try:
                open_result = await issue_tool.execute(
                    {
                        "operation": "list",
                        "params": {"status": "open"},
                    }
                )
                open_issues = open_result.output.get("issues", [])
                for issue in open_issues:
                    issue_id = issue.get("id")
                    if issue_id and issue_id not in seen_ids:
                        issues_to_recover.append(issue)
                        seen_ids.add(issue_id)
            except Exception as e:
                logger.warning(f"Failed to fetch open issues for recovery: {e}")

            # 2. "in_progress" issues - worker may have died mid-execution
            try:
                in_progress_result = await issue_tool.execute(
                    {
                        "operation": "list",
                        "params": {"status": "in_progress"},
                    }
                )
                in_progress_issues = in_progress_result.output.get("issues", [])
                for issue in in_progress_issues:
                    issue_id = issue.get("id")
                    if issue_id and issue_id not in seen_ids:
                        # Only recover if we don't already have a task running
                        if issue_id not in self._worker_tasks:
                            issues_to_recover.append(issue)
                            seen_ids.add(issue_id)
            except Exception as e:
                logger.warning(f"Failed to fetch in_progress issues for recovery: {e}")

            # Report orphaned issues (but don't auto-spawn - that blocks the event loop)
            # Auto-spawning workers during recovery causes the event loop to block
            # because bundle loading does heavy I/O (git clone, module activation).
            # Instead, we just detect and report - user can re-create issues if needed.
            if issues_to_recover:
                logger.info(
                    f"Found {len(issues_to_recover)} orphaned issue(s) from previous session"
                )
                # Store for reporting in progress check
                self._orphaned_issues = issues_to_recover
                recovered = len(issues_to_recover)

            self._recovered_count = recovered
            return recovered

        except Exception as e:
            logger.error(f"Recovery failed: {e}", exc_info=True)
            return 0

    async def execute(
        self,
        prompt: str,
        context: Any,
        providers: dict[str, Any],
        tools: dict[str, Any],
        hooks: HookRegistry,
        coordinator: Any = None,
    ) -> str:
        """
        Execute the foreman agent loop.

        This runs a standard LLM conversation loop but:
        1. Injects foreman system prompt to guide behavior
        2. Monitors issue_manager calls to auto-spawn workers
        3. Reports progress from background workers
        """
        self._coordinator = coordinator
        self._hooks = hooks  # Store for diagnostic events in async tasks

        # Emit prompt submit event (CRITICAL for session state management)
        prompt_submit_result = await hooks.emit(PROMPT_SUBMIT, {"prompt": prompt})
        if coordinator:
            prompt_submit_result = await coordinator.process_hook_result(
                prompt_submit_result, "prompt:submit", "orchestrator"
            )
            if prompt_submit_result.action == "deny":
                return f"Operation denied: {prompt_submit_result.reason}"

        # Emit execution start event
        await hooks.emit("execution:start", {"prompt": prompt})

        # Add the user message to context at the start of execution
        # This ensures proper conversation state management
        await context.add_message({"role": "user", "content": prompt})

        # Get primary provider
        provider_name = next(iter(providers.keys()), None)
        if not provider_name:
            return "No LLM provider available."

        provider = providers[provider_name]

        # Get issue tool (needed for recovery and progress)
        issue_tool = tools.get("issue") or tools.get("tool-issue") or tools.get("issue_manager")

        # Recovery: Check for orphaned issues on first execute
        if issue_tool and not self._recovery_done:
            await self._maybe_recover_orphaned_issues(issue_tool)

        # Check for worker updates before processing
        progress_report = ""
        if issue_tool:
            progress_report = await self._check_worker_progress(issue_tool)

        # Add spawn errors to progress report if any exist
        if hasattr(self, "_spawn_errors") and self._spawn_errors:
            if progress_report:
                progress_report += "\n\n"
            progress_report += "⚠️ Worker Spawn Errors:\n" + "\n".join(self._spawn_errors)
            # Clear errors after reporting
            self._spawn_errors = []

        # Build messages with foreman system prompt
        messages = await self._build_messages(prompt, context, progress_report)

        # Get tool specs
        tool_specs = self._get_tool_specs(tools)

        # Run the agent loop
        final_response = ""
        iteration = 0

        while iteration < self.max_iterations:
            iteration += 1

            # Call LLM - provider.complete() requires ChatRequest
            try:
                # Convert dict messages to Message objects
                message_objects = [Message(**m) for m in messages]
                request = ChatRequest(messages=message_objects, tools=tool_specs)
                response = await provider.complete(request)
            except Exception as e:
                logger.error(f"LLM call failed: {e}")
                return f"Error communicating with LLM: {e}"

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

            # Handle tool calls
            if response.tool_calls:
                # Build assistant message content from response
                assistant_content: list[Any] = []
                if response.content and isinstance(response.content, list):
                    for block in response.content:
                        if hasattr(block, "model_dump"):
                            assistant_content.append(block.model_dump())
                        else:
                            assistant_content.append(block)
                elif final_response:
                    assistant_content.append({"type": "text", "text": final_response})

                # Add assistant message with tool_calls as SEPARATE field (not in content!)
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": assistant_content if assistant_content else "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "tool": tc.name,
                            "arguments": tc.arguments,
                        }
                        for tc in response.tool_calls
                    ],
                }
                messages.append(assistant_msg)

                # Execute tools and collect results
                tool_results = await self._execute_tools(
                    response.tool_calls, tools, issue_tool, hooks
                )

                # Add tool results as role="tool" messages (not user messages!)
                for result in tool_results:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": result["tool_call_id"],
                            "content": result["content"],
                        }
                    )

                # Continue loop for more LLM processing
                continue

            # No tool calls - we're done
            break

        # Store conversation in context
        await self._update_context(context, final_response)

        # Emit orchestrator complete event - CRITICAL for session state management
        await hooks.emit(
            ORCHESTRATOR_COMPLETE,
            {
                "orchestrator": "foreman",
                "turn_count": iteration,
                "status": "success" if final_response else "incomplete",
            },
        )

        # Emit execution end event
        await hooks.emit("execution:end", {})

        return final_response

    async def _build_messages(
        self, prompt: str, context: Any, progress_report: str
    ) -> list[dict[str, Any]]:
        """Build message list with foreman system prompt and history."""
        messages: list[dict[str, Any]] = []

        # Add foreman system prompt
        system_content = FOREMAN_SYSTEM_PROMPT
        if progress_report:
            system_content += f"\n\n## CURRENT WORKER STATUS\n{progress_report}"

        messages.append({"role": "system", "content": system_content})

        # Add conversation history from context (get_messages is async)
        # Note: The current user prompt was already added to context in execute()
        # so it will be included in this history
        history = []
        if hasattr(context, "get_messages"):
            history = await context.get_messages()
        for msg in history[-10:]:  # Last 10 messages for context
            if msg.get("role") in ("user", "assistant"):
                messages.append({"role": msg["role"], "content": msg.get("content", "")})

        return messages

    def _get_tool_specs(self, tools: dict[str, Any]) -> list[ToolSpec]:
        """Get tool specifications for LLM."""
        specs = []
        for name, tool in tools.items():
            # Use Tool protocol: name, description, input_schema properties
            description = getattr(tool, "description", "")
            input_schema = getattr(tool, "input_schema", {})
            specs.append(
                ToolSpec(
                    name=name,
                    description=description,
                    parameters=input_schema,
                )
            )
        return specs

    async def _execute_tools(
        self,
        tool_calls: list[Any],
        tools: dict[str, Any],
        issue_tool: Any,
        hooks: Any,
    ) -> list[dict[str, Any]]:
        """Execute tool calls and return results, emitting hook events."""
        results = []

        for tc in tool_calls:
            tool = tools.get(tc.name)
            if not tool:
                results.append(
                    {
                        "tool_call_id": tc.id,
                        "content": f"Tool '{tc.name}' not found",
                    }
                )
                continue

            # Emit tool:pre event
            if hooks:
                await hooks.emit(
                    "tool:pre",
                    {"tool_name": tc.name, "arguments": tc.arguments},
                )

            try:
                # Execute the tool
                result = await tool.execute(tc.arguments)
                output = result.output if hasattr(result, "output") else str(result)

                # If this was an issue creation, spawn a worker
                if (
                    tc.name == "issue_manager" or tc.name == "tool-issue" or tc.name == "issue"
                ) and tc.arguments.get("operation") == "create":
                    if isinstance(output, dict):
                        await self._maybe_spawn_worker(output, issue_tool)

                results.append(
                    {
                        "tool_call_id": tc.id,
                        "content": json.dumps(output) if isinstance(output, dict) else str(output),
                    }
                )

                # Emit tool:post event on success
                if hooks:
                    await hooks.emit(
                        "tool:post",
                        {"tool_name": tc.name, "result": output},
                    )

            except Exception as e:
                logger.error(f"Tool {tc.name} failed: {e}")
                results.append(
                    {
                        "tool_call_id": tc.id,
                        "content": f"Error: {e}",
                    }
                )
                # Emit tool:post event on error too (for logging/tracking)
                if hooks:
                    await hooks.emit(
                        "tool:post",
                        {"tool_name": tc.name, "error": str(e)},
                    )

        return results

    async def _maybe_spawn_worker(self, issue_result: dict[str, Any], issue_tool: Any) -> None:
        """Spawn a worker for a newly created issue by loading bundle directly.

        This method loads worker bundles directly using amplifier_foundation's
        load_bundle() and PreparedBundle.create_session(), enabling spawning
        of full bundles by URL without requiring CLI-level support.

        The foreman pattern specifically needs to spawn complete external bundles
        (not just agents defined in the parent config), so we bypass the CLI's
        session.spawn capability and use foundation primitives directly.
        """
        issue = issue_result.get("issue", {})
        issue_id = issue.get("id")

        if not issue_id:
            return

        # Check if already spawned AND still running (allow respawn if task died)
        if issue_id in self._spawned_issues:
            task = self._worker_tasks.get(issue_id)
            if task and not task.done():
                logger.debug(f"Worker already running for issue {issue_id}, skipping")
                return
            # Task finished or doesn't exist - allow respawn (recovery case)
            logger.debug(
                f"Allowing respawn for issue {issue_id} (previous task finished or missing)"
            )

        self._spawned_issues.add(issue_id)

        # Route to appropriate worker pool
        pool_config = self._route_issue(issue)
        if not pool_config:
            error = f"No worker pool found for issue {issue_id}"
            logger.warning(error)
            self._append_spawn_error(issue_id, error)
            return

        # Get worker bundle path
        worker_bundle = pool_config.get("worker_bundle")
        if not worker_bundle:
            error = f"No worker_bundle configured for pool {pool_config.get('name', 'unknown')}"
            logger.warning(error)
            self._append_spawn_error(issue_id, error)
            return

        # Ensure worker_bundle is properly resolved
        worker_bundle = self._resolve_bundle_path(worker_bundle)
        logger.info(f"Resolved worker bundle path: {worker_bundle}")

        # NOTE: We intentionally do NOT set in_progress here.
        # The worker will claim the issue when it starts, setting:
        # - status: "in_progress"
        # - assignee: worker session ID
        # This provides accurate lifecycle tracking.

        # Build worker prompt
        worker_prompt = self._build_worker_prompt(issue)

        # Spawn worker as tracked asyncio task
        try:
            logger.info(f"Spawning tracked worker for issue {issue_id}")
            self._spawn_worker_task(
                issue_id,
                self._run_spawn_and_handle_result(worker_bundle, worker_prompt, issue_id),
            )
            logger.info(f"Successfully initiated worker spawn for issue {issue_id}")
        except Exception as e:
            error = f"Failed to spawn worker: {e}"
            logger.error(error, exc_info=True)
            self._append_spawn_error(issue_id, error)

    def _build_worker_prompt(self, issue: dict[str, Any]) -> str:
        """Build the instruction prompt for a worker session.

        Args:
            issue: The issue dict containing id, title, description, metadata

        Returns:
            Formatted prompt string for the worker
        """
        issue_id = issue.get("id", "unknown")
        title = issue.get("title", "Untitled")
        description = issue.get("description", "No description")

        return f"""You are a worker assigned to complete this issue:

## Issue #{issue_id}: {title}

{description}

## Your Workflow

### Step 1: Claim the Issue (FIRST!)
Before doing any work, claim this issue by updating it:
```
issue_manager operation="update" issue_id="{issue_id}" status="in_progress"
```
This tells the foreman you've started working on it.

### Step 2: Complete the Work
Do the work described in the issue above.

### Step 3: Update Final Status
When done, update the issue with your results:
- **Success**: status="completed" with a summary of what you did
- **Blocked**: status="blocked" with what's blocking you
- **Need input**: status="pending_user_input" with your specific question

## Important
- Always claim the issue FIRST before starting work
- Always update the issue status when you finish (success or failure)
- Be specific in your status updates so the foreman can report progress
"""

    async def _emit_diagnostic(self, event: str, data: dict[str, Any]) -> None:
        """Emit a diagnostic event if hooks are available.

        These events show up in events.jsonl to help trace worker task execution.
        """
        if self._hooks:
            try:
                await self._hooks.emit(event, data)
            except Exception as e:
                logger.warning(f"Failed to emit diagnostic event {event}: {e}")

    async def _run_spawn_and_handle_result(
        self,
        worker_bundle_uri: str,
        worker_prompt: str,
        issue_id: str,
    ) -> None:
        """Load worker bundle and execute in a new session.

        This method is run as an asyncio task (fire-and-forget) so that
        workers execute in the background while the foreman continues
        interacting with the user.

        Uses amplifier_foundation's load_bundle() and PreparedBundle.create_session()
        to spawn complete external bundles directly, without requiring CLI support.

        Args:
            worker_bundle_uri: Bundle URI (git+https://..., file path, etc.)
            worker_prompt: Instruction prompt for the worker
            issue_id: Issue ID for logging and error tracking
        """
        # Diagnostic: Task started executing
        await self._emit_diagnostic(
            "foreman:worker:task_started",
            {"issue_id": issue_id, "bundle_uri": worker_bundle_uri},
        )

        from amplifier_foundation import load_bundle

        try:
            # Diagnostic: Bundle loading started
            await self._emit_diagnostic(
                "foreman:worker:bundle_loading",
                {"issue_id": issue_id, "bundle_uri": worker_bundle_uri},
            )

            # Load the worker bundle from URI
            logger.info(f"Loading worker bundle from: {worker_bundle_uri}")
            bundle = await load_bundle(worker_bundle_uri)
            logger.info(f"Loaded bundle '{bundle.name}' for issue {issue_id}")

            # Diagnostic: Bundle loaded successfully
            await self._emit_diagnostic(
                "foreman:worker:bundle_loaded",
                {"issue_id": issue_id, "bundle_name": bundle.name},
            )

            # Get parent session for config inheritance
            parent_session = getattr(self._coordinator, "session", None)
            parent_id = parent_session.session_id if parent_session else None

            # Inherit providers from parent session
            # Worker bundles typically don't define providers - they inherit from foreman
            if parent_session and parent_session.config.get("providers"):
                parent_providers = parent_session.config["providers"]
                if not bundle.providers:
                    bundle.providers = list(parent_providers)
                    logger.info(f"Inherited {len(parent_providers)} providers from parent session")

            # Prepare the bundle (activates modules, creates resolver)
            prepared = await bundle.prepare()
            logger.info(f"Prepared bundle '{bundle.name}' for issue {issue_id}")

            # Diagnostic: Bundle prepared
            await self._emit_diagnostic(
                "foreman:worker:bundle_prepared",
                {"issue_id": issue_id, "bundle_name": bundle.name},
            )

            # Inherit UX systems from parent for consistent display/approval
            approval_system = None
            display_system = None
            parent_working_dir = None
            if parent_session and hasattr(parent_session, "coordinator"):
                approval_system = getattr(parent_session.coordinator, "approval_system", None)
                display_system = getattr(parent_session.coordinator, "display_system", None)
                # Get working directory from parent - critical for file operations
                parent_working_dir = parent_session.coordinator.get_capability(
                    "session.working_dir"
                )

            # Create worker session with inherited working directory
            # IMPORTANT: session_cwd must be passed at creation time, not after
            # The session's project directory is determined at creation based on this
            from pathlib import Path

            worker_session = await prepared.create_session(
                parent_id=parent_id,
                approval_system=approval_system,
                display_system=display_system,
                session_cwd=Path(parent_working_dir) if parent_working_dir else None,
            )

            # Diagnostic: Session created
            await self._emit_diagnostic(
                "foreman:worker:session_created",
                {
                    "issue_id": issue_id,
                    "worker_session_id": worker_session.session_id,
                    "parent_id": parent_id,
                },
            )

            # Also register the capability so tools can access it
            if parent_working_dir:
                worker_session.coordinator.register_capability(
                    "session.working_dir", parent_working_dir
                )
                logger.info(f"Worker session using working_dir: {parent_working_dir}")

            try:
                # Diagnostic: Worker execution starting
                await self._emit_diagnostic(
                    "foreman:worker:execution_starting",
                    {"issue_id": issue_id, "worker_session_id": worker_session.session_id},
                )

                # Execute the worker instruction
                logger.info(f"Executing worker for issue {issue_id}")
                await worker_session.execute(worker_prompt)
                logger.info(
                    f"Worker completed for issue {issue_id}, session: {worker_session.session_id}"
                )

                # Diagnostic: Worker execution completed
                await self._emit_diagnostic(
                    "foreman:worker:execution_completed",
                    {"issue_id": issue_id, "worker_session_id": worker_session.session_id},
                )

                # Write worker session state (metadata.json, transcript.jsonl)
                # This is normally done by CLI's SessionStore, but workers bypass that
                await self._write_worker_session_state(
                    worker_session=worker_session,
                    bundle_name=bundle.name,
                    issue_id=issue_id,
                    working_dir=parent_working_dir,
                )
            finally:
                # Always cleanup the worker session
                await worker_session.cleanup()

        except Exception as e:
            error = f"Worker execution failed for issue {issue_id}: {e}"
            logger.error(error, exc_info=True)
            self._append_spawn_error(issue_id, f"Worker execution failed: {e}")

            # Diagnostic: Worker execution failed
            await self._emit_diagnostic(
                "foreman:worker:execution_failed",
                {"issue_id": issue_id, "error": str(e)},
            )

    def _route_issue(self, issue: dict[str, Any]) -> dict[str, Any] | None:
        """Route issue to appropriate worker pool based on metadata."""
        issue_type = issue.get("issue_type") or issue.get("metadata", {}).get("type", "general")

        # Check routing rules
        rules = self.routing_config.get("rules", [])
        for rule in rules:
            if "if_metadata_type" in rule:
                if issue_type in rule["if_metadata_type"]:
                    pool_name = rule.get("then_pool")
                    return self._get_pool_by_name(pool_name)

        # Fall back to default pool
        default_pool = self.routing_config.get("default_pool")
        if default_pool:
            return self._get_pool_by_name(default_pool)

        # Fall back to first pool
        return self.worker_pools[0] if self.worker_pools else None

    def _get_pool_by_name(self, name: str) -> dict[str, Any] | None:
        """Get worker pool config by name."""
        for pool in self.worker_pools:
            if pool.get("name") == name:
                return pool
        return None

    def _resolve_bundle_path(self, bundle_path: str) -> str:
        """Ensure bundle path is fully resolved to prevent directory-specific issues.

        This method handles different types of bundle paths:
        - git+https://... (absolute git URL)
        - http://... (absolute HTTP URL)
        - file://... (absolute file URL)
        - /absolute/path (absolute filesystem path)
        - relative/path (relative path - may need resolution)
        """
        # Already absolute URL or path
        if (
            bundle_path.startswith("git+")
            or bundle_path.startswith("http")
            or bundle_path.startswith("file:")
            or bundle_path.startswith("/")
        ):
            return bundle_path

        # Try to find repository root for relative paths
        repo_root = self._find_repo_root()
        if repo_root:
            # Resolve path relative to repo root
            absolute_path = os.path.normpath(os.path.join(repo_root, bundle_path))
            logger.debug(f"Resolved relative bundle path '{bundle_path}' to '{absolute_path}'")
            return absolute_path

        # If we can't find repo root, return the path as-is but log a warning
        logger.warning(f"Could not resolve relative bundle path '{bundle_path}' to absolute path")
        return bundle_path

    def _find_repo_root(self) -> str | None:
        """Find the repository root directory from the current working directory."""
        # Start from current directory
        path = os.getcwd()

        # Walk up until we find .git or .amplifier directory
        while path != "/":
            if os.path.exists(os.path.join(path, ".git")) or os.path.exists(
                os.path.join(path, ".amplifier")
            ):
                return path
            # Move up one directory
            parent = os.path.dirname(path)
            if parent == path:  # Reached root
                break
            path = parent

        # If coordinator has repo root capability, use that
        if self._coordinator:
            repo_root = self._coordinator.get_capability("repo.root_path")
            if repo_root:
                return repo_root

        return None

    def _append_spawn_error(self, issue_id: str, error: str) -> None:
        """Add worker spawn error for reporting to user."""
        if not hasattr(self, "_spawn_errors"):
            self._spawn_errors = []

        error_msg = f"Issue #{issue_id}: {error}"
        self._spawn_errors.append(error_msg)

        # Also update issue status to blocked
        asyncio.create_task(self._update_issue_status_blocked(issue_id, error))

    async def _update_issue_status_blocked(self, issue_id: str, error: str) -> None:
        """Update issue status to blocked with error message."""
        # Get issue tool (may be called outside normal execution path)
        if not hasattr(self, "_coordinator") or not self._coordinator:
            logger.error("Cannot update issue status: coordinator not available")
            return

        tools = getattr(self._coordinator, "tools", {})
        issue_tool = tools.get("issue") or tools.get("tool-issue") or tools.get("issue_manager")
        if not issue_tool:
            logger.error("Cannot update issue status: issue tool not available")
            return

        try:
            await issue_tool.execute(
                {
                    "operation": "update",
                    "params": {
                        "issue_id": issue_id,
                        "status": "blocked",
                        "comment": f"Worker spawning failed: {error}",
                    },
                }
            )
            logger.info(f"Updated issue #{issue_id} status to blocked")
        except Exception as e:
            logger.error(f"Failed to update issue #{issue_id} status: {e}")

    async def _check_worker_progress(self, issue_tool: Any) -> str:
        """Check for completed or blocked issues from workers."""
        parts = []

        # Report orphaned issues found during recovery
        if self._orphaned_issues:
            parts.append(
                f"⚠️ Found {len(self._orphaned_issues)} orphaned issue(s) from previous session:"
            )
            for issue in self._orphaned_issues[:5]:  # Show first 5
                parts.append(f"  - #{issue.get('id', '?')[:8]}: {issue.get('title', 'Untitled')}")
            if len(self._orphaned_issues) > 5:
                parts.append(f"  ... and {len(self._orphaned_issues) - 5} more")
            parts.append("  (Use 'list issues' to see all, or create new issues to restart work)")
            self._orphaned_issues = []  # Clear after reporting

        # Report active worker tasks
        task_status = self.get_worker_status()
        running_count = sum(1 for s in task_status.values() if s == "running")
        if running_count:
            parts.append(f"🔧 {running_count} worker task(s) currently running")

        try:
            # Check completed issues
            completed_result = await issue_tool.execute(
                {
                    "operation": "list",
                    "params": {"status": "completed"},
                }
            )
            completed = completed_result.output.get("issues", [])
            if completed:
                parts.append(f"✅ {len(completed)} issue(s) completed by workers")

            # Check blocked issues
            blocked_result = await issue_tool.execute(
                {
                    "operation": "list",
                    "params": {"status": "pending_user_input"},
                }
            )
            blocked = blocked_result.output.get("issues", [])
            if blocked:
                parts.append(f"⚠️ {len(blocked)} issue(s) need user input:")
                for issue in blocked[:3]:  # Show first 3
                    parts.append(f"  - #{issue['id']}: {issue['title']}")

            # Check in-progress issues
            in_progress_result = await issue_tool.execute(
                {
                    "operation": "list",
                    "params": {"status": "in_progress"},
                }
            )
            in_progress = in_progress_result.output.get("issues", [])
            if in_progress:
                parts.append(f"⏳ {len(in_progress)} issue(s) in progress")

        except Exception as e:
            logger.error(f"Failed to check worker progress: {e}")

        return "\n".join(parts)

    async def _update_context(self, context: Any, response: str) -> None:
        """Store the assistant response in context.

        Note: User message is already added at the start of execute(),
        so we only need to add the assistant response here.
        """
        try:
            if hasattr(context, "add_message"):
                await context.add_message({"role": "assistant", "content": response})
        except Exception as e:
            logger.error(f"Failed to update context: {e}")


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the foreman orchestrator."""
    config = config or {}
    orchestrator = ForemanOrchestrator(config)
    await coordinator.mount("orchestrator", orchestrator)
    return None
